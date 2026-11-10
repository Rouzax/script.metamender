"""The certification chain: which source answers an item's certification.

Per item, cheapest and most authoritative first:

1. TMDB's certification for the configured country (already in the TMDB
   response the scan has): *real*.
2. The configured country's own website (FSK for DE, BBFC for GB,
   Medieraadet for DK): *real*.
3. TMDB's certifications of the preset's inference countries, mapped onto the
   configured system, no I/O: *derived*.
4. The other countries' websites, in the same order, mapped: *derived*.

Steps 2 and 4 go through the SiteGate, and only when the caller passes one
(the scan does so only when the certification itself is due). An unavailable
step makes the chain unclean but does not stop it: a rating found further on
is still an answer, while "nothing found" counts only from a clean chain.

Logging:
    Logger: 'certchain'
    Key events:
        - cert.site (DEBUG): One site request and its outcome
        - cert.cap_reached (INFO): The cycle's site-request budget is used up
        - cert.site_error (ERROR): A site lookup raised something unexpected
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable

from resources.lib.constants import (
    CERT_ITEM_BUDGET_SEC,
    SITE_MIN_INTERVAL_SEC,
    SITE_REQUESTS_PER_CYCLE,
)
from resources.lib.data.presets import Preset
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.certification import normalize_title
from resources.lib.engine.chain import TRANSIENT
from resources.lib.providers import bbfc, fsk, medieraadet
from resources.lib.utils import ApiKeyError, TransientProviderError, get_logger

log = get_logger('certchain')

SITES: dict[str, ModuleType] = {m.SITE: m for m in (fsk, bbfc, medieraadet)}
NATIVE_SITE = {"DE": "fsk", "GB": "bbfc", "DK": "medieraadet"}

REAL = "real"
DERIVED = "derived"
NONE = "none"
UNAVAILABLE = "transient"
NOT_ASKED = "not_asked"


@dataclass(frozen=True)
class CertQuery:
    title: str
    original_title: str
    year: int | None
    media_type: str
    imdb_id: str | None
    # {country: certification} from TMDB, or TRANSIENT when TMDB did not answer.
    tmdb_certs: Any


@dataclass(frozen=True)
class CertAnswer:
    kind: str
    rating: str | None
    source: str | None


class SiteGate:
    """Every request to a certification site in one scan cycle passes here:
    the user's site toggles, one breaker key per site, a minimum interval per
    site, a cap on requests per cycle, a memo of answers, and Kodi shutdown.
    One gate serves the movie and TV show passes of a cycle."""

    def __init__(
        self,
        enabled: frozenset[str],
        breaker: CircuitBreaker,
        monitor: Any,
        cap: int = SITE_REQUESTS_PER_CYCLE,
        min_interval: float = SITE_MIN_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.requests = 0
        self.aborted = False
        self._breaker = breaker
        self._monitor = monitor
        self._cap = cap
        self._min_interval = min_interval
        self._clock = clock
        self._last: dict[str, float] = {}
        self._memo: dict[tuple[str, str, int | None, str], str | None] = {}
        self._cap_logged = False

    def ask(self, site: str, query: CertQuery) -> Any:
        """The site's bare rating, None for "no rating there" (including a
        site that is switched off or does not rate this media type), or
        TRANSIENT when the site cannot be asked or did not answer."""
        module = SITES[site]
        if site not in self.enabled or query.media_type not in module.MEDIA_TYPES:
            return None
        key = (site, normalize_title(query.title), query.year, query.media_type)
        if key in self._memo:
            return self._memo[key]
        if self.aborted or self._breaker.is_disabled(site):
            return TRANSIENT
        if self.requests >= self._cap:
            if not self._cap_logged:
                self._cap_logged = True
                log.info("Certification site budget used up for this cycle",
                         event="cert.cap_reached", cap=self._cap)
            return TRANSIENT
        if not self._wait(site):
            return TRANSIENT
        self.requests += 1
        self._last[site] = self._clock()
        try:
            answer = module.lookup(
                query.title, query.original_title, query.year, query.media_type,
                query.imdb_id,
            )
        except TransientProviderError as exc:
            if exc.status in (403, 429):
                # Blocked or rate limited: more requests this cycle would
                # only make it worse.
                self._breaker.disable(site)
            else:
                self._breaker.record_failure(site)
            log.debug("Certification site unavailable", event="cert.site",
                      site=site, outcome="transient", status=exc.status)
            return TRANSIENT
        except ApiKeyError:
            # A site's 401 is not the user's TMDB key: it must not stop the
            # scan the way ApiKeyError does elsewhere.
            self._breaker.disable(site)
            log.debug("Certification site refused the request", event="cert.site",
                      site=site, outcome="refused")
            return TRANSIENT
        except Exception:
            self._breaker.disable(site)
            log.exception("Certification site lookup failed", event="cert.site_error",
                          site=site)
            return TRANSIENT
        self._breaker.record_success(site)
        self._memo[key] = answer
        log.debug("Certification site answered", event="cert.site", site=site,
                  outcome="rating" if answer else "none", rating=answer)
        return answer

    def _wait(self, site: str) -> bool:
        """Wait out the site's interval; False when Kodi is shutting down."""
        last = self._last.get(site)
        if last is None:
            return True
        remaining = last + self._min_interval - self._clock()
        if remaining <= 0:
            return True
        if self._monitor is None:
            time.sleep(remaining)
            return True
        if self._monitor.waitForAbort(remaining):
            self.aborted = True
            return False
        return True


def resolve_certification(
    query: CertQuery,
    preset: Preset,
    gate: SiteGate | None,
    budget_sec: float = CERT_ITEM_BUDGET_SEC,
    clock: Callable[[], float] = time.monotonic,
) -> CertAnswer:
    """Run the chain for one item (see the module docstring)."""
    start = clock()
    unclean = False
    asked: set[str] = set()
    certs = query.tmdb_certs if isinstance(query.tmdb_certs, dict) else {}
    if query.tmdb_certs is TRANSIENT:
        unclean = True

    def site(name: str) -> str | None:
        nonlocal unclean
        if gate is None or name in asked:
            return None
        if clock() - start > budget_sec:
            unclean = True
            return None
        asked.add(name)
        answer = gate.ask(name, query)
        if answer is TRANSIENT:
            unclean = True
            return None
        return answer

    direct = certs.get(preset.country)
    if direct in preset.valid:
        return CertAnswer(REAL, direct, "tmdb")

    native = NATIVE_SITE.get(preset.country)
    if native:
        rating = site(native)
        if rating in preset.valid:
            return CertAnswer(REAL, rating, native)

    for country in preset.inference:
        mapped = preset.mappings.get(country, {}).get(certs.get(country) or "")
        if mapped:
            return CertAnswer(DERIVED, mapped, "tmdb-" + country.lower())

    for country in preset.inference:
        name = NATIVE_SITE.get(country)
        if name is None:
            continue
        rating = site(name)
        mapped = preset.mappings.get(country, {}).get(rating or "")
        if mapped:
            return CertAnswer(DERIVED, mapped, name)

    if unclean:
        return CertAnswer(UNAVAILABLE, None, None)
    if gate is None:
        return CertAnswer(NOT_ASKED, None, None)
    return CertAnswer(NONE, None, None)

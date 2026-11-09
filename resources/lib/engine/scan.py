"""
Scan orchestrator for MetaMender.

Coordinates a single scan cycle: queries Kodi, decides per field what is due,
calls providers, writes results, and updates the tracker.

Text fields come from TMDB and TheTVDB (TVDB): title and plot run through a
per-field provider chain (engine/chain.py) that ends in fallback-language
steps; every other text field has one TMDB step. Ratings come from TMDB and
the IMDb ratings store. Each field gets its own verdict: a field is transient
(no write, no clock advance) only when a provider in its own chain, or its
own ID prerequisite, was transient.

Logging:
    Logger: 'scan'
    Key events:
        - scan.start (INFO): Scan cycle starting for a media type
        - scan.complete (INFO): Scan cycle finished with stats and the
          providers disabled this cycle
        - scan.skip (DEBUG): Item skipped (disabled source, not due, no
          candidates, or reason="unreachable": the media could not be found,
          so NFO state is unknown; scan.complete counts these)
        - scan.protected (DEBUG): An answered value was not written because
          the item is NFO-managed and the field holds a value metamender did
          not write
        - scan.abort (INFO): Scan interrupted by Kodi shutdown
        - scan.processing (DEBUG): Starting to process a single item
        - scan.updated (INFO): Item metadata written to Kodi
        - scan.breaker (WARNING): A provider tripped, or every provider the
          enabled fields need is down so the pass stops (scope="all")
        - scan.apikey (ERROR): TMDB rejected the API key (terminal)
        - scan.tvdb_auth (ERROR): TVDB rejected the add-on's credentials;
          TVDB is disabled for the cycle, the scan continues
        - scan.imdb_lookup_failed (ERROR): The IMDb ratings store raised
          unexpectedly; the rating is transient for that item. Logged with
          the traceback once per scan, at DEBUG after that
        - scan.unknown_id_accepted (INFO): An `unknown`-type uniqueid was
          verified as the item's TVDB id
        - scan.unknown_id_rejected (INFO): An `unknown`-type uniqueid names a
          different TVDB series, or an episode outside the show's TVDB list
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

import xbmc

from resources.lib.constants import (
    DEFAULT_REFETCH_SCHEDULE,
    FALLBACK_RECHECK_DAYS,
    PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
    parse_refetch_schedule,
)
from resources.lib.data.identity import compute_identity
from resources.lib.data.kodi import get_all_items, update_item
from resources.lib.data.media_types import MediaType
from resources.lib.data.nfo import NFO, UNREACHABLE, nfo_state
from resources.lib.data.sources import match_source
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.chain import TRANSIENT, FieldOutcome, Step, run_chain
from resources.lib.engine.idverify import show_matches
from resources.lib.engine.maturity import (
    FETCH_NOW,
    FINAL,
    apply_fallback_recheck,
    compute_next_due,
    decide_field,
    fallback_recheck_due,
)
from resources.lib.engine.protect import fingerprint, may_write
from resources.lib.engine.resolve import build_ratings, rating_sources, slot_fingerprint
from resources.lib.providers.imdb_ratings import lookup as imdb_rating_lookup
from resources.lib.providers.languages import tmdb_language, tvdb_language
from resources.lib.providers.tmdb import (
    fetch_episode_external_ids as tmdb_fetch_episode_ids,
    fetch_movie as tmdb_fetch_movie,
    fetch_season as tmdb_fetch_season,
    fetch_tvshow as tmdb_fetch_tvshow,
    fetch_tvshow_external_ids as tmdb_fetch_tvshow_ids,
    find_episode_by_tvdb as tmdb_find_episode,
    resolve_id,
)
from resources.lib.providers.tvdb import (
    fetch_series as tvdb_fetch_series,
    fetch_series_episodes as tvdb_fetch_series_episodes,
)
from resources.lib.utils import (
    ApiKeyError,
    TransientProviderError,
    TvdbAuthError,
    get_logger,
    is_empty,
)

log = get_logger('scan')

# Mapping from TMDB response keys to Kodi field names.
# "mpaa" is intentionally absent: Phase 8 handles certifications.
_TMDB_TO_KODI = {
    "rating": "rating",
    "votes": "votes",
    "plot": "plot",
    "tagline": "tagline",
    "directors": "director",
    "writers": "writer",
    "genres": "genre",
    "studios": "studio",
    "countries": "country",
    "trailer": "trailer",
    "runtime": "runtime",
    "firstaired": "firstaired",
    "title": "title",
}

_KODI_TO_TMDB = {v: k for k, v in _TMDB_TO_KODI.items()}

# Text fields TVDB also supplies. Only these get the full provider chain with
# fallback-language steps; every other field has a single TMDB step.
_CHAIN_FIELDS = frozenset({"title", "plot"})
_RATING_FIELDS = frozenset({"rating", "votes"})
# Key holding each chained field in a TVDB series record.
_TVDB_SERIES_KEYS = {"title": "name", "plot": "overview"}

# text_source_order / fallback_order setting values (settings.xml).
TEXT_TMDB_FIRST = 0
TEXT_TVDB_FIRST = 1
FALLBACK_TVDB_FIRST = 0
FALLBACK_TMDB_FIRST = 1
DEFAULT_FALLBACK_LANGUAGE = "en-US"


def settings_fingerprint(
    field: str,
    settings: Dict[str, Any],
    folder_language: str,
    media_type: str,
) -> str:
    """The settings a field's answer depends on, as a stable string.

    A field whose stored fingerprint differs is re-checked as if never
    sampled, so the inputs are exactly what changes the answer: rating_source
    for ratings; for title and plot the folder and fallback languages, plus
    the provider and fallback orders on shows and episodes (movies ask TMDB
    only); for every other field, one TMDB step, the folder language.
    """
    if field in _RATING_FIELDS:
        inputs = {"rating_source": settings.get("rating_source", 0)}  # type: Dict[str, Any]
    elif field in _CHAIN_FIELDS:
        inputs = {
            "folder_language": folder_language,
            "fallback_language": settings.get("fallback_language", DEFAULT_FALLBACK_LANGUAGE),
        }
        if media_type != "movie":
            inputs["text_source_order"] = settings.get("text_source_order", TEXT_TMDB_FIRST)
            inputs["fallback_order"] = settings.get("fallback_order", FALLBACK_TVDB_FIRST)
    else:
        inputs = {"folder_language": folder_language}
    return json.dumps(inputs, sort_keys=True)


def _make_stats() -> Dict[str, Any]:
    """Return a fresh stats dict."""
    return {
        "processed": 0,
        "skipped": 0,
        "updated": 0,
        "errors": 0,
        # Items skipped because their media could not be reached, and answered
        # field values not written because an NFO supplied the field.
        "unreachable": 0,
        "protected": 0,
        "fields": {},
        # True when the pass stopped before reaching the end of the library
        # (shutdown, or every provider disabled). The caller must not treat
        # such a pass as a completed cycle.
        "aborted": False,
    }


def _content_age_days(
    item: Dict[str, Any], media_type: MediaType, today: str
) -> Optional[int]:
    """Age of the content in days from its release date, or None if unknown.

    Uses the release date (premiered/firstaired), never dateadded. Returns None
    when the date is missing or not a full ISO date.
    """
    raw = item.get(media_type.date_field, "")
    if not raw or len(raw) < 10:
        return None
    try:
        return (date.fromisoformat(today) - date.fromisoformat(raw[:10])).days
    except ValueError:
        return None


def _item_title(item: Dict[str, Any], media_type: MediaType) -> str:
    """Build the display title for an item (logging/UI, not the tracker key).

    The tracker key is produced separately by compute_identity. Movies and TV
    shows use the title directly; episodes use "ShowTitle S01E02" format.
    """
    if media_type.name == "episode":
        return "{showtitle} S{season:02d}E{episode:02d}".format(
            showtitle=item.get("showtitle", ""),
            season=item.get("season", 0),
            episode=item.get("episode", 0),
        )
    return item.get("title", "")


def _extract_ids(
    item: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Extract (tmdb_id, imdb_id, tvdb_id, unknown_id) from a Kodi item's
    uniqueid dict. ``unknown_id`` is an id of type `unknown`, a TVDB id
    candidate that is only used once verified."""
    uid = item.get("uniqueid") or {}
    return (
        uid.get("tmdb") or uid.get("themoviedb"),
        uid.get("imdb"),
        uid.get("tvdb") or uid.get("thetvdb"),
        uid.get("unknown"),
    )


def _get_season_data(
    show_tmdb_id: str,
    season_number: int,
    api_key: str,
    language: str,
    cache: Dict[str, Optional[List[Dict[str, Any]]]],
) -> Optional[List[Dict[str, Any]]]:
    """Fetch season data from TMDB with caching.

    Caches by show_tmdb_id, season_number, and language to avoid
    repeated API calls for episodes of the same season.
    """
    key = "{}:{}:{}".format(show_tmdb_id, season_number, language)
    if key not in cache:
        cache[key] = tmdb_fetch_season(show_tmdb_id, season_number, api_key, language)
    return cache[key]


def _find_episode_by_tmdb_id(
    season_data: List[Dict[str, Any]],
    tmdb_episode_id: str,
) -> Optional[Dict[str, Any]]:
    """Find an episode by TMDB episode ID in a season's episode list."""
    for ep in season_data:
        if ep.get("tmdb_id") == tmdb_episode_id:
            return ep
    return None


# Kodi's separator for list fields (advancedsettings itemseparator default).
# Lists are stored joined with it and split on it when read back.
_KODI_ITEM_SEPARATOR = " / "


def _kodi_form(value: Any) -> Any:
    """A provider value as Kodi will store and return it.

    Kodi's CVideoInfoTag setters trim text and drop empty list entries, and a
    list comes back split on the item separator. Writing and fingerprinting
    this form keeps the same-value skip and the provenance check matching what
    Kodi reports on the next scan; the raw provider value would differ by
    whitespace and never match again.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []  # type: List[Any]
        for entry in value:
            if not isinstance(entry, str):
                parts.append(entry)
                continue
            for piece in entry.split(_KODI_ITEM_SEPARATOR):
                piece = piece.strip()
                if piece:
                    parts.append(piece)
        return parts
    return value


def _extract_fields(
    provider_data: Dict[str, Any],
    gap_fields: List[str],
) -> Dict[str, Any]:
    """Provider values for the requested fields, in the form Kodi stores.

    Skips "mpaa" (Phase 8) and the named-slot rating/votes (handled by
    build_ratings). Values are put in Kodi's stored form (_kodi_form), then
    empty ones are dropped, runtime is converted
    minutes -> seconds, and firstaired is kept only as a full ISO date (bare
    years are dropped). Title and plot arrive from the provider chain, which
    never yields a placeholder title, so no placeholder check is needed here.

    The result is what would be written, so it is also what the write's
    provenance fingerprint is taken of. Whether a value is written at all is
    _plan_writes' decision.
    """
    fields = {}  # type: Dict[str, Any]

    for tmdb_key, kodi_key in _TMDB_TO_KODI.items():
        if kodi_key not in gap_fields:
            continue

        # Rating and votes are written via the named-slot ratings map
        # (see build_ratings + the scan write path), never as flat fields.
        if kodi_key in ("rating", "votes"):
            continue

        value = _kodi_form(provider_data.get(tmdb_key))

        # Skip empty provider values
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        if isinstance(value, (int, float)) and value == 0:
            continue

        # Runtime: providers return minutes; Kodi stores seconds
        if kodi_key == "runtime":
            if not isinstance(value, (int, float)):
                continue
            value = int(value) * 60

        # firstaired: only write a full ISO date; skip bare-year values
        if kodi_key == "firstaired" and not (
            isinstance(value, str) and len(value) == 10
            and value[4] == "-" and value[7] == "-"
        ):
            continue

        fields[kodi_key] = value

    return fields


def _plan_writes(
    values: Dict[str, Any],
    item: Dict[str, Any],
    nfo_managed: bool,
    fs_map: Dict[str, Dict[str, Any]],
    identity: str,
) -> Tuple[Dict[str, Any], Set[str]]:
    """Split extracted values into (fields to write, fields Kodi already holds).

    Under the maturity model a non-final field is refreshed overwrite-on-diff:
    a differing value overwrites the current one, an identical value is never
    rewritten. On an NFO-managed item may_write decides with the field's
    stored provenance; a field it forbids is in neither result, so it gets
    no write and no source.
    """
    to_write = {}  # type: Dict[str, Any]
    held = set()  # type: Set[str]
    for f, value in values.items():
        current = item.get(f)
        if current is not None and current == value:
            held.add(f)
            continue
        if not may_write(nfo_managed, current, fs_map.get(f, {}).get("written_value")):
            log.debug(
                "Field protected, value left as it is",
                event="scan.protected", identity=identity, field=f,
            )
            continue
        to_write[f] = value
    return to_write, held


def _parse_slots(raw: Any) -> Dict[str, str]:
    """A rating row's JSON provenance; anything unreadable owns nothing."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(parsed, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
    ):
        return parsed
    return {}


def _owned_slots(
    fs_map: Dict[str, Dict[str, Any]],
    current_ratings: Dict[str, Any],
) -> Dict[str, str]:
    """Rating slots metamender wrote, from the JSON provenance on the rating
    and votes rows.

    The rows can disagree: only the due rating fields are recorded, so a row
    whose field is final or disabled keeps older JSON. Per slot, the
    fingerprint that matches the slot Kodi holds now wins, since that is the
    write that put it there; otherwise the rating row's. Unreadable JSON owns
    nothing, which protects every slot it would have named.
    """
    rows = [_parse_slots(fs_map.get(f, {}).get("written_value")) for f in ("rating", "votes")]
    owned = {}  # type: Dict[str, str]
    for slot in set(rows[0]) | set(rows[1]):
        claims = [r[slot] for r in rows if slot in r]
        entry = current_ratings.get(slot)
        held = slot_fingerprint(entry) if isinstance(entry, dict) else None
        owned[slot] = held if held in claims else claims[0]
    return owned


class _Pacer:
    """Rate-limit pacing for provider calls.

    Sleeps ``rate_limit`` seconds before a call, but only once "armed", so the
    first call in a scan is not delayed and subsequent ones are spaced out.
    """

    def __init__(self, rate_limit: float) -> None:
        self._rate = rate_limit
        self._armed = False

    def wait(self) -> None:
        """Sleep before this call if armed, then arm for the next one."""
        if self._armed and self._rate > 0:
            time.sleep(self._rate)
        self._armed = True

    def arm(self) -> None:
        """Arm without sleeping (a call happened without going through wait())."""
        self._armed = True


@dataclass(frozen=True)
class _ScanCtx:
    """Scan-level constants shared across one run_scan cycle.

    Holds everything that does not vary per item, so the per-item helpers take a
    single ``ctx`` plus the handful of per-item values. Mutable members
    (breaker, pacer, caches, tracker) are referenced, not copied.
    """

    media_type: MediaType
    tracker: TrackerDB
    breaker: CircuitBreaker
    pacer: _Pacer
    tmdb_api_key: str
    default_language: str
    sources: List[Dict[str, Any]]
    enabled_fields: FrozenSet[str]
    show_id_map: Dict[int, str]
    show_tvdb_map: Dict[int, str]
    # Kodi ids of the shows whose id lookups have answered (see
    # TrackerDB.get_checked_show_ids); episode passes only.
    checked_shows: FrozenSet[int]
    season_cache: Dict[str, Optional[List[Dict[str, Any]]]]
    # (kind, tvdb_id, tvdb_lang) -> fetched record. Only real answers are
    # cached, so a transient failure is retried by the next item.
    tvdb_cache: Dict[Tuple[str, str, str], Any]
    # Events already logged in full this scan, so a persistent failure does
    # not repeat its traceback for every item.
    logged_once: Set[str]
    schedule: Tuple[int, ...]
    rating_pref: int
    text_order: int
    fallback_order: int
    fallback_language: str
    imdb_ready: bool


def _call_provider(
    provider: str,
    fn: Any,
    ctx: _ScanCtx,
    identity: str,
    best_effort: bool = False,
) -> Any:
    """Pace, run a provider fn, and apply circuit-breaker bookkeeping.

    Records breaker success on any non-raising return (including None, a real
    "nothing"). On ApiKeyError re-raises (terminal, TMDB only). On
    TvdbAuthError disables TVDB for the cycle, logs once, and returns
    ``TRANSIENT``. On TransientProviderError records a breaker failure, logs
    the trip once, and returns ``TRANSIENT``. When ``best_effort`` is True
    (episode ID enrichment) both TMDB exceptions are logged at scan.error and
    yield ``TRANSIENT`` without touching the breaker.
    """
    ctx.pacer.wait()
    try:
        result = fn()
    except TvdbAuthError:
        if ctx.breaker.disable("tvdb"):
            log.exception(
                "TheTVDB rejected the add-on's credentials, TVDB disabled for this cycle",
                event="scan.tvdb_auth", identity=identity,
            )
        return TRANSIENT
    except ApiKeyError:
        if best_effort:
            log.exception(
                "Provider call failed (best effort)",
                event="scan.error", provider=provider, identity=identity,
            )
            return TRANSIENT
        log.exception(
            "Provider API key invalid",
            event="scan.apikey", provider=provider, identity=identity,
        )
        raise
    except TransientProviderError:
        if best_effort:
            log.exception(
                "Provider call failed (best effort)",
                event="scan.error", provider=provider, identity=identity,
            )
            return TRANSIENT
        if ctx.breaker.record_failure(provider):
            log.warning(
                "Provider disabled for this cycle after repeated failures",
                event="scan.breaker", provider=provider,
                threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD, identity=identity,
            )
        return TRANSIENT
    ctx.breaker.record_success(provider)
    return result


def _enrich_imdb_id(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    new_imdb: str,
) -> None:
    """Write a discovered IMDB ID back to Kodi and the tracker."""
    uid = item.get("uniqueid", {})
    uid["imdb"] = new_imdb
    update_item(kodi_id, ctx.media_type, title, uniqueid=uid)
    ctx.tracker.upsert_item(ctx.media_type.name, identity, imdb_id=new_imdb)
    log.info(
        "Enriched IMDB ID from TMDB",
        event="scan.enrich_id", title=title, imdb_id=new_imdb,
    )


class _Ids:
    """An item's external ids for this scan: Kodi's, those the tracker kept
    from earlier scans, and those the lookups below add.

    A ``*_transient`` flag means the id is missing and a lookup that could
    have found it did not answer, so every field that depends on it is
    transient. A missing id without the flag is an answer: the provider has
    no such item.
    """

    def __init__(
        self,
        tmdb: Optional[str],
        imdb: Optional[str],
        tvdb: Optional[str],
        may_look_up: bool,
        tmdb_season: Optional[int] = None,
    ) -> None:
        self.tmdb = tmdb
        self.imdb = imdb
        self.tvdb = tvdb
        # Episode only: the TMDB season /find placed the episode in, when its
        # TMDB id came from /find. None means Kodi's season number applies.
        self.tmdb_season = tmdb_season
        self.tmdb_transient = False
        self.tvdb_transient = False
        # Cross-reference lookups (and unknown-id verification) may run for
        # this item now; see _lookup_allowed.
        self.may_look_up = may_look_up
        # A lookup ran and answered / a lookup ran and did not answer.
        self.looked_up = False
        self.lookup_failed = False

    def defer(self) -> None:
        """A lookup this item needs could not run yet (its show is not
        resolved): like a failed lookup, it must not close the gate."""
        self.lookup_failed = True

    def any(self) -> bool:
        return bool(self.tmdb or self.imdb or self.tvdb)


def _lookup_allowed(
    checked: Optional[str], today: str, due: bool, is_show: bool,
) -> bool:
    """Whether an item's id lookups may run this scan.

    Lookups serve due fields, so they run only when a field is due, and an
    answer that left an id missing is asked again only after
    FALLBACK_RECHECK_DAYS. A show that has never been looked up is resolved
    even when none of its own fields is due: its episodes depend on its ids,
    and they can only tell "the show is not on TMDB" from "not resolved yet"
    once the show's lookups have answered.
    """
    if not checked:
        return due or is_show
    if not due:
        return False
    try:
        age = (date.fromisoformat(today) - date.fromisoformat(checked)).days
    except ValueError:
        return True
    return age >= FALLBACK_RECHECK_DAYS


def _show_kodi_id(item: Dict[str, Any]) -> int:
    """The episode's show Kodi id, the key of the per-show maps."""
    value = item.get("tvshowid")
    return value if isinstance(value, int) else -1


def _tmdb_season(item: Dict[str, Any], ids: _Ids) -> int:
    """Season number to fetch the episode's TMDB record through."""
    if ids.tmdb_season is not None:
        return ids.tmdb_season
    return int(item.get("season", 0) or 0)


def _premiered_year(item: Dict[str, Any]) -> Optional[int]:
    raw = str(item.get("premiered") or "")[:4]
    return int(raw) if len(raw) == 4 and raw.isdigit() else None


def _show_checked(ctx: _ScanCtx, item: Dict[str, Any]) -> bool:
    """True when the episode's show has answered its id lookups, so a show id
    it lacks is an answer rather than not known yet."""
    return _show_kodi_id(item) in ctx.checked_shows


def _lookup(provider: str, fn: Any, ctx: _ScanCtx, identity: str) -> Any:
    """_call_provider, or ``TRANSIENT`` without a call while the provider's
    breaker is open."""
    if ctx.breaker.is_disabled(provider):
        return TRANSIENT
    return _call_provider(provider, fn, ctx, identity)


def _tvdb_cached(
    ctx: _ScanCtx,
    kind: str,
    tvdb_id: str,
    lang: str,
    fetch: Callable[[str, str], Any],
    identity: str,
) -> Any:
    """A TVDB record through the per-scan cache. Only real answers are cached,
    so a transient failure is retried by the next item."""
    key = (kind, tvdb_id, lang)
    if key in ctx.tvdb_cache:
        return ctx.tvdb_cache[key]
    result = _lookup("tvdb", lambda: fetch(tvdb_id, lang), ctx, identity)
    if result is not TRANSIENT:
        ctx.tvdb_cache[key] = result
    return result


def _verification_lang(folder_lang: str) -> str:
    """TVDB language for the verification fetch: the folder language, so the
    record is the one the text chain asks for anyway, else English."""
    return tvdb_language(folder_lang) or "eng"


def _unknown_verdict(
    ctx: _ScanCtx, ids: _Ids, identity: str, unknown_id: str,
    accepted: Any, reason: str,
) -> None:
    """Apply a verification result (True, False or TRANSIENT) to ``ids``."""
    if accepted is TRANSIENT:
        ids.tvdb_transient = True
        ids.lookup_failed = True
        return
    ids.looked_up = True
    if accepted:
        ids.tvdb = unknown_id
        ctx.tracker.upsert_item(ctx.media_type.name, identity, tvdb_id=unknown_id)
        log.info(
            "Unknown-type id verified as TVDB id",
            event="scan.unknown_id_accepted", identity=identity,
            media_type=ctx.media_type.name, tvdb_id=unknown_id,
        )
    else:
        log.info(
            "Unknown-type id rejected, not used as TVDB id",
            event="scan.unknown_id_rejected", identity=identity,
            media_type=ctx.media_type.name, unknown_id=unknown_id, reason=reason,
        )


def _verify_unknown_show(
    ctx: _ScanCtx, item: Dict[str, Any], identity: str, ids: _Ids,
    unknown_id: str, folder_lang: str,
) -> None:
    """Accept the show's unknown id when the TVDB series it names carries the
    Kodi title among its names, translations or aliases."""
    series = _tvdb_cached(
        ctx, "series", unknown_id, _verification_lang(folder_lang),
        tvdb_fetch_series, identity,
    )
    if series is TRANSIENT:
        _unknown_verdict(ctx, ids, identity, unknown_id, TRANSIENT, "")
        return
    if series is None:
        _unknown_verdict(ctx, ids, identity, unknown_id, False, "no_such_series")
        return
    matched = show_matches(
        item.get("title", ""), series.get("names") or [],
        kodi_year=_premiered_year(item), candidate_year=series.get("year"),
    )
    _unknown_verdict(ctx, ids, identity, unknown_id, matched, "name_mismatch")


def _verify_unknown_episode(
    ctx: _ScanCtx, item: Dict[str, Any], identity: str, ids: _Ids,
    unknown_id: str, folder_lang: str,
) -> None:
    """Accept the episode's unknown id when it is in the show's TVDB episode
    list (one cached fetch per show, no per-episode call)."""
    show_tvdb_id = ctx.show_tvdb_map.get(_show_kodi_id(item))
    if not show_tvdb_id:
        if not _show_checked(ctx, item):
            ids.tvdb_transient = True
            ids.defer()
        # The show has no TVDB id, so no episode list can confirm this one.
        # Derived without a call, so it is not recorded as a lookup.
        return
    episodes = _tvdb_cached(
        ctx, "episodes", show_tvdb_id, _verification_lang(folder_lang),
        tvdb_fetch_series_episodes, identity,
    )
    if episodes is TRANSIENT:
        _unknown_verdict(ctx, ids, identity, unknown_id, TRANSIENT, "")
        return
    listed = bool(episodes) and str(unknown_id) in episodes
    _unknown_verdict(ctx, ids, identity, unknown_id, listed, "not_in_series_list")


def _resolve_tmdb_by_external(ctx: _ScanCtx, identity: str, ids: _Ids) -> None:
    """Movie/show TMDB id from its TVDB or IMDb id via TMDB /find."""
    resolved = _lookup(
        "tmdb",
        lambda: resolve_id(ctx.tmdb_api_key, tvdb_id=ids.tvdb, imdb_id=ids.imdb),
        ctx, identity,
    )
    if resolved is TRANSIENT:
        ids.tmdb_transient = True
        ids.lookup_failed = True
        return
    ids.looked_up = True
    if resolved:
        ids.tmdb = resolved
        ctx.tracker.upsert_item(ctx.media_type.name, identity, tmdb_id=resolved)


def _show_tvdb_from_tmdb(ctx: _ScanCtx, identity: str, ids: _Ids) -> None:
    """Show TVDB id from TMDB's curated external ids."""
    tmdb_id = ids.tmdb or ""
    found = _lookup(
        "tmdb", lambda: tmdb_fetch_tvshow_ids(ctx.tmdb_api_key, tmdb_id),
        ctx, identity,
    )
    if found is TRANSIENT:
        ids.tvdb_transient = True
        ids.lookup_failed = True
        return
    ids.looked_up = True
    ids.tvdb_transient = False
    if found and found.get("tvdb_id"):
        ids.tvdb = found["tvdb_id"]
        ctx.tracker.upsert_item(ctx.media_type.name, identity, tvdb_id=ids.tvdb)


def _episode_tmdb_from_tvdb(
    ctx: _ScanCtx, item: Dict[str, Any], identity: str, ids: _Ids,
) -> None:
    """Episode TMDB id from its TVDB id via TMDB /find.

    Without the show's TMDB id there is nothing to fetch the episode through,
    so no /find is made: when the show's lookups answered that TMDB does not
    have the show, TMDB has no answer for its episodes either; when they have
    not answered yet, the episode's TMDB fields wait for them.
    """
    if not ctx.show_id_map.get(_show_kodi_id(item)):
        if not _show_checked(ctx, item):
            ids.tmdb_transient = True
            ids.defer()
        return
    if not ids.may_look_up:
        return
    tvdb_id = ids.tvdb or ""
    found = _lookup(
        "tmdb", lambda: tmdb_find_episode(ctx.tmdb_api_key, tvdb_id),
        ctx, identity,
    )
    if found is TRANSIENT:
        ids.tmdb_transient = True
        ids.lookup_failed = True
        return
    ids.looked_up = True
    if found:
        ids.tmdb = found["tmdb_id"]
        ids.tmdb_season = found["season"]
        ctx.tracker.upsert_item(
            ctx.media_type.name, identity,
            tmdb_id=ids.tmdb, tmdb_season=ids.tmdb_season,
        )


def _resolve_ids(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    identity: str,
    kodi_ids: Tuple[Optional[str], Optional[str], Optional[str], Optional[str]],
    stored: Dict[str, Any],
    folder_lang: str,
    may_look_up: bool,
) -> _Ids:
    """Settle the item's ids before any field is fetched.

    Kodi's typed ids win; ids found by earlier scans come from the tracker.
    Then, while lookups are allowed: an `unknown` id is verified as a TVDB id
    (shows and episodes), a missing TMDB id is found from the TVDB or IMDb id,
    and a show's missing TVDB id comes from TMDB's external ids. An episode's
    missing TVDB and IMDb ids come from TMDB's episode external ids in
    _fetch_episode_tmdb, which has the TMDB episode numbers. Found ids go to
    the tracker only, never to Kodi.
    """
    kodi_tmdb, kodi_imdb, kodi_tvdb, unknown_id = kodi_ids
    kind = ctx.media_type.name
    ids = _Ids(
        tmdb=kodi_tmdb or stored.get("tmdb_id"),
        imdb=kodi_imdb or stored.get("imdb_id"),
        tvdb=kodi_tvdb or stored.get("tvdb_id"),
        may_look_up=may_look_up,
        # A Kodi-typed TMDB id goes with Kodi's own season number.
        tmdb_season=None if kodi_tmdb else stored.get("tmdb_season"),
    )

    if kind != "movie" and not ids.tvdb and unknown_id and may_look_up:
        verify = _verify_unknown_show if kind == "tvshow" else _verify_unknown_episode
        verify(ctx, item, identity, ids, unknown_id, folder_lang)

    if kind == "episode":
        if not ids.tmdb and ids.tvdb:
            _episode_tmdb_from_tvdb(ctx, item, identity, ids)
        return ids

    if not ids.tmdb and (ids.tvdb or ids.imdb) and may_look_up:
        _resolve_tmdb_by_external(ctx, identity, ids)
    if kind == "tvshow" and ids.tmdb and not ids.tvdb and may_look_up:
        _show_tvdb_from_tmdb(ctx, identity, ids)
    return ids


def _finish_lookups(ctx: _ScanCtx, identity: str, ids: _Ids, today: str) -> None:
    """Record that the item's lookups answered, so an id they left missing is
    not asked for again before FALLBACK_RECHECK_DAYS. Nothing is recorded
    when any lookup failed, so the next scan retries."""
    if ids.looked_up and not ids.lookup_failed:
        ctx.tracker.upsert_item(ctx.media_type.name, identity, ids_checked=today)


@dataclass(frozen=True)
class _Primary:
    """The item's folder-language TMDB answer and what it settled about IDs."""

    # TMDB record, None (TMDB answered "nothing", or no TMDB id), or TRANSIENT.
    tmdb: Any
    # Movie/show TMDB id or the episode's own TMDB id.
    tmdb_id: Optional[str]
    # The episode's show TMDB id; None for movies and shows.
    show_tmdb_id: Optional[str]
    imdb_id: Optional[str]
    # The IMDb id is missing and the lookup that could have found it did not
    # answer, so an IMDb rating cannot be known this scan.
    imdb_id_transient: bool


def _fetch_movie_or_show(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    ids: _Ids,
    language: str,
) -> _Primary:
    """Fetch movie/TV show data from TMDB and write back an IMDb id TMDB
    reports.

    A TMDB call this item needs but cannot make or complete (breaker open, or
    the id lookup failed transiently) yields ``TRANSIENT``, never None: None
    means TMDB answered "nothing", which would advance the maturity clock.
    """
    tmdb_data = None  # type: Any
    tmdb_id = ids.tmdb
    imdb_id = ids.imdb

    if ids.tmdb_transient or (tmdb_id and ctx.breaker.is_disabled("tmdb")):
        tmdb_data = TRANSIENT
    elif tmdb_id:
        fetch = tmdb_fetch_movie if ctx.media_type.name == "movie" else tmdb_fetch_tvshow
        tmdb_data = _call_provider(
            "tmdb",
            lambda: fetch(tmdb_id, ctx.tmdb_api_key, language),
            ctx, identity,
        )

    if (not imdb_id and tmdb_data is not TRANSIENT and tmdb_data
            and tmdb_data.get("imdb_id")):
        imdb_id = tmdb_data["imdb_id"]
        _enrich_imdb_id(ctx, item, kodi_id, title, identity, imdb_id)

    return _Primary(
        tmdb=tmdb_data,
        tmdb_id=tmdb_id,
        show_tmdb_id=None,
        imdb_id=imdb_id,
        imdb_id_transient=not imdb_id and tmdb_data is TRANSIENT,
    )


def _episode_cross_ids(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    ids: _Ids,
    ep_show_tmdb_id: str,
    season_number: int,
    episode_number: int,
) -> bool:
    """Fill the episode's missing IMDb and TVDB ids from TMDB's episode
    external ids. Returns True when the IMDb id stays unknown because the
    lookup did not answer.

    Best effort: a failure does not count against the TMDB breaker. A found
    IMDb id is written back to Kodi (_enrich_imdb_id); a TVDB id goes to the
    tracker only.
    """
    found = _call_provider(
        "tmdb",
        lambda: tmdb_fetch_episode_ids(
            ep_show_tmdb_id, season_number, episode_number, ctx.tmdb_api_key,
        ),
        ctx, identity, best_effort=True,
    )
    if found is TRANSIENT:
        ids.lookup_failed = True
        if not ids.tvdb:
            ids.tvdb_transient = True
        return not ids.imdb
    ids.looked_up = True
    found = found or {}
    if not ids.imdb and found.get("imdb_id"):
        ids.imdb = found["imdb_id"]
        _enrich_imdb_id(ctx, item, kodi_id, title, identity, ids.imdb)
    if not ids.tvdb and found.get("tvdb_id"):
        ids.tvdb = found["tvdb_id"]
        ctx.tracker.upsert_item(ctx.media_type.name, identity, tvdb_id=ids.tvdb)
    return False


def _fetch_episode_tmdb(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    ids: _Ids,
    ep_show_tmdb_id: str,
    season_number: int,
    language: str,
) -> Tuple[Any, bool]:
    """TMDB episode path: look up the season (cached), match by episode ID,
    and fill missing IMDb/TVDB ids. Returns (tmdb_episode_data,
    imdb_id_transient).

    The (cached) season fetch is not preceded by a pacing sleep and only arms
    the pacer on a cache miss; breaker success is recorded once at the end;
    ApiKeyError propagates and a transient failure trips the breaker.
    """
    tmdb_data = None  # type: Any
    imdb_id_transient = False
    try:
        cache_key = "{}:{}:{}".format(ep_show_tmdb_id, season_number, language)
        is_cache_miss = cache_key not in ctx.season_cache
        season_data = _get_season_data(
            ep_show_tmdb_id, season_number, ctx.tmdb_api_key, language, ctx.season_cache
        )
        if season_data is not None:
            tmdb_data = _find_episode_by_tmdb_id(season_data, ids.tmdb or "")
            if (tmdb_data is not None and ids.may_look_up
                    and not (ids.imdb and ids.tvdb)):
                imdb_id_transient = _episode_cross_ids(
                    ctx, item, kodi_id, title, identity, ids,
                    ep_show_tmdb_id, season_number,
                    tmdb_data.get("episode_number", 0),
                )
        if is_cache_miss:
            ctx.pacer.arm()
        ctx.breaker.record_success("tmdb")
    except ApiKeyError:
        log.exception(
            "Provider API key invalid",
            event="scan.apikey", provider="tmdb", identity=identity,
        )
        raise
    except TransientProviderError:
        tmdb_data = TRANSIENT
        if ctx.breaker.record_failure("tmdb"):
            log.warning(
                "Provider disabled for this cycle after repeated failures",
                event="scan.breaker", provider="tmdb",
                threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD, identity=identity,
            )
    return tmdb_data, imdb_id_transient


def _fetch_episode(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    ids: _Ids,
    language: str,
) -> _Primary:
    """Fetch the episode's TMDB record by its TMDB id via the show's season.

    As in _fetch_movie_or_show, a needed TMDB call that cannot be made (breaker
    open, show not resolved yet) yields ``TRANSIENT`` rather than None. A show
    whose lookups answered that TMDB does not have it yields None.
    """
    tmdb_data = None  # type: Any
    imdb_id_transient = False
    tmdb_id = ids.tmdb
    ep_show_tmdb_id = ctx.show_id_map.get(_show_kodi_id(item))

    if ids.tmdb_transient or (tmdb_id and ctx.breaker.is_disabled("tmdb")):
        tmdb_data = TRANSIENT
    elif tmdb_id and not ep_show_tmdb_id:
        if not _show_checked(ctx, item):
            # Not knowing the show is not an answer from TMDB: hold the clock.
            log.debug(
                "No show TMDB ID for episode, TMDB path transient",
                event="scan.skip", title=title, tvshowid=item.get("tvshowid"),
            )
            tmdb_data = TRANSIENT
    elif tmdb_id and ep_show_tmdb_id:
        tmdb_data, imdb_id_transient = _fetch_episode_tmdb(
            ctx, item, kodi_id, title, identity, ids,
            ep_show_tmdb_id, _tmdb_season(item, ids), language,
        )

    if (tmdb_data is TRANSIENT and ids.may_look_up and tmdb_id
            and not (ids.imdb and ids.tvdb)):
        # The episode external ids lookup needs the TMDB record, so it did
        # not run. A missing TVDB id can only come from there.
        ids.defer()
        if not ids.tvdb:
            ids.tvdb_transient = True

    return _Primary(
        tmdb=tmdb_data,
        tmdb_id=tmdb_id,
        show_tmdb_id=ep_show_tmdb_id,
        imdb_id=ids.imdb,
        imdb_id_transient=imdb_id_transient or (not ids.imdb and tmdb_data is TRANSIENT),
    )


_UNFETCHED = object()


class _TextSources:
    """Lazy per-item lookups behind the title/plot chain steps.

    Each method returns the field's value, None (the provider answered and has
    nothing, or the item has no id for it) or ``TRANSIENT``. TMDB in the folder
    language reuses the primary fetch; the fallback-language TMDB record is
    fetched at most once per item, and TVDB records go through the per-scan
    cache so a show's episode list is fetched once per language.
    """

    def __init__(
        self,
        ctx: _ScanCtx,
        item: Dict[str, Any],
        identity: str,
        primary: _Primary,
        ids: _Ids,
    ) -> None:
        self._ctx = ctx
        self._item = item
        self._identity = identity
        self._primary = primary
        self._ids = ids
        self._tmdb_fallback = _UNFETCHED  # type: Any

    def tmdb(self, field: str, fallback: bool) -> Any:
        if fallback:
            if self._tmdb_fallback is _UNFETCHED:
                self._tmdb_fallback = self._fetch_tmdb_fallback()
            data = self._tmdb_fallback
        else:
            data = self._primary.tmdb
        if data is TRANSIENT:
            return TRANSIENT
        return data.get(field) if data else None

    def _fetch_tmdb_fallback(self) -> Any:
        ctx = self._ctx
        p = self._primary
        lang = tmdb_language(ctx.fallback_language)
        if p.tmdb is TRANSIENT:
            return TRANSIENT
        tmdb_id = p.tmdb_id
        if not tmdb_id:
            return None
        if ctx.media_type.name == "episode" and not p.show_tmdb_id:
            return None if _show_checked(ctx, self._item) else TRANSIENT
        if ctx.breaker.is_disabled("tmdb"):
            return TRANSIENT
        if ctx.media_type.name != "episode":
            fetch = tmdb_fetch_movie if ctx.media_type.name == "movie" else tmdb_fetch_tvshow
            return _call_provider(
                "tmdb",
                lambda: fetch(tmdb_id, ctx.tmdb_api_key, lang),
                ctx, self._identity,
            )
        season_number = _tmdb_season(self._item, self._ids)
        show_id = p.show_tmdb_id or ""
        cache_key = "{}:{}:{}".format(show_id, season_number, lang)
        if cache_key in ctx.season_cache:
            season = ctx.season_cache[cache_key]  # type: Any
        else:
            season = _call_provider(
                "tmdb",
                lambda: _get_season_data(
                    show_id, season_number, ctx.tmdb_api_key, lang, ctx.season_cache,
                ),
                ctx, self._identity,
            )
        if season is TRANSIENT or season is None:
            return season
        return _find_episode_by_tmdb_id(season, tmdb_id)

    def tvdb(self, field: str, kodi_lang: str) -> Any:
        ctx = self._ctx
        lang = tvdb_language(kodi_lang)
        if lang is None:
            return None
        if self._ids.tvdb_transient:
            return TRANSIENT
        tvdb_id = self._ids.tvdb
        if not tvdb_id:
            return None
        if ctx.media_type.name == "tvshow":
            series = _tvdb_cached(
                ctx, "series", tvdb_id, lang, tvdb_fetch_series, self._identity,
            )
            if series is TRANSIENT or series is None:
                return series
            return series.get(_TVDB_SERIES_KEYS[field])
        if ctx.media_type.name == "episode":
            show_tvdb_id = ctx.show_tvdb_map.get(_show_kodi_id(self._item))
            if not show_tvdb_id:
                return None if _show_checked(ctx, self._item) else TRANSIENT
            episodes = _tvdb_cached(
                ctx, "episodes", show_tvdb_id, lang, tvdb_fetch_series_episodes,
                self._identity,
            )
            if episodes is TRANSIENT or episodes is None:
                return episodes
            episode = episodes.get(str(tvdb_id))
            return episode.get(field) if episode else None
        return None


def _fallback_applies(ctx: _ScanCtx, folder_lang: str) -> bool:
    """The fallback steps run only when the fallback language is set and is
    not the folder's own language."""
    fallback = ctx.fallback_language.strip()
    return bool(fallback) and tmdb_language(fallback) != tmdb_language(folder_lang)


def _text_chain(
    ctx: _ScanCtx,
    sources: _TextSources,
    field: str,
    folder_lang: str,
    current: Any,
) -> Tuple[FieldOutcome, Optional[str]]:
    """Run the title/plot chain. Returns the outcome and the provider whose
    step produced the value (None when there is no value).

    Shows and episodes ask TMDB and TVDB in the folder language in the
    text_source_order, then (when the fallback language differs) both in the
    fallback language in the fallback_order. Movies ask TMDB only.
    """
    def tmdb_step(fallback: bool) -> Tuple[str, Callable[[], Any], bool]:
        return ("tmdb", lambda: sources.tmdb(field, fallback), fallback)

    def tvdb_step(lang: str, fallback: bool) -> Tuple[str, Callable[[], Any], bool]:
        return ("tvdb", lambda: sources.tvdb(field, lang), fallback)

    movie = ctx.media_type.name == "movie"
    if movie:
        specs = [tmdb_step(False)]
    elif ctx.text_order == TEXT_TVDB_FIRST:
        specs = [tvdb_step(folder_lang, False), tmdb_step(False)]
    else:
        specs = [tmdb_step(False), tvdb_step(folder_lang, False)]

    if _fallback_applies(ctx, folder_lang):
        if movie:
            specs.append(tmdb_step(True))
        elif ctx.fallback_order == FALLBACK_TMDB_FIRST:
            specs += [tmdb_step(True), tvdb_step(ctx.fallback_language, True)]
        else:
            specs += [tvdb_step(ctx.fallback_language, True), tmdb_step(True)]

    heard = []  # type: List[str]

    def step(provider: str, fetch: Callable[[], Any], fallback: bool) -> Step:
        def run() -> Any:
            heard.append(provider)
            return fetch()
        return Step(fetch=run, fallback=fallback)

    outcome = run_chain([step(*spec) for spec in specs], current)
    return outcome, (heard[-1] if outcome.value is not None else None)


def _imdb_rating(ctx: _ScanCtx, primary: _Primary, identity: str) -> Any:
    """The item's IMDb (rating, votes), None when IMDb has none, or TRANSIENT
    when the store is not ready, the IMDb id could not be settled, or the
    lookup failed."""
    if not ctx.imdb_ready or primary.imdb_id_transient:
        return TRANSIENT
    if not primary.imdb_id:
        return None
    try:
        return imdb_rating_lookup(primary.imdb_id)
    except TransientProviderError:
        return TRANSIENT
    except Exception:
        event = "scan.imdb_lookup_failed"
        if event in ctx.logged_once:
            log.debug("IMDb rating lookup failed again", event=event, identity=identity)
        else:
            ctx.logged_once.add(event)
            log.exception("IMDb rating lookup failed", event=event, identity=identity)
        return TRANSIENT


def _resolve_ratings(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    primary: _Primary,
    identity: str,
    nfo_managed: bool,
    owned_slots: Dict[str, str],
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str], Optional[int], Dict[str, str]]:
    """Ratings verdict and map. Returns (transient, new_ratings_map,
    default_source, default_votes, slot_fingerprints of the slots written).

    Transient when a source rating_source consults did not answer for this
    item; a source it does not consult cannot hold the rating back.
    """
    use_tmdb, use_imdb = rating_sources(ctx.rating_pref)
    imdb = _imdb_rating(ctx, primary, identity) if use_imdb else None
    if (use_tmdb and primary.tmdb is TRANSIENT) or imdb is TRANSIENT:
        return True, None, None, None, {}
    tmdb_data = primary.tmdb if primary.tmdb is not TRANSIENT else None
    new_ratings, source, votes, slot_fps = build_ratings(
        tmdb_data or {}, imdb, item.get("ratings") or {},
        ctx.rating_pref, nfo_managed=nfo_managed, owned_slots=owned_slots,
    )
    return False, new_ratings, source, votes, slot_fps


def _resolve_fields(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    identity: str,
    primary: _Primary,
    ids: _Ids,
    folder_lang: str,
    fields: List[str],
) -> Tuple[Dict[str, FieldOutcome], Dict[str, Any], Dict[str, Optional[str]]]:
    """Per-field verdicts for the non-rating fields.

    Returns (outcomes keyed by Kodi field, provider values keyed by TMDB key
    for _extract_fields, winning provider per Kodi field).
    """
    outcomes = {}  # type: Dict[str, FieldOutcome]
    values = {}  # type: Dict[str, Any]
    providers = {}  # type: Dict[str, Optional[str]]
    text = _TextSources(ctx, item, identity, primary, ids)
    tmdb_transient = primary.tmdb is TRANSIENT
    tmdb_data = {} if tmdb_transient or not primary.tmdb else primary.tmdb

    for f in fields:
        if f in _RATING_FIELDS:
            continue
        key = _KODI_TO_TMDB.get(f, f)
        if f in _CHAIN_FIELDS:
            outcome, provider = _text_chain(ctx, text, f, folder_lang, item.get(f))
        else:
            value = None if tmdb_transient else tmdb_data.get(key)
            outcome = FieldOutcome(transient=tmdb_transient, value=value, from_fallback=False)
            provider = None if is_empty(value) else "tmdb"
        outcomes[f] = outcome
        providers[f] = provider
        if outcome.value is not None:
            values[key] = outcome.value
    return outcomes, values, providers


def _write_fields(
    ctx: _ScanCtx,
    kodi_id: Any,
    title: str,
    identity: str,
    fields_to_write: Dict[str, Any],
    stats: Dict[str, Any],
) -> set:
    """Write fields to Kodi and count stats. Returns the set of fields written
    this pass (empty if the write failed or there was nothing to write).

    Field facts (fetch_count, next_retry, source, vote_count) are recorded by the
    caller via tracker.record_field_fetch, not here.
    """
    ratings_written = "ratings" in fields_to_write
    write_succeeded = False

    if fields_to_write:
        write_succeeded = update_item(kodi_id, ctx.media_type, title, **fields_to_write)
        if write_succeeded:
            stats["updated"] += 1
            for field_name in fields_to_write:
                stats["fields"][field_name] = stats["fields"].get(field_name, 0) + 1
            log.info(
                "Item updated",
                event="scan.updated", title=title, identity=identity,
                media_type=ctx.media_type.name,
                fields_written=list(fields_to_write.keys()),
            )
        else:
            stats["errors"] += 1

    written = set()  # type: set
    if write_succeeded:
        written = set(fields_to_write.keys())
        if ratings_written:
            written.update({"rating", "votes"})
            written.discard("ratings")
    return written


def _yield_to_playback(ctx: _ScanCtx, monitor: Any, processed: int) -> None:
    """Pause the scan while a video is playing, resume when it stops."""
    if monitor is None or not xbmc.getCondVisibility("Player.HasVideo"):
        return
    log.info(
        "Scan paused, video playing",
        event="scan.pause", media_type=ctx.media_type.name, processed=processed,
    )
    while (xbmc.getCondVisibility("Player.HasVideo")
           and not monitor.abortRequested()):
        if monitor.waitForAbort(10):
            break
    if not monitor.abortRequested():
        log.info(
            "Scan resumed, playback stopped",
            event="scan.resume", media_type=ctx.media_type.name,
        )


def _candidate_fields(media_type: MediaType, enabled_fields: FrozenSet[str]) -> List[str]:
    """Fields this pass manages: enabled, backfilled for the media type, and
    known to the providers. Titles are managed for episodes only."""
    fields = (
        {_TMDB_TO_KODI.get(k, k) for k in _TMDB_TO_KODI}
        & enabled_fields & media_type.backfill_fields
    )
    if media_type.name != "episode":
        fields.discard("title")
    return sorted(fields)


def _needed_providers(media_type: MediaType, candidates: List[str]) -> List[str]:
    """Providers the pass can still make progress with. TMDB always; TVDB
    when a show or episode text field it supplies is managed. The IMDb store
    has no breaker, so it never stops a pass."""
    needed = ["tmdb"]
    if media_type.name != "movie" and _CHAIN_FIELDS & set(candidates):
        needed.append("tvdb")
    return needed


def run_scan(
    media_type: MediaType,
    tracker: TrackerDB,
    source_config: Dict[str, Any],
    enabled_fields: FrozenSet[str],
    tmdb_api_key: str,
    settings: Dict[str, Any],
    monitor: Any = None,
    show_id_map: Optional[Dict[int, str]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    kodi_filter: Optional[Dict[str, str]] = None,
    breaker: Optional[CircuitBreaker] = None,
    reset: bool = False,
) -> Dict[str, int]:
    """Run a single scan cycle for one media type.

    Queries Kodi for all items (or uses a pre-built list), computes a per-field
    maturity decision for each, fetches metadata when any field is due,
    refreshes non-final fields overwrite-on-diff, and records the per-field
    sampling facts in the tracker.

    Args:
        media_type: MOVIE, TVSHOW, or EPISODE.
        tracker: Open TrackerDB instance.
        source_config: Loaded source config dict with "sources" list
            and "default_language".
        enabled_fields: Set of field names the user wants backfilled.
        tmdb_api_key: TMDB API key.
        settings: Dict with stale_threshold_days, rate_limit, rating_source,
            text_source_order, fallback_order, fallback_language, imdb_ready
            (whether the IMDb ratings store is loaded this cycle; an IMDb
            rating is transient while it is False) and optionally
            refetch_schedule.
        monitor: xbmc.Monitor instance (has abortRequested()), or
            None for tests.
        show_id_map: Dict mapping a show's Kodi id to its show-level TMDB ID.
            Required for episode scans so fetch_season uses the show
            ID, not the episode ID. The show TVDB ids come from the tracker.
        items: Pre-built list of Kodi items to process. When provided,
            skips the Kodi library query. Useful for targeted scans
            (single item, folder, season) and testing.
        breaker: Shared CircuitBreaker for this scan cycle. The daemon
            creates one per cycle and passes the same instance into each
            media-type pass so a provider that fails repeatedly stays
            disabled across passes. When None, a fresh per-call breaker
            is created (single-pass / test convenience).
        reset: Single-item "Update now" re-baseline path. When True every
            candidate field is treated as due regardless of recorded maturity,
            the disabled-source gate is skipped (so the action always runs),
            the playback yield is skipped, and each field's clock is
            re-baselined (reset_field_clock) instead of advanced. Intended
            for items=[one_item].

    Returns:
        Stats dict with processed, skipped, updated, errors, and
        per-field counts.
    """
    scan_start = time.perf_counter()
    stats = _make_stats()
    if breaker is None:
        breaker = CircuitBreaker()
    raw_schedule = settings.get("refetch_schedule")
    if isinstance(raw_schedule, str):
        schedule = parse_refetch_schedule(raw_schedule)
    elif raw_schedule:
        schedule = tuple(raw_schedule)
    else:
        schedule = DEFAULT_REFETCH_SCHEDULE
    ctx = _ScanCtx(
        media_type=media_type,
        tracker=tracker,
        breaker=breaker,
        pacer=_Pacer(settings.get("rate_limit", 0.25)),
        tmdb_api_key=tmdb_api_key,
        default_language=source_config.get("default_language", "en-US"),
        sources=source_config.get("sources", []),
        enabled_fields=enabled_fields,
        show_id_map=show_id_map or {},
        show_tvdb_map=(
            tracker.get_show_tvdb_map() if media_type.name == "episode" else {}
        ),
        checked_shows=(
            tracker.get_checked_show_ids()
            if media_type.name == "episode" else frozenset()
        ),
        season_cache={},
        tvdb_cache={},
        logged_once=set(),
        schedule=schedule,
        rating_pref=settings.get("rating_source", 0),
        text_order=settings.get("text_source_order", TEXT_TMDB_FIRST),
        fallback_order=settings.get("fallback_order", FALLBACK_TVDB_FIRST),
        fallback_language=settings.get("fallback_language", DEFAULT_FALLBACK_LANGUAGE),
        imdb_ready=bool(settings.get("imdb_ready", False)),
    )
    candidates = _candidate_fields(media_type, enabled_fields)
    needed = _needed_providers(media_type, candidates)

    log.info(
        "Scan starting",
        event="scan.start",
        media_type=media_type.name,
        reset=reset,
    )

    if items is None:
        items = get_all_items(media_type, kodi_filter=kodi_filter)

    for item in items:
        # Check for Kodi shutdown
        if monitor is not None and monitor.abortRequested():
            log.info(
                "Scan interrupted by shutdown",
                event="scan.abort",
                media_type=media_type.name,
                processed=stats["processed"],
            )
            stats["aborted"] = True
            break

        # Yield: pause while video is playing. The reset path (Update now)
        # skips this so a manual single-item action does not silently block.
        if not reset:
            _yield_to_playback(ctx, monitor, stats["processed"])

        identity = compute_identity(item, media_type)
        title = _item_title(item, media_type)
        kodi_id = item.get(media_type.kodi_id_field, 0)

        # Resume: skip items already processed in this cycle. The reset path
        # (Update now) bypasses this so a just-scanned item can still be
        # corrected on demand.
        if not reset:
            pre_tracker = tracker.get_item(media_type.name, identity)
            if pre_tracker and pre_tracker.get("cycle_processed"):
                stats["skipped"] += 1
                continue

        # Match source. The reset path (Update now) bypasses the disabled-source
        # gate: the user explicitly requested a refresh, so the source setting
        # must not silently block it.
        source = match_source(item.get("file", ""), ctx.sources)
        if not reset and not source.get("enabled", True):
            log.debug(
                "Skipping disabled source",
                event="scan.skip",
                title=title,
                source_path=source.get("path", ""),
            )
            stats["skipped"] += 1
            continue

        language = source.get("language", ctx.default_language)

        # Extract external IDs
        kodi_ids = _extract_ids(item)
        if not any(kodi_ids):
            log.debug(
                "Skipping item without external IDs",
                event="scan.skip",
                title=title,
            )
            stats["skipped"] += 1
            continue
        tmdb_id, imdb_id, tvdb_id, _unknown = kodi_ids

        # Sync to tracker. None ids are not written, so ids found by earlier
        # scans survive and are read back below.
        tracker.upsert_item(
            media_type.name,
            identity,
            title=title,
            kodi_id=kodi_id,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            source_path=item.get("file", ""),
            premiered=item.get(media_type.date_field, ""),
            date_added=item.get("dateadded", ""),
        )
        tracker.update_last_seen(media_type.name, identity, date.today().isoformat())
        stored = tracker.get_item(media_type.name, identity) or {}

        # --- Per-field maturity decisions (see maturity.decide_field) ---
        today = date.today().isoformat()
        age = _content_age_days(item, media_type, today)
        fs_map = tracker.get_field_status_map(media_type.name, identity)
        threshold = settings.get("stale_threshold_days", 365)

        # A field sampled under other settings (or before fingerprints were
        # stored) is re-checked as if never sampled, and re-baselined once it
        # answers. A transient keeps the old fingerprint, so it stays due.
        fps = {
            f: settings_fingerprint(f, settings, language, media_type.name)
            for f in candidates
        }
        rebaseline = {
            f for f in candidates
            if reset or (f in fs_map and fs_map[f].get("settings_fp") != fps[f])
        }

        decisions = {}  # type: Dict[str, str]
        for f in candidates:
            if f in rebaseline:
                # Update now, or changed settings: due regardless of the
                # recorded maturity, so the clock re-baselines.
                decisions[f] = FETCH_NOW
                continue
            fs = fs_map.get(f, {})
            decisions[f] = apply_fallback_recheck(
                decide_field(
                    age,
                    int(fs.get("fetch_count") or 0),
                    fs.get("next_retry"),
                    today,
                    threshold,
                ),
                bool(fs.get("from_fallback")),
                int(fs.get("fallback_checks") or 0),
                fs.get("next_retry"),
                today,
            )

        due = any(d == FETCH_NOW for d in decisions.values())

        # NFO detection touches the share, so it runs once, and only for an
        # item with a field to process. Unreachable media (a sleeping or
        # offline share) says nothing about the NFO: the item is left exactly
        # as it is, ids_checked and cycle flag included, and retried later.
        nfo_managed = False
        if due:
            state = nfo_state(media_type.name, item.get("file", ""))
            if state == UNREACHABLE:
                log.debug(
                    "Media unreachable, skipping item",
                    event="scan.skip", reason="unreachable", title=title,
                    identity=identity, media_type=media_type.name,
                )
                stats["skipped"] += 1
                stats["unreachable"] += 1
                continue
            nfo_managed = state == NFO

        ids = _resolve_ids(
            ctx, item, identity, kodi_ids, stored, language,
            _lookup_allowed(
                stored.get("ids_checked"), today, due, media_type.name == "tvshow",
            ),
        )

        # Nothing is due this scan: mark processed and move on without a fetch.
        if not due:
            _finish_lookups(ctx, identity, ids, today)
            tracker.mark_cycle_processed(media_type.name, identity)
            stats["skipped"] += 1
            continue

        # Only an unverified (rejected, or not yet answered) unknown id: no
        # provider can be asked, and no field is sampled.
        if not ids.any():
            log.debug(
                "Item has no usable ID, skipping",
                event="scan.skip", title=title, media_type=media_type.name,
                tvdb_transient=ids.tvdb_transient,
            )
            _finish_lookups(ctx, identity, ids, today)
            tracker.mark_cycle_processed(media_type.name, identity)
            stats["skipped"] += 1
            continue

        log.debug(
            "Processing item",
            event="scan.processing",
            title=title,
            identity=identity,
            media_type=media_type.name,
            tmdb_id=ids.tmdb,
            tvdb_id=ids.tvdb,
            decisions=decisions,
            rebaseline=sorted(rebaseline),
        )

        if breaker.all_disabled(needed):
            log.warning(
                "Every provider the enabled fields need is disabled, stopping scan",
                event="scan.breaker", scope="all", providers=needed,
                media_type=media_type.name, processed=stats["processed"],
            )
            stats["aborted"] = True
            break

        tmdb_lang = tmdb_language(language)
        fetch_primary = (
            _fetch_episode if media_type.name == "episode" else _fetch_movie_or_show
        )
        primary = fetch_primary(ctx, item, kodi_id, title, identity, ids, tmdb_lang)

        # Non-final fields are refreshed (overwrite-on-diff); final ones are not.
        nonfinal = [f for f, d in decisions.items() if d != FINAL]
        outcomes, values, providers = _resolve_fields(
            ctx, item, identity, primary, ids, language, nonfinal,
        )
        answered = [f for f in nonfinal if f in outcomes and not outcomes[f].transient]
        extracted = _extract_fields(values, answered)
        fields_to_write, held = _plan_writes(
            extracted, item, nfo_managed, fs_map, identity,
        )
        stats["protected"] += len(extracted) - len(fields_to_write) - len(held)

        ratings_source = None  # type: Optional[str]
        ratings_votes = None  # type: Optional[int]
        owned_slots = _owned_slots(fs_map, item.get("ratings") or {})
        slot_fps = {}  # type: Dict[str, str]
        rating_due = [f for f in nonfinal if f in _RATING_FIELDS]
        if rating_due:
            (transient, new_ratings, ratings_source, ratings_votes,
             slot_fps) = _resolve_ratings(
                ctx, item, primary, identity, nfo_managed, owned_slots,
            )
            rating_outcome = FieldOutcome(
                transient=transient, value=None, from_fallback=False,
            )
            for f in rating_due:
                outcomes[f] = rating_outcome
            if new_ratings is not None:
                fields_to_write["ratings"] = new_ratings

        written = _write_fields(
            ctx, kodi_id, title, identity, fields_to_write, stats,
        )

        # A field whose write was attempted and rejected by Kodi was NOT filled,
        # so its clock must not advance: otherwise aged content reaches FINAL on
        # the next scan and is never written again. Fields with nothing to write
        # (provider answered, value empty or unchanged) are a clean sample and
        # do advance.
        attempted = set(fields_to_write)
        if "ratings" in attempted:
            attempted.discard("ratings")
            attempted.update(_RATING_FIELDS)
        write_failed = attempted - written

        # Provenance: the fingerprint of each value as written to Kodi. Rating
        # rows hold the per-slot fingerprints as JSON, merged over the slots
        # owned before so a slot not rewritten this scan stays owned.
        provenance = {
            f: fingerprint(fields_to_write[f])
            for f in written if f in fields_to_write
        }  # type: Dict[str, str]
        if slot_fps and _RATING_FIELDS & written:
            merged = dict(owned_slots)
            merged.update(slot_fps)
            for f in _RATING_FIELDS & written:
                provenance[f] = json.dumps(merged, sort_keys=True)

        # Advance a field's clock ONLY when its own providers answered; a
        # transient field is retried next scan instead of being wrongly
        # sampled/finalized. A protected field is answered and advances.
        # Update now and changed settings re-baseline instead.
        premiered = item.get(media_type.date_field, "")
        for f in nonfinal:
            outcome = outcomes[f]
            if outcome.transient or f in write_failed:
                continue
            fs = fs_map.get(f, {})
            new_count = 1 if f in rebaseline else int(fs.get("fetch_count") or 0) + 1
            next_due = compute_next_due(
                new_count, ctx.schedule, premiered, threshold, today,
            )
            if f in _RATING_FIELDS:
                # Rating/votes are sourced from the named-slot ratings map;
                # record the chosen slot and its votes for staleness.
                source = ratings_source
                vote_count = ratings_votes
                from_fallback = None  # type: Optional[bool]
            else:
                # Source describes the value Kodi now holds, so it is recorded
                # only when the answer is in Kodi (written now, or already
                # equal); a dropped or protected answer keeps the old one. The
                # fallback flag follows a write, and a folder-language answer
                # equal to Kodi's value clears it: the value no longer depends
                # on the fallback language.
                source = providers.get(f) if f in written or f in held else None
                vote_count = None
                if f in written:
                    from_fallback = outcome.from_fallback
                elif f in held and not outcome.from_fallback:
                    from_fallback = False
                else:
                    from_fallback = None
            stays_fallback = (
                from_fallback if from_fallback is not None
                else bool(fs.get("from_fallback"))
            )
            if stays_fallback:
                # Past the stability age the schedule no longer applies, so
                # the recheck is a month out; younger content keeps an
                # earlier schedule date.
                mature = age is not None and age >= threshold
                next_due = fallback_recheck_due(None if mature else next_due, today)
            record = (
                tracker.reset_field_clock if f in rebaseline
                else tracker.record_field_fetch
            )
            record(
                media_type.name, identity, f,
                next_retry=next_due, settings_fp=fps[f],
                written_value=provenance.get(f), source=source,
                vote_count=vote_count, from_fallback=from_fallback,
            )
        _finish_lookups(ctx, identity, ids, today)
        tracker.commit()

        tracker.mark_cycle_processed(media_type.name, identity)
        stats["processed"] += 1

    log.info(
        "Scan complete",
        event="scan.complete",
        media_type=media_type.name,
        reset=reset,
        processed=stats["processed"],
        skipped=stats["skipped"],
        updated=stats["updated"],
        errors=stats["errors"],
        unreachable=stats["unreachable"],
        protected=stats["protected"],
        disabled=breaker.disabled(),
        duration_ms=int((time.perf_counter() - scan_start) * 1000),
    )

    return stats

"""
Scan orchestrator for MetaMender.

Coordinates a single scan cycle: queries Kodi, detects gaps,
calls providers, writes results, and updates the tracker.

Logging:
    Logger: 'scan'
    Key events:
        - scan.start (INFO): Scan cycle starting for a media type
        - scan.complete (INFO): Scan cycle finished with stats
        - scan.skip (DEBUG): Item skipped (disabled source, not due, no candidates)
        - scan.abort (INFO): Scan interrupted by Kodi shutdown
        - scan.processing (DEBUG): Starting to process a single item
        - scan.updated (INFO): Item metadata written to Kodi
        - scan.breaker (WARNING): A provider tripped, or both providers down (scope="both")
        - scan.apikey (ERROR): A provider rejected the API key (terminal)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import xbmc

from resources.lib.constants import (
    DEFAULT_REFETCH_SCHEDULE,
    PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
    parse_refetch_schedule,
)
from resources.lib.data.identity import compute_identity
from resources.lib.data.kodi import get_all_items, update_item
from resources.lib.data.media_types import MediaType
from resources.lib.data.sources import match_source
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.maturity import (
    FETCH_NOW,
    FINAL,
    FINAL_EMPTY,
    compute_next_due,
    decide_field,
)
from resources.lib.engine.resolve import (
    IMDB_ONLY,
    build_ratings,
    resolve_metadata,
    wants_imdb,
)
from resources.lib.providers.imdbapi import (
    fetch_episode as imdb_fetch_episode,
    fetch_movie as imdb_fetch_movie,
)
from resources.lib.providers.tmdb import (
    fetch_episode_external_ids as tmdb_fetch_episode_ids,
    fetch_movie as tmdb_fetch_movie,
    fetch_season as tmdb_fetch_season,
    fetch_tvshow as tmdb_fetch_tvshow,
    resolve_id,
)
from resources.lib.utils import (
    ApiKeyError,
    TransientProviderError,
    get_logger,
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

# Fields resolved by resolve_metadata for a plain SetXDetails write. "rating"
# and "votes" are excluded: they are written via the named-slot ratings map
# (build_ratings), not as flat fields, so resolving them here would be discarded.
_RESOLVE_FIELDS = [k for k in _TMDB_TO_KODI if k not in ("rating", "votes")]


def _make_stats() -> Dict[str, Any]:
    """Return a fresh stats dict."""
    return {
        "processed": 0,
        "skipped": 0,
        "updated": 0,
        "errors": 0,
        "fields": {},
        # True when the pass stopped before reaching the end of the library
        # (shutdown, or every provider disabled). The caller must not treat
        # such a pass as a completed cycle.
        "aborted": False,
    }


# Fields the active source setting cannot supply -> immediate final-empty.
# IMDb /titles has no tagline/studio/trailer, so an IMDb-only run can never
# fill them and should not keep sampling. IMDb does supply countries
# (originCountries), so "country" is intentionally absent from this set.
# Episode firstaired is TMDB-only (the direct IMDb episode fetch has no air
# date), so it is included here; it has no effect for movies/shows because
# "firstaired" is not in their candidate field sets.
_TMDB_ONLY_FIELDS = frozenset({"tagline", "studio", "trailer", "firstaired"})


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


def _structurally_absent(field: str, default_pref: int) -> bool:
    """Whether the active source setting cannot supply this field at all."""
    return default_pref == IMDB_ONLY and field in _TMDB_ONLY_FIELDS


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


def _extract_ids(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract (tmdb_id, imdb_id, tvdb_id) from a Kodi item's uniqueid dict."""
    uid = item.get("uniqueid", {})
    return (
        uid.get("tmdb") or uid.get("themoviedb"),
        uid.get("imdb"),
        uid.get("tvdb") or uid.get("thetvdb"),
    )


def _has_any_id(tmdb_id: Optional[str], imdb_id: Optional[str], tvdb_id: Optional[str]) -> bool:
    """Return True if at least one external ID is available."""
    return bool(tmdb_id or imdb_id or tvdb_id)


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


def _extract_fields(
    provider_data: Dict[str, Any],
    gap_fields: List[str],
    media_type: MediaType,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract writable Kodi fields from provider data for the requested fields.

    Under the maturity model a non-final field is refreshed overwrite-on-diff:
    a differing, non-empty provider value overwrites the current Kodi value
    (even when that value is already non-empty). Skips "mpaa" (Phase 8) and the
    named-slot rating/votes (handled by build_ratings).

    Episode titles take the latest provider value (no placeholder gating).

    Guards that remain: empty provider values are skipped, identical values are
    never rewritten (same-value skip), runtime is converted minutes -> seconds,
    and firstaired is only written when it is a full ISO date (bare years are
    skipped).
    """
    fields = {}  # type: Dict[str, Any]

    for tmdb_key, kodi_key in _TMDB_TO_KODI.items():
        if kodi_key not in gap_fields:
            continue

        # Rating and votes are written via the named-slot ratings map
        # (see build_ratings + the scan write path), never as flat fields.
        if kodi_key in ("rating", "votes"):
            continue

        value = provider_data.get(tmdb_key)

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

        current = item.get(kodi_key)

        # Same-value skip: never rewrite identical data.
        if current is not None and current == value:
            continue

        fields[kodi_key] = value

    return fields


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
    (breaker, pacer, season_cache, tracker) are referenced, not copied.
    """

    media_type: MediaType
    tracker: TrackerDB
    breaker: CircuitBreaker
    pacer: _Pacer
    tmdb_api_key: str
    default_language: str
    sources: List[Dict[str, Any]]
    enabled_fields: FrozenSet[str]
    show_id_map: Dict[str, str]
    season_cache: Dict[str, Optional[List[Dict[str, Any]]]]
    settings: Dict[str, Any]
    schedule: Tuple[int, ...]
    rating_pref: int
    plot_pref: int
    default_pref: int


# Sentinel returned by _call_provider when a provider transient-failed (as
# opposed to a clean "no data" 404, which returns None). It lets the caller tell
# "provider down" from "provider has nothing" so counters only advance on a
# clean answer.
_TRANSIENT = object()


def _call_provider(
    provider: str,
    fn: Any,
    ctx: _ScanCtx,
    identity: str,
    best_effort: bool = False,
) -> Any:
    """Pace, run a provider fn, and apply circuit-breaker bookkeeping.

    Records breaker success on any non-raising return (including None). On
    ApiKeyError re-raises (terminal). On TransientProviderError records a breaker
    failure, logs the trip once, and returns the ``_TRANSIENT`` sentinel. When
    ``best_effort`` is True (episode ID enrichment) BOTH exceptions are
    swallowed, logged at scan.error, None is returned, and the breaker is left
    untouched.
    """
    ctx.pacer.wait()
    try:
        result = fn()
    except ApiKeyError:
        if best_effort:
            log.exception(
                "Provider call failed (best effort)",
                event="scan.error", provider=provider, identity=identity,
            )
            return None
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
            return None
        if ctx.breaker.record_failure(provider):
            log.warning(
                "Provider disabled for this cycle after repeated failures",
                event="scan.breaker", provider=provider,
                threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD, identity=identity,
            )
        return _TRANSIENT
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


def _fetch_movie_or_show(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    tmdb_id: Optional[str],
    imdb_id: Optional[str],
    tvdb_id: Optional[str],
    language: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """Fetch movie/TV show data from TMDB then IMDb. Returns
    (tmdb_data, imdb_data, imdb_id). Caller has checked both_disabled().

    A provider this item needs but cannot reach (breaker open, or the TMDB ID
    lookup failed transiently) yields ``_TRANSIENT``, never None: None means the
    provider answered "nothing", which would advance the maturity clock.
    """
    tmdb_data = None  # type: Any
    imdb_data = None  # type: Any

    # Resolve TMDB ID if missing.
    if not tmdb_id:
        if ctx.breaker.is_disabled("tmdb"):
            tmdb_data = _TRANSIENT
        else:
            resolved = _call_provider(
                "tmdb",
                lambda: resolve_id(ctx.tmdb_api_key, tvdb_id=tvdb_id, imdb_id=imdb_id),
                ctx, identity,
            )
            if resolved is _TRANSIENT:
                tmdb_data = _TRANSIENT
            elif resolved:
                tmdb_id = resolved
                ctx.tracker.upsert_item(ctx.media_type.name, identity, tmdb_id=tmdb_id)

    # Fetch TMDB data.
    if tmdb_id and ctx.breaker.is_disabled("tmdb"):
        tmdb_data = _TRANSIENT
    elif tmdb_id:
        if ctx.media_type.name == "movie":
            tmdb_data = _call_provider(
                "tmdb",
                lambda: tmdb_fetch_movie(tmdb_id, ctx.tmdb_api_key, language),
                ctx, identity,
            )
        else:
            tmdb_data = _call_provider(
                "tmdb",
                lambda: tmdb_fetch_tvshow(tmdb_id, ctx.tmdb_api_key, language),
                ctx, identity,
            )

    # Enrich: if TMDB returned an IMDB ID and the item lacks one, write it back.
    if (not imdb_id and tmdb_data is not _TRANSIENT and tmdb_data
            and tmdb_data.get("imdb_id")):
        new_imdb = tmdb_data["imdb_id"]
        _enrich_imdb_id(ctx, item, kodi_id, title, identity, new_imdb)
        imdb_id = new_imdb

    # Fetch IMDb data if a source setting would consult it.
    if imdb_id and wants_imdb(ctx.default_pref, ctx.rating_pref, ctx.plot_pref):
        if ctx.breaker.is_disabled("imdb"):
            imdb_data = _TRANSIENT
        else:
            imdb_data = _call_provider(
                "imdb", lambda: imdb_fetch_movie(imdb_id), ctx, identity,
            )

    return tmdb_data, imdb_data, imdb_id


def _fetch_episode_tmdb(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    ep_show_tmdb_id: str,
    season_number: int,
    tmdb_id: str,
    imdb_id: Optional[str],
    language: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """TMDB episode path: look up the season (cached), match by episode ID, and
    best-effort enrich the IMDB ID. Returns (tmdb_episode_data, imdb_id).

    Mirrors the original behaviour exactly: the (cached) season fetch is NOT
    preceded by a pacing sleep and only arms the pacer on a cache MISS; breaker
    success is recorded once at the end; ApiKeyError propagates and a transient
    failure trips the breaker.
    """
    tmdb_data = None  # type: Any
    try:
        cache_key = "{}:{}:{}".format(ep_show_tmdb_id, season_number, language)
        is_cache_miss = cache_key not in ctx.season_cache
        season_data = _get_season_data(
            ep_show_tmdb_id, season_number, ctx.tmdb_api_key, language, ctx.season_cache
        )
        if season_data is not None:
            tmdb_data = _find_episode_by_tmdb_id(season_data, tmdb_id)
            # Enrich: if the episode has no IMDB ID, fetch it from TMDB.
            if not imdb_id and tmdb_data is not None:
                ep_num = tmdb_data.get("episode_number", 0)
                enriched = _call_provider(
                    "tmdb",
                    lambda: tmdb_fetch_episode_ids(
                        ep_show_tmdb_id, season_number, ep_num, ctx.tmdb_api_key,
                    ),
                    ctx, identity, best_effort=True,
                )
                if enriched:
                    _enrich_imdb_id(ctx, item, kodi_id, title, identity, enriched)
                    imdb_id = enriched
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
        # Signal "provider down", not "provider has nothing": returning None
        # here would make _fetch_for_item compute fetch_clean=True and advance
        # the maturity clock on an answer that was never received.
        tmdb_data = _TRANSIENT
        if ctx.breaker.record_failure("tmdb"):
            log.warning(
                "Provider disabled for this cycle after repeated failures",
                event="scan.breaker", provider="tmdb",
                threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD, identity=identity,
            )
    return tmdb_data, imdb_id


def _fetch_episode(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    tmdb_id: Optional[str],
    imdb_id: Optional[str],
    language: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """Fetch episode data by ID. Returns (tmdb_data, imdb_data, imdb_id). Caller
    has checked both_disabled() and that at least one ID is present.

    As in _fetch_movie_or_show, a needed provider whose breaker is open yields
    ``_TRANSIENT`` rather than None.
    """
    tmdb_data = None  # type: Any
    imdb_data = None  # type: Any

    # TMDB path first (so enrichment can provide imdb_id for the IMDb path).
    if tmdb_id and ctx.breaker.is_disabled("tmdb"):
        tmdb_data = _TRANSIENT
    elif tmdb_id:
        showtitle = item.get("showtitle", "")
        ep_show_tmdb_id = (ctx.show_id_map or {}).get(showtitle)
        if not ep_show_tmdb_id:
            log.debug(
                "No show TMDB ID for episode, skipping TMDB path",
                event="scan.skip", title=title, showtitle=showtitle,
            )
        if ep_show_tmdb_id:
            season_number = item.get("season", 0)
            tmdb_data, imdb_id = _fetch_episode_tmdb(
                ctx, item, kodi_id, title, identity,
                ep_show_tmdb_id, season_number, tmdb_id, imdb_id, language,
            )

    # IMDb path: direct episode fetch by IMDB ID, if a source setting wants it.
    if imdb_id and wants_imdb(ctx.default_pref, ctx.rating_pref, ctx.plot_pref):
        if ctx.breaker.is_disabled("imdb"):
            imdb_data = _TRANSIENT
        else:
            imdb_data = _call_provider(
                "imdb", lambda: imdb_fetch_episode(imdb_id), ctx, identity,
            )

    return tmdb_data, imdb_data, imdb_id


def _fetch_for_item(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    tmdb_id: Optional[str],
    imdb_id: Optional[str],
    tvdb_id: Optional[str],
    language: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], bool]:
    """Dispatch the movie/show or episode fetch and report whether the answer
    is clean.

    Wraps the existing _fetch_movie_or_show / _fetch_episode dispatch. A
    consulted provider that transient-failed yields the _TRANSIENT sentinel;
    this converts it to None (so resolve treats it as "no data") and reports
    fetch_clean=False. A clean 404 stays None with fetch_clean=True. Counters
    advance only when fetch_clean is True.
    """
    if ctx.media_type.name == "episode":
        tmdb_raw, imdb_raw, _ = _fetch_episode(
            ctx, item, kodi_id, title, identity, tmdb_id, imdb_id, language,
        )
    else:
        tmdb_raw, imdb_raw, _ = _fetch_movie_or_show(
            ctx, item, kodi_id, title, identity,
            tmdb_id, imdb_id, tvdb_id, language,
        )

    fetch_clean = not (tmdb_raw is _TRANSIENT or imdb_raw is _TRANSIENT)
    tmdb_data = None if tmdb_raw is _TRANSIENT else tmdb_raw
    imdb_data = None if imdb_raw is _TRANSIENT else imdb_raw
    return tmdb_data, imdb_data, fetch_clean


def _write_fields(
    ctx: _ScanCtx,
    item: Dict[str, Any],
    kodi_id: Any,
    title: str,
    identity: str,
    fields_to_write: Dict[str, Any],
    source_map: Dict[str, str],
    ratings_source: Optional[str],
    ratings_votes: Optional[int],
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


def run_scan(
    media_type: MediaType,
    tracker: TrackerDB,
    source_config: Dict[str, Any],
    enabled_fields: FrozenSet[str],
    tmdb_api_key: str,
    settings: Dict[str, Any],
    monitor: Any = None,
    show_id_map: Optional[Dict[str, str]] = None,
    items: Optional[List[Dict[str, Any]]] = None,
    kodi_filter: Optional[Dict[str, str]] = None,
    breaker: Optional[CircuitBreaker] = None,
    reset: bool = False,
) -> Dict[str, int]:
    """Run a single scan cycle for one media type.

    Queries Kodi for all items (or uses a pre-built list), computes a per-field
    maturity decision for each, fetches metadata once when any field is due,
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
            plot_source, default_source, and optionally refetch_schedule.
        monitor: xbmc.Monitor instance (has abortRequested()), or
            None for tests.
        show_id_map: Dict mapping show title to show-level TMDB ID.
            Required for episode scans so fetch_season uses the show
            ID, not the episode ID.
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
        season_cache={},
        settings=settings,
        schedule=schedule,
        rating_pref=settings.get("rating_source", 0),
        plot_pref=settings.get("plot_source", 0),
        default_pref=settings.get("default_source", 0),
    )

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
        tmdb_id, imdb_id, tvdb_id = _extract_ids(item)
        if not _has_any_id(tmdb_id, imdb_id, tvdb_id):
            log.debug(
                "Skipping item without external IDs",
                event="scan.skip",
                title=title,
            )
            stats["skipped"] += 1
            continue

        # Sync to tracker
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

        # --- Per-field maturity decisions (see maturity.decide_field) ---
        today = date.today().isoformat()
        age = _content_age_days(item, media_type, today)
        fs_map = tracker.get_field_status_map(media_type.name, identity)
        threshold = settings.get("stale_threshold_days", 365)

        candidate_fields = sorted(
            {_TMDB_TO_KODI.get(k, k) for k in _TMDB_TO_KODI}
            & ctx.enabled_fields & media_type.backfill_fields
        )

        decisions = {}  # type: Dict[str, str]
        for f in candidate_fields:
            if f == "title" and media_type.name != "episode":
                continue
            if reset:
                # Reset path (Update now): every candidate field is due
                # regardless of its recorded maturity so the clock re-baselines.
                decisions[f] = FETCH_NOW
                continue
            fs = fs_map.get(f, {})
            decisions[f] = decide_field(
                age,
                int(fs.get("fetch_count") or 0),
                fs.get("next_retry"),
                today,
                threshold,
                _structurally_absent(f, ctx.default_pref),
            )

        # Nothing is due this scan: mark processed and move on without a fetch.
        if not any(d == FETCH_NOW for d in decisions.values()):
            tracker.mark_cycle_processed(media_type.name, identity)
            stats["skipped"] += 1
            continue

        log.debug(
            "Processing item",
            event="scan.processing",
            title=title,
            identity=identity,
            media_type=media_type.name,
            tmdb_id=tmdb_id,
            decisions=decisions,
        )

        if breaker.both_disabled():
            log.warning(
                "Both providers disabled, stopping scan",
                event="scan.breaker", scope="both",
                media_type=media_type.name, processed=stats["processed"],
            )
            stats["aborted"] = True
            break

        if media_type.name == "episode" and not imdb_id and not tmdb_id:
            log.debug(
                "Episode has no usable ID, skipping (re-checked each scan)",
                event="scan.skip", title=title, media_type=media_type.name,
            )
            tracker.mark_cycle_processed(media_type.name, identity)
            stats["skipped"] += 1
            continue

        # Fetch the item once; fetch_clean is False if a consulted provider
        # transient-failed (see _fetch_for_item / _call_provider).
        tmdb_data, imdb_data, fetch_clean = _fetch_for_item(
            ctx, item, kodi_id, title, identity,
            tmdb_id, imdb_id, tvdb_id, language,
        )

        merged_data, source_map = resolve_metadata(
            tmdb_data, imdb_data, _RESOLVE_FIELDS, ctx.plot_pref, ctx.default_pref,
        )

        # Non-final fields are refreshed (overwrite-on-diff); final ones are not.
        nonfinal = [
            f for f, d in decisions.items() if d not in (FINAL, FINAL_EMPTY)
        ]
        fields_to_write = _extract_fields(merged_data, nonfinal, media_type, item)

        ratings_source = None  # type: Optional[str]
        ratings_votes = None  # type: Optional[int]
        if "rating" in nonfinal or "votes" in nonfinal:
            new_ratings, ratings_source, ratings_votes = build_ratings(
                tmdb_data or {}, imdb_data or {}, item.get("ratings") or {},
                ctx.rating_pref,
            )
            if new_ratings is not None:
                fields_to_write["ratings"] = new_ratings

        written = _write_fields(
            ctx, item, kodi_id, title, identity, fields_to_write,
            source_map, ratings_source, ratings_votes, stats,
        )

        # A field whose write was attempted and rejected by Kodi was NOT filled,
        # so its clock must not advance: otherwise aged content reaches FINAL on
        # the next scan and is never written again. Fields with nothing to write
        # (provider answered, value empty or unchanged) are a clean sample and
        # do advance.
        attempted = set(fields_to_write)
        if "ratings" in attempted:
            attempted.discard("ratings")
            attempted.update({"rating", "votes"})
        write_failed = attempted - written

        # Advance counters ONLY on a clean answer; a transient failure leaves
        # them put so the field is retried next scan instead of being wrongly
        # sampled/finalized. The reset path (Update now) re-baselines each clock.
        if fetch_clean:
            premiered = item.get(media_type.date_field, "")
            for f in nonfinal:
                if f in write_failed:
                    continue
                if reset:
                    nd = compute_next_due(
                        1, ctx.schedule, premiered, threshold, today,
                    )
                    tracker.reset_field_clock(media_type.name, identity, f, next_retry=nd)
                    continue
                new_count = int(fs_map.get(f, {}).get("fetch_count") or 0) + 1
                next_due = compute_next_due(
                    new_count, ctx.schedule, premiered, threshold, today,
                )
                if f in ("rating", "votes"):
                    # Rating/votes are sourced from the named-slot ratings map;
                    # record the chosen slot and its votes for staleness.
                    src = ratings_source
                    vc = ratings_votes
                else:
                    src = source_map.get(_KODI_TO_TMDB.get(f, f))
                    vc = None
                tracker.record_field_fetch(
                    media_type.name, identity, f,
                    next_retry=next_due, source=src, vote_count=vc,
                )
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
        tmdb_disabled=breaker.is_disabled("tmdb"),
        imdb_disabled=breaker.is_disabled("imdb"),
        duration_ms=int((time.perf_counter() - scan_start) * 1000),
    )

    return stats

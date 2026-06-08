"""
Scan orchestrator for MetaMender.

Coordinates a single scan cycle: queries Kodi, detects gaps,
calls providers, writes results, and updates the tracker.

Logging:
    Logger: 'scan'
    Key events:
        - scan.start (INFO): Scan cycle starting for a media type
        - scan.complete (INFO): Scan cycle finished with stats
        - scan.skip (DEBUG): Item skipped (disabled source, exhausted, no gaps)
        - scan.abort (INFO): Scan interrupted by Kodi shutdown
        - scan.stale (DEBUG): Rating detected as stale, re-added to gaps
        - scan.processing (DEBUG): Starting to process a single item
        - scan.updated (INFO): Item metadata written to Kodi
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import xbmc

from resources.lib.data.identity import compute_identity
from resources.lib.data.kodi import get_all_items, update_item
from resources.lib.data.media_types import MediaType
from resources.lib.data.sources import match_source
from resources.lib.data.tracker import TrackerDB
from resources.lib.constants import PROVIDER_CIRCUIT_BREAKER_THRESHOLD
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.gaps import detect_gaps, is_placeholder_title
from resources.lib.engine.resolve import TMDB_ONLY, build_ratings, resolve_metadata
from resources.lib.engine.staleness import compute_next_retry, is_rating_stale
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


def _make_stats() -> Dict[str, Any]:
    """Return a fresh stats dict."""
    return {
        "processed": 0,
        "skipped": 0,
        "updated": 0,
        "errors": 0,
        "fields": {},
    }


def _record_attempt_with_retry(
    tracker: TrackerDB,
    media_type_name: str,
    identity: str,
    max_retries: int,
) -> None:
    """Record an attempt and set next retry or mark exhausted.

    Reads the current attempt count from the tracker, computes the
    next retry date for attempt_count + 1, then calls record_attempt
    (which increments the count). If retries are exhausted, marks the
    item as exhausted.
    """
    tracker_item = tracker.get_item(media_type_name, identity)
    current_count = tracker_item["attempt_count"] if tracker_item else 0
    next_attempt = current_count + 1
    next_retry_date = compute_next_retry(next_attempt, max_retries)

    tracker.record_attempt(media_type_name, identity, next_retry=next_retry_date)

    if next_retry_date is None:
        tracker.update_item_status(media_type_name, identity, "exhausted")


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
    """Extract writable Kodi fields from provider data based on detected gaps.

    Only returns fields that are in gap_fields and have non-empty values
    from the provider. Skips "mpaa" (handled by Phase 8).

    For episode titles: only overwrites if provider returns a different,
    non-empty title and the current title is a placeholder.
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

        # Episode title: only overwrite placeholder titles with a different title
        if kodi_key == "title" and media_type.name == "episode":
            current_title = item.get("title", "")
            if not is_placeholder_title(current_title):
                continue
            if value == current_title:
                continue
            fields[kodi_key] = value
            continue

        # Runtime: providers return minutes; Kodi stores seconds
        if kodi_key == "runtime":
            if not isinstance(value, (int, float)):
                continue
            value = int(value) * 60

        # firstaired: only write a full ISO date; skip bare-year values
        if kodi_key == "firstaired":
            if not (isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"):
                continue

        current = item.get(kodi_key)

        # Empty-only guard (A2): never overwrite an existing non-empty value
        # for these fields (plot, tagline, credits, genre, runtime, etc.).
        # First-encounter adds already-filled fields to gaps to establish a
        # baseline; we must record them as checked, not clobber them.
        if not is_empty(current):
            continue

        fields[kodi_key] = value

    return fields


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
) -> Dict[str, int]:
    """Run a single scan cycle for one media type.

    Queries Kodi for all items (or uses a pre-built list), detects
    gaps, fetches metadata from TMDB, writes results back to Kodi,
    and updates the tracker.

    Args:
        media_type: MOVIE, TVSHOW, or EPISODE.
        tracker: Open TrackerDB instance.
        source_config: Loaded source config dict with "sources" list
            and "default_language".
        enabled_fields: Set of field names the user wants backfilled.
        tmdb_api_key: TMDB API key.
        settings: Dict with min_votes_movie, min_votes_episode,
            stale_threshold_days, max_retries, rate_limit.
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

    Returns:
        Stats dict with processed, skipped, updated, errors, and
        per-field counts.
    """
    scan_start = time.perf_counter()
    stats = _make_stats()
    if breaker is None:
        breaker = CircuitBreaker()
    rate_limit = settings.get("rate_limit", 0.25)
    max_retries = settings.get("max_retries", 4)
    sources = source_config.get("sources", [])
    default_language = source_config.get("default_language", "en-US")

    log.info(
        "Scan starting",
        event="scan.start",
        media_type=media_type.name,
    )

    if items is None:
        items = get_all_items(media_type, kodi_filter=kodi_filter)

    # Season cache for episode batching
    season_cache = {}  # type: Dict[str, Optional[List[Dict[str, Any]]]]
    # Track whether we need a rate-limit sleep before the next API call
    needs_sleep = False

    for item in items:
        # Check for Kodi shutdown
        if monitor is not None and monitor.abortRequested():
            log.info(
                "Scan interrupted by shutdown",
                event="scan.abort",
                media_type=media_type.name,
                processed=stats["processed"],
            )
            break

        # Yield: pause while video is playing
        if monitor is not None and xbmc.getCondVisibility("Player.HasVideo"):
            log.info(
                "Scan paused, video playing",
                event="scan.pause",
                media_type=media_type.name,
                processed=stats["processed"],
            )
            while (xbmc.getCondVisibility("Player.HasVideo")
                   and not monitor.abortRequested()):
                if monitor.waitForAbort(10):
                    break
            if not monitor.abortRequested():
                log.info(
                    "Scan resumed, playback stopped",
                    event="scan.resume",
                    media_type=media_type.name,
                )

        identity = compute_identity(item, media_type)
        title = _item_title(item, media_type)
        kodi_id = item.get(media_type.kodi_id_field, 0)

        # Resume: skip items already processed in this cycle
        pre_tracker = tracker.get_item(media_type.name, identity)
        if pre_tracker and pre_tracker.get("cycle_processed"):
            stats["skipped"] += 1
            continue

        # Match source
        source = match_source(item.get("file", ""), sources)
        if not source.get("enabled", True):
            log.debug(
                "Skipping disabled source",
                event="scan.skip",
                title=title,
                source_path=source.get("path", ""),
            )
            stats["skipped"] += 1
            continue

        language = source.get("language", default_language)

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

        # Detect gaps
        gaps = detect_gaps(item, enabled_fields, media_type)

        # Check tracker state for retry gating and staleness
        tracker_item = tracker.get_item(media_type.name, identity)
        if tracker_item is not None:
            # Skip exhausted items
            if tracker_item["status"] == "exhausted":
                log.debug(
                    "Skipping exhausted item",
                    event="scan.skip",
                    title=title,
                    media_type=media_type.name,
                    attempt_count=tracker_item["attempt_count"],
                )
                tracker.mark_cycle_processed(media_type.name, identity)
                stats["skipped"] += 1
                continue

            # Skip items not yet due for retry
            next_retry = tracker_item.get("next_retry")
            if next_retry and tracker_item["attempt_count"] > 0:
                today = date.today().isoformat()
                if next_retry > today:
                    log.debug(
                        "Skipping item, retry not due",
                        event="scan.skip",
                        title=title,
                        media_type=media_type.name,
                        next_retry=next_retry,
                    )
                    tracker.mark_cycle_processed(media_type.name, identity)
                    stats["skipped"] += 1
                    continue

        if not gaps:
            # Check rating staleness before declaring complete: even if
            # the item has a rating, it might be based on too few votes
            # for recent content.
            if "rating" in enabled_fields and media_type.name in ("movie", "tvshow"):
                min_votes = settings.get("min_votes_movie", 50)
                stale_days = settings.get("stale_threshold_days", 365)
                field_info = tracker.get_field_status(
                    media_type.name, identity, "rating"
                )
                if field_info is not None:
                    stale = is_rating_stale(
                        premiered=item.get(media_type.date_field, ""),
                        vote_count=field_info.get("vote_count") or 0,
                        last_fetched=field_info.get("fetched_at"),
                        min_votes=min_votes,
                        stale_threshold_days=stale_days,
                    )
                    if stale:
                        gaps.append("rating")
                        log.debug(
                            "Rating is stale, adding to gaps",
                            event="scan.stale",
                            title=title,
                            media_type=media_type.name,
                            vote_count=field_info.get("vote_count"),
                            premiered=item.get(media_type.date_field, ""),
                        )
            elif "rating" in enabled_fields and media_type.name == "episode":
                min_votes = settings.get("min_votes_episode", 20)
                stale_days = settings.get("stale_threshold_days", 365)
                field_info = tracker.get_field_status(
                    media_type.name, identity, "rating"
                )
                if field_info is not None:
                    stale = is_rating_stale(
                        premiered=item.get(media_type.date_field, ""),
                        vote_count=field_info.get("vote_count") or 0,
                        last_fetched=field_info.get("fetched_at"),
                        min_votes=min_votes,
                        stale_threshold_days=stale_days,
                    )
                    if stale:
                        gaps.append("rating")
                        log.debug(
                            "Rating is stale, adding to gaps",
                            event="scan.stale",
                            title=title,
                            media_type=media_type.name,
                            vote_count=field_info.get("vote_count"),
                            premiered=item.get(media_type.date_field, ""),
                        )

        # First-encounter check: if any enabled field has no field_status
        # entry, add it to gaps so the provider call establishes a baseline.
        kodi_fields = {
            _TMDB_TO_KODI.get(k, k)
            for k in _TMDB_TO_KODI
        } & enabled_fields & media_type.backfill_fields
        checked = tracker.get_checked_fields(media_type.name, identity)
        unchecked = kodi_fields - checked - set(gaps)
        if unchecked:
            gaps.extend(sorted(unchecked))
            log.debug(
                "First encounter, unchecked fields need provider call",
                event="scan.first_check",
                title=title,
                media_type=media_type.name,
                unchecked=sorted(unchecked),
            )

        if not gaps:
            tracker.update_item_status(media_type.name, identity, "complete")
            tracker.mark_cycle_processed(media_type.name, identity)
            stats["skipped"] += 1
            continue

        log.debug(
            "Processing item",
            event="scan.processing",
            title=title,
            media_type=media_type.name,
            tmdb_id=tmdb_id,
            gaps=gaps,
        )

        # --- Provider fetch: movies and TV shows ---
        rating_source = settings.get("rating_source", 0)
        plot_source = settings.get("plot_source", 0)
        tmdb_data = None  # type: Optional[Dict[str, Any]]
        imdb_data = None  # type: Optional[Dict[str, Any]]
        source_map = {}  # type: Dict[str, str]

        if media_type.name in ("movie", "tvshow"):
            # Check circuit breaker: both providers down
            if breaker.both_disabled():
                log.warning(
                    "Both providers disabled, stopping scan",
                    event="scan.circuit_breaker",
                    media_type=media_type.name,
                    processed=stats["processed"],
                )
                break

            # Resolve TMDB ID if missing (show-level only)
            if not tmdb_id and not breaker.is_disabled("tmdb"):
                try:
                    if needs_sleep and rate_limit > 0:
                        time.sleep(rate_limit)
                    tmdb_id = resolve_id(tmdb_api_key, tvdb_id=tvdb_id, imdb_id=imdb_id)
                    needs_sleep = True
                    breaker.record_success("tmdb")
                except ApiKeyError:
                    log.error(
                        "TMDB API key invalid",
                        event="scan.apikey",
                        provider="tmdb",
                    )
                    break
                except TransientProviderError:
                    if breaker.record_failure("tmdb"):
                        log.warning(
                            "TMDB disabled for this cycle after repeated failures",
                            event="scan.breaker",
                            provider="tmdb",
                            threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
                        )

                if tmdb_id:
                    tracker.upsert_item(media_type.name, identity, tmdb_id=tmdb_id)

            # Fetch TMDB data
            if tmdb_id and not breaker.is_disabled("tmdb"):
                try:
                    if needs_sleep and rate_limit > 0:
                        time.sleep(rate_limit)
                    if media_type.name == "movie":
                        tmdb_data = tmdb_fetch_movie(tmdb_id, tmdb_api_key, language)
                    else:
                        tmdb_data = tmdb_fetch_tvshow(tmdb_id, tmdb_api_key, language)
                    needs_sleep = True
                    breaker.record_success("tmdb")
                except ApiKeyError:
                    log.error(
                        "TMDB API key invalid",
                        event="scan.apikey",
                        provider="tmdb",
                    )
                    break
                except TransientProviderError:
                    if breaker.record_failure("tmdb"):
                        log.warning(
                            "TMDB disabled for this cycle after repeated failures",
                            event="scan.breaker",
                            provider="tmdb",
                            threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
                        )

            # Enrich: if TMDB returned an IMDB ID and the item lacks one, write it
            if not imdb_id and tmdb_data and tmdb_data.get("imdb_id"):
                enriched_imdb = tmdb_data["imdb_id"]
                uid = item.get("uniqueid", {})
                uid["imdb"] = enriched_imdb
                update_item(kodi_id, media_type, title, uniqueid=uid)
                tracker.upsert_item(media_type.name, identity, imdb_id=enriched_imdb)
                imdb_id = enriched_imdb
                log.info(
                    "Enriched IMDB ID from TMDB",
                    event="scan.enrich_id",
                    title=title,
                    imdb_id=enriched_imdb,
                )

            # Fetch IMDb data if available and not TMDB-only
            if imdb_id and not breaker.is_disabled("imdb") and not (rating_source == TMDB_ONLY and plot_source == TMDB_ONLY):
                try:
                    if needs_sleep and rate_limit > 0:
                        time.sleep(rate_limit)
                    imdb_data = imdb_fetch_movie(imdb_id)
                    needs_sleep = True
                    breaker.record_success("imdb")
                except ApiKeyError:
                    log.error(
                        "IMDb API key invalid",
                        event="scan.apikey",
                        provider="imdb",
                    )
                    break
                except TransientProviderError:
                    if breaker.record_failure("imdb"):
                        log.warning(
                            "IMDb disabled for this cycle after repeated failures",
                            event="scan.breaker",
                            provider="imdb",
                            threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
                        )

            if tmdb_data is None and imdb_data is None:
                # No data found (not a provider failure): record the attempt so
                # the item is retried, but do not count it as an error.
                _record_attempt_with_retry(
                    tracker, media_type.name, identity, max_retries,
                )
                tracker.mark_cycle_processed(media_type.name, identity)
                stats["processed"] += 1
                continue

            # Resolve best values per field
            merged_data, source_map = resolve_metadata(
                tmdb_data, imdb_data,
                list(_TMDB_TO_KODI.keys()),
                rating_source, plot_source,
            )

        # --- Provider fetch: episodes (ID-based matching) ---
        elif media_type.name == "episode":
            if breaker.both_disabled():
                log.warning(
                    "Both providers disabled, stopping scan",
                    event="scan.circuit_breaker",
                    media_type=media_type.name,
                    processed=stats["processed"],
                )
                break

            if not imdb_id and not tmdb_id:
                log.debug(
                    "Episode has no usable ID, skipping (re-checked each scan)",
                    event="scan.skip",
                    title=title,
                    media_type=media_type.name,
                )
                tracker.mark_cycle_processed(media_type.name, identity)
                stats["skipped"] += 1
                continue

            # TMDB path first: match by TMDB episode ID in season data
            # (runs before IMDb so enrichment can provide imdb_id)
            if tmdb_id and not breaker.is_disabled("tmdb"):
                showtitle = item.get("showtitle", "")
                ep_show_tmdb_id = (show_id_map or {}).get(showtitle)
                if not ep_show_tmdb_id:
                    log.debug(
                        "No show TMDB ID for episode, skipping TMDB path",
                        event="scan.skip",
                        title=title,
                        showtitle=showtitle,
                    )
                if ep_show_tmdb_id:
                    season_number = item.get("season", 0)
                    try:
                        cache_key = "{}:{}:{}".format(ep_show_tmdb_id, season_number, language)
                        is_cache_miss = cache_key not in season_cache
                        season_data = _get_season_data(
                            ep_show_tmdb_id, season_number, tmdb_api_key, language, season_cache
                        )
                        if season_data is not None:
                            tmdb_data = _find_episode_by_tmdb_id(season_data, tmdb_id)
                            # Enrich: if episode has no IMDB ID, fetch it from TMDB
                            if not imdb_id and tmdb_data is not None:
                                ep_num = tmdb_data.get("episode_number", 0)
                                try:
                                    if needs_sleep and rate_limit > 0:
                                        time.sleep(rate_limit)
                                    enriched_imdb = tmdb_fetch_episode_ids(
                                        ep_show_tmdb_id, season_number, ep_num, tmdb_api_key,
                                    )
                                    needs_sleep = True
                                    if enriched_imdb:
                                        uid = item.get("uniqueid", {})
                                        uid["imdb"] = enriched_imdb
                                        update_item(kodi_id, media_type, title, uniqueid=uid)
                                        tracker.upsert_item(media_type.name, identity, imdb_id=enriched_imdb)
                                        imdb_id = enriched_imdb
                                        log.info(
                                            "Enriched IMDB ID from TMDB",
                                            event="scan.enrich_id",
                                            title=title,
                                            imdb_id=enriched_imdb,
                                        )
                                except (ApiKeyError, TransientProviderError):
                                    # Enrichment is best-effort; a failure here
                                    # does not abort the episode scan.
                                    log.exception(
                                        "Episode external IDs fetch failed",
                                        event="scan.error",
                                        title=title,
                                    )
                        if is_cache_miss:
                            needs_sleep = True
                        breaker.record_success("tmdb")
                    except ApiKeyError:
                        log.error(
                            "TMDB API key invalid",
                            event="scan.apikey",
                            provider="tmdb",
                        )
                        break
                    except TransientProviderError:
                        if breaker.record_failure("tmdb"):
                            log.warning(
                                "TMDB disabled for this cycle after repeated failures",
                                event="scan.breaker",
                                provider="tmdb",
                                threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
                            )

            # IMDb path: direct episode fetch by IMDB ID
            # (runs after TMDB so enrichment can provide imdb_id)
            if imdb_id and not breaker.is_disabled("imdb"):
                try:
                    if needs_sleep and rate_limit > 0:
                        time.sleep(rate_limit)
                    imdb_data = imdb_fetch_episode(imdb_id)
                    needs_sleep = True
                    breaker.record_success("imdb")
                except ApiKeyError:
                    log.error(
                        "IMDb API key invalid",
                        event="scan.apikey",
                        provider="imdb",
                    )
                    break
                except TransientProviderError:
                    if breaker.record_failure("imdb"):
                        log.warning(
                            "IMDb disabled for this cycle after repeated failures",
                            event="scan.breaker",
                            provider="imdb",
                            threshold=PROVIDER_CIRCUIT_BREAKER_THRESHOLD,
                        )

            if tmdb_data is None and imdb_data is None:
                # No data found (not a provider failure): record the attempt so
                # the item is retried, but do not count it as an error.
                _record_attempt_with_retry(
                    tracker, media_type.name, identity, max_retries,
                )
                tracker.mark_cycle_processed(media_type.name, identity)
                stats["processed"] += 1
                continue

            # Resolve best values per field
            merged_data, source_map = resolve_metadata(
                tmdb_data, imdb_data,
                list(_TMDB_TO_KODI.keys()),
                rating_source, plot_source,
            )

        else:
            continue

        # Extract writable non-rating fields from resolved provider data
        fields_to_write = _extract_fields(merged_data, gaps, media_type, item)

        # Build the named-slot ratings map when rating is in scope
        ratings_source = None  # type: Optional[str]
        ratings_votes = None  # type: Optional[int]
        ratings_in_scope = "rating" in gaps or "votes" in gaps
        if ratings_in_scope:
            new_ratings, ratings_source, ratings_votes = build_ratings(
                tmdb_data or {}, imdb_data or {}, item.get("ratings") or {},
                rating_source,
            )
            if new_ratings is not None:
                fields_to_write["ratings"] = new_ratings

        ratings_written = "ratings" in fields_to_write
        write_succeeded = False

        if fields_to_write:
            write_succeeded = update_item(kodi_id, media_type, title, **fields_to_write)
            if write_succeeded:
                stats["updated"] += 1
                for field_name in fields_to_write:
                    stats["fields"][field_name] = stats["fields"].get(field_name, 0) + 1
                log.info(
                    "Item updated",
                    event="scan.updated",
                    title=title,
                    media_type=media_type.name,
                    fields_written=list(fields_to_write.keys()),
                )
                # field_status for non-rating written fields
                for field_name in fields_to_write:
                    if field_name == "ratings":
                        continue
                    tmdb_key = _KODI_TO_TMDB.get(field_name, field_name)
                    field_source = source_map.get(tmdb_key, "tmdb")
                    tracker.set_field_status(
                        media_type.name, identity, field_name, "filled",
                        source=field_source,
                    )
                # field_status for the rating (+ votes mirror) when written
                if ratings_written:
                    for rf in ("rating", "votes"):
                        tracker.set_field_status(
                            media_type.name, identity, rf, "filled",
                            source=ratings_source, vote_count=ratings_votes,
                        )
            else:
                stats["errors"] += 1

        # The set of fields considered successfully resolved this pass
        written = set()  # type: set
        if write_succeeded:
            written = set(fields_to_write.keys())
            if ratings_written:
                written.update({"rating", "votes"})
                written.discard("ratings")

        # Record status for gap fields the provider did not (successfully) write
        for gap_field in gaps:
            if gap_field in written:
                continue
            tmdb_key = _KODI_TO_TMDB.get(gap_field, gap_field)
            kodi_val = item.get(gap_field)
            if kodi_val is None:
                kodi_val = item.get(tmdb_key)
            if kodi_val and kodi_val != "" and kodi_val != 0:
                tracker.set_field_status(media_type.name, identity, gap_field, "checked")
            else:
                tracker.set_field_status(media_type.name, identity, gap_field, "empty")

        # Determine overall item status. "filled" or "checked" fields are
        # resolved; truly empty fields remain gaps. A failed write leaves its
        # fields unresolved (A9), so the item is not marked complete.
        resolved = set(written)
        for gap_field in gaps:
            if gap_field not in resolved:
                fs = tracker.get_field_status(media_type.name, identity, gap_field)
                if fs and fs.get("status") == "checked":
                    resolved.add(gap_field)
        remaining_gaps = [g for g in gaps if g not in resolved]
        if not remaining_gaps:
            tracker.update_item_status(media_type.name, identity, "complete")
        else:
            _record_attempt_with_retry(
                tracker, media_type.name, identity, max_retries,
            )

        tracker.mark_cycle_processed(media_type.name, identity)
        stats["processed"] += 1

    elapsed = round(time.perf_counter() - scan_start, 2)
    log.info(
        "Scan complete",
        event="scan.complete",
        media_type=media_type.name,
        processed=stats["processed"],
        skipped=stats["skipped"],
        updated=stats["updated"],
        errors=stats["errors"],
        tmdb_disabled=breaker.is_disabled("tmdb"),
        imdb_disabled=breaker.is_disabled("imdb"),
        total_elapsed_sec=elapsed,
    )

    return stats

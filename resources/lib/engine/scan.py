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

from resources.lib.data.kodi import get_all_items, update_item
from resources.lib.data.media_types import MediaType
from resources.lib.data.sources import match_source
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.gaps import detect_gaps, is_placeholder_title
from resources.lib.engine.staleness import compute_next_retry, is_rating_stale
from resources.lib.providers.tmdb import (
    fetch_movie,
    fetch_season,
    fetch_tvshow,
    resolve_id,
)
from resources.lib.utils import ApiKeyError, get_logger

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
    title: str,
    max_retries: int,
) -> None:
    """Record an attempt and set next retry or mark exhausted.

    Reads the current attempt count from the tracker, computes the
    next retry date for attempt_count + 1, then calls record_attempt
    (which increments the count). If retries are exhausted, marks the
    item as exhausted.
    """
    tracker_item = tracker.get_item(media_type_name, title)
    current_count = tracker_item["attempt_count"] if tracker_item else 0
    next_attempt = current_count + 1
    next_retry_date = compute_next_retry(next_attempt, max_retries)

    tracker.record_attempt(media_type_name, title, next_retry=next_retry_date)

    if next_retry_date is None:
        tracker.update_item_status(media_type_name, title, "exhausted")


def _item_title(item: Dict[str, Any], media_type: MediaType) -> str:
    """Build the tracker key for an item.

    Movies and TV shows use the title directly.
    Episodes use "ShowTitle S01E02" format.
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
        cache[key] = fetch_season(show_tmdb_id, season_number, api_key, language)
    return cache[key]


def _find_episode_in_season(
    season_data: List[Dict[str, Any]],
    episode_number: int,
) -> Optional[Dict[str, Any]]:
    """Find an episode by episode_number in a season's episode list."""
    for ep in season_data:
        if ep.get("episode_number") == episode_number:
            return ep
    return None


def _extract_fields(
    tmdb_data: Dict[str, Any],
    gap_fields: List[str],
    media_type: MediaType,
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract writable Kodi fields from TMDB data based on detected gaps.

    Only returns fields that are in gap_fields and have non-empty values
    from TMDB. Skips "mpaa" (handled by Phase 8).

    For episode titles: only overwrites if TMDB returns a different,
    non-empty title and the current title is a placeholder.
    """
    fields = {}  # type: Dict[str, Any]

    for tmdb_key, kodi_key in _TMDB_TO_KODI.items():
        if kodi_key not in gap_fields:
            continue

        value = tmdb_data.get(tmdb_key)

        # Skip empty values
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        if isinstance(value, (int, float)) and value == 0:
            continue

        # Episode title: only overwrite placeholder titles with different TMDB titles
        if kodi_key == "title" and media_type.name == "episode":
            current_title = item.get("title", "")
            if not is_placeholder_title(current_title):
                continue
            if value == current_title:
                continue

        # Kodi expects votes as a string
        if kodi_key == "votes" and isinstance(value, int):
            value = str(value)

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

    Returns:
        Stats dict with processed, skipped, updated, errors, and
        per-field counts.
    """
    scan_start = time.perf_counter()
    stats = _make_stats()
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
        items = get_all_items(media_type)

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

        title = _item_title(item, media_type)
        kodi_id = item.get(media_type.kodi_id_field, 0)

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
            title,
            kodi_id=kodi_id,
            tmdb_id=tmdb_id,
            imdb_id=imdb_id,
            tvdb_id=tvdb_id,
            source_path=item.get("file", ""),
            premiered=item.get(media_type.date_field, ""),
            date_added=item.get("dateadded", ""),
        )

        # Detect gaps
        gaps = detect_gaps(item, enabled_fields, media_type)

        # Check tracker state for retry gating and staleness
        tracker_item = tracker.get_item(media_type.name, title)
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
                tracker.mark_cycle_processed(media_type.name, title)
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
                    tracker.mark_cycle_processed(media_type.name, title)
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
                    media_type.name, title, "rating"
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
                    media_type.name, title, "rating"
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

        if not gaps:
            tracker.update_item_status(media_type.name, title, "complete")
            tracker.mark_cycle_processed(media_type.name, title)
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

        # Resolve TMDB ID if missing
        if not tmdb_id:
            try:
                if needs_sleep and rate_limit > 0:
                    time.sleep(rate_limit)
                tmdb_id = resolve_id(tmdb_api_key, tvdb_id=tvdb_id, imdb_id=imdb_id)
                needs_sleep = True
            except ApiKeyError:
                log.exception(
                    "Invalid TMDB API key",
                    event="scan.error",
                )
                stats["errors"] += 1
                break
            except Exception:
                log.exception(
                    "Failed to resolve TMDB ID",
                    event="scan.error",
                    title=title,
                )
                stats["errors"] += 1
                _record_attempt_with_retry(
                    tracker, media_type.name, title, max_retries,
                )
                tracker.mark_cycle_processed(media_type.name, title)
                stats["processed"] += 1
                continue

            if not tmdb_id:
                log.debug(
                    "Could not resolve TMDB ID, skipping",
                    event="scan.skip",
                    title=title,
                )
                _record_attempt_with_retry(
                    tracker, media_type.name, title, max_retries,
                )
                tracker.mark_cycle_processed(media_type.name, title)
                stats["processed"] += 1
                continue

            # Update tracker with resolved ID
            tracker.upsert_item(media_type.name, title, tmdb_id=tmdb_id)

        # Fetch metadata from TMDB
        tmdb_data = None  # type: Optional[Dict[str, Any]]
        try:
            if needs_sleep and rate_limit > 0:
                time.sleep(rate_limit)

            if media_type.name == "movie":
                tmdb_data = fetch_movie(tmdb_id, tmdb_api_key, language)
                needs_sleep = True
            elif media_type.name == "tvshow":
                tmdb_data = fetch_tvshow(tmdb_id, tmdb_api_key, language)
                needs_sleep = True
            elif media_type.name == "episode":
                season_number = item.get("season", 0)
                episode_number = item.get("episode", 0)
                showtitle = item.get("showtitle", "")

                ep_show_tmdb_id = (show_id_map or {}).get(showtitle)
                if not ep_show_tmdb_id:
                    log.debug(
                        "No show TMDB ID for episode",
                        event="scan.skip",
                        title=title,
                        showtitle=showtitle,
                    )
                    stats["skipped"] += 1
                    _record_attempt_with_retry(
                        tracker, media_type.name, title, max_retries,
                    )
                    tracker.mark_cycle_processed(media_type.name, title)
                    continue

                cache_key = "{}:{}:{}".format(ep_show_tmdb_id, season_number, language)
                is_cache_miss = cache_key not in season_cache
                season_data = _get_season_data(
                    ep_show_tmdb_id, season_number, tmdb_api_key, language, season_cache
                )
                if season_data is not None:
                    tmdb_data = _find_episode_in_season(season_data, episode_number)
                if is_cache_miss:
                    needs_sleep = True
        except ApiKeyError:
            log.exception(
                "Invalid TMDB API key",
                event="scan.error",
            )
            stats["errors"] += 1
            break
        except Exception:
            log.exception(
                "TMDB fetch failed",
                event="scan.error",
                title=title,
                tmdb_id=tmdb_id,
            )
            stats["errors"] += 1
            _record_attempt_with_retry(
                tracker, media_type.name, title, max_retries,
            )
            tracker.mark_cycle_processed(media_type.name, title)
            stats["processed"] += 1
            continue

        if tmdb_data is None:
            _record_attempt_with_retry(
                tracker, media_type.name, title, max_retries,
            )
            tracker.mark_cycle_processed(media_type.name, title)
            stats["processed"] += 1
            continue

        # Extract writable fields from TMDB response
        fields_to_write = _extract_fields(tmdb_data, gaps, media_type, item)

        if fields_to_write:
            success = update_item(kodi_id, media_type, title, **fields_to_write)
            if success:
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

                # Update field status in tracker
                for field_name in fields_to_write:
                    vote_count = None  # type: Optional[int]
                    if field_name == "rating":
                        vote_count = tmdb_data.get("votes")
                        if isinstance(vote_count, str):
                            try:
                                vote_count = int(vote_count)
                            except ValueError:
                                vote_count = None
                    tracker.set_field_status(
                        media_type.name,
                        title,
                        field_name,
                        "filled",
                        source="tmdb",
                        vote_count=vote_count,
                    )
            else:
                stats["errors"] += 1

        # Record fields that TMDB did not have data for
        for gap_field in gaps:
            if gap_field not in fields_to_write:
                tracker.set_field_status(
                    media_type.name, title, gap_field, "empty"
                )

        # Determine overall item status
        remaining_gaps = [g for g in gaps if g not in fields_to_write]
        if not remaining_gaps:
            tracker.update_item_status(media_type.name, title, "complete")
        else:
            _record_attempt_with_retry(
                tracker, media_type.name, title, max_retries,
            )

        tracker.mark_cycle_processed(media_type.name, title)
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
        total_elapsed_sec=elapsed,
    )

    return stats

"""
Background service daemon for MetaMender.

Runs as a Kodi service, periodically scanning the library for
metadata gaps and backfilling from TMDB and IMDb.

Logging:
    Logger: 'service'
    Key events:
        - service.start (INFO): Service started
        - service.stop (INFO): Service stopped
        - service.scan_trigger (INFO): Scan triggered (scheduled or library event)
        - service.scan_complete (INFO): Scan finished with summary
        - service.scan_error (ERROR): Scan failed
        - service.skip_playback (DEBUG): Scan skipped because video is playing
        - service.library_scan_finished (INFO): Library scan event received
        - service.orphans_pruned (INFO/DEBUG): Orphan prune result (INFO when >0)
        - service.prune_skip (DEBUG): Prune skipped (targeted scan or disabled)
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

import xbmc
import xbmcvfs

from resources.lib.constants import (
    BACKFILL_DB_FILENAME,
    DEFAULT_ADDON_ID,
    DEFAULT_SCAN_INTERVAL_HOURS,
    LIBRARY_SCAN_DEBOUNCE_SEC,
    SCAN_FAILURE_BACKOFF_SEC,
    SERVICE_SLEEP_INTERVAL_SEC,
    SOURCES_CONFIG_FILENAME,
)
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW
from resources.lib.data.sources import load_source_config
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.scan import run_scan
from resources.lib.service.lock import acquire_scan_lock, release_scan_lock
from resources.lib.utils import (
    ApiKeyError,
    KodiQueryError,
    StructuredLogger,
    get_addon,
    get_bool_setting,
    get_float_setting,
    get_int_setting,
    get_logger,
    get_setting,
    lang,
    notify,
)

log = get_logger('service')


class MetaMendMonitor(xbmc.Monitor):
    """Monitor subclass that tracks library scan/clean events."""

    def __init__(self) -> None:
        super().__init__()
        self._scan_requested: bool = False
        self._last_scan_event: float = 0.0

    def onScanFinished(self, library: str) -> None:
        if library == "video":
            self._scan_requested = True
            self._last_scan_event = time.time()
            log.info(
                "Library scan finished, metadata scan queued",
                event="service.library_scan_finished",
            )

    def onCleanFinished(self, library: str) -> None:
        if library == "video":
            self._scan_requested = True
            self._last_scan_event = time.time()
            log.info(
                "Library clean finished, metadata scan queued",
                event="service.library_clean_finished",
            )

    def has_pending_scan(self, debounce_sec: float) -> bool:
        """Check if a library-triggered scan is pending and debounce has elapsed."""
        if not self._scan_requested:
            return False
        return (time.time() - self._last_scan_event) >= debounce_sec

    def clear_scan_request(self) -> None:
        """Clear the pending scan flag after a scan runs."""
        self._scan_requested = False


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

_FIELD_TOGGLES = {
    "enable_ratings": frozenset({"rating"}),
    "enable_plots": frozenset({"plot"}),
    "enable_episode_titles": frozenset({"title"}),
    "enable_certifications": frozenset({"mpaa"}),
    "enable_credits": frozenset({"director", "writer"}),
    "enable_trailers": frozenset({"trailer"}),
    "enable_taglines": frozenset({"tagline"}),
    "enable_details": frozenset({"genre", "studio", "country", "runtime"}),
}


def _load_settings() -> Dict[str, Any]:
    """Read all settings needed by the scan engine.

    Returns:
        Dict with tmdb_api_key, rating_source, plot_source, default_source,
        enabled_fields (FrozenSet), min_votes_movie,
        min_votes_episode, stale_threshold_days, refetch_schedule,
        rate_limit, scan_interval, show_notifications, and
        resume_interrupted.
    """
    # Build enabled fields from individual toggles
    fields: set = set()
    for setting_id, field_names in _FIELD_TOGGLES.items():
        if get_bool_setting(setting_id):
            fields.update(field_names)

    return {
        "tmdb_api_key": get_setting("tmdb_api_key"),
        "rating_source": get_int_setting("rating_source", 0),
        "plot_source": get_int_setting("plot_source", 0),
        "default_source": get_int_setting("default_source", 0),
        "enabled_fields": frozenset(fields),
        "min_votes_movie": get_int_setting("min_votes_movie", 50),
        "min_votes_episode": get_int_setting("min_votes_episode", 20),
        "stale_threshold_days": get_int_setting("stale_threshold_days", 365),
        "refetch_schedule": get_setting("refetch_schedule"),
        "rate_limit": get_float_setting("rate_limit", 0.25),
        "scan_interval": get_int_setting("scan_interval", DEFAULT_SCAN_INTERVAL_HOURS),
        "show_notifications": get_bool_setting("show_notifications"),
        "resume_interrupted": get_bool_setting("resume_interrupted"),
        "orphan_retention_days": get_int_setting("orphan_retention_days", 30),
    }


def _get_db_path() -> str:
    """Build the absolute path to the backfill tracker database."""
    addon_data = xbmcvfs.translatePath(
        "special://profile/addon_data/{}/".format(DEFAULT_ADDON_ID)
    )
    if not xbmcvfs.exists(addon_data):
        xbmcvfs.mkdirs(addon_data)
    return os.path.join(addon_data, BACKFILL_DB_FILENAME)


def _get_sources_config_path() -> str:
    """Build the absolute path to the sources configuration file."""
    addon_data = xbmcvfs.translatePath(
        "special://profile/addon_data/{}/".format(DEFAULT_ADDON_ID)
    )
    return os.path.join(addon_data, SOURCES_CONFIG_FILENAME)




def _run_scan(
    tracker: TrackerDB,
    source_config: Dict[str, Any],
    settings: Dict[str, Any],
    monitor: MetaMendMonitor,
    kodi_filter: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, int]]:
    """Run scan for MOVIE, TVSHOW, and EPISODE in order.

    TV shows are scanned before episodes so their TMDB IDs are
    available for the episode season lookups.

    Returns:
        Dict keyed by media type name with stats dicts as values.
    """
    tmdb_api_key = settings["tmdb_api_key"]
    enabled_fields = settings["enabled_fields"]

    all_stats = {}  # type: Dict[str, Dict[str, int]]

    # One breaker for the whole cycle: a provider that fails repeatedly
    # stays disabled across the movie/tvshow/episode passes.
    breaker = CircuitBreaker()

    for media_type in (MOVIE, TVSHOW):
        stats = run_scan(
            media_type=media_type,
            tracker=tracker,
            source_config=source_config,
            enabled_fields=enabled_fields,
            tmdb_api_key=tmdb_api_key,
            settings=settings,
            monitor=monitor,
            kodi_filter=kodi_filter,
            breaker=breaker,
        )
        all_stats[media_type.name] = stats

    show_id_map = tracker.get_show_id_map()
    log.info(
        "Show ID map built for episode scan",
        event="service.show_id_map",
        shows=len(show_id_map),
    )

    episode_stats = run_scan(
        media_type=EPISODE,
        tracker=tracker,
        source_config=source_config,
        enabled_fields=enabled_fields,
        tmdb_api_key=tmdb_api_key,
        settings=settings,
        monitor=monitor,
        show_id_map=show_id_map,
        kodi_filter=kodi_filter,
        breaker=breaker,
    )
    all_stats[EPISODE.name] = episode_stats

    return all_stats


def _maybe_prune_orphans(
    tracker: TrackerDB,
    settings: Dict[str, Any],
    kodi_filter: Optional[Dict[str, str]],
) -> None:
    """Prune orphaned tracker rows, but ONLY after a full scan.

    CRITICAL CORRECTNESS GUARD: pruning compares each item's `last_seen`
    stamp against a retention cutoff. A targeted/library-event scan
    (kodi_filter is not None) only stamps `last_seen` on the few new items
    it touches, so pruning after one would wrongly delete EVERYTHING not in
    that small set. Only a full scan (kodi_filter is None) refreshes
    `last_seen` for the whole library, so pruning is safe only then.

    A retention of 0 disables pruning entirely.
    """
    if kodi_filter is not None:
        # Targeted scan: last_seen was only refreshed for a subset of items.
        # Pruning here would delete the rest of the library. Never prune.
        log.debug(
            "Prune skipped: targeted scan",
            event="service.prune_skip",
            reason="targeted",
        )
        return
    retention = settings["orphan_retention_days"]
    if retention <= 0:
        log.debug(
            "Prune skipped: disabled",
            event="service.prune_skip",
            reason="disabled",
        )
        return
    cutoff = (date.today() - timedelta(days=retention)).isoformat()
    total_removed = 0
    for mt in (MOVIE, TVSHOW, EPISODE):
        total_removed += tracker.prune_orphans(mt.name, cutoff=cutoff)
    if total_removed:
        log.info(
            "Pruned orphaned tracker rows",
            event="service.orphans_pruned",
            removed=total_removed,
            retention_days=retention,
        )
    else:
        log.debug(
            "Prune ran, no orphans removed",
            event="service.orphans_pruned",
            removed=0,
            retention_days=retention,
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _select_trigger(
    now: float,
    last_scan: float,
    interval_sec: float,
    has_pending_event: bool,
) -> Tuple[bool, str]:
    """Decide whether to scan this tick and why.

    A due scheduled scan takes precedence over a library event in the same
    tick: the scheduled scan is full (staleness + retries) and also covers the
    newly added items, so the pending event is satisfied by it. Only fall back
    to a targeted library-event scan when no scheduled scan is due.

    Returns (should_scan, reason) where reason is "scheduled" or "library_event".
    """
    if now - last_scan >= interval_sec:
        return (True, "scheduled")
    if has_pending_event:
        return (True, "library_event")
    return (False, "scheduled")


def run() -> None:
    """Main service entry point. Blocks until Kodi requests abort."""
    # Re-initialize the logger now that Kodi settings are readable.
    # get_logger() at module import time may have initialized with
    # debug_enabled=False because settings weren't available yet, so
    # reconfigure now that settings are readable.
    try:
        debug_on = get_bool_setting('logging')
        addon_id = get_addon().getAddonInfo('id')
    except RuntimeError:
        debug_on = False
        addon_id = DEFAULT_ADDON_ID
    StructuredLogger.reconfigure(debug_enabled=debug_on, addon_id=addon_id)

    log.info("Service started", event="service.start")

    monitor = MetaMendMonitor()
    tracker: Optional[TrackerDB] = None
    last_scan = 0.0  # Force scan on first loop iteration
    next_retry_after = 0.0  # Backoff gate after a failed scan (both triggers)

    try:
        tracker = TrackerDB(_get_db_path())

        while not monitor.abortRequested():
            try:
                now = time.time()

                # Trigger selection (scheduled takes precedence over a
                # same-tick library event; see _select_trigger). Only the scan
                # interval is needed to decide this, so read it cheaply each
                # tick; the full settings dict is loaded only when about to scan.
                interval_sec = get_int_setting("scan_interval", 24) * 3600
                should_scan, reason = _select_trigger(
                    now, last_scan, interval_sec,
                    monitor.has_pending_scan(LIBRARY_SCAN_DEBOUNCE_SEC),
                )

                # Honor a post-failure backoff for BOTH trigger types so a
                # persistently-failing Kodi query is not re-attempted every loop
                # (the library-event trigger is independent of last_scan).
                if should_scan and now < next_retry_after:
                    should_scan = False

                # Guard: skip while video is playing
                if should_scan and xbmc.getCondVisibility("Player.HasVideo"):
                    should_scan = False
                    log.debug(
                        "Skipping scan, video is playing",
                        event="service.skip_playback",
                    )

                settings = None  # type: Optional[Dict[str, Any]]
                if should_scan:
                    settings = _load_settings()
                    if not settings.get("tmdb_api_key"):
                        log.warning(
                            "No TMDB API key configured, skipping scan",
                            event="service.skip_no_key",
                        )
                        last_scan = time.time()
                        should_scan = False

                if should_scan and not acquire_scan_lock():
                    log.info(
                        "Scan already running elsewhere, skipping",
                        event="service.locked",
                    )
                    should_scan = False

                if should_scan:
                    # settings is always loaded above when should_scan stays True.
                    assert settings is not None
                    # Lock acquired above; release on EVERY exit path
                    # (success, KodiQueryError continue, unexpected error).
                    try:
                        StructuredLogger.start_scan_log()

                        kodi_filter = None  # type: Optional[Dict[str, str]]
                        if reason == "library_event":
                            last_completed = tracker.get_scan_state(
                                "last_scan_completed"
                            )
                            if last_completed:
                                kodi_filter = {
                                    "field": "dateadded",
                                    "operator": "after",
                                    "value": last_completed,
                                }

                        log.info(
                            "Scan triggered",
                            event="service.scan_trigger",
                            reason=reason,
                            targeted=kodi_filter is not None,
                        )

                        source_config = load_source_config(
                            _get_sources_config_path()
                        )

                        if not settings["resume_interrupted"]:
                            tracker.start_new_cycle()

                        scan_start = time.perf_counter()
                        try:
                            all_stats = _run_scan(
                                tracker, source_config, settings, monitor,
                                kodi_filter=kodi_filter,
                            )
                        except KodiQueryError:
                            # A Kodi library query failed (RPC/parse error),
                            # which is NOT an empty library. Treat the cycle as
                            # FAILED: do not advance the schedule or the
                            # watermark, and do not clear a library-event
                            # request, so it retries. Set a short backoff
                            # instead of a full interval so the next loop
                            # re-attempts soon without hammering Kodi.
                            log.exception(
                                "Scan aborted on Kodi query failure",
                                event="service.scan_failed",
                                reason=reason,
                            )
                            next_retry_after = (
                                time.time() + SCAN_FAILURE_BACKOFF_SEC
                            )
                            continue
                        except ApiKeyError:
                            # A provider rejected the API key. This is terminal
                            # for the cycle: notify the user, do NOT advance the
                            # watermark or clear a library-event request, and back
                            # off so we do not re-attempt every loop while the key
                            # stays invalid.
                            log.warning(
                                "Scan stopped: invalid API key",
                                event="service.scan_apikey",
                                reason=reason,
                            )
                            notify(lang(32341))  # Invalid TMDB API key
                            next_retry_after = (
                                time.time() + SCAN_FAILURE_BACKOFF_SEC
                            )
                            continue
                        finally:
                            StructuredLogger.stop_scan_log()

                        duration_ms = int((time.perf_counter() - scan_start) * 1000)

                        movie_stats = all_stats.get("movie", {})
                        tvshow_stats = all_stats.get("tvshow", {})
                        episode_stats = all_stats.get("episode", {})

                        log.info(
                            "Scan complete",
                            event="service.scan_complete",
                            movies_updated=movie_stats.get("updated", 0),
                            tvshows_updated=tvshow_stats.get("updated", 0),
                            episodes_updated=episode_stats.get("updated", 0),
                            duration_ms=duration_ms,
                        )

                        last_scan = time.time()
                        monitor.clear_scan_request()
                        tracker.set_scan_state(
                            "last_scan_completed",
                            time.strftime("%Y-%m-%d %H:%M:%S"),
                        )

                        # Prune orphaned tracker rows. Guarded to run only
                        # after a FULL scan (kodi_filter is None); see
                        # _maybe_prune_orphans for why a targeted scan must
                        # never prune.
                        _maybe_prune_orphans(tracker, settings, kodi_filter)

                        # Notification
                        if settings["show_notifications"]:
                            total_updated = (
                                movie_stats.get("updated", 0)
                                + tvshow_stats.get("updated", 0)
                                + episode_stats.get("updated", 0)
                            )
                            parts = []
                            if total_updated:
                                parts.append(lang(32349).format(total_updated))  # {} updated
                            if not parts:
                                parts.append(lang(32348))  # No changes
                            notify(", ".join(parts))
                    finally:
                        release_scan_lock()

            except Exception:
                log.exception("Scan cycle failed", event="service.scan_error")

            if monitor.waitForAbort(SERVICE_SLEEP_INTERVAL_SEC):
                break

    finally:
        if tracker is not None:
            tracker.close()
        log.info("Service stopped", event="service.stop")
        StructuredLogger.shutdown()

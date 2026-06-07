"""
Background service daemon for MetaMender.

Runs as a Kodi service, periodically scanning the library for
metadata gaps and backfilling from TMDB and OMDB.

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
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, FrozenSet, Optional

import xbmc
import xbmcvfs

from resources.lib.constants import (
    BACKFILL_DB_FILENAME,
    DEFAULT_ADDON_ID,
    DEFAULT_MAX_RETRIES,
    DEFAULT_SCAN_INTERVAL_HOURS,
    LIBRARY_SCAN_DEBOUNCE_SEC,
    SERVICE_SLEEP_INTERVAL_SEC,
    SOURCES_CONFIG_FILENAME,
)
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW
from resources.lib.data.sources import load_source_config
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.scan import run_scan
from resources.lib.utils import (
    StructuredLogger,
    get_bool_setting,
    get_float_setting,
    get_int_setting,
    get_logger,
    get_setting,
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
        Dict with tmdb_api_key, omdb_api_key, enabled_fields (FrozenSet),
        min_votes_movie, min_votes_episode, stale_threshold_days,
        max_retries, rate_limit, scan_interval, show_notifications,
        and resume_interrupted.
    """
    # Build enabled fields from individual toggles
    fields: set = set()
    for setting_id, field_names in _FIELD_TOGGLES.items():
        if get_bool_setting(setting_id):
            fields.update(field_names)

    return {
        "tmdb_api_key": get_setting("tmdb_api_key"),
        "omdb_api_key": get_setting("omdb_api_key"),
        "enabled_fields": frozenset(fields),
        "min_votes_movie": get_int_setting("min_votes_movie", 50),
        "min_votes_episode": get_int_setting("min_votes_episode", 20),
        "stale_threshold_days": get_int_setting("stale_threshold_days", 365),
        "max_retries": get_int_setting("max_retries", DEFAULT_MAX_RETRIES),
        "rate_limit": get_float_setting("rate_limit", 0.25),
        "scan_interval": get_int_setting("scan_interval", DEFAULT_SCAN_INTERVAL_HOURS),
        "show_notifications": get_bool_setting("show_notifications"),
        "resume_interrupted": get_bool_setting("resume_interrupted"),
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
) -> Dict[str, Dict[str, int]]:
    """Run scan for MOVIE, TVSHOW, and EPISODE in order.

    Returns:
        Dict keyed by media type name with stats dicts as values.
    """
    tmdb_api_key = settings["tmdb_api_key"]
    enabled_fields: FrozenSet[str] = settings["enabled_fields"]

    all_stats: Dict[str, Dict[str, int]] = {}

    for media_type in (MOVIE, TVSHOW, EPISODE):
        stats = run_scan(
            media_type=media_type,
            tracker=tracker,
            source_config=source_config,
            enabled_fields=enabled_fields,
            tmdb_api_key=tmdb_api_key,
            settings=settings,
            monitor=monitor,
        )
        all_stats[media_type.name] = stats

    return all_stats


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Main service entry point. Blocks until Kodi requests abort."""
    log.info("Service started", event="service.start")

    monitor = MetaMendMonitor()
    tracker: Optional[TrackerDB] = None
    last_scan = 0.0  # Force scan on first loop iteration

    try:
        tracker = TrackerDB(_get_db_path())

        while not monitor.abortRequested():
            try:
                should_scan = False
                now = time.time()

                # Periodic / startup trigger
                settings = _load_settings()
                interval_sec = settings["scan_interval"] * 3600
                if now - last_scan >= interval_sec:
                    should_scan = True
                    reason = "scheduled"

                # Library event trigger (with debounce)
                if monitor.has_pending_scan(LIBRARY_SCAN_DEBOUNCE_SEC):
                    should_scan = True
                    reason = "library_event"

                # Guard: skip while video is playing
                if should_scan and xbmc.getCondVisibility("Player.HasVideo"):
                    should_scan = False
                    log.debug(
                        "Skipping scan, video is playing",
                        event="service.skip_playback",
                    )

                if should_scan:
                    log.info(
                        "Scan triggered",
                        event="service.scan_trigger",
                        reason=reason,
                    )

                    source_config = load_source_config(
                        _get_sources_config_path()
                    )

                    if not settings["resume_interrupted"]:
                        tracker.start_new_cycle()

                    scan_start = time.perf_counter()
                    all_stats = _run_scan(tracker, source_config, settings, monitor)
                    elapsed = round(time.perf_counter() - scan_start, 2)

                    movie_stats = all_stats.get("movie", {})
                    tvshow_stats = all_stats.get("tvshow", {})
                    episode_stats = all_stats.get("episode", {})

                    log.info(
                        "Scan complete",
                        event="service.scan_complete",
                        movies_updated=movie_stats.get("updated", 0),
                        tvshows_updated=tvshow_stats.get("updated", 0),
                        episodes_updated=episode_stats.get("updated", 0),
                        total_elapsed_sec=elapsed,
                    )

                    last_scan = time.time()
                    monitor.clear_scan_request()

                    # Notification
                    if settings["show_notifications"]:
                        total_updated = (
                            movie_stats.get("updated", 0)
                            + tvshow_stats.get("updated", 0)
                            + episode_stats.get("updated", 0)
                        )
                        parts = []
                        if total_updated:
                            parts.append("{} updated".format(total_updated))
                        if not parts:
                            parts.append("No changes")
                        notify(", ".join(parts))

            except Exception:
                log.exception("Scan cycle failed", event="service.scan_error")

            if monitor.waitForAbort(SERVICE_SLEEP_INTERVAL_SEC):
                break

    finally:
        if tracker is not None:
            tracker.close()
        log.info("Service stopped", event="service.stop")
        StructuredLogger.shutdown()

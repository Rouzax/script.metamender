"""
MetaMender manual trigger.

Runs a single scan cycle when launched via RunScript(script.metamender).

Logging:
    Logger: 'manual'
    Key events:
        - manual.start (INFO): Manual scan started
        - manual.complete (INFO): Manual scan finished with summary
        - manual.error (ERROR): Manual scan failed
"""
from __future__ import annotations

import time

from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW
from resources.lib.data.sources import load_source_config
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.scan import run_scan
from resources.lib.service.daemon import (
    _build_show_id_map,
    _get_db_path,
    _get_sources_config_path,
    _load_settings,
)
from resources.lib.service.lock import acquire_scan_lock, release_scan_lock
from resources.lib.constants import DEFAULT_ADDON_ID
from resources.lib.utils import (
    StructuredLogger,
    get_addon,
    get_bool_setting,
    get_logger,
    notify,
)

log = get_logger('manual')


def run() -> None:
    """Run a single manual scan cycle."""
    StructuredLogger._initialized = False
    try:
        debug_on = get_bool_setting('logging')
        addon_id = get_addon().getAddonInfo('id')
    except RuntimeError:
        debug_on = False
        addon_id = DEFAULT_ADDON_ID
    StructuredLogger.initialize(debug_enabled=debug_on, addon_id=addon_id)
    StructuredLogger.start_scan_log()

    log.info("Manual scan started", event="manual.start")

    tracker = None
    locked = False

    try:
        settings = _load_settings()

        if not settings["tmdb_api_key"]:
            log.warning(
                "No TMDB API key configured, skipping scan",
                event="manual.skip_no_key",
            )
            return

        if not acquire_scan_lock():
            log.info(
                "A scan is already running, skipping",
                event="manual.locked",
            )
            return
        locked = True

        tracker = TrackerDB(_get_db_path())
        source_config = load_source_config(_get_sources_config_path())

        if not settings["resume_interrupted"]:
            tracker.start_new_cycle()

        tmdb_api_key = settings["tmdb_api_key"]
        enabled_fields = settings["enabled_fields"]

        scan_start = time.perf_counter()
        all_stats = {}

        # One breaker for the whole manual cycle (shared across passes).
        breaker = CircuitBreaker()

        for media_type in (MOVIE, TVSHOW):
            stats = run_scan(
                media_type=media_type,
                tracker=tracker,
                source_config=source_config,
                enabled_fields=enabled_fields,
                tmdb_api_key=tmdb_api_key,
                settings=settings,
                breaker=breaker,
            )
            all_stats[media_type.name] = stats

        show_id_map = _build_show_id_map(tracker)
        episode_stats = run_scan(
            media_type=EPISODE,
            tracker=tracker,
            source_config=source_config,
            enabled_fields=enabled_fields,
            tmdb_api_key=tmdb_api_key,
            settings=settings,
            show_id_map=show_id_map,
            breaker=breaker,
        )
        all_stats[EPISODE.name] = episode_stats

        elapsed = round(time.perf_counter() - scan_start, 2)

        movie_stats = all_stats.get("movie", {})
        tvshow_stats = all_stats.get("tvshow", {})
        episode_stats = all_stats.get("episode", {})

        log.info(
            "Manual scan complete",
            event="manual.complete",
            movies_updated=movie_stats.get("updated", 0),
            tvshows_updated=tvshow_stats.get("updated", 0),
            episodes_updated=episode_stats.get("updated", 0),
            total_elapsed_sec=elapsed,
        )

        # Notification
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
        log.exception("Manual scan failed", event="manual.error")

    finally:
        if locked:
            release_scan_lock()
        StructuredLogger.stop_scan_log()
        if tracker is not None:
            tracker.close()

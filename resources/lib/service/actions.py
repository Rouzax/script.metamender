"""
MetaMender entry-point action handlers.

Handlers invoked from default.py when the script is launched with an action
argument (for example RunScript(script.metamender,reset_exhausted) from a
settings action button).

Logging:
    Logger: 'actions'
    Key events:
        - actions.reset_exhausted_start (INFO): Reset-exhausted-fields action starting
        - actions.reset_exhausted (INFO): Reset-exhausted-fields action finished
        - actions.update_now_start (INFO): Update-now force scan starting
        - actions.update_now (INFO): Update-now force scan finished
        - actions.update_now_invalid (WARNING): Update-now bad dbtype/dbid
        - actions.update_now_notfound (WARNING): Update-now item not in library
        - actions.skip_no_key (WARNING): Update-now skipped, no TMDB API key
        - actions.locked (INFO): Update-now skipped, a scan is running
        - actions.error (ERROR): Action handler raised an unexpected exception
"""
from __future__ import annotations

from typing import Dict, Optional

from resources.lib.constants import DEFAULT_ADDON_ID
from resources.lib.data.kodi import get_item_by_id
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW, MediaType
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
from resources.lib.utils import (
    StructuredLogger,
    get_addon,
    get_bool_setting,
    get_logger,
    notify,
)

log = get_logger('actions')

# Map a Kodi ListItem.DBType string to its MediaType.
_DBTYPE_TO_MEDIA_TYPE: Dict[str, MediaType] = {
    "movie": MOVIE,
    "tvshow": TVSHOW,
    "episode": EPISODE,
}


def _init_logging() -> None:
    """Initialize StructuredLogger for a one-shot action invocation."""
    StructuredLogger._initialized = False
    try:
        debug_on = get_bool_setting('logging')
        addon_id = get_addon().getAddonInfo('id')
    except RuntimeError:
        debug_on = False
        addon_id = DEFAULT_ADDON_ID
    StructuredLogger.initialize(debug_enabled=debug_on, addon_id=addon_id)


def reset_exhausted() -> None:
    """Reset all exhausted field_status rows and notify the user."""
    _init_logging()

    log.info(
        "Reset exhausted fields action starting",
        event="actions.reset_exhausted_start",
    )

    tracker = None
    try:
        tracker = TrackerDB(_get_db_path())
        count = tracker.reset_exhausted_fields()
        log.info(
            "Reset exhausted fields action complete",
            event="actions.reset_exhausted",
            count=count,
        )
        notify("Reset {} exhausted fields".format(count))
    except Exception:
        log.exception("Reset exhausted fields action failed", event="actions.error")
    finally:
        if tracker is not None:
            tracker.close()


def update_now(dbtype: str, dbid: str) -> None:
    """Force a fresh single-item correction (D3 "Update now" context action).

    Runs a force scan of one library item: it bypasses gating, exhaustion,
    the cycle-processed skip, and the empty-only overwrite guard so provider
    data overwrites existing non-empty fields. Acquires the shared scan lock
    so it never runs concurrently with the service/manual scan.

    Args:
        dbtype: Kodi ListItem.DBTYPE ("movie", "tvshow", or "episode").
        dbid: Kodi database ID as a string (from $INFO[ListItem.DBID]).
    """
    _init_logging()

    log.info(
        "Update now action starting",
        event="actions.update_now_start",
        media_type=dbtype,
        item_id=str(dbid),
    )

    media_type: Optional[MediaType] = _DBTYPE_TO_MEDIA_TYPE.get(dbtype)
    if media_type is None:
        log.warning(
            "Update now called with unknown dbtype",
            event="actions.update_now_invalid",
            dbtype=dbtype,
        )
        notify("Cannot update this item type")
        return

    try:
        item_id = int(dbid)
    except (TypeError, ValueError):
        log.warning(
            "Update now called with invalid dbid",
            event="actions.update_now_invalid",
            dbid=str(dbid),
        )
        notify("Cannot update this item type")
        return

    if not acquire_scan_lock():
        log.info(
            "A scan is already running, skipping update now",
            event="actions.locked",
        )
        notify("A scan is already running")
        return

    tracker = None
    try:
        item = get_item_by_id(media_type, item_id)
        if item is None:
            log.warning(
                "Update now could not find the item",
                event="actions.update_now_notfound",
                media_type=media_type.name,
                item_id=item_id,
            )
            notify("Item not found")
            return

        settings = _load_settings()
        tmdb_api_key = settings["tmdb_api_key"]
        if not tmdb_api_key:
            log.warning(
                "No TMDB API key configured, cannot update",
                event="actions.skip_no_key",
            )
            notify("No TMDB API key configured")
            return

        tracker = TrackerDB(_get_db_path())
        source_config = load_source_config(_get_sources_config_path())

        show_id_map: Optional[Dict[str, str]] = None
        if media_type.name == "episode":
            show_id_map = _build_show_id_map(tracker)

        stats = run_scan(
            media_type=media_type,
            tracker=tracker,
            source_config=source_config,
            enabled_fields=settings["enabled_fields"],
            tmdb_api_key=tmdb_api_key,
            settings=settings,
            items=[item],
            force=True,
            breaker=CircuitBreaker(),
            show_id_map=show_id_map,
        )

        title = item.get("title", "") or str(item_id)
        updated = stats.get("updated", 0)
        log.info(
            "Update now complete",
            event="actions.update_now",
            media_type=media_type.name,
            title=title,
            updated=updated,
        )
        if updated:
            notify("Updated: {}".format(title))
        else:
            notify("No changes: {}".format(title))
    except Exception:
        log.exception("Update now action failed", event="actions.error")
        notify("Update failed")
    finally:
        release_scan_lock()
        if tracker is not None:
            tracker.close()

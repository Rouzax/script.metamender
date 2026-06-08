"""
MetaMender entry-point action handlers.

Handlers invoked from default.py when the script is launched with an action
argument (for example RunScript(script.metamender,reset_exhausted) from a
settings action button).

Logging:
    Logger: 'actions'
    Key events:
        - actions.reset_exhausted (INFO): Reset-exhausted-fields action finished
        - actions.error (ERROR): Action handler failed
"""
from __future__ import annotations

from resources.lib.constants import DEFAULT_ADDON_ID
from resources.lib.data.tracker import TrackerDB
from resources.lib.service.daemon import _get_db_path
from resources.lib.utils import (
    StructuredLogger,
    get_addon,
    get_bool_setting,
    get_logger,
    notify,
)

log = get_logger('actions')


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

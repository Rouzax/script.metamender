"""Opening the setup wizard or the folder-languages dialog from any entry
point, and starting the first scan after a first setup.

Logging:
    Logger: 'setup' (the window logs through resources.lib.ui.setup_window)
    Key events:
        - setup.notices_failed (ERROR): the library notices could not be
          cleared
"""
from __future__ import annotations

from resources.lib.data.tracker import TrackerDB
from resources.lib.service import attention, manual
from resources.lib.service.daemon import _get_db_path, _get_sources_config_path
from resources.lib.ui.setup_model import WIZARD
from resources.lib.ui.setup_window import run_setup
from resources.lib.utils import get_logger, is_setup_complete

log = get_logger('setup')


def _libraries_seen() -> None:
    """The library list is on screen: the new-library and conversion
    notices have done their job."""
    tracker = None
    try:
        tracker = TrackerDB(_get_db_path())
        attention.update(tracker, lambda a: a.libraries_opened())
    except Exception:
        log.exception("Clearing the library notices failed", event="setup.notices_failed")
    finally:
        if tracker is not None:
            tracker.close()


def open_setup(mode: str) -> bool:
    """Run the wizard ("wizard") or the folder dialog ("folders"). Finishing
    the wizard for the first time starts the first scan at once; a re-run
    applies from the next scheduled scan. True when the user saved."""
    first = not is_setup_complete()
    if not first:
        _libraries_seen()
    saved = run_setup(mode, _get_sources_config_path())
    if saved and first and mode == WIZARD:
        manual.run()
    return saved

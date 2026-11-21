"""Opening the setup wizard or the Libraries dialog from any entry point,
and starting the first scan after a first setup.

Logging:
    Logger: 'setup' (the window logs through resources.lib.ui.setup_window)
    Key events:
        - setup.notices_failed (ERROR): the library notices could not be
          cleared
        - setup.convert_failed (ERROR): the 1.1 settings could not be
          converted before the library list opened
"""
from __future__ import annotations

from resources.lib.data.libraries import FILE_LEGACY, read_libraries
from resources.lib.data.tracker import TrackerDB
from resources.lib.service import attention, manual
from resources.lib.service.daemon import (
    _convert_libraries,
    _get_db_path,
    _get_sources_config_path,
)
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


def _convert_pending(path: str) -> None:
    """Convert the 1.1 settings now when the service has not yet, so the
    list opens on the converted libraries instead of refusing to open."""
    if read_libraries(path)[1] != FILE_LEGACY:
        return
    tracker = None
    try:
        tracker = TrackerDB(_get_db_path())
        _convert_libraries(tracker, path)
    except Exception:
        log.exception("Converting the settings failed", event="setup.convert_failed")
    finally:
        if tracker is not None:
            tracker.close()


def open_setup(mode: str) -> bool:
    """Run the wizard (WIZARD) or the Libraries dialog (LIBRARIES).
    Finishing the wizard for the first time starts the first scan at once;
    a re-run applies from the next scheduled scan. True when the user
    saved."""
    first = not is_setup_complete()
    path = _get_sources_config_path()
    if not first:
        _convert_pending(path)
    # Before the first setup the service raises no library notices.
    saved = run_setup(mode, path, None if first else _libraries_seen)
    if saved and first and mode == WIZARD:
        manual.run()
    return saved

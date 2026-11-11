"""MetaMender script entry point: the add-on's own launch, its settings
buttons and RunScript actions.

Opening the add-on runs the setup wizard until setup has been finished
once, and a manual scan after that.
"""
from __future__ import annotations

import sys

from resources.lib.service.actions import reset_tracker, update_now
from resources.lib.service.manual import run
from resources.lib.service.setup import open_setup
from resources.lib.ui.setup_model import FOLDERS, WIZARD
from resources.lib.utils import get_logger, is_setup_complete

log = get_logger('default')


def main(argv: list[str]) -> None:
    action = argv[0] if argv else ""
    if action == "reset_tracker":
        reset_tracker()
    elif action == "update_now":
        update_now(argv[1] if len(argv) > 1 else "", argv[2] if len(argv) > 2 else "")
    elif action in (WIZARD, FOLDERS):
        open_setup(action)
    elif not is_setup_complete():
        open_setup(WIZARD)
    else:
        if action:
            # An argument was passed but it matched no known action; fall
            # through to a manual scan and leave a breadcrumb.
            log.debug(
                "Unknown action argument, running manual scan",
                event="default.unknown_action",
                arg=action,
            )
        run()


if __name__ == "__main__":
    main(sys.argv[1:])

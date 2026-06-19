"""MetaMender manual trigger."""
from __future__ import annotations

import sys

from resources.lib.service.actions import reset_tracker, update_now
from resources.lib.service.manual import run
from resources.lib.utils import get_logger

log = get_logger('default')

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "reset_tracker":
        reset_tracker()
    elif action == "update_now":
        dbtype = sys.argv[2] if len(sys.argv) > 2 else ""
        dbid = sys.argv[3] if len(sys.argv) > 3 else ""
        update_now(dbtype, dbid)
    else:
        if len(sys.argv) > 1:
            # An argument was passed but it matched no known action; fall
            # through to a manual scan and leave a breadcrumb.
            log.debug(
                "Unknown action argument, running manual scan",
                event="default.unknown_action",
                arg=sys.argv[1],
            )
        run()

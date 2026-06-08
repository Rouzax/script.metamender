"""MetaMender manual trigger."""
from __future__ import annotations

import sys

from resources.lib.service.actions import reset_exhausted, update_now
from resources.lib.service.manual import run

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "reset_exhausted":
        reset_exhausted()
    elif action == "update_now":
        dbtype = sys.argv[2] if len(sys.argv) > 2 else ""
        dbid = sys.argv[3] if len(sys.argv) > 3 else ""
        update_now(dbtype, dbid)
    else:
        run()

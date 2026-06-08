"""MetaMender manual trigger."""
from __future__ import annotations

import sys

from resources.lib.service.actions import reset_exhausted
from resources.lib.service.manual import run

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "reset_exhausted":
        reset_exhausted()
    else:
        run()

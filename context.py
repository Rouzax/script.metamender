"""MetaMender context-menu entry point.

Launched from the "MetaMender: Update now" context-menu item (declared as a
kodi.context.item extension in addon.xml). Reads the focused list item's
DBTYPE and DBID and runs the single-item force-correction handler.

Logging:
    Delegates to resources.lib.service.actions.update_now ('actions' logger).
"""
from __future__ import annotations

import xbmc

from resources.lib.service.actions import update_now

if __name__ == "__main__":
    dbtype = xbmc.getInfoLabel("ListItem.DBTYPE")
    dbid = xbmc.getInfoLabel("ListItem.DBID")
    update_now(dbtype, dbid)

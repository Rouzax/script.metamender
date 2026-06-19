"""Cross-process scan lock via a shared Kodi window property.

The service and a RunScript-launched manual scan run in separate Python
interpreters but the SAME Kodi process, so a Home-window (10000) property is
visible to both and serves as a mutex preventing concurrent scans (which would
otherwise contend on the SQLite tracker).

Logging:
    Logger: 'lock'
    Key events:
        - service.lock_reclaimed (WARNING): A stale lock was reclaimed (prior
          scan likely crashed). Normal acquire/release are not logged.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

import xbmcgui

from resources.lib.constants import KODI_HOME_WINDOW_ID
from resources.lib.utils import get_logger

log = get_logger('lock')

_PROP = "metamender_scan_lock"
# Reclaim a lock older than this, so a crashed scan never wedges scanning
# forever. 6h is far beyond any real scan; it only guards against a crash that
# left the property set.
_STALE_SECONDS = 6 * 3600

# This process's current lock token (timestamp:nonce), set on a successful
# acquire and checked on release so we never clear a lock another process now
# holds (for example after ours was reclaimed as stale). None when unheld.
_my_token: Optional[str] = None


def _home_window():  # type: ignore[no-untyped-def]
    return xbmcgui.Window(KODI_HOME_WINDOW_ID)


def _now() -> float:
    return time.time()


def _is_stale(value: str) -> bool:
    """True if a stored lock value is old enough to reclaim (or unparseable)."""
    try:
        ts = float(value.split(":", 1)[0])
    except (ValueError, IndexError):
        return True
    return (_now() - ts) >= _STALE_SECONDS


def acquire_scan_lock() -> bool:
    """Acquire the scan lock. Returns False if another scan holds it.

    Window properties have no compare-and-set, so this cannot be perfectly
    atomic. The race is narrowed with write-then-verify: claim the lock with a
    unique token, re-read, and proceed only if our token is the one stored. A
    concurrent acquirer whose write lost is detected here and backs off. A lock
    older than _STALE_SECONDS (for example from a crashed scan) is reclaimed.
    """
    global _my_token
    win = _home_window()
    held = win.getProperty(_PROP)
    if held and not _is_stale(held):
        return False
    if held:
        log.warning(
            "Reclaimed stale scan lock (previous scan likely crashed)",
            event="service.lock_reclaimed",
        )
    token = "{}:{}".format(_now(), uuid.uuid4().hex)
    win.setProperty(_PROP, token)
    if win.getProperty(_PROP) != token:
        # A concurrent acquirer overwrote our claim; they win, we back off.
        return False
    _my_token = token
    return True


def release_scan_lock() -> None:
    """Release the lock, but only if this process still holds it."""
    global _my_token
    win = _home_window()
    if _my_token is not None and win.getProperty(_PROP) == _my_token:
        win.clearProperty(_PROP)
    _my_token = None

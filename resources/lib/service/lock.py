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

import xbmcgui

from resources.lib.utils import get_logger

log = get_logger('lock')

_PROP = "metamender_scan_lock"
# Reclaim a lock older than this, so a crashed scan never wedges scanning
# forever. Tradeoff: this is a simple time-based mutex with no fencing token,
# so a (pathological) scan that runs longer than this window can have its lock
# reclaimed and run concurrently with the next one. 6h is far beyond any real
# scan; it only guards against a crash that left the property set.
_STALE_SECONDS = 6 * 3600


def _home_window():  # type: ignore[no-untyped-def]
    return xbmcgui.Window(10000)


def _now() -> float:
    return time.time()


def acquire_scan_lock() -> bool:
    """Acquire the scan lock. Returns False if another scan holds it."""
    win = _home_window()
    held = win.getProperty(_PROP)
    if held:
        try:
            age = _now() - float(held)
        except ValueError:
            age = _STALE_SECONDS + 1
        if age < _STALE_SECONDS:
            return False
        log.warning(
            "Reclaimed stale scan lock (previous scan likely crashed)",
            event="service.lock_reclaimed",
            age_sec=int(age),
        )
    win.setProperty(_PROP, str(_now()))
    return True


def release_scan_lock() -> None:
    _home_window().clearProperty(_PROP)

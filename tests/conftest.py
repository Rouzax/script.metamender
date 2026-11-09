"""Pytest configuration with Kodi API stubs."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Generator
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Kodi module stubs - must be injected before any addon code is imported
# ---------------------------------------------------------------------------

def _make_xbmc_stub() -> MagicMock:
    stub = MagicMock()
    stub.LOGDEBUG = 0
    stub.LOGINFO = 1
    stub.LOGWARNING = 3
    stub.LOGERROR = 4
    stub.LOGFATAL = 6
    stub.log = MagicMock()
    stub.executeJSONRPC = MagicMock(return_value="{}")
    stub.getCondVisibility = MagicMock(return_value=False)
    stub.Monitor = MagicMock

    class FakeMonitor:
        def abortRequested(self) -> bool:
            return False
        def waitForAbort(self, timeout: float = 0) -> bool:
            return False
        def onScanFinished(self, library: str) -> None:
            pass
        def onCleanFinished(self, library: str) -> None:
            pass

    stub.Monitor = FakeMonitor
    return stub


def _make_xbmcaddon_stub() -> MagicMock:
    _project_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _addon_info = {"id": "script.metamender", "path": _project_root_dir}

    stub = MagicMock()
    addon_mock = MagicMock()
    addon_mock.getSetting = MagicMock(return_value="")
    addon_mock.getAddonInfo = MagicMock(
        side_effect=lambda key: _addon_info.get(key, "script.metamender")
    )
    addon_mock.getLocalizedString = MagicMock(return_value="")
    stub.Addon = MagicMock(return_value=addon_mock)
    return stub


def _make_xbmcgui_stub() -> MagicMock:
    stub = MagicMock()
    dialog_mock = MagicMock()
    dialog_mock.notification = MagicMock()
    stub.Dialog = MagicMock(return_value=dialog_mock)
    return stub


def _make_xbmcvfs_stub(tmp_path: str) -> MagicMock:
    stub = MagicMock()
    stub.translatePath = MagicMock(side_effect=lambda p: os.path.join(
        tmp_path, p.replace("special://profile/addon_data/", "")
    ))
    stub.exists = MagicMock(side_effect=lambda p: os.path.exists(p))
    stub.mkdirs = MagicMock(side_effect=lambda p: os.makedirs(p, exist_ok=True))
    stub.delete = MagicMock(side_effect=lambda p: os.remove(p) if os.path.exists(p) else None)
    stub.rename = MagicMock(side_effect=lambda s, d: os.rename(s, d))
    stat_mock = MagicMock()
    stat_mock.st_size = MagicMock(return_value=0)
    stub.Stat = MagicMock(return_value=stat_mock)
    return stub


# Inject stubs before addon imports
_xbmc = _make_xbmc_stub()
_xbmcaddon = _make_xbmcaddon_stub()
_xbmcgui = _make_xbmcgui_stub()

sys.modules.setdefault("xbmc", _xbmc)
sys.modules.setdefault("xbmcaddon", _xbmcaddon)
sys.modules.setdefault("xbmcgui", _xbmcgui)
sys.modules.setdefault("xbmcvfs", MagicMock())
# script.module.imdbratings is a Kodi add-on module, not installed under pytest.
sys.modules.setdefault("imdbratings", MagicMock())

# Add project root to path so `resources.lib` resolves
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


@pytest.fixture
def kodi_stubs(tmp_path: Any) -> Generator[Dict[str, MagicMock], None, None]:
    """Provide fresh Kodi stubs with tmp_path-backed xbmcvfs."""
    xbmcvfs = _make_xbmcvfs_stub(str(tmp_path))
    sys.modules["xbmcvfs"] = xbmcvfs
    yield {
        "xbmc": _xbmc,
        "xbmcaddon": _xbmcaddon,
        "xbmcgui": _xbmcgui,
        "xbmcvfs": xbmcvfs,
    }
    sys.modules["xbmcvfs"] = MagicMock()


@pytest.fixture
def settings_mock() -> Generator[MagicMock, None, None]:
    """Mock addon settings. Set return values via .side_effect or .return_value."""
    addon_mock = _xbmcaddon.Addon.return_value
    original = addon_mock.getSetting.side_effect
    yield addon_mock.getSetting
    addon_mock.getSetting.side_effect = original


@pytest.fixture
def imdbratings_stub() -> Generator[MagicMock, None, None]:
    """A fresh stand-in for script.module.imdbratings, restored on teardown."""
    stub = MagicMock()
    previous = sys.modules.get("imdbratings")
    sys.modules["imdbratings"] = stub
    yield stub
    if previous is None:
        sys.modules.pop("imdbratings", None)
    else:
        sys.modules["imdbratings"] = previous

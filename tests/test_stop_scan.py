"""Stop scan: the context-menu action that ends a running background scan."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from resources.lib.constants import STOP_SCAN_PROPERTY, VIDEO_NAV_WINDOW
from resources.lib.data.media_types import MOVIE
from resources.lib.engine import scan
from resources.lib.engine.scan import _hold_while_browsing, run_scan
from resources.lib.service import stopscan
from tests.test_scan import (  # noqa: F401 - the autouse fixture keeps providers and NFO checks offline
    _libs,
    _make_movie_item,
    _make_settings,
    _mock_secondary_providers,
    _tmdb_movie_response,
)


class _Window:
    def __init__(self) -> None:
        self.props: dict[str, str] = {}

    def getProperty(self, name: str) -> str:
        return self.props.get(name, "")

    def setProperty(self, name: str, value: str) -> None:
        self.props[name] = value

    def clearProperty(self, name: str) -> None:
        self.props.pop(name, None)


class _Monitor:
    def __init__(self) -> None:
        self.waits = 0

    def abortRequested(self) -> bool:
        return False

    def waitForAbort(self, timeout: float = 0) -> bool:
        self.waits += 1
        return False


def _home(window):
    return patch.object(scan.xbmcgui, "Window", return_value=window)


def _scan(tmp_path, window, reset=False):
    from resources.lib.data.tracker import TrackerDB
    item = _make_movie_item(plot="Old plot")
    item["premiered"] = date.today().isoformat()
    tracker = TrackerDB(str(tmp_path / "t.db"))
    with patch("resources.lib.providers.tmdb.fetch_movie",
               return_value=_tmdb_movie_response(plot="Fresh plot")), \
         patch("resources.lib.data.kodi.update_item", return_value=True) as update, \
         patch.object(scan, "_hold_while_browsing", MagicMock()), _home(window):
        stats = run_scan(MOVIE, tracker, _libs(frozenset({"plot"})), "key",
                         _make_settings(), monitor=_Monitor(), items=[item], reset=reset)
    tracker.close()
    return stats, update


def test_a_requested_stop_ends_the_scan_before_the_next_item(tmp_path, kodi_stubs):
    window = _Window()
    window.setProperty(STOP_SCAN_PROPERTY, "1")
    stats, update = _scan(tmp_path, window)
    update.assert_not_called()
    assert stats["stopped"] and stats["aborted"]
    assert stats["processed"] == 0


def test_a_scan_without_a_stop_request_runs(tmp_path, kodi_stubs):
    stats, update = _scan(tmp_path, _Window())
    assert update.call_count == 1
    assert not stats["stopped"] and not stats["aborted"]


def test_update_now_ignores_a_stop_request(tmp_path, kodi_stubs):
    window = _Window()
    window.setProperty(STOP_SCAN_PROPERTY, "1")
    stats, update = _scan(tmp_path, window, reset=True)
    assert update.call_count == 1
    assert not stats["stopped"]


def test_a_stop_ends_a_browsing_hold():
    window = _Window()
    monitor = _Monitor()
    calls = {"n": 0}

    def idle():
        calls["n"] += 1
        if calls["n"] == 3:
            window.setProperty(STOP_SCAN_PROPERTY, "1")
        return 0

    with patch.object(scan.xbmcgui, "getCurrentWindowId", return_value=VIDEO_NAV_WINDOW), \
         patch.object(scan.xbmc, "getGlobalIdleTime", side_effect=idle), _home(window):
        _hold_while_browsing(MOVIE, monitor)
    # The first check opens the hold, the second waits, the third sees the stop.
    assert monitor.waits == 1


def test_request_and_clear_stop():
    window = _Window()
    with patch.object(stopscan.xbmcgui, "Window", return_value=window):
        assert not stopscan.stop_requested()
        stopscan.request_stop()
        assert stopscan.stop_requested()
        stopscan.clear_stop()
        assert not stopscan.stop_requested()


def test_user_stopped_reads_the_pass_stats():
    assert stopscan.user_stopped([{"stopped": False}, {"stopped": True}])
    assert not stopscan.user_stopped([{"stopped": False}, {}])


def _run_without_items(tmp_path, window, reset=False):
    from resources.lib.data.tracker import TrackerDB
    tracker = TrackerDB(str(tmp_path / "t.db"))
    with patch("resources.lib.data.kodi.get_all_items", return_value=[]) as listing, \
         patch("resources.lib.providers.tmdb.fetch_movie") as fetch, _home(window):
        stats = run_scan(MOVIE, tracker, _libs(frozenset({"plot"})), "key",
                         _make_settings(), monitor=_Monitor(), reset=reset)
    tracker.close()
    return stats, listing, fetch


def test_a_stop_before_a_pass_skips_its_kodi_listing(tmp_path, kodi_stubs):
    window = _Window()
    window.setProperty(STOP_SCAN_PROPERTY, "1")
    stats, listing, fetch = _run_without_items(tmp_path, window)
    listing.assert_not_called()
    fetch.assert_not_called()
    assert stats["stopped"] and stats["aborted"]
    assert stats["processed"] == 0


def test_update_now_still_lists_when_a_stop_is_set(tmp_path, kodi_stubs):
    window = _Window()
    window.setProperty(STOP_SCAN_PROPERTY, "1")
    stats, listing, _fetch = _run_without_items(tmp_path, window, reset=True)
    listing.assert_called_once()
    assert not stats["stopped"]


def test_daemon_skips_the_remaining_passes_after_a_stop(kodi_stubs):
    from resources.lib.data.media_types import EPISODE, SEASON, TVSHOW
    from resources.lib.service import daemon
    from tests.test_daemon import _AbortMonitor, _lib
    from resources.lib.data.libraries import LibraryConfig

    window = _Window()
    tracker = MagicMock()
    passes = []

    def scan_pass(**kw):
        passes.append(kw["media_type"])
        window.setProperty(STOP_SCAN_PROPERTY, "1")
        return {"stopped": True, "aborted": True, "processed": 0}

    with patch.object(daemon, "_prepare_imdb_ratings", return_value=True), \
            patch.object(daemon, "run_scan", side_effect=scan_pass) as run, \
            patch.object(stopscan.xbmcgui, "Window", return_value=window):
        stats = daemon._run_scan(
            tracker, LibraryConfig([_lib("/m/", toggles=("posters",))]),
            {"tmdb_api_key": "k"}, _AbortMonitor())
    assert passes == [MOVIE]
    assert run.call_count == 1
    assert not {TVSHOW, SEASON, EPISODE} & set(passes)
    tracker.get_show_id_map.assert_not_called()
    assert list(stats) == ["movie"]
    assert stopscan.user_stopped(stats.values())

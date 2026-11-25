"""A scan holds its Kodi writes while the user browses the video library,
and Update now holds them while the user is pressing keys.

Every JSON-RPC library write makes Kodi reload the active window's list, so a
scan writing while the Videos window is in use keeps that list reloading.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from resources.lib.constants import (
    BROWSE_IDLE_SECONDS,
    UPDATE_NOW_IDLE_SECONDS,
    VIDEO_NAV_WINDOW,
)
from resources.lib.data.media_types import MOVIE
from resources.lib.engine import scan
from resources.lib.engine.scan import _hold_while_browsing, run_scan
from tests.test_scan import (  # noqa: F401 - the autouse fixture keeps providers and NFO checks offline
    _libs,
    _make_movie_item,
    _make_settings,
    _mock_secondary_providers,
    _tmdb_movie_response,
)


class _Monitor:
    def __init__(self, abort_after: int | None = None) -> None:
        self.waits = 0
        self._abort_after = abort_after

    def abortRequested(self) -> bool:
        return self._abort_after is not None and self.waits >= self._abort_after

    def waitForAbort(self, timeout: float = 0) -> bool:
        self.waits += 1
        return self.abortRequested()


def _kodi(windows, idles):
    """Patch the active window id and Kodi's idle time, one value per check;
    the last value repeats."""
    def seq(values):
        it = iter(values)
        last = [values[-1]]

        def nxt():
            try:
                last[0] = next(it)
            except StopIteration:
                pass
            return last[0]
        return nxt
    return (
        patch.object(scan.xbmcgui, "getCurrentWindowId", side_effect=seq(windows)),
        patch.object(scan.xbmc, "getGlobalIdleTime", side_effect=seq(idles)),
    )


def test_no_hold_outside_the_video_library():
    monitor = _Monitor()
    w, i = _kodi([10000], [0])
    with w, i:
        _hold_while_browsing(MOVIE, monitor)
    assert monitor.waits == 0


def test_no_hold_when_idle_in_the_video_library():
    monitor = _Monitor()
    w, i = _kodi([VIDEO_NAV_WINDOW], [BROWSE_IDLE_SECONDS])
    with w, i:
        _hold_while_browsing(MOVIE, monitor)
    assert monitor.waits == 0


def test_holds_until_the_user_goes_idle():
    monitor = _Monitor()
    w, i = _kodi([VIDEO_NAV_WINDOW], [3, 10, 40, BROWSE_IDLE_SECONDS])
    with w, i:
        _hold_while_browsing(MOVIE, monitor)
    # 3 opens the hold; it waits at 10 and 40 and ends at the idle limit.
    assert monitor.waits == 2


def test_holds_until_the_user_leaves_the_library():
    monitor = _Monitor()
    w, i = _kodi([VIDEO_NAV_WINDOW, VIDEO_NAV_WINDOW, 10000], [1])
    with w, i:
        _hold_while_browsing(MOVIE, monitor)
    # The first check opens the hold; it waits once and ends on the home window.
    assert monitor.waits == 1


def test_hold_ends_on_kodi_shutdown():
    monitor = _Monitor(abort_after=2)
    w, i = _kodi([VIDEO_NAV_WINDOW], [0])
    with w, i:
        _hold_while_browsing(MOVIE, monitor)
    assert monitor.waits == 2


def test_no_hold_without_a_monitor():
    w, i = _kodi([VIDEO_NAV_WINDOW], [0])
    with w as win, i:
        _hold_while_browsing(MOVIE, None)
    win.assert_not_called()


def test_update_now_holds_until_the_remote_is_quiet():
    monitor = _Monitor()
    w, i = _kodi([VIDEO_NAV_WINDOW], [0, 1, UPDATE_NOW_IDLE_SECONDS])
    with w, i:
        _hold_while_browsing(MOVIE, monitor, UPDATE_NOW_IDLE_SECONDS)
    # A press just now opens the hold; it waits once and ends when quiet.
    assert monitor.waits == 1


def test_update_now_does_not_hold_when_the_remote_is_quiet():
    monitor = _Monitor()
    w, i = _kodi([VIDEO_NAV_WINDOW], [UPDATE_NOW_IDLE_SECONDS])
    with w, i:
        _hold_while_browsing(MOVIE, monitor, UPDATE_NOW_IDLE_SECONDS)
    assert monitor.waits == 0


def test_no_window_lookup_while_the_remote_is_quiet():
    # getCurrentWindowId takes Kodi's graphics lock, which waits out a list
    # reload; the lock-free idle time is checked first.
    monitor = _Monitor()
    w, i = _kodi([VIDEO_NAV_WINDOW], [UPDATE_NOW_IDLE_SECONDS])
    with w as win, i:
        _hold_while_browsing(MOVIE, monitor, UPDATE_NOW_IDLE_SECONDS)
    win.assert_not_called()


def test_update_now_idle_window_is_short():
    assert 0 < UPDATE_NOW_IDLE_SECONDS < BROWSE_IDLE_SECONDS


def _scan(tmp_path, reset):
    from resources.lib.data.tracker import TrackerDB
    item = _make_movie_item(plot="Old plot")
    item["premiered"] = date.today().isoformat()
    tracker = TrackerDB(str(tmp_path / "t.db"))
    hold = MagicMock()
    with patch("resources.lib.providers.tmdb.fetch_movie",
               return_value=_tmdb_movie_response(plot="Fresh plot")), \
         patch("resources.lib.data.kodi.update_item", return_value=True) as update, \
         patch.object(scan, "_hold_while_browsing", hold):
        run_scan(MOVIE, tracker, _libs(frozenset({"plot"})), "key",
                 _make_settings(), monitor=_Monitor(), items=[item], reset=reset)
    tracker.close()
    return hold, update


def test_background_scan_holds_before_writing(tmp_path, kodi_stubs):
    hold, update = _scan(tmp_path, reset=False)
    assert hold.call_count == 1
    assert hold.call_args.args[2] == BROWSE_IDLE_SECONDS
    assert update.call_count == 1


def test_update_now_holds_only_while_keys_are_pressed(tmp_path, kodi_stubs):
    hold, update = _scan(tmp_path, reset=True)
    assert hold.call_count == 1
    assert hold.call_args.args[2] == UPDATE_NOW_IDLE_SECONDS
    assert update.call_count == 1

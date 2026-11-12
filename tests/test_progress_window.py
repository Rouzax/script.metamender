"""Tests for the refresh progress window's properties (the window itself
needs Kodi and is checked live)."""
from __future__ import annotations

import pytest

from resources.lib.service.refresh import RefreshProgress
from resources.lib.ui import progress_window
from resources.lib.ui.progress_window import progress_properties
from resources.lib.ui.theme import THEMES

GOLDEN_HOUR = THEMES[0]

_LANG = {
    32474: "{} of {} episodes", 32475: "About {} minutes left",
    32476: "Less than a minute left", 32477: "{} updated", 32478: "{} unchanged",
    32479: "{} could not be checked", 32480: "Stop", 32481: "Background",
}


@pytest.fixture(autouse=True)
def strings(monkeypatch):
    from resources.lib.service import refresh
    monkeypatch.setattr(progress_window, "lang", lambda i: _LANG.get(i, ""))
    monkeypatch.setattr(refresh, "lang", lambda i: _LANG.get(i, ""))


def _progress(done, total, updated=0, unchecked=0):
    p = RefreshProgress()
    p.begin(total, now=0.0)
    for n in range(done):
        after = {"updated": 1 if n < updated else 0,
                 "transient": 1 if updated <= n < updated + unchecked else 0}
        p.item_finished({}, after)
    p.item_started("Season 3, episode 7: One Minute")
    return p


def test_properties_mid_run():
    props = progress_properties(_progress(6, 10, updated=2, unchecked=1), "Refreshing Lost",
                                now=60.0)
    assert props["MM.Heading"] == "Refreshing Lost"
    assert props["MM.Current"] == "Season 3, episode 7: One Minute"
    assert props["MM.Count"] == "6 of 10 episodes"
    assert props["MM.TimeLeft"] == "Less than a minute left"  # 10 s each, 4 to go
    assert props["MM.Percent"] == "60"
    assert (props["MM.Updated"], props["MM.Unchanged"], props["MM.Unchecked"]) == (
        "2 updated", "3 unchanged", "1 could not be checked")
    assert (props["MM.Stop"], props["MM.Background"]) == ("Stop", "Background")
    assert GOLDEN_HOUR.items() <= props.items()


def test_minutes_left_and_no_estimate_yet():
    assert progress_properties(_progress(3, 100), "x", now=300.0)["MM.TimeLeft"] == (
        "About 162 minutes left")  # 100 s each, 97 to go
    assert progress_properties(_progress(1, 100), "x", now=10.0)["MM.TimeLeft"] == ""


def test_empty_scope_has_zero_percent():
    assert progress_properties(_progress(0, 0), "x", now=0.0)["MM.Percent"] == "0"



def test_close_is_claimed_once_by_racing_closers():
    """The follower thread and a button press closing together: one claim."""
    import threading

    for _ in range(50):
        state = progress_window.CloseOnce()
        state.opened()
        barrier = threading.Barrier(8)
        wins = []

        def close(state=state, barrier=barrier, wins=wins):
            barrier.wait()
            wins.append(state.claim())

        threads = [threading.Thread(target=close) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert wins.count(True) == 1
        assert not state.is_open


def test_a_window_never_opened_cannot_be_closed():
    state = progress_window.CloseOnce()
    assert state.claim() is False


def test_the_refresh_loop_ends_when_kodi_quits():
    """The window's refresh thread waits on Kodi's abort, not a plain sleep, so a
    Kodi that is quitting is not held up by it."""
    import threading

    class QuittingMonitor:
        def waitForAbort(self, timeout):
            return True

    state = progress_window.CloseOnce()
    state.opened()
    renders, closes = [], []
    progress_window.follow(state, threading.Event(), lambda: renders.append(1),
                           closes.append, QuittingMonitor())
    assert renders == [1] and closes == ["background"]


def test_the_refresh_loop_closes_when_the_refresh_is_done():
    import threading

    class QuietMonitor:
        def waitForAbort(self, timeout):
            return False

    state = progress_window.CloseOnce()
    state.opened()
    done = threading.Event()
    done.set()
    closes = []
    progress_window.follow(state, done, lambda: None, closes.append, QuietMonitor())
    assert closes == ["done"]

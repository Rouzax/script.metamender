"""Tests for the show and season refresh (Update now on a show or season)."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from resources.lib.data.media_types import EPISODE, TVSHOW
from resources.lib.service import refresh
from resources.lib.service.refresh import (
    RefreshProgress,
    Scope,
    resolve_scope,
    run_refresh,
    show_id_of,
)

_SHOW = {"tvshowid": 7, "title": "Lost"}


def test_resolve_scope_for_a_show():
    with patch.object(refresh, "get_item_by_id", return_value=_SHOW):
        assert resolve_scope("tvshow", 7) == Scope(7, None, "Lost")


def test_resolve_scope_for_a_season():
    with patch.object(refresh, "get_season_info", return_value=(7, 2)), \
            patch.object(refresh, "get_item_by_id", return_value=_SHOW):
        assert resolve_scope("season", 31) == Scope(7, 2, "Lost")


def test_resolve_scope_unknown_or_missing():
    with patch.object(refresh, "get_season_info", return_value=None):
        assert resolve_scope("season", 31) is None
    with patch.object(refresh, "get_item_by_id", return_value=None):
        assert resolve_scope("tvshow", 7) is None
    assert resolve_scope("movie", 5) is None


def test_show_id_of():
    with patch.object(refresh, "get_season_info", return_value=(7, 2)), \
            patch.object(refresh, "get_episode_show_id", return_value=7):
        assert show_id_of("tvshow", 7) == 7
        assert show_id_of("season", 31) == 7
        assert show_id_of("episode", 100) == 7
        assert show_id_of("movie", 5) is None
    with patch.object(refresh, "get_episode_show_id", return_value=None):
        assert show_id_of("episode", 100) is None


# --- progress model --------------------------------------------------------

_LANG = {
    32351: "No changes: {}",
    32353: "Could not check now: {}",
    32474: "{} of {} episodes",
    32482: "Updated: {}, {} of {} changed",
    32483: "Stopped after {} of {} episodes: {}",
    32484: "Could not finish: {}",
}


@pytest.fixture
def strings(monkeypatch):
    monkeypatch.setattr(refresh, "lang", lambda i: _LANG.get(i, ""))


def _stats(updated=0, transient=0, unreachable=0):
    return {"updated": updated, "transient": transient, "unreachable": unreachable}


def test_items_are_classified_from_the_stats_delta():
    p = RefreshProgress()
    p.begin(3, now=0.0)
    p.item_finished(_stats(), _stats(updated=1))
    p.item_finished(_stats(updated=1), _stats(updated=1, transient=2))
    p.item_finished(_stats(updated=1, transient=2), _stats(updated=1, transient=2))
    assert (p.done, p.updated, p.unchecked, p.unchanged) == (3, 1, 1, 1)


def test_an_item_both_updated_and_partly_unchecked_counts_as_updated():
    p = RefreshProgress()
    p.begin(1, now=0.0)
    p.item_finished(_stats(), _stats(updated=1, unreachable=1))
    assert (p.updated, p.unchecked) == (1, 0)


def test_time_left_appears_after_three_items():
    p = RefreshProgress()
    p.begin(10, now=100.0)
    for n in range(2):
        p.item_finished(_stats(), _stats())
    assert p.seconds_left(now=120.0) is None
    p.item_finished(_stats(), _stats())
    assert p.seconds_left(now=130.0) == 70  # 10 s per item, 7 to go


def test_empty_scope(strings):
    p = RefreshProgress()
    p.begin(0, now=0.0)
    assert p.bar_text() == "0 of 0 episodes"
    assert p.seconds_left(now=50.0) is None
    assert p.toast("Lost", stopped=False, could_not_finish=False) == "No changes: Lost"


@pytest.mark.parametrize("counts,stopped,failed,expected", [
    ((5, 2, 0), True, False, "Stopped after 5 of 10 episodes: Lost"),
    ((5, 2, 0), False, True, "Could not finish: Lost"),
    ((10, 3, 1), False, False, "Updated: Lost, 3 of 10 changed"),
    ((10, 0, 1), False, False, "Could not check now: Lost"),
    ((10, 0, 0), False, False, "No changes: Lost"),
])
def test_toast(strings, counts, stopped, failed, expected):
    p = RefreshProgress()
    p.begin(10, now=0.0)
    done, updated, unchecked = counts
    for n in range(done):
        if n < updated:
            p.item_finished(_stats(), _stats(updated=1))
        elif n < updated + unchecked:
            p.item_finished(_stats(), _stats(transient=1))
        else:
            p.item_finished(_stats(), _stats())
    assert p.toast("Lost", stopped=stopped, could_not_finish=failed) == expected


def test_stop_request_crosses_threads():
    p = RefreshProgress()
    worker = threading.Thread(target=p.request_stop)
    worker.start()
    worker.join()
    assert p.stop_requested is True


# --- runner ------------------------------------------------------------------

_SETTINGS = {"enabled_fields": frozenset({"plot"}), "tmdb_api_key": "k"}


def _episode(n, season=1):
    return {"episodeid": 100 + n, "title": "Ep %d" % n, "season": season, "episode": n}


class FakeScan:
    """Stands in for run_scan: calls progress per item like the real loop and
    reports every item as updated."""

    def __init__(self, stop_after=None, aborted_on=None):
        self.calls = []
        self.stop_after = stop_after
        self.aborted_on = aborted_on

    def __call__(self, media_type, tracker, source_config, enabled_fields, api_key,
                 settings, **kw):
        self.calls.append((media_type, [i.get("title") for i in kw["items"]],
                           kw.get("show_id_map"), kw.get("reset")))
        stats = {"updated": 0, "transient": 0, "unreachable": 0,
                 "stopped": False, "aborted": False}
        for index, item in enumerate(kw["items"]):
            if not kw["progress"](index, item, stats):
                stats["stopped"] = True
                break
            stats["updated"] += 1
        if self.aborted_on is media_type:
            stats["aborted"] = True
        return stats


class FakeTracker:
    def __init__(self, scan):
        self.scan = scan

    def get_show_id_map(self):
        # Built after the show pass, so the episodes see the ids it found.
        return {"after_passes": len(self.scan.calls)}


class Monitor:
    def __init__(self, abort=False):
        self.abort = abort

    def abortRequested(self):
        return self.abort


def _run(scope, scan, episodes, monitor=None, progress=None, strings_ok=True):
    progress = progress or RefreshProgress()
    with patch.object(refresh, "run_scan", scan), \
            patch.object(refresh, "get_all_items", return_value=episodes) as listing, \
            patch.object(refresh, "get_item_by_id", return_value={"tvshowid": 7, "title": "Lost"}), \
            patch.object(refresh, "lang", lambda i: {32472: "Show details",
                                                     32473: "Season {}, episode {}: {}"}.get(i, "")):
        outcome = run_refresh(scope, progress, _SETTINGS, {"sources": []},
                              FakeTracker(scan), monitor or Monitor())
    return outcome, progress, listing


def test_show_refresh_runs_the_show_then_its_episodes():
    scan = FakeScan()
    outcome, progress, listing = _run(Scope(7, None, "Lost"), scan, [_episode(1), _episode(2)])
    assert [c[0] for c in scan.calls] == [TVSHOW, EPISODE]
    assert scan.calls[1][2] == {"after_passes": 1}  # map built after the show pass
    assert all(c[3] is True for c in scan.calls)
    assert listing.call_args.kwargs == {"tvshowid": 7, "season": None}
    assert (progress.total, progress.done, progress.updated) == (3, 3, 3)
    assert not (outcome.stopped or outcome.could_not_finish or outcome.aborted)


def test_season_refresh_runs_only_its_episodes():
    scan = FakeScan()
    _, progress, listing = _run(Scope(7, 2, "Lost"), scan, [_episode(1, 2)])
    assert [c[0] for c in scan.calls] == [EPISODE]
    assert listing.call_args.kwargs == {"tvshowid": 7, "season": 2}
    assert progress.current == "Season 2, episode 1: Ep 1"


def test_stop_ends_the_refresh_between_items():
    progress = RefreshProgress()
    scan = FakeScan()
    original = scan.__call__

    def stopping(*a, **kw):
        inner = kw["progress"]

        def wrapped(index, item, stats):
            if item.get("title") == "Ep 2":
                progress.request_stop()
            return inner(index, item, stats)
        kw["progress"] = wrapped
        return original(*a, **kw)

    stopping.calls = scan.calls  # type: ignore[attr-defined]  # FakeTracker reads it
    outcome, progress, _ = _run(Scope(7, None, "Lost"), stopping,
                                [_episode(1), _episode(2), _episode(3)], progress=progress)
    assert outcome.stopped is True
    assert progress.done == 2  # the show record and episode 1


def test_providers_down_means_could_not_finish():
    outcome, _, _ = _run(Scope(7, None, "Lost"), FakeScan(aborted_on=TVSHOW), [_episode(1)])
    assert outcome.could_not_finish is True and outcome.aborted is False


def test_kodi_shutdown_means_aborted():
    outcome, _, _ = _run(Scope(7, None, "Lost"), FakeScan(aborted_on=TVSHOW), [_episode(1)],
                         monitor=Monitor(abort=True))
    assert outcome.aborted is True and outcome.could_not_finish is False


def test_show_without_episodes_runs_one_pass():
    scan = FakeScan()
    _, progress, _ = _run(Scope(7, None, "Lost"), scan, [])
    assert [c[0] for c in scan.calls] == [TVSHOW]
    assert (progress.total, progress.done) == (1, 1)


# --- views, signals and the whole flow ---------------------------------------


class FakeBar:
    def __init__(self, log):
        self.log = log

    def create(self, heading, message):
        self.log.append(("create", heading, message))

    def update(self, percent, heading, message):
        self.log.append(("update", percent))

    def close(self):
        self.log.append(("close",))


def _drive(choices, finish_after_window=None, reopen_at=None, finish_after_updates=2):
    """Run drive_views with a window that returns ``choices`` in turn and a
    worker that finishes after a given number of bar updates."""
    done = threading.Event()
    progress = RefreshProgress()
    progress.begin(4, now=0.0)
    log = []
    shown = []
    updates = {"n": 0}

    def show_window():
        choice = choices[len(shown)]
        shown.append(choice)
        if finish_after_window == len(shown):
            done.set()
        return choice

    class Bar(FakeBar):
        def update(self, percent, heading, message):
            super().update(percent, heading, message)
            updates["n"] += 1
            if updates["n"] >= finish_after_updates and (reopen_at is None or len(shown) > 1):
                done.set()

    def take_reopen():
        return reopen_at is not None and updates["n"] == reopen_at and len(shown) == 1

    refresh.drive_views(progress, done, "MetaMender: Lost", show_window,
                        lambda: Bar(log), take_reopen, poll=0.001)
    return shown, log, progress


def test_background_then_finish_closes_the_bar():
    shown, log, _ = _drive(["background"])
    assert shown == ["background"]
    assert log[0][0] == "create" and log[-1] == ("close",)


def test_reopen_request_shows_the_window_again():
    shown, log, _ = _drive(["background", "background"], reopen_at=1)
    assert shown == ["background", "background"]
    assert [e[0] for e in log].count("create") == 2


def test_stop_requests_stop_and_waits_in_the_bar():
    shown, log, progress = _drive(["stop"])
    assert progress.stop_requested is True
    assert shown == ["stop"] and log[-1] == ("close",)


def test_finished_while_the_window_is_open_shows_no_bar():
    shown, log, _ = _drive(["done"], finish_after_window=1)
    assert shown == ["done"] and log == []


@pytest.mark.parametrize("running,clicked,expected", [
    (7, 7, "reopen"), (7, 8, "busy"), (7, None, "busy"), (None, 7, "busy"),
])
def test_bring_back_or_busy(running, clicked, expected):
    with patch.object(refresh, "refresh_running_show", return_value=running):
        assert refresh.bring_back_or_busy(clicked) == expected


class Home:
    """The home window's properties."""

    def __init__(self):
        self.props = {}

    def getProperty(self, name):
        return self.props.get(name, "")

    def setProperty(self, name, value):
        self.props[name] = value

    def clearProperty(self, name):
        self.props.pop(name, None)


@pytest.fixture
def flow(monkeypatch):
    """open_refresh with Kodi, the lock and the worker's pieces replaced."""
    home = Home()
    notes = []
    released = []
    state = {"outcome": refresh.RefreshOutcome(), "raise": None, "lock": True,
             "running_seen": None}
    monkeypatch.setattr(refresh, "_home_window", lambda: home)
    monkeypatch.setattr(refresh, "resolve_scope", lambda d, i: Scope(7, None, "Lost"))
    monkeypatch.setattr(refresh, "acquire_scan_lock", lambda: state["lock"])
    monkeypatch.setattr(refresh, "release_scan_lock", lambda: released.append(True))
    monkeypatch.setattr(refresh, "notify", notes.append)
    monkeypatch.setattr(refresh, "lang", lambda i: "#%d {}" % i)
    monkeypatch.setattr(refresh, "_load_settings", lambda: {"tmdb_api_key": "k"})
    monkeypatch.setattr(refresh, "TrackerDB", lambda path: type("T", (), {"close": lambda s: None})())
    monkeypatch.setattr(refresh, "_get_db_path", lambda: ":memory:")
    monkeypatch.setattr(refresh, "load_source_config", lambda p: {"sources": []})
    monkeypatch.setattr(refresh, "_get_sources_config_path", lambda: "x")
    monkeypatch.setattr(refresh, "_prepare_imdb_ratings", lambda s, m: False)
    monkeypatch.setattr(refresh.xbmc, "Monitor", lambda: Monitor())
    monkeypatch.setattr(refresh.StructuredLogger, "start_scan_log", lambda: None)
    monkeypatch.setattr(refresh.StructuredLogger, "stop_scan_log", lambda: None)

    def fake_run(scope, progress, settings, source_config, tracker, monitor):
        state["running_seen"] = home.getProperty(refresh._RUNNING_SHOW)
        if state["raise"]:
            raise state["raise"]
        return state["outcome"]

    def fake_window(progress, heading, done):
        done.wait(2)
        return "done"

    monkeypatch.setattr(refresh, "run_refresh", fake_run)
    monkeypatch.setattr(refresh, "show_progress_window", fake_window)
    return home, notes, released, state


def test_flow_reports_and_cleans_up(flow):
    home, notes, released, state = flow
    refresh.open_refresh("tvshow", 7)
    assert state["running_seen"] == "7"
    assert notes == ["#32351 Lost"]
    assert released == [True] and home.props == {}


def test_flow_worker_failure_still_cleans_up(flow):
    home, notes, released, state = flow
    state["raise"] = RuntimeError("boom")
    refresh.open_refresh("tvshow", 7)
    assert notes == ["#32347 {}"]
    assert released == [True] and home.props == {}


def test_flow_after_shutdown_shows_no_toast(flow):
    home, notes, released, state = flow
    state["outcome"] = refresh.RefreshOutcome(aborted=True)
    refresh.open_refresh("tvshow", 7)
    assert notes == [] and released == [True]


def test_flow_with_the_lock_held_reopens_or_says_busy(flow):
    home, notes, released, state = flow
    state["lock"] = False
    home.setProperty(refresh._RUNNING_SHOW, "7")
    refresh.open_refresh("season", 31)
    assert home.getProperty(refresh._REOPEN) == "1" and notes == [] and released == []
    home.props.clear()
    refresh.open_refresh("season", 31)
    assert notes == ["#32344 {}"]

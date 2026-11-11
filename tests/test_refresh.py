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

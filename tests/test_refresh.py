"""Tests for the show and season refresh (Update now on a show or season)."""
from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from resources.lib.service import refresh
from resources.lib.service.refresh import RefreshProgress, Scope, resolve_scope, show_id_of

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

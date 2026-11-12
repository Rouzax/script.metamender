"""Tests for service/attention.py: the record behind the attention window."""
from __future__ import annotations

from resources.lib.service import attention
from resources.lib.service.attention import (
    FOLDER_OFFLINE,
    KEY_REJECTED,
    NO_KEY,
    SCANS_FAILING,
    SETUP,
    TVDB_AUTH,
    Attention,
    Problem,
)

DAY = 24 * 3600


class FakeTracker:
    def __init__(self, value=None):
        self.values = {} if value is None else {"attention": value}

    def get_scan_state(self, key):
        return self.values.get(key)

    def set_scan_state(self, key, value):
        self.values[key] = value


def test_window_problem_shown_once():
    a = Attention()
    a.raise_problem(KEY_REJECTED)
    assert a.pending_window() == (Problem(KEY_REJECTED, ""), [])
    a.mark_window_shown()
    assert a.pending_window() is None
    a.raise_problem(KEY_REJECTED)
    assert a.pending_window() is None


def test_cleared_and_back_shows_again():
    a = Attention()
    a.raise_problem(KEY_REJECTED)
    a.mark_window_shown()
    a.clear(KEY_REJECTED)
    assert a.pending_window() is None
    a.raise_problem(KEY_REJECTED)
    assert a.pending_window() == (Problem(KEY_REJECTED, ""), [])


def test_changed_detail_shows_again():
    a = Attention()
    a.raise_problem(FOLDER_OFFLINE, "smb://a/")
    a.mark_window_shown()
    a.raise_problem(FOLDER_OFFLINE, "smb://a/, smb://b/")
    assert a.pending_window() == (Problem(FOLDER_OFFLINE, "smb://a/, smb://b/"), [])


def test_most_important_first_and_others_listed():
    a = Attention()
    a.raise_problem(FOLDER_OFFLINE, "smb://a/")
    a.raise_problem(SETUP)
    assert a.pending_window() == (Problem(SETUP, ""), [Problem(FOLDER_OFFLINE, "smb://a/")])
    a.mark_window_shown()
    a.raise_problem(NO_KEY)
    assert a.pending_window() == (
        Problem(SETUP, ""), [Problem(NO_KEY, ""), Problem(FOLDER_OFFLINE, "smb://a/")])


def test_failing_streak_needs_three_and_a_day():
    a = Attention()
    for t in (0, 300, 600):
        a.attempt(failed=True, now=t)
    assert a.pending_window() is None
    a.attempt(failed=True, now=DAY)
    assert a.pending_window() == (Problem(SCANS_FAILING, ""), [])
    a.attempt(failed=False, now=DAY + 10)
    assert a.pending_window() is None
    for t in (DAY + 20, DAY + 30, DAY + 40):
        a.attempt(failed=True, now=t)
    assert a.pending_window() is None


def test_three_failures_over_a_day_raise():
    a = Attention()
    for t in (0, DAY / 2, DAY):
        a.attempt(failed=True, now=t)
    assert a.pending_window() == (Problem(SCANS_FAILING, ""), [])


def test_folder_offline_after_three_days_and_back():
    a = Attention()
    path = "smb://nas/Series/"
    a.folders_checked({path: False, "smb://nas/Movies/": True}, now=0)
    a.folders_checked({path: False}, now=2 * DAY)
    assert a.pending_window() is None
    a.folders_checked({path: False}, now=3 * DAY)
    assert a.pending_window() == (Problem(FOLDER_OFFLINE, path), [])
    a.folders_checked({path: True}, now=3 * DAY + 60)
    assert a.pending_window() is None
    a.folders_checked({path: False}, now=4 * DAY)
    a.folders_checked({path: False}, now=6 * DAY)
    assert a.pending_window() is None
    a.folders_checked({path: False}, now=7 * DAY)
    assert a.pending_window() == (Problem(FOLDER_OFFLINE, path), [])


def test_several_offline_folders_in_one_detail():
    a = Attention()
    a.folders_checked({"smb://b/": False, "smb://a/": False}, now=0)
    a.folders_checked({"smb://b/": False, "smb://a/": False}, now=3 * DAY)
    assert a.pending_window() == (Problem(FOLDER_OFFLINE, "smb://a/, smb://b/"), [])


def test_tvdb_toast_once():
    a = Attention()
    assert not a.pending_toast(TVDB_AUTH)
    a.raise_problem(TVDB_AUTH)
    assert a.pending_toast(TVDB_AUTH)
    assert a.pending_window() is None
    a.mark_toast_shown(TVDB_AUTH)
    assert not a.pending_toast(TVDB_AUTH)
    a.raise_problem(TVDB_AUTH)
    assert not a.pending_toast(TVDB_AUTH)
    a.clear(TVDB_AUTH)
    a.raise_problem(TVDB_AUTH)
    assert a.pending_toast(TVDB_AUTH)


def test_corrupt_or_missing_state_is_empty():
    for text in (None, "", "{oops", "[]", '{"open": 5}'):
        a = Attention.from_json(text)
        assert a.pending_window() is None
        assert not a.pending_toast(TVDB_AUTH)


def test_json_round_trip():
    a = Attention()
    a.raise_problem(KEY_REJECTED)
    a.raise_problem(TVDB_AUTH)
    a.mark_toast_shown(TVDB_AUTH)
    a.attempt(failed=True, now=5)
    a.folders_checked({"smb://a/": False}, now=7)
    a.mark_window_shown()
    b = Attention.from_json(a.to_json())
    assert b.to_json() == a.to_json()
    assert b.pending_window() is None and not b.pending_toast(TVDB_AUTH)


def test_update_rereads_before_writing():
    tracker = FakeTracker()
    stale = attention.load(tracker)
    attention.update(tracker, lambda a: a.raise_problem(KEY_REJECTED))
    # A writer holding an old copy changes it through update, not by saving
    # its copy, so the problem the other one raised survives.
    del stale
    result = attention.update(tracker, lambda a: a.raise_problem(SETUP))
    main, others = result.pending_window()
    assert main == Problem(SETUP, "") and others == [Problem(KEY_REJECTED, "")]
    assert attention.load(tracker).pending_window() == (main, others)


def test_store_errors_give_an_empty_record():
    class Broken:
        def get_scan_state(self, key):
            raise RuntimeError("db locked")

        def set_scan_state(self, key, value):
            raise RuntimeError("db locked")

    assert attention.load(Broken()).pending_window() is None
    assert attention.update(Broken(), lambda a: a.raise_problem(SETUP)).pending_window() == (
        Problem(SETUP, ""), [])

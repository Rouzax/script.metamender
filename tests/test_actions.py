"""Tests for entry-point action handlers (reset_exhausted, A20/D12)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resources.lib.service import actions


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Patch everything actions.reset_exhausted() touches."""
    tracker = MagicMock()
    tracker.reset_exhausted_fields = MagicMock(return_value=3)
    tracker_cls = MagicMock(return_value=tracker)
    notify = MagicMock()

    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(actions, "notify", notify)
    monkeypatch.setattr(actions.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(actions, "get_bool_setting", MagicMock(return_value=False))

    return {"tracker": tracker, "tracker_cls": tracker_cls, "notify": notify}


def test_reset_exhausted_calls_tracker_and_notifies(patched):
    actions.reset_exhausted()
    patched["tracker"].reset_exhausted_fields.assert_called_once_with()
    patched["notify"].assert_called_once()
    message = patched["notify"].call_args[0][0]
    assert "3" in message
    patched["tracker"].close.assert_called_once_with()


def test_reset_exhausted_closes_tracker_on_error(patched):
    patched["tracker"].reset_exhausted_fields.side_effect = RuntimeError("boom")
    actions.reset_exhausted()  # should not raise
    patched["tracker"].close.assert_called_once_with()
    patched["notify"].assert_not_called()


# ---------------------------------------------------------------------------
# update_now (D3 "Update now" force scan)
# ---------------------------------------------------------------------------


@pytest.fixture
def update_patched(monkeypatch: pytest.MonkeyPatch):
    """Patch everything actions.update_now() touches."""
    tracker = MagicMock()
    tracker_cls = MagicMock(return_value=tracker)
    notify = MagicMock()
    run_scan = MagicMock(return_value={"updated": 1})
    get_item_by_id = MagicMock(return_value={"title": "Air", "movieid": 7})
    acquire = MagicMock(return_value=True)
    release = MagicMock()
    load_source_config = MagicMock(return_value={"sources": [], "default_language": "en-US"})
    build_show_id_map = MagicMock(return_value={"Some Show": "555"})
    load_settings = MagicMock(return_value={
        "tmdb_api_key": "key",
        "enabled_fields": frozenset({"plot"}),
        "rate_limit": 0.0,
    })

    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(actions, "_get_sources_config_path", MagicMock(return_value="src.json"))
    monkeypatch.setattr(actions, "_load_settings", load_settings)
    monkeypatch.setattr(actions, "_build_show_id_map", build_show_id_map)
    monkeypatch.setattr(actions, "load_source_config", load_source_config)
    monkeypatch.setattr(actions, "get_item_by_id", get_item_by_id)
    monkeypatch.setattr(actions, "run_scan", run_scan)
    monkeypatch.setattr(actions, "acquire_scan_lock", acquire)
    monkeypatch.setattr(actions, "release_scan_lock", release)
    monkeypatch.setattr(actions, "notify", notify)
    monkeypatch.setattr(actions.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(actions, "get_bool_setting", MagicMock(return_value=False))

    return {
        "tracker": tracker, "notify": notify, "run_scan": run_scan,
        "get_item_by_id": get_item_by_id, "acquire": acquire, "release": release,
        "build_show_id_map": build_show_id_map,
    }


def test_update_now_movie_runs_force_scan(update_patched):
    actions.update_now("movie", "7")
    update_patched["get_item_by_id"].assert_called_once()
    update_patched["run_scan"].assert_called_once()
    _, kwargs = update_patched["run_scan"].call_args
    assert kwargs["force"] is True
    assert kwargs["items"] == [{"title": "Air", "movieid": 7}]
    update_patched["notify"].assert_called_once()
    update_patched["release"].assert_called_once_with()


def test_update_now_unknown_dbtype_notifies_and_returns(update_patched):
    actions.update_now("musicvideo", "7")
    update_patched["run_scan"].assert_not_called()
    update_patched["acquire"].assert_not_called()
    update_patched["notify"].assert_called_once()


def test_update_now_item_not_found(update_patched):
    update_patched["get_item_by_id"].return_value = None
    actions.update_now("movie", "999")
    update_patched["run_scan"].assert_not_called()
    update_patched["notify"].assert_called_once()
    # lock acquired then released even when item missing
    update_patched["release"].assert_called_once_with()


def test_update_now_locked_skips(update_patched):
    update_patched["acquire"].return_value = False
    actions.update_now("movie", "7")
    update_patched["run_scan"].assert_not_called()
    update_patched["get_item_by_id"].assert_not_called()
    update_patched["notify"].assert_called_once()
    update_patched["release"].assert_not_called()


def test_update_now_episode_builds_show_id_map(update_patched):
    update_patched["get_item_by_id"].return_value = {
        "title": "Pilot", "episodeid": 5, "showtitle": "Some Show",
    }
    actions.update_now("episode", "5")
    update_patched["build_show_id_map"].assert_called_once()
    _, kwargs = update_patched["run_scan"].call_args
    assert kwargs["show_id_map"] == {"Some Show": "555"}
    assert kwargs["force"] is True


def test_update_now_no_changes_notifies(update_patched):
    update_patched["run_scan"].return_value = {"updated": 0}
    actions.update_now("movie", "7")
    msg = update_patched["notify"].call_args[0][0]
    assert "No changes" in msg or "no changes" in msg.lower()

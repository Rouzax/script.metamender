"""Tests for entry-point action handlers (reset_tracker, update_now)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resources.lib.service import actions


# English msgid templates for the notification string IDs actions.py uses.
# Mirrors resources/language/.../strings.po so tests assert on real text
# (the Kodi stub's getLocalizedString returns "" by default).
_LANG = {
    32341: "Invalid TMDB API key",
    32342: "Cannot update this item type",
    32343: "Invalid item",
    32344: "A scan is already running",
    32345: "Item not found",
    32346: "No TMDB API key configured",
    32347: "Update failed",
    32350: "Updated: {}",
    32351: "No changes: {}",
    32353: "Could not check now: {}",
    32360: "Reset tracker?",
    32361: "This will clear all field status records so the next scan re-fetches everything.",
    32362: "Reset complete: {} fields cleared",
}


def _fake_lang(string_id):
    return _LANG.get(string_id, "")


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Patch everything actions.reset_tracker() touches."""
    tracker = MagicMock()
    tracker.clear_field_status = MagicMock(return_value=3)
    tracker_cls = MagicMock(return_value=tracker)
    notify = MagicMock()
    acquire = MagicMock(return_value=True)
    release = MagicMock()

    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(actions, "notify", notify)
    monkeypatch.setattr(actions, "lang", _fake_lang)
    monkeypatch.setattr(actions, "acquire_scan_lock", acquire)
    monkeypatch.setattr(actions, "release_scan_lock", release)
    monkeypatch.setattr(actions.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(actions, "get_bool_setting", MagicMock(return_value=False))

    return {
        "tracker": tracker, "tracker_cls": tracker_cls, "notify": notify,
        "acquire": acquire, "release": release,
    }


# ---------------------------------------------------------------------------
# reset_tracker (settings action: clear all field_status rows)
# ---------------------------------------------------------------------------


def test_reset_tracker_clears_field_status(patched, kodi_stubs):
    import xbmcgui
    xbmcgui.Dialog.return_value.yesno.return_value = True
    actions.reset_tracker()
    assert xbmcgui.Dialog.return_value.yesno.called
    patched["tracker"].clear_field_status.assert_called_once_with()
    patched["notify"].assert_called_once()
    message = patched["notify"].call_args[0][0]
    assert "3" in message
    patched["tracker"].close.assert_called_once_with()


def test_reset_tracker_cancelled_when_user_declines(patched, kodi_stubs):
    import xbmcgui
    xbmcgui.Dialog.return_value.yesno.return_value = False
    actions.reset_tracker()
    patched["tracker_cls"].assert_not_called()
    patched["notify"].assert_not_called()


def test_reset_tracker_closes_tracker_on_error(patched, kodi_stubs):
    import xbmcgui
    xbmcgui.Dialog.return_value.yesno.return_value = True
    patched["tracker"].clear_field_status.side_effect = RuntimeError("boom")
    actions.reset_tracker()  # should not raise
    patched["tracker"].close.assert_called_once_with()
    patched["notify"].assert_not_called()


def test_reset_tracker_skips_when_scan_running(patched, kodi_stubs):
    """If the scan lock is held, reset must not touch the tracker."""
    import xbmcgui
    xbmcgui.Dialog.return_value.yesno.return_value = True
    patched["acquire"].return_value = False
    actions.reset_tracker()
    patched["tracker_cls"].assert_not_called()
    patched["tracker"].clear_field_status.assert_not_called()
    patched["notify"].assert_called_once()


# ---------------------------------------------------------------------------
# update_now (D3 "Update now" reset scan)
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
    build_show_id_map = MagicMock(return_value={7: "555"})
    tracker.get_show_id_map = build_show_id_map
    load_settings = MagicMock(return_value={
        "tmdb_api_key": "key",
        "enabled_fields": frozenset({"plot"}),
        "rate_limit": 0.0,
    })

    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(actions, "_get_sources_config_path", MagicMock(return_value="src.json"))
    monkeypatch.setattr(actions, "_load_settings", load_settings)
    monkeypatch.setattr(actions, "load_source_config", load_source_config)
    monkeypatch.setattr(actions, "get_item_by_id", get_item_by_id)
    monkeypatch.setattr(actions, "run_scan", run_scan)
    monkeypatch.setattr(actions, "acquire_scan_lock", acquire)
    monkeypatch.setattr(actions, "release_scan_lock", release)
    monkeypatch.setattr(actions, "notify", notify)
    monkeypatch.setattr(actions, "lang", _fake_lang)
    monkeypatch.setattr(actions.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(actions, "get_bool_setting", MagicMock(return_value=False))
    monkeypatch.setattr(actions, "is_setup_complete", MagicMock(return_value=True))
    open_setup = MagicMock(return_value=False)
    monkeypatch.setattr(actions, "open_setup", open_setup)

    return {
        "open_setup": open_setup,
        "tracker": tracker, "notify": notify, "run_scan": run_scan,
        "get_item_by_id": get_item_by_id, "acquire": acquire, "release": release,
        "build_show_id_map": build_show_id_map,
    }


def test_update_now_movie_runs_reset_scan(update_patched):
    actions.update_now("movie", "7")
    update_patched["get_item_by_id"].assert_called_once()
    update_patched["run_scan"].assert_called_once()
    _, kwargs = update_patched["run_scan"].call_args
    assert kwargs["reset"] is True
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
    assert kwargs["show_id_map"] == {7: "555"}
    assert kwargs["reset"] is True


def test_update_now_no_changes_notifies(update_patched):
    update_patched["run_scan"].return_value = {"updated": 0}
    actions.update_now("movie", "7")
    msg = update_patched["notify"].call_args[0][0]
    assert "No changes" in msg or "no changes" in msg.lower()


@pytest.mark.parametrize("stats", [
    {"updated": 0, "unreachable": 1},
    {"updated": 0, "transient": 2},
    {"updated": 0, "aborted": True},
])
def test_update_now_says_could_not_check(update_patched, stats):
    update_patched["run_scan"].return_value = stats
    actions.update_now("movie", "7")
    msg = update_patched["notify"].call_args[0][0]
    assert msg == "Could not check now: Air"


def test_update_now_reports_an_update_even_if_a_field_was_unchecked(update_patched):
    update_patched["run_scan"].return_value = {"updated": 1, "transient": 1}
    actions.update_now("movie", "7")
    assert update_patched["notify"].call_args[0][0] == "Updated: Air"


@patch("resources.lib.service.actions.run_scan")
@patch("resources.lib.service.actions.get_item_by_id")
def test_update_now_runs_on_disabled_source(mock_get, mock_scan, kodi_stubs, settings_mock):
    """Source disabled in config: the reset path must still run a scan."""
    from resources.lib.service import actions as _actions

    # Wire up the minimal set of dependencies that update_now touches
    # before calling run_scan (lock, db path, sources config, settings).
    with patch.object(_actions, "acquire_scan_lock", return_value=True), \
         patch.object(_actions, "release_scan_lock"), \
         patch.object(_actions, "TrackerDB"), \
         patch.object(_actions, "_get_db_path", return_value=":memory:"), \
         patch.object(_actions, "_get_sources_config_path", return_value="s.json"), \
         patch.object(_actions, "load_source_config",
                      return_value={"sources": [], "default_language": "en-US"}), \
         patch.object(_actions, "_load_settings", return_value={
             "tmdb_api_key": "key",
             "enabled_fields": frozenset({"plot"}),
             "rate_limit": 0.0,
         }), \
         patch.object(_actions, "notify"), \
         patch.object(_actions, "lang", _fake_lang), \
         patch.object(_actions, "get_bool_setting", return_value=False), \
         patch.object(_actions, "is_setup_complete", return_value=True), \
         patch.object(_actions.StructuredLogger, "initialize", MagicMock()):

        mock_get.return_value = {
            "movieid": 1,
            "title": "X",
            "file": "smb://disabled/x.mkv",
            "uniqueid": {"tmdb": "1"},
        }
        mock_scan.return_value = {"updated": 1}

        _actions.update_now("movie", "1")

    assert mock_scan.called, "run_scan was not called even though source is disabled"
    assert mock_scan.call_args.kwargs.get("reset") is True


def test_update_now_tvdb_auth_failure_notified(update_patched, monkeypatch):
    monkeypatch.setattr(actions, "lang", lambda i: "s{}".format(i))
    update_patched["run_scan"].return_value = {"updated": 0, "tvdb_auth_failed": True}
    actions.update_now("movie", "7")
    notified = [c.args[0] for c in update_patched["notify"].call_args_list]
    assert notified.count("s32352") == 1
    assert "s32341" not in notified


def test_update_now_opens_wizard_before_setup(update_patched, monkeypatch):
    monkeypatch.setattr(actions, "is_setup_complete", MagicMock(return_value=False))
    actions.update_now("movie", "7")
    update_patched["open_setup"].assert_called_once_with("wizard")
    update_patched["run_scan"].assert_not_called()

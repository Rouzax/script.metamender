"""Tests for entry-point action handlers (reset_tracker, update_now)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from resources.lib.data.libraries import Library, LibraryConfig
from resources.lib.service import actions, refresh

_AIR = "smb://nas/Movies/Air (2023)/air.mkv"
_MOVIES = Library(path="smb://nas/Movies/", label="Movies", included=True,
                  language="en-US", fallback_language="", toggles=frozenset({"plots"}))


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
    ask = MagicMock(return_value=True)
    monkeypatch.setattr(actions, "show_question", ask)

    return {
        "tracker": tracker, "tracker_cls": tracker_cls, "notify": notify,
        "acquire": acquire, "release": release, "ask": ask,
    }


# ---------------------------------------------------------------------------
# reset_tracker (settings action: clear all field_status rows)
# ---------------------------------------------------------------------------


def test_reset_tracker_clears_field_status(patched, kodi_stubs):
    actions.reset_tracker()
    patched["ask"].assert_called_once_with("Reset tracker?", _LANG[32361])
    patched["tracker"].clear_field_status.assert_called_once_with()
    patched["notify"].assert_called_once()
    message = patched["notify"].call_args[0][0]
    assert "3" in message
    patched["tracker"].close.assert_called_once_with()


def test_reset_tracker_cancelled_when_user_declines(patched, kodi_stubs):
    patched["ask"].return_value = False
    actions.reset_tracker()
    patched["tracker_cls"].assert_not_called()
    patched["notify"].assert_not_called()


def test_reset_tracker_closes_tracker_on_error(patched, kodi_stubs):
    patched["tracker"].clear_field_status.side_effect = RuntimeError("boom")
    actions.reset_tracker()  # should not raise
    patched["tracker"].close.assert_called_once_with()
    patched["notify"].assert_not_called()


def test_reset_tracker_skips_when_scan_running(patched, kodi_stubs):
    """If the scan lock is held, reset must not touch the tracker."""
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
    get_item_by_id = MagicMock(return_value={"title": "Air", "movieid": 7, "file": _AIR})
    acquire = MagicMock(return_value=True)
    release = MagicMock()
    load_libraries = MagicMock(return_value=LibraryConfig([_MOVIES]))
    build_show_id_map = MagicMock(return_value={7: "555"})
    tracker.get_show_id_map = build_show_id_map
    load_settings = MagicMock(return_value={"tmdb_api_key": "key", "rate_limit": 0.0})

    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(refresh, "_get_sources_config_path", MagicMock(return_value="src.json"))
    monkeypatch.setattr(actions, "_load_settings", load_settings)
    monkeypatch.setattr(refresh, "load_libraries", load_libraries)
    monkeypatch.setattr(refresh, "notify", notify)
    monkeypatch.setattr(refresh, "lang", _fake_lang)
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
        "build_show_id_map": build_show_id_map, "load_libraries": load_libraries,
        "tracker_cls": tracker_cls,
    }


def test_update_now_movie_runs_reset_scan(update_patched):
    actions.update_now("movie", "7")
    update_patched["get_item_by_id"].assert_called_once()
    update_patched["run_scan"].assert_called_once()
    _, kwargs = update_patched["run_scan"].call_args
    assert kwargs["reset"] is True
    assert kwargs["items"] == [{"title": "Air", "movieid": 7, "file": _AIR}]
    assert kwargs["libraries"] == LibraryConfig([_MOVIES])
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
        "file": "smb://nas/Movies/Some Show/s01e01.mkv",
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


@pytest.mark.parametrize("libraries", [
    LibraryConfig([replace(_MOVIES, included=False)]),
    LibraryConfig([replace(_MOVIES, subfolders=({"path": "smb://nas/Movies/", "enabled": False},))]),
    LibraryConfig([]),
    None,  # no libraries file
], ids=["library_left_out", "subfolder_left_out", "no_library", "no_file"])
def test_update_now_on_excluded_library_shows_message_and_changes_nothing(
        update_patched, monkeypatch, libraries):
    monkeypatch.setattr(refresh, "lang", lambda i: "s{}".format(i))
    update_patched["load_libraries"].return_value = libraries
    actions.update_now("movie", "7")
    update_patched["run_scan"].assert_not_called()
    update_patched["tracker_cls"].assert_not_called()
    assert [c.args[0] for c in update_patched["notify"].call_args_list] == ["s32650"]
    update_patched["release"].assert_called_once_with()


def test_update_now_prepares_imdb_for_the_item_library_only(update_patched, monkeypatch):
    prepare = MagicMock(return_value=True)
    monkeypatch.setattr(actions, "_prepare_imdb_ratings", prepare)
    actions.update_now("movie", "7")
    assert prepare.call_args.args[0] == [_MOVIES]
    assert update_patched["run_scan"].call_args.kwargs["settings"]["imdb_ready"] is True


def test_update_now_tvdb_auth_failure_notified(update_patched, monkeypatch):
    monkeypatch.setattr(actions, "lang", lambda i: "s{}".format(i))
    update_patched["run_scan"].return_value = {"updated": 0, "tvdb_auth_failed": True}
    actions.update_now("movie", "7")
    notified = [c.args[0] for c in update_patched["notify"].call_args_list]
    assert notified.count("s32352") == 1
    assert "s32341" not in notified


@pytest.mark.parametrize("key, text", [("mine", "s32742"), ("", "s32743")])
def test_update_now_fanart_auth_failure_names_the_key_in_use(
        update_patched, monkeypatch, key, text):
    monkeypatch.setattr(actions, "lang", lambda i: "s{}".format(i))
    monkeypatch.setattr(actions, "_load_settings", lambda: {
        "tmdb_api_key": "key", "rate_limit": 0.0, "fanart_client_key": key})
    update_patched["run_scan"].return_value = {"updated": 0, "fanart_auth_failed": True}
    actions.update_now("movie", "7")
    notified = [c.args[0] for c in update_patched["notify"].call_args_list]
    assert notified.count(text) == 1


def test_update_now_opens_wizard_before_setup(update_patched, monkeypatch):
    monkeypatch.setattr(actions, "is_setup_complete", MagicMock(return_value=False))
    actions.update_now("movie", "7")
    update_patched["open_setup"].assert_called_once_with("wizard")
    update_patched["run_scan"].assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_update_now_writes_its_own_log(update_patched, monkeypatch, fails):
    start, stop = MagicMock(return_value="x.log"), MagicMock()
    monkeypatch.setattr(actions.StructuredLogger, "start_scan_log", start)
    monkeypatch.setattr(actions.StructuredLogger, "stop_scan_log", stop)
    if fails:
        update_patched["run_scan"].side_effect = RuntimeError("boom")
    actions.update_now("movie", "7")
    start.assert_called_once()
    stop.assert_called_once()


def test_update_now_opens_no_log_while_a_scan_runs(update_patched, monkeypatch):
    start = MagicMock()
    monkeypatch.setattr(actions.StructuredLogger, "start_scan_log", start)
    update_patched["acquire"].return_value = False
    actions.update_now("movie", "7")
    start.assert_not_called()


@pytest.mark.parametrize("dbtype", ["tvshow", "season"])
def test_update_now_on_a_show_or_season_opens_the_refresh(update_patched, monkeypatch, dbtype):
    opened = MagicMock()
    monkeypatch.setattr(actions, "open_refresh", opened)
    actions.update_now(dbtype, "7")
    opened.assert_called_once_with(dbtype, 7)
    update_patched["run_scan"].assert_not_called()
    update_patched["acquire"].assert_not_called()


def test_update_now_on_an_episode_inside_the_running_refresh_reopens_it(update_patched, monkeypatch):
    update_patched["acquire"].return_value = False
    reopen = MagicMock()
    monkeypatch.setattr(actions, "show_id_of", lambda d, i: 7)
    monkeypatch.setattr(actions, "bring_back_or_busy", lambda show: "reopen" if show == 7 else "busy")
    monkeypatch.setattr(actions, "request_reopen", reopen)
    actions.update_now("episode", "100")
    reopen.assert_called_once()
    update_patched["notify"].assert_not_called()


def test_update_now_on_a_movie_while_a_refresh_runs_says_busy(update_patched, monkeypatch):
    update_patched["acquire"].return_value = False
    monkeypatch.setattr(actions, "show_id_of", lambda d, i: None)
    monkeypatch.setattr(actions, "bring_back_or_busy", lambda show: "busy")
    actions.update_now("movie", "7")
    assert "already running" in update_patched["notify"].call_args[0][0]


def _scan_state_store(tracker):
    """Back the tracker mock's scan_state with a dict, as the real table."""
    store = {}
    tracker.get_scan_state.side_effect = store.get
    tracker.set_scan_state.side_effect = store.__setitem__
    return store


def _open_problems(store):
    import json
    return set(json.loads(store.get("attention", "{}")).get("open", {}))


def test_update_now_rejected_key_is_recorded(update_patched):
    from resources.lib.utils import ApiKeyError
    store = _scan_state_store(update_patched["tracker"])
    update_patched["run_scan"].side_effect = ApiKeyError("tmdb")
    actions.update_now("movie", "7")
    assert "key_rejected" in _open_problems(store)
    assert _fake_lang(32341) in [c.args[0] for c in update_patched["notify"].call_args_list]


def test_update_now_working_key_clears_the_problem(update_patched):
    store = _scan_state_store(update_patched["tracker"])
    store["attention"] = '{"open": {"key_rejected": ""}}'
    update_patched["run_scan"].return_value = {"updated": 0, "tmdb_answered": False}
    actions.update_now("movie", "7")
    assert "key_rejected" in _open_problems(store)  # TMDB was never asked
    update_patched["run_scan"].return_value = {"updated": 1, "tmdb_answered": True}
    actions.update_now("movie", "7")
    assert "key_rejected" not in _open_problems(store)


# ---------------------------------------------------------------------------
# update_now on a movie set
# ---------------------------------------------------------------------------


def _set_plan(reason=None, title="Heat Collection"):
    from resources.lib.engine.sets import SetPlan
    return SetPlan(setid=40, title=title, reason=reason, collection_id="1001",
                   file=_AIR, premiered="2023-04-05", member_files=(_AIR,),
                   managed_setid=None)


@pytest.fixture
def set_patched(update_patched, monkeypatch):
    describe = MagicMock(return_value=_set_plan())
    monkeypatch.setattr(actions, "describe_set", describe)
    monkeypatch.setattr(actions, "load_libraries",
                        MagicMock(return_value=LibraryConfig([_MOVIES])))
    monkeypatch.setattr(actions, "_get_sources_config_path", MagicMock(return_value="s.json"))
    monkeypatch.setattr(actions, "lang", lambda i: _LANG.get(i, "s{}".format(i)))
    update_patched["describe"] = describe
    return update_patched


def test_update_now_on_set_runs_set_pass_with_reset(set_patched):
    from resources.lib.data.media_types import SET
    actions.update_now("set", "40")
    set_patched["run_scan"].assert_called_once()
    args, kwargs = set_patched["run_scan"].call_args
    assert kwargs["media_type"] is SET
    assert kwargs["reset"] is True
    assert kwargs["only_set"] == 40
    assert kwargs.get("items") is None
    # Every library votes on which library the set follows.
    assert kwargs["libraries"] == LibraryConfig([_MOVIES])
    set_patched["get_item_by_id"].assert_not_called()
    assert [c.args[0] for c in set_patched["notify"].call_args_list] == [
        "Updated: Heat Collection"]
    set_patched["release"].assert_called_once_with()


@pytest.mark.parametrize("stats", [{"updated": 0, "moved": 2}, {"updated": 0, "joined": 1}])
def test_update_now_on_set_counts_moved_and_joined_movies_as_updated(set_patched, stats):
    set_patched["run_scan"].return_value = stats
    actions.update_now("set", "40")
    assert set_patched["notify"].call_args[0][0] == "Updated: Heat Collection"


@pytest.mark.parametrize("reason", ["tie_library", "no_members", "off"])
def test_update_now_on_left_alone_set_notifies(set_patched, reason):
    set_patched["describe"].return_value = _set_plan(reason)
    actions.update_now("set", "40")
    set_patched["run_scan"].assert_not_called()
    assert [c.args[0] for c in set_patched["notify"].call_args_list] == ["s32774"]
    set_patched["release"].assert_called_once_with()


def test_update_now_on_set_left_alone_after_asking_tmdb_says_so(set_patched):
    """A reason only TMDB's answers can give is known after the pass."""
    set_patched["describe"].side_effect = [_set_plan(), _set_plan("no_majority")]
    set_patched["run_scan"].return_value = {"updated": 0}
    actions.update_now("set", "40")
    set_patched["run_scan"].assert_called_once()
    assert [c.args[0] for c in set_patched["notify"].call_args_list] == ["s32774"]


def test_update_now_on_set_with_tmdb_down_says_could_not_check(set_patched):
    """A member's collection TMDB did not answer is not a reason to leave
    the set alone: the pass counts it as transient."""
    set_patched["describe"].side_effect = [_set_plan(), _set_plan("no_collection")]
    set_patched["run_scan"].return_value = {"updated": 0, "transient": 1}
    actions.update_now("set", "40")
    assert [c.args[0] for c in set_patched["notify"].call_args_list] == [
        "Could not check now: Heat Collection"]


def test_update_now_on_a_duplicate_set_runs_the_merge(set_patched):
    set_patched["describe"].return_value = _set_plan("duplicate", "Heat Collectie")
    set_patched["run_scan"].return_value = {"updated": 0}
    actions.update_now("set", "40")
    set_patched["run_scan"].assert_called_once()
    assert set_patched["notify"].call_args[0][0] == "No changes: Heat Collectie"


def test_update_now_on_a_missing_set(set_patched):
    set_patched["describe"].return_value = None
    actions.update_now("set", "40")
    set_patched["run_scan"].assert_not_called()
    assert [c.args[0] for c in set_patched["notify"].call_args_list] == ["Item not found"]


def test_reset_confirmation_says_locks_and_originals_stay():
    po = (Path(__file__).resolve().parent.parent
          / "resources/language/resource.language.en_gb/strings.po").read_text()
    text = re.search(r'msgctxt "#32361"\nmsgid "(.*)"', po).group(1)
    assert "lock" in text.lower()
    assert "original" in text.lower()


def _dbtypes(visible):
    return set(re.findall(r"String\.IsEqual\(ListItem\.DBType,(\w+)\)", visible))


def test_addon_context_menu_is_one_metamender_submenu():
    root_dir = Path(__file__).resolve().parent.parent
    root = ET.parse(str(root_dir / "addon.xml")).getroot()
    main = root.find("./extension[@point='kodi.context.item']/menu[@id='kodi.core.main']")
    assert main.findall("item") == []
    (submenu,) = main.findall("menu")
    po = (root_dir / "resources/language/resource.language.en_gb/strings.po").read_text()
    strings = dict(re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', po))
    assert strings[submenu.findtext("label")] == "MetaMender"
    # Kodi's schema allows no condition on a submenu; Kodi shows it while any
    # of its items is visible (ContextMenuManager::IsVisible).
    assert submenu.find("visible") is None

    items = {i.get("library"): i for i in submenu.findall("item")}
    assert set(items) == {"context.py", "context_details.py", "context_stop.py"}
    stop = items["context_stop.py"]
    assert strings[stop.findtext("label")] == "Stop scan"
    # Only while a scan holds the lock, and only on a video library item.
    assert "Window(Home).Property(metamender_scan_lock)" in stop.findtext("visible")
    assert "ListItem.DBType" in stop.findtext("visible")
    update, details = items["context.py"], items["context_details.py"]
    assert strings[update.findtext("label")] == "Update now"
    every = {"movie", "episode", "tvshow", "season", "set"}
    assert _dbtypes(update.findtext("visible")) == every
    assert strings[details.findtext("label")] == "Details"
    assert _dbtypes(details.findtext("visible")) == every
    for library in items:
        assert (root_dir / library).is_file()

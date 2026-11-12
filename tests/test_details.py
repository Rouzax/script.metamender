"""Tests for restoring an original value and unlocking a field."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from resources.lib.data.media_types import MOVIE, TVSHOW
from resources.lib.data.tracker import REASON_LOCKED, TrackerDB
from resources.lib.engine.protect import fingerprint
from resources.lib.service import details

IDENT = "Heat (1995)"


@pytest.fixture
def db(tmp_path):
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("movie", IDENT, "Heat")
    yield tracker
    tracker.close()


def _setup(db, field, original, written, *, has_original=True):
    """An item whose `field` MetaMender replaced `original` with `written`."""
    if has_original:
        db.keep_original("movie", IDENT, field, original)
    db.set_written_fp("movie", IDENT, field, fingerprint(written))
    db.commit()


def _run(db, field, current, *, write=True):
    with patch.object(details.kodi, "get_item_by_id", return_value={field: current}), \
            patch.object(details.kodi, "update_item", return_value=write) as upd:
        result = details.restore_field(db, MOVIE, 7, IDENT, field)
    return result, upd


def test_restore_writes_the_original_and_locks(db):
    _setup(db, "plot", "old plot", "new plot")
    result, upd = _run(db, "plot", "new plot")
    assert result == "restored"
    upd.assert_called_once()
    assert upd.call_args.args[:2] == (7, MOVIE)
    assert upd.call_args.kwargs["plot"] == "old plot"
    keep = db.get_keep("movie", IDENT)["plot"]
    assert keep["locked"] is True
    assert db.get_field_status_map("movie", IDENT)["plot"]["last_reason"] == REASON_LOCKED


def test_restore_refused_when_the_value_changed(db):
    _setup(db, "plot", "old plot", "new plot")
    result, upd = _run(db, "plot", "edited by the user")
    assert result == "changed"
    upd.assert_not_called()
    assert db.get_keep("movie", IDENT)["plot"]["locked"] is False


def test_restore_list_field_as_list_and_empty_as_empty(db):
    _setup(db, "genre", ["Drama", "Crime"], ["Action"])
    _setup(db, "tagline", "", "A tagline")
    result, upd = _run(db, "genre", ["Action"])
    assert result == "restored"
    assert upd.call_args.kwargs["genre"] == ["Drama", "Crime"]
    result, upd = _run(db, "tagline", "A tagline")
    assert result == "restored"
    assert upd.call_args.kwargs["tagline"] == ""


def test_ratings_are_never_restorable(db):
    for field in ("rating", "votes", "ratings"):
        _setup(db, field, 5.0, 7.0)
        keep = db.get_keep("movie", IDENT)
        assert details.can_restore(field, 7.0, {}, keep) is False
        result, upd = _run(db, field, 7.0)
        assert result == "changed"
        upd.assert_not_called()


def test_failed_write_does_not_lock(db):
    _setup(db, "plot", "old plot", "new plot")
    result, _ = _run(db, "plot", "new plot", write=False)
    assert result == "failed"
    assert db.get_keep("movie", IDENT)["plot"]["locked"] is False


def test_restore_after_reset_uses_written_value(db):
    db.keep_original("movie", IDENT, "plot", "old plot")
    db.commit()
    keep = db.get_keep("movie", IDENT)
    fs = {"written_value": fingerprint("new plot")}
    assert details.can_restore("plot", "new plot", fs, keep) is True
    assert details.can_restore("plot", "other", fs, keep) is False
    assert details.can_restore("plot", "new plot", {}, keep) is False


def test_no_original_is_not_restorable(db):
    _setup(db, "plot", None, "new plot", has_original=False)
    keep = db.get_keep("movie", IDENT)
    assert details.can_restore("plot", "new plot", {}, keep) is False


def test_restore_commits_the_lock_before_writing_kodi(db, tmp_path):
    """A scan that starts after the Kodi write must already see the lock."""
    _setup(db, "plot", "old plot", "new plot")
    seen = []

    def kodi_write(*args, **kwargs):
        other = TrackerDB(str(tmp_path / "backfill.db"))
        seen.append(other.get_keep("movie", IDENT)["plot"]["locked"])
        other.close()
        return True

    with patch.object(details.kodi, "get_item_by_id", return_value={"plot": "new plot"}), \
            patch.object(details.kodi, "update_item", side_effect=kodi_write):
        assert details.restore_field(db, MOVIE, 7, IDENT, "plot") == "restored"
    assert seen == [True]


def test_restore_lock_not_saved_writes_nothing(db):
    _setup(db, "plot", "old plot", "new plot")
    with patch.object(db, "set_locked", return_value=False):
        result, upd = _run(db, "plot", "new plot")
    assert result == details.LOCK_FAILED
    upd.assert_not_called()


def test_restore_lock_commit_failure_writes_nothing(db):
    _setup(db, "plot", "old plot", "new plot")
    with patch.object(db, "commit", side_effect=sqlite3.OperationalError("database is locked")):
        result, upd = _run(db, "plot", "new plot")
    assert result == details.LOCK_FAILED
    upd.assert_not_called()


def test_failed_write_keeps_an_earlier_lock(db):
    _setup(db, "plot", "old plot", "new plot")
    db.set_locked("movie", IDENT, "plot", True)
    db.commit()
    result, _ = _run(db, "plot", "new plot", write=False)
    assert result == "failed"
    assert db.get_keep("movie", IDENT)["plot"]["locked"] is True


def test_restore_show_title_moves_its_episode_rows(tmp_path):
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("tvshow", "tmdb:1", "Kwak")
    tracker.keep_original("tvshow", "tmdb:1", "title", "Alfred J. Kwak")
    tracker.set_written_fp("tvshow", "tmdb:1", "title", fingerprint("Alfred Jodocus Kwak"))
    tracker.upsert_item("episode", "Alfred Jodocus Kwak S01E01", kodi_id=3)
    tracker.set_locked("episode", "Alfred Jodocus Kwak S01E01", "plot", True)
    tracker.commit()
    with patch.object(details.kodi, "get_item_by_id",
                      return_value={"title": "Alfred Jodocus Kwak"}), \
            patch.object(details.kodi, "update_item", return_value=True):
        assert details.restore_field(tracker, TVSHOW, 5, "tmdb:1", "title") == "restored"
    assert tracker.get_item("episode", "Alfred Jodocus Kwak S01E01") is None
    assert tracker.get_item("episode", "Alfred J. Kwak S01E01") is not None
    assert tracker.get_keep("episode", "Alfred J. Kwak S01E01")["plot"]["locked"] is True
    tracker.close()


def test_restore_refused_show_title_keeps_the_episode_rows(tmp_path):
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("tvshow", "tmdb:1", "Kwak")
    tracker.keep_original("tvshow", "tmdb:1", "title", "Alfred J. Kwak")
    tracker.set_written_fp("tvshow", "tmdb:1", "title", fingerprint("Alfred Jodocus Kwak"))
    tracker.upsert_item("episode", "Alfred Jodocus Kwak S01E01", kodi_id=3)
    tracker.commit()
    with patch.object(details.kodi, "get_item_by_id",
                      return_value={"title": "Alfred Jodocus Kwak"}), \
            patch.object(details.kodi, "update_item", return_value=False):
        assert details.restore_field(tracker, TVSHOW, 5, "tmdb:1", "title") == "failed"
    assert tracker.get_item("episode", "Alfred Jodocus Kwak S01E01") is not None
    tracker.close()


def test_unlock(db):
    db.set_locked("movie", IDENT, "plot", True)
    db.commit()
    assert details.unlock_field(db, MOVIE, IDENT, "plot") is True
    assert db.get_keep("movie", IDENT)["plot"]["locked"] is False


def test_unlock_failure_is_reported_not_logged_as_done(db):
    db.set_locked("movie", IDENT, "plot", True)
    db.commit()
    with patch.object(db, "set_locked", return_value=False), \
            patch.object(details, "log") as log:
        assert details.unlock_field(db, MOVIE, IDENT, "plot") is False
    assert "details.unlocked" not in [c.kwargs.get("event") for c in log.info.call_args_list]
    assert db.get_keep("movie", IDENT)["plot"]["locked"] is True


# ---------------------------------------------------------------------------
# open_details and the window's session
# ---------------------------------------------------------------------------

_PO = (Path(__file__).resolve().parent.parent
       / "resources/language/resource.language.en_gb/strings.po").read_text()
_PO_STRINGS = {int(k): v for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', _PO)}

_LANG = {
    32342: "Cannot update this item type",
    32343: "Invalid item",
    32344: "A scan is already running",
    32345: "Item not found",
    32585: "{} · Season {}, episode {}",
    32590: "The value changed since this window opened, so nothing was restored.",
    32591: "Kodi did not accept the restore.",
    32592: "Could not read this item's details.",
    32595: _PO_STRINGS[32595],
    32596: _PO_STRINGS[32596],
}

MOVIE_ITEM = {
    "movieid": 7, "title": "Heat", "file": "/movies/Heat.mkv",
    "uniqueid": {"tmdb": "949"}, "plot": "new plot", "genre": ["Crime"],
}
EPISODE_ITEM = {
    "episodeid": 12, "title": "Amsterdam Centraal", "showtitle": "Het gouden uur",
    "season": 1, "episode": 1, "file": "/series/Het gouden uur/s01e01.mkv",
    "plot": "Agenten", "uniqueid": {},
}
SHOW_ITEM = {
    "tvshowid": 5, "title": "Het gouden uur", "file": "/series/Het gouden uur/",
    "uniqueid": {"tmdb": "250000"}, "plot": "Show plot",
}


@pytest.fixture
def opened(tmp_path, monkeypatch):
    """open_details with a real tracker, fake Kodi, settings and window."""
    db_path = str(tmp_path / "backfill.db")
    items = {7: MOVIE_ITEM, 12: EPISODE_ITEM, 5: SHOW_ITEM}
    shown = []
    notes = []
    calls = {"update_now": [], "acquire": 0, "release": 0}

    def get_item(media_type, kodi_id):
        item = items.get(kodi_id)
        return dict(item) if item else None

    def acquire():
        calls["acquire"] += 1
        return calls.get("lock_free", True)

    def release():
        calls["release"] += 1

    monkeypatch.setattr(details, "lang", lambda i: _LANG.get(i, ""))
    from resources.lib.ui import details_model
    monkeypatch.setattr(details_model, "lang", lambda i: _PO_STRINGS.get(i, ""))
    monkeypatch.setattr(details, "notify", notes.append)
    monkeypatch.setattr(details.kodi, "get_item_by_id", get_item)
    monkeypatch.setattr(details, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(details, "_get_sources_config_path", lambda: str(tmp_path / "s.json"))
    monkeypatch.setattr(details, "load_source_config", lambda path: {
        "sources": [{"path": "/series/", "language": "nl-NL"}],
    })
    monkeypatch.setattr(details, "_load_settings", lambda: {
        "enabled_fields": frozenset({"plot", "title", "main_title", "genre", "mpaa"}),
        "stale_threshold_days": 365,
    })
    monkeypatch.setattr(details, "language_name", lambda code: {"nl-NL": "Dutch"}.get(code, code))
    monkeypatch.setattr(details, "kodi_ui_language", lambda: "en-US")
    monkeypatch.setattr(details, "_init_logging", lambda: None)
    monkeypatch.setattr(details, "acquire_scan_lock", acquire)
    monkeypatch.setattr(details, "release_scan_lock", release)
    monkeypatch.setattr(details.actions, "update_now",
                        lambda dbtype, dbid: calls["update_now"].append((dbtype, dbid)))
    from resources.lib.ui import details_window
    monkeypatch.setattr(details_window, "show_details_window",
                        lambda session, view: shown.append((session, view)))
    return {"db_path": db_path, "shown": shown, "notes": notes, "calls": calls}


def _keys(view):
    return [r.key for r in view.rows]


def test_open_details_movie_shows_its_rows_and_writes_nothing(opened):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.keep_original("movie", "tmdb:949", "plot", "old plot")
    tracker.set_written_fp("movie", "tmdb:949", "plot", fingerprint("new plot"))
    tracker.commit()
    before = tracker._conn.execute("SELECT COUNT(*) FROM field_status").fetchone()[0]
    tracker.close()

    details.open_details("movie", "7")

    session, view = opened["shown"][0]
    assert view.heading == "Heat"
    assert view.sub.startswith("No folder language")
    assert _keys(view)[0] == "ids"
    assert set(_keys(view)) == {"ids", "plot", "title", "genre", "mpaa"}
    assert next(r for r in view.rows if r.key == "plot").action == "restore"
    assert session.identity == "tmdb:949"
    assert opened["notes"] == []
    tracker = TrackerDB(opened["db_path"])
    assert tracker._conn.execute("SELECT COUNT(*) FROM field_status").fetchone()[0] == before
    tracker.close()


def test_open_details_episode_heading_and_folder_language(opened):
    details.open_details("episode", "12")
    session, view = opened["shown"][0]
    assert view.heading == "Het gouden uur · Season 1, episode 1"
    assert view.sub.startswith("Dutch folder")
    # Episodes have no certification; an episode title is managed as "title".
    assert set(_keys(view)) == {"ids", "plot", "title"}
    session.update_now()
    assert opened["calls"]["update_now"] == [("episode", "12")]


def test_open_details_tvshow_update_now_opens_the_show_refresh(opened):
    details.open_details("tvshow", "5")
    session, view = opened["shown"][0]
    assert view.heading == "Het gouden uur"
    assert "mpaa" in _keys(view)
    session.update_now()
    assert opened["calls"]["update_now"] == [("tvshow", "5")]


def test_open_details_unsupported_type_says_so(opened):
    details.open_details("season", "3")
    assert opened["shown"] == []
    assert opened["notes"] == ["Cannot update this item type"]


def test_open_details_invalid_or_missing_item(opened):
    details.open_details("movie", "abc")
    details.open_details("movie", "99")
    assert opened["shown"] == []
    assert opened["notes"] == ["Invalid item", "Item not found"]


def test_duplicate_item_reads_its_own_row(opened, monkeypatch):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    own = "tmdb:949|/movies/Heat (copy).mkv"
    tracker.upsert_item("movie", own, "Heat", kodi_id=8)
    tracker.commit()
    tracker.close()
    copy = dict(MOVIE_ITEM, movieid=8, file="/movies/Heat (copy).mkv")
    monkeypatch.setattr(details.kodi, "get_item_by_id",
                        lambda mt, i: dict(copy) if i == 8 else dict(MOVIE_ITEM))
    details.open_details("movie", "8")
    session, _ = opened["shown"][0]
    assert session.identity == own


def _restorable(opened):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.keep_original("movie", "tmdb:949", "plot", "old plot")
    tracker.set_written_fp("movie", "tmdb:949", "plot", fingerprint("new plot"))
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    return session, next(r for r in view.rows if r.key == "plot")


def test_session_restore_takes_the_scan_lock(opened):
    session, row = _restorable(opened)
    with patch.object(details.kodi, "update_item", return_value=True) as upd:
        session.run_action(row)
    assert upd.call_args.kwargs["plot"] == "old plot"
    assert opened["calls"]["acquire"] == 1 and opened["calls"]["release"] == 1
    tracker = TrackerDB(opened["db_path"])
    assert tracker.get_keep("movie", "tmdb:949")["plot"]["locked"] is True
    tracker.close()
    assert opened["notes"] == []


def test_session_restore_changed_value_tells_the_user(opened):
    session, row = _restorable(opened)
    edited = dict(MOVIE_ITEM, plot="edited")
    with patch.object(details.kodi, "get_item_by_id", return_value=edited), \
            patch.object(details.kodi, "update_item") as upd:
        session.run_action(row)
    upd.assert_not_called()
    assert opened["notes"] == [_LANG[32590]]


def test_session_restore_while_a_scan_runs_writes_nothing(opened):
    session, row = _restorable(opened)
    opened["calls"]["lock_free"] = False
    with patch.object(details.kodi, "update_item") as upd:
        session.run_action(row)
    upd.assert_not_called()
    assert opened["notes"] == ["A scan is already running"]
    assert opened["calls"]["release"] == 0


def test_session_unlock(opened):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.set_locked("movie", "tmdb:949", "genre", True)
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    row = next(r for r in view.rows if r.key == "genre")
    assert row.action == "unlock"
    session.run_action(row)
    view = session.load()
    assert next(r for r in view.rows if r.key == "genre").action == ""


@pytest.mark.parametrize("failure", ["refused", "raised"])
def test_session_unlock_failure_tells_the_user(opened, failure):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.set_locked("movie", "tmdb:949", "genre", True)
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    row = next(r for r in view.rows if r.key == "genre")
    patched = ({"return_value": False} if failure == "refused"
               else {"side_effect": sqlite3.OperationalError("database is locked")})
    with patch.object(TrackerDB, "set_locked", **patched):
        session.run_action(row)
    assert opened["notes"] == [_PO_STRINGS[32596]]
    view = session.load()
    assert next(r for r in view.rows if r.key == "genre").action == "unlock"


def test_session_restore_lock_failure_tells_the_user(opened):
    session, row = _restorable(opened)
    with patch.object(TrackerDB, "set_locked", return_value=False), \
            patch.object(details.kodi, "update_item") as upd:
        session.run_action(row)
    upd.assert_not_called()
    assert opened["notes"] == [_PO_STRINGS[32595]]


def test_open_details_reads_the_row_a_scan_would_adopt(opened):
    # MetaMender wrote back the tmdb id, so the item now computes tmdb:949
    # while its row is still keyed on its title.
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "Heat", "Heat", kodi_id=7, tmdb_id="949")
    tracker.keep_original("movie", "Heat", "plot", "old plot")
    tracker.set_written_fp("movie", "Heat", "plot", fingerprint("new plot"))
    tracker.commit()
    tracker.close()

    details.open_details("movie", "7")

    session, view = opened["shown"][0]
    assert session.identity == "Heat"
    assert next(r for r in view.rows if r.key == "plot").action == "restore"
    tracker = TrackerDB(opened["db_path"])
    assert tracker.get_item("movie", "tmdb:949") is None
    assert tracker.get_item("movie", "Heat") is not None
    tracker.close()


def test_open_details_read_error_logs_and_tells_the_user(opened, monkeypatch):
    def broken(media_type, kodi_id):
        raise RuntimeError("json-rpc down")

    monkeypatch.setattr(details.kodi, "get_item_by_id", broken)
    details.open_details("movie", "7")
    assert opened["shown"] == []
    assert opened["notes"] == [_LANG[32592]]


def test_reload_error_returns_none_and_tells_the_user(opened, monkeypatch):
    details.open_details("movie", "7")
    session, _ = opened["shown"][0]
    monkeypatch.setattr(details, "TrackerDB", lambda path: (_ for _ in ()).throw(
        __import__("sqlite3").OperationalError("database is locked")))
    assert session.reload() is None
    assert opened["notes"] == [_LANG[32592]]


def test_reload_of_a_removed_item_says_not_found(opened, monkeypatch):
    details.open_details("movie", "7")
    session, _ = opened["shown"][0]
    monkeypatch.setattr(details.kodi, "get_item_by_id", lambda mt, i: None)
    assert session.reload() is None
    assert opened["notes"] == ["Item not found"]

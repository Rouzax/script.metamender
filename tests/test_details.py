"""Tests for restoring an original value and unlocking a field."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from resources.lib.data.libraries import Library, LibraryConfig
from resources.lib.data.media_types import MOVIE, SEASON, SET, TVSHOW
from resources.lib.data.tracker import REASON_LOCKED, TrackerDB
from resources.lib.engine.protect import fingerprint
from resources.lib.service import details
from resources.lib.service.actions import update_now as _REAL_UPDATE_NOW

IDENT = "Heat (1995)"


@pytest.fixture
def db(tmp_path):
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("movie", IDENT, "Heat")
    yield tracker
    tracker.close()


def _setup(db, field, original, written):
    """An item whose `field` MetaMender replaced `original` with `written`."""
    db.keep_original("movie", IDENT, field, original)
    db.set_written_fp("movie", IDENT, field, fingerprint(written))
    db.commit()


def _run(db, field, current, *, write=True):
    item = {field: current, "title": "Heat"}
    with patch.object(details.kodi, "get_item_by_id", return_value=item), \
            patch.object(details.kodi, "update_item", return_value=write) as upd:
        result = details.restore_field(db, MOVIE, 7, IDENT, field)
    return result, upd


def test_restore_writes_the_original_and_locks(db):
    _setup(db, "plot", "old plot", "new plot")
    result, upd = _run(db, "plot", "new plot")
    assert result == "restored"
    upd.assert_called_once()
    # The title Kodi holds now, as update_item names the item in its log.
    assert upd.call_args.args[:3] == (7, MOVIE, "Heat")
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
        assert result == details.NOT_RESTORABLE
        upd.assert_not_called()
        assert db.get_keep("movie", IDENT)[field]["locked"] is False


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


def test_no_restore_for_missing_local_original():
    """The kept original is a local file that no longer exists: putting the
    dead path back would only break the art again."""
    nl_poster = "https://image.tmdb.org/t/p/original/nl.jpg"
    fs = {"written_value": fingerprint(nl_poster)}
    local = {"poster": {"has_original": True, "original": "smb://h/p.jpg",
                        "written_fp": fingerprint(nl_poster)}}
    wrapped = {"poster": dict(local["poster"],
                              original="image://smb%3a%2f%2fh%2fp.jpg/")}
    web = {"poster": dict(local["poster"], original="https://example.org/p.jpg")}
    assert not details.can_restore("poster", nl_poster, fs, local)
    assert not details.can_restore("poster", nl_poster, fs, wrapped)
    assert details.can_restore("poster", nl_poster, fs, web)


def test_no_original_is_not_restorable(db):
    db.set_written_fp("movie", IDENT, "plot", fingerprint("new plot"))
    db.commit()
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
    32590: _PO_STRINGS[32590],
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
    opened_settings = {
        "toggles": frozenset({"plots", "episode_titles", "main_titles", "details",
                              "certifications"}),
        "cert_country": "NL",
    }

    def libraries(path):
        def lib(folder, language, label=""):
            return Library(path=folder, label=label, included=True, language=language,
                           fallback_language="", toggles=opened_settings["toggles"])
        return LibraryConfig([lib("/movies/", "", "Films"), lib("/series/", "nl-NL")])

    def get_item(media_type, kodi_id):
        item = items.get(kodi_id)
        return dict(item) if item else None

    def lookup_item(media_type, kodi_id, art=False):
        # Answers from whatever get_item_by_id a test sets up; an item it
        # does not have is gone.
        item = details.kodi.get_item_by_id(media_type, kodi_id)
        return (details.kodi.ITEM_FOUND, item) if item else (details.kodi.ITEM_GONE, None)

    def acquire():
        calls["acquire"] += 1
        return calls.get("lock_free", True)

    def release():
        calls["release"] += 1

    monkeypatch.setattr(details, "lang", lambda i: _LANG.get(i, _PO_STRINGS.get(i, "")))
    from resources.lib.ui import details_model
    monkeypatch.setattr(details_model, "lang", lambda i: _PO_STRINGS.get(i, ""))
    monkeypatch.setattr(details, "notify", notes.append)
    monkeypatch.setattr(details.kodi, "get_item_by_id", get_item)
    monkeypatch.setattr(details.kodi, "lookup_item", lookup_item)
    monkeypatch.setattr(details.kodi, "file_runtime",
                        lambda media_type, kodi_id: calls.get("file_runtime"))
    monkeypatch.setattr(details, "_get_db_path", lambda: db_path)
    monkeypatch.setattr(details, "_get_sources_config_path", lambda: str(tmp_path / "s.json"))
    monkeypatch.setattr(details, "load_libraries", libraries)
    monkeypatch.setattr(details, "resolve_country",
                        lambda lib, ui: opened_settings["cert_country"])
    monkeypatch.setattr(details, "_load_settings", lambda: {
        "stale_threshold_days": 365, "ui_language": "en-US",
    })
    monkeypatch.setattr(details, "language_name", lambda code: {"nl-NL": "Dutch"}.get(code, code))
    monkeypatch.setattr(details, "_init_logging", lambda: None)
    monkeypatch.setattr(details, "acquire_scan_lock", acquire)
    monkeypatch.setattr(details, "release_scan_lock", release)
    monkeypatch.setattr(details.actions, "update_now",
                        lambda dbtype, dbid: calls["update_now"].append((dbtype, dbid)))
    from resources.lib.ui import details_window
    monkeypatch.setattr(details_window, "show_details_window",
                        lambda session, view: shown.append((session, view)))
    return {"db_path": db_path, "shown": shown, "notes": notes, "calls": calls,
            "settings": opened_settings}


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
    assert set(_keys(view)) == {"ids", "plot", "title", "genre", "studio", "country",
                                "runtime", "mpaa"}
    assert next(r for r in view.rows if r.key == "plot").action == "restore"
    assert session.identity == "tmdb:949"
    assert opened["notes"] == []
    tracker = TrackerDB(opened["db_path"])
    assert tracker._conn.execute("SELECT COUNT(*) FROM field_status").fetchone()[0] == before
    tracker.close()


def test_open_details_runtime_is_the_file_length_kodi_shows(opened):
    opened["calls"]["file_runtime"] = 3424
    details.open_details("movie", "7")
    _session, view = opened["shown"][0]
    runtime = next(r for r in view.rows if r.key == "runtime")
    assert dict(runtime.lines)[""] == "57 min"
    assert runtime.group == "left"


def test_open_details_episode_heading_and_folder_language(opened):
    details.open_details("episode", "12")
    session, view = opened["shown"][0]
    assert view.heading == "Het gouden uur · Season 1, episode 1"
    assert view.sub.startswith("Dutch folder")
    # Episodes have no certification; an episode title is managed as "title".
    assert set(_keys(view)) == {"ids", "plot", "title", "runtime", "firstaired"}
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
    details.open_details("collection", "3")
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


def _second_entry(opened, monkeypatch, own_row_holder=None):
    """Kodi lists Heat's file twice, as movie 7 (which holds the row, with a
    restorable plot) and movie 8. With ``own_row_holder``, the plain row is
    another file's and 7 holds Heat's ``<identity>|<file>`` row."""
    identity = "tmdb:949"
    tracker = TrackerDB(opened["db_path"])
    if own_row_holder is not None:
        tracker.upsert_item("movie", identity, "Heat", kodi_id=own_row_holder)
        identity = "tmdb:949|/movies/Heat.mkv"
    tracker.upsert_item("movie", identity, "Heat", kodi_id=7)
    tracker.keep_original("movie", identity, "plot", "old plot")
    tracker.set_written_fp("movie", identity, "plot", fingerprint("new plot"))
    tracker.commit()
    tracker.close()
    entries = {7: MOVIE_ITEM, 8: dict(MOVIE_ITEM, movieid=8),
               40: dict(MOVIE_ITEM, movieid=40, file="/movies/Heat (copy).mkv")}
    monkeypatch.setattr(details.kodi, "get_item_by_id",
                        lambda mt, i: dict(entries[i]) if i in entries else None)
    return identity


@pytest.mark.parametrize("own_row_holder", [None, 40], ids=["plain_row", "file_row"])
def test_details_on_the_second_entry_of_a_file_is_read_only(
        opened, monkeypatch, own_row_holder):
    _second_entry(opened, monkeypatch, own_row_holder)

    details.open_details("movie", "8")

    session, view = opened["shown"][0]
    assert [r.key for r in view.rows if r.action] == []
    assert ("", _PO_STRINGS[32789]) in view.rows[0].lines
    with patch.object(details.kodi, "update_item", return_value=True) as upd:
        for row in view.rows:
            session.run_action(row)
    upd.assert_not_called()
    assert opened["calls"]["acquire"] == 0


@pytest.mark.parametrize("own_row_holder", [None, 40], ids=["plain_row", "file_row"])
def test_details_on_the_entry_that_holds_the_row_is_unchanged(
        opened, monkeypatch, own_row_holder):
    identity = _second_entry(opened, monkeypatch, own_row_holder)

    details.open_details("movie", "7")

    session, view = opened["shown"][0]
    assert session.identity == identity
    assert next(r for r in view.rows if r.key == "plot").action == "restore"
    assert ("", _PO_STRINGS[32789]) not in view.rows[0].lines


def test_details_whose_row_holder_is_gone_reads_the_row_without_a_warning(
        opened, monkeypatch):
    """Kodi re-added the movie: the row's recorded entry is gone, an answer
    Details expects, so it is not asked through the call that warns."""
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=6)
    tracker.commit()
    tracker.close()
    asked = []
    real = details.kodi.get_item_by_id
    monkeypatch.setattr(details.kodi, "get_item_by_id",
                        lambda mt, i: asked.append(i) or real(mt, i))
    monkeypatch.setattr(details.kodi, "lookup_item",
                        lambda mt, i, art=False: ((details.kodi.ITEM_GONE, None) if i == 6
                                                  else (details.kodi.ITEM_FOUND, real(mt, i))))

    details.open_details("movie", "7")

    session, view = opened["shown"][0]
    assert session.identity == "tmdb:949"
    assert 6 not in asked
    assert ("", _PO_STRINGS[32789]) not in view.rows[0].lines


def test_details_when_kodi_does_not_answer_about_the_holder_shows_the_row(
        opened, monkeypatch):
    _second_entry(opened, monkeypatch)
    monkeypatch.setattr(details.kodi, "lookup_item",
                        lambda mt, i, art=False: (details.kodi.ITEM_FAILED, None))

    details.open_details("movie", "8")

    session, view = opened["shown"][0]
    assert session.identity == "tmdb:949"
    assert ("", _PO_STRINGS[32789]) not in view.rows[0].lines


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
    assert next(r for r in view.rows if r.key == "genre").action == "keep"


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


def test_restore_show_title_tells_when_the_episodes_could_not_follow(tmp_path):
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("tvshow", "tmdb:1", "Kwak")
    tracker.keep_original("tvshow", "tmdb:1", "title", "Alfred J. Kwak")
    tracker.set_written_fp("tvshow", "tmdb:1", "title", fingerprint("Alfred Jodocus Kwak"))
    tracker.commit()
    with patch.object(details.kodi, "get_item_by_id",
                      return_value={"title": "Alfred Jodocus Kwak"}), \
            patch.object(details.kodi, "update_item", return_value=True), \
            patch.object(tracker, "rename_show_episodes", return_value=None), \
            patch.object(details, "log") as log:
        result = details.restore_field(tracker, TVSHOW, 5, "tmdb:1", "title")
    assert result == details.EPISODES_NOT_MOVED
    events = [c.kwargs.get("event") for c in log.warning.call_args_list]
    assert "details.rekey_failed" in events
    # The restore itself stands: Kodi holds the original under a lock.
    assert tracker.get_keep("tvshow", "tmdb:1")["title"]["locked"] is True
    tracker.close()


def _locked_genre(opened):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.set_locked("movie", "tmdb:949", "genre", True)
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    return session, next(r for r in view.rows if r.key == "genre")


def test_session_restore_episode_rekey_failure_tells_the_user(opened, monkeypatch):
    session, row = _restorable(opened)
    monkeypatch.setattr(details, "restore_field",
                        lambda *a, **k: details.EPISODES_NOT_MOVED)
    session.run_action(row)
    assert opened["notes"] == [_PO_STRINGS[32605]]


def test_session_restore_of_a_rating_is_a_plain_no(opened, monkeypatch):
    session, row = _restorable(opened)
    monkeypatch.setattr(details, "restore_field",
                        lambda *a, **k: details.NOT_RESTORABLE)
    session.run_action(row)
    assert opened["notes"] == [_PO_STRINGS[32606]]


def test_session_unlock_while_a_scan_runs_says_so_without_waiting(opened):
    session, row = _locked_genre(opened)
    opened["calls"]["lock_free"] = False
    with patch.object(details, "unlock_field") as unlock:
        session.run_action(row)
    unlock.assert_not_called()
    assert opened["notes"] == ["A scan is already running"]
    assert opened["calls"]["release"] == 0


def test_session_unlock_holds_the_scan_lock(opened):
    session, row = _locked_genre(opened)
    session.run_action(row)
    assert opened["calls"]["acquire"] == 1 and opened["calls"]["release"] == 1
    assert opened["notes"] == []


def test_certification_listed_only_with_a_usable_country_preset(opened):
    opened["settings"]["cert_country"] = None
    details.open_details("movie", "7")
    _session, view = opened["shown"][0]
    assert "mpaa" not in _keys(view)
    opened["settings"]["cert_country"] = "XX"
    details.open_details("movie", "7")
    _session, view = opened["shown"][1]
    assert "mpaa" not in _keys(view)


def test_adoptable_title_keyed_row_lists_the_title_the_scan_manages(opened):
    """The scan adopts the row under tmdb:949 first, and then manages the
    title, which a title-keyed identity would not."""
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "Heat (1995)", "Heat", kodi_id=7, tmdb_id="949")
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    assert session.identity == "Heat (1995)"
    assert "title" in _keys(view)


def test_locked_field_of_a_switched_off_group_is_listed_without_a_next_scan(opened):
    opened["settings"]["toggles"] = frozenset({"plots"})
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    # Locked while the group was on: the scans then recorded the lock.
    tracker.set_locked("movie", "tmdb:949", "genre", True)
    tracker.record_field_reason("movie", "tmdb:949", "genre", "locked")
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    assert set(_keys(view)) == {"ids", "plot", "genre"}
    row = next(r for r in view.rows if r.key == "genre")
    assert row.action == "unlock"
    session.run_action(row)
    row = next(r for r in session.load().rows if r.key == "genre")
    assert row.action == ""
    assert row.hint == _PO_STRINGS[32602]


def test_stale_identity_after_an_adoption_is_resolved_on_reload(opened):
    """A scan adopted the row under a new identity while the window was
    open: the press finds nothing under the old one and says so neutrally,
    and the reload that follows finds the row, so the next press works."""
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "Heat", "Heat", kodi_id=7, tmdb_id="949")
    tracker.keep_original("movie", "Heat", "plot", "old plot")
    tracker.set_written_fp("movie", "Heat", "plot", fingerprint("new plot"))
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    row = next(r for r in view.rows if r.key == "plot")
    tracker = TrackerDB(opened["db_path"])
    assert tracker.adopt_identity("movie", "tmdb:949", 7) == "Heat"
    tracker.close()
    with patch.object(details.kodi, "update_item", return_value=True) as upd:
        session.run_action(row)
        upd.assert_not_called()
        assert opened["notes"] == [_PO_STRINGS[32590]]
        assert "changed" not in _PO_STRINGS[32590].split(":")[0]
        view = session.reload()
        assert session.identity == "tmdb:949"
        session.run_action(next(r for r in view.rows if r.key == "plot"))
    assert upd.call_args.kwargs["plot"] == "old plot"


def test_refilled_placeholder_is_not_restorable(db):
    _setup(db, "title", "Episode 5", "Episode 5")
    keep = db.get_keep("movie", IDENT)
    assert details.can_restore("title", "Episode 5", {}, keep) is False


def test_restore_rekey_failure_survives_a_failing_commit(tmp_path):
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("tvshow", "tmdb:1", "Kwak")
    tracker.keep_original("tvshow", "tmdb:1", "title", "Alfred J. Kwak")
    tracker.set_written_fp("tvshow", "tmdb:1", "title", fingerprint("Alfred Jodocus Kwak"))
    tracker.commit()
    with patch.object(details.kodi, "get_item_by_id",
                      return_value={"title": "Alfred Jodocus Kwak"}), \
            patch.object(details.kodi, "update_item", return_value=True), \
            patch.object(tracker, "rename_show_episodes", return_value=None):
        real_commit = tracker.commit
        calls = []

        def commit():
            calls.append(1)
            if len(calls) > 1:  # the lock's own commit succeeds
                raise sqlite3.OperationalError("database is locked")
            real_commit()

        with patch.object(tracker, "commit", side_effect=commit), \
                patch.object(tracker, "rollback",
                             side_effect=sqlite3.OperationalError("disk I/O error")):
            result = details.restore_field(tracker, TVSHOW, 5, "tmdb:1", "title")
    assert result == details.EPISODES_NOT_MOVED
    tracker.close()


def test_unknown_cert_country_logs_no_error_on_open(opened):
    from resources.lib.data import presets

    opened["settings"]["cert_country"] = "XX"
    with patch.object(presets, "log") as log:
        details.open_details("movie", "7")
    assert not log.error.called and not log.exception.called
    _session, view = opened["shown"][0]
    assert "mpaa" not in _keys(view)


def test_keep_locks_the_field_and_records_why(db):
    assert details.keep_field(db, MOVIE, IDENT, "genre") == details.KEPT
    assert db.get_keep("movie", IDENT)["genre"]["locked"] is True
    assert db.get_field_status_map("movie", IDENT)["genre"]["last_reason"] == REASON_LOCKED


def test_keep_lock_failure_keeps_nothing(db):
    with patch.object(db, "set_locked", return_value=False):
        assert details.keep_field(db, MOVIE, IDENT, "genre") == details.LOCK_FAILED
    assert "genre" not in db.get_keep("movie", IDENT)


def test_ratings_cannot_be_kept(db):
    assert details.keep_field(db, MOVIE, IDENT, "rating") == details.NOT_RESTORABLE
    assert "rating" not in db.get_keep("movie", IDENT)


def test_session_keep_this_value(opened):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    row = next(r for r in view.rows if r.key == "genre")
    assert row.action == "keep"
    session.run_action(row)
    assert opened["notes"] == []
    assert opened["calls"]["release"] == 1
    genre = next(r for r in session.load().rows if r.key == "genre")
    assert genre.action == "unlock" and genre.group == "left"


def test_session_keep_failure_tells_the_user(opened):
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    session, view = opened["shown"][0]
    row = next(r for r in view.rows if r.key == "genre")
    with patch.object(TrackerDB, "set_locked", return_value=False):
        session.run_action(row)
    assert opened["notes"] == [_PO_STRINGS[32627]]


def test_details_of_an_item_in_no_library_lists_no_managed_fields(opened):
    MOVIE_ELSEWHERE = dict(MOVIE_ITEM, file="/elsewhere/Heat.mkv")
    with patch.object(details.kodi, "get_item_by_id", return_value=MOVIE_ELSEWHERE):
        details.open_details("movie", "7")
    _session, view = opened["shown"][0]
    assert _keys(view) == ["ids"]
    assert view.sub.startswith("No folder language")


def test_update_now_on_excluded_library_shows_message_and_changes_nothing(opened, monkeypatch):
    from resources.lib.service import actions, refresh

    details.open_details("movie", "7")
    session, _view = opened["shown"][0]
    run_scan, tracker_cls = MagicMock(), MagicMock()
    monkeypatch.setattr(actions, "update_now", _REAL_UPDATE_NOW)
    monkeypatch.setattr(actions, "_init_logging", lambda: None)
    monkeypatch.setattr(actions, "is_setup_complete", lambda: True)
    monkeypatch.setattr(actions, "acquire_scan_lock", lambda: True)
    monkeypatch.setattr(actions, "release_scan_lock", lambda: None)
    monkeypatch.setattr(actions, "get_item_by_id", lambda mt, i: dict(MOVIE_ITEM))
    monkeypatch.setattr(actions, "run_scan", run_scan)
    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions.StructuredLogger, "start_scan_log", lambda: None)
    monkeypatch.setattr(actions.StructuredLogger, "stop_scan_log", lambda: None)
    monkeypatch.setattr(refresh, "_get_sources_config_path", lambda: "s.json")
    monkeypatch.setattr(refresh, "load_libraries", lambda path: LibraryConfig([Library(
        path="/movies/", label="", included=False, language="en-US",
        fallback_language="", toggles=frozenset({"plots"}))]))
    monkeypatch.setattr(refresh, "notify", opened["notes"].append)
    monkeypatch.setattr(refresh, "lang", lambda i: _PO_STRINGS.get(i, ""))
    session.update_now()
    assert opened["notes"] == [_PO_STRINGS[32650]]
    run_scan.assert_not_called()
    tracker_cls.assert_not_called()


SEASON_ITEM = {
    "seasonid": 30, "tvshowid": 5, "season": 2, "showtitle": "Het gouden uur",
    "title": "Season 2", "poster": "",
}


def _open_season(opened, monkeypatch, *, show_tmdb="250000", show_tvdb=None):
    opened["settings"]["toggles"] = frozenset({"posters"})
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("tvshow", "tmdb:250000" if show_tmdb else "Het gouden uur", "Het gouden uur",
                        kodi_id=5, tmdb_id=show_tmdb, tvdb_id=show_tvdb)
    tracker.commit()
    tracker.close()
    real = details.kodi.get_item_by_id
    monkeypatch.setattr(details.kodi, "get_item_by_id",
                        lambda mt, i: dict(SEASON_ITEM) if mt is SEASON else real(mt, i))
    details.open_details("season", "30")


def test_details_opens_for_season(opened, monkeypatch):
    _open_season(opened, monkeypatch)
    session, view = opened["shown"][0]
    assert view.heading == "Het gouden uur · Season 2"
    assert _keys(view) == ["ids", "poster"]
    assert session.identity == "tmdb:250000/S02"
    ids = view.rows[0]
    assert ("TMDB", "250000") in [(n, t.split(",")[0]) for n, t in ids.lines]


def test_details_of_an_unscanned_season_shows_its_show_tvdb_id(opened, monkeypatch):
    _open_season(opened, monkeypatch, show_tvdb="81189")
    _session, view = opened["shown"][0]
    assert ("TheTVDB", "81189") in view.rows[0].lines


def test_details_of_a_season_of_a_show_without_tmdb_id_has_no_fields(opened, monkeypatch):
    _open_season(opened, monkeypatch, show_tmdb=None)
    _session, view = opened["shown"][0]
    assert _keys(view) == ["ids"]
    assert opened["notes"] == []


def test_unlock_poster_forgets_first_seen(db):
    db.set_first_fp("movie", IDENT, "poster", "fp1")
    db.set_locked("movie", IDENT, "poster", True)
    db.commit()
    assert details.unlock_field(db, MOVIE, IDENT, "poster") is True
    assert db.get_first_fp("movie", IDENT, "poster") is None


def test_restore_poster_writes_art(db):
    original = "image://video@smb%3a%2f%2fnas%2fm.mkv"
    _setup(db, "poster", original, "https://image.tmdb.org/t/p/original/a.jpg")
    result, upd = _run(db, "poster", "https://image.tmdb.org/t/p/original/a.jpg")
    assert result == "restored"
    assert upd.call_args.kwargs["poster"] == original


def test_duplicate_season_reads_its_own_row(opened, monkeypatch):
    """The season holding the shared row is told apart through its show's id."""
    opened["settings"]["toggles"] = frozenset({"posters"})
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("tvshow", "tmdb:250000", "Het gouden uur", kodi_id=5,
                        tmdb_id="250000")
    tracker.upsert_item("season", "tmdb:250000/S02", "Season 2", kodi_id=31)
    own = "tmdb:250000/S02|/series/Het gouden uur/"
    tracker.commit()
    tracker.close()
    real = details.kodi.get_item_by_id
    seasons = {30: SEASON_ITEM, 31: dict(SEASON_ITEM, seasonid=31)}
    monkeypatch.setattr(details.kodi, "get_item_by_id",
                        lambda mt, i: dict(seasons[i]) if mt is SEASON else real(mt, i))
    details.open_details("season", "30")
    session, _ = opened["shown"][0]
    assert session.identity == own


# ---------------------------------------------------------------------------
# Movie sets
# ---------------------------------------------------------------------------

SET_ITEM = {"setid": 40, "title": "Heat Collection", "plot": "Set plot", "poster": ""}
SETS = {40: SET_ITEM, 41: {"setid": 41, "title": "Favourites", "plot": "", "poster": ""}}
HEAT_MEMBER = {"movieid": 7, "title": "Heat", "set": "Heat Collection", "setid": 40,
               "file": "/movies/Heat.mkv", "uniqueid": {"tmdb": "949"},
               "premiered": "1995-12-15"}


def _with_sets(opened, monkeypatch, *, collection="1001", members=None, setid_of_heat=40):
    """Kodi with the sets above; Heat (movie 7) sits in set 40 (or the setid
    given) and TMDB's answer for it, when given, is stored."""
    opened["settings"]["toggles"] = frozenset({"movie_sets", "plots", "posters"})
    tracker = TrackerDB(opened["db_path"])
    if collection is not None:
        tracker.set_movie_collection("949", collection, "2026-11-01")
    tracker.commit()
    tracker.close()
    real = details.kodi.get_item_by_id
    monkeypatch.setattr(details.kodi, "get_item_by_id",
                        lambda mt, i: dict(SETS[i]) if mt is SET and i in SETS
                        else (None if mt is SET else real(mt, i)))
    monkeypatch.setattr(details.kodi, "get_all_items",
                        lambda mt, **kw: [dict(s) for s in SETS.values()])
    members = [dict(HEAT_MEMBER, setid=setid_of_heat)] if members is None else members
    monkeypatch.setattr(details.kodi, "get_movies_for_sets", lambda: [dict(m) for m in members])
    monkeypatch.setattr(details.kodi, "get_movie_setid", lambda movieid: setid_of_heat)


def _ids_texts(view):
    return [text for _, text in view.rows[0].lines]


def test_set_details_rows_name_plot_art(opened, monkeypatch):
    _with_sets(opened, monkeypatch)
    details.open_details("set", "40")
    session, view = opened["shown"][0]
    assert view.heading == "Heat Collection"
    assert set(_keys(view)) == {"ids", "title", "plot", "poster"}
    assert session.identity == "collection:1001"
    assert ("TMDB collection", "1001") in view.rows[0].lines
    assert opened["notes"] == []
    session.update_now()
    assert opened["calls"]["update_now"] == [("set", "40")]


def test_set_details_shows_reason_when_left_alone(opened, monkeypatch):
    _with_sets(opened, monkeypatch, collection="")
    details.open_details("set", "40")
    session, view = opened["shown"][0]
    assert _keys(view) == ["ids"]
    assert session.identity == ""
    assert "Left alone: TMDB has no collection for its movies" in _ids_texts(view)


def test_set_details_reason_names_the_library_without_the_tick(opened, monkeypatch):
    _with_sets(opened, monkeypatch)
    opened["settings"]["toggles"] = frozenset({"plots"})
    details.open_details("set", "40")
    _session, view = opened["shown"][0]
    assert _keys(view) == ["ids"]
    assert "Left alone: Movie sets is not ticked for Films" in _ids_texts(view)


def test_set_details_of_a_previously_managed_set_keeps_its_history(opened, monkeypatch):
    """A set left alone now still shows the fields it holds a lock on."""
    _with_sets(opened, monkeypatch)
    opened["settings"]["toggles"] = frozenset({"plots"})
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("set", "collection:1001", "Heat Collection", kodi_id=40,
                        tmdb_id="1001")
    tracker.set_locked("set", "collection:1001", "title", True)
    tracker.commit()
    tracker.close()
    details.open_details("set", "40")
    session, view = opened["shown"][0]
    assert session.identity == "collection:1001"
    assert _keys(view) == ["ids", "title"]
    assert view.rows[1].action == "unlock"


def test_set_details_lists_moves(opened, monkeypatch):
    _with_sets(opened, monkeypatch)
    tracker = TrackerDB(opened["db_path"])
    tracker.record_set_move("tmdb:949", 7, "Heat Collectie", 40, "2026-11-10")
    tracker.commit()
    tracker.close()
    details.open_details("set", "40")
    _session, view = opened["shown"][0]
    assert "Heat: moved here from Heat Collectie on 10 Nov 2026" in _ids_texts(view)


def test_movie_details_shows_its_move(opened, monkeypatch):
    _with_sets(opened, monkeypatch)
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.record_set_move("tmdb:949", 7, "Heat Collectie", 40, "2026-11-10")
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    _session, view = opened["shown"][0]
    assert "Moved by MetaMender from the set Heat Collectie on 10 Nov 2026" in _ids_texts(view)


def test_movie_details_says_the_user_moved_it(opened, monkeypatch):
    _with_sets(opened, monkeypatch, setid_of_heat=41)
    tracker = TrackerDB(opened["db_path"])
    tracker.upsert_item("movie", "tmdb:949", "Heat", kodi_id=7)
    tracker.record_set_move("tmdb:949", 7, "Heat Collectie", 40, "2026-11-10")
    tracker.commit()
    tracker.close()
    details.open_details("movie", "7")
    _session, view = opened["shown"][0]
    assert "Left in the set Favourites: you moved it there" in _ids_texts(view)


def test_movie_details_without_a_move_asks_kodi_nothing_about_sets(opened, monkeypatch):
    asked = []
    monkeypatch.setattr(details.kodi, "get_movie_setid", asked.append)
    details.open_details("movie", "7")
    assert asked == []
    _session, view = opened["shown"][0]
    assert not any("set" in text.lower() for text in _ids_texts(view))


def test_restore_set_name_writes_title(tmp_path, monkeypatch):
    ident = "collection:1001"
    tracker = TrackerDB(str(tmp_path / "backfill.db"))
    tracker.upsert_item("set", ident, "Heat Collection", kodi_id=40, tmdb_id="1001")
    tracker.keep_original("set", ident, "title", "Heat Collectie")
    tracker.set_written_fp("set", ident, "title", fingerprint("Heat Collection"))
    tracker.commit()
    sent = []

    def rpc(raw):
        import json
        query = json.loads(raw)
        sent.append(query)
        if query["method"] == "VideoLibrary.GetMovieSetDetails":
            return json.dumps({"result": {"setdetails": dict(SET_ITEM)}})
        return json.dumps({"result": "OK"})

    monkeypatch.setattr(details.kodi.xbmc, "executeJSONRPC", rpc)
    try:
        assert details.restore_field(tracker, SET, 40, ident, "title") == "restored"
        assert tracker.get_keep("set", ident)["title"]["locked"] is True
    finally:
        tracker.close()
    write = sent[-1]
    assert write["method"] == "VideoLibrary.SetMovieSetDetails"
    assert write["params"] == {"setid": 40, "title": "Heat Collectie"}

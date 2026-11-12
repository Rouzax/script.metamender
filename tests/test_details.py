"""Tests for restoring an original value and unlocking a field."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from resources.lib.data.media_types import MOVIE
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


def test_unlock(db):
    db.set_locked("movie", IDENT, "plot", True)
    db.commit()
    details.unlock_field(db, MOVIE, IDENT, "plot")
    assert db.get_keep("movie", IDENT)["plot"]["locked"] is False

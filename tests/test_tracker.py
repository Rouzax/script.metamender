"""Tests for SQLite tracker."""
from __future__ import annotations

import os

from resources.lib.data.tracker import TrackerDB


def test_create_db(tmp_path):
    db_path = str(tmp_path / "backfill.db")
    db = TrackerDB(db_path)
    assert os.path.exists(db_path)
    db.close()


def test_schema_has_items_table(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    cursor = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
    )
    assert cursor.fetchone() is not None
    db.close()


def test_schema_has_field_status_table(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    cursor = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='field_status'"
    )
    assert cursor.fetchone() is not None
    db.close()


def test_schema_has_scan_state_table(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    cursor = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_state'"
    )
    assert cursor.fetchone() is not None
    db.close()


def test_upsert_item_creates_new(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
    item = db.get_item("movie", "Test Movie")
    assert item is not None
    assert item["tmdb_id"] == "12345"
    assert item["status"] == "pending"
    assert item["attempt_count"] == 0
    db.close()


def test_upsert_item_updates_existing(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
    db.upsert_item("movie", "Test Movie", kodi_id=2)
    item = db.get_item("movie", "Test Movie")
    assert item["kodi_id"] == 2
    assert item["tmdb_id"] == "12345"  # preserved from first insert
    db.close()


def test_upsert_returns_id(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    item_id = db.upsert_item("movie", "Test Movie", kodi_id=1)
    assert isinstance(item_id, int)
    assert item_id > 0
    db.close()


def test_get_item_not_found(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    assert db.get_item("movie", "Nonexistent") is None
    db.close()


def test_get_pending_items(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.upsert_item("movie", "Movie B", kodi_id=2, tmdb_id="2")
    db.update_item_status("movie", "Movie A", "complete")
    pending = db.get_items_by_status("movie", "pending")
    assert len(pending) == 1
    assert pending[0]["title"] == "Movie B"
    db.close()


def test_get_items_by_status_empty(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    assert db.get_items_by_status("movie", "pending") == []
    db.close()


def test_record_attempt_increments_count(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.record_attempt("movie", "Test Movie", next_retry="2026-06-14")
    item = db.get_item("movie", "Test Movie")
    assert item["attempt_count"] == 1
    assert item["next_retry"] == "2026-06-14"
    assert item["last_scanned"] is not None
    db.close()


def test_record_attempt_increments_multiple(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.record_attempt("movie", "Test Movie", next_retry="2026-06-14")
    db.record_attempt("movie", "Test Movie", next_retry="2026-07-14")
    item = db.get_item("movie", "Test Movie")
    assert item["attempt_count"] == 2
    assert item["next_retry"] == "2026-07-14"
    db.close()

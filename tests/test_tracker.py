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


# --- Task 2.3: Field status ---

def test_set_field_status(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.set_field_status("movie", "Test Movie", "rating", "filled",
                        source="tmdb", vote_count=500)
    field = db.get_field_status("movie", "Test Movie", "rating")
    assert field is not None
    assert field["status"] == "filled"
    assert field["source"] == "tmdb"
    assert field["vote_count"] == 500
    assert field["fetched_at"] is not None
    db.close()


def test_set_field_status_updates_existing(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.set_field_status("movie", "Test Movie", "rating", "stale",
                        source="tmdb", vote_count=5)
    db.set_field_status("movie", "Test Movie", "rating", "filled",
                        source="omdb", vote_count=2000)
    field = db.get_field_status("movie", "Test Movie", "rating")
    assert field["status"] == "filled"
    assert field["source"] == "omdb"
    assert field["vote_count"] == 2000
    db.close()


def test_get_field_status_not_found(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    assert db.get_field_status("movie", "Test Movie", "rating") is None
    db.close()


def test_get_fields_by_status(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.set_field_status("movie", "Test Movie", "rating", "filled", source="tmdb")
    db.set_field_status("movie", "Test Movie", "plot", "empty")
    db.set_field_status("movie", "Test Movie", "tagline", "empty")
    empty = db.get_fields_by_status("movie", "Test Movie", "empty")
    assert len(empty) == 2
    assert {f["field"] for f in empty} == {"plot", "tagline"}
    db.close()


def test_get_fields_by_status_empty_result(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    assert db.get_fields_by_status("movie", "Test Movie", "filled") == []
    db.close()


def test_get_checked_fields(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.set_field_status("movie", "Test Movie", "rating", "filled",
                        source="tmdb", vote_count=500)
    db.set_field_status("movie", "Test Movie", "plot", "empty")
    checked = db.get_checked_fields("movie", "Test Movie")
    assert checked == {"rating", "plot"}
    db.close()


def test_get_checked_fields_empty(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    checked = db.get_checked_fields("movie", "Test Movie")
    assert checked == set()
    db.close()


def test_get_checked_fields_unknown_item(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    checked = db.get_checked_fields("movie", "Unknown")
    assert checked == set()
    db.close()


# --- Task 2.4: Cycle tracking ---

def test_start_new_cycle_resets_processed(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.mark_cycle_processed("movie", "Movie A")
    item = db.get_item("movie", "Movie A")
    assert item["cycle_processed"] == 1
    db.start_new_cycle()
    item = db.get_item("movie", "Movie A")
    assert item["cycle_processed"] == 0
    db.close()


def test_get_unprocessed_in_cycle(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.upsert_item("movie", "Movie B", kodi_id=2, tmdb_id="2")
    db.mark_cycle_processed("movie", "Movie A")
    unprocessed = db.get_unprocessed_items("movie")
    assert len(unprocessed) == 1
    assert unprocessed[0]["title"] == "Movie B"
    db.close()


def test_get_unprocessed_excludes_exhausted(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.upsert_item("movie", "Movie B", kodi_id=2, tmdb_id="2")
    db.update_item_status("movie", "Movie A", "exhausted")
    unprocessed = db.get_unprocessed_items("movie")
    assert len(unprocessed) == 1
    assert unprocessed[0]["title"] == "Movie B"
    db.close()


def test_get_unprocessed_excludes_complete(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.update_item_status("movie", "Movie A", "complete")
    assert db.get_unprocessed_items("movie") == []
    db.close()


def test_scan_state_persistence(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.set_scan_state("omdb_calls_today", "42")
    assert db.get_scan_state("omdb_calls_today") == "42"
    db.close()


def test_scan_state_update(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.set_scan_state("omdb_calls_today", "42")
    db.set_scan_state("omdb_calls_today", "99")
    assert db.get_scan_state("omdb_calls_today") == "99"
    db.close()


def test_scan_state_not_found(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    assert db.get_scan_state("nonexistent") is None
    db.close()

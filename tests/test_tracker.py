"""Tests for SQLite tracker."""
from __future__ import annotations

import os

from resources.lib.constants import SCHEMA_VERSION
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


def test_set_field_status_checked_preserves_vote_metadata(tmp_path):
    # A4: confirming a rating "checked" must NOT erase the staleness metadata
    # (source/vote_count/fetched_at) recorded when it was last filled.
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.set_field_status("movie", "tmdb:1", "rating", "filled",
                        source="themoviedb", vote_count=12)
    first = db.get_field_status("movie", "tmdb:1", "rating")
    db.set_field_status("movie", "tmdb:1", "rating", "checked")  # no source/votes
    fs = db.get_field_status("movie", "tmdb:1", "rating")
    assert fs["status"] == "checked"
    assert fs["vote_count"] == 12
    assert fs["source"] == "themoviedb"
    assert fs["fetched_at"] == first["fetched_at"]
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


# ----------------------------------------------------------------------
# Task 3: new schema (columns, version, busy_timeout)
# ----------------------------------------------------------------------


def test_items_has_identity_column(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(items)")}
    assert "identity" in cols
    assert "last_seen" in cols
    db.close()


def test_field_status_has_retry_columns(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(field_status)")}
    assert "attempt_count" in cols
    assert "next_retry" in cols
    db.close()


def test_identity_is_unique_key(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    idx = db._conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='items'"
    ).fetchone()["sql"]
    assert "UNIQUE(media_type, identity)" in idx
    db.close()


def test_schema_version_recorded(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    assert db.get_scan_state("schema_version") == str(SCHEMA_VERSION)
    db.close()


def test_busy_timeout_set(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    val = db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert val >= 5000
    db.close()


# ----------------------------------------------------------------------
# Task 4: identity-keyed item CRUD (backward-compatible)
# ----------------------------------------------------------------------


def test_upsert_with_distinct_identity_and_title(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:438631", title="Dune", tmdb_id="438631")
    item = db.get_item("movie", "tmdb:438631")
    assert item is not None
    assert item["identity"] == "tmdb:438631"
    assert item["title"] == "Dune"
    db.close()


def test_remakes_do_not_collide(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Dune (1984)", title="Dune", kodi_id=1)
    db.upsert_item("movie", "Dune (2021)", title="Dune", kodi_id=2)
    a = db.get_item("movie", "Dune (1984)")
    b = db.get_item("movie", "Dune (2021)")
    assert a["kodi_id"] == 1
    assert b["kodi_id"] == 2
    db.close()


def test_upsert_does_not_clobber_title_when_omitted(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="Real Title", kodi_id=1)
    db.upsert_item("movie", "tmdb:1", imdb_id="tt9")  # no title -> keep existing
    item = db.get_item("movie", "tmdb:1")
    assert item["title"] == "Real Title"
    assert item["imdb_id"] == "tt9"
    db.close()


def test_title_defaults_to_identity_when_omitted_on_insert(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Some Title", kodi_id=1)  # legacy-style call
    item = db.get_item("movie", "Some Title")
    assert item["title"] == "Some Title"
    db.close()


def test_update_last_seen(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A", kodi_id=1)
    db.update_last_seen("movie", "tmdb:1", "2026-06-08")
    assert db.get_item("movie", "tmdb:1")["last_seen"] == "2026-06-08"


# ----------------------------------------------------------------------
# Task 5: per-field retry storage
# ----------------------------------------------------------------------


def test_record_field_attempt_increments_and_sets_retry(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.set_field_status("movie", "tmdb:1", "plot", "empty")
    db.record_field_attempt("movie", "tmdb:1", "plot", next_retry="2026-06-15")
    fs = db.get_field_status("movie", "tmdb:1", "plot")
    assert fs["attempt_count"] == 1
    assert fs["next_retry"] == "2026-06-15"
    db.record_field_attempt("movie", "tmdb:1", "plot", next_retry="2026-07-15")
    fs = db.get_field_status("movie", "tmdb:1", "plot")
    assert fs["attempt_count"] == 2
    assert fs["next_retry"] == "2026-07-15"
    db.close()


def test_record_field_attempt_creates_row_if_missing(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.record_field_attempt("movie", "tmdb:1", "rating", next_retry="2026-06-15")
    fs = db.get_field_status("movie", "tmdb:1", "rating")
    assert fs is not None
    assert fs["attempt_count"] == 1
    db.close()


def test_set_field_status_preserves_attempt_count(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.record_field_attempt("movie", "tmdb:1", "plot", next_retry="2026-06-15")
    db.set_field_status("movie", "tmdb:1", "plot", "filled", source="tmdb")
    fs = db.get_field_status("movie", "tmdb:1", "plot")
    assert fs["status"] == "filled"
    assert fs["attempt_count"] == 1  # not reset by a status update
    db.close()


# ----------------------------------------------------------------------
# Task 6: orphan pruning
# ----------------------------------------------------------------------


def test_prune_orphans_removes_long_absent(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.update_last_seen("movie", "tmdb:1", "2026-01-01")  # long ago
    removed = db.prune_orphans("movie", cutoff="2026-06-01")
    assert removed == 1
    assert db.get_item("movie", "tmdb:1") is None
    db.close()


def test_prune_orphans_keeps_recent(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.update_last_seen("movie", "tmdb:1", "2026-06-07")
    removed = db.prune_orphans("movie", cutoff="2026-06-01")
    assert removed == 0
    assert db.get_item("movie", "tmdb:1") is not None
    db.close()


def test_prune_orphans_keeps_null_last_seen(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")  # never stamped
    removed = db.prune_orphans("movie", cutoff="2026-06-01")
    assert removed == 0
    db.close()


def test_prune_orphans_cascades_field_status(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.set_field_status("movie", "tmdb:1", "plot", "empty")
    db.update_last_seen("movie", "tmdb:1", "2026-01-01")
    item_id = db._get_item_id("movie", "tmdb:1")
    db.prune_orphans("movie", cutoff="2026-06-01")
    rows = db._conn.execute(
        "SELECT COUNT(*) c FROM field_status WHERE item_id = ?", (item_id,)
    ).fetchone()["c"]
    assert rows == 0
    db.close()


def test_prune_orphans_multiple_items(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.upsert_item("movie", "tmdb:2", title="B")
    db.upsert_item("movie", "tmdb:3", title="C")
    db.update_last_seen("movie", "tmdb:1", "2026-01-01")
    db.update_last_seen("movie", "tmdb:2", "2026-01-02")
    db.update_last_seen("movie", "tmdb:3", "2026-06-07")  # recent, kept
    removed = db.prune_orphans("movie", cutoff="2026-06-01")
    assert removed == 2
    assert db.get_item("movie", "tmdb:1") is None
    assert db.get_item("movie", "tmdb:2") is None
    assert db.get_item("movie", "tmdb:3") is not None
    db.close()

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


# --- Task 2.3: Field status ---

def test_record_field_fetch_sets_facts(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    db.record_field_fetch("movie", "Test Movie", "rating",
                          next_retry="2026-12-01", source="tmdb", vote_count=500)
    db.commit()
    field = db.get_field_status("movie", "Test Movie", "rating")
    assert field is not None
    assert field["fetch_count"] == 1
    assert field["source"] == "tmdb"
    assert field["vote_count"] == 500
    assert field["next_retry"] == "2026-12-01"
    assert field["fetched_at"] is not None
    db.close()


def test_record_field_fetch_coalesces_metadata(tmp_path):
    # A later fetch with None source/vote_count keeps the previously recorded
    # values (COALESCE), preserving staleness provenance.
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.record_field_fetch("movie", "tmdb:1", "rating",
                          next_retry="2026-12-01", source="themoviedb", vote_count=12)
    db.commit()
    first = db.get_field_status("movie", "tmdb:1", "rating")
    db.record_field_fetch("movie", "tmdb:1", "rating", next_retry="2027-01-01")
    db.commit()
    fs = db.get_field_status("movie", "tmdb:1", "rating")
    assert fs["fetch_count"] == 2
    assert fs["vote_count"] == 12
    assert fs["source"] == "themoviedb"
    assert fs["fetched_at"] >= first["fetched_at"]
    db.close()


def test_get_field_status_not_found(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="1")
    assert db.get_field_status("movie", "Test Movie", "rating") is None
    db.close()


def test_get_field_status_map(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.record_field_fetch("movie", "tmdb:1", "plot", next_retry=None, source="tmdb")
    db.record_field_fetch("movie", "tmdb:1", "tagline", next_retry="2026-12-01")
    db.commit()
    m = db.get_field_status_map("movie", "tmdb:1")
    assert set(m.keys()) == {"plot", "tagline"}
    assert m["plot"]["source"] == "tmdb"
    assert m["tagline"]["next_retry"] == "2026-12-01"
    assert db.get_field_status_map("movie", "unknown") == {}
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


def test_field_status_has_maturity_columns(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(field_status)")}
    assert "fetch_count" in cols
    assert "next_retry" in cols
    # The legacy status machine columns were removed (Phase 7c).
    assert "status" not in cols
    assert "attempt_count" not in cols
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
    db.record_field_fetch("movie", "tmdb:1", "plot", next_retry=None)
    db.commit()
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


def test_clear_field_status_removes_all_rows(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", title="A")
    db.record_field_fetch("movie", "tmdb:1", "plot", next_retry=None)
    db.record_field_fetch("movie", "tmdb:1", "tagline", next_retry="2026-12-01")
    db.commit()
    n = db.clear_field_status()
    assert n == 2
    assert db.get_field_status_map("movie", "tmdb:1") == {}
    db.close()


def test_fresh_db_stamps_current_schema_version(tmp_path):
    db = TrackerDB(str(tmp_path / "fresh.db"))
    row = db._conn.execute(
        "SELECT value FROM scan_state WHERE key='schema_version'"
    ).fetchone()
    db.close()
    assert int(row["value"]) == SCHEMA_VERSION


def test_newer_schema_db_is_not_downgraded(tmp_path):
    """Opening a DB from a newer addon must not re-stamp it to an older version."""
    path = str(tmp_path / "newer.db")
    db = TrackerDB(path)
    db._conn.execute(
        "INSERT OR REPLACE INTO scan_state (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION + 1),),
    )
    db._conn.commit()
    db.close()

    db2 = TrackerDB(path)  # must leave the newer version intact
    row = db2._conn.execute(
        "SELECT value FROM scan_state WHERE key='schema_version'"
    ).fetchone()
    db2.close()
    assert int(row["value"]) == SCHEMA_VERSION + 1


# ----------------------------------------------------------------------
# Task 3: maturity-fact recording (fetch_count, record_field_fetch, etc.)
# ----------------------------------------------------------------------

from datetime import date


def test_record_field_fetch_increments_count(tmp_path, kodi_stubs):
    from resources.lib.data.tracker import TrackerDB
    t = TrackerDB(str(tmp_path / "t.db"))
    t.upsert_item("movie", "Air", kodi_id=1, tmdb_id="1")
    t.record_field_fetch("movie", "Air", "plot", next_retry="2026-07-01", source="tmdb")
    t.record_field_fetch("movie", "Air", "plot", next_retry="2026-08-01", source="tmdb")
    row = t.get_field_status_map("movie", "Air")["plot"]
    t.close()
    assert row["fetch_count"] == 2
    assert row["fetched_at"] == date.today().isoformat()
    assert row["next_retry"] == "2026-08-01"


def test_reset_field_clock(tmp_path, kodi_stubs):
    from resources.lib.data.tracker import TrackerDB
    t = TrackerDB(str(tmp_path / "t.db"))
    t.upsert_item("movie", "Air", kodi_id=1, tmdb_id="1")
    t.record_field_fetch("movie", "Air", "plot", next_retry="2026-07-01")
    t.record_field_fetch("movie", "Air", "plot", next_retry="2026-08-01")
    t.reset_field_clock("movie", "Air", "plot", next_retry="2026-09-15")
    row = t.get_field_status_map("movie", "Air")["plot"]
    t.close()
    assert row["fetch_count"] == 1
    assert row["next_retry"] == "2026-09-15"


# --- Cycle lifecycle: begin_cycle / mark_cycle_complete ---

def test_begin_cycle_starts_fresh_cycle_on_first_run(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.mark_cycle_processed("movie", "Movie A")
    assert db.begin_cycle(resume_interrupted=True) is True
    assert db.get_item("movie", "Movie A")["cycle_processed"] == 0
    db.close()


def test_begin_cycle_starts_fresh_cycle_after_previous_completed(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.begin_cycle(resume_interrupted=True)
    db.mark_cycle_processed("movie", "Movie A")
    db.mark_cycle_complete()
    # resume_interrupted must NOT hold the previous completed cycle open.
    assert db.begin_cycle(resume_interrupted=True) is True
    assert db.get_item("movie", "Movie A")["cycle_processed"] == 0
    db.close()


def test_begin_cycle_resumes_interrupted_cycle(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.begin_cycle(resume_interrupted=True)
    db.mark_cycle_processed("movie", "Movie A")
    # No mark_cycle_complete: the cycle was interrupted.
    assert db.begin_cycle(resume_interrupted=True) is False
    assert db.get_item("movie", "Movie A")["cycle_processed"] == 1
    db.close()


def test_begin_cycle_restarts_interrupted_cycle_when_resume_disabled(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Movie A", kodi_id=1, tmdb_id="1")
    db.begin_cycle(resume_interrupted=False)
    db.mark_cycle_processed("movie", "Movie A")
    assert db.begin_cycle(resume_interrupted=False) is True
    assert db.get_item("movie", "Movie A")["cycle_processed"] == 0
    db.close()

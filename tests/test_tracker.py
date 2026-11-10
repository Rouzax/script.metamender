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


def test_reset_field_clock_records_written_value_and_keeps_old(tmp_path, kodi_stubs):
    from resources.lib.data.tracker import TrackerDB
    t = TrackerDB(str(tmp_path / "t.db"))
    t.upsert_item("movie", "Air", kodi_id=1, tmdb_id="1")
    t.record_field_fetch("movie", "Air", "plot", next_retry="2026-07-01", written_value="a")
    t.reset_field_clock("movie", "Air", "plot", "2026-09-15")
    kept = t.get_field_status_map("movie", "Air")["plot"]["written_value"]
    t.reset_field_clock("movie", "Air", "plot", "2026-09-15", written_value="b")
    replaced = t.get_field_status_map("movie", "Air")["plot"]["written_value"]
    t.reset_field_clock("movie", "Air", "tagline", "2026-09-15", written_value="c")
    inserted = t.get_field_status_map("movie", "Air")["tagline"]["written_value"]
    t.close()
    assert (kept, replaced, inserted) == ("a", "b", "c")


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


def test_upsert_none_id_keeps_stored_id(tmp_path):
    tracker = TrackerDB(str(tmp_path / "t.db"))
    tracker.upsert_item("tvshow", "s1", tmdb_id="123", tvdb_id="80767")
    tracker.upsert_item("tvshow", "s1", tmdb_id=None, tvdb_id=None, kodi_id=7)
    row = tracker.get_item("tvshow", "s1")
    tracker.close()
    assert row["tmdb_id"] == "123" and row["tvdb_id"] == "80767" and row["kodi_id"] == 7


def test_show_id_map_keyed_by_kodi_id_survives_same_title(tmp_path):
    tracker = TrackerDB(str(tmp_path / "t.db"))
    tracker.upsert_item("tvshow", "a", title="The Office", kodi_id=1, tmdb_id="2316")
    tracker.upsert_item("tvshow", "b", title="The Office", kodi_id=2, tmdb_id="2996")
    result = tracker.get_show_id_map()
    tracker.close()
    assert result == {1: "2316", 2: "2996"}


def test_show_tvdb_map_keyed_by_kodi_id(tmp_path):
    tracker = TrackerDB(str(tmp_path / "t.db"))
    tracker.upsert_item("tvshow", "a", title="A", kodi_id=1, tvdb_id="71663")
    tracker.upsert_item("tvshow", "b", title="B", kodi_id=2, tmdb_id="2996")
    tracker.upsert_item("episode", "c", title="C", kodi_id=3, tvdb_id="349232")
    result = tracker.get_show_tvdb_map()
    tracker.close()
    assert result == {1: "71663"}


def test_ids_checked_stored_and_kept(tmp_path):
    tracker = TrackerDB(str(tmp_path / "t.db"))
    tracker.upsert_item("tvshow", "a", title="A", kodi_id=1, ids_checked="2026-11-01")
    tracker.upsert_item("tvshow", "a", title="A", kodi_id=1, tmdb_id="5")
    row = tracker.get_item("tvshow", "a")
    tracker.close()
    assert row["ids_checked"] == "2026-11-01"
    assert row["tmdb_id"] == "5"


def test_checked_show_ids_keyed_by_kodi_id(tmp_path):
    tracker = TrackerDB(str(tmp_path / "t.db"))
    tracker.upsert_item("tvshow", "a", title="A", kodi_id=1, ids_checked="2026-11-01")
    tracker.upsert_item("tvshow", "b", title="B", kodi_id=2, tmdb_id="2996")
    tracker.upsert_item("episode", "c", title="C", kodi_id=3, ids_checked="2026-11-01")
    result = tracker.get_checked_show_ids()
    tracker.close()
    assert result == frozenset({1})


# --- Schema v3: provenance columns and settings fingerprint ---

_V2_SCHEMA_SQL = """\
CREATE TABLE items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    identity TEXT NOT NULL,
    title TEXT NOT NULL,
    kodi_id INTEGER, tmdb_id TEXT, imdb_id TEXT, tvdb_id TEXT,
    source_path TEXT, premiered TEXT, date_added TEXT, last_scanned TEXT,
    last_seen TEXT, next_retry TEXT,
    attempt_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    cycle_processed INTEGER DEFAULT 0,
    UNIQUE(media_type, identity)
);
CREATE TABLE field_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    field TEXT NOT NULL,
    source TEXT, vote_count INTEGER,
    fetch_count INTEGER DEFAULT 0,
    next_retry TEXT, fetched_at TEXT,
    UNIQUE(item_id, field)
);
CREATE TABLE scan_state (key TEXT PRIMARY KEY, value TEXT);
"""


def test_older_schema_is_recreated(tmp_path, monkeypatch):
    import sqlite3
    from resources.lib.data import tracker as tracker_mod

    path = str(tmp_path / "v2.db")
    conn = sqlite3.connect(path)
    conn.executescript(_V2_SCHEMA_SQL)
    conn.execute("INSERT INTO scan_state VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO items (media_type, identity, title) VALUES ('movie', 'a', 'A')")
    conn.execute("INSERT INTO field_status (item_id, field) VALUES (1, 'plot')")
    conn.commit()
    conn.close()

    warnings = []
    monkeypatch.setattr(
        tracker_mod.log, "warning", lambda *a, **k: warnings.append((a, k))
    )
    db = TrackerDB(path)
    cols = {r["name"] for r in db._conn.execute("PRAGMA table_info(field_status)")}
    n_items = db._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    n_fields = db._conn.execute("SELECT COUNT(*) FROM field_status").fetchone()[0]
    version = db._conn.execute(
        "SELECT value FROM scan_state WHERE key='schema_version'"
    ).fetchone()["value"]
    db.close()

    assert {"written_value", "from_fallback", "fallback_checks", "settings_fp"} <= cols
    assert n_items == 0 and n_fields == 0
    assert version == "3"
    assert [k.get("event") for _, k in warnings] == ["tracker.schema_reset"]


def _tracker_with_item(tmp_path):
    t = TrackerDB(str(tmp_path / "t.db"))
    t.upsert_item("movie", "Air", kodi_id=1, tmdb_id="1")
    return t


def test_record_written_value_and_fallback(tmp_path):
    t = _tracker_with_item(tmp_path)
    t.record_field_fetch(
        "movie", "Air", "plot", next_retry=None, source="imdb",
        written_value="A plot", from_fallback=True, settings_fp="fp1",
    )
    row = t.get_field_status("movie", "Air", "plot")
    assert row["written_value"] == "A plot"
    assert row["from_fallback"] == 1
    assert row["fallback_checks"] == 0
    assert row["settings_fp"] == "fp1"
    # None keeps the stored provenance
    t.record_field_fetch("movie", "Air", "plot", next_retry=None, settings_fp="fp1")
    row = t.get_field_status("movie", "Air", "plot")
    t.close()
    assert row["written_value"] == "A plot"
    assert row["source"] == "imdb"


def test_fallback_checks_increment_and_reset(tmp_path):
    t = _tracker_with_item(tmp_path)
    t.record_field_fetch("movie", "Air", "plot", None, from_fallback=False)
    row = t.get_field_status("movie", "Air", "plot")
    assert (row["from_fallback"], row["fallback_checks"]) == (0, 0)
    t.record_field_fetch("movie", "Air", "plot", None, from_fallback=True)
    row = t.get_field_status("movie", "Air", "plot")
    assert (row["from_fallback"], row["fallback_checks"]) == (1, 0)
    t.record_field_fetch(
        "movie", "Air", "plot", None, from_fallback=True, fallback_recheck=True,
    )
    t.record_field_fetch("movie", "Air", "plot", None, fallback_recheck=True)
    row = t.get_field_status("movie", "Air", "plot")
    assert (row["from_fallback"], row["fallback_checks"]) == (1, 2)
    # A sample that was not a recheck (young content on its schedule)
    # leaves the count alone.
    t.record_field_fetch("movie", "Air", "plot", None, from_fallback=True)
    t.record_field_fetch("movie", "Air", "plot", None)
    row = t.get_field_status("movie", "Air", "plot")
    assert (row["from_fallback"], row["fallback_checks"]) == (1, 2)
    t.record_field_fetch("movie", "Air", "plot", None, from_fallback=False)
    row = t.get_field_status("movie", "Air", "plot")
    t.close()
    assert (row["from_fallback"], row["fallback_checks"]) == (0, 0)


def test_settings_fp_overwritten(tmp_path):
    t = _tracker_with_item(tmp_path)
    t.record_field_fetch("movie", "Air", "plot", None, settings_fp="a")
    t.record_field_fetch("movie", "Air", "plot", None, settings_fp="b")
    assert t.get_field_status("movie", "Air", "plot")["settings_fp"] == "b"
    t.record_field_fetch("movie", "Air", "plot", None)
    assert t.get_field_status("movie", "Air", "plot")["settings_fp"] is None
    t.reset_field_clock("movie", "Air", "plot", "2026-09-15", settings_fp="c")
    row = t.get_field_status("movie", "Air", "plot")
    t.close()
    assert row["settings_fp"] == "c"
    assert row["fetch_count"] == 1


def test_reset_field_clock_restarts_fallback_checks_and_keeps_provenance(tmp_path):
    """Update now re-baselines the recheck count; the value is still the
    fallback value metamender wrote, so flag and fingerprint stay."""
    t = _tracker_with_item(tmp_path)
    t.record_field_fetch("movie", "Air", "plot", None, written_value="fp", from_fallback=True)
    t.record_field_fetch("movie", "Air", "plot", None, fallback_recheck=True)
    t.record_field_fetch("movie", "Air", "plot", None, fallback_recheck=True)
    assert t.get_field_status("movie", "Air", "plot")["fallback_checks"] == 2
    t.reset_field_clock("movie", "Air", "plot", "2026-09-15")
    row = t.get_field_status("movie", "Air", "plot")
    t.close()
    assert (row["from_fallback"], row["fallback_checks"]) == (1, 0)
    assert row["written_value"] == "fp"


def test_reset_field_clock_records_source_votes_and_fallback(tmp_path):
    t = _tracker_with_item(tmp_path)
    t.record_field_fetch("movie", "Air", "plot", None, source="tmdb", vote_count=5)
    t.reset_field_clock(
        "movie", "Air", "plot", "2026-09-15", source="tvdb", from_fallback=True,
    )
    row = t.get_field_status("movie", "Air", "plot")
    assert (row["source"], row["vote_count"], row["from_fallback"]) == ("tvdb", 5, 1)
    t.reset_field_clock("movie", "Air", "plot", "2026-09-15", vote_count=9)
    row = t.get_field_status("movie", "Air", "plot")
    assert (row["source"], row["vote_count"], row["from_fallback"]) == ("tvdb", 9, 1)
    t.reset_field_clock("movie", "Air", "plot", "2026-09-15", from_fallback=False)
    row = t.get_field_status("movie", "Air", "plot")
    t.reset_field_clock("movie", "Air", "tagline", "2026-09-15", from_fallback=True)
    inserted = t.get_field_status("movie", "Air", "tagline")
    t.close()
    assert row["from_fallback"] == 0
    assert (inserted["from_fallback"], inserted["fallback_checks"]) == (1, 0)


# ======================================================================
# Tests: adopt_identity
# =============================================================================


def _row_count(db):
    return db._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]


def test_adopt_identity_renames_the_same_kodi_items_row(tmp_path):
    # metamender wrote an IMDb id to a show keyed on "title (year)"; the
    # next scan computes imdb:<id> for the same Kodi item.
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("tvshow", "De Snorkels (1984)", kodi_id=7, imdb_id="tt0086798")
    db.record_field_fetch("tvshow", "De Snorkels (1984)", "plot", next_retry=None, source="tmdb")
    db.commit()

    assert db.adopt_identity("tvshow", "imdb:tt0086798", 7) == "De Snorkels (1984)"

    assert db.get_item("tvshow", "De Snorkels (1984)") is None
    assert db.get_item("tvshow", "imdb:tt0086798")["kodi_id"] == 7
    assert db.get_field_status("tvshow", "imdb:tt0086798", "plot")["fetch_count"] == 1
    assert _row_count(db) == 1
    db.close()


def test_adopt_identity_matches_tmdb_ids(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Foo (2001)", kodi_id=3, tmdb_id="170")
    assert db.adopt_identity("movie", "tmdb:170", 3) == "Foo (2001)"
    assert db.get_item("movie", "tmdb:170") is not None
    db.close()


def test_adopt_identity_noop_when_identity_exists(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "imdb:tt1", kodi_id=3, imdb_id="tt1")
    db.upsert_item("movie", "Foo (2001)", kodi_id=3, imdb_id="tt1")
    assert db.adopt_identity("movie", "imdb:tt1", 3) is None
    assert db.get_item("movie", "Foo (2001)") is not None
    db.close()


def test_adopt_identity_needs_the_same_kodi_item(tmp_path):
    # Another Kodi item that resolved to the same id keeps its own row.
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Foo (2001)", kodi_id=3, imdb_id="tt1")
    assert db.adopt_identity("movie", "imdb:tt1", 4) is None
    assert db.get_item("movie", "Foo (2001)") is not None
    db.close()


def test_adopt_identity_needs_the_matching_id(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "Foo (2001)", kodi_id=3, imdb_id="tt2")
    assert db.adopt_identity("movie", "imdb:tt1", 3) is None
    db.close()


def test_adopt_identity_ignores_identities_without_an_id(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("episode", "Show S01E01", kodi_id=3)
    assert db.adopt_identity("episode", "Show S01E02", 3) is None
    assert db.adopt_identity("movie", "Bar (1999)", 3) is None
    db.close()


def test_known_kodi_ids_per_media_type(tmp_path):
    db = TrackerDB(str(tmp_path / "backfill.db"))
    db.upsert_item("movie", "tmdb:1", kodi_id=3)
    db.upsert_item("movie", "tmdb:2", kodi_id=5)
    db.upsert_item("movie", "Foo (2001)")
    db.upsert_item("tvshow", "tmdb:9", kodi_id=4)
    assert db.known_kodi_ids("movie") == {3, 5}
    assert db.known_kodi_ids("episode") == set()
    db.close()

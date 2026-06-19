"""
SQLite tracker for backfill state.

Tracks which items have been processed, which fields were filled,
retry scheduling, and scan cycle state.

Logging:
    Logger: 'tracker'
    Key events:
        - tracker.upsert (DEBUG): Item created or updated
        - tracker.attempt (DEBUG): Retry attempt recorded
        - tracker.exhausted (INFO): Item marked as exhausted
        - tracker.field_update (DEBUG): Field status changed
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any, Dict, Optional

from resources.lib.constants import SCHEMA_VERSION
from resources.lib.utils import get_logger

log = get_logger('tracker')

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    identity TEXT NOT NULL,
    title TEXT NOT NULL,
    kodi_id INTEGER,
    tmdb_id TEXT,
    imdb_id TEXT,
    tvdb_id TEXT,
    source_path TEXT,
    premiered TEXT,
    date_added TEXT,
    last_scanned TEXT,
    last_seen TEXT,
    next_retry TEXT,
    attempt_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    cycle_processed INTEGER DEFAULT 0,
    UNIQUE(media_type, identity)
);

CREATE TABLE IF NOT EXISTS field_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    field TEXT NOT NULL,
    source TEXT,
    vote_count INTEGER,
    fetch_count INTEGER DEFAULT 0,
    next_retry TEXT,
    fetched_at TEXT,
    UNIQUE(item_id, field)
);

CREATE TABLE IF NOT EXISTS scan_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_next_retry ON items(next_retry);
CREATE INDEX IF NOT EXISTS idx_items_cycle ON items(cycle_processed);
CREATE INDEX IF NOT EXISTS idx_items_last_seen ON items(last_seen);
CREATE INDEX IF NOT EXISTS idx_field_status ON field_status(item_id, field);
"""

# Columns that callers can set via upsert_item kwargs
_UPSERT_KWARGS = frozenset((
    "kodi_id", "tmdb_id", "imdb_id", "tvdb_id",
    "source_path", "premiered", "date_added",
))


class TrackerDB:
    """SQLite-backed tracker for backfill item state."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables/indexes if missing and reconcile the schema version.

        Fresh database: create everything and stamp SCHEMA_VERSION. Existing
        database: read the stored version and, if older, run ordered migrations
        (none defined yet for v2) before re-stamping. A database from a NEWER
        addon (stored > SCHEMA_VERSION) is left as-is with a warning rather than
        silently re-stamped, so a forward-incompatible open is at least visible.
        """
        # CREATE TABLE IF NOT EXISTS is safe on fresh and existing DBs, and
        # guarantees scan_state exists so the version marker can be read.
        self._conn.executescript(_SCHEMA_SQL)

        row = self._conn.execute(
            "SELECT value FROM scan_state WHERE key='schema_version'"
        ).fetchone()
        stored = int(row["value"]) if row and str(row["value"]).isdigit() else None

        if stored is not None and stored > SCHEMA_VERSION:
            log.warning(
                "Tracker database is from a newer addon version; "
                "operating without migrating",
                event="tracker.schema_newer",
                stored=stored,
                supported=SCHEMA_VERSION,
            )
            return

        if stored is not None and stored < SCHEMA_VERSION:
            # Ordered migrations would run here as the schema evolves.
            log.info(
                "Migrating tracker schema",
                event="tracker.schema_migrate",
                from_version=stored,
                to_version=SCHEMA_VERSION,
            )

        self._conn.execute(
            "INSERT OR REPLACE INTO scan_state (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Item CRUD
    # ------------------------------------------------------------------

    def upsert_item(
        self, media_type: str, identity: str, title: Optional[str] = None, **kwargs: Any
    ) -> int:
        """Insert or update an item keyed on (media_type, identity).

        `title` is display-only. When omitted it defaults to `identity` on
        insert and is left unchanged on update. Other supplied kwargs update
        their columns; unmentioned columns keep their existing values.

        Returns the row id of the inserted or updated item.
        """
        cols = {k: v for k, v in kwargs.items() if k in _UPSERT_KWARGS}

        set_parts = []
        for col in _UPSERT_KWARGS:
            if col in cols:
                set_parts.append("{col} = excluded.{col}".format(col=col))
            else:
                set_parts.append("{col} = items.{col}".format(col=col))
        # title: update only when explicitly provided, else keep existing
        if title is not None:
            set_parts.append("title = excluded.title")
        else:
            set_parts.append("title = items.title")

        insert_cols = ["media_type", "identity", "title"] + list(cols.keys())
        placeholders = ", ".join(["?"] * len(insert_cols))
        values = [media_type, identity, title if title is not None else identity] + list(cols.values())

        sql = (
            "INSERT INTO items ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT(media_type, identity) DO UPDATE SET {sets}"
        ).format(
            cols=", ".join(insert_cols),
            placeholders=placeholders,
            sets=", ".join(set_parts),
        )

        try:
            cursor = self._conn.execute(sql, values)
        except sqlite3.Error:
            log.exception(
                "Failed to upsert item",
                event="tracker.error",
                media_type=media_type,
                identity=identity,
            )
            return -1

        log.debug("Item upserted", event="tracker.upsert", media_type=media_type, identity=identity)
        return cursor.lastrowid  # type: ignore[return-value]

    def get_item(self, media_type: str, identity: str) -> Optional[Dict[str, Any]]:
        """Get an item by (media_type, identity), or None if not found."""
        cursor = self._conn.execute(
            "SELECT * FROM items WHERE media_type = ? AND identity = ?",
            (media_type, identity),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def update_item_status(
        self, media_type: str, identity: str, status: str
    ) -> None:
        """Update the status column for an item."""
        try:
            self._conn.execute(
                "UPDATE items SET status = ? WHERE media_type = ? AND identity = ?",
                (status, media_type, identity),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to update item status",
                event="tracker.error",
                media_type=media_type,
                identity=identity,
                status=status,
            )
            return

        log.debug(
            "Item status updated",
            media_type=media_type,
            identity=identity,
            status=status,
        )

    def update_last_seen(self, media_type: str, identity: str, today: str) -> None:
        """Record that this item was present in the Kodi library on `today`."""
        try:
            self._conn.execute(
                "UPDATE items SET last_seen = ? WHERE media_type = ? AND identity = ?",
                (today, media_type, identity),
            )
        except sqlite3.Error:
            log.exception(
                "Failed to update last_seen",
                event="tracker.error",
                media_type=media_type,
                identity=identity,
            )

    def prune_orphans(self, media_type: str, cutoff: str) -> int:
        """Delete items of this type whose last_seen is older than `cutoff`.

        Items with a NULL last_seen are kept (never confirmed present yet).
        Associated field_status rows are deleted too. `cutoff` is an ISO date
        string (callers pass today minus the retention window). Returns the
        number of items removed.
        """
        try:
            ids = [
                r["id"] for r in self._conn.execute(
                    "SELECT id FROM items WHERE media_type = ? "
                    "AND last_seen IS NOT NULL AND last_seen < ?",
                    (media_type, cutoff),
                )
            ]
            if not ids:
                return 0
            placeholders = ", ".join(["?"] * len(ids))
            self._conn.execute(
                "DELETE FROM field_status WHERE item_id IN ({})".format(placeholders), ids
            )
            self._conn.execute(
                "DELETE FROM items WHERE id IN ({})".format(placeholders), ids
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to prune orphans", event="tracker.error", media_type=media_type
            )
            return 0

        log.info(
            "Pruned orphaned items", event="tracker.prune",
            media_type=media_type, removed=len(ids),
        )
        return len(ids)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_item_id(self, media_type: str, identity: str) -> Optional[int]:
        """Look up the row id for an item by (media_type, identity)."""
        cursor = self._conn.execute(
            "SELECT id FROM items WHERE media_type = ? AND identity = ?",
            (media_type, identity),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row["id"]  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Field status (Task 2.3)
    # ------------------------------------------------------------------

    def get_field_status(
        self, media_type: str, identity: str, field: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single field_status row, or None if not found."""
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            log.debug(
                "Item not found for field status query",
                media_type=media_type,
                identity=identity,
                field=field,
            )
            return None

        cursor = self._conn.execute(
            "SELECT * FROM field_status WHERE item_id = ? AND field = ?",
            (item_id, field),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def get_field_status_map(
        self, media_type: str, identity: str
    ) -> Dict[str, Dict[str, Any]]:
        """Return {field: row_dict} for all field_status rows of an item."""
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            return {}
        cursor = self._conn.execute(
            "SELECT * FROM field_status WHERE item_id = ?", (item_id,)
        )
        return {row["field"]: dict(row) for row in cursor.fetchall()}

    def record_field_fetch(
        self,
        media_type: str,
        identity: str,
        field: str,
        next_retry: Optional[str],
        source: Optional[str] = None,
        vote_count: Optional[int] = None,
    ) -> None:
        """Record a successful sample: bump fetch_count, stamp fetched_at, set the
        next due date and (optionally) source/vote_count. COALESCE keeps prior
        source/vote_count when the new call passes None."""
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            return
        today = date.today().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO field_status "
                "(item_id, field, fetch_count, fetched_at, next_retry, source, vote_count) "
                "VALUES (?, ?, 1, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, field) DO UPDATE SET "
                "fetch_count = field_status.fetch_count + 1, "
                "fetched_at = excluded.fetched_at, "
                "next_retry = excluded.next_retry, "
                "source = COALESCE(excluded.source, field_status.source), "
                "vote_count = COALESCE(excluded.vote_count, field_status.vote_count)",
                (item_id, field, today, next_retry, source, vote_count),
            )
        except sqlite3.Error:
            log.exception(
                "Failed to record field fetch",
                event="tracker.error",
                media_type=media_type,
                identity=identity,
                field=field,
            )

    def reset_field_clock(
        self, media_type: str, identity: str, field: str, next_retry: Optional[str]
    ) -> None:
        """Re-baseline a field to its first sample (manual 'Update now').

        Stores the supplied next_retry date so young content is re-sampled on
        the normal schedule after a manual update, rather than being skipped
        forever because NULL next_retry is treated as "not due" by decide_field.
        """
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            return
        today = date.today().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO field_status (item_id, field, fetch_count, fetched_at, next_retry) "
                "VALUES (?, ?, 1, ?, ?) "
                "ON CONFLICT(item_id, field) DO UPDATE SET "
                "fetch_count = 1, fetched_at = excluded.fetched_at, next_retry = excluded.next_retry",
                (item_id, field, today, next_retry),
            )
        except sqlite3.Error:
            log.exception(
                "Failed to reset field clock",
                event="tracker.error",
                media_type=media_type,
                identity=identity,
                field=field,
            )

    def clear_field_status(self) -> int:
        """Delete all field_status rows (the Reset-tracker action). Returns count."""
        try:
            cur = self._conn.execute("DELETE FROM field_status")
            self._conn.commit()
            return cur.rowcount
        except sqlite3.Error:
            log.exception("Failed to clear field status", event="tracker.error")
            return 0

    def commit(self) -> None:
        """Commit the current transaction (callers batch one item's writes)."""
        self._conn.commit()

    def get_show_id_map(self) -> Dict[str, str]:
        """Map TV show title to its show-level TMDB ID.

        Built from the tvshow items recorded during the show scan so the
        episode scan can resolve the correct show TMDB ID for season lookups.
        """
        show_map = {}  # type: Dict[str, str]
        cursor = self._conn.execute(
            "SELECT title, tmdb_id FROM items "
            "WHERE media_type = 'tvshow' AND tmdb_id IS NOT NULL"
        )
        for row in cursor.fetchall():
            show_map[row["title"]] = row["tmdb_id"]
        return show_map

    # ------------------------------------------------------------------
    # Cycle tracking and scan state (Task 2.4)
    # ------------------------------------------------------------------

    def start_new_cycle(self) -> None:
        """Reset cycle_processed to 0 for all items."""
        try:
            cursor = self._conn.execute("UPDATE items SET cycle_processed = 0")
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to start new cycle",
                event="tracker.error",
            )
            return

        log.info(
            "New scan cycle started",
            event="tracker.cycle_start",
            items_reset=cursor.rowcount,
        )

    def mark_cycle_processed(self, media_type: str, identity: str) -> None:
        """Mark an item as processed in the current cycle."""
        try:
            self._conn.execute(
                "UPDATE items SET cycle_processed = 1 "
                "WHERE media_type = ? AND identity = ?",
                (media_type, identity),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to mark cycle processed",
                event="tracker.error",
                media_type=media_type,
                identity=identity,
            )

    def set_scan_state(self, key: str, value: str) -> None:
        """Insert or replace a key-value pair in the scan_state table."""
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO scan_state (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to set scan state",
                event="tracker.error",
                key=key,
            )

    def get_scan_state(self, key: str) -> Optional[str]:
        """Get a value from the scan_state table, or None if not found."""
        cursor = self._conn.execute(
            "SELECT value FROM scan_state WHERE key = ?",
            (key,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return row["value"]  # type: ignore[no-any-return]

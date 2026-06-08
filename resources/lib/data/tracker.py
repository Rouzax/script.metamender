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
from typing import Any, Dict, List, Optional

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
    status TEXT DEFAULT 'empty',
    source TEXT,
    vote_count INTEGER,
    attempt_count INTEGER DEFAULT 0,
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
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables and indexes if they do not exist, record version."""
        self._conn.executescript(_SCHEMA_SQL)
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
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to upsert item",
                event="tracker.error",
                media_type=media_type,
                title=identity,
            )
            return -1

        log.debug("Item upserted", event="tracker.upsert", media_type=media_type, title=identity)
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

    def get_items_by_status(
        self, media_type: str, status: str
    ) -> List[Dict[str, Any]]:
        """Get all items matching (media_type, status)."""
        cursor = self._conn.execute(
            "SELECT * FROM items WHERE media_type = ? AND status = ?",
            (media_type, status),
        )
        return [dict(r) for r in cursor.fetchall()]

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
                title=identity,
                status=status,
            )
            return

        if status == "exhausted":
            log.info(
                "Item marked as exhausted",
                event="tracker.exhausted",
                media_type=media_type,
                title=identity,
                status=status,
            )
        else:
            log.debug(
                "Item status updated",
                media_type=media_type,
                title=identity,
                status=status,
            )

    def record_attempt(
        self,
        media_type: str,
        identity: str,
        next_retry: Optional[str] = None,
    ) -> None:
        """Increment attempt_count, set last_scanned to today, set next_retry."""
        today = date.today().isoformat()
        try:
            self._conn.execute(
                "UPDATE items "
                "SET attempt_count = attempt_count + 1, "
                "    last_scanned = ?, "
                "    next_retry = ? "
                "WHERE media_type = ? AND identity = ?",
                (today, next_retry, media_type, identity),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to record attempt",
                event="tracker.error",
                media_type=media_type,
                title=identity,
            )
            return

        # Query the new attempt_count for the log message
        cursor = self._conn.execute(
            "SELECT attempt_count FROM items "
            "WHERE media_type = ? AND identity = ?",
            (media_type, identity),
        )
        row = cursor.fetchone()
        attempt_count = row["attempt_count"] if row else None

        log.debug(
            "Attempt recorded",
            event="tracker.attempt",
            media_type=media_type,
            title=identity,
            next_retry=next_retry,
            attempt_count=attempt_count,
        )

    def update_last_seen(self, media_type: str, identity: str, today: str) -> None:
        """Record that this item was present in the Kodi library on `today`."""
        try:
            self._conn.execute(
                "UPDATE items SET last_seen = ? WHERE media_type = ? AND identity = ?",
                (today, media_type, identity),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to update last_seen",
                event="tracker.error",
                media_type=media_type,
                title=identity,
            )

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

    def set_field_status(
        self,
        media_type: str,
        identity: str,
        field: str,
        status: str,
        source: Optional[str] = None,
        vote_count: Optional[int] = None,
    ) -> None:
        """Insert or replace a field_status row for the given item and field.

        Sets fetched_at to today's date when status is "filled".
        """
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            log.warning(
                "Cannot update field status: item not found",
                event="tracker.field_update",
                media_type=media_type,
                title=identity,
                field=field,
            )
            return

        fetched_at = date.today().isoformat() if status == "filled" else None

        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO field_status "
                "(item_id, field, status, source, vote_count, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, field, status, source, vote_count, fetched_at),
            )
            self._conn.commit()
        except sqlite3.Error:
            log.exception(
                "Failed to update field status",
                event="tracker.error",
                media_type=media_type,
                title=identity,
                field=field,
            )
            return

        log.debug(
            "Field status updated",
            event="tracker.field_update",
            media_type=media_type,
            title=identity,
            field=field,
            status=status,
        )

    def get_field_status(
        self, media_type: str, identity: str, field: str
    ) -> Optional[Dict[str, Any]]:
        """Get a single field_status row, or None if not found."""
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            log.debug(
                "Item not found for field status query",
                media_type=media_type,
                title=identity,
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

    def get_fields_by_status(
        self, media_type: str, identity: str, status: str
    ) -> List[Dict[str, Any]]:
        """Get all field_status rows matching a given status for an item."""
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            log.debug(
                "Item not found for fields by status query",
                media_type=media_type,
                title=identity,
                status=status,
            )
            return []

        cursor = self._conn.execute(
            "SELECT * FROM field_status WHERE item_id = ? AND status = ?",
            (item_id, status),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_checked_fields(self, media_type: str, identity: str) -> set:
        """Return the set of field names that have any field_status entry."""
        item_id = self._get_item_id(media_type, identity)
        if item_id is None:
            return set()
        cursor = self._conn.execute(
            "SELECT DISTINCT field FROM field_status WHERE item_id = ?",
            (item_id,),
        )
        return {row["field"] for row in cursor.fetchall()}

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
                title=identity,
            )

    def get_unprocessed_items(self, media_type: str) -> List[Dict[str, Any]]:
        """Get items not yet processed in this cycle.

        Excludes items with status 'exhausted' or 'complete'.
        """
        cursor = self._conn.execute(
            "SELECT * FROM items "
            "WHERE media_type = ? AND cycle_processed = 0 "
            "AND status NOT IN ('exhausted', 'complete')",
            (media_type,),
        )
        return [dict(r) for r in cursor.fetchall()]

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

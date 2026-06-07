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

from resources.lib.utils import get_logger

log = get_logger('tracker')

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    kodi_id INTEGER,
    tmdb_id TEXT,
    imdb_id TEXT,
    tvdb_id TEXT,
    source_path TEXT,
    premiered TEXT,
    date_added TEXT,
    last_scanned TEXT,
    next_retry TEXT,
    attempt_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    cycle_processed INTEGER DEFAULT 0,
    UNIQUE(media_type, title)
);

CREATE TABLE IF NOT EXISTS field_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    field TEXT NOT NULL,
    status TEXT DEFAULT 'empty',
    source TEXT,
    vote_count INTEGER,
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
        self._ensure_schema()
        self._conn.execute("PRAGMA journal_mode=WAL")

    def _ensure_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        self._conn.executescript(_SCHEMA_SQL)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Item CRUD
    # ------------------------------------------------------------------

    def upsert_item(self, media_type: str, title: str, **kwargs: Any) -> int:
        """Insert or update an item keyed on (media_type, title).

        On conflict, only the supplied kwargs are updated; columns not
        mentioned in kwargs keep their existing values.

        Returns:
            The row id of the inserted or updated item.
        """
        # Filter to accepted columns only
        cols = {k: v for k, v in kwargs.items() if k in _UPSERT_KWARGS}

        # Build the SET clause: each column uses COALESCE(excluded.col, col)
        # when the caller supplies a value, or keeps the existing value when
        # the caller does not.
        set_parts = []
        for col in _UPSERT_KWARGS:
            if col in cols:
                set_parts.append("{col} = excluded.{col}".format(col=col))
            else:
                set_parts.append("{col} = items.{col}".format(col=col))

        insert_cols = ["media_type", "title"] + list(cols.keys())
        placeholders = ", ".join(["?"] * len(insert_cols))
        values = [media_type, title] + list(cols.values())

        sql = (
            "INSERT INTO items ({cols}) VALUES ({placeholders}) "
            "ON CONFLICT(media_type, title) DO UPDATE SET {sets}"
        ).format(
            cols=", ".join(insert_cols),
            placeholders=placeholders,
            sets=", ".join(set_parts),
        )

        cursor = self._conn.execute(sql, values)
        self._conn.commit()

        log.debug(
            "Item upserted",
            event="tracker.upsert",
            media_type=media_type,
            title=title,
        )

        return cursor.lastrowid  # type: ignore[return-value]

    def get_item(self, media_type: str, title: str) -> Optional[Dict[str, Any]]:
        """Get an item by (media_type, title), or None if not found."""
        cursor = self._conn.execute(
            "SELECT * FROM items WHERE media_type = ? AND title = ?",
            (media_type, title),
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
        self, media_type: str, title: str, status: str
    ) -> None:
        """Update the status column for an item."""
        self._conn.execute(
            "UPDATE items SET status = ? WHERE media_type = ? AND title = ?",
            (status, media_type, title),
        )
        self._conn.commit()

    def record_attempt(
        self,
        media_type: str,
        title: str,
        next_retry: Optional[str] = None,
    ) -> None:
        """Increment attempt_count, set last_scanned to today, set next_retry."""
        today = date.today().isoformat()
        self._conn.execute(
            "UPDATE items "
            "SET attempt_count = attempt_count + 1, "
            "    last_scanned = ?, "
            "    next_retry = ? "
            "WHERE media_type = ? AND title = ?",
            (today, next_retry, media_type, title),
        )
        self._conn.commit()

        log.debug(
            "Attempt recorded",
            event="tracker.attempt",
            media_type=media_type,
            title=title,
            next_retry=next_retry,
        )

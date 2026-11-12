"""The problems that need the user, and which of them were already shown.

The background service shows serious problems in the attention window, once
each: a problem appears again only when its detail changes, or when it was
cleared and comes back. The record also keeps the failed-scan streak and
how long each library folder has been unreachable, the evidence two of the
problems are raised from.

It is stored as JSON in the tracker's scan_state table, which the service
and Update now (a separate process) both open, so either can raise or clear
a problem for the other to see. ``update`` re-reads the stored record before
it writes, so a writer never saves an old copy over the other's change.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from resources.lib.utils import display_path, get_logger

log = get_logger('attention')

# Window problems, most important first: the first open one is the window's
# main problem, the others get one line each.
SETUP = "setup"
NO_KEY = "no_key"
KEY_REJECTED = "key_rejected"
SCANS_FAILING = "scans_failing"
FOLDER_OFFLINE = "folder_offline"
WINDOW_KINDS: tuple[str, ...] = (SETUP, NO_KEY, KEY_REJECTED, SCANS_FAILING, FOLDER_OFFLINE)

# A problem the user cannot fix, told by a toast instead of the window.
TVDB_AUTH = "tvdb_auth"

FOLDER_OFFLINE_DAYS = 3
FAILING_ATTEMPTS = 3
# Retries after a failure come every few minutes, so a short Kodi hiccup
# alone must not open the window: the failed attempts must span a day.
FAILING_MIN_SECONDS = 24 * 3600

_STATE_KEY = "attention"


@dataclass
class Problem:
    kind: str
    detail: str


class Attention:
    """Open problems (kind -> detail), what was shown for each, the
    failed-scan streak and the folders' first-unreachable times."""

    def __init__(self) -> None:
        self._open: dict[str, str] = {}
        self._shown: dict[str, str] = {}
        self._streak_count = 0
        self._streak_first = 0.0
        self._offline: dict[str, float] = {}

    # --- persistence --------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps({
            "open": self._open, "shown": self._shown,
            "streak": {"count": self._streak_count, "first": self._streak_first},
            "offline": self._offline,
        }, sort_keys=True)

    @classmethod
    def from_json(cls, text: str | None) -> Attention:
        """The stored record; an empty one when there is none or it cannot
        be read (a first run, a reset tracker, a hand-edited database)."""
        record = cls()
        try:
            data = json.loads(text or "{}")
            open_ = {str(k): str(v) for k, v in data.get("open", {}).items()}
            shown = {str(k): str(v) for k, v in data.get("shown", {}).items()}
            streak = data.get("streak", {})
            count, first = int(streak.get("count", 0)), float(streak.get("first", 0.0))
            offline = {str(k): float(v) for k, v in data.get("offline", {}).items()}
        except (ValueError, TypeError, AttributeError):
            return record
        record._open, record._shown = open_, shown
        record._streak_count, record._streak_first = count, first
        record._offline = offline
        return record

    # --- problems -----------------------------------------------------------

    def raise_problem(self, kind: str, detail: str = "") -> None:
        if self._open.get(kind) != detail:
            log.info("Problem raised", event="attention.raised", kind=kind, detail=detail)
        self._open[kind] = detail

    def clear(self, kind: str) -> None:
        """Close a problem and forget that it was shown, so it shows again
        if it comes back."""
        if kind in self._open:
            log.info("Problem cleared", event="attention.cleared", kind=kind)
        self._open.pop(kind, None)
        self._shown.pop(kind, None)

    def pending_window(self) -> tuple[Problem, list[Problem]] | None:
        """(main problem, the other open ones) when any open window problem
        has not been shown as it is now; else None."""
        open_ = [Problem(k, self._open[k]) for k in WINDOW_KINDS if k in self._open]
        if not any(self._shown.get(p.kind) != p.detail for p in open_):
            return None
        return open_[0], open_[1:]

    def mark_window_shown(self) -> None:
        for kind in WINDOW_KINDS:
            if kind in self._open:
                self._shown[kind] = self._open[kind]

    def pending_toast(self, kind: str) -> bool:
        return kind in self._open and self._shown.get(kind) != self._open[kind]

    def mark_toast_shown(self, kind: str) -> None:
        if kind in self._open:
            self._shown[kind] = self._open[kind]

    # --- evidence -----------------------------------------------------------

    def attempt(self, failed: bool, now: float) -> None:
        """Record a background scan attempt: a completed one ends the
        failure streak, a failed or early-stopped one extends it."""
        if not failed:
            self._streak_count, self._streak_first = 0, 0.0
            self.clear(SCANS_FAILING)
            return
        if self._streak_count == 0:
            self._streak_first = float(now)
        self._streak_count += 1
        if (self._streak_count >= FAILING_ATTEMPTS
                and now - self._streak_first >= FAILING_MIN_SECONDS):
            self.raise_problem(SCANS_FAILING)

    def folders_checked(self, reachable: dict[str, bool], now: float) -> None:
        """Record which library folders answered; a folder no longer in
        the library is forgotten. Folders are kept and shown without the
        credentials a source URL can carry."""
        answered = {display_path(path): ok for path, ok in reachable.items()}
        self._offline = {
            path: self._offline.get(path, float(now))
            for path, ok in answered.items() if not ok
        }
        overdue = sorted(
            path for path, since in self._offline.items()
            if now - since >= FOLDER_OFFLINE_DAYS * 24 * 3600
        )
        if overdue:
            self.raise_problem(FOLDER_OFFLINE, ", ".join(overdue))
        else:
            self.clear(FOLDER_OFFLINE)


def _read(tracker: Any) -> Attention:
    try:
        return Attention.from_json(tracker.get_scan_state(_STATE_KEY))
    except Exception:
        log.exception("Could not read the problem record", event="attention.store_failed")
        return Attention()


def load(tracker: Any) -> Attention:
    """The stored record (empty when it cannot be read)."""
    return _read(tracker)


def update(tracker: Any, change: Callable[[Attention], None]) -> Attention:
    """Apply ``change`` to the stored record as it is now and save it."""
    record = _read(tracker)
    change(record)
    try:
        tracker.set_scan_state(_STATE_KEY, record.to_json())
    except Exception:
        log.exception("Could not save the problem record", event="attention.store_failed")
    return record

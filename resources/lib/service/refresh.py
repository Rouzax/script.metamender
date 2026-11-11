"""Update now on a TV show or a season: refresh the show record (for a show)
and every episode under it, with a progress window that can be stopped or
sent to Kodi's background progress bar.

Logging:
    Logger: 'refresh'
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from resources.lib.data.kodi import (
    get_episode_show_id,
    get_item_by_id,
    get_season_info,
)
from resources.lib.data.media_types import TVSHOW
from resources.lib.utils import get_logger, lang

log = get_logger('refresh')


@dataclass(frozen=True)
class Scope:
    """What one refresh covers: a whole show (season None) or one season."""
    tvshowid: int
    season: int | None
    title: str


def resolve_scope(dbtype: str, dbid: int) -> Scope | None:
    """The refresh scope of a clicked show or season, or None for anything
    else or an item Kodi no longer has."""
    if dbtype == "tvshow":
        tvshowid, season = dbid, None
    elif dbtype == "season":
        info = get_season_info(dbid)
        if info is None:
            return None
        tvshowid, season = info
    else:
        return None
    show = get_item_by_id(TVSHOW, tvshowid)
    if show is None:
        return None
    return Scope(tvshowid, season, str(show.get("title") or tvshowid))


def show_id_of(dbtype: str, dbid: int) -> int | None:
    """The show a clicked show, season or episode belongs to; None for a
    movie or anything Kodi cannot place."""
    if dbtype == "tvshow":
        return dbid
    if dbtype == "season":
        info = get_season_info(dbid)
        return info[0] if info else None
    if dbtype == "episode":
        return get_episode_show_id(dbid)
    return None


# Items done before the time left is shown: the first ones include the slow
# show record and warm-up, so an early estimate is mostly noise.
_ETA_MIN_DONE = 3


class RefreshProgress:
    """Counts and the current item of one refresh, shared between the worker
    thread that runs it and the main thread that shows it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0
        self.done = 0
        self.updated = 0
        self.unchanged = 0
        self.unchecked = 0
        self.current = ""
        self._started = 0.0
        self._stop = False

    def begin(self, total: int, now: float) -> None:
        with self._lock:
            self.total, self._started = total, now

    def item_started(self, label: str) -> None:
        with self._lock:
            self.current = label

    def item_finished(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Count one item by what changed in the scan stats while it ran: an
        update wins over a field that could not be checked."""
        def grew(key: str) -> bool:
            return int(after.get(key) or 0) > int(before.get(key) or 0)
        with self._lock:
            self.done += 1
            if grew("updated"):
                self.updated += 1
            elif grew("transient") or grew("unreachable"):
                self.unchecked += 1
            else:
                self.unchanged += 1

    def request_stop(self) -> None:
        with self._lock:
            self._stop = True

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop

    def seconds_left(self, now: float) -> int | None:
        with self._lock:
            if self.total <= 0 or self.done < _ETA_MIN_DONE:
                return None
            per_item = (now - self._started) / self.done
            return round(per_item * max(self.total - self.done, 0))

    def bar_text(self) -> str:
        with self._lock:
            return lang(32474).format(self.done, self.total)

    def toast(self, title: str, stopped: bool, could_not_finish: bool) -> str:
        with self._lock:
            if stopped:
                return lang(32483).format(self.done, self.total, title)
            if could_not_finish:
                return lang(32484).format(title)
            if self.updated:
                return lang(32482).format(title, self.updated, self.total)
            if self.unchecked:
                return lang(32353).format(title)
            return lang(32351).format(title)

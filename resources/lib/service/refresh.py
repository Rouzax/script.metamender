"""Update now on a TV show or a season: refresh the show record (for a show)
and every episode under it, with a progress window that can be stopped or
sent to Kodi's background progress bar.

Logging:
    Logger: 'refresh'
"""
from __future__ import annotations

from dataclasses import dataclass

from resources.lib.data.kodi import (
    get_episode_show_id,
    get_item_by_id,
    get_season_info,
)
from resources.lib.data.media_types import TVSHOW
from resources.lib.utils import get_logger

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

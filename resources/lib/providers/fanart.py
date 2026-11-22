"""
fanart.tv provider for MetaMender poster lookups.

Movies are looked up by TMDB id (IMDb id as the fallback), shows by TVDB id.
fanart.tv answers an id it does not know with HTTP 200 and an empty object,
which is a clean "no posters", not an error.

Auth:
    The add-on's project key is always sent; the user's personal key
    (``client_key``) only when set, which lifts fanart.tv's delay on new
    uploads. A 401 raises FanartAuthError so the caller can stop using
    fanart.tv for the cycle; the caller logs it once.

Logging:
    Logger: 'fanart'
    Key events:
        - fanart.movie: movie posters fetched
        - fanart.show: show and season posters fetched
    Failures from providers/http.py log as `fanart.movie.transient` etc.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from resources.lib.constants import FANART_BASE_URL
from resources.lib.engine.art import Poster
from resources.lib.providers.http import provider_get
from resources.lib.utils import (
    ApiKeyError,
    FanartAuthError,
    get_logger,
    log_timing,
)

log = get_logger('fanart')

# fanart.tv marks a textless image with language "00".
_TEXTLESS = ("00", "")


def _get(
    path: str, label: str, project_key: str, client_key: str, **context: Any,
) -> Optional[Dict[str, Any]]:
    params = {"api_key": project_key}
    if client_key:
        params["client_key"] = client_key
    with log_timing(log, label, **context):
        try:
            return provider_get(
                "{}/{}".format(FANART_BASE_URL, path), logger=log, label=label,
                params=params, provider="fanart",
            )
        except ApiKeyError:
            raise FanartAuthError("fanart.tv rejected the API key")


def _poster(item: Dict[str, Any]) -> Optional[Poster]:
    """One poster from a fanart.tv item; None when it has no usable url or
    likes count. ``likes`` arrives as a string."""
    url = item.get("url")
    if not url:
        return None
    try:
        likes = int(item.get("likes") or 0)
    except (TypeError, ValueError):
        return None
    lang = item.get("lang")
    return Poster(
        url=url, provider="fanart",
        lang=None if lang in _TEXTLESS or lang is None else lang,
        score=(likes,),
    )


def _posters(items: Any) -> List[Poster]:
    parsed = (_poster(item) for item in items or [])
    return [p for p in parsed if p is not None]


def fetch_movie_posters(
    tmdb_id: Optional[int], imdb_id: Optional[str],
    project_key: str, client_key: str,
) -> Optional[List[Poster]]:
    """Movie posters, TMDB id preferred over IMDb id. [] when fanart.tv has
    none; None when there is no id to ask with or the lookup answered 404.
    Raises FanartAuthError on HTTP 401 and TransientProviderError when
    fanart.tv could not be reached."""
    movie_id = str(tmdb_id) if tmdb_id else (imdb_id or "")
    if not movie_id:
        return None
    data = _get(
        "movies/{}".format(movie_id), "fanart.movie", project_key, client_key,
        movie_id=movie_id,
    )
    if data is None:
        return None
    posters = _posters(data.get("movieposter"))
    log.info(
        "Movie posters fetched", event="fanart.movie", movie_id=movie_id,
        posters=len(posters),
    )
    return posters


def fetch_show_posters(
    tvdb_id: int, project_key: str, client_key: str,
) -> Optional[Dict[str, Any]]:
    """Show posters as {"show": [Poster], "seasons": {season: [Poster]}}
    (season-agnostic "all" posters are dropped), or None on 404. Raises
    FanartAuthError on HTTP 401 and TransientProviderError when fanart.tv
    could not be reached."""
    data = _get(
        "tv/{}".format(tvdb_id), "fanart.show", project_key, client_key,
        tvdb_id=tvdb_id,
    )
    if data is None:
        return None
    seasons: Dict[int, List[Poster]] = {}
    for item in data.get("seasonposter") or []:
        try:
            season = int(item.get("season"))
        except (TypeError, ValueError):
            continue
        poster = _poster(item)
        if poster is not None:
            seasons.setdefault(season, []).append(poster)
    show = _posters(data.get("tvposter"))
    log.info(
        "Show posters fetched", event="fanart.show", tvdb_id=tvdb_id,
        posters=len(show), seasons=len(seasons),
    )
    return {"show": show, "seasons": seasons}

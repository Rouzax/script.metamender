"""
TheTVDB v4 provider for MetaMender metadata lookups.

Fetches series names, translations and aliases (for show text fields and for
verifying untyped TVDB ids) and the full episode list of a series in one
language (for episode titles and plots).

Auth:
    The add-on's project key is exchanged for a bearer token at login. The
    token is cached for the process; a 401 triggers one fresh login and one
    retry. A login rejection, or a 401 that survives the fresh login, raises
    TvdbAuthError so the caller can disable TVDB without touching other
    providers; the caller logs it once.

Logging:
    Logger: 'tvdb'
    Key events:
        - tvdb.login: bearer token obtained
        - tvdb.series: series names and translations fetched
        - tvdb.episodes: full episode list fetched
"""
from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from resources.lib.constants import TVDB_API_KEY, TVDB_BASE_URL
from resources.lib.providers.http import provider_get, provider_post
from resources.lib.utils import (
    ApiKeyError,
    TransientProviderError,
    TvdbAuthError,
    get_logger,
)

log = get_logger('tvdb')

_PROVIDER = "tvdb"
_FALLBACK_LANG = "eng"
# A series has far fewer than this many 500-episode pages; the cap only stops
# a server that never returns links.next=null from looping forever.
_MAX_EPISODE_PAGES = 100

_token: Optional[str] = None
_token_lock = threading.Lock()


def _reset_token() -> None:
    """Forget the cached bearer token (tests, and the forced re-login)."""
    global _token
    with _token_lock:
        _token = None


def _login() -> str:
    """Exchange the project key for a bearer token and cache it."""
    global _token
    try:
        body = provider_post(
            "{}/login".format(TVDB_BASE_URL),
            log,
            "tvdb.login",
            {"apikey": TVDB_API_KEY},
            provider=_PROVIDER,
            transient_400=True,
        )
    except ApiKeyError:
        raise TvdbAuthError("TVDB login rejected")
    token = ((body or {}).get("data") or {}).get("token")
    if not token:
        raise TransientProviderError("tvdb login returned no token")
    with _token_lock:
        _token = token
    log.debug("TVDB login ok", event="tvdb.login")
    return token


def _bearer() -> Dict[str, str]:
    token = _token or _login()
    return {"Authorization": "Bearer {}".format(token)}


def _get(path: str) -> Optional[Dict[str, Any]]:
    """GET a TVDB v4 path with the bearer token; None on 404."""
    url = "{}{}".format(TVDB_BASE_URL, path)
    for attempt in (0, 1):
        try:
            return provider_get(
                url,
                log,
                "tvdb.request",
                provider=_PROVIDER,
                headers=_bearer(),
                transient_400=True,
            )
        except ApiKeyError:
            if attempt:
                raise TvdbAuthError("TVDB refused a fresh token")
            _reset_token()
    raise AssertionError("unreachable")  # pragma: no cover


def _translation(tvdb_id: str, lang: str) -> Dict[str, str]:
    body = _get("/series/{}/translations/{}".format(tvdb_id, lang))
    data = (body or {}).get("data") or {}
    return {
        "name": data.get("name") or "",
        "overview": data.get("overview") or "",
    }


def fetch_series(tvdb_id: str, lang: str) -> Optional[Dict[str, Any]]:
    """Fetch a series' text in `lang` (a TVDB code such as "nld") and its names.

    Returns {"name", "overview", "names"} or None when TVDB has no such
    series. name/overview are the `lang` translation only ("" when TVDB has
    none), never the primary-language text, so a caller cannot mistake a
    foreign original for a native value. `names` holds the primary name, the
    `lang` and English translations and every alias, for id verification.
    """
    series = _get("/series/{}".format(tvdb_id))
    if series is None:
        return None
    primary = (series.get("data") or {}).get("name") or ""
    native = _translation(tvdb_id, lang)
    english = native if lang == _FALLBACK_LANG else _translation(tvdb_id, _FALLBACK_LANG)
    extended = _get("/series/{}/extended?short=true".format(tvdb_id))
    aliases = ((extended or {}).get("data") or {}).get("aliases") or []

    names: List[str] = []
    for candidate in [primary, native["name"], english["name"]] + [
        a.get("name") or "" for a in aliases
    ]:
        if candidate and candidate not in names:
            names.append(candidate)
    log.debug("TVDB series fetched", event="tvdb.series", tvdb_id=tvdb_id, lang=lang)
    return {"name": native["name"], "overview": native["overview"], "names": names}


def fetch_series_episodes(
    tvdb_id: str, lang: str
) -> Optional[Dict[str, Dict[str, str]]]:
    """Fetch every episode of a series in `lang`, keyed by TVDB episode id.

    Returns {episode_id: {"title", "plot"}}, or None when TVDB has no such
    series. Title and plot are the `lang` translation only: TVDB returns null
    for an episode with no translation in `lang` (verified live, no fallback
    to the original or English text), which becomes "". Records without an id
    are skipped. Raises TransientProviderError when any page
    fails or vanishes, so a partial list is never returned or cached.
    """
    episodes: Dict[str, Dict[str, str]] = {}
    for page in range(_MAX_EPISODE_PAGES):
        body = _get("/series/{}/episodes/default/{}?page={}".format(tvdb_id, lang, page))
        if body is None:
            if page == 0:
                return None
            raise TransientProviderError("tvdb episode page {} missing".format(page))
        for ep in (body.get("data") or {}).get("episodes") or []:
            if ep.get("id") is None:
                continue
            episodes[str(ep["id"])] = {
                "title": ep.get("name") or "",
                "plot": ep.get("overview") or "",
            }
        if not (body.get("links") or {}).get("next"):
            log.debug(
                "TVDB episodes fetched",
                event="tvdb.episodes",
                tvdb_id=tvdb_id,
                lang=lang,
                episodes=len(episodes),
            )
            return episodes
    raise TransientProviderError("tvdb episode paging did not end")

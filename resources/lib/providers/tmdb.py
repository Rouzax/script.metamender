"""
TMDB provider for MetaMender metadata lookups.

Handles all TMDB API calls: movies, TV shows, seasons, and ID resolution.
Fetches full metadata (rating, plot, tagline, credits, trailers,
certifications) rather than just certifications.

Logging:
    Logger: 'tmdb'
    Key events:
        - tmdb.movie: Movie metadata fetched successfully
        - tmdb.season: Season metadata fetched successfully
        - tmdb.tvshow: TV show metadata fetched successfully
        - tmdb.resolve_id: External ID resolved to TMDB ID
        - tmdb.error: API request or parsing failure
        - tmdb.auth_error: Invalid API key (HTTP 401)
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

from resources.lib.constants import METAMENDER_USER_AGENT, TMDB_BASE_URL
from resources.lib.providers.http import provider_get
from resources.lib.utils import ApiKeyError, get_logger, log_timing

log = get_logger('tmdb')

# TMDB uses different certification strings for SE TV vs SE movies.
# Normalize TV format ("Fran 7 ar") to movie format ("7") so the rest
# of the pipeline only sees one set of values per country.
_CERT_NORMALIZE = {
    "SE": {
        "Från 7 år": "7",
        "Från 11 år": "11",
        "Från 15 år": "15",
    },
}


# =============================================================================
# Internal Helpers
# =============================================================================


def _parse_directors(credits: Dict) -> List[str]:
    """Extract director names from credits.crew where job is Director."""
    return [
        member["name"]
        for member in credits.get("crew", [])
        if member.get("job") == "Director"
    ]


def _parse_writers(credits: Dict) -> List[str]:
    """Extract deduplicated writer names from credits.crew in the Writing department."""
    seen = set()  # type: set
    writers = []  # type: List[str]
    for member in credits.get("crew", []):
        if member.get("department") == "Writing":
            name = member.get("name", "")
            if name and name not in seen:
                seen.add(name)
                writers.append(name)
    return writers


def _parse_trailer(videos: Dict) -> Optional[str]:
    """Find the first YouTube trailer and return a Kodi plugin URL."""
    for video in videos.get("results", []):
        if video.get("type") == "Trailer" and video.get("site") == "YouTube":
            key = video.get("key")
            if key:
                return "plugin://plugin.video.youtube/?action=play_video&videoid={}".format(key)
    return None


def _parse_movie_certifications(release_dates: Dict) -> Dict[str, str]:
    """Extract {country: rating} from movie release_dates.results.

    Takes the first non-empty certification per country.
    """
    certs = {}  # type: Dict[str, str]
    for entry in release_dates.get("results", []):
        country = entry.get("iso_3166_1", "")
        if not country or country in certs:
            continue
        for rd in entry.get("release_dates", []):
            cert = rd.get("certification", "")
            if cert:
                certs[country] = cert
                break
    return certs


def _parse_tv_certifications(content_ratings: Dict) -> Dict[str, str]:
    """Extract {country: rating} from TV content_ratings.results."""
    certs = {}  # type: Dict[str, str]
    for entry in content_ratings.get("results", []):
        country = entry.get("iso_3166_1", "")
        rating = entry.get("rating", "")
        if country and rating and country not in certs:
            certs[country] = rating
    return certs


def _normalize_certs(certs: Dict[str, str]) -> Dict[str, str]:
    """Normalize country-specific certification aliases to canonical values."""
    for country, table in _CERT_NORMALIZE.items():
        if country in certs:
            certs[country] = table.get(certs[country], certs[country])
    return certs


# =============================================================================
# Public API
# =============================================================================


def resolve_id(
    api_key: str,
    tvdb_id: Optional[str] = None,
    imdb_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve a TMDB ID from an external ID using the /find endpoint.

    Tries tvdb_id first, then imdb_id. Returns the TMDB ID as a string,
    or None if resolution fails. Raises ApiKeyError on HTTP 401.
    """
    external_id = None  # type: Optional[str]
    source = None  # type: Optional[str]
    if tvdb_id:
        external_id = tvdb_id
        source = "tvdb_id"
    elif imdb_id:
        external_id = imdb_id
        source = "imdb_id"

    if not external_id:
        return None

    url = "{}/find/{}".format(TMDB_BASE_URL, external_id)

    try:
        with log_timing(log, "tmdb.resolve_id", tmdb_id=external_id):
            resp = requests.get(
                url,
                params={"api_key": api_key, "external_source": source},
                timeout=10,
                headers={"User-Agent": METAMENDER_USER_AGENT},
            )
    except requests.RequestException:
        log.exception(
            "Resolve request failed",
            event="tmdb.error",
            tmdb_id=external_id,
        )
        return None

    if resp.status_code == 401:
        raise ApiKeyError("tmdb")

    if resp.status_code != 200:
        log.warning(
            "Resolve returned unexpected status",
            event="tmdb.error",
            tmdb_id=external_id,
            status_code=resp.status_code,
        )
        return None

    try:
        data = resp.json()
    except ValueError:
        log.exception(
            "Invalid JSON in resolve response",
            event="tmdb.error",
            tmdb_id=external_id,
        )
        return None

    for key in ("movie_results", "tv_results"):
        results = data.get(key, [])
        if results:
            resolved_id = results[0].get("id")
            if resolved_id is not None:
                log.info(
                    "Resolved TMDB ID",
                    event="tmdb.resolve_id",
                    tmdb_id=str(resolved_id),
                    external_id=external_id,
                    source=source,
                )
                return str(resolved_id)

    log.debug(
        "Could not resolve TMDB ID",
        tmdb_id=external_id,
        source=source,
    )
    return None


def fetch_movie(
    tmdb_id: str,
    api_key: str,
    language: str = "en-US",
) -> Optional[Dict]:
    """Fetch full movie metadata from TMDB.

    Uses append_to_response to get credits, videos, and release_dates
    in a single API call. Returns a standardized metadata dict or None
    on failure. Raises ApiKeyError on HTTP 401.
    """
    url = "{}/movie/{}".format(TMDB_BASE_URL, tmdb_id)
    params = {
        "api_key": api_key,
        "language": language,
        "append_to_response": "credits,videos,release_dates",
    }

    rpc_start = time.perf_counter()
    with log_timing(log, "tmdb.movie", tmdb_id=tmdb_id):
        data = provider_get(
            url, logger=log, label="tmdb.movie", params=params, provider="tmdb",
        )
    if data is None:
        return None

    elapsed_ms = int((time.perf_counter() - rpc_start) * 1000)

    credits = data.get("credits", {})
    videos = data.get("videos", {})
    release_dates = data.get("release_dates", {})

    raw_certs = _parse_movie_certifications(release_dates)
    certifications = _normalize_certs(raw_certs)

    result = {
        "rating": data.get("vote_average", 0.0),
        "votes": data.get("vote_count", 0),
        "plot": data.get("overview", ""),
        "tagline": data.get("tagline", ""),
        "directors": _parse_directors(credits),
        "writers": _parse_writers(credits),
        "genres": [g["name"] for g in data.get("genres", []) if "name" in g],
        "studios": [
            c["name"] for c in data.get("production_companies", []) if "name" in c
        ],
        "countries": [
            c["name"] for c in data.get("production_countries", []) if "name" in c
        ],
        "trailer": _parse_trailer(videos),
        "runtime": data.get("runtime"),
        "premiered": data.get("release_date", ""),
        "original_title": data.get("original_title", ""),
        "certifications": certifications,
        "imdb_id": data.get("imdb_id", ""),
    }

    log.info(
        "Movie metadata fetched",
        event="tmdb.movie",
        tmdb_id=tmdb_id,
        title=data.get("title", ""),
        field_count=len(result),
        duration_ms=elapsed_ms,
    )

    return result


def fetch_season(
    show_tmdb_id: str,
    season_number: int,
    api_key: str,
    language: str = "en-US",
) -> Optional[List[Dict]]:
    """Fetch season metadata from TMDB, returning a list of episode dicts.

    Each episode dict contains title, rating, votes, plot, directors,
    writers, firstaired, and runtime. Returns None on failure.
    Raises ApiKeyError on HTTP 401.
    """
    url = "{}/tv/{}/season/{}".format(TMDB_BASE_URL, show_tmdb_id, season_number)
    params = {
        "api_key": api_key,
        "language": language,
    }

    rpc_start = time.perf_counter()
    with log_timing(
        log, "tmdb.season",
        tmdb_id=show_tmdb_id, season=season_number,
    ):
        data = provider_get(
            url, logger=log, label="tmdb.season", params=params, provider="tmdb",
        )
    if data is None:
        return None

    elapsed_ms = int((time.perf_counter() - rpc_start) * 1000)

    episodes = []  # type: List[Dict]
    for ep in data.get("episodes", []):
        crew = {"crew": ep.get("crew", [])}
        ep_id = ep.get("id")
        episodes.append({
            "tmdb_id": str(ep_id) if ep_id is not None else "",
            "episode_number": ep.get("episode_number", 0),
            "season_number": ep.get("season_number", season_number),
            "title": ep.get("name", ""),
            "rating": ep.get("vote_average", 0.0),
            "votes": ep.get("vote_count", 0),
            "plot": ep.get("overview", ""),
            "directors": _parse_directors(crew),
            "writers": _parse_writers(crew),
            "firstaired": ep.get("air_date", ""),
            "runtime": ep.get("runtime"),
        })

    log.info(
        "Season metadata fetched",
        event="tmdb.season",
        tmdb_id=show_tmdb_id,
        season=season_number,
        episodes=len(episodes),
        duration_ms=elapsed_ms,
    )

    return episodes


def fetch_tvshow(
    tmdb_id: str,
    api_key: str,
    language: str = "en-US",
) -> Optional[Dict]:
    """Fetch TV show metadata from TMDB.

    Uses append_to_response to get content_ratings in a single call.
    Returns a standardized metadata dict or None on failure.
    Raises ApiKeyError on HTTP 401.
    """
    url = "{}/tv/{}".format(TMDB_BASE_URL, tmdb_id)
    params = {
        "api_key": api_key,
        "language": language,
        "append_to_response": "content_ratings,external_ids",
    }

    rpc_start = time.perf_counter()
    with log_timing(log, "tmdb.tvshow", tmdb_id=tmdb_id):
        data = provider_get(
            url, logger=log, label="tmdb.tvshow", params=params, provider="tmdb",
        )
    if data is None:
        return None

    elapsed_ms = int((time.perf_counter() - rpc_start) * 1000)

    content_ratings = data.get("content_ratings", {})
    raw_certs = _parse_tv_certifications(content_ratings)
    certifications = _normalize_certs(raw_certs)

    result = {
        "rating": data.get("vote_average", 0.0),
        "votes": data.get("vote_count", 0),
        "plot": data.get("overview", ""),
        "genres": [g["name"] for g in data.get("genres", []) if "name" in g],
        "certifications": certifications,
        "imdb_id": data.get("external_ids", {}).get("imdb_id", ""),
    }

    log.info(
        "TV show metadata fetched",
        event="tmdb.tvshow",
        tmdb_id=tmdb_id,
        title=data.get("name", ""),
        field_count=len(result),
        duration_ms=elapsed_ms,
    )

    return result


def fetch_episode_external_ids(
    show_tmdb_id: str,
    season_number: int,
    episode_number: int,
    api_key: str,
) -> Optional[str]:
    """Fetch the IMDB ID for a specific episode from TMDB.

    Returns the IMDB ID string (e.g., "tt1234567") or None if not
    available or on failure.
    """
    url = "{}/tv/{}/season/{}/episode/{}/external_ids".format(
        TMDB_BASE_URL, show_tmdb_id, season_number, episode_number,
    )

    with log_timing(
        log, "tmdb.episode_ids",
        tmdb_id=show_tmdb_id, season=season_number, episode=episode_number,
    ):
        data = provider_get(
            url, logger=log, label="tmdb.episode_ids",
            params={"api_key": api_key}, provider="tmdb",
        )
    if data is None:
        return None

    imdb_id = data.get("imdb_id")
    if imdb_id:
        return str(imdb_id)
    return None

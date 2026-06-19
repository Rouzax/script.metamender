"""
IMDb API provider for MetaMender metadata lookups.

Uses imdbapi.dev (no authentication, no daily limit) to fetch movie,
episode, season, and certificate data. Returns standardized dicts
matching the TMDB provider format so the scan engine can use either
provider interchangeably.

Logging:
    Logger: 'imdb'
    Key events:
        - imdb.movie: Movie/show metadata fetched successfully
        - imdb.episode: Episode metadata fetched successfully
        - imdb.season: Season metadata fetched successfully
        - imdb.certificates: Certificates fetched successfully
        - imdb.error: API request or parsing failure
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from resources.lib.constants import IMDBAPI_BASE_URL
from resources.lib.providers.http import provider_get
from resources.lib.utils import get_logger, log_timing

log = get_logger('imdb')


def _extract_names(people: Optional[List[Dict[str, Any]]]) -> List[str]:
    """Extract displayName strings from a list of person objects."""
    if not people:
        return []
    return [
        p["displayName"]
        for p in people
        if isinstance(p, dict) and p.get("displayName")
    ]


def _format_release_date(release_date: Optional[Dict[str, Any]]) -> str:
    """Format a releaseDate object ({year, month, day}) as YYYY-MM-DD."""
    if not release_date:
        return ""
    year = release_date.get("year")
    month = release_date.get("month")
    day = release_date.get("day")
    if not year:
        return ""
    if month and day:
        return "{:04d}-{:02d}-{:02d}".format(year, month, day)
    return str(year)


def _runtime_minutes(seconds: Optional[int]) -> Optional[int]:
    """Convert runtimeSeconds to minutes, rounding to nearest."""
    if not seconds or seconds <= 0:
        return None
    return round(seconds / 60)


def _parse_rating(data: Dict[str, Any]) -> float:
    """Extract aggregateRating from a rating object, defaulting to 0.0."""
    rating = data.get("rating")
    if isinstance(rating, dict):
        return rating.get("aggregateRating", 0.0)
    return 0.0


def _parse_votes(data: Dict[str, Any]) -> int:
    """Extract voteCount from a rating object, defaulting to 0."""
    rating = data.get("rating")
    if isinstance(rating, dict):
        return rating.get("voteCount", 0)
    return 0


# =============================================================================
# Public API
# =============================================================================


def fetch_movie(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Fetch movie or TV show metadata from imdbapi.dev.

    Works for both movies and TV shows (same endpoint). Returns a
    standardized dict matching the TMDB provider format, or None on
    failure.
    """
    url = "{}/titles/{}".format(IMDBAPI_BASE_URL, imdb_id)

    with log_timing(log, "imdb.movie", imdb_id=imdb_id):
        data = provider_get(
            url, logger=log, label="imdb.movie", provider="imdb",
        )
    if data is None:
        return None

    result = {
        "rating": _parse_rating(data),
        "votes": _parse_votes(data),
        "plot": data.get("plot", ""),
        "directors": _extract_names(data.get("directors")),
        "writers": _extract_names(data.get("writers")),
        "genres": data.get("genres", []),
        "countries": [
            c["name"]
            for c in data.get("originCountries", [])
            if isinstance(c, dict) and c.get("name")
        ],
        "runtime": _runtime_minutes(data.get("runtimeSeconds")),
        "premiered": str(data.get("startYear", "")) if data.get("startYear") else "",
    }

    log.info(
        "Movie metadata fetched",
        event="imdb.movie",
        imdb_id=imdb_id,
        title=data.get("primaryTitle", ""),
        field_count=len(result),
    )

    return result


def fetch_episode(imdb_id: str) -> Optional[Dict[str, Any]]:
    """Fetch episode metadata directly by IMDB ID.

    Calls the same /titles endpoint as fetch_movie but returns a dict
    matching the TMDB episode format. This is the primary safe path for
    episode matching: called with the episode's own IMDB ID, no season
    lookup or S/E number matching needed.
    """
    url = "{}/titles/{}".format(IMDBAPI_BASE_URL, imdb_id)

    with log_timing(log, "imdb.episode", imdb_id=imdb_id):
        data = provider_get(
            url, logger=log, label="imdb.episode", provider="imdb",
        )
    if data is None:
        return None


    result = {
        "rating": _parse_rating(data),
        "votes": _parse_votes(data),
        "plot": data.get("plot", ""),
        "title": data.get("primaryTitle", ""),
        "directors": _extract_names(data.get("directors")),
        "writers": _extract_names(data.get("writers")),
        "runtime": _runtime_minutes(data.get("runtimeSeconds")),
    }

    log.info(
        "Episode metadata fetched",
        event="imdb.episode",
        imdb_id=imdb_id,
        title=data.get("primaryTitle", ""),
    )

    return result


def fetch_season(
    show_imdb_id: str,
    season: int,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch all episodes for a season from imdbapi.dev.

    Auto-paginates with pageSize=50 until all episodes are collected.
    Returns a list of episode dicts, or None on failure.
    """
    episodes = []  # type: List[Dict[str, Any]]
    page_token = None  # type: Optional[str]


    with log_timing(
        log, "imdb.season",
        imdb_id=show_imdb_id, season=season,
    ):
        while True:
            params = {
                "season": str(season),
                "pageSize": "50",
            }  # type: Dict[str, str]
            if page_token:
                params["pageToken"] = page_token

            url = "{}/titles/{}/episodes".format(IMDBAPI_BASE_URL, show_imdb_id)

            data = provider_get(
                url, logger=log, label="imdb.season",
                params=params, provider="imdb",
            )
            if data is None:
                if not episodes:
                    return None
                # A later page returned no data; keep the episodes already
                # collected rather than discarding the whole season.
                log.warning(
                    "Season pagination truncated; returning partial result",
                    event="imdb.season_truncated",
                    imdb_id=show_imdb_id,
                    season=season,
                    episodes=len(episodes),
                )
                break

            for ep in data.get("episodes", []):
                episodes.append({
                    "imdb_id": ep.get("id", ""),
                    "episode_number": ep.get("episodeNumber", 0),
                    "season_number": int(ep.get("season", season)),
                    "title": ep.get("title", ""),
                    "rating": _parse_rating(ep),
                    "votes": _parse_votes(ep),
                    "plot": ep.get("plot", ""),
                    "runtime": _runtime_minutes(ep.get("runtimeSeconds")),
                    "firstaired": _format_release_date(ep.get("releaseDate")),
                })

            next_token = data.get("nextPageToken")
            if not next_token:
                break
            page_token = next_token


    log.info(
        "Season metadata fetched",
        event="imdb.season",
        imdb_id=show_imdb_id,
        season=season,
        episodes=len(episodes),
    )

    return episodes


def fetch_certificates(
    imdb_id: str,
) -> Optional[Dict[str, List[Dict[str, Any]]]]:
    """Fetch age certificates for a title from imdbapi.dev.

    Returns all entries grouped by country code. Multiple entries per
    country are preserved (e.g., original rating vs re-rating, or
    province-specific ratings). Phase 8 cert module handles selection.

    Returns None on failure.
    """
    url = "{}/titles/{}/certificates".format(IMDBAPI_BASE_URL, imdb_id)

    with log_timing(log, "imdb.certificates", imdb_id=imdb_id):
        data = provider_get(
            url, logger=log, label="imdb.certificates", provider="imdb",
        )
    if data is None:
        return None


    grouped = {}  # type: Dict[str, List[Dict[str, Any]]]
    for cert in data.get("certificates", []):
        country = cert.get("country")
        if not isinstance(country, dict):
            continue
        code = country.get("code", "")
        rating = cert.get("rating", "")
        if not code or not rating:
            continue
        if code not in grouped:
            grouped[code] = []
        grouped[code].append({
            "rating": rating,
            "attributes": cert.get("attributes", []),
        })

    log.info(
        "Certificates fetched",
        event="imdb.certificates",
        imdb_id=imdb_id,
        countries=len(grouped),
    )

    return grouped

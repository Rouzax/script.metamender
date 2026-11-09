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

from typing import Any, Dict, List, Optional

from resources.lib.constants import TMDB_BASE_URL
from resources.lib.providers.http import provider_get
from resources.lib.utils import get_logger, log_timing

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
        if member.get("job") == "Director" and member.get("name")
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

    Tries tvdb_id first, then imdb_id. Returns the TMDB ID as a string, or
    None when the provider answered and had no match (404, or empty results).
    Raises ApiKeyError on HTTP 401 and TransientProviderError when the provider
    could not be reached, so a failure is never mistaken for "no match".
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

    # Routed through provider_get like every other outbound call, so a 5xx,
    # timeout or malformed response raises TransientProviderError and counts
    # toward the circuit breaker. Returning None on those would report a
    # provider outage to the scan engine as a clean "no match".
    with log_timing(log, "tmdb.resolve_id", tmdb_id=external_id):
        data = provider_get(
            url,
            logger=log,
            label="tmdb.resolve_id",
            params={"api_key": api_key, "external_source": source},
            provider="tmdb",
        )

    if data is None:
        return None

    # Intentionally show-level only: "tv_episode_results" is omitted. Episodes
    # are matched by their own TMDB/IMDB id (no /find), so resolve_id never
    # needs episode-level resolution. (The spec mentions tv_episode_results;
    # that predates the ID-only episode decision and does not apply here.)
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

    with log_timing(log, "tmdb.movie", tmdb_id=tmdb_id):
        data = provider_get(
            url, logger=log, label="tmdb.movie", params=params, provider="tmdb",
        )
    if data is None:
        return None


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

    with log_timing(
        log, "tmdb.season",
        tmdb_id=show_tmdb_id, season=season_number,
    ):
        data = provider_get(
            url, logger=log, label="tmdb.season", params=params, provider="tmdb",
        )
    if data is None:
        return None


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

    with log_timing(log, "tmdb.tvshow", tmdb_id=tmdb_id):
        data = provider_get(
            url, logger=log, label="tmdb.tvshow", params=params, provider="tmdb",
        )
    if data is None:
        return None


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
    )

    return result


def _clean_ids(data: Dict[str, Any]) -> Dict[str, str]:
    """Pick imdb_id and tvdb_id from an external_ids payload as strings.

    TMDB returns tvdb_id as an int; keys with empty values are left out.
    """
    ids = {}  # type: Dict[str, str]
    for key in ("imdb_id", "tvdb_id"):
        value = data.get(key)
        if value:
            ids[key] = str(value)
    return ids


def fetch_episode_external_ids(
    show_tmdb_id: str,
    season_number: int,
    episode_number: int,
    api_key: str,
) -> Optional[Dict[str, str]]:
    """Fetch the IMDb and TVDB IDs for a specific episode from TMDB.

    Returns a dict with "imdb_id" and/or "tvdb_id" (only non-empty ones), or
    None when TMDB has neither. Raises TransientProviderError when TMDB could
    not be reached.
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

    ids = _clean_ids(data)
    log.info(
        "Episode external IDs fetched",
        event="tmdb.episode_ids",
        tmdb_id=show_tmdb_id,
        season=season_number,
        episode=episode_number,
        has_imdb="imdb_id" in ids,
        has_tvdb="tvdb_id" in ids,
    )
    return ids or None


def find_episode_by_tvdb(
    api_key: str,
    tvdb_episode_id: str,
) -> Optional[Dict[str, Any]]:
    """Find a TMDB episode from its TVDB episode ID via /find.

    Returns {"tmdb_id", "show_tmdb_id", "season", "episode"} or None when TMDB
    has no match, or only matches lacking any of those fields. Raises TransientProviderError when TMDB could not be reached.
    """
    url = "{}/find/{}".format(TMDB_BASE_URL, tvdb_episode_id)
    with log_timing(log, "tmdb.find_episode", tvdb_id=tvdb_episode_id):
        data = provider_get(
            url, logger=log, label="tmdb.find_episode",
            params={"api_key": api_key, "external_source": "tvdb_id"},
            provider="tmdb",
        )
    if data is None:
        return None

    for result in data.get("tv_episode_results") or []:
        # Every field is required: a missing season number read as 0 would
        # address the show's Specials.
        if any(result.get(k) is None for k in (
            "id", "show_id", "season_number", "episode_number",
        )):
            continue
        found = {
            "tmdb_id": str(result["id"]),
            "show_tmdb_id": str(result["show_id"]),
            "season": int(result["season_number"]),
            "episode": int(result["episode_number"]),
        }
        log.info(
            "Episode found by TVDB ID",
            event="tmdb.find_episode",
            tvdb_id=tvdb_episode_id,
            tmdb_id=found["tmdb_id"],
        )
        return found
    return None


def fetch_tvshow_external_ids(
    api_key: str,
    tmdb_id: str,
) -> Optional[Dict[str, str]]:
    """Fetch the IMDb and TVDB IDs of a show from TMDB.

    Returns a dict with "imdb_id" and/or "tvdb_id" (only non-empty ones), or
    None when TMDB has neither. Raises TransientProviderError when TMDB could
    not be reached.
    """
    url = "{}/tv/{}/external_ids".format(TMDB_BASE_URL, tmdb_id)
    with log_timing(log, "tmdb.tvshow_ids", tmdb_id=tmdb_id):
        data = provider_get(
            url, logger=log, label="tmdb.tvshow_ids",
            params={"api_key": api_key}, provider="tmdb",
        )
    if data is None:
        return None

    ids = _clean_ids(data)
    log.info(
        "Show external IDs fetched",
        event="tmdb.tvshow_ids",
        tmdb_id=tmdb_id,
        has_imdb="imdb_id" in ids,
        has_tvdb="tvdb_id" in ids,
    )
    return ids or None

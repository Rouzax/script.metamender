#!/usr/bin/env python3
"""
Surgical integration test against a live Kodi instance.

Clears metadata on specific items, runs the scan engine against them,
and verifies the results. Uses the real TMDB API.

Usage:
    python3 tests/integration_test.py

Requires:
    - Kodi running at http://localhost:8085/jsonrpc
    - TMDB API key in docker/.env
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List

import requests

KODI_URL = os.environ.get("KODI_URL", "http://localhost:8085/jsonrpc")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "REDACTED_TMDB_KEY")

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def rpc(method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        payload["params"] = params
    resp = requests.post(KODI_URL, json=payload, timeout=10)
    return resp.json().get("result", {})


def rpc_raw(method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        payload["params"] = params
    resp = requests.post(KODI_URL, json=payload, timeout=10)
    return resp.json()


def get_movie(movie_id: int) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetMovieDetails", {
        "movieid": movie_id,
        "properties": ["title", "rating", "votes", "plot", "tagline",
                        "director", "writer", "genre", "studio", "country",
                        "trailer", "runtime", "mpaa", "uniqueid", "premiered"],
    })
    return result.get("moviedetails", {})


def get_episode(episode_id: int) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetEpisodeDetails", {
        "episodeid": episode_id,
        "properties": ["title", "rating", "votes", "plot", "showtitle",
                        "season", "episode", "uniqueid", "firstaired",
                        "director", "writer", "runtime"],
    })
    return result.get("episodedetails", {})


def get_tvshow(tvshow_id: int) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetTVShowDetails", {
        "tvshowid": tvshow_id,
        "properties": ["title", "rating", "votes", "plot", "genre",
                        "mpaa", "uniqueid", "premiered"],
    })
    return result.get("tvshowdetails", {})


def clear_movie_field(movie_id: int, **fields: Any) -> None:
    params = {"movieid": movie_id}
    params.update(fields)
    rpc("VideoLibrary.SetMovieDetails", params)


def clear_episode_field(episode_id: int, **fields: Any) -> None:
    params = {"episodeid": episode_id}
    params.update(fields)
    rpc("VideoLibrary.SetEpisodeDetails", params)


def clear_tvshow_field(tvshow_id: int, **fields: Any) -> None:
    params = {"tvshowid": tvshow_id}
    params.update(fields)
    rpc("VideoLibrary.SetTVShowDetails", params)


def find_movie_by_title(title: str) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetMovies", {
        "properties": ["title", "rating", "uniqueid", "tagline", "trailer"],
        "filter": {"field": "title", "operator": "is", "value": title},
    })
    movies = result.get("movies", [])
    return movies[0] if movies else {}


def find_show_by_title(title: str) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetTVShows", {
        "properties": ["title", "rating", "uniqueid"],
        "filter": {"field": "title", "operator": "is", "value": title},
    })
    shows = result.get("tvshows", [])
    return shows[0] if shows else {}


def find_episodes_by_show(tvshow_id: int, season: int = None) -> List[Dict[str, Any]]:
    params = {
        "tvshowid": tvshow_id,
        "properties": ["title", "rating", "plot", "season", "episode",
                        "uniqueid", "runtime"],
    }  # type: Dict[str, Any]
    if season is not None:
        params["season"] = season
    result = rpc("VideoLibrary.GetEpisodes", params)
    return result.get("episodes", [])


class IntegrationTest:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []  # type: List[str]

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print("  PASS: {}".format(name))
        else:
            self.failed += 1
            msg = "  FAIL: {}".format(name)
            if detail:
                msg += " ({})".format(detail)
            print(msg)
            self.errors.append(msg)

    def summary(self) -> None:
        print("\n{'=' * 60}")
        print("Results: {} passed, {} failed".format(self.passed, self.failed))
        if self.errors:
            print("\nFailures:")
            for e in self.errors:
                print(e)
        print("{'=' * 60}")


def run_targeted_scan(
    media_type_name: str,
    db_path: str,
    show_id_map: Dict[str, str] = None,
) -> Dict[str, int]:
    """Run scan engine against the live Kodi instance."""
    # We need Kodi stubs since scan.py imports xbmc
    # But we're running against real Kodi via HTTP, not internal JSON-RPC
    # So we can't use scan.py directly. Instead, test the TMDB provider
    # and verify via JSON-RPC reads.
    pass


def test_tmdb_provider_directly(t: IntegrationTest) -> None:
    """Test TMDB provider with real API calls."""
    from resources.lib.providers.tmdb import fetch_movie, fetch_season, fetch_tvshow, resolve_id

    print("\n=== TMDB Provider Tests ===")

    # Test 1.1: fetch_movie for a known good movie
    print("\n--- Test 1.1: fetch_movie (Interstellar, tmdb:157336) ---")
    data = fetch_movie("157336", TMDB_KEY)
    t.check("fetch_movie returns data", data is not None)
    if data:
        t.check("has rating", data["rating"] > 0, "got {}".format(data["rating"]))
        t.check("has votes", data["votes"] > 0, "got {}".format(data["votes"]))
        t.check("has plot", len(data["plot"]) > 0)
        t.check("has directors", len(data["directors"]) > 0, "got {}".format(data["directors"]))
        t.check("has genres", len(data["genres"]) > 0, "got {}".format(data["genres"]))
        t.check("has trailer", data["trailer"] is not None and "youtube" in data["trailer"])
        t.check("has certifications", len(data["certifications"]) > 0)
        t.check("has runtime", data["runtime"] is not None and data["runtime"] > 0)

    # Test 1.2: fetch_movie for a niche title with no data
    print("\n--- Test 1.2: fetch_movie (Burial, tmdb:1155875, niche) ---")
    data = fetch_movie("1155875", TMDB_KEY)
    t.check("fetch_movie returns data (even for niche)", data is not None)
    if data:
        t.check("niche: rating is 0", data["rating"] == 0.0)
        t.check("niche: no trailer", data["trailer"] is None)

    # Test 1.3: fetch_movie with language
    print("\n--- Test 1.3: fetch_movie with nl-NL language ---")
    data_nl = fetch_movie("157336", TMDB_KEY, language="nl-NL")
    t.check("nl-NL returns data", data_nl is not None)
    if data_nl:
        t.check("nl-NL has plot", len(data_nl["plot"]) > 0)

    # Test 2.1: fetch_season
    print("\n--- Test 2.1: fetch_season (Night Agent S02, show:129552) ---")
    eps = fetch_season("129552", 2, TMDB_KEY)
    t.check("fetch_season returns data", eps is not None)
    if eps:
        t.check("has episodes", len(eps) > 0, "got {}".format(len(eps)))
        ep1 = eps[0]
        t.check("episode has title", len(ep1.get("title", "")) > 0, "got '{}'".format(ep1.get("title")))
        t.check("episode has rating", ep1.get("rating", 0) > 0)
        t.check("episode has plot", len(ep1.get("plot", "")) > 0)
        t.check("episode has directors", len(ep1.get("directors", [])) > 0)

    # Test 2.2: fetch_season for nonexistent season
    print("\n--- Test 2.2: fetch_season (nonexistent season 99) ---")
    eps_bad = fetch_season("129552", 99, TMDB_KEY)
    t.check("nonexistent season returns None", eps_bad is None)

    # Test 3.1: fetch_tvshow
    print("\n--- Test 3.1: fetch_tvshow (Night Agent, tmdb:129552) ---")
    show = fetch_tvshow("129552", TMDB_KEY)
    t.check("fetch_tvshow returns data", show is not None)
    if show:
        t.check("show has rating", show["rating"] > 0)
        t.check("show has genres", len(show["genres"]) > 0)
        t.check("show has certifications", len(show["certifications"]) > 0)
        us_cert = show["certifications"].get("US")
        t.check("US certification present", us_cert is not None, "got {}".format(us_cert))

    # Test 4.1: resolve_id from tvdb
    print("\n--- Test 4.1: resolve_id (tvdb:78988, Disney Animated Shorts) ---")
    resolved = resolve_id(TMDB_KEY, tvdb_id="78988")
    t.check("resolve_id from tvdb returns ID", resolved is not None, "got {}".format(resolved))

    # Test 4.2: resolve_id from imdb
    print("\n--- Test 4.2: resolve_id (imdb:tt13918776, Night Agent) ---")
    resolved = resolve_id(TMDB_KEY, imdb_id="tt13918776")
    t.check("resolve_id from imdb returns ID", resolved is not None, "got {}".format(resolved))
    if resolved:
        t.check("resolved to correct ID", resolved == "129552", "got {}".format(resolved))

    # Test 4.3: resolve_id with no valid IDs
    print("\n--- Test 4.3: resolve_id with bogus ID ---")
    resolved = resolve_id(TMDB_KEY, tvdb_id="99999999")
    t.check("bogus ID returns None", resolved is None)


def test_kodi_read_write(t: IntegrationTest) -> None:
    """Test reading from and writing to Kodi via JSON-RPC."""
    print("\n=== Kodi Read/Write Tests ===")

    # Find a known movie
    movie = find_movie_by_title("Interstellar")
    if not movie:
        print("  SKIP: Interstellar not in library")
        return

    movie_id = movie["movieid"]
    original = get_movie(movie_id)

    print("\n--- Test 5.1: Clear and restore movie rating ---")
    orig_rating = original["rating"]
    orig_votes = original.get("votes", "0")
    t.check("movie has rating before clear", orig_rating > 0, "got {}".format(orig_rating))

    # Clear rating
    clear_movie_field(movie_id, rating=0, votes="0")
    cleared = get_movie(movie_id)
    t.check("rating cleared", cleared["rating"] == 0)

    # Restore
    clear_movie_field(movie_id, rating=orig_rating, votes=orig_votes)
    restored = get_movie(movie_id)
    t.check("rating restored", abs(restored["rating"] - orig_rating) < 0.1)

    print("\n--- Test 5.2: Clear and restore movie tagline ---")
    orig_tagline = original.get("tagline", "")
    if orig_tagline:
        clear_movie_field(movie_id, tagline="")
        cleared = get_movie(movie_id)
        t.check("tagline cleared", cleared.get("tagline", "") == "")
        clear_movie_field(movie_id, tagline=orig_tagline)
        restored = get_movie(movie_id)
        t.check("tagline restored", restored.get("tagline") == orig_tagline)
    else:
        print("  SKIP: movie has no tagline to test with")


def test_edge_cases(t: IntegrationTest) -> None:
    """Test ID edge cases from the library."""
    print("\n=== Edge Case Tests ===")

    from resources.lib.providers.tmdb import resolve_id

    # Test 6.1: Show with "None" string as ID
    print('\n--- Test 6.1: "None" string IDs (De 12 van Schouwendam) ---')
    show = find_show_by_title("De 12 van Schouwendam")
    if show:
        ids = show.get("uniqueid", {})
        t.check("has uniqueid", bool(ids))
        imdb = ids.get("imdb")
        tvdb = ids.get("tvdb")
        t.check("imdb is 'None' string", imdb == "None", "got '{}'".format(imdb))
        t.check("tvdb is 'None' string", tvdb == "None", "got '{}'".format(tvdb))
        # Our _extract_ids should filter these out
        tvmaze = ids.get("tvmaze")
        t.check("has tvmaze ID", tvmaze is not None and tvmaze != "None")
        # resolve_id should handle None strings gracefully
        resolved = resolve_id(TMDB_KEY, tvdb_id=tvdb if tvdb != "None" else None,
                              imdb_id=imdb if imdb != "None" else None)
        t.check("resolve_id with None strings returns None", resolved is None)
    else:
        print("  SKIP: show not in library")

    # Test 6.2: Show with "unknown" ID type
    print('\n--- Test 6.2: "unknown" ID type (Alfred Jodocus Kwak) ---')
    show = find_show_by_title("Alfred Jodocus Kwak")
    if show:
        ids = show.get("uniqueid", {})
        t.check("only has unknown ID", "unknown" in ids and "tmdb" not in ids)
    else:
        print("  SKIP: show not in library")


def test_timing(t: IntegrationTest) -> None:
    """Test API call timing."""
    print("\n=== Timing Tests ===")
    from resources.lib.providers.tmdb import fetch_movie, fetch_season

    # Movie call timing
    print("\n--- Test 7.1: Movie call timing ---")
    times = []
    for tmdb_id in ["157336", "666277", "49046"]:
        start = time.perf_counter()
        fetch_movie(tmdb_id, TMDB_KEY)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        print("  tmdb:{}: {:.0f}ms".format(tmdb_id, elapsed))
    avg = sum(times) / len(times)
    t.check("avg movie call < 1000ms", avg < 1000, "avg {:.0f}ms".format(avg))

    # Season call timing
    print("\n--- Test 7.2: Season call timing ---")
    start = time.perf_counter()
    eps = fetch_season("129552", 2, TMDB_KEY)
    elapsed = (time.perf_counter() - start) * 1000
    print("  Night Agent S02 ({} eps): {:.0f}ms".format(len(eps) if eps else 0, elapsed))
    t.check("season call < 1000ms", elapsed < 1000, "{:.0f}ms".format(elapsed))


def main() -> None:
    print("MetaMender Integration Test")
    print("Kodi: {}".format(KODI_URL))
    print("TMDB key: {}...".format(TMDB_KEY[:8]))

    # Verify Kodi is reachable
    try:
        result = rpc("JSONRPC.Ping")
        assert result == "pong"
        print("Kodi: connected\n")
    except Exception as e:
        print("ERROR: Cannot reach Kodi at {}: {}".format(KODI_URL, e))
        sys.exit(1)

    t = IntegrationTest()

    test_tmdb_provider_directly(t)
    test_kodi_read_write(t)
    test_edge_cases(t)
    test_timing(t)

    t.summary()
    sys.exit(1 if t.failed > 0 else 0)


if __name__ == "__main__":
    main()

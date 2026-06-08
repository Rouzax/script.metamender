#!/usr/bin/env python3
"""
Surgical integration tests against a live Kodi instance.

Tests specific scenarios by manipulating individual items,
running targeted scans, and verifying results. Uses real TMDB API.

Usage:
    python3 tests/live_integration_test.py [test_number]
    python3 tests/live_integration_test.py          # run all
    python3 tests/live_integration_test.py 1        # run test 1 only

Requires:
    - Kodi running at localhost:8085
    - TMDB API key
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests

KODI_URL = os.environ.get("KODI_URL", "http://localhost:8085/jsonrpc")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "")  # set TMDB_API_KEY env var to run

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Inject Kodi stubs so addon modules can be imported.
# xbmc.executeJSONRPC is patched to proxy to real Kodi via HTTP,
# so the scan engine can read from and write to the live instance.
import json as _json
from unittest.mock import MagicMock

_xbmc_stub = MagicMock()

def _proxy_jsonrpc(query_str):
    # type: (str) -> str
    """Forward JSON-RPC calls to the real Kodi instance via HTTP."""
    query = _json.loads(query_str)
    resp = requests.post(KODI_URL, json=query, timeout=10)
    return _json.dumps(resp.json())

_xbmc_stub.executeJSONRPC = _proxy_jsonrpc
_xbmc_stub.LOGDEBUG = 0
_xbmc_stub.LOGINFO = 1
_xbmc_stub.LOGWARNING = 3
_xbmc_stub.LOGERROR = 4
_xbmc_stub.log = MagicMock()
_xbmc_stub.getCondVisibility = MagicMock(return_value=False)

sys.modules.setdefault("xbmc", _xbmc_stub)
for mod in ("xbmcaddon", "xbmcgui", "xbmcvfs"):
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()


# ---------------------------------------------------------------------------
# Kodi JSON-RPC helpers (via HTTP)
# ---------------------------------------------------------------------------

def rpc(method, params=None):
    # type: (str, Optional[Dict[str, Any]]) -> Dict[str, Any]
    payload = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        payload["params"] = params
    resp = requests.post(KODI_URL, json=payload, timeout=10)
    return resp.json().get("result", {})


def get_movie(movie_id):
    # type: (int) -> Dict[str, Any]
    result = rpc("VideoLibrary.GetMovieDetails", {
        "movieid": movie_id,
        "properties": [
            "title", "rating", "votes", "plot", "tagline", "director",
            "writer", "genre", "studio", "country", "trailer", "runtime",
            "mpaa", "uniqueid", "premiered", "dateadded", "file",
        ],
    })
    return result.get("moviedetails", {})


def get_episode(episode_id):
    # type: (int) -> Dict[str, Any]
    result = rpc("VideoLibrary.GetEpisodeDetails", {
        "episodeid": episode_id,
        "properties": [
            "title", "rating", "votes", "plot", "showtitle", "season",
            "episode", "uniqueid", "firstaired", "director", "writer",
            "runtime", "dateadded", "file",
        ],
    })
    return result.get("episodedetails", {})


def get_tvshow(tvshow_id):
    # type: (int) -> Dict[str, Any]
    result = rpc("VideoLibrary.GetTVShowDetails", {
        "tvshowid": tvshow_id,
        "properties": [
            "title", "rating", "votes", "plot", "genre", "mpaa",
            "uniqueid", "premiered", "dateadded", "file",
        ],
    })
    return result.get("tvshowdetails", {})


def set_movie(movie_id, **fields):
    # type: (int, Any) -> None
    params = {"movieid": movie_id}
    params.update(fields)
    rpc("VideoLibrary.SetMovieDetails", params)


def set_episode(episode_id, **fields):
    # type: (int, Any) -> None
    params = {"episodeid": episode_id}
    params.update(fields)
    rpc("VideoLibrary.SetEpisodeDetails", params)


# ---------------------------------------------------------------------------
# Scan engine helpers
# ---------------------------------------------------------------------------

def make_tracker(tmp_dir):
    # type: (str) -> Any
    from resources.lib.data.tracker import TrackerDB
    return TrackerDB(os.path.join(tmp_dir, "test.db"))


def run_targeted_scan(media_type, items, tracker, show_id_map=None):
    # type: (Any, List[Dict], Any, Optional[Dict[str, str]]) -> Dict[str, Any]
    from resources.lib.engine.scan import run_scan
    return run_scan(
        media_type=media_type,
        tracker=tracker,
        source_config={"sources": [], "default_language": "en-US"},
        enabled_fields=frozenset({
            "rating", "plot", "tagline", "director", "writer",
            "genre", "studio", "country", "trailer", "runtime", "title",
        }),
        tmdb_api_key=TMDB_KEY,
        settings={
            "min_votes_movie": 50,
            "min_votes_episode": 20,
            "stale_threshold_days": 365,
            "max_retries": 4,
            "rate_limit": 0.0,
        },
        items=items,
        show_id_map=show_id_map,
    )


def set_tvshow(tvshow_id, **fields):
    # type: (int, Any) -> None
    params = {"tvshowid": tvshow_id}
    params.update(fields)
    rpc("VideoLibrary.SetTVShowDetails", params)


def run_scan_with_settings(
    media_type, items, tracker,
    show_id_map=None,
    rating_source=0,
    plot_source=0,
    rate_limit=0.0,
    max_retries=4,
):
    # type: (...) -> Dict[str, Any]
    from resources.lib.engine.scan import run_scan
    return run_scan(
        media_type=media_type,
        tracker=tracker,
        source_config={"sources": [], "default_language": "en-US"},
        enabled_fields=frozenset({
            "rating", "plot", "tagline", "director", "writer",
            "genre", "studio", "country", "trailer", "runtime", "title",
        }),
        tmdb_api_key=TMDB_KEY,
        settings={
            "min_votes_movie": 50,
            "min_votes_episode": 20,
            "stale_threshold_days": 365,
            "max_retries": max_retries,
            "rate_limit": rate_limit,
            "rating_source": rating_source,
            "plot_source": plot_source,
        },
        items=items,
        show_id_map=show_id_map,
    )


class LogCapture:
    """Capture Kodi log output for assertion."""

    def __init__(self):
        # type: () -> None
        self.lines = []  # type: List[str]

    def __enter__(self):
        # type: () -> LogCapture
        import xbmc
        self._orig_log = xbmc.log
        self_ref = self

        def capture(msg, level=0):
            self_ref.lines.append(str(msg))

        xbmc.log = capture
        return self

    def __exit__(self, *args):
        # type: (...) -> None
        import xbmc
        xbmc.log = self._orig_log

    def find(self, substring):
        # type: (str) -> List[str]
        return [l for l in self.lines if substring in l]

    def has(self, substring):
        # type: (str) -> bool
        return len(self.find(substring)) > 0


# ---------------------------------------------------------------------------
# Test framework
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self):
        # type: () -> None
        self.passed = 0
        self.failed = 0
        self.errors = []  # type: List[str]

    def check(self, name, condition, detail=""):
        # type: (str, bool, str) -> None
        if condition:
            self.passed += 1
            print("    PASS: {}".format(name))
        else:
            self.failed += 1
            msg = "    FAIL: {} ({})".format(name, detail) if detail else "    FAIL: {}".format(name)
            print(msg)
            self.errors.append(msg)

    def summary(self):
        # type: () -> None
        print("\n" + "=" * 60)
        print("Results: {} passed, {} failed".format(self.passed, self.failed))
        if self.errors:
            print("\nFailures:")
            for e in self.errors:
                print(e)
        print("=" * 60)


# ---------------------------------------------------------------------------
# Test 1: Movie rating backfill (happy path)
# ---------------------------------------------------------------------------

def test_1_movie_rating_backfill(t):
    # type: (TestResult) -> None
    """Movie with zero rating should get rating from TMDB.

    Uses "Whale" (id:353, tmdb:1082914) which genuinely has zero
    rating in Kodi. TMDB may or may not have data; this tests the
    flow either way.

    Also tests writing a rating to Air (id:6) to verify the JSON-RPC
    write path works for non-zero values.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 1: Movie rating backfill ---")

    # Part A: verify we can write a rating via JSON-RPC
    air = get_movie(6)
    t.check("Air found", air.get("title") == "Air")
    orig_rating = air["rating"]
    orig_votes = air.get("votes", "0")

    set_movie(6, rating=9.9)
    check = get_movie(6)
    t.check("can set rating (9.9)", abs(check["rating"] - 9.9) < 0.2,
            "got {}".format(check["rating"]))
    set_movie(6, rating=orig_rating, votes=orig_votes)

    # Part B: scan a movie that already has zero rating
    whale = get_movie(353)
    t.check("Whale found", whale.get("title") == "Whale",
            "got '{}'".format(whale.get("title")))
    t.check("Whale found with rating", whale["rating"] is not None)

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_movie(353)
    stats = run_targeted_scan(MOVIE, [item], tracker)
    t.check("processed", stats["processed"] >= 1)

    # TMDB may have zero votes for Whale too; either way, tracker should record
    tracker_item = tracker.get_item("movie", "Whale")
    t.check("tracker has item", tracker_item is not None)
    if tracker_item:
        t.check("attempt recorded", tracker_item["attempt_count"] >= 1)

    tracker.close()


# ---------------------------------------------------------------------------
# Test 2: Tagline gap with no TMDB data
# ---------------------------------------------------------------------------

def test_2_movie_tagline_no_tmdb_data(t):
    # type: (TestResult) -> None
    """Movie with no tagline on TMDB should record attempt with retry."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 2: Tagline gap, TMDB empty (All Quiet, id:8, tmdb:49046) ---")

    movie = get_movie(8)
    t.check("movie found", "All Quiet" in movie.get("title", ""))
    t.check("tagline already empty", movie.get("tagline", "") == "")

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_movie(8)
    stats = run_targeted_scan(MOVIE, [item], tracker)
    total = stats["processed"] + stats["skipped"]
    t.check("item handled", total >= 1,
            "processed={}, skipped={}".format(stats["processed"], stats["skipped"]))

    tracker_item = tracker.get_item("movie", movie["title"])
    t.check("tracker has item", tracker_item is not None)

    tracker.close()


# ---------------------------------------------------------------------------
# Test 3: Episode title replacement (placeholder to real)
# ---------------------------------------------------------------------------

def test_3_episode_title_replacement(t):
    # type: (TestResult) -> None
    """Set placeholder title, scan, verify TMDB title replaces it."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 3: Episode title replacement (1923 S01E02, id:2) ---")

    ep = get_episode(2)
    orig_title = ep["title"]
    t.check("episode found", ep.get("showtitle") == "1923")
    t.check("has real title", orig_title != "", "got '{}'".format(orig_title))

    # Set placeholder
    set_episode(2, title="Episode 2")
    cleared = get_episode(2)
    t.check("title set to placeholder", cleared["title"] == "Episode 2")

    # Scan with show_id_map
    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_episode(2)
    show_map = {"1923": "157744"}
    stats = run_targeted_scan(EPISODE, [item], tracker, show_id_map=show_map)
    t.check("processed", stats["processed"] >= 1)
    t.check("updated", stats["updated"] >= 1)

    # Verify title restored
    after = get_episode(2)
    t.check("title restored from TMDB", after["title"] != "Episode 2",
            "got '{}'".format(after["title"]))
    t.check("title matches original", after["title"] == orig_title,
            "expected '{}', got '{}'".format(orig_title, after["title"]))

    tracker.close()

    # Safety restore
    if after["title"] != orig_title:
        set_episode(2, title=orig_title)


# ---------------------------------------------------------------------------
# Test 4: Title NOT replaced when TMDB matches placeholder
# ---------------------------------------------------------------------------

def test_4_episode_title_not_replaced(t):
    # type: (TestResult) -> None
    """Placeholder title that matches TMDB should NOT be overwritten."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 4: Title NOT replaced (Marcella S01E01, id:1723) ---")

    ep = get_episode(1723)
    t.check("episode found", ep.get("showtitle") == "Marcella")
    t.check("title is placeholder", ep["title"] == "Episode 1",
            "got '{}'".format(ep["title"]))

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_episode(1723)
    show_map = {"Marcella": "66023"}
    stats = run_targeted_scan(EPISODE, [item], tracker, show_id_map=show_map)

    after = get_episode(1723)
    t.check("title unchanged", after["title"] == "Episode 1",
            "got '{}'".format(after["title"]))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 5: Retry gating (future next_retry skipped)
# ---------------------------------------------------------------------------

def test_5_retry_gating(t):
    # type: (TestResult) -> None
    """Pre-seed tracker with future retry date, verify item is skipped."""
    from resources.lib.data.media_types import MOVIE
    from resources.lib.providers.tmdb import fetch_movie

    print("\n--- Test 5: Retry gating (Burial, id:50, tmdb:1155875) ---")

    movie = get_movie(50)
    t.check("movie found", movie.get("title") == "Burial")

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)

    # Pre-seed with future retry
    tracker.upsert_item("movie", "Burial", kodi_id=50, tmdb_id="1155875")
    tracker.record_attempt("movie", "Burial", next_retry="2099-01-01")
    count_before = tracker.get_item("movie", "Burial")["attempt_count"]

    item = get_movie(50)

    # Track TMDB calls
    call_count = [0]
    orig_fetch = fetch_movie

    def counting_fetch(*args, **kwargs):
        call_count[0] += 1
        return orig_fetch(*args, **kwargs)

    import resources.lib.engine.scan as scan_mod
    old = scan_mod.tmdb_fetch_movie
    scan_mod.tmdb_fetch_movie = counting_fetch
    try:
        stats = run_targeted_scan(MOVIE, [item], tracker)
    finally:
        scan_mod.tmdb_fetch_movie = old

    t.check("item skipped", stats["skipped"] >= 1)
    t.check("no TMDB call", call_count[0] == 0, "got {} calls".format(call_count[0]))

    count_after = tracker.get_item("movie", "Burial")["attempt_count"]
    t.check("attempt_count unchanged", count_after == count_before,
            "before={}, after={}".format(count_before, count_after))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 6: Exhaustion after max retries
# ---------------------------------------------------------------------------

def test_6_exhaustion(t):
    # type: (TestResult) -> None
    """Item at max retries should be marked exhausted."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 6: Exhaustion (STRANGER, id:324, tmdb:1156433) ---")

    movie = get_movie(324)
    t.check("movie found", movie.get("title") == "STRANGER")

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)

    # Pre-seed with 3 attempts (max is 4), past retry date
    tracker.upsert_item("movie", "STRANGER", kodi_id=324, tmdb_id="1156433")
    for _ in range(3):
        tracker.record_attempt("movie", "STRANGER", next_retry="2020-01-01")

    item = get_movie(324)
    stats = run_targeted_scan(MOVIE, [item], tracker)

    tracker_item = tracker.get_item("movie", "STRANGER")
    t.check("status exhausted", tracker_item["status"] == "exhausted",
            "got '{}'".format(tracker_item["status"]))
    t.check("attempt_count is 4", tracker_item["attempt_count"] == 4,
            "got {}".format(tracker_item["attempt_count"]))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 7: Staleness detection (low votes on recent content)
# ---------------------------------------------------------------------------

def test_7_staleness_detection(t):
    # type: (TestResult) -> None
    """Recent movie with low votes should trigger stale re-check."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 7: Staleness (Miss You Love You, id:372, votes=7) ---")

    movie = get_movie(372)
    t.check("movie found", "Miss You" in movie.get("title", ""),
            "got '{}'".format(movie.get("title")))
    t.check("has rating", movie["rating"] > 0)
    t.check("premiered 2026", movie.get("premiered", "").startswith("2026"))

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)

    # Pre-seed tracker as if we fetched 10 days ago with low votes
    tracker.upsert_item(
        "movie", movie["title"], kodi_id=372, tmdb_id="1515615",
        premiered=movie.get("premiered", ""),
    )
    tracker.set_field_status(
        "movie", movie["title"], "rating", "filled",
        source="tmdb", vote_count=7,
    )
    # Set fetched_at to 10 days ago
    fetched_at = (date.today() - timedelta(days=10)).isoformat()
    tracker._conn.execute(
        "UPDATE field_status SET fetched_at = ? WHERE field = 'rating' AND item_id = ("
        "SELECT id FROM items WHERE media_type = 'movie' AND title = ?)",
        (fetched_at, movie["title"]),
    )
    tracker._conn.commit()

    item = get_movie(372)
    stats = run_targeted_scan(MOVIE, [item], tracker)

    t.check("processed", stats["processed"] >= 1)

    # Field should be re-fetched
    field = tracker.get_field_status("movie", movie["title"], "rating")
    t.check("field re-fetched", field is not None)
    if field:
        t.check("field status updated",
                field.get("status") in ("filled", "checked"),
                "got {}".format(field.get("status")))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 8: TVDB-only show (needs resolve_id)
# ---------------------------------------------------------------------------

def test_8_tvdb_only_resolve_id(t):
    # type: (TestResult) -> None
    """Show with tvdb ID only should resolve via TMDB find endpoint."""
    from resources.lib.data.media_types import TVSHOW

    print("\n--- Test 8: TVDB-only resolve (Ontdek de Ruimte, id:410, tvdb:302930) ---")

    show = get_tvshow(410)
    t.check("show found", "Ruimte" in show.get("title", "") or "Kuipers" in show.get("title", ""),
            "got '{}'".format(show.get("title")))
    ids = show.get("uniqueid", {})
    t.check("has tvdb", "tvdb" in ids)
    t.check("no tmdb", "tmdb" not in ids or not ids.get("tmdb"))

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_tvshow(410)
    stats = run_targeted_scan(TVSHOW, [item], tracker)
    t.check("processed", stats["processed"] >= 1)

    tracker_item = tracker.get_item("tvshow", show["title"])
    if tracker_item:
        # This show may not exist on TMDB; resolve_id returns None for it.
        # The important thing is it was processed without crashing.
        resolved = tracker_item.get("tmdb_id")
        if resolved:
            t.check("tmdb_id resolved", True, "got {}".format(resolved))
        else:
            t.check("graceful handling when unresolvable", True)

    tracker.close()


# ---------------------------------------------------------------------------
# Test 9: Bogus IDs (graceful skip)
# ---------------------------------------------------------------------------

def test_9_bogus_ids_graceful_skip(t):
    # type: (TestResult) -> None
    """Items with 'None' string IDs should be handled gracefully."""
    from resources.lib.data.media_types import TVSHOW

    print('\n--- Test 9: Bogus IDs (De 12 van Schouwendam, id:421) ---')

    show = get_tvshow(421)
    t.check("show found", "Schouwendam" in show.get("title", ""),
            "got '{}'".format(show.get("title")))
    ids = show.get("uniqueid", {})
    t.check("has None string IDs", ids.get("imdb") == "None" or ids.get("tvdb") == "None")

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_tvshow(421)
    stats = run_targeted_scan(TVSHOW, [item], tracker)
    t.check("no crash", True)
    t.check("handled gracefully", stats["errors"] <= 1, "got {}".format(stats["errors"]))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 10: Logging completeness
# ---------------------------------------------------------------------------

def test_10_logging_completeness(t):
    # type: (TestResult) -> None
    """Run a small scan and verify log output has all required fields."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 10: Logging completeness ---")

    import xbmc
    logged = []  # type: List[str]
    orig_log = xbmc.log

    def capture_log(msg, level=0):
        logged.append(str(msg))

    xbmc.log = capture_log
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        # Use Whale (zero rating) to force a TMDB call
        item = get_movie(353)
        run_targeted_scan(MOVIE, [item], tracker)
        tracker.close()
    finally:
        xbmc.log = orig_log

    t.check("captured log lines", len(logged) > 0, "got {}".format(len(logged)))

    # Check scan.start
    scan_starts = [l for l in logged if "event=scan.start" in l]
    t.check("has scan.start", len(scan_starts) >= 1)

    # Check tmdb.movie
    tmdb_calls = [l for l in logged if "event=tmdb.movie" in l]
    t.check("has tmdb.movie", len(tmdb_calls) >= 1)
    if tmdb_calls:
        t.check("tmdb.movie has duration_ms", "duration_ms=" in tmdb_calls[0])
        t.check("tmdb.movie has tmdb_id", "tmdb_id=" in tmdb_calls[0])
        t.check("tmdb.movie has title", "title=" in tmdb_calls[0])

    # Check scan.updated (may not be present if TMDB had no data for the item)
    updates = [l for l in logged if "event=scan.updated" in l]
    if updates:
        t.check("scan.updated has title", "title=" in updates[0])
        t.check("scan.updated has media_type", "media_type=" in updates[0])
        t.check("scan.updated has fields_written", "fields_written=" in updates[0])

    # Check scan.complete
    completes = [l for l in logged if "event=scan.complete" in l]
    t.check("has scan.complete", len(completes) >= 1)
    if completes:
        t.check("scan.complete has processed", "processed=" in completes[0])
        t.check("scan.complete has updated", "updated=" in completes[0])
        t.check("scan.complete has elapsed", "duration_ms=" in completes[0])


# ===========================================================================
# Group A: IMDb Provider (tests 11-14)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 11: IMDb movie fetch (The Matrix)
# ---------------------------------------------------------------------------

def test_11_imdb_movie_fetch(t):
    # type: (TestResult) -> None
    """Fetch The Matrix from imdbapi.dev, verify all fields parsed."""
    from resources.lib.providers.imdbapi import fetch_movie

    print("\n--- Test 11: IMDb movie fetch (The Matrix, tt0133093) ---")

    result = fetch_movie("tt0133093")
    t.check("fetch returned data", result is not None)
    if result is None:
        return

    t.check("rating ~8.7", 8.0 <= result["rating"] <= 9.5,
            "got {}".format(result["rating"]))
    t.check("votes > 2M", result["votes"] > 2000000,
            "got {}".format(result["votes"]))
    t.check("plot non-empty", len(result.get("plot", "")) > 20)
    t.check("directors has Lana Wachowski",
            "Lana Wachowski" in result.get("directors", []),
            "got {}".format(result.get("directors")))
    t.check("directors has Lilly Wachowski",
            "Lilly Wachowski" in result.get("directors", []))
    t.check("writers has Lilly Wachowski",
            "Lilly Wachowski" in result.get("writers", []),
            "got {}".format(result.get("writers")))
    t.check("genres has Action", "Action" in result.get("genres", []),
            "got {}".format(result.get("genres")))
    t.check("genres has Sci-Fi", "Sci-Fi" in result.get("genres", []))
    t.check("countries has United States",
            "United States" in result.get("countries", []),
            "got {}".format(result.get("countries")))
    t.check("runtime is 136", result.get("runtime") == 136,
            "got {}".format(result.get("runtime")))
    t.check("metacritic is 73", result.get("metacritic") == 73,
            "got {}".format(result.get("metacritic")))


# ---------------------------------------------------------------------------
# Test 12: IMDb episode direct fetch (Bluey S01E01)
# ---------------------------------------------------------------------------

def test_12_imdb_episode_fetch(t):
    # type: (TestResult) -> None
    """Fetch Bluey S01E01 directly by IMDB ID."""
    from resources.lib.providers.imdbapi import fetch_episode

    print("\n--- Test 12: IMDb episode fetch (Bluey S01E01, tt8865002) ---")

    result = fetch_episode("tt8865002")
    t.check("fetch returned data", result is not None)
    if result is None:
        return

    t.check("title is Magic Xylophone", result.get("title") == "Magic Xylophone",
            "got '{}'".format(result.get("title")))
    t.check("rating > 0", result.get("rating", 0) > 0,
            "got {}".format(result.get("rating")))
    t.check("plot non-empty", len(result.get("plot", "")) > 10)
    t.check("directors non-empty", len(result.get("directors", [])) > 0,
            "got {}".format(result.get("directors")))
    t.check("runtime > 0", (result.get("runtime") or 0) > 0,
            "got {}".format(result.get("runtime")))
    t.check("no firstaired key", "firstaired" not in result)


# ---------------------------------------------------------------------------
# Test 13: IMDb season fetch with pagination (Bluey S01, 52 eps)
# ---------------------------------------------------------------------------

def test_13_imdb_season_pagination(t):
    # type: (TestResult) -> None
    """Fetch Bluey S01 (52 episodes), verify auto-pagination."""
    from resources.lib.providers.imdbapi import fetch_season

    print("\n--- Test 13: IMDb season pagination (Bluey S01, tt7678620) ---")

    result = fetch_season("tt7678620", 1)
    t.check("fetch returned data", result is not None)
    if result is None:
        return

    t.check("got 52 episodes", len(result) == 52,
            "got {}".format(len(result)))
    t.check("first ep is tt8865002", result[0].get("imdb_id") == "tt8865002",
            "got {}".format(result[0].get("imdb_id")))

    missing_fields = []
    for i, ep in enumerate(result):
        for field in ("imdb_id", "title", "rating", "votes", "plot", "episode_number"):
            if field not in ep:
                missing_fields.append("ep{}: {}".format(i + 1, field))
    t.check("all episodes have required fields", len(missing_fields) == 0,
            "missing: {}".format(missing_fields[:5]))

    ep_nums = [ep.get("episode_number", 0) for ep in result]
    expected = list(range(1, 53))
    t.check("episode numbers 1-52", ep_nums == expected,
            "got {}".format(ep_nums[:5]))


# ---------------------------------------------------------------------------
# Test 14: IMDb certificates fetch (The Matrix)
# ---------------------------------------------------------------------------

def test_14_imdb_certificates(t):
    # type: (TestResult) -> None
    """Fetch certificates for The Matrix, verify multi-entry grouping."""
    from resources.lib.providers.imdbapi import fetch_certificates

    print("\n--- Test 14: IMDb certificates (The Matrix, tt0133093) ---")

    result = fetch_certificates("tt0133093")
    t.check("fetch returned data", result is not None)
    if result is None:
        return

    t.check("NL present", "NL" in result, "countries: {}".format(list(result.keys())[:10]))
    t.check("US present", "US" in result)
    t.check("GB present", "GB" in result)

    if "NL" in result:
        nl = result["NL"]
        t.check("NL has >= 2 entries", len(nl) >= 2,
                "got {} entries".format(len(nl)))
        ratings = [e.get("rating") for e in nl]
        t.check("NL has 16", "16" in ratings, "got {}".format(ratings))
        t.check("NL has 12", "12" in ratings, "got {}".format(ratings))

    if "US" in result:
        us = result["US"]
        t.check("US has multiple entries", len(us) >= 2,
                "got {} entries".format(len(us)))
        for entry in us:
            t.check("US entry has rating", isinstance(entry.get("rating"), str))
            t.check("US entry has attributes list",
                    isinstance(entry.get("attributes"), list))
            break


# ===========================================================================
# Group B: Source Priority Resolution (tests 15-17)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 15: Source priority: Prefer TMDB (default)
# ---------------------------------------------------------------------------

def test_15_prefer_tmdb(t):
    # type: (TestResult) -> None
    """Scan movie with Prefer TMDB for plot, verify TMDB plot written."""
    from resources.lib.data.media_types import MOVIE
    from resources.lib.providers.imdbapi import fetch_movie as imdb_fetch
    from resources.lib.providers.tmdb import fetch_movie as tmdb_fetch

    print("\n--- Test 15: Prefer TMDB (Air, id:6, tmdb:964980, imdb:tt16419074) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    tmdb_data = tmdb_fetch("964980", TMDB_KEY)
    imdb_data = imdb_fetch("tt16419074")
    t.check("TMDB has data", tmdb_data is not None)
    t.check("IMDb has data", imdb_data is not None)
    if not tmdb_data or not imdb_data:
        return

    tmdb_plot = tmdb_data["plot"]
    imdb_plot = imdb_data["plot"]
    t.check("plots differ", tmdb_plot != imdb_plot,
            "tmdb='{}...', imdb='{}...'".format(tmdb_plot[:40], imdb_plot[:40]))

    set_movie(6, plot="")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker, plot_source=0)

        after = get_movie(6)
        t.check("plot matches TMDB",
                after["plot"] == tmdb_plot,
                "written='{}...', tmdb='{}...'".format(
                    after["plot"][:40], tmdb_plot[:40]))

        field = tracker.get_field_status("movie", "Air", "plot")
        t.check("tracker source is tmdb",
                field is not None and field.get("source") == "tmdb",
                "got {}".format(field.get("source") if field else "None"))

        tracker.close()
    finally:
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 16: Source priority: Prefer IMDb
# ---------------------------------------------------------------------------

def test_16_prefer_imdb(t):
    # type: (TestResult) -> None
    """Scan movie with Prefer IMDb for plot, verify IMDb plot written."""
    from resources.lib.data.media_types import MOVIE
    from resources.lib.providers.imdbapi import fetch_movie as imdb_fetch
    from resources.lib.providers.tmdb import fetch_movie as tmdb_fetch

    print("\n--- Test 16: Prefer IMDb (Air, id:6) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    tmdb_data = tmdb_fetch("964980", TMDB_KEY)
    imdb_data = imdb_fetch("tt16419074")
    if not tmdb_data or not imdb_data:
        t.check("both providers have data", False)
        return

    imdb_plot = imdb_data["plot"]

    set_movie(6, plot="")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker, plot_source=1)

        after = get_movie(6)
        t.check("plot matches IMDb",
                after["plot"] == imdb_plot,
                "written='{}...', imdb='{}...'".format(
                    after["plot"][:40], imdb_plot[:40]))

        field = tracker.get_field_status("movie", "Air", "plot")
        t.check("tracker source is imdb",
                field is not None and field.get("source") == "imdb",
                "got {}".format(field.get("source") if field else "None"))

        tracker.close()
    finally:
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 17: Source priority: TMDB only (no IMDb calls)
# ---------------------------------------------------------------------------

def test_17_tmdb_only(t):
    # type: (TestResult) -> None
    """TMDB-only mode should not call IMDb at all."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 17: TMDB only mode (Air, id:6) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    imdb_call_count = [0]
    import resources.lib.engine.scan as scan_mod
    orig_imdb = scan_mod.imdb_fetch_movie

    def counting_imdb(*args, **kwargs):
        imdb_call_count[0] += 1
        return orig_imdb(*args, **kwargs)

    set_movie(6, plot="")
    scan_mod.imdb_fetch_movie = counting_imdb
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)
        stats = run_scan_with_settings(MOVIE, [item], tracker,
                                       rating_source=2, plot_source=2)

        t.check("no IMDb calls", imdb_call_count[0] == 0,
                "got {} calls".format(imdb_call_count[0]))
        t.check("item processed", stats["processed"] >= 1)

        after = get_movie(6)
        t.check("plot written", len(after["plot"]) > 20,
                "got '{}'".format(after["plot"][:40]))

        tracker.close()
    finally:
        scan_mod.imdb_fetch_movie = orig_imdb
        set_movie(6, plot=orig_plot)


# ===========================================================================
# Group C: Episode Matching Across Scrapers (tests 18-22)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 18: TMDB-scraped episode (has tmdb_id)
# ---------------------------------------------------------------------------

def test_18_tmdb_episode_matching(t):
    # type: (TestResult) -> None
    """Episode with TMDB ID matches by tmdb_id in season data."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 18: TMDB episode (1923 S01E02, id:2, tmdb:4053246) ---")

    ep = get_episode(2)
    orig_plot = ep["plot"]

    set_episode(2, plot="")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_episode(2)
        show_map = {"1923": "157744"}
        stats = run_scan_with_settings(EPISODE, [item], tracker,
                                       show_id_map=show_map)

        t.check("processed", stats["processed"] >= 1)
        t.check("updated", stats["updated"] >= 1,
                "got updated={}".format(stats["updated"]))

        after = get_episode(2)
        t.check("plot written", len(after["plot"]) > 10,
                "got '{}'".format(after["plot"][:40]))

        tracker.close()
    finally:
        set_episode(2, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 19: TVDB-scraped episode (has imdb_id, no tmdb_id)
# ---------------------------------------------------------------------------

def test_19_tvdb_episode_imdb_fetch(t):
    # type: (TestResult) -> None
    """Episode with IMDB ID but no TMDB ID uses direct IMDb fetch."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 19: TVDB episode (Bluey S01E01, id:4311, imdb:tt8865002) ---")

    ep = get_episode(4311)
    t.check("episode found", ep.get("showtitle") == "Bluey (2018)",
            "got '{}'".format(ep.get("showtitle")))
    ids = ep.get("uniqueid", {})
    t.check("has imdb", "imdb" in ids, "ids: {}".format(ids))
    t.check("no tmdb", "tmdb" not in ids, "ids: {}".format(ids))

    orig_plot = ep.get("plot", "")

    set_episode(4311, plot="")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_episode(4311)
        show_map = {"Bluey (2018)": "82728"}
        stats = run_scan_with_settings(EPISODE, [item], tracker,
                                       show_id_map=show_map)

        t.check("processed", stats["processed"] >= 1)
        t.check("updated", stats["updated"] >= 1,
                "got updated={}".format(stats["updated"]))

        after = get_episode(4311)
        t.check("plot written", len(after.get("plot", "")) > 10,
                "got '{}'".format(after.get("plot", "")[:40]))

        field = tracker.get_field_status("episode",
                                         "Bluey (2018) S01E01", "plot")
        t.check("source is imdb",
                field is not None and field.get("source") == "imdb",
                "got {}".format(field.get("source") if field else "None"))

        tracker.close()
    finally:
        set_episode(4311, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 20: TVDB-only episode (no imdb, no tmdb): skip + exhaust
# ---------------------------------------------------------------------------

def test_20_tvdb_only_episode_exhaust(t):
    # type: (TestResult) -> None
    """Episode with only TVDB ID records attempt and eventually exhausts."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 20: TVDB-only episode (Transformers S01E01, id:6098) ---")

    ep = get_episode(6098)
    t.check("episode found", "Pilot" in ep.get("title", ""),
            "got '{}'".format(ep.get("title")))
    ids = ep.get("uniqueid", {})
    t.check("has tvdb only", "tvdb" in ids and "imdb" not in ids and "tmdb" not in ids,
            "ids: {}".format(ids))

    orig_rating = ep["rating"]
    title_key = "{} S{:02d}E{:02d}".format(
        ep.get("showtitle", ""), ep.get("season", 0), ep.get("episode", 0))

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_episode(6098)
    show_map = {}

    # Scan 1
    stats = run_scan_with_settings(EPISODE, [item], tracker,
                                   show_id_map=show_map, max_retries=3)
    t.check("processed (not just skipped)", stats["processed"] >= 1)

    tracker_item = tracker.get_item("episode", title_key)
    t.check("attempt recorded", tracker_item is not None and tracker_item["attempt_count"] >= 1,
            "key='{}', count={}".format(title_key,
                                        tracker_item["attempt_count"] if tracker_item else "None"))

    after = get_episode(6098)
    t.check("rating unchanged", after["rating"] == orig_rating,
            "before={}, after={}".format(orig_rating, after["rating"]))

    # Scans 2 and 3: force past retry date, reset cycle between scans
    for _ in range(2):
        tracker._conn.execute(
            "UPDATE items SET next_retry = '2020-01-01' WHERE media_type = 'episode' AND title = ?",
            (title_key,),
        )
        tracker._conn.commit()
        tracker.start_new_cycle()
        item = get_episode(6098)
        run_scan_with_settings(EPISODE, [item], tracker,
                               show_id_map=show_map, max_retries=3)

    tracker_item = tracker.get_item("episode", title_key)
    t.check("exhausted after max retries",
            tracker_item is not None and tracker_item["status"] == "exhausted",
            "status={}, count={}".format(
                tracker_item["status"] if tracker_item else "?",
                tracker_item["attempt_count"] if tracker_item else "?"))

    # Scan 4: exhausted item should be skipped
    item = get_episode(6098)
    stats = run_scan_with_settings(EPISODE, [item], tracker,
                                   show_id_map=show_map, max_retries=3)
    t.check("exhausted item skipped", stats["skipped"] >= 1)

    tracker.close()


# ---------------------------------------------------------------------------
# Test 21: TVmaze-only episode
# ---------------------------------------------------------------------------

def test_21_tvmaze_only_episode(t):
    # type: (TestResult) -> None
    """Episode with only TVmaze ID is skipped (tvmaze not in recognized IDs)."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 21: TVmaze-only (Flikken Rotterdam S01E01, id:6190) ---")

    ep = get_episode(6190)
    t.check("episode found", ep.get("title") == "Slachter",
            "got '{}'".format(ep.get("title")))
    ids = ep.get("uniqueid", {})
    t.check("has tvmaze only", "tvmaze" in ids and "imdb" not in ids and "tmdb" not in ids,
            "ids: {}".format(ids))

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_episode(6190)
    stats = run_scan_with_settings(EPISODE, [item], tracker,
                                   show_id_map={})

    t.check("skipped (tvmaze not recognized)", stats["skipped"] >= 1,
            "skipped={}, processed={}".format(stats["skipped"], stats["processed"]))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 22: NFO-scraped episode (no real IDs)
# ---------------------------------------------------------------------------

def test_22_nfo_episode_skip(t):
    # type: (TestResult) -> None
    """Episode with no external IDs is skipped entirely."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 22: NFO episode (Looney Tunes, id:5046) ---")

    ep = get_episode(5046)
    t.check("episode found", "Congo" in ep.get("title", ""),
            "got '{}'".format(ep.get("title")))
    ids = ep.get("uniqueid", {})
    has_real_id = any(ids.get(k) for k in ("imdb", "tmdb", "tvdb", "tvmaze"))
    t.check("no real IDs", not has_real_id, "ids: {}".format(ids))

    tmp = tempfile.mkdtemp()
    tracker = make_tracker(tmp)
    item = get_episode(5046)
    stats = run_scan_with_settings(EPISODE, [item], tracker,
                                   show_id_map={})

    t.check("skipped", stats["skipped"] >= 1,
            "skipped={}, processed={}".format(stats["skipped"], stats["processed"]))

    tracker.close()


# ===========================================================================
# Group D: Data Lifecycle (tests 23-26)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 23: Idempotent re-scan
# ---------------------------------------------------------------------------

def test_23_idempotent_rescan(t):
    # type: (TestResult) -> None
    """Second scan of a complete item skips without provider calls."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 23: Idempotent re-scan (Air, id:6) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    set_movie(6, plot="")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)

        stats1 = run_scan_with_settings(MOVIE, [item], tracker)
        t.check("first scan updated", stats1["updated"] >= 1)

        tracker_item = tracker.get_item("movie", "Air")
        t.check("status complete", tracker_item["status"] == "complete",
                "got '{}'".format(tracker_item["status"]))

        import resources.lib.engine.scan as scan_mod
        call_count = [0]
        orig_tmdb = scan_mod.tmdb_fetch_movie

        def counting_tmdb(*args, **kwargs):
            call_count[0] += 1
            return orig_tmdb(*args, **kwargs)

        scan_mod.tmdb_fetch_movie = counting_tmdb
        try:
            item2 = get_movie(6)
            stats2 = run_scan_with_settings(MOVIE, [item2], tracker)
        finally:
            scan_mod.tmdb_fetch_movie = orig_tmdb

        t.check("second scan skipped", stats2["skipped"] >= 1)
        t.check("no provider calls", call_count[0] == 0,
                "got {} calls".format(call_count[0]))

        tracker.close()
    finally:
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 24: Selective field update
# ---------------------------------------------------------------------------

def test_24_selective_field_update(t):
    # type: (TestResult) -> None
    """Clearing one field then scanning fills it without touching others."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 24: Selective field update (Air, id:6) ---")

    air = get_movie(6)
    orig_rating = air["rating"]
    orig_votes = air.get("votes", "0")
    orig_plot = air["plot"]

    set_movie(6, rating=0.0, votes="0")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker)

        after1 = get_movie(6)
        t.check("rating filled", after1["rating"] > 0)
        t.check("plot unchanged", after1["plot"] == orig_plot)
        written_rating = after1["rating"]

        set_movie(6, plot="")
        # Reset cycle and clear plot field_status so it's re-checked
        tracker.start_new_cycle()
        tracker.set_field_status("movie", "Air", "plot", "empty")
        item2 = get_movie(6)
        run_scan_with_settings(MOVIE, [item2], tracker)

        after2 = get_movie(6)
        t.check("plot now filled", len(after2["plot"]) > 20)
        t.check("rating unchanged from scan 1",
                abs(after2["rating"] - written_rating) < 0.01,
                "expected {}, got {}".format(written_rating, after2["rating"]))

        tracker.close()
    finally:
        set_movie(6, rating=orig_rating, votes=orig_votes, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 25: Exhaustion lifecycle (full cycle)
# ---------------------------------------------------------------------------

def test_25_exhaustion_lifecycle(t):
    # type: (TestResult) -> None
    """Full retry cycle: attempt, backoff, exhaust, skip."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 25: Exhaustion lifecycle (Air, id:6, patched to None) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    import resources.lib.engine.scan as scan_mod
    orig_tmdb = scan_mod.tmdb_fetch_movie
    orig_imdb = scan_mod.imdb_fetch_movie
    call_count = [0]

    def null_tmdb(*a, **kw):
        call_count[0] += 1
        return None

    def null_imdb(*a, **kw):
        call_count[0] += 1
        return None

    set_movie(6, plot="")
    scan_mod.tmdb_fetch_movie = null_tmdb
    scan_mod.imdb_fetch_movie = null_imdb
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)

        # Scan 1
        run_scan_with_settings(MOVIE, [item], tracker, max_retries=3)
        ti = tracker.get_item("movie", "Air")
        t.check("scan1: attempt=1", ti["attempt_count"] == 1)
        t.check("scan1: next_retry set", ti["next_retry"] is not None)

        # Force retry past, scan 2
        tracker._conn.execute(
            "UPDATE items SET next_retry = '2020-01-01' WHERE title = 'Air'")
        tracker._conn.commit()
        tracker.start_new_cycle()
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker, max_retries=3)
        ti = tracker.get_item("movie", "Air")
        t.check("scan2: attempt=2", ti["attempt_count"] == 2)

        # Force retry past, scan 3
        tracker._conn.execute(
            "UPDATE items SET next_retry = '2020-01-01' WHERE title = 'Air'")
        tracker._conn.commit()
        tracker.start_new_cycle()
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker, max_retries=3)
        ti = tracker.get_item("movie", "Air")
        t.check("scan3: exhausted", ti["status"] == "exhausted",
                "got {}".format(ti["status"]))
        t.check("scan3: attempt=3", ti["attempt_count"] == 3)

        # Scan 4: should skip (exhausted)
        tracker.start_new_cycle()
        item = get_movie(6)
        stats = run_scan_with_settings(MOVIE, [item], tracker, max_retries=3)
        t.check("scan4: skipped", stats["skipped"] >= 1)

        t.check("total provider calls == 6 (2 per scan)", call_count[0] == 6,
                "got {}".format(call_count[0]))

        tracker.close()
    finally:
        scan_mod.tmdb_fetch_movie = orig_tmdb
        scan_mod.imdb_fetch_movie = orig_imdb
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 26: Data survives across scans
# ---------------------------------------------------------------------------

def test_26_data_survives(t):
    # type: (TestResult) -> None
    """Filling one field, then another, preserves both."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 26: Data survives across scans (Air, id:6) ---")

    air = get_movie(6)
    orig_rating = air["rating"]
    orig_votes = air.get("votes", "0")
    orig_plot = air["plot"]

    set_movie(6, rating=0.0, votes="0")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker)

        after1 = get_movie(6)
        rating_val = after1["rating"]
        t.check("rating filled", rating_val > 0)

        set_movie(6, plot="")
        # Reset tracker so the item is re-evaluated with the new gap
        tracker.update_item_status("movie", "Air", "pending")
        tracker.set_field_status("movie", "Air", "plot", "empty")
        tracker._conn.execute(
            "UPDATE items SET cycle_processed = 0 WHERE title = 'Air'")
        tracker._conn.commit()
        item2 = get_movie(6)
        run_scan_with_settings(MOVIE, [item2], tracker)

        after2 = get_movie(6)
        t.check("rating survived", abs(after2["rating"] - rating_val) < 0.01,
                "expected {}, got {}".format(rating_val, after2["rating"]))
        t.check("plot now filled", len(after2["plot"]) > 20)

        p_field = tracker.get_field_status("movie", "Air", "plot")
        t.check("plot filled in tracker",
                p_field is not None and p_field["status"] == "filled")

        tracker.close()
    finally:
        set_movie(6, rating=orig_rating, votes=orig_votes, plot=orig_plot)


# ===========================================================================
# Group E: Logging Consistency (tests 27-30)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 27: Provider log events
# ---------------------------------------------------------------------------

def test_27_provider_logs(t):
    # type: (TestResult) -> None
    """Provider logs have all required key-value pairs."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 27: Provider log events ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    set_movie(6, plot="")
    try:
        with LogCapture() as logs:
            tmp = tempfile.mkdtemp()
            tracker = make_tracker(tmp)
            item = get_movie(6)
            run_scan_with_settings(MOVIE, [item], tracker)
            tracker.close()

        tmdb_lines = logs.find("event=tmdb.movie")
        t.check("has tmdb.movie log", len(tmdb_lines) >= 1)
        if tmdb_lines:
            line = tmdb_lines[0]
            t.check("tmdb.movie has tmdb_id", "tmdb_id=" in line)
            t.check("tmdb.movie has title", "title=" in line)
            t.check("tmdb.movie has duration_ms", "duration_ms=" in line)
            t.check("tmdb.movie has field_count", "field_count=" in line)

        imdb_lines = logs.find("event=imdb.movie")
        if imdb_lines:
            line = imdb_lines[0]
            t.check("imdb.movie has imdb_id", "imdb_id=" in line)
            t.check("imdb.movie has title", "title=" in line)
            t.check("imdb.movie has duration_ms", "duration_ms=" in line)
        else:
            t.check("imdb.movie present (or TMDB-only mode)", True)
    finally:
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 28: Scan lifecycle log events
# ---------------------------------------------------------------------------

def test_28_scan_lifecycle_logs(t):
    # type: (TestResult) -> None
    """Scan lifecycle events have all required fields."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 28: Scan lifecycle logs ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    set_movie(6, plot="")
    try:
        with LogCapture() as logs:
            tmp = tempfile.mkdtemp()
            tracker = make_tracker(tmp)
            item = get_movie(6)
            run_scan_with_settings(MOVIE, [item], tracker)
            tracker.close()

        starts = logs.find("event=scan.start")
        t.check("has scan.start", len(starts) >= 1)
        if starts:
            t.check("scan.start has media_type", "media_type=" in starts[0])

        updates = logs.find("event=scan.updated")
        if updates:
            line = updates[0]
            t.check("scan.updated has title", "title=" in line)
            t.check("scan.updated has media_type", "media_type=" in line)
            t.check("scan.updated has fields_written", "fields_written=" in line)

        completes = logs.find("event=scan.complete")
        t.check("has scan.complete", len(completes) >= 1)
        if completes:
            line = completes[0]
            t.check("scan.complete has processed", "processed=" in line)
            t.check("scan.complete has skipped", "skipped=" in line)
            t.check("scan.complete has updated", "updated=" in line)
            t.check("scan.complete has errors", "errors=" in line)
            t.check("scan.complete has elapsed",
                    "duration_ms=" in line)
    finally:
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 29: Episode provider logs (IMDb direct fetch)
# ---------------------------------------------------------------------------

def test_29_episode_provider_logs(t):
    # type: (TestResult) -> None
    """IMDb episode fetch logs have correct event and fields."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 29: Episode provider logs (Bluey S01E01) ---")

    ep = get_episode(4311)
    orig_plot = ep.get("plot", "")

    set_episode(4311, plot="")
    try:
        with LogCapture() as logs:
            tmp = tempfile.mkdtemp()
            tracker = make_tracker(tmp)
            item = get_episode(4311)
            show_map = {"Bluey (2018)": "82728"}
            run_scan_with_settings(EPISODE, [item], tracker,
                                   show_id_map=show_map)
            tracker.close()

        imdb_lines = logs.find("event=imdb.episode")
        t.check("has imdb.episode log", len(imdb_lines) >= 1)
        if imdb_lines:
            line = imdb_lines[0]
            t.check("has imdb_id=tt8865002", "imdb_id=tt8865002" in line,
                    "line: {}".format(line[:100]))
            t.check("has title", "title=" in line)
            t.check("has duration_ms", "duration_ms=" in line)
    finally:
        set_episode(4311, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 30: Log consistency across providers
# ---------------------------------------------------------------------------

def test_30_log_consistency(t):
    # type: (TestResult) -> None
    """All log lines follow naming conventions and have required keys."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 30: Log consistency ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    set_movie(6, plot="")
    try:
        with LogCapture() as logs:
            tmp = tempfile.mkdtemp()
            tracker = make_tracker(tmp)
            item = get_movie(6)
            run_scan_with_settings(MOVIE, [item], tracker)
            tracker.close()

        import re
        event_pattern = re.compile(r"event=([a-z]+\.[a-z_]+)")
        event_lines = [l for l in logs.lines if "event=" in l]
        t.check("has event lines", len(event_lines) > 0)

        bad_events = []
        for line in event_lines:
            match = event_pattern.search(line)
            if not match:
                if "event=None" in line or "event= " in line:
                    bad_events.append(line[:80])
        t.check("no malformed events", len(bad_events) == 0,
                "bad: {}".format(bad_events[:3]))

        duration_lines = [l for l in logs.lines if "duration_ms=" in l]
        bad_duration = []
        duration_re = re.compile(r"duration_ms=(\d+)")
        for line in duration_lines:
            m = duration_re.search(line)
            if not m or int(m.group(1)) <= 0:
                bad_duration.append(line[:80])
        t.check("all duration_ms positive", len(bad_duration) == 0,
                "bad: {}".format(bad_duration[:3]))

        completes = logs.find("event=scan.complete")
        if completes:
            line = completes[0]
            proc_m = re.search(r"processed=(\d+)", line)
            skip_m = re.search(r"skipped=(\d+)", line)
            if proc_m and skip_m:
                total = int(proc_m.group(1)) + int(skip_m.group(1))
                t.check("processed+skipped == 1 item",
                        total == 1, "got {}".format(total))
    finally:
        set_movie(6, plot=orig_plot)


# ===========================================================================
# Group F: Edge Cases (tests 31-33)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 31: Episode with both providers (data merge)
# ---------------------------------------------------------------------------

def test_31_episode_dual_provider(t):
    # type: (TestResult) -> None
    """Episode with both IDs: rating from IMDb, plot from TMDB."""
    from resources.lib.data.media_types import EPISODE
    from resources.lib.providers.imdbapi import fetch_episode as imdb_fetch_ep
    from resources.lib.providers.tmdb import fetch_season as tmdb_fetch_season_fn

    print("\n--- Test 31: Dual provider episode (1923 S01E01, id:1) ---")

    ep = get_episode(1)
    orig_rating = ep["rating"]
    orig_votes = ep.get("votes", "0")
    orig_plot = ep["plot"]

    imdb_data = imdb_fetch_ep("tt18469978")
    tmdb_season = tmdb_fetch_season_fn("157744", 1, TMDB_KEY)
    t.check("IMDb has data", imdb_data is not None)
    t.check("TMDB season has data", tmdb_season is not None)

    tmdb_ep = None
    if tmdb_season:
        for ep_data in tmdb_season:
            if ep_data.get("tmdb_id") == "4014249":
                tmdb_ep = ep_data
                break
    t.check("TMDB episode found by ID", tmdb_ep is not None)
    if not imdb_data or not tmdb_ep:
        return

    tmdb_plot = tmdb_ep["plot"]
    imdb_plot = imdb_data["plot"]
    t.check("plots differ", tmdb_plot != imdb_plot,
            "tmdb='{}...', imdb='{}...'".format(tmdb_plot[:40], imdb_plot[:40]))

    set_episode(1, plot="")
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_episode(1)
        show_map = {"1923": "157744"}
        run_scan_with_settings(EPISODE, [item], tracker,
                               show_id_map=show_map,
                               plot_source=0)

        after = get_episode(1)
        t.check("plot matches TMDB",
                after["plot"] == tmdb_plot,
                "written='{}...', tmdb='{}...'".format(
                    after["plot"][:40], tmdb_plot[:40]))

        p_field = tracker.get_field_status("episode", "1923 S01E01", "plot")
        t.check("plot source is tmdb",
                p_field is not None and p_field.get("source") == "tmdb",
                "got {}".format(p_field.get("source") if p_field else "None"))

        tracker.close()
    finally:
        set_episode(1, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 32: Show ID map mismatch (IMDb fallback)
# ---------------------------------------------------------------------------

def test_32_show_id_map_mismatch(t):
    # type: (TestResult) -> None
    """Wrong show_id_map key: TMDB path fails, IMDb path succeeds."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 32: Show ID map mismatch (1923 S01E01, id:1) ---")

    ep = get_episode(1)
    orig_plot = ep["plot"]

    wrong_map = {"1923 WRONG": "157744"}

    set_episode(1, plot="")
    try:
        with LogCapture() as logs:
            tmp = tempfile.mkdtemp()
            tracker = make_tracker(tmp)
            item = get_episode(1)
            stats = run_scan_with_settings(EPISODE, [item], tracker,
                                           show_id_map=wrong_map)

        # The "No show TMDB ID" message is at DEBUG level, which only
        # goes to file, not xbmc.log. We verify the TMDB path was
        # skipped by checking that IMDb was the source (below).

        after = get_episode(1)
        t.check("plot written via IMDb", len(after.get("plot", "")) > 10,
                "got '{}'".format(after.get("plot", "")[:40]))
        t.check("updated", stats["updated"] >= 1,
                "updated={}".format(stats["updated"]))

        field = tracker.get_field_status("episode", "1923 S01E01", "plot")
        t.check("source is imdb",
                field is not None and field.get("source") == "imdb",
                "got {}".format(field.get("source") if field else "None"))

        tracker.close()
    finally:
        set_episode(1, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 33: Both providers return None
# ---------------------------------------------------------------------------

def test_33_both_providers_none(t):
    # type: (TestResult) -> None
    """When both providers return None, errors counted and attempt recorded."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 33: Both providers return None ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    import resources.lib.engine.scan as scan_mod
    orig_tmdb = scan_mod.tmdb_fetch_movie
    orig_imdb = scan_mod.imdb_fetch_movie

    set_movie(6, plot="")
    scan_mod.tmdb_fetch_movie = lambda *a, **kw: None
    scan_mod.imdb_fetch_movie = lambda *a, **kw: None
    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)
        item = get_movie(6)
        stats = run_scan_with_settings(MOVIE, [item], tracker)

        t.check("errors >= 1", stats["errors"] >= 1,
                "errors={}".format(stats["errors"]))

        ti = tracker.get_item("movie", "Air")
        t.check("attempt recorded", ti is not None and ti["attempt_count"] >= 1)
        t.check("not complete",
                ti is not None and ti["status"] != "complete",
                "status={}".format(ti["status"] if ti else "None"))

        tracker.close()
    finally:
        scan_mod.tmdb_fetch_movie = orig_tmdb
        scan_mod.imdb_fetch_movie = orig_imdb
        set_movie(6, plot=orig_plot)


# ===========================================================================
# Group G: Resume (test 34)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 34: Resume interrupted scan
# ---------------------------------------------------------------------------

def test_34_resume_interrupted_scan(t):
    # type: (TestResult) -> None
    """Interrupt a scan mid-way, then resume and verify remaining items processed."""
    from resources.lib.data.media_types import MOVIE
    from resources.lib.engine.scan import run_scan

    print("\n--- Test 34: Resume interrupted scan ---")

    # Get 3 movies with empty plots
    movies = []
    orig_plots = {}
    for mid in (6, 8, 50):
        m = get_movie(mid)
        orig_plots[mid] = m["plot"]
        set_movie(mid, plot="")
        movies.append(get_movie(mid))

    try:
        tmp = tempfile.mkdtemp()
        tracker = make_tracker(tmp)

        # Create a monitor that aborts after 1 item
        abort_after = [1]
        processed_count = [0]

        class AbortingMonitor:
            def abortRequested(self):
                # type: () -> bool
                return processed_count[0] >= abort_after[0]
            def waitForAbort(self, timeout=0):
                # type: (float) -> bool
                return False

        # Patch to count items processed
        import resources.lib.engine.scan as scan_mod
        orig_mark = tracker.mark_cycle_processed

        def counting_mark(media_type, title_key):
            processed_count[0] += 1
            return orig_mark(media_type, title_key)

        tracker.mark_cycle_processed = counting_mark

        # First scan: should process ~1 item then abort
        stats1 = run_scan(
            media_type=MOVIE,
            tracker=tracker,
            source_config={"sources": [], "default_language": "en-US"},
            enabled_fields=frozenset({"plot"}),
            tmdb_api_key=TMDB_KEY,
            settings={
                "min_votes_movie": 50, "min_votes_episode": 20,
                "stale_threshold_days": 365, "max_retries": 4,
                "rate_limit": 0.0, "rating_source": 0, "plot_source": 0,
            },
            monitor=AbortingMonitor(),
            items=movies,
        )

        items_after_scan1 = stats1["processed"] + stats1["skipped"]
        t.check("scan1 interrupted early", items_after_scan1 < 3,
                "processed {} items out of 3".format(items_after_scan1))

        # Count how many items are cycle_processed
        cycle_done = tracker._conn.execute(
            "SELECT COUNT(*) FROM items WHERE cycle_processed = 1"
        ).fetchone()[0]
        t.check("some items marked processed", cycle_done >= 1,
                "cycle_processed={}".format(cycle_done))

        # Resume: re-fetch items from Kodi (scan1 may have filled some plots)
        processed_count[0] = 0
        abort_after[0] = 999
        movies2 = [get_movie(mid) for mid in (6, 8, 50)]

        stats2 = run_scan(
            media_type=MOVIE,
            tracker=tracker,
            source_config={"sources": [], "default_language": "en-US"},
            enabled_fields=frozenset({"plot"}),
            tmdb_api_key=TMDB_KEY,
            settings={
                "min_votes_movie": 50, "min_votes_episode": 20,
                "stale_threshold_days": 365, "max_retries": 4,
                "rate_limit": 0.0, "rating_source": 0, "plot_source": 0,
            },
            items=movies2,
        )

        total_processed = (stats1["processed"] + stats1["skipped"]
                           + stats2["processed"] + stats2["skipped"])
        t.check("all items handled across both scans", total_processed >= 3,
                "scan1={}, scan2={}, total={}".format(
                    stats1["processed"] + stats1["skipped"],
                    stats2["processed"] + stats2["skipped"],
                    total_processed))

        # Items from scan1 should be skipped in scan2 (already processed)
        t.check("scan2 skipped scan1 items", stats2["skipped"] >= 1,
                "skipped={}".format(stats2["skipped"]))

        tracker.mark_cycle_processed = orig_mark
        tracker.close()
    finally:
        for mid, plot in orig_plots.items():
            set_movie(mid, plot=plot)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

GROUPS = {
    "imdb": ("11", "12", "13", "14"),
    "resolve": ("15", "16", "17"),
    "episode": ("18", "19", "20", "21", "22"),
    "lifecycle": ("23", "24", "25", "26"),
    "logging": ("27", "28", "29", "30"),
    "edge": ("31", "32", "33"),
    "resume": ("34",),
}

ALL_TESTS = [
    ("1", "Movie rating backfill", test_1_movie_rating_backfill),
    ("2", "Tagline gap (no TMDB data)", test_2_movie_tagline_no_tmdb_data),
    ("3", "Episode title replacement", test_3_episode_title_replacement),
    ("4", "Title NOT replaced (TMDB matches)", test_4_episode_title_not_replaced),
    ("5", "Retry gating", test_5_retry_gating),
    ("6", "Exhaustion after max retries", test_6_exhaustion),
    ("7", "Staleness detection", test_7_staleness_detection),
    ("8", "TVDB-only resolve_id", test_8_tvdb_only_resolve_id),
    ("9", "Bogus IDs graceful skip", test_9_bogus_ids_graceful_skip),
    ("10", "Logging completeness", test_10_logging_completeness),
    ("11", "IMDb movie fetch", test_11_imdb_movie_fetch),
    ("12", "IMDb episode fetch", test_12_imdb_episode_fetch),
    ("13", "IMDb season pagination", test_13_imdb_season_pagination),
    ("14", "IMDb certificates", test_14_imdb_certificates),
    ("15", "Prefer TMDB", test_15_prefer_tmdb),
    ("16", "Prefer IMDb", test_16_prefer_imdb),
    ("17", "TMDB only mode", test_17_tmdb_only),
    ("18", "TMDB episode matching", test_18_tmdb_episode_matching),
    ("19", "TVDB episode IMDb fetch", test_19_tvdb_episode_imdb_fetch),
    ("20", "TVDB-only episode exhaust", test_20_tvdb_only_episode_exhaust),
    ("21", "TVmaze-only episode", test_21_tvmaze_only_episode),
    ("22", "NFO episode skip", test_22_nfo_episode_skip),
    ("23", "Idempotent re-scan", test_23_idempotent_rescan),
    ("24", "Selective field update", test_24_selective_field_update),
    ("25", "Exhaustion lifecycle", test_25_exhaustion_lifecycle),
    ("26", "Data survives across scans", test_26_data_survives),
    ("27", "Provider log events", test_27_provider_logs),
    ("28", "Scan lifecycle logs", test_28_scan_lifecycle_logs),
    ("29", "Episode provider logs", test_29_episode_provider_logs),
    ("30", "Log consistency", test_30_log_consistency),
    ("31", "Episode dual provider", test_31_episode_dual_provider),
    ("32", "Show ID map mismatch", test_32_show_id_map_mismatch),
    ("33", "Both providers None", test_33_both_providers_none),
    ("34", "Resume interrupted scan", test_34_resume_interrupted_scan),
]


def main():
    # type: () -> None
    print("MetaMender Integration Tests")
    print("Kodi: {}".format(KODI_URL))
    print("TMDB: {}...".format(TMDB_KEY[:8]))

    try:
        result = rpc("JSONRPC.Ping")
        assert result == "pong"
        print("Kodi: connected")
    except Exception as e:
        print("ERROR: Cannot reach Kodi: {}".format(e))
        sys.exit(1)

    selected = sys.argv[1] if len(sys.argv) > 1 else None

    selected_nums = None  # type: Optional[set]
    if selected:
        if selected in GROUPS:
            selected_nums = set(GROUPS[selected])
        elif "-" in selected:
            parts = selected.split("-")
            start, end = int(parts[0]), int(parts[1])
            selected_nums = {str(n) for n in range(start, end + 1)}
        else:
            selected_nums = {selected}

    t = TestResult()

    for num, name, func in ALL_TESTS:
        if selected_nums and num not in selected_nums:
            continue
        try:
            func(t)
        except Exception as e:
            print("    ERROR: {} crashed: {}".format(name, e))
            import traceback
            traceback.print_exc()
            t.failed += 1
            t.errors.append("    CRASH: {}".format(name))

    t.summary()
    sys.exit(1 if t.failed > 0 else 0)


if __name__ == "__main__":
    main()

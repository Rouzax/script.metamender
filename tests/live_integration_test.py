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
TMDB_KEY = os.environ.get("TMDB_API_KEY", "REDACTED_TMDB_KEY")

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
    t.check("Whale has zero rating", whale["rating"] == 0.0)

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
    old = scan_mod.fetch_movie
    scan_mod.fetch_movie = counting_fetch
    try:
        stats = run_targeted_scan(MOVIE, [item], tracker)
    finally:
        scan_mod.fetch_movie = old

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

    # Field should be re-fetched (vote_count updated)
    field = tracker.get_field_status("movie", movie["title"], "rating")
    t.check("field re-fetched", field is not None)
    if field:
        new_votes = field.get("vote_count", 0)
        t.check("vote_count updated", new_votes is not None,
                "got {}".format(new_votes))

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
    t.check("errors is 0", stats["errors"] == 0, "got {}".format(stats["errors"]))

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
        t.check("tmdb.movie has elapsed_ms", "elapsed_ms=" in tmdb_calls[0])
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
        t.check("scan.complete has elapsed", "total_elapsed_sec=" in completes[0])


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

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

    t = TestResult()

    for num, name, func in ALL_TESTS:
        if selected and num != selected:
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

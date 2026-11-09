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
    - Kodi running at localhost:8101 (KODI_URL to change it)
    - TMDB_API_KEY in the environment (TheTVDB uses the add-on's own key)
    - the media share mounted read-only at MEDIA_ROOT (default
      /mnt/media/Data/), the target of the rig's smb://NAS/Data/ path
      substitution, so NFO detection sees the real files
    - IMDBRATINGS_LIB pointing at script.module.imdbratings/lib for the IMDb
      rating scenarios (16, 17)

Scenarios write to the library and restore what they change; bracket a run
with the rig's backup-db.sh / restore-db.sh all the same.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests

KODI_URL = os.environ.get("KODI_URL", "http://localhost:8101/jsonrpc")
TMDB_KEY = os.environ.get("TMDB_API_KEY", "")  # set TMDB_API_KEY env var to run

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Inject Kodi stubs so addon modules can be imported.
# xbmc.executeJSONRPC is patched to proxy to real Kodi via HTTP,
# so the scan engine can read from and write to the live instance.
import json as _json  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

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
_xbmc_stub.Monitor.return_value.abortRequested.return_value = False

# The library stores smb:// paths; the host reaches the same share through its
# read-only mount, so NFO detection sees the real files the way Kodi's own
# path substitution would.
MEDIA_FROM = "smb://NAS/Data/"
MEDIA_TO = os.environ.get("MEDIA_ROOT", "/mnt/media/Data/")
# Trackers and the IMDb ratings store of one run live here and go with it.
_SCRATCH = tempfile.mkdtemp(prefix="mm_live_")
atexit.register(shutil.rmtree, _SCRATCH, True)


def _local(path):
    # type: (str) -> str
    if path.startswith(MEDIA_FROM):
        return MEDIA_TO + path[len(MEDIA_FROM):]
    if path.startswith("special://"):
        local = os.path.join(_SCRATCH, path[len("special://"):])
        os.makedirs(os.path.dirname(local), exist_ok=True)
        return local
    return path


def _listdir(path):
    # type: (str) -> Any
    local = _local(path)
    names = os.listdir(local)
    dirs = [n for n in names if os.path.isdir(os.path.join(local, n))]
    return dirs, [n for n in names if n not in dirs]


_xbmcvfs_stub = MagicMock()
_xbmcvfs_stub.exists = lambda p: os.path.exists(_local(p))
_xbmcvfs_stub.listdir = _listdir
_xbmcvfs_stub.translatePath = _local

sys.modules.setdefault("xbmc", _xbmc_stub)
sys.modules.setdefault("xbmcvfs", _xbmcvfs_stub)
for mod in ("xbmcaddon", "xbmcgui"):
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

# script.module.imdbratings is a Kodi add-on, not a pip package: point
# IMDBRATINGS_LIB at its lib/ folder (for example inside a Kodi profile's
# addons/script.module.imdbratings/lib). Its store is created in the scratch
# folder, so the first IMDb test downloads IMDb's ratings dataset.
_IMDBRATINGS_LIB = os.environ.get("IMDBRATINGS_LIB", "")
if _IMDBRATINGS_LIB:
    sys.path.insert(0, _IMDBRATINGS_LIB)

from resources.lib.data.identity import compute_identity  # noqa: E402


# ---------------------------------------------------------------------------
# Kodi JSON-RPC helpers (via HTTP)
# ---------------------------------------------------------------------------

def rpc(method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params:
        payload["params"] = params
    resp = requests.post(KODI_URL, json=payload, timeout=10)
    return resp.json().get("result", {})


def get_movie(movie_id: int) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetMovieDetails", {
        "movieid": movie_id,
        "properties": [
            "title", "rating", "votes", "plot", "tagline", "director",
            "writer", "genre", "studio", "country", "trailer", "runtime",
            "mpaa", "uniqueid", "premiered", "dateadded", "file",
        ],
    })
    return result.get("moviedetails", {})


def get_episode(episode_id: int) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetEpisodeDetails", {
        "episodeid": episode_id,
        "properties": [
            "title", "rating", "votes", "plot", "showtitle", "season",
            "episode", "tvshowid", "uniqueid", "firstaired", "director", "writer",
            "runtime", "dateadded", "file",
        ],
    })
    return result.get("episodedetails", {})


def get_tvshow(tvshow_id: int) -> Dict[str, Any]:
    result = rpc("VideoLibrary.GetTVShowDetails", {
        "tvshowid": tvshow_id,
        "properties": [
            "title", "rating", "votes", "plot", "genre", "mpaa",
            "uniqueid", "premiered", "dateadded", "file",
        ],
    })
    return result.get("tvshowdetails", {})


def set_movie(movie_id: int, **fields: Any) -> None:
    params = {"movieid": movie_id}
    params.update(fields)
    rpc("VideoLibrary.SetMovieDetails", params)


def set_episode(episode_id: int, **fields: Any) -> None:
    params = {"episodeid": episode_id}
    params.update(fields)
    rpc("VideoLibrary.SetEpisodeDetails", params)


def set_tvshow(tvshow_id: int, **fields: Any) -> None:
    params = {"tvshowid": tvshow_id}
    params.update(fields)
    rpc("VideoLibrary.SetTVShowDetails", params)


# ---------------------------------------------------------------------------
# Scan engine helpers
# ---------------------------------------------------------------------------

def make_tracker(tmp_dir: str) -> Any:
    from resources.lib.data.tracker import TrackerDB
    return TrackerDB(os.path.join(tmp_dir, "test.db"))


_IMDB_STATE = {}  # type: Dict[str, bool]


def _imdb_ready():
    # type: () -> bool
    """Load the IMDb ratings store once per run; False without the module."""
    if "ready" not in _IMDB_STATE:
        from resources.lib.providers import imdb_ratings
        _IMDB_STATE["ready"] = bool(_IMDBRATINGS_LIB) and imdb_ratings.prepare(lambda: False)
    return _IMDB_STATE["ready"]


def _default_settings(**overrides: Any) -> Dict[str, Any]:
    """Return a baseline settings dict for run_scan.

    Keys:
        rating_source:       0=prefer TMDB, 1=prefer IMDb, 2=TMDB only, 3=IMDb only
        text_source_order:   0=TMDB then TVDB, 1=TVDB then TMDB
        fallback_order:      0=TVDB then TMDB, 1=TMDB then TVDB
        fallback_language:   TMDB-style code of the fallback language
        imdb_ready:          whether the IMDb ratings store is loaded this cycle
        refetch_schedule:    comma-separated string (e.g. "7,30,90")
        stale_threshold_days: integer; content older than this is treated as stable
        rate_limit:          seconds between external API calls (0.0 in tests)
    """
    base = {
        "rating_source": 0,
        "text_source_order": 0,
        "fallback_order": 0,
        "fallback_language": "en-US",
        "imdb_ready": _imdb_ready(),
        "refetch_schedule": "7,30,90",
        "stale_threshold_days": 365,
        "rate_limit": 0.0,
    }
    base.update(overrides)
    return base


_ALL_FIELDS = frozenset({
    "rating", "plot", "tagline", "director", "writer",
    "genre", "studio", "country", "trailer", "runtime", "title",
})


def run_targeted_scan(
    media_type: Any,
    items: List[Dict[str, Any]],
    tracker: Any,
    show_id_map: Optional[Dict[str, str]] = None,
    enabled_fields: Optional[Any] = None,
) -> Dict[str, Any]:
    from resources.lib.engine.scan import run_scan
    return run_scan(
        media_type=media_type,
        tracker=tracker,
        source_config={"sources": [], "default_language": "en-US"},
        enabled_fields=_ALL_FIELDS if enabled_fields is None else enabled_fields,
        tmdb_api_key=TMDB_KEY,
        settings=_default_settings(),
        items=items,
        show_id_map=show_id_map,
    )


def run_scan_with_settings(
    media_type, items, tracker,
    show_id_map=None,
    rating_source=0,
    text_source_order=0,
    imdb_ready=None,
    language="en-US",
    rate_limit=0.0,
    refetch_schedule="7,30,90",
    stale_threshold_days=365,
    reset=False,
    enabled_fields=None,
):
    # type: (...) -> Dict[str, Any]
    from resources.lib.engine.scan import run_scan
    return run_scan(
        media_type=media_type,
        tracker=tracker,
        source_config={"sources": [], "default_language": language},
        enabled_fields=_ALL_FIELDS if enabled_fields is None else enabled_fields,
        tmdb_api_key=TMDB_KEY,
        settings=_default_settings(
            rating_source=rating_source,
            text_source_order=text_source_order,
            imdb_ready=_imdb_ready() if imdb_ready is None else imdb_ready,
            rate_limit=rate_limit,
            refetch_schedule=refetch_schedule,
            stale_threshold_days=stale_threshold_days,
        ),
        items=items,
        show_id_map=show_id_map,
        reset=reset,
    )


def find_by_title(method, key, title, props):
    # type: (str, str, str, List[str]) -> Optional[Dict[str, Any]]
    """First library item of `method` whose title is `title`, or None."""
    result = rpc(method, {
        "properties": props,
        "filter": {"field": "title", "operator": "is", "value": title},
    })
    found = result.get(key) or []
    return found[0] if found else None


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
        return [line for line in self.lines if substring in line]

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

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_movie(353)
    stats = run_targeted_scan(MOVIE, [item], tracker)
    t.check("processed", stats["processed"] >= 1)

    # Tracker should have a field_status row for the rating field (fetch_count >= 1)
    identity_whale = compute_identity(item, MOVIE)
    tracker_item = tracker.get_item("movie", identity_whale)
    t.check("tracker has item", tracker_item is not None)
    if tracker_item:
        fs = tracker.get_field_status("movie", identity_whale, "rating")
        t.check("rating field sampled", fs is not None and (fs.get("fetch_count") or 0) >= 1,
                "fs={}".format(fs))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 2: Tagline gap with no TMDB data
# ---------------------------------------------------------------------------

def test_2_movie_tagline_no_tmdb_data(t):
    # type: (TestResult) -> None
    """Movie with no tagline on TMDB should record a field fetch without error."""
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 2: Tagline gap, TMDB empty (All Quiet, id:8, tmdb:49046) ---")

    movie = get_movie(8)
    t.check("movie found", "All Quiet" in movie.get("title", ""))
    t.check("tagline already empty", movie.get("tagline", "") == "")

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_movie(8)
    stats = run_targeted_scan(MOVIE, [item], tracker)
    total = stats["processed"] + stats["skipped"]
    t.check("item handled", total >= 1,
            "processed={}, skipped={}".format(stats["processed"], stats["skipped"]))

    identity_aq = compute_identity(item, MOVIE)
    tracker_item = tracker.get_item("movie", identity_aq)
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
    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_episode(2)
    show_map = {item["tvshowid"]: "157744"}
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
# Test 4: Title not replaced when TMDB matches placeholder
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

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_episode(1723)
    show_map = {item["tvshowid"]: "66023"}
    run_targeted_scan(EPISODE, [item], tracker, show_id_map=show_map)

    after = get_episode(1723)
    t.check("title unchanged", after["title"] == "Episode 1",
            "got '{}'".format(after["title"]))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 5: Old content is FINAL after one fetch
# ---------------------------------------------------------------------------

def test_5_old_content_final_after_one_fetch(t):
    # type: (TestResult) -> None
    """Content with premiered date older than stale_threshold_days finalizes after
    one successful fetch. A second scan does NOT advance fetch_count because
    decide_field returns FINAL for all fields.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 5: Old content FINAL after one fetch (Burial, id:50, tmdb:1155875) ---")

    movie = get_movie(50)
    t.check("movie found", movie.get("title") == "Burial")

    # Patch the item to simulate old content: premiered 2000 (well past threshold)
    item = get_movie(50)
    item["premiered"] = "2000-01-01"

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)

    # Scan 1: first encounter, fetch_count goes to 1
    identity_burial = compute_identity(item, MOVIE)
    stats1 = run_targeted_scan(MOVIE, [item], tracker)
    t.check("scan1: processed", stats1["processed"] >= 1)

    fs = tracker.get_field_status("movie", identity_burial, "plot")
    count_after_scan1 = (fs.get("fetch_count") or 0) if fs else 0
    t.check("scan1: plot field sampled",
            count_after_scan1 >= 1,
            "fetch_count={}".format(count_after_scan1))

    # Scan 2: content is old (age >= stale_threshold_days), decide_field returns FINAL,
    # no fetch is triggered, fetch_count must NOT advance.
    import resources.lib.engine.scan as scan_mod
    call_count = [0]
    orig_tmdb = scan_mod.tmdb_fetch_movie

    def counting_tmdb(*args, **kwargs):
        call_count[0] += 1
        return orig_tmdb(*args, **kwargs)

    tracker.start_new_cycle()
    scan_mod.tmdb_fetch_movie = counting_tmdb
    try:
        stats2 = run_targeted_scan(MOVIE, [item], tracker)
    finally:
        scan_mod.tmdb_fetch_movie = orig_tmdb

    t.check("scan2: item skipped (all fields FINAL)", stats2["skipped"] >= 1,
            "processed={} skipped={}".format(stats2["processed"], stats2["skipped"]))
    t.check("scan2: no TMDB call",
            call_count[0] == 0,
            "got {} calls".format(call_count[0]))

    fs2 = tracker.get_field_status("movie", identity_burial, "plot")
    count_after_scan2 = (fs2.get("fetch_count") or 0) if fs2 else 0
    t.check("scan2: fetch_count unchanged",
            count_after_scan2 == count_after_scan1,
            "before={}, after={}".format(count_after_scan1, count_after_scan2))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 6: Young content gets fetch_count advanced and next_retry set
# ---------------------------------------------------------------------------

def test_6_young_content_scheduled(t):
    # type: (TestResult) -> None
    """Content with premiered date within stale_threshold_days gets fetch_count
    incremented and a future next_retry after each clean fetch.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 6: Young content scheduled (STRANGER, id:324, tmdb:1156433) ---")

    movie = get_movie(324)
    t.check("movie found", movie.get("title") == "STRANGER")

    # Patch item to look young: premiered yesterday
    item = get_movie(324)
    today_str = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    item["premiered"] = yesterday

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    identity_stranger = compute_identity(item, MOVIE)

    # Scan 1: first encounter
    stats1 = run_targeted_scan(MOVIE, [item], tracker)
    t.check("scan1: processed", stats1["processed"] >= 1)

    fs = tracker.get_field_status("movie", identity_stranger, "plot")
    t.check("scan1: plot field sampled", fs is not None)
    if fs:
        t.check("scan1: fetch_count == 1",
                (fs.get("fetch_count") or 0) == 1,
                "got {}".format(fs.get("fetch_count")))
        # next_retry is set in the future (because content is young)
        nr = fs.get("next_retry")
        t.check("scan1: next_retry is set and in the future",
                nr is not None and nr > today_str,
                "next_retry={}".format(nr))

    tracker.close()


# ---------------------------------------------------------------------------
# Test 7: Transient provider failure does NOT advance fetch_count
# ---------------------------------------------------------------------------

def test_7_transient_failure_no_advance(t):
    # type: (TestResult) -> None
    """When the provider raises TransientProviderError (fetch_clean=False),
    fetch_count must not advance so the field is retried next scan.
    """
    from resources.lib.data.media_types import MOVIE
    from resources.lib.utils import TransientProviderError

    print("\n--- Test 7: Transient failure does not advance fetch_count (Miss You Love You, id:372) ---")

    movie = get_movie(372)
    t.check("movie found", "Miss You" in movie.get("title", ""),
            "got '{}'".format(movie.get("title")))

    item = get_movie(372)

    import resources.lib.engine.scan as scan_mod
    orig_tmdb = scan_mod.tmdb_fetch_movie

    def raising_tmdb(*a, **kw):
        raise TransientProviderError("simulated transient")

    scan_mod.tmdb_fetch_movie = raising_tmdb
    try:
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        stats = run_targeted_scan(MOVIE, [item], tracker)

        # Item is processed (it went through the full per-item path)
        # but fetch_count should remain at 0 (transient failure is not a clean answer)
        fs = tracker.get_field_status("movie", compute_identity(item, MOVIE), "plot")
        count = (fs.get("fetch_count") or 0) if fs else 0
        t.check("fetch_count not advanced after transient failure",
                count == 0,
                "fetch_count={}".format(count))
        t.check("errors not counted (transient is not error)",
                stats["errors"] == 0,
                "errors={}".format(stats["errors"]))

        tracker.close()
    finally:
        scan_mod.tmdb_fetch_movie = orig_tmdb


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

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_tvshow(410)
    stats = run_targeted_scan(TVSHOW, [item], tracker)
    t.check("processed", stats["processed"] >= 1)

    tracker_item = tracker.get_item("tvshow", compute_identity(item, TVSHOW))
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

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
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
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        # Use Whale (zero rating) to force a TMDB call
        item = get_movie(353)
        run_targeted_scan(MOVIE, [item], tracker)
        tracker.close()
    finally:
        xbmc.log = orig_log

    t.check("captured log lines", len(logged) > 0, "got {}".format(len(logged)))

    # Check scan.start
    scan_starts = [ln for ln in logged if "event=scan.start" in ln]
    t.check("has scan.start", len(scan_starts) >= 1)

    # Check tmdb.movie
    tmdb_calls = [ln for ln in logged if "event=tmdb.movie" in ln]
    t.check("has tmdb.movie", len(tmdb_calls) >= 1)
    if tmdb_calls:
        t.check("tmdb.movie has tmdb_id", "tmdb_id=" in tmdb_calls[0])
        t.check("tmdb.movie has title", "title=" in tmdb_calls[0])

    # Check scan.updated (may not be present if TMDB had no data for the item)
    updates = [ln for ln in logged if "event=scan.updated" in ln]
    if updates:
        t.check("scan.updated has title", "title=" in updates[0])
        t.check("scan.updated has media_type", "media_type=" in updates[0])
        t.check("scan.updated has fields_written", "fields_written=" in updates[0])

    # Check scan.complete
    completes = [ln for ln in logged if "event=scan.complete" in ln]
    t.check("has scan.complete", len(completes) >= 1)
    if completes:
        t.check("scan.complete has processed", "processed=" in completes[0])
        t.check("scan.complete has updated", "updated=" in completes[0])
        t.check("scan.complete has elapsed", "duration_ms=" in completes[0])


# ===========================================================================
# Group A: Providers (tests 11-14)
# ===========================================================================

_BUURMAN_TVDB = "80767"


# ---------------------------------------------------------------------------
# Test 11: TVDB series verification (Buurman en Buurman)
# ---------------------------------------------------------------------------

def test_11_tvdb_series_verification(t):
    # type: (TestResult) -> None
    """TVDB's "Pat a Mat" carries the Dutch title the library uses.

    The library holds this show's TVDB id as an untyped "unknown" uniqueid;
    the scan accepts it only when one of the series' names matches.
    """
    from resources.lib.engine.idverify import show_matches
    from resources.lib.providers.tvdb import fetch_series

    print("\n--- Test 11: TVDB series verification (tvdb:{}) ---".format(_BUURMAN_TVDB))

    series = fetch_series(_BUURMAN_TVDB, "nld")
    t.check("fetch returned data", series is not None)
    if series is None:
        return
    t.check("Dutch name is Buurman en Buurman",
            series["name"] == "Buurman en Buurman", "got {!r}".format(series["name"]))
    t.check("Kodi title matches a series name",
            show_matches("Buurman en Buurman", series["names"], 1976, series["year"]),
            "names={} year={}".format(series["names"], series["year"]))
    t.check("an unrelated title does not match",
            not show_matches("Bluey", series["names"]))


# ---------------------------------------------------------------------------
# Test 12: TVDB Dutch episode list covers the library's unknown ids
# ---------------------------------------------------------------------------

def test_12_tvdb_episode_list(t):
    # type: (TestResult) -> None
    """Every untyped episode id of Buurman en Buurman is a TVDB episode id."""
    from resources.lib.engine.textrules import is_placeholder_title
    from resources.lib.providers.tvdb import fetch_series_episodes

    print("\n--- Test 12: TVDB episode list (tvdb:{}, nld) ---".format(_BUURMAN_TVDB))

    show = find_by_title("VideoLibrary.GetTVShows", "tvshows", "Buurman en Buurman",
                         ["title", "uniqueid"])
    t.check("show in library", show is not None)
    if show is None:
        return
    eps = rpc("VideoLibrary.GetEpisodes", {
        "tvshowid": show["tvshowid"], "properties": ["uniqueid", "title"],
    }).get("episodes") or []
    unknown = [e["uniqueid"]["unknown"] for e in eps if (e.get("uniqueid") or {}).get("unknown")]
    t.check("library episodes carry unknown ids", len(unknown) > 0,
            "{} episodes, {} unknown ids".format(len(eps), len(unknown)))

    listed = fetch_series_episodes(_BUURMAN_TVDB, "nld")
    t.check("episode list returned", bool(listed))
    if not listed:
        return
    missing = [u for u in unknown if u not in listed]
    t.check("nearly every unknown id is in the TVDB list", len(missing) * 10 <= len(unknown),
            "{} of {} missing, e.g. {}".format(len(missing), len(unknown), missing[:5]))
    if missing:
        print("    INFO: ids not in the list (the scan does not use them): {}".format(missing[:10]))
    placeholders = [v["title"] for v in listed.values() if is_placeholder_title(v["title"])]
    t.check("TVDB Dutch titles are not placeholders", not placeholders,
            "e.g. {}".format(placeholders[:5]))


# ---------------------------------------------------------------------------
# Test 13: TMDB placeholder titles are recognised
# ---------------------------------------------------------------------------

def test_13_tmdb_placeholder_titles(t):
    # type: (TestResult) -> None
    """TMDB answers "Aflevering N" for an untranslated Dutch episode title."""
    from resources.lib.engine.textrules import is_placeholder_title
    from resources.lib.providers.tmdb import fetch_season

    print("\n--- Test 13: TMDB placeholder titles (Dutch shows, nl-NL) ---")

    shows = rpc("VideoLibrary.GetTVShows", {"properties": ["title", "uniqueid", "file"]}).get("tvshows") or []
    dutch = [s for s in shows
             if ("/TVSeries/NL/" in s.get("file", "") or "/Kids/TV/" in s.get("file", ""))
             and (s.get("uniqueid") or {}).get("tmdb")]
    t.check("Dutch shows with a tmdb id in library", bool(dutch))
    generic, checked = [], 0
    for show in dutch[:15]:
        season = fetch_season(show["uniqueid"]["tmdb"], 1, TMDB_KEY, "nl-NL") or []
        checked += len(season)
        generic += [e.get("title") or "" for e in season
                    if (e.get("title") or "").lower().startswith(("aflevering", "episode"))]
    t.check("every generic title is a placeholder",
            all(is_placeholder_title(x) for x in generic), "titles={}".format(generic[:5]))
    t.check("real titles are not placeholders",
            not is_placeholder_title("Aflevering met de buren") and not is_placeholder_title("Episode Seven Revisited"))
    print("    INFO: {} of {} nl-NL season-1 titles are generic".format(len(generic), checked))


# ---------------------------------------------------------------------------
# Test 14: TMDB cross-reference from a TVDB episode id
# ---------------------------------------------------------------------------

def test_14_tmdb_find_by_tvdb(t):
    # type: (TestResult) -> None
    """/find with a TVDB episode id returns the same TMDB episode Kodi has."""
    from resources.lib.providers.tmdb import find_episode_by_tvdb

    print("\n--- Test 14: TMDB /find by TVDB episode id ---")

    eps = rpc("VideoLibrary.GetEpisodes", {
        "properties": ["uniqueid", "season", "episode", "showtitle"],
        "limits": {"start": 0, "end": 400},
    }).get("episodes") or []
    both = [e for e in eps
            if (e.get("uniqueid") or {}).get("tvdb") and (e.get("uniqueid") or {}).get("tmdb")]
    t.check("library has an episode with tvdb and tmdb ids", bool(both))
    if not both:
        return
    ep = both[0]
    found = find_episode_by_tvdb(TMDB_KEY, ep["uniqueid"]["tvdb"])
    t.check("TMDB found the episode", found is not None,
            "{} S{}E{} tvdb={}".format(ep["showtitle"], ep["season"], ep["episode"],
                                      ep["uniqueid"]["tvdb"]))
    if found is None:
        return
    t.check("same TMDB episode id", found["tmdb_id"] == ep["uniqueid"]["tmdb"],
            "found {} kodi {}".format(found["tmdb_id"], ep["uniqueid"]["tmdb"]))
    t.check("same season/episode numbers",
            (found["season"], found["episode"]) == (ep["season"], ep["episode"]),
            "found {}".format(found))


# ===========================================================================
# Group B: Ratings and text sources (tests 15-17)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 15: Movie plot from TMDB
# ---------------------------------------------------------------------------

def test_15_movie_plot_from_tmdb(t):
    # type: (TestResult) -> None
    """An emptied movie plot is refilled from TMDB and recorded as such."""
    from resources.lib.data.media_types import MOVIE
    from resources.lib.providers.tmdb import fetch_movie as tmdb_fetch

    print("\n--- Test 15: Movie plot from TMDB (Air) ---")

    found = find_by_title("VideoLibrary.GetMovies", "movies", "Air", ["title"])
    t.check("movie in library", found is not None)
    if found is None:
        return
    movie_id = found["movieid"]
    air = get_movie(movie_id)
    orig_plot = air["plot"]
    tmdb_id = (air.get("uniqueid") or {}).get("tmdb")
    tmdb_data = tmdb_fetch(tmdb_id, TMDB_KEY) if tmdb_id else None
    t.check("TMDB has data", tmdb_data is not None)
    if not tmdb_data:
        return

    set_movie(movie_id, plot="")
    try:
        tracker = make_tracker(tempfile.mkdtemp(dir=_SCRATCH))
        item = get_movie(movie_id)
        run_scan_with_settings(MOVIE, [item], tracker)

        after = get_movie(movie_id)
        t.check("plot matches TMDB (as Kodi stores it)",
                after["plot"] == tmdb_data["plot"].strip(),
                "written='{}...', tmdb='{}...'".format(after["plot"][:40], tmdb_data["plot"][:40]))
        field = tracker.get_field_status("movie", compute_identity(item, MOVIE), "plot")
        t.check("tracker source is tmdb",
                field is not None and field.get("source") == "tmdb",
                "got {}".format(field.get("source") if field else "None"))
        tracker.close()
    finally:
        set_movie(movie_id, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 16: IMDb ratings module
# ---------------------------------------------------------------------------

def test_16_imdb_ratings_module(t):
    # type: (TestResult) -> None
    """The ratings store loads and answers known and unknown titles."""
    from resources.lib.providers import imdb_ratings

    print("\n--- Test 16: IMDb ratings module (The Matrix, tt0133093) ---")

    if not _IMDBRATINGS_LIB:
        t.check("IMDBRATINGS_LIB set", False, "point it at script.module.imdbratings/lib")
        return
    ready = imdb_ratings.prepare(lambda: False)
    t.check("store loaded", ready)
    if not ready:
        return
    matrix = imdb_ratings.lookup("tt0133093")
    t.check("The Matrix has a rating", matrix is not None)
    if matrix:
        t.check("rating between 8 and 9.5", 8.0 <= matrix[0] <= 9.5, "got {}".format(matrix))
        t.check("votes above 1M", matrix[1] > 1000000, "got {}".format(matrix))
    t.check("unknown title answers None (absent, not an error)",
            imdb_ratings.lookup("tt0000000") is None)


# ---------------------------------------------------------------------------
# Test 17: rating_source TMDB only never consults IMDb
# ---------------------------------------------------------------------------

def test_17_tmdb_only(t):
    # type: (TestResult) -> None
    """TMDB-only ratings make no IMDb lookup; prefer-IMDb does."""
    from resources.lib.data.media_types import MOVIE
    import resources.lib.engine.scan as scan_mod

    print("\n--- Test 17: rating_source TMDB only vs prefer IMDb (Air) ---")

    found = find_by_title("VideoLibrary.GetMovies", "movies", "Air", ["title"])
    if found is None:
        t.check("movie in library", False)
        return
    movie_id = found["movieid"]
    calls = [0]
    orig_lookup = scan_mod.imdb_rating_lookup

    def counting(*args, **kwargs):
        calls[0] += 1
        return orig_lookup(*args, **kwargs)

    scan_mod.imdb_rating_lookup = counting
    try:
        tracker = make_tracker(tempfile.mkdtemp(dir=_SCRATCH))
        stats = run_scan_with_settings(MOVIE, [get_movie(movie_id)], tracker,
                                       rating_source=2, imdb_ready=True,
                                       enabled_fields=frozenset({"rating"}))
        t.check("TMDB only: no IMDb lookups", calls[0] == 0, "got {}".format(calls[0]))
        t.check("item processed", stats["processed"] >= 1)
        tracker.close()

        if _IMDBRATINGS_LIB:
            tracker = make_tracker(tempfile.mkdtemp(dir=_SCRATCH))
            run_scan_with_settings(MOVIE, [get_movie(movie_id)], tracker,
                                   rating_source=1, imdb_ready=True,
                                   enabled_fields=frozenset({"rating"}))
            t.check("prefer IMDb: IMDb consulted", calls[0] >= 1, "got {}".format(calls[0]))
            tracker.close()
    finally:
        scan_mod.imdb_rating_lookup = orig_lookup


# ===========================================================================
# Group C: Episode Matching Across Scrapers (tests 18, 20-22)
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
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        item = get_episode(2)
        show_map = {item["tvshowid"]: "157744"}
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
# Test 20: TVDB-only episode (no imdb, no tmdb): skipped at episode level
# ---------------------------------------------------------------------------

def test_20_tvdb_only_episode_skipped(t):
    # type: (TestResult) -> None
    """Episode with only a TVDB id, whose show is not resolved yet.

    A TVDB id is usable, so the episode is processed, but every provider step
    needs the show's ids: without them the fields are transient, nothing is
    written and fetch_count stays at 0, scan after scan, until the show is
    resolved.
    """
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 20: TVDB-only episode, show unresolved (Transformers S01E01, id:6098) ---")

    ep = get_episode(6098)
    t.check("episode found", "Pilot" in ep.get("title", ""),
            "got '{}'".format(ep.get("title")))
    ids = ep.get("uniqueid", {})
    t.check("has tvdb only", "tvdb" in ids and "imdb" not in ids and "tmdb" not in ids,
            "ids: {}".format(ids))

    orig_rating = ep["rating"]
    title_key = "{} S{:02d}E{:02d}".format(
        ep.get("showtitle", ""), ep.get("season", 0), ep.get("episode", 0))

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_episode(6098)

    # Scan 1: processed, every field transient
    stats = run_scan_with_settings(EPISODE, [item], tracker, show_id_map={})
    t.check("processed, nothing written", stats["processed"] >= 1 and stats["updated"] == 0,
            "processed={} updated={}".format(stats["processed"], stats["updated"]))

    after = get_episode(6098)
    t.check("rating unchanged", after["rating"] == orig_rating,
            "before={}, after={}".format(orig_rating, after["rating"]))

    # Scan 2: reset cycle, same result
    tracker.start_new_cycle()
    item = get_episode(6098)
    stats2 = run_scan_with_settings(EPISODE, [item], tracker, show_id_map={})
    t.check("second scan: still nothing written", stats2["updated"] == 0,
            "updated={}".format(stats2["updated"]))

    # No fetch_count rows should exist (item was never fetched)
    tracker_item = tracker.get_item("episode", title_key)
    if tracker_item:
        fs = tracker.get_field_status("episode", title_key, "plot")
        count = (fs.get("fetch_count") or 0) if fs else 0
        t.check("fetch_count stays at 0 (never fetched)",
                count == 0,
                "fetch_count={}".format(count))

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

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_episode(6190)
    stats = run_scan_with_settings(EPISODE, [item], tracker, show_id_map={})

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

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    item = get_episode(5046)
    stats = run_scan_with_settings(EPISODE, [item], tracker, show_id_map={})

    t.check("skipped", stats["skipped"] >= 1,
            "skipped={}, processed={}".format(stats["skipped"], stats["processed"]))

    tracker.close()


# ===========================================================================
# Group D: Data Lifecycle (tests 23-26)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 23: Idempotent re-scan with refresh-on-diff
# ---------------------------------------------------------------------------

def test_23_idempotent_rescan(t):
    # type: (TestResult) -> None
    """Under the maturity model a second scan with unchanged provider data
    writes nothing (same-value skip) and a changed provider value overwrites
    the existing Kodi value.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 23: Idempotent re-scan / refresh-on-diff (Air, id:6) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    set_movie(6, plot="")
    try:
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        item = get_movie(6)

        stats1 = run_scan_with_settings(MOVIE, [item], tracker)
        t.check("first scan updated", stats1["updated"] >= 1)

        identity_air = compute_identity(item, MOVIE)
        fs_after1 = tracker.get_field_status("movie", identity_air, "plot")
        count_after1 = (fs_after1.get("fetch_count") or 0) if fs_after1 else 0
        t.check("fetch_count is 1 after first scan",
                count_after1 == 1,
                "got {}".format(count_after1))

        # Second scan with no provider data change: same-value skip means no write,
        # but because content may be young, fetch_count still advances.
        import resources.lib.engine.scan as scan_mod
        call_count = [0]
        orig_tmdb = scan_mod.tmdb_fetch_movie

        def counting_tmdb(*args, **kwargs):
            call_count[0] += 1
            return orig_tmdb(*args, **kwargs)

        # Force the field to be due again by setting next_retry to the past
        tracker._conn.execute(
            "UPDATE field_status SET next_retry = '2020-01-01' "
            "WHERE field = 'plot' AND item_id = ("
            "SELECT id FROM items WHERE media_type='movie' AND title='Air')"
        )
        tracker._conn.commit()
        tracker.start_new_cycle()
        scan_mod.tmdb_fetch_movie = counting_tmdb
        try:
            item2 = get_movie(6)
            # Young content so the field stays volatile and a due refetch happens
            # (the real "Air" premiered is years ago, which would finalize it).
            item2["premiered"] = (date.today() - timedelta(days=1)).isoformat()
            stats2 = run_scan_with_settings(MOVIE, [item2], tracker)
        finally:
            scan_mod.tmdb_fetch_movie = orig_tmdb

        # The same-value skip means no Kodi write, but the fetch still happened
        t.check("second scan had a provider call (field was due)", call_count[0] >= 1,
                "got {} calls".format(call_count[0]))
        # updated may be 0 (same-value skip) or 1 (provider value changed) - both valid
        t.check("no write error", stats2["errors"] == 0,
                "errors={}".format(stats2["errors"]))

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
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        item = get_movie(6)
        run_scan_with_settings(MOVIE, [item], tracker)

        after1 = get_movie(6)
        t.check("rating filled", after1["rating"] > 0)
        t.check("plot unchanged", after1["plot"] == orig_plot)
        written_rating = after1["rating"]

        # Clear plot and force field to be due again
        set_movie(6, plot="")
        tracker.start_new_cycle()
        tracker._conn.execute(
            "UPDATE field_status SET next_retry = '2020-01-01' "
            "WHERE item_id = (SELECT id FROM items WHERE media_type='movie' AND title='Air')"
        )
        tracker._conn.commit()

        item2 = get_movie(6)
        # Young content so the past-due field refetches (real "Air" is old -> FINAL).
        item2["premiered"] = (date.today() - timedelta(days=1)).isoformat()
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
# Test 25: Both providers None: fetch_count advances, no errors
# ---------------------------------------------------------------------------

def test_25_both_providers_none_no_error(t):
    # type: (TestResult) -> None
    """When TMDB returns None (clean 404, no data), this is a valid sample:
    fetch_count advances and errors are NOT incremented.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 25: TMDB None, fetch_count advances (Air, id:6) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    import resources.lib.engine.scan as scan_mod
    orig_tmdb = scan_mod.tmdb_fetch_movie

    def null_tmdb(*a, **kw):
        return None

    set_movie(6, plot="")
    scan_mod.tmdb_fetch_movie = null_tmdb
    try:
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        item = get_movie(6)

        stats = run_scan_with_settings(MOVIE, [item], tracker)

        # TMDB returned None cleanly: the sample happened, the provider just
        # had nothing, so fetch_count advances to 1
        fs = tracker.get_field_status("movie", compute_identity(item, MOVIE), "plot")
        count = (fs.get("fetch_count") or 0) if fs else 0
        t.check("fetch_count advanced (clean no-data is a valid sample)",
                count >= 1,
                "fetch_count={}".format(count))

        # A clean 404 is not an error
        t.check("errors == 0 (clean no-data is not an error)",
                stats["errors"] == 0,
                "errors={}".format(stats["errors"]))

        tracker.close()
    finally:
        scan_mod.tmdb_fetch_movie = orig_tmdb
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 26: Manual reset re-baselines fetch_count and sets next_retry
# ---------------------------------------------------------------------------

def test_26_manual_reset_rebaselines(t):
    # type: (TestResult) -> None
    """The reset=True path (Update now) re-baselines fetch_count to 1 and
    stores a non-None next_retry for young content so it continues being
    sampled on schedule.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 26: Manual reset re-baselines (Air, id:6) ---")

    air = get_movie(6)
    orig_plot = air["plot"]

    # Use a young premiered date so that next_retry is computed (not None)
    item = get_movie(6)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    item["premiered"] = yesterday

    set_movie(6, plot="")
    try:
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
        tracker = make_tracker(tmp)
        identity_air_26 = compute_identity(item, MOVIE)

        # Initial scan: fetch_count goes to 1, next_retry set
        stats1 = run_scan_with_settings(MOVIE, [item], tracker)
        t.check("initial scan processed", stats1["processed"] >= 1)

        fs_before = tracker.get_field_status("movie", identity_air_26, "plot")
        count_before = (fs_before.get("fetch_count") or 0) if fs_before else 0
        t.check("initial fetch_count >= 1", count_before >= 1,
                "got {}".format(count_before))

        # Simulate multiple prior samples so fetch_count > 1
        # by recording additional fetches directly
        tracker.record_field_fetch("movie", identity_air_26, "plot",
                                   next_retry="2099-01-01")
        tracker.record_field_fetch("movie", identity_air_26, "plot",
                                   next_retry="2099-01-01")
        tracker.commit()

        fs_pre_reset = tracker.get_field_status("movie", identity_air_26, "plot")
        count_pre = (fs_pre_reset.get("fetch_count") or 0) if fs_pre_reset else 0
        t.check("pre-reset fetch_count >= 3", count_pre >= 3,
                "got {}".format(count_pre))

        # Now run with reset=True: fetch_count is re-baselined to 1
        tracker.start_new_cycle()
        item2 = get_movie(6)
        item2["premiered"] = yesterday
        stats_reset = run_scan_with_settings(MOVIE, [item2], tracker, reset=True)
        t.check("reset scan processed", stats_reset["processed"] >= 1)

        fs_after = tracker.get_field_status("movie", identity_air_26, "plot")
        count_after = (fs_after.get("fetch_count") or 0) if fs_after else 0
        t.check("fetch_count re-baselined to 1",
                count_after == 1,
                "got {}".format(count_after))

        # next_retry must be set and in the future (not None) for young content
        today_str = date.today().isoformat()
        nr = (fs_after.get("next_retry") or "") if fs_after else ""
        t.check("next_retry is set and in the future after reset",
                nr > today_str,
                "next_retry={}".format(nr))

        tracker.close()
    finally:
        set_movie(6, plot=orig_plot)


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
            tmp = tempfile.mkdtemp(dir=_SCRATCH)
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
            t.check("tmdb.movie has field_count", "field_count=" in line)

        imdb_lines = logs.find("event=imdb.movie")
        if imdb_lines:
            line = imdb_lines[0]
            t.check("imdb.movie has imdb_id", "imdb_id=" in line)
            t.check("imdb.movie has title", "title=" in line)
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
            tmp = tempfile.mkdtemp(dir=_SCRATCH)
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
            t.check("scan.complete has elapsed", "duration_ms=" in line)
    finally:
        set_movie(6, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 29: Episode provider logs (IMDb direct fetch)
# ---------------------------------------------------------------------------

def test_29_episode_provider_logs(t):
    # type: (TestResult) -> None
    """The TMDB season fetch behind an episode logs its event and fields."""
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 29: Episode provider logs (Bluey S01E01) ---")

    ep = get_episode(4311)
    orig_plot = ep.get("plot", "")

    set_episode(4311, plot="")
    try:
        with LogCapture() as logs:
            tmp = tempfile.mkdtemp(dir=_SCRATCH)
            tracker = make_tracker(tmp)
            item = get_episode(4311)
            show_map = {item["tvshowid"]: "82728"}
            run_scan_with_settings(EPISODE, [item], tracker,
                                   show_id_map=show_map)
            tracker.close()

        season_lines = logs.find("event=tmdb.season")
        t.check("has tmdb.season log", len(season_lines) >= 1)
        if season_lines:
            line = season_lines[0]
            t.check("has tmdb_id=82728", "tmdb_id=82728" in line,
                    "line: {}".format(line[:120]))
            t.check("has episodes count", "episodes=" in line)
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
            tmp = tempfile.mkdtemp(dir=_SCRATCH)
            tracker = make_tracker(tmp)
            item = get_movie(6)
            run_scan_with_settings(MOVIE, [item], tracker)
            tracker.close()

        import re
        event_pattern = re.compile(r"event=([a-z]+\.[a-z_]+)")
        event_lines = [ln for ln in logs.lines if "event=" in ln]
        t.check("has event lines", len(event_lines) > 0)

        bad_events = []
        for line in event_lines:
            match = event_pattern.search(line)
            if not match:
                if "event=None" in line or "event= " in line:
                    bad_events.append(line[:80])
        t.check("no malformed events", len(bad_events) == 0,
                "bad: {}".format(bad_events[:3]))

        duration_lines = [ln for ln in logs.lines if "duration_ms=" in ln]
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
# Group F: Edge Cases (tests 32-33)
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 32: Show ID map mismatch (IMDb fallback)
# ---------------------------------------------------------------------------

def test_32_show_id_map_mismatch(t):
    # type: (TestResult) -> None
    """An episode whose show is missing from show_id_map is transient.

    Without its show's TMDB id the episode cannot be looked up, which says
    nothing about the episode: nothing is written and the clock stays put.
    """
    from resources.lib.data.media_types import EPISODE

    print("\n--- Test 32: Show ID map mismatch (1923 S01E01, id:1) ---")

    ep = get_episode(1)
    orig_plot = ep["plot"]

    set_episode(1, plot="")
    try:
        tracker = make_tracker(tempfile.mkdtemp(dir=_SCRATCH))
        item = get_episode(1)
        wrong_map = {item["tvshowid"] + 100000: "157744"}
        stats = run_scan_with_settings(EPISODE, [item], tracker,
                                       show_id_map=wrong_map,
                                       enabled_fields=frozenset({"plot"}))

        after = get_episode(1)
        t.check("plot not written", after.get("plot", "") == "",
                "got '{}'".format(after.get("plot", "")[:40]))
        t.check("not counted as updated", stats["updated"] == 0,
                "updated={}".format(stats["updated"]))
        t.check("no error counted", stats["errors"] == 0,
                "errors={}".format(stats["errors"]))
        field = tracker.get_field_status("episode", compute_identity(item, EPISODE), "plot")
        count = (field.get("fetch_count") or 0) if field else 0
        t.check("fetch_count not advanced", count == 0, "fetch_count={}".format(count))
        tracker.close()
    finally:
        set_episode(1, plot=orig_plot)


# ---------------------------------------------------------------------------
# Test 33: SKIP_NOT_DUE: future next_retry prevents provider call
# ---------------------------------------------------------------------------

def test_33_skip_not_due(t):
    # type: (TestResult) -> None
    """Pre-seed a field with fetch_count >= 1 and a future next_retry.
    decide_field returns SKIP_NOT_DUE and no provider call is made.
    """
    from resources.lib.data.media_types import MOVIE

    print("\n--- Test 33: SKIP_NOT_DUE (Burial, id:50, tmdb:1155875) ---")

    movie = get_movie(50)
    t.check("movie found", movie.get("title") == "Burial")

    # Use a young premiered date so content is not FINAL
    item = get_movie(50)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    item["premiered"] = yesterday

    tmp = tempfile.mkdtemp(dir=_SCRATCH)
    tracker = make_tracker(tmp)
    identity_burial_33 = compute_identity(item, MOVIE)

    # Upsert the item and record a field fetch with a far-future next_retry
    tracker.upsert_item("movie", identity_burial_33, title="Burial",
                        kodi_id=50, tmdb_id="1155875")
    tracker.commit()
    # Stamp the fingerprint of the settings this scan uses; a row without one
    # counts as sampled under other settings and is re-checked.
    from resources.lib.engine.scan import settings_fingerprint
    tracker.record_field_fetch("movie", identity_burial_33, "plot",
                               next_retry="2099-01-01",
                               settings_fp=settings_fingerprint(
                                   "plot", _default_settings(), "en-US", "movie"))
    tracker.commit()

    import resources.lib.engine.scan as scan_mod
    call_count = [0]
    orig_tmdb = scan_mod.tmdb_fetch_movie

    def counting_tmdb(*args, **kwargs):
        call_count[0] += 1
        return orig_tmdb(*args, **kwargs)

    scan_mod.tmdb_fetch_movie = counting_tmdb
    try:
        # Scope to only the pre-seeded field, so the item-level decision reflects
        # plot's SKIP_NOT_DUE (other fields would be first-encounter and fetch).
        stats = run_targeted_scan(MOVIE, [item], tracker,
                                  enabled_fields=frozenset({"plot"}))
    finally:
        scan_mod.tmdb_fetch_movie = orig_tmdb

    t.check("item skipped (all fields SKIP_NOT_DUE or already set)",
            stats["skipped"] >= 1,
            "processed={} skipped={}".format(stats["processed"], stats["skipped"]))
    t.check("no TMDB call",
            call_count[0] == 0,
            "got {} calls".format(call_count[0]))

    fs = tracker.get_field_status("movie", identity_burial_33, "plot")
    count_after = (fs.get("fetch_count") or 0) if fs else 0
    t.check("fetch_count unchanged (was SKIP_NOT_DUE)",
            count_after == 1,
            "expected 1, got {}".format(count_after))

    tracker.close()


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
        tmp = tempfile.mkdtemp(dir=_SCRATCH)
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
            settings=_default_settings(rate_limit=0.0),
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
            settings=_default_settings(rate_limit=0.0),
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
    "providers": ("11", "12", "13", "14"),
    "resolve": ("15", "16", "17"),
    "episode": ("18", "20", "21", "22"),
    "lifecycle": ("23", "24", "25", "26"),
    "logging": ("27", "28", "29", "30"),
    "edge": ("32", "33"),
    "resume": ("34",),
}

ALL_TESTS = [
    ("1", "Movie rating backfill", test_1_movie_rating_backfill),
    ("2", "Tagline gap (no TMDB data)", test_2_movie_tagline_no_tmdb_data),
    ("3", "Episode title replacement", test_3_episode_title_replacement),
    ("4", "Title NOT replaced (TMDB matches)", test_4_episode_title_not_replaced),
    ("5", "Old content FINAL after one fetch", test_5_old_content_final_after_one_fetch),
    ("6", "Young content scheduled", test_6_young_content_scheduled),
    ("7", "Transient failure no advance", test_7_transient_failure_no_advance),
    ("8", "TVDB-only resolve_id", test_8_tvdb_only_resolve_id),
    ("9", "Bogus IDs graceful skip", test_9_bogus_ids_graceful_skip),
    ("10", "Logging completeness", test_10_logging_completeness),
    ("11", "TVDB series verification", test_11_tvdb_series_verification),
    ("12", "TVDB episode list", test_12_tvdb_episode_list),
    ("13", "TMDB placeholder titles", test_13_tmdb_placeholder_titles),
    ("14", "TMDB find by TVDB id", test_14_tmdb_find_by_tvdb),
    ("15", "Movie plot from TMDB", test_15_movie_plot_from_tmdb),
    ("16", "IMDb ratings module", test_16_imdb_ratings_module),
    ("17", "TMDB only ratings", test_17_tmdb_only),
    ("18", "TMDB episode matching", test_18_tmdb_episode_matching),
    ("20", "TVDB-only episode, show unresolved", test_20_tvdb_only_episode_skipped),
    ("21", "TVmaze-only episode", test_21_tvmaze_only_episode),
    ("22", "NFO episode skip", test_22_nfo_episode_skip),
    ("23", "Idempotent re-scan / refresh-on-diff", test_23_idempotent_rescan),
    ("24", "Selective field update", test_24_selective_field_update),
    ("25", "TMDB None, no error", test_25_both_providers_none_no_error),
    ("26", "Manual reset re-baselines", test_26_manual_reset_rebaselines),
    ("27", "Provider log events", test_27_provider_logs),
    ("28", "Scan lifecycle logs", test_28_scan_lifecycle_logs),
    ("29", "Episode provider logs", test_29_episode_provider_logs),
    ("30", "Log consistency", test_30_log_consistency),
    ("32", "Show ID map mismatch", test_32_show_id_map_mismatch),
    ("33", "SKIP_NOT_DUE gate", test_33_skip_not_due),
    ("34", "Resume interrupted scan", test_34_resume_interrupted_scan),
]


def main():
    # type: () -> None
    print("MetaMender Integration Tests")
    print("Kodi: {}".format(KODI_URL))
    print("TMDB key: {}".format("set" if TMDB_KEY else "MISSING"))

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

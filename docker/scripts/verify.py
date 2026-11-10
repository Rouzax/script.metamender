#!/usr/bin/env python3
"""Host-side harness for surgical MetaMender checks against live Kodi.

Runs the REAL engine modules on the host, proxying xbmc.executeJSONRPC to the
headless Kodi container over JSON-RPC. This is the proven way to exercise the
scan engine without installing the addon in the container (and it sidesteps the
headless window-property limitation that affects the in-container service).

Commands:
    verify.py probe     Read-only. Library composition + identity-fallback
                        analysis (which items land on the bare-title key) +
                        per-field gap counts. Mutates nothing.
    verify.py apikey    Read-only on Kodi. Regression check: an invalid TMDB key
                        makes run_scan raise ApiKeyError instead of finishing
                        as if nothing were wrong. Mutates nothing in Kodi (the
                        bogus key aborts before any write; the tracker is a
                        throwaway temp DB).
    verify.py certs     No Kodi needed. Asks each certification site for a few
                        known titles and compares the answers. Read-only.

Env:
    KODI_URL        default http://localhost:8101/jsonrpc
    TMDB_API_KEY    optional; only needed if you extend this with real-write
                    tests. The bundled commands do not require it.

Any command that writes to the live library must be bracketed with
backup-db.sh (before) and restore-db.sh (after). The bundled commands do not
write to Kodi.
"""
from __future__ import annotations

import json as _json
import os
import sys
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock

import requests

KODI_URL = os.environ.get("KODI_URL", "http://localhost:8101/jsonrpc")
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

_TMP = tempfile.mkdtemp(prefix="mm_verify_")


def _install_kodi_stubs() -> None:
    """Inject Kodi module stubs; proxy executeJSONRPC to the real instance."""
    def _proxy(query_str: str) -> str:
        resp = requests.post(KODI_URL, json=_json.loads(query_str), timeout=20)
        return _json.dumps(resp.json())

    xbmc = MagicMock()
    xbmc.executeJSONRPC = _proxy
    xbmc.LOGDEBUG, xbmc.LOGINFO, xbmc.LOGWARNING, xbmc.LOGERROR, xbmc.LOGFATAL = 0, 1, 3, 4, 6
    xbmc.log = MagicMock()
    xbmc.getCondVisibility = MagicMock(return_value=False)

    addon = MagicMock()
    addon.getSetting = MagicMock(return_value="")  # debug off -> no file logging
    addon.getAddonInfo = MagicMock(
        side_effect=lambda k: {"id": "script.metamender", "path": PROJECT_ROOT}.get(k, "script.metamender")
    )
    addon.getLocalizedString = MagicMock(return_value="")
    xbmcaddon = MagicMock()
    xbmcaddon.Addon = MagicMock(return_value=addon)

    xbmcvfs = MagicMock()
    xbmcvfs.translatePath = MagicMock(
        side_effect=lambda p: os.path.join(_TMP, p.replace("special://profile/addon_data/", ""))
    )
    xbmcvfs.exists = MagicMock(side_effect=os.path.exists)
    xbmcvfs.mkdirs = MagicMock(side_effect=lambda p: os.makedirs(p, exist_ok=True))

    sys.modules["xbmc"] = xbmc
    sys.modules["xbmcaddon"] = xbmcaddon
    sys.modules["xbmcgui"] = MagicMock()
    sys.modules["xbmcvfs"] = xbmcvfs


def _rpc(method: str, props: List[str]) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "method": method,
               "params": {"properties": props}, "id": 1}
    resp = requests.post(KODI_URL, json=payload, timeout=30)
    return resp.json().get("result", {})


def _has_ext_id(item: Dict[str, Any]) -> bool:
    u = item.get("uniqueid") or {}
    return bool(u.get("tmdb") or u.get("themoviedb") or u.get("imdb"))


def cmd_probe() -> int:
    """Read-only library + identity-fallback + gap analysis.

    Uses get_all_items() so items carry exactly the properties the addon
    requests at scan time. That is the point: _build_properties does NOT
    request `year`, so compute_identity's "title (year)" fallback collapses to
    bare title for no-ID items. We separately query `year` to show Kodi DOES
    have it (the fix is to request it), without letting that mask the bug.
    """
    from collections import Counter
    from resources.lib.data.kodi import get_all_items
    from resources.lib.data.media_types import MOVIE, TVSHOW
    from resources.lib.data.identity import compute_identity

    for mt, method in ((MOVIE, "VideoLibrary.GetMovies"),
                       (TVSHOW, "VideoLibrary.GetTVShows")):
        # Real scan-time view (no `year` requested).
        items = get_all_items(mt)
        total = len(items)
        no_id = [i for i in items if not _has_ext_id(i)]
        print("=== {} ({} total) ===".format(mt.label, total))
        print("  fall onto title-fallback identity (no tmdb/imdb): {}".format(len(no_id)))

        ids = Counter(compute_identity(i, mt) for i in no_id)
        collisions = {k: c for k, c in ids.items() if c > 1}
        bare_title = sum(1 for i in no_id if compute_identity(i, mt) == (i.get("title", "")))
        print("  identity == bare title at real scan time (year missing): {}".format(bare_title))
        print("  identity collisions within that set: {}".format(collisions or "none"))

        # Show Kodi actually has the year (so the fix is to request it).
        year_avail = _rpc(method, ["title", "year"]).get(mt.kodi_result_key, [])
        by_title = {y.get("label"): y.get("year") for y in year_avail}
        for i in no_id[:6]:
            t = i.get("title", "")
            print("    {!r}  ->  identity {!r}   (Kodi year={})".format(
                t, compute_identity(i, mt), by_title.get(t)))
        print()
    return 0


def cmd_apikey() -> int:
    """An invalid TMDB key must surface as ApiKeyError (read-only on Kodi)."""
    from resources.lib.data.kodi import get_all_items
    from resources.lib.data.media_types import TVSHOW
    from resources.lib.data.tracker import TrackerDB
    from resources.lib.engine.scan import run_scan
    from resources.lib.providers.tmdb import fetch_tvshow
    from resources.lib.utils import ApiKeyError

    bogus = "0000000000000000000000000000bad0"
    shows = get_all_items(TVSHOW)
    target = next((s for s in shows if (s.get("uniqueid") or {}).get("tmdb")), None)
    if target is None:
        print("No tvshow with a tmdb id found in the library; cannot run.")
        return 1
    print("target:", target.get("title"), target.get("uniqueid"))

    try:
        fetch_tvshow(target["uniqueid"]["tmdb"], bogus, "en-US")
        print("provider raised ApiKeyError on bad key?  NO (unexpected)")
    except ApiKeyError:
        print("provider raised ApiKeyError on bad key?  YES")

    tracker = TrackerDB(os.path.join(_TMP, "tracker.db"))
    settings = {"rate_limit": 0, "rating_source": 0,
                "text_source_order": 0, "stale_threshold_days": 365}
    raised = None
    stats = None
    try:
        stats = run_scan(
            media_type=TVSHOW, tracker=tracker,
            source_config={"sources": [], "default_language": "en-US"},
            enabled_fields=TVSHOW.backfill_fields, tmdb_api_key=bogus,
            settings=settings, monitor=None, items=[target],
        )
    except Exception as exc:  # noqa: BLE001
        raised = exc
    tracker.close()

    # Post-fix (Phase 2): run_scan must RE-RAISE ApiKeyError so the daemon can
    # surface it and hold the watermark. This is now a regression check.
    if isinstance(raised, ApiKeyError):
        print("run_scan raised ApiKeyError?  YES (fixed: error surfaces to caller)")
        return 0
    if raised is not None:
        print("run_scan raised?  YES but wrong type ->", type(raised).__name__)
        return 1
    print("run_scan raised?  NO  (REGRESSION: invalid key silently swallowed) -> stats:", stats)
    return 1


# (site module, title, original title, year, media type, expected rating)
_CERT_CHECKS = [
    ("fsk", "The Matrix", "", 1999, "movie", "16"),
    ("fsk", "Dark", "", 2017, "tvshow", "12"),
    ("bbfc", "Interstellar", "", 2014, "movie", "12"),
    ("bbfc", "Sherlock", "", 2010, "tvshow", "15"),
    ("medieraadet", "Interstellar", "", 2014, "movie", "11"),
    ("medieraadet", "The Hunt", "", 2020, "movie", "15"),
]


def cmd_certs() -> int:
    """Live answers of the certification sites for known titles."""
    import importlib

    failed = 0
    for site, title, original, year, media, expected in _CERT_CHECKS:
        module = importlib.import_module("resources.lib.providers." + site)
        try:
            got = module.lookup(title, original, year, media)
        except Exception as exc:  # noqa: BLE001 - report every failure kind
            got = "error: {}".format(exc)
        ok = got == expected
        failed += not ok
        print("{:<12} {:<28} {:>6}  expected {:<5} got {!r}  {}".format(
            site, title, media, expected, got, "OK" if ok else "FAIL"))
    return 1 if failed else 0


def main() -> int:
    _install_kodi_stubs()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "probe"
    if cmd == "probe":
        return cmd_probe()
    if cmd == "apikey":
        return cmd_apikey()
    if cmd == "certs":
        return cmd_certs()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

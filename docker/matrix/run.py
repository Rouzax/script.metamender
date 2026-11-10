#!/usr/bin/env python3
"""Scraper and settings matrix: build a small library with one of Kodi's
scrapers, let metamender scan it twice, and check what it wrote.

A run is named ``K<n>-M<n>[-C<n>]``: how Kodi builds the library (KODI), the
metamender settings (MM), and optionally a setting changed after the second
scan (CHANGES). Every run starts from an empty library in the kodi-matrix
container (compose.yaml here), which never touches the shared MariaDB
libraries.

    docker/matrix/run.py --out DIR K1-M1 K2-M3      # chosen runs
    docker/matrix/run.py --out DIR --plan           # every planned run
    docker/matrix/run.py --out DIR --summary        # summary of finished runs

Each run writes snapshots, the tracker databases, the logs and a report to
``DIR/<run>/``, and ``DIR/summary.md`` covers every run in DIR. The TMDB key
comes from docker/.env and is written only into the container.

Stop the rig afterwards with ``docker compose -f docker/matrix/compose.yaml
down -v``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
COMPOSE = ["docker", "compose", "-f", str(HERE / "compose.yaml")]
CONTAINER = "kodi-matrix"
URL = "http://127.0.0.1:8102/jsonrpc"
HOST_MEDIA = "/mnt/media/Data"
SUBSET_DIR = "/config/subset"
KODI_HOME = "/config/.kodi"
ADDON_ID = "script.metamender"
ADDON_DATA = f"{KODI_HOME}/userdata/addon_data/{ADDON_ID}"
VIDEO_DB = f"{KODI_HOME}/userdata/Database/MyVideos131.db"
SCAN_TIMEOUT_SEC = 3600

# Kodi rejects nothing it cannot parse in these; the headless one-off below is
# documented in docker/README.md.
KNOWN_HARMLESS = ("Window id does not exist",)

# root -> (share folder, folder language, Kodi content, folders)
SUBSET: dict[str, tuple[str, str, str, list[str]]] = {
    "tv-nl": ("Kids/TV", "nl-NL", "tvshows", [
        "De Snorkels (1984)", "Alfred Jodocus Kwak (1989)", "Planet Earth II",
        "Ontdek de Ruimte met André Kuipers (2015)", "De Avonturen van Kuifje",
    ]),
    "tv-nl2": ("TVSeries/NL", "nl-NL", "tvshows", [
        "De 12 van Schouwendam (2019)", "Stanley (2019)", "Het gouden uur (2022)",
        "The Jewish Council (2024)",
    ]),
    "tv-en": ("TVSeries/EN", "en-US", "tvshows", [
        "A Very English Scandal (2018)", "Behind Her Eyes (2021)",
        "Adolescence (2025)", "A Very British Scandal (2021)", "Bass X Machina (2026)",
    ]),
    "movies-en": ("Movies", "en-US", "movies", [
        "28 Days Later (2002)", "X (2022)", "You Hurt My Feelings (2023)",
        "Young Washington (2026)", "Your Mother Your Mother Your Mother (2026)",
    ]),
    "movies-nl": ("Kids/Movies", "nl-NL", "movies", [
        "Ernst, Bobbie en de Rest De Grote Verhuisshow (2006)",
        "Ernst, Bobbie en de Rest Een bruid voor de boswachter (2008)",
        "Alfie, the Little Werewolf (2011)", "A Monster in Paris (2011)", "Aladdin (1992)",
    ]),
}

TV_SCRAPERS = {
    "tmdb": "metadata.tvshows.themoviedb.org.python",
    "tvdb": "metadata.tvshows.thetvdb.com.v4.python",
    "tvmaze": "metadata.tvmaze",
}
MOVIE_SCRAPER = "metadata.themoviedb.org.python"
REPO_ADDONS = [
    "metadata.tvshows.thetvdb.com.v4.python", "metadata.tvmaze",
    "script.module.requests", "script.module.imdbratings",
]

# The image bundles older TMDB scrapers that read a different language
# setting; Kodi replaces them from its repository at some point after start,
# so a run waits for these versions before it scrapes.
MIN_VERSIONS = {
    "metadata.tvshows.themoviedb.org.python": (1, 7, 13),
    MOVIE_SCRAPER: (3, 2, 2),
}

# Kodi side: TV scraper, and the scraper language ("folder" follows the
# root's language, "en" scrapes every root in English).
KODI: dict[str, tuple[str, str]] = {
    "K1": ("tmdb", "folder"),
    "K2": ("tvdb", "folder"),
    "K3": ("tmdb", "en"),
    "K4": ("tvdb", "en"),
    "K5": ("tvmaze", "en"),
}

_TEXT_ONLY = {
    "enable_ratings": "false", "enable_credits": "false",
    "enable_trailers": "false", "enable_taglines": "false", "enable_details": "false",
}
# metamender side: settings on top of BASE_SETTINGS; "sources" False drops
# sources.json.
MM: dict[str, dict[str, Any]] = {
    "M1": {},
    "M2": {"fallback_language": "nl-NL"},
    "M3": {"text_source_order": "1"},
    "M4": {"rating_source": "1"},
    "M4b": {"rating_source": "3"},
    "M5": {"sources": False},
    "M6": dict(_TEXT_ONLY),
}

# Setting changed after the second scan, before a third.
CHANGES: dict[str, dict[str, Any]] = {
    "C1": {"fallback_language": "de-DE"},
    "C2": {"fallback_language": "en-US"},
    "C3": {"fallback_order": "1"},
}

PLAN = (
    [f"{k}-M1" for k in KODI]
    + [f"{k}-{m}" for m in ("M2", "M3", "M4", "M5", "M6") for k in ("K1", "K2")]
    + ["K1-M4b"]
    # M2 fills the English placeholder titles from its Dutch fallback, so
    # these start with values a setting change may reopen.
    + [f"K1-M2-{c}" for c in CHANGES]
)

BASE_SETTINGS = {
    "enable_ratings": "true", "enable_plots": "true", "enable_episode_titles": "true",
    "enable_certifications": "false", "enable_credits": "true",
    "enable_trailers": "true", "enable_taglines": "true", "enable_details": "true",
    "enable_fsk": "false", "enable_bbfc": "false", "enable_medieraadet": "false",
    "enable_kijkwijzer": "false", "scan_interval": "24", "rate_limit": "0.25",
    "show_notifications": "false", "logging": "true",
}

FIELDS = {
    "movie": ["title", "plot", "rating", "votes", "ratings", "tagline", "director",
              "writer", "genre", "studio", "country", "trailer", "runtime", "mpaa",
              "premiered"],
    "tvshow": ["title", "plot", "rating", "votes", "ratings", "genre", "mpaa", "premiered"],
    "episode": ["title", "plot", "rating", "votes", "ratings", "director", "writer",
                "firstaired", "runtime"],
}
LIST_CALLS = {
    "movie": ("VideoLibrary.GetMovies", "movies", "movieid"),
    "tvshow": ("VideoLibrary.GetTVShows", "tvshows", "tvshowid"),
    "episode": ("VideoLibrary.GetEpisodes", "episodes", "episodeid"),
}
TEXT_FIELDS = ("title", "plot", "tagline")


def _host_path(kodi_path: str) -> str:
    """The share path on this host behind a subset path inside the container."""
    prefix = SUBSET_DIR + "/"
    if not kodi_path.startswith(prefix):
        return kodi_path
    root, _, rest = kodi_path[len(prefix):].partition("/")
    return f"{HOST_MEDIA}/{SUBSET[root][0]}/{rest}"


def _engine_rules() -> tuple[Any, Any, Any]:
    """metamender's own empty/placeholder and NFO rules, run on the host.

    nfo.py reads files through xbmcvfs and logs through utils, so those Kodi
    modules get host stand-ins backed by the local filesystem.
    """
    for name in ("xbmc", "xbmcaddon", "xbmcgui"):
        sys.modules.setdefault(name, MagicMock())
    vfs = MagicMock()
    vfs.exists = os.path.exists

    def listdir(path: str) -> tuple[list[str], list[str]]:
        names = sorted(os.listdir(path))
        return ([n for n in names if os.path.isdir(os.path.join(path, n))],
                [n for n in names if not os.path.isdir(os.path.join(path, n))])

    vfs.listdir = listdir
    sys.modules.setdefault("xbmcvfs", vfs)
    sys.path.insert(0, str(REPO))
    from resources.lib.data.nfo import NFO, nfo_state
    from resources.lib.engine.protect import _is_empty
    from resources.lib.engine.textrules import is_placeholder_title

    def is_nfo(media_type: str, kodi_path: str) -> bool:
        return nfo_state(media_type, _host_path(kodi_path)) == NFO

    def blank(field: str, value: Any) -> bool:
        if field == "title" and is_placeholder_title(value):
            return True
        if field == "votes" and str(value).strip() in ("", "0"):
            # Kodi reports votes as a string, "0" when there are none.
            return True
        return bool(_is_empty(value))

    return is_nfo, blank, is_placeholder_title


# --- Kodi and the container -------------------------------------------------

def rpc(method: str, params: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    body = {"jsonrpc": "2.0", "method": method, "id": 1, "params": params or {}}
    req = urllib.request.Request(URL, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']}")
    return data["result"]


def sh(*cmd: str, stdin: str | None = None, check: bool = True) -> str:
    out = subprocess.run(cmd, input=stdin, capture_output=True, text=True, check=False)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}...: {out.stderr.strip()}")
    return out.stdout


def dexec(script: str, stdin: str | None = None, check: bool = True) -> str:
    return sh("docker", "exec", "-i", CONTAINER, "sh", "-c", script, stdin=stdin, check=check)


def put_file(path: str, content: str) -> None:
    dexec(f"mkdir -p \"$(dirname '{path}')\" && cat > '{path}'", stdin=content)


def wait_ready(timeout: int = 180, library: bool = True) -> None:
    """Wait for JSON-RPC, and unless ``library`` is False for the video library
    too (it fails until advancedsettings.xml drops the image's MySQL block)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            rpc("JSONRPC.Ping", timeout=5)
            if library:
                rpc("VideoLibrary.GetMovies", {"limits": {"end": 1}}, timeout=5)
            return
        except (OSError, RuntimeError, urllib.error.URLError):
            time.sleep(2)
    raise RuntimeError("Kodi did not become ready")


def restart_kodi() -> None:
    sh("docker", "restart", CONTAINER)
    time.sleep(3)
    wait_ready()


def wait_until(check: Any, timeout: int, what: str, step: float = 3) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return
        time.sleep(step)
    raise RuntimeError(f"timed out waiting for {what}")


def addon_ready(addon_id: str) -> bool:
    try:
        details = rpc("Addons.GetAddonDetails",
                      {"addonid": addon_id, "properties": ["enabled"]})["addon"]
    except RuntimeError:
        return False
    if not details.get("enabled"):
        rpc("Addons.SetAddonEnabled", {"addonid": addon_id, "enabled": True})
        return False
    return True


def _version(addon_id: str) -> tuple[int, ...]:
    try:
        text = rpc("Addons.GetAddonDetails",
                   {"addonid": addon_id, "properties": ["version"]})["addon"]["version"]
    except RuntimeError:
        return ()
    return tuple(int(n) for n in re.findall(r"\d+", text)[:3])


def update_scrapers() -> None:
    """Ask Kodi to update from its repository until the scrapers are current."""
    deadline = time.time() + 600
    while time.time() < deadline:
        if all(_version(a) >= v for a, v in MIN_VERSIONS.items()):
            return
        sh("docker", "exec", CONTAINER, "kodi-send", "--action=UpdateAddonRepos")
        time.sleep(30)
    raise RuntimeError(f"scrapers not updated: { {a: _version(a) for a in MIN_VERSIONS} }")


def fresh_kodi() -> None:
    sh(*COMPOSE, "down", "-v")
    sh(*COMPOSE, "up", "-d")
    wait_ready(library=False)
    sh("docker", "cp", str(HERE / "advancedsettings.xml"),
       f"{CONTAINER}:{KODI_HOME}/userdata/advancedsettings.xml")
    restart_kodi()
    sh("docker", "exec", CONTAINER, "install_addon", *REPO_ADDONS)
    for addon_id in REPO_ADDONS:
        wait_until(lambda a=addon_id: addon_ready(a), 300, f"add-on {addon_id}")
    update_scrapers()


# --- Library build ----------------------------------------------------------

def _scraper_settings(scraper: str, language: str) -> str:
    if scraper == TV_SCRAPERS["tmdb"]:
        pairs = {"languageDetails": language, "languageImages": language}
    elif scraper == MOVIE_SCRAPER:
        pairs = {"language": language, "searchlanguage": language}
    elif scraper == TV_SCRAPERS["tvdb"]:
        pairs = {"language": {"nl-NL": "Dutch", "en-US": "English"}[language]}
    else:
        return ""
    body = "".join(f'<setting id="{k}">{v}</setting>' for k, v in pairs.items())
    return f'<settings version="2">{body}</settings>'


def build_library(kodi_id: str) -> None:
    tv_scraper, lang_mode = KODI[kodi_id]
    links = []
    rows = []
    for root, (share, language, content, folders) in SUBSET.items():
        for name in folders:
            if not os.path.isdir(f"{HOST_MEDIA}/{share}/{name}"):
                raise RuntimeError(f"subset folder missing: {share}/{name}")
            links.append([f"/media/{share}/{name}", f"{SUBSET_DIR}/{root}/{name}"])
        scraper = TV_SCRAPERS[tv_scraper] if content == "tvshows" else MOVIE_SCRAPER
        scrape_lang = language if lang_mode == "folder" else "en-US"
        movies = content == "movies"
        rows.append([f"{SUBSET_DIR}/{root}/", content, scraper,
                     2147483647 if movies else 0, 1 if movies else 0,
                     _scraper_settings(scraper, scrape_lang)])
    setup = (
        "import json, os, sqlite3, sys\n"
        "links, rows = json.load(sys.stdin)\n"
        "for target, link in links:\n"
        "    os.makedirs(os.path.dirname(link), exist_ok=True)\n"
        "    os.path.lexists(link) or os.symlink(target, link)\n"
        f"c = sqlite3.connect('{VIDEO_DB}')\n"
        "for path, content, scraper, recursive, folder_names, settings in rows:\n"
        "    c.execute('INSERT INTO path(strPath, strContent, strScraper, scanRecursive,"
        " useFolderNames, strSettings, noUpdate, exclude, allAudio)"
        " VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0)',"
        " (path, content, scraper, recursive, folder_names, settings))\n"
        "c.commit()\n"
    )
    sh("docker", "exec", "-i", CONTAINER, "python3", "-c", setup,
       stdin=json.dumps([links, rows]))
    for root in SUBSET:
        rpc("VideoLibrary.Scan", {"directory": f"{SUBSET_DIR}/{root}/", "showdialogs": False})
        time.sleep(5)
        wait_until(lambda: not rpc("XBMC.GetInfoBooleans", {
            "booleans": ["Library.IsScanningVideo"]})["Library.IsScanningVideo"],
            1800, f"Kodi scan of {root}", step=5)


# --- metamender ---------------------------------------------------------------

def _tmdb_key() -> str:
    for line in (REPO / "docker" / ".env").read_text().splitlines():
        if line.startswith("TMDB_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("TMDB_API_KEY missing from docker/.env")


def write_mm_settings(settings: dict[str, Any]) -> None:
    values = dict(BASE_SETTINGS, tmdb_api_key=_tmdb_key())
    values.update({k: v for k, v in settings.items() if k != "sources"})
    body = "".join(f'    <setting id="{k}">{v}</setting>\n' for k, v in values.items())
    put_file(f"{ADDON_DATA}/settings.xml", f'<settings version="2">\n{body}</settings>\n')
    if settings.get("sources", True):
        sources = {"default_language": "en-US", "sources": [
            {"path": f"{SUBSET_DIR}/{root}/", "language": lang, "enabled": True}
            for root, (_s, lang, _c, _f) in SUBSET.items()]}
        put_file(f"{ADDON_DATA}/sources.json", json.dumps(sources, indent=2))
    else:
        dexec(f"rm -f '{ADDON_DATA}/sources.json'")


def kodi_log() -> str:
    return dexec(f"cat {KODI_HOME}/temp/kodi.log", check=False)


def run_service_scan() -> str:
    """Restart Kodi, which makes the service scan at once, and wait for it."""
    restart_kodi()
    if not addon_ready(ADDON_ID):
        wait_until(lambda: addon_ready(ADDON_ID), 60, "metamender enabled")

    def finished() -> bool:
        log = kodi_log()
        for stop in ("service.scan_failed", "service.scan_apikey", "service.skip_no_key"):
            if stop in log:
                raise RuntimeError(f"metamender scan stopped: {stop}")
        return "event=service.scan_complete" in log

    wait_until(finished, SCAN_TIMEOUT_SEC, "metamender scan", step=10)
    return kodi_log()


def snapshot() -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for media_type, (method, key, id_field) in LIST_CALLS.items():
        props = FIELDS[media_type] + ["file", "uniqueid"]
        if media_type == "episode":
            props += ["showtitle", "season", "episode"]
        for item in rpc(method, {"properties": props}).get(key, []):
            items[f"{media_type}:{item[id_field]}"] = item
    return items


def save_tracker(dest: Path) -> None:
    """Copy the tracker with its WAL: recent writes live only in -wal until
    sqlite checkpoints, and the copy is opened under the same suffixes."""
    for suffix in ("", "-wal", "-shm"):
        sh("docker", "cp", f"{CONTAINER}:{ADDON_DATA}/backfill.db{suffix}",
           f"{dest}{suffix}", check=bool(suffix == ""))


def tracker_fields(db: Path) -> dict[str, dict[str, Any]]:
    """field_status rows keyed ``<media_type>:<kodi_id>:<field>``."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT i.media_type, i.kodi_id, f.field, f.source, f.fetch_count,"
        " f.from_fallback, f.written_value, f.next_retry, f.fallback_checks,"
        " f.settings_fp, i.identity, i.id AS item_id FROM field_status f"
        " JOIN items i ON i.id = f.item_id").fetchall()
    conn.close()
    return {f"{r['media_type'].lower()}:{r['kodi_id']}:{r['field']}": dict(r) for r in rows}


# --- One run ------------------------------------------------------------------

def parse_run(name: str) -> tuple[str, str, str | None]:
    parts = name.split("-")
    if len(parts) not in (2, 3) or parts[0] not in KODI or parts[1] not in MM or (
            len(parts) == 3 and parts[2] not in CHANGES):
        raise SystemExit(f"unknown run {name!r}; known: {', '.join(PLAN)}")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else None


def hand_edit(tracker: dict[str, dict[str, Any]], snap: dict[str, dict[str, Any]]
              ) -> dict[str, Any] | None:
    """Edit one value metamender filled from the fallback, as a user would."""
    for key, row in sorted(tracker.items()):
        media_type, kodi_id, field = key.split(":")
        if row["from_fallback"] and field in ("plot", "title") and media_type == "episode":
            if f"{media_type}:{kodi_id}" not in snap:
                continue
            value = f"Handmatig bewerkt ({field})"
            rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": int(kodi_id), field: value})
            return {"key": f"{media_type}:{kodi_id}", "field": field, "value": value}
    return None


def run_one(name: str, out: Path) -> None:
    kodi_id, mm_id, change_id = parse_run(name)
    run_dir = out / name
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    print(f"[{name}] fresh Kodi", flush=True)
    fresh_kodi()
    scrapers = {a: ".".join(map(str, _version(a))) for a in MIN_VERSIONS}
    print(f"[{name}] building library with {KODI[kodi_id]}", flush=True)
    build_library(kodi_id)
    (run_dir / "A.json").write_text(json.dumps(snapshot(), indent=1, ensure_ascii=False))

    dexec(f"ln -sfn /addon-metamender {KODI_HOME}/addons/{ADDON_ID}")
    write_mm_settings(MM[mm_id])
    logs: dict[str, str] = {}
    stages = ["B", "C"] + (["D"] if change_id else [])
    for stage in stages:
        if stage == "D":
            assert change_id is not None
            edit = hand_edit(tracker_fields(run_dir / "C.db"),
                             json.loads((run_dir / "C.json").read_text()))
            (run_dir / "edit.json").write_text(json.dumps(edit, ensure_ascii=False))
            write_mm_settings(dict(MM[mm_id], **CHANGES[change_id]))
        print(f"[{name}] metamender scan {stage}", flush=True)
        logs[stage] = run_service_scan()
        (run_dir / f"kodi-{stage}.log").write_text(logs[stage])
        (run_dir / f"{stage}.json").write_text(
            json.dumps(snapshot(), indent=1, ensure_ascii=False))
        save_tracker(run_dir / f"{stage}.db")
    sh("docker", "cp", f"{CONTAINER}:{ADDON_DATA}/logs", str(run_dir / "mm-logs"),
       check=False)
    report = analyse(name, run_dir)
    report["scrapers"] = scrapers
    report["duration_min"] = round((time.time() - started) / 60, 1)
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"[{name}] done: {report['verdict']}", flush=True)


# --- Analysis -----------------------------------------------------------------

def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for key, old in before.items():
        new = after.get(key)
        if new is None:
            continue
        media_type = key.split(":")[0]
        fields = {f: [old.get(f), new.get(f)] for f in FIELDS[media_type]
                  if old.get(f) != new.get(f)}
        if fields:
            changes[key] = fields
    return changes


_STATS_RE = re.compile(r"event=service\.scan_complete, (.*)")


def _scan_stats(log: str) -> dict[str, str]:
    match = _STATS_RE.search(log)
    if not match:
        return {}
    return dict(p.split("=", 1) for p in match.group(1).split(", ") if "=" in p)


def _problems(log: str) -> list[str]:
    """metamender errors and warnings, and any Python traceback, in kodi.log."""
    found = []
    lines = log.splitlines()
    for i, line in enumerate(lines):
        if any(h in line for h in KNOWN_HARMLESS):
            continue
        logged = "metamender" in line and (" error " in line or " warning " in line)
        traceback = "Traceback" in line and "metamender" in "\n".join(lines[i:i + 30])
        if logged or traceback:
            found.append(line.strip())
    return found


TMDB_SLOTS = {"tmdb", "themoviedb"}


def _slots_added(field: str, old: Any, new: Any) -> bool:
    """A ratings change that only adds slots and leaves the old ones as they were."""
    if field != "ratings" or not isinstance(old, dict) or not isinstance(new, dict):
        return False
    return all(new.get(name) == slot for name, slot in old.items())


def _label(item: dict[str, Any]) -> str:
    if "showtitle" in item:
        return f"{item['showtitle']} S{item['season']:02}E{item['episode']:02}"
    return item.get("title") or item.get("label", "?")


def analyse(name: str, run_dir: Path) -> dict[str, Any]:
    is_nfo, blank, _ = _engine_rules()
    snaps = {s: json.loads((run_dir / f"{s}.json").read_text())
             for s in "ABCD" if (run_dir / f"{s}.json").exists()}
    trackers = {s: tracker_fields(run_dir / f"{s}.db")
                for s in "BCD" if (run_dir / f"{s}.db").exists()}
    logs = {s: (run_dir / f"kodi-{s}.log").read_text()
            for s in "BCD" if (run_dir / f"kodi-{s}.log").exists()}
    a, b, c = snaps["A"], snaps["B"], snaps["C"]
    nfo = {k: is_nfo(k.split(":")[0], v["file"]) for k, v in a.items()}
    ab, bc = _diff(a, b), _diff(b, c)
    checks: dict[str, list[str]] = {
        "nfo_protected": [], "second_scan_quiet": [], "no_errors": [],
        "fallback_fill_only": [], "change_reopens_only_fallback": [],
        "one_tmdb_rating": [],
    }
    notes: dict[str, list[str]] = {"real_text_replaced": [], "still_blank": []}

    for key, fields in ab.items():
        label = _label(a[key])
        for field, (old, new) in fields.items():
            row = trackers["B"].get(f"{key}:{field}", {})
            if nfo[key] and not blank(field, old) and not _slots_added(field, old, new):
                checks["nfo_protected"].append(f"{label}: {field} {old!r} -> {new!r}")
            if field in TEXT_FIELDS and not blank(field, old):
                if row.get("from_fallback"):
                    checks["fallback_fill_only"].append(
                        f"{label}: {field} real text replaced from the fallback: "
                        f"{str(old)[:60]!r} -> {str(new)[:60]!r}")
                else:
                    notes["real_text_replaced"].append(
                        f"{label}: {field} {str(old)[:60]!r} -> {str(new)[:60]!r}")
    for key, item in b.items():
        media_type = key.split(":")[0]
        for field in ("title", "plot"):
            if field in FIELDS[media_type] and blank(field, item.get(field)):
                row = trackers["B"].get(f"{key}:{field}", {})
                notes["still_blank"].append(
                    f"{_label(item)}: {field} {item.get(field)!r} "
                    f"(source={row.get('source')}, fetches={row.get('fetch_count')})")
    for key, item in b.items():
        slots = set(item.get("ratings") or {})
        if TMDB_SLOTS <= slots:
            checks["one_tmdb_rating"].append(f"{_label(item)}: {sorted(slots)}")
    for key, fields in bc.items():
        checks["second_scan_quiet"].append(f"{_label(b[key])}: {sorted(fields)}")
    stats_c = _scan_stats(logs["C"])
    if any(v != "0" for k, v in stats_c.items() if k.endswith("_updated")):
        checks["second_scan_quiet"].append(f"scan C stats: {stats_c}")
    for stage, log in logs.items():
        checks["no_errors"] += [f"{stage}: {p}" for p in _problems(log)]

    edit = None
    if "D" in snaps:
        edit = json.loads((run_dir / "edit.json").read_text())
        for key, fields in _diff(c, snaps["D"]).items():
            for field in fields:
                filled = trackers["C"].get(f"{key}:{field}", {}).get("from_fallback")
                if edit and edit["key"] == key and edit["field"] == field:
                    continue  # the edit itself; checked against its value below
                was_blank = blank(field, c[key].get(field))
                if field in TEXT_FIELDS and not (filled or was_blank):
                    checks["change_reopens_only_fallback"].append(
                        f"{_label(c[key])}: {field} {fields[field]}")
        if edit and snaps["D"][edit["key"]].get(edit["field"]) != edit["value"]:
            checks["change_reopens_only_fallback"].append(f"hand edit overwritten: {edit}")

    fallback_rows = sum(1 for r in trackers["B"].values() if r["from_fallback"])
    return {
        "run": name,
        "verdict": "PASS" if not any(checks.values()) else "FAIL",
        "items": {t: sum(1 for k in a if k.startswith(t + ":")) for t in FIELDS},
        "nfo_items": sum(nfo.values()),
        "changed_items_scan_B": len(ab),
        "changed_fields_scan_B": sum(len(f) for f in ab.values()),
        "fallback_filled_fields": fallback_rows,
        "stats": {s: _scan_stats(log) for s, log in logs.items()},
        "hand_edit": edit,
        "checks": checks,
        "notes": notes,
    }


def summary(out: Path) -> None:
    reports = [json.loads(p.read_text()) for p in sorted(out.glob("*/report.json"))]
    lines = ["| Run | Verdict | Items | NFO | Changed (B) | Fallback fills | "
             + " | ".join(reports[0]["checks"]) + " | Real text replaced | Still blank |",
             "|" + "---|" * (8 + len(reports[0]["checks"]))] if reports else []
    for r in reports:
        counts = " | ".join(str(len(v)) for v in r["checks"].values())
        lines.append(
            f"| {r['run']} | {r['verdict']} | {sum(r['items'].values())} | {r['nfo_items']} "
            f"| {r['changed_fields_scan_B']} | {r['fallback_filled_fields']} | {counts} "
            f"| {len(r['notes']['real_text_replaced'])} | {len(r['notes']['still_blank'])} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--plan", action="store_true", help="every planned run")
    parser.add_argument("--summary", action="store_true", help="only rebuild summary.md")
    parser.add_argument("--reanalyse", action="store_true",
                        help="rebuild each run's report from its saved files")
    args = parser.parse_args()
    if args.reanalyse:
        for run_dir in sorted(p for p in args.out.iterdir() if (p / "C.json").exists()):
            report = analyse(run_dir.name, run_dir)
            (run_dir / "report.json").write_text(
                json.dumps(report, indent=1, ensure_ascii=False))
    if not args.summary and not args.reanalyse:
        names = PLAN if args.plan else args.runs
        for name in names:
            parse_run(name)
        for name in names:
            try:
                run_one(name, args.out)
            except (RuntimeError, OSError) as exc:
                print(f"[{name}] ERROR: {exc}", flush=True)
                (args.out / name).mkdir(parents=True, exist_ok=True)
                (args.out / name / "error.txt").write_text(str(exc))
    summary(args.out)


if __name__ == "__main__":
    main()

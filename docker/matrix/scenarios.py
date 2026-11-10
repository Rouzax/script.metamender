#!/usr/bin/env python3
"""Timeline scenarios: one matrix library taken through time passing, data
changing between scans, and outliers, with metamender scanning after each step.

    docker/matrix/scenarios.py --out DIR TIME CHANGES OUTLIERS

Every scenario builds its library like a run.py run, lets metamender scan it
once, then works through its steps. A step changes something (moves the
tracker's dates back to stand in for time passing, edits Kodi, blocks the
providers, ...), lets the service scan, and is checked:

- clock: a field's fetch_count rises by at most one, and only when the scan
  logged that field as due;
- decisions: the decisions the scan logged are the ones metamender's maturity
  rules give for the tracker as it was before the scan;
- nfo: an NFO item keeps every value that was not empty, a placeholder, or
  metamender's own;
- errors: no metamender error or warning, or traceback, beyond the ones the
  step expects;
- the step's own expectations.

Moving dates back cannot age the content itself: a show from 2026 stays young
however far the tracker moves, so its fields keep their refetch schedule.

Results go to ``DIR/<scenario>/``: per step the snapshots, tracker and logs,
and ``report.json`` with each step's fetch and write counts, failures and the
values it changed; ``DIR/scenarios.md`` summarises every scenario.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import run as rig

THRESHOLD_DAYS = 365


# --- Captured state -------------------------------------------------------------

@dataclass
class State:
    snap: dict[str, dict[str, Any]]
    tracker: dict[str, dict[str, Any]]
    log: str = ""
    scan_log: str = ""


@dataclass
class Ctx:
    run_dir: Path
    state: State
    targets: dict[str, str] = field(default_factory=dict)
    marks: dict[str, Any] = field(default_factory=dict)


Action = Callable[[Ctx], None]
Expect = Callable[[Ctx, State, State, State], list[str]]


@dataclass
class Step:
    label: str
    act: Action | None = None
    scan: str = "restart"  # restart | manual | apikey | killed | event
    allow: tuple[str, ...] = ()
    expect: Expect | None = None


def capture(step_dir: Path, log: str) -> State:
    step_dir.mkdir(parents=True, exist_ok=True)
    snap = rig.snapshot()
    (step_dir / "snap.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False))
    rig.save_tracker(step_dir / "tracker.db")
    scan_log = rig.dexec(f"cat \"$(ls -t {rig.ADDON_DATA}/logs/*.log | head -1)\"",
                         check=False)
    (step_dir / "kodi.log").write_text(log)
    (step_dir / "scan.log").write_text(scan_log)
    return State(snap, rig.tracker_fields(step_dir / "tracker.db"), log, scan_log)


# --- Actions --------------------------------------------------------------------

_SET = {"movie": ("VideoLibrary.SetMovieDetails", "movieid"),
        "tvshow": ("VideoLibrary.SetTVShowDetails", "tvshowid"),
        "episode": ("VideoLibrary.SetEpisodeDetails", "episodeid")}


def set_fields(key: str, **values: Any) -> None:
    media_type, kodi_id = key.split(":")
    method, id_field = _SET[media_type]
    rig.rpc(method, {id_field: int(kodi_id), **values})


def _tracker_sql(script: str, *args: Any) -> None:
    """Run ``script`` against the tracker in the container, with ``db``
    open and ``args`` decoded from JSON."""
    code = (
        "import json, re, sqlite3, sys\n"
        "from datetime import date, datetime, timedelta\n"
        "args = json.load(sys.stdin)\n"
        f"db = sqlite3.connect('{rig.ADDON_DATA}/backfill.db')\n"
        + script + "\ndb.commit()\n"
    )
    rig.sh("docker", "exec", "-i", rig.CONTAINER, "python3", "-c", code,
           stdin=json.dumps(list(args)))


_SHIFT = """
days = args[0]
iso = re.compile(r'^(\\d{4}-\\d{2}-\\d{2})(.*)$')
keep = {'premiered', 'date_added', 'title', 'identity', 'media_type'}
for table in ('items', 'field_status', 'scan_state'):
    cols = [r[1] for r in db.execute(f'PRAGMA table_info({table})')]
    for row in db.execute(f'SELECT rowid, * FROM {table}').fetchall():
        for col, value in zip(cols, row[1:]):
            if col in keep or not isinstance(value, str):
                continue
            m = iso.match(value)
            if not m:
                continue
            moved = (date.fromisoformat(m.group(1)) - timedelta(days=days)).isoformat()
            db.execute(f'UPDATE {table} SET {col} = ? WHERE rowid = ?',
                       (moved + m.group(2), row[0]))
"""


def shift(days: int) -> Action:
    """Stand in for ``days`` passing: every date the tracker holds moves back."""
    def act(_ctx: Ctx) -> None:
        _tracker_sql(_SHIFT, days)
    return act


def forget(*keys: str) -> None:
    """Drop items from the tracker, so the next scan treats them as new."""
    _tracker_sql(
        "for key in args[0]:\n"
        "    media_type, kodi_id = key.split(':')\n"
        "    ids = [r[0] for r in db.execute('SELECT id FROM items WHERE media_type = ?"
        " AND kodi_id = ?', (media_type, int(kodi_id)))]\n"
        "    for item_id in ids:\n"
        "        db.execute('DELETE FROM field_status WHERE item_id = ?', (item_id,))\n"
        "        db.execute('DELETE FROM items WHERE id = ?', (item_id,))\n",
        list(keys))


def set_written(key: str, tracker_field: str, written_value: str) -> None:
    media_type, kodi_id = key.split(":")
    _tracker_sql(
        "db.execute('UPDATE field_status SET written_value = ? WHERE field = ? AND item_id IN"
        " (SELECT id FROM items WHERE media_type = ? AND kodi_id = ?)',"
        " (args[0], args[1], args[2], int(args[3])))",
        written_value, tracker_field, media_type, kodi_id)


BLOCKED = "api.themoviedb.org api4.thetvdb.com"
# Both families: the container resolves the APIs over IPv6 too, and an IPv4
# entry alone leaves those addresses reachable.
HOSTS_LINES = (f"127.0.0.1 {BLOCKED} # matrix-block", f"::1 {BLOCKED} # matrix-block")
_PROBE = (
    "import socket, sys\n"
    "try:\n"
    "    socket.create_connection(('api.themoviedb.org', 443), timeout=5).close()\n"
    "except OSError:\n"
    "    sys.exit(1)\n"
)


def block_providers(on: bool) -> Action:
    def act(_ctx: Ctx) -> None:
        # /etc/hosts is a bind mount, so it is rewritten in place, not replaced.
        added = "".join(f"echo '{line}' >> /tmp/hosts; " for line in HOSTS_LINES)
        rig.dexec("grep -v matrix-block /etc/hosts > /tmp/hosts; "
                  + (added if on else "") + "cat /tmp/hosts > /etc/hosts")
        if _probe_ok() == on:
            raise RuntimeError(f"TMDB {'still reachable' if on else 'not reachable'}")
    return act


def _probe_ok() -> bool:
    out = subprocess.run(["docker", "exec", rig.CONTAINER, "python3", "-c", _PROBE],
                             capture_output=True, check=False)
    return out.returncode == 0


def api_key(valid: bool) -> Action:
    def act(_ctx: Ctx) -> None:
        settings = dict(rig.MM[_ctx.marks["mm"]])
        if not valid:
            settings["tmdb_api_key"] = "0" * 32
        rig.write_mm_settings(settings)
    return act


def both(*actions: Action) -> Action:
    def act(ctx: Ctx) -> None:
        for a in actions:
            a(ctx)
    return act


# --- Scans ----------------------------------------------------------------------

def _count(log: str, needle: str) -> int:
    return log.count(needle)


def scan(mode: str) -> str:
    if mode == "restart":
        return rig.run_service_scan()
    if mode == "apikey":
        # Either verdict is a result: the expectation decides which is right.
        rig.restart_kodi()
        rig.wait_until(lambda: any(e in rig.kodi_log() for e in (
            "event=service.scan_apikey", "event=service.scan_complete")),
            600, "scan verdict", step=5)
        return rig.kodi_log()
    if mode == "killed":
        rig.restart_kodi()
        rig.wait_until(lambda: _count(rig.dexec(
            f"cat \"$(ls -t {rig.ADDON_DATA}/logs/*.log | head -1)\"", check=False),
            "event=scan.processing") >= 40, 900, "scan under way", step=1)
        rig.sh("docker", "kill", "--signal=KILL", rig.CONTAINER)
        rig.sh(*rig.COMPOSE, "up", "-d")
        rig.wait_ready()
        return rig.run_service_scan()
    if mode == "manual":
        # The add-on's own manual scan, without a restart: Docker rewrites
        # /etc/hosts when the container restarts, which would lift a block.
        before = _count(rig.kodi_log(), "event=manual.")
        rig.rpc("Addons.ExecuteAddon", {"addonid": rig.ADDON_ID})
        rig.wait_until(lambda: any(e in rig.kodi_log() for e in (
            "event=manual.complete", "event=manual.apikey", "event=manual.error"))
            and _count(rig.kodi_log(), "event=manual.") > before + 1,
            1800, "manual scan", step=5)
        return rig.kodi_log()
    if mode == "event":
        before = _count(rig.kodi_log(), "event=service.scan_complete")
        rig.wait_until(lambda: _count(rig.kodi_log(), "event=service.scan_complete") > before,
                       1800, "library-event scan", step=15)
        return rig.kodi_log()
    raise ValueError(mode)


# --- Checks ---------------------------------------------------------------------

_PROCESSING = re.compile(
    r"event=scan\.processing, title=.*?, identity=(.*?), media_type=(\w+), .*?"
    r"decisions=(\{.*?\}), rebaseline=(\[.*?\])")


def logged_decisions(scan_log: str) -> dict[tuple[str, str], tuple[dict[str, str], list[str]]]:
    out = {}
    for m in _PROCESSING.finditer(scan_log):
        out[(m.group(2), m.group(1))] = (ast.literal_eval(m.group(3)),
                                        ast.literal_eval(m.group(4)))
    return out


def _by_identity(tracker: dict[str, dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(r["media_type"], r["identity"], r["field"]): r for r in tracker.values()}


def _before(pre: State, post: State) -> dict[tuple[str, str, str], dict[str, Any]]:
    """The pre-scan rows under the post-scan keys. Rows are matched on the
    tracker's row id, which survives metamender renaming an item's identity
    (tracker.adopt_identity); a row the step dropped and the scan made again
    has a new id and starts from nothing."""
    by_id = {(r["item_id"], r["field"]): r for r in pre.tracker.values()}
    return {(r["media_type"], r["identity"], r["field"]): by_id.get((r["item_id"], r["field"]), {})
            for r in post.tracker.values()}


def check_clock(pre: State, post: State) -> list[str]:
    decisions = logged_decisions(post.scan_log)
    found = []
    old = _before(pre, post)
    for key, row in _by_identity(post.tracker).items():
        before = int(old.get(key, {}).get("fetch_count") or 0)
        delta = int(row["fetch_count"] or 0) - before
        if delta not in (0, 1):
            found.append(f"{key}: fetch_count {before} -> {row['fetch_count']}")
        elif delta == 1:
            # A due field makes the scan refresh every non-final field of the
            # item, so a field not due itself may advance; a final one may not.
            item_decisions = decisions.get((key[0], key[1]), ({}, []))[0]
            logged = item_decisions.get(key[2])
            if logged == "final" or (
                    logged is not None and "fetch_now" not in item_decisions.values()):
                found.append(f"{key}: fetched although logged {logged}")
    return found


def check_decisions(pre: State, post: State, maturity: Any) -> list[str]:
    """The scan's logged decisions against metamender's own rules applied to
    the tracker before the scan."""
    today = date.today().isoformat()
    old = _before(pre, post)
    kodi_ids = {(r["media_type"], r["identity"]): r["kodi_id"] for r in post.tracker.values()}
    found = []
    for (media_type, identity), (decisions, rebaseline) in logged_decisions(post.scan_log).items():
        item = pre.snap.get(f"{media_type}:{kodi_ids.get((media_type, identity))}", {})
        raw = item.get("firstaired" if media_type == "episode" else "premiered") or ""
        try:
            age = (date.fromisoformat(today) - date.fromisoformat(raw[:10])).days \
                if len(raw) >= 10 else None
        except ValueError:
            age = None
        for f, logged in decisions.items():
            if f in rebaseline:
                continue
            row = old.get((media_type, identity, f), {})
            base = maturity.decide_field(age, int(row.get("fetch_count") or 0),
                                         row.get("next_retry"), today, THRESHOLD_DAYS)
            want = maturity.apply_fallback_recheck(
                base, bool(row.get("from_fallback")), int(row.get("fallback_checks") or 0),
                row.get("next_retry"), today)
            if want != logged:
                found.append(f"{identity} {f}: logged {logged}, rules give {want}")
    return found


def check_nfo(ctx: Ctx, pre: State, post: State, is_nfo: Any, blank: Any) -> list[str]:
    owned = ctx.marks.get("owned", set())
    found = []
    for key, fields in rig._diff(pre.snap, post.snap).items():
        if not is_nfo(key.split(":")[0], pre.snap[key]["file"]):
            continue
        for f, (old, new) in fields.items():
            if blank(f, old) or rig._slots_added(f, old, new) or (key, f) in owned:
                continue
            found.append(f"{rig._label(pre.snap[key])}: {f} {old!r} -> {new!r}")
    return found


def writes(pre: State, post: State) -> list[str]:
    return [f"{rig._label(pre.snap[k])}: {f} {str(o)[:50]!r} -> {str(n)[:50]!r}"
            for k, fields in rig._diff(pre.snap, post.snap).items()
            for f, (o, n) in fields.items()]


def fetched(pre: State, post: State) -> int:
    old = _before(pre, post)
    return sum(int(r["fetch_count"] or 0) - int(old.get(k, {}).get("fetch_count") or 0)
               for k, r in _by_identity(post.tracker).items())


def problems(log: str, allow: tuple[str, ...]) -> list[str]:
    return [p for p in rig._problems(log) if not any(a in p for a in allow)]


# --- Targets --------------------------------------------------------------------

def pick(state: State, is_nfo: Any, media_type: str, *, nfo: bool = False,
         root: str | None = None, show: str | None = None, young: bool | None = None,
         skip: tuple[str, ...] = ()) -> str:
    today = date.today()
    for key, item in sorted(state.snap.items(), key=lambda kv: int(kv[0].split(":")[1])):
        if not key.startswith(media_type + ":") or key in skip:
            continue
        if is_nfo(media_type, item["file"]) != nfo:
            continue
        if root and f"/{root}/" not in item["file"]:
            continue
        if show and item.get("showtitle") != show:
            continue
        raw = item.get("firstaired" if media_type == "episode" else "premiered") or ""
        if young is not None:
            if len(raw) < 10:
                continue
            is_young = (today - date.fromisoformat(raw[:10])).days < THRESHOLD_DAYS
            if is_young != young:
                continue
        if not (item.get("uniqueid") or {}).get("tmdb"):
            continue
        return key
    raise RuntimeError(f"no {media_type} target (nfo={nfo}, root={root}, show={show})")


# --- Scenarios ------------------------------------------------------------------

def _value(state: State, key: str, f: str) -> Any:
    return state.snap[key].get(f)


def _tmdb_slot(state: State, key: str) -> dict[str, Any]:
    return (state.snap[key].get("ratings") or {}).get("themoviedb") or {}


def time_steps() -> list[Step]:
    steps = [Step("rescan the same day",
                  expect=lambda c, b, pre, post: (
                      [f"fetched {fetched(pre, post)} fields"] if fetched(pre, post) else []))]
    total = 0
    for days in (8, 23, 60, 310):
        total += days
        steps.append(Step(f"{total} days later", shift(days)))
    for n in range(13):
        total += 31
        steps.append(Step(f"{total} days later (monthly {n + 1})", shift(31)))
    return steps


def changes_setup(ctx: Ctx, is_nfo: Any) -> None:
    s = ctx.state
    t = ctx.targets
    t["old_movie"] = pick(s, is_nfo, "movie", young=False, root="movies-en")
    t["young_movie"] = pick(s, is_nfo, "movie", young=True)
    t["edit_movie"] = pick(s, is_nfo, "movie", young=False, skip=(t["old_movie"],))
    t["young_ep"] = pick(s, is_nfo, "episode", young=True)
    t["empty_ep"] = pick(s, is_nfo, "episode", root="tv-nl", show="Planet Earth II")
    t["title_ep"] = pick(s, is_nfo, "episode", root="tv-nl", show="Planet Earth II",
                         skip=(t["empty_ep"],))
    t["refresh_ep"] = pick(s, is_nfo, "episode", root="tv-nl2")
    t["removed_ep"] = pick(s, is_nfo, "episode", root="tv-nl2", skip=(t["refresh_ep"],))
    t["wrong_id_movie"] = pick(s, is_nfo, "movie", root="movies-nl")
    ctx.marks["provider"] = {}


def fake_provider_change(target: str) -> Action:
    """Kodi holds an older rating and plot that metamender itself wrote, as if
    the provider's values changed since the last run."""
    def act(ctx: Ctx) -> None:
        from resources.lib.engine.protect import fingerprint
        from resources.lib.engine.resolve import slot_fingerprint
        key = ctx.targets[target]
        slot = dict(_tmdb_slot(ctx.state, key))
        if not slot:
            raise RuntimeError(f"{key} has no themoviedb rating to age")
        old_slot = {"rating": round(max(1.0, float(slot["rating"]) - 1.3), 1),
                    "votes": max(1, int(slot["votes"]) // 2)}
        ratings = dict(ctx.state.snap[key]["ratings"])
        ratings["themoviedb"] = {**old_slot, "default": slot.get("default", False)}
        old_plot = "Een oudere samenvatting die de provider sindsdien heeft vervangen."
        set_fields(key, ratings=ratings, plot=old_plot)
        row = ctx.state.tracker.get(f"{key}:rating", {})
        owned = json.loads(row.get("written_value") or "{}")
        owned["themoviedb"] = slot_fingerprint(old_slot)
        set_written(key, "rating", json.dumps(owned, sort_keys=True))
        set_written(key, "plot", fingerprint(old_plot))
        ctx.marks["provider"][target] = (slot, _value(ctx.state, key, "plot"))
        ctx.marks.setdefault("owned", set()).update({(key, "ratings"), (key, "plot"),
                                                     (key, "rating"), (key, "votes")})
    return act


def restored(target: str, expect_restore: bool) -> Expect:
    def expect(ctx: Ctx, _base: State, pre: State, post: State) -> list[str]:
        key = ctx.targets[target]
        slot, plot = ctx.marks["provider"][target]
        now_slot = _tmdb_slot(post, key)
        back = abs(float(now_slot.get("rating", 0)) - float(slot["rating"])) < 0.05
        plot_back = _value(post, key, "plot") == plot
        if expect_restore and not back:
            return [f"{target}: rating not refreshed: {now_slot} (provider {slot})"]
        if not expect_restore and back:
            return [f"{target}: rating refreshed before it was due"]
        if expect_restore != plot_back:
            return [f"{target}: plot {'not ' if expect_restore else ''}refreshed"]
        return []
    return expect


def unchanged(target: str, f: str, value_mark: str) -> Expect:
    def expect(ctx: Ctx, _base: State, pre: State, post: State) -> list[str]:
        key = ctx.targets[target]
        if _value(post, key, f) != ctx.marks[value_mark]:
            return [f"{target}: {f} changed: {_value(post, key, f)!r}"]
        return []
    return expect


def changes_steps() -> list[Step]:
    def edit_plot(ctx: Ctx) -> None:
        ctx.marks["edited_plot"] = "Mijn eigen samenvatting van deze film."
        set_fields(ctx.targets["edit_movie"], plot=ctx.marks["edited_plot"])

    def empty_and_placeholder(ctx: Ctx) -> None:
        set_fields(ctx.targets["empty_ep"], plot="")
        set_fields(ctx.targets["title_ep"], title="Episode 5")

    def refresh(ctx: Ctx) -> None:
        _media_type, kodi_id = ctx.targets["refresh_ep"].split(":")
        rig.rpc("VideoLibrary.RefreshEpisode", {"episodeid": int(kodi_id)})
        time.sleep(20)

    def remove(ctx: Ctx) -> None:
        _m, kodi_id = ctx.targets["removed_ep"].split(":")
        rig.rpc("VideoLibrary.RemoveEpisode", {"episodeid": int(kodi_id)})

    def removed_row(present: bool) -> Expect:
        def expect(ctx: Ctx, _b: State, pre: State, post: State) -> list[str]:
            key = ctx.targets["removed_ep"]
            has = any(k.startswith(key + ":") for k in post.tracker)
            return [] if has == present else [
                f"removed episode {'pruned too early' if present else 'not pruned'}"]
        return expect

    def wrong_id(ctx: Ctx) -> None:
        other = ctx.targets["old_movie"]
        set_fields(ctx.targets["wrong_id_movie"],
                   uniqueid={"tmdb": ctx.state.snap[other]["uniqueid"]["tmdb"]})
        forget(ctx.targets["wrong_id_movie"])

    return [
        Step("user edits the plot of an old non-NFO movie", edit_plot,
             expect=unchanged("edit_movie", "plot", "edited_plot")),
        Step("user empties a plot and puts a placeholder title on episodes",
             empty_and_placeholder),
        Step("provider rating and plot changed (young and old movie)",
             both(fake_provider_change("young_movie"), fake_provider_change("old_movie")),
             expect=lambda c, b, pre, post: restored("young_movie", False)(c, b, pre, post)
             + restored("old_movie", False)(c, b, pre, post)),
        Step("8 days later", shift(8),
             expect=lambda c, b, pre, post: restored("young_movie", True)(c, b, pre, post)
             + restored("old_movie", False)(c, b, pre, post)
             + unchanged("edit_movie", "plot", "edited_plot")(c, b, pre, post)),
        Step("Kodi refreshes an episode from its scraper", refresh),
        Step("an episode is removed from the library", remove, expect=removed_row(True)),
        Step("31 days later", shift(31), expect=removed_row(False)),
        Step("a movie gets another movie's TMDB id", wrong_id),
    ]


def outlier_setup(ctx: Ctx, is_nfo: Any) -> None:
    eps = [k for k in sorted(ctx.state.snap, key=lambda k: int(k.split(":")[1]))
           if k.startswith("episode:") and not is_nfo("episode", ctx.state.snap[k]["file"])
           and "/Planet Earth II/" not in ctx.state.snap[k]["file"]]
    if len(eps) < 16:
        raise RuntimeError("not enough non-NFO episodes for the outliers")
    for i, key in enumerate(eps[:16]):
        ctx.targets[f"ep{i}"] = key


ODD_IDS = [{"tmdb": "None", "tvdb": "None", "imdb": "None"}, {"tmdb": "0"}, {"tmdb": "-5"},
           {"tmdb": "9" * 30}, {"tmdb": " 68595 "}, {"imdb": "tt0000000"}]
ODD_TEXT = [("plot", "   "), ("plot", "[B][/B]"), ("plot", "x" * 20000),
            ("plot", "🎬🍿 " * 5), ("title", "TBA"), ("title", "Folge 2"),
            ("title", "Aflevering 3"), ("title", "Episode 05")]
ODD_DATES = ["", "2099-01-01", "1900-01-01"]


def outlier_steps() -> list[Step]:
    def odd_ids(ctx: Ctx) -> None:
        keys = [ctx.targets[f"ep{i}"] for i in range(len(ODD_IDS))]
        for key, ids in zip(keys, ODD_IDS):
            set_fields(key, uniqueid=ids)
        forget(*keys)

    def odd_text(ctx: Ctx) -> None:
        keys = [ctx.targets[f"ep{i}"] for i in range(6, 6 + len(ODD_TEXT))]
        for key, (f, value) in zip(keys, ODD_TEXT):
            set_fields(key, **{f: value})
        forget(*keys)

    def odd_dates(ctx: Ctx) -> None:
        keys = [ctx.targets[f"ep{i}"] for i in range(13, 13 + len(ODD_DATES))]
        for key, value in zip(keys, ODD_DATES):
            set_fields(key, firstaired=value)
        forget(*keys)

    def unlink(on: bool) -> Action:
        def act(ctx: Ctx) -> None:
            link = f"{rig.SUBSET_DIR}/tv-nl/Planet Earth II"
            if on:
                rig.dexec(f"rm '{link}'")
            else:
                rig.dexec(f"ln -s '/media/Kids/TV/Planet Earth II' '{link}'")
        return act

    def forget_some(ctx: Ctx) -> None:
        """Make a dozen items due, so the step's scan has to ask the providers."""
        forget(*[ctx.targets[f"ep{i}"] for i in range(6, 16)],
               *[k for k in sorted(ctx.state.snap) if k.startswith("movie:")][:2])

    def forget_show(ctx: Ctx) -> None:
        forget(*[k for k, item in ctx.state.snap.items()
                 if "/Planet Earth II/" in item["file"]])

    def no_fetches(_c: Ctx, _b: State, pre: State, post: State) -> list[str]:
        found = []
        n = fetched(pre, post)
        if n:
            found.append(f"{n} fields counted as fetched while unanswerable")
        if not _count(post.scan_log, "event=scan.processing"):
            found.append("no item was processed, so nothing was tested")
        return found

    def transient_seen(c: Ctx, b: State, pre: State, post: State) -> list[str]:
        found = no_fetches(c, b, pre, post)
        if not _count(post.scan_log, "event=scan.transient") and \
                "scan.breaker" not in post.scan_log:
            found.append("no transient answer logged")
        return found

    def unreachable_untouched(c: Ctx, b: State, pre: State, post: State) -> list[str]:
        found = []
        if "reason=unreachable" not in post.scan_log:
            found.append("no item was skipped as unreachable, so nothing was tested")
        for key, fields in rig._diff(pre.snap, post.snap).items():
            if "/Planet Earth II/" in pre.snap[key]["file"]:
                found.append(f"{rig._label(pre.snap[key])}: {sorted(fields)} written")
        old = _before(pre, post)
        for k, row in _by_identity(post.tracker).items():
            if k[1].startswith("Planet Earth II") and \
                    row["fetch_count"] != old.get(k, {}).get("fetch_count"):
                found.append(f"{k}: fetch_count moved while unreachable")
        return found

    def add_show(ctx: Ctx) -> None:
        rig.dexec(f"ln -s '/media/TVSeries/EN/Alice and Steve (2026)' "
                  f"'{rig.SUBSET_DIR}/tv-en/Alice and Steve (2026)'")
        rig.rpc("VideoLibrary.Scan", {"directory": f"{rig.SUBSET_DIR}/tv-en/",
                                      "showdialogs": False})

    def event_scan(_c: Ctx, _b: State, pre: State, post: State) -> list[str]:
        found = []
        if "reason=library_event" not in post.log or "targeted=True" not in post.log:
            found.append("no targeted library-event scan logged")
        if not any(r["identity"].startswith("Alice and Steve") for r in post.tracker.values()):
            found.append("new show not in the tracker")
        return found

    breaker = ("scan.breaker", "provider", "http", "transient", "unreachable")
    return [
        Step("odd uniqueids", odd_ids),
        Step("odd text in plots and titles", odd_text),
        Step("odd release dates", odd_dates),
        Step("a show folder disappears, 8 days later",
             both(forget_show, unlink(True), shift(8)), expect=unreachable_untouched),
        Step("the show folder is back", unlink(False)),
        Step("providers unreachable, 8 days later",
             both(block_providers(True), forget_some), scan="manual", allow=breaker,
             expect=transient_seen),
        Step("providers back", block_providers(False)),
        Step("invalid TMDB key", both(api_key(False), forget_some),
             scan="apikey", allow=("apikey", "API key", "api_key"),
             expect=lambda c, b, pre, post: no_fetches(c, b, pre, post) + (
                 [] if "event=service.scan_apikey" in post.log
                 else ["scan finished without reporting the invalid key"])),
        Step("valid key again", api_key(True)),
        Step("Kodi killed in the middle of a full rescan",
             lambda c: forget(*{k.rsplit(":", 1)[0] for k in c.state.tracker}),
             scan="killed"),
        Step("a new show is added while the service runs", add_show, scan="event",
             expect=event_scan),
    ]


SCENARIOS: dict[str, tuple[str, str, Callable[[Ctx, Any], None] | None, Callable[[], list[Step]]]] = {
    "TIME": ("K1", "M2", None, time_steps),
    "CHANGES": ("K1", "M1", changes_setup, changes_steps),
    "OUTLIERS": ("K1", "M1", outlier_setup, outlier_steps),
    "OUTAGE": ("K1", "M1", outlier_setup,
               lambda: [s for s in outlier_steps() if s.label.startswith("providers")]),
}


def run_scenario(name: str, out: Path) -> dict[str, Any]:
    kodi_id, mm_id, setup, make_steps = SCENARIOS[name]
    is_nfo, blank, _ = rig._engine_rules()
    from resources.lib.engine import maturity
    run_dir = out / name
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{name}] building {kodi_id}-{mm_id}", flush=True)
    rig.fresh_kodi()
    rig.build_library(kodi_id)
    rig.dexec(f"ln -sfn /addon-metamender {rig.KODI_HOME}/addons/{rig.ADDON_ID}")
    rig.write_mm_settings(rig.MM[mm_id])
    state = capture(run_dir / "00-first-scan", rig.run_service_scan())
    ctx = Ctx(run_dir, state, marks={"mm": mm_id})
    if setup:
        setup(ctx, is_nfo)
    base = state
    results = []
    for n, step in enumerate(make_steps(), 1):
        print(f"[{name}] step {n}: {step.label}", flush=True)
        step_dir = run_dir / f"{n:02}-{re.sub(r'[^a-z0-9]+', '-', step.label.lower())[:40]}"
        try:
            if step.act:
                step.act(ctx)
            step_dir.mkdir(parents=True, exist_ok=True)
            rig.save_tracker(step_dir / "pre-tracker.db")
            pre = State(rig.snapshot(), rig.tracker_fields(step_dir / "pre-tracker.db"))
            (step_dir / "pre-snap.json").write_text(
                json.dumps(pre.snap, indent=1, ensure_ascii=False))
            post = capture(step_dir, scan(step.scan))
        except (RuntimeError, OSError) as exc:
            results.append({"step": step.label, "error": str(exc)})
            print(f"[{name}] step {n} ERROR: {exc}", flush=True)
            break
        failures = {
            "clock": check_clock(pre, post),
            # A killed scan did part of the work under the pre-step tracker.
            "decisions": [] if step.scan == "killed"
            else check_decisions(pre, post, maturity),
            "nfo": check_nfo(ctx, pre, post, is_nfo, blank),
            "errors": problems(post.log, step.allow),
            "expected": step.expect(ctx, base, pre, post) if step.expect else [],
        }
        results.append({
            "step": step.label,
            "fetched": fetched(pre, post),
            "written_items": _count(post.scan_log, "event=scan.updated"),
            "transient": _count(post.scan_log, "event=scan.transient"),
            "failures": {k: v for k, v in failures.items() if v},
            "writes": writes(pre, post),
        })
        ctx.state = post
    report = {"scenario": name, "targets": ctx.targets, "steps": results,
              "verdict": "PASS" if all(not r.get("failures") and "error" not in r
                                       for r in results) else "FAIL"}
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"[{name}] done: {report['verdict']}", flush=True)
    return report


def summary(out: Path) -> None:
    lines = []
    for path in sorted(out.glob("*/report.json")):
        report = json.loads(path.read_text())
        lines += [f"## {report['scenario']}: {report['verdict']}", "",
                  "| Step | Fetched | Items written | Transient | Failures |",
                  "|---|---|---|---|---|"]
        for r in report["steps"]:
            if "error" in r:
                lines.append(f"| {r['step']} | | | | ERROR: {r['error']} |")
                continue
            fails = "; ".join(f"{k}: {len(v)}" for k, v in r["failures"].items()) or "none"
            lines.append(f"| {r['step']} | {r['fetched']} | {r['written_items']} "
                         f"| {r['transient']} | {fails} |")
        lines.append("")
    (out / "scenarios.md").write_text("\n".join(lines))
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("scenarios", nargs="*", default=list(SCENARIOS))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if not args.summary:
        for name in args.scenarios:
            if name not in SCENARIOS:
                sys.exit(f"unknown scenario {name}; known: {', '.join(SCENARIOS)}")
        for name in args.scenarios:
            try:
                run_scenario(name, args.out)
            except (RuntimeError, OSError) as exc:
                print(f"[{name}] ERROR: {exc}", flush=True)
    summary(args.out)


if __name__ == "__main__":
    main()

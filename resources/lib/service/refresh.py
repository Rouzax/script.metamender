"""Update now on a TV show or a season: refresh the show record (for a show)
and every episode under it, with a progress window that can be stopped or
sent to Kodi's background progress bar.

Logging:
    Logger: 'refresh'
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import xbmc
import xbmcgui

from resources.lib.data.kodi import (
    get_all_items,
    get_episode_show_id,
    get_item_by_id,
    get_season_info,
)
from resources.lib.data.media_types import EPISODE, TVSHOW, MediaType
from resources.lib.data.sources import load_source_config
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.scan import run_scan
from resources.lib.service.daemon import (
    _get_db_path,
    _get_sources_config_path,
    _load_settings,
    _prepare_imdb_ratings,
)
from resources.lib.service.lock import (
    _home_window,
    acquire_scan_lock,
    release_scan_lock,
)
from resources.lib.ui.progress_window import show_progress_window
from resources.lib.utils import ApiKeyError, StructuredLogger, get_logger, lang, notify

log = get_logger('refresh')


@dataclass(frozen=True)
class Scope:
    """What one refresh covers: a whole show (season None) or one season."""
    tvshowid: int
    season: int | None
    title: str


def resolve_scope(dbtype: str, dbid: int) -> Scope | None:
    """The refresh scope of a clicked show or season, or None for anything
    else or an item Kodi no longer has."""
    if dbtype == "tvshow":
        tvshowid, season = dbid, None
    elif dbtype == "season":
        info = get_season_info(dbid)
        if info is None:
            return None
        tvshowid, season = info
    else:
        return None
    show = get_item_by_id(TVSHOW, tvshowid)
    if show is None:
        return None
    return Scope(tvshowid, season, str(show.get("title") or tvshowid))


def show_id_of(dbtype: str, dbid: int) -> int | None:
    """The show a clicked show, season or episode belongs to; None for a
    movie or anything Kodi cannot place."""
    if dbtype == "tvshow":
        return dbid
    if dbtype == "season":
        info = get_season_info(dbid)
        return info[0] if info else None
    if dbtype == "episode":
        return get_episode_show_id(dbid)
    return None


# Items done before the time left is shown: the first ones include the slow
# show record and warm-up, so an early estimate is mostly noise.
_ETA_MIN_DONE = 3


class RefreshProgress:
    """Counts and the current item of one refresh, shared between the worker
    thread that runs it and the main thread that shows it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total = 0
        self.done = 0
        self.updated = 0
        self.unchanged = 0
        self.unchecked = 0
        self.current = ""
        self._started = 0.0
        self._stop = False

    def begin(self, total: int, now: float) -> None:
        with self._lock:
            self.total, self._started = total, now

    def item_started(self, label: str) -> None:
        with self._lock:
            self.current = label

    def item_finished(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Count one item by what changed in the scan stats while it ran: an
        update wins over a field that could not be checked."""
        def grew(key: str) -> bool:
            return int(after.get(key) or 0) > int(before.get(key) or 0)
        with self._lock:
            self.done += 1
            if grew("updated"):
                self.updated += 1
            elif grew("transient") or grew("unreachable"):
                self.unchecked += 1
            else:
                self.unchanged += 1

    def request_stop(self) -> None:
        with self._lock:
            self._stop = True

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop

    def seconds_left(self, now: float) -> int | None:
        with self._lock:
            if self.total <= 0 or self.done < _ETA_MIN_DONE:
                return None
            per_item = (now - self._started) / self.done
            return round(per_item * max(self.total - self.done, 0))

    def percent(self) -> int:
        with self._lock:
            return 0 if self.total <= 0 else min(100, self.done * 100 // self.total)

    def bar_text(self) -> str:
        with self._lock:
            return lang(32474).format(self.done, self.total)

    def toast(self, title: str, stopped: bool, could_not_finish: bool) -> str:
        with self._lock:
            if stopped:
                return lang(32483).format(self.done, self.total, title)
            if could_not_finish:
                return lang(32484).format(title)
            if self.updated:
                return lang(32482).format(title, self.updated, self.total)
            if self.unchecked:
                return lang(32353).format(title)
            return lang(32351).format(title)


@dataclass
class RefreshOutcome:
    """How a refresh ended: stopped by the user, cut short because every
    provider it needed was down, or interrupted by Kodi shutting down."""
    stopped: bool = False
    could_not_finish: bool = False
    aborted: bool = False


def _label(media_type: MediaType, item: dict[str, Any]) -> str:
    if media_type is TVSHOW:
        return lang(32472)
    return lang(32473).format(item.get("season", ""), item.get("episode", ""),
                              item.get("title", ""))


def _pass(
    media_type: MediaType,
    items: list[dict[str, Any]],
    progress: RefreshProgress,
    settings: dict[str, Any],
    source_config: dict[str, Any],
    tracker: TrackerDB,
    monitor: Any,
    breaker: CircuitBreaker,
    show_id_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    """One run_scan pass over ``items``, counting each finished item into
    ``progress``: an item is counted when the next one starts or the pass
    ends, by what it changed in the stats."""
    pending: list[dict[str, Any]] = []

    def on_item(index: int, item: dict[str, Any], stats: dict[str, Any]) -> bool:
        if pending:
            progress.item_finished(pending.pop(), stats)
        if progress.stop_requested:
            return False
        pending.append(dict(stats))
        progress.item_started(_label(media_type, item))
        return True

    stats = run_scan(
        media_type, tracker, source_config, settings["enabled_fields"],
        settings["tmdb_api_key"], settings, monitor=monitor, items=items,
        reset=True, breaker=breaker, show_id_map=show_id_map, progress=on_item,
    )
    if pending:
        progress.item_finished(pending.pop(), stats)
    return stats


def run_refresh(
    scope: Scope,
    progress: RefreshProgress,
    settings: dict[str, Any],
    source_config: dict[str, Any],
    tracker: TrackerDB,
    monitor: Any,
) -> RefreshOutcome:
    """Refresh a show (its record, then every episode) or a season (its
    episodes), as Update now does for one item. The episode pass builds its
    show id map after the show pass, so it uses the ids that pass found."""
    episodes = get_all_items(EPISODE, tvshowid=scope.tvshowid, season=scope.season)
    show = get_item_by_id(TVSHOW, scope.tvshowid) if scope.season is None else None
    progress.begin(len(episodes) + (1 if show else 0), now=time.monotonic())
    log.info("Refresh starting", event="refresh.start", tvshowid=scope.tvshowid,
             season=scope.season, episodes=len(episodes))
    breaker = CircuitBreaker()
    passes: list[dict[str, Any]] = []
    if show:
        passes.append(_pass(TVSHOW, [show], progress, settings, source_config,
                            tracker, monitor, breaker))
    if episodes and not (passes and (passes[-1]["stopped"] or passes[-1]["aborted"])):
        passes.append(_pass(EPISODE, episodes, progress, settings, source_config,
                            tracker, monitor, breaker, tracker.get_show_id_map()))
    shutdown = bool(monitor is not None and monitor.abortRequested())
    outcome = RefreshOutcome(
        stopped=any(s.get("stopped") for s in passes),
        could_not_finish=not shutdown and any(s.get("aborted") for s in passes),
        aborted=shutdown,
    )
    log.info("Refresh finished", event="refresh.finish", tvshowid=scope.tvshowid,
             season=scope.season, done=progress.done, total=progress.total,
             updated=progress.updated, unchecked=progress.unchecked,
             stopped=outcome.stopped, could_not_finish=outcome.could_not_finish,
             aborted=outcome.aborted)
    return outcome


# Home-window properties shared with a second Update now (its own process):
# the running refresh's show, and its request to show the window again.
_RUNNING_SHOW = "metamender_refresh_show"
_REOPEN = "metamender_refresh_reopen"
# How often the background bar is updated and the reopen request checked.
_POLL_SECONDS = 0.5


def refresh_running_show() -> int | None:
    value = _home_window().getProperty(_RUNNING_SHOW)
    return int(value) if value.isdigit() else None


def request_reopen() -> None:
    _home_window().setProperty(_REOPEN, "1")


def _take_reopen() -> bool:
    window = _home_window()
    if window.getProperty(_REOPEN):
        window.clearProperty(_REOPEN)
        return True
    return False


def bring_back_or_busy(clicked_show: int | None) -> str:
    """What a second Update now does while a scan holds the lock: "reopen"
    when it points into the running refresh's show, else "busy"."""
    running = refresh_running_show()
    if running is not None and clicked_show == running:
        return "reopen"
    return "busy"


def drive_views(
    progress: RefreshProgress,
    done: threading.Event,
    bar_heading: str,
    show_window: Callable[[], str],
    make_bar: Callable[[], Any],
    take_reopen: Callable[[], bool],
    poll: float = _POLL_SECONDS,
) -> None:
    """Show the window, and after Background or Stop Kodi's background bar,
    until the refresh is done. A reopen request brings the window back;
    after Stop the bar only waits for the item in progress to finish."""
    while not done.is_set():
        choice = show_window()
        if choice == "stop":
            progress.request_stop()
        if done.is_set():
            return
        bar = make_bar()
        bar.create(bar_heading, progress.bar_text())
        try:
            while not done.wait(poll):
                bar.update(progress.percent(), bar_heading, progress.bar_text())
                if choice != "stop" and take_reopen():
                    break
        finally:
            bar.close()


def open_refresh(dbtype: str, dbid: int) -> None:
    """Update now on a show or season: run the refresh on a worker thread
    while this thread shows its progress, then report the result."""
    scope = resolve_scope(dbtype, dbid)
    if scope is None:
        notify(lang(32345))  # Item not found
        return
    if not acquire_scan_lock():
        if bring_back_or_busy(scope.tvshowid) == "reopen":
            request_reopen()
        else:
            notify(lang(32344))  # A scan is already running
        return
    home = _home_window()
    home.setProperty(_RUNNING_SHOW, str(scope.tvshowid))
    home.clearProperty(_REOPEN)
    StructuredLogger.start_scan_log()
    try:
        settings = _load_settings()
        if not settings.get("tmdb_api_key"):
            notify(lang(32346))  # No TMDB API key configured
            return
        progress = RefreshProgress()
        done = threading.Event()
        result: dict[str, Any] = {}
        monitor = xbmc.Monitor()

        def work() -> None:
            # The tracker's SQLite connection belongs to the thread that
            # opens it, so the worker opens its own.
            tracker = None
            try:
                tracker = TrackerDB(_get_db_path())
                source_config = load_source_config(_get_sources_config_path())
                settings["imdb_ready"] = _prepare_imdb_ratings(settings, monitor)
                result["outcome"] = run_refresh(
                    scope, progress, settings, source_config, tracker, monitor)
            except ApiKeyError:
                log.exception("Refresh stopped: invalid API key", event="refresh.apikey")
                result["error"] = 32341
            except Exception:
                log.exception("Refresh failed", event="refresh.error")
                result["error"] = 32347
            finally:
                if tracker is not None:
                    tracker.close()
                done.set()

        worker = threading.Thread(target=work, name="metamender-refresh")
        worker.start()
        heading = (lang(32470).format(scope.title) if scope.season is None
                   else lang(32471).format(scope.title, scope.season))
        try:
            drive_views(progress, done, lang(32485).format(scope.title),
                        lambda: show_progress_window(progress, heading, done),
                        xbmcgui.DialogProgressBG, _take_reopen)
        except Exception:
            # Without its window the refresh cannot be followed or stopped,
            # so it stops; it must also end before the lock is released.
            log.exception("Refresh window failed", event="refresh.view_error")
            result.setdefault("error", 32347)
        finally:
            if not done.is_set():
                progress.request_stop()
            worker.join()
        if "error" in result:
            notify(lang(result["error"]))
        else:
            outcome: RefreshOutcome = result["outcome"]
            if not outcome.aborted:
                notify(progress.toast(scope.title, outcome.stopped, outcome.could_not_finish))
    finally:
        home.clearProperty(_RUNNING_SHOW)
        home.clearProperty(_REOPEN)
        StructuredLogger.stop_scan_log()
        release_scan_lock()

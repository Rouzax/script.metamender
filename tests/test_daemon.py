"""Tests for the MetaMender service daemon helpers."""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from resources.lib.data.libraries import FIELD_TOGGLES, Library, LibraryConfig
from resources.lib.data.media_types import EPISODE, MOVIE, SEASON, SET, TVSHOW
from resources.lib.service import daemon


def test_select_trigger_scheduled_when_due():
    should, reason = daemon._select_trigger(
        now=1000.0, last_scan=0.0, interval_sec=100.0, has_pending_event=False
    )
    assert (should, reason) == (True, "scheduled")


def test_select_trigger_scheduled_wins_over_event_when_both_due():
    # A due scheduled (full) scan must not be masked by a same-tick event.
    should, reason = daemon._select_trigger(
        now=1000.0, last_scan=0.0, interval_sec=100.0, has_pending_event=True
    )
    assert (should, reason) == (True, "scheduled")


def test_select_trigger_library_event_when_not_due():
    should, reason = daemon._select_trigger(
        now=50.0, last_scan=0.0, interval_sec=100.0, has_pending_event=True
    )
    assert (should, reason) == (True, "library_event")


def test_select_trigger_idle_when_nothing_due():
    should, reason = daemon._select_trigger(
        now=50.0, last_scan=0.0, interval_sec=100.0, has_pending_event=False
    )
    assert should is False


def test_maybe_prune_orphans_full_scan_prunes_all_types():
    """A full scan with retention > 0 prunes every type."""
    tracker = MagicMock()
    tracker.prune_orphans.return_value = 2
    settings = {"orphan_retention_days": 30}

    daemon._maybe_prune_orphans(tracker, settings, targeted=False)

    expected_cutoff = (date.today() - timedelta(days=30)).isoformat()
    called = {
        c.args[0] for c in tracker.prune_orphans.call_args_list
    }
    assert called == {"movie", "tvshow", "season", "episode"}
    for c in tracker.prune_orphans.call_args_list:
        assert c.kwargs["cutoff"] == expected_cutoff


def test_maybe_prune_orphans_targeted_scan_never_prunes():
    """A targeted (library-event) scan must NEVER prune.

    A targeted scan only stamps last_seen on the few items it sees, so
    pruning then would delete everything else in the library.
    """
    tracker = MagicMock()
    settings = {"orphan_retention_days": 30}

    daemon._maybe_prune_orphans(tracker, settings, targeted=True)

    tracker.prune_orphans.assert_not_called()


def test_maybe_prune_orphans_retention_zero_never_prunes():
    """Retention of 0 means never prune."""
    tracker = MagicMock()
    settings = {"orphan_retention_days": 0}

    daemon._maybe_prune_orphans(tracker, settings, targeted=False)

    tracker.prune_orphans.assert_not_called()


def test_load_settings_has_general_keys_only(kodi_stubs, settings_mock):
    """_load_settings carries the general settings and the interface
    language; what a library does comes from the library."""
    with patch.object(daemon, "kodi_ui_language", return_value="nl-NL"):
        s = daemon._load_settings()

    assert isinstance(s["refetch_schedule"], str)
    assert s["ui_language"] == "nl-NL"
    for moved in ("rating_source", "text_source_order", "fallback_order", "enabled_fields",
                  "cert_country", "cert_prefix_override", "cert_replace_foreign",
                  "cert_not_found"):
        assert moved not in s
    for retired in ("plot_source", "default_source", "max_retries", "fallback_language",
                    "min_votes_movie", "min_votes_episode"):
        assert retired not in s


def _lib(path="/m/", rating_source=0, toggles=("ratings",), included=True, **kw):
    return Library(path=path, label=kw.pop("label", "L"), included=included,
                   language="en-US", fallback_language="", toggles=frozenset(toggles),
                   rating_source=rating_source, **kw)


def _prepare_libs(rating_source=0, toggles=("ratings",)):
    return [_lib(rating_source=rating_source, toggles=toggles)]


class _AbortMonitor:
    def __init__(self, aborted=False):
        self.aborted = aborted

    def abortRequested(self):
        return self.aborted


def test_prepare_imdb_ratings_passes_abort_on_shutdown_or_playback(kodi_stubs):
    monitor = _AbortMonitor()
    with patch.object(daemon.imdb_ratings, "prepare", return_value=True) as prep:
        assert daemon._prepare_imdb_ratings(_prepare_libs(), monitor) is True
    abort = prep.call_args[0][0]
    with patch.object(daemon.xbmc, "getCondVisibility", return_value=False):
        assert abort() is False
        monitor.aborted = True
        assert abort() is True
    monitor.aborted = False
    with patch.object(daemon.xbmc, "getCondVisibility", return_value=True):
        assert abort() is True


def test_prepare_imdb_ratings_skipped_when_imdb_not_consulted(kodi_stubs):
    with patch.object(daemon.imdb_ratings, "prepare") as prep:
        assert daemon._prepare_imdb_ratings(
            _prepare_libs(rating_source=2), _AbortMonitor()
        ) is False
        assert daemon._prepare_imdb_ratings(
            _prepare_libs(toggles=("plots",)), _AbortMonitor()
        ) is False
    prep.assert_not_called()


def test_prepare_imdb_ratings_exception_yields_not_ready(kodi_stubs):
    with patch.object(daemon.imdb_ratings, "prepare", side_effect=RuntimeError("boom")), \
            patch.object(daemon.log, "exception") as log_exc:
        assert daemon._prepare_imdb_ratings(_prepare_libs(), _AbortMonitor()) is False
    log_exc.assert_called_once()
    assert log_exc.call_args.kwargs["event"] == "service.imdb_prepare_failed"


def test_run_scan_passes_imdb_ready_to_every_pass(kodi_stubs):
    tracker = MagicMock()
    tracker.get_show_id_map.return_value = {}
    settings = {"tmdb_api_key": "k"}
    libraries = LibraryConfig([_lib("/m/"), _lib("/k/", included=False)])
    with patch.object(daemon, "_prepare_imdb_ratings", return_value=True) as prep, \
            patch.object(daemon, "run_scan", return_value={}) as scan:
        daemon._run_scan(tracker, libraries, settings, _AbortMonitor())
    prep.assert_called_once()
    assert [lib.path for lib in prep.call_args.args[0]] == ["/m/"]
    assert all(c.kwargs["libraries"] is libraries for c in scan.call_args_list)
    assert [c.kwargs["settings"]["imdb_ready"] for c in scan.call_args_list] == [True] * 3
    assert "imdb_ready" not in settings


def test_daemon_scans_seasons_between_shows_and_episodes(kodi_stubs):
    tracker = MagicMock()
    order = []

    def scan(**kw):
        order.append(kw["media_type"])
        return {}

    tracker.get_show_id_map.side_effect = lambda: order.append("show_id_map") or {7: "4607"}
    with patch.object(daemon, "_prepare_imdb_ratings", return_value=True), \
            patch.object(daemon, "run_scan", side_effect=scan) as run:
        stats = daemon._run_scan(tracker, LibraryConfig([_lib("/m/", toggles=("posters",))]),
                                 {"tmdb_api_key": "k"}, _AbortMonitor(), new_only=True)
    assert order == [MOVIE, TVSHOW, "show_id_map", SEASON, EPISODE]
    by_type = {c.kwargs["media_type"]: c.kwargs for c in run.call_args_list}
    assert by_type[SEASON]["show_id_map"] == {7: "4607"}
    assert by_type[SEASON]["new_only"] is True
    assert by_type[SEASON]["breaker"] is by_type[EPISODE]["breaker"]
    # One cache per cycle: the show pass's answers serve the season pass.
    for cache in ("fanart_cache", "show_cache"):
        assert by_type[SEASON][cache] is by_type[TVSHOW][cache]
        assert by_type[MOVIE][cache] is by_type[EPISODE][cache] is by_type[TVSHOW][cache]
    assert list(stats) == ["movie", "tvshow", "season", "episode"]


def test_daemon_runs_set_pass_after_movies(kodi_stubs):
    tracker = MagicMock()
    order = []

    def scan(**kw):
        order.append(kw["media_type"])
        return {}

    tracker.get_show_id_map.return_value = {}
    libraries = LibraryConfig([_lib("/m/", toggles=("posters", "movie_sets"))])
    with patch.object(daemon, "_prepare_imdb_ratings", return_value=True), \
            patch.object(daemon, "run_scan", side_effect=scan) as run:
        stats = daemon._run_scan(tracker, libraries, {"tmdb_api_key": "k"}, _AbortMonitor(),
                                 new_only=True)
    assert order == [MOVIE, SET, TVSHOW, SEASON, EPISODE]
    by_type = {c.kwargs["media_type"]: c.kwargs for c in run.call_args_list}
    assert by_type[SET]["new_only"] is True
    assert by_type[SET]["breaker"] is by_type[MOVIE]["breaker"]
    assert by_type[SET]["fanart_cache"] is by_type[MOVIE]["fanart_cache"]
    assert list(stats) == ["movie", "set", "tvshow", "season", "episode"]


def test_prune_includes_sets_only_when_their_pass_ran():
    tracker = MagicMock()
    tracker.prune_orphans.return_value = 0
    daemon._maybe_prune_orphans(tracker, {"orphan_retention_days": 30}, targeted=False,
                                scanned=["movie", "set", "tvshow", "episode"])
    assert [c.args[0] for c in tracker.prune_orphans.call_args_list] == [
        "movie", "tvshow", "episode", "set"]
    tracker.reset_mock()
    daemon._maybe_prune_orphans(tracker, {"orphan_retention_days": 30}, targeted=False)
    assert "set" not in [c.args[0] for c in tracker.prune_orphans.call_args_list]


def test_prune_includes_seasons():
    tracker = MagicMock()
    tracker.prune_orphans.return_value = 0
    daemon._maybe_prune_orphans(tracker, {"orphan_retention_days": 30}, targeted=False)
    assert "season" in [c.args[0] for c in tracker.prune_orphans.call_args_list]


def test_prune_keeps_the_rows_of_a_type_not_scanned():
    tracker = MagicMock()
    tracker.prune_orphans.return_value = 0
    daemon._maybe_prune_orphans(tracker, {"orphan_retention_days": 30}, targeted=False,
                                scanned=["movie", "tvshow", "episode"])
    assert [c.args[0] for c in tracker.prune_orphans.call_args_list] == [
        "movie", "tvshow", "episode"]


def test_no_season_listing_without_a_poster_library(kodi_stubs, tmp_path):
    from resources.lib.data.tracker import TrackerDB

    listed = []
    tracker = TrackerDB(str(tmp_path / "t.db"))
    try:
        with patch("resources.lib.data.kodi.get_all_items",
                   side_effect=lambda mt, **kw: listed.append(mt) or []), \
                patch("resources.lib.data.kodi.get_seasons_for_scan") as seasons:
            stats = daemon._run_scan(tracker, LibraryConfig([_lib("/m/")]),
                                     {"tmdb_api_key": "k", "ui_language": "en-US"},
                                     _AbortMonitor())
    finally:
        tracker.close()
    seasons.assert_not_called()
    assert listed == [MOVIE, TVSHOW, EPISODE]
    assert "season" not in stats


class _FakeMonitor:
    """Monitor stub that lets the service loop run a fixed number of ticks."""

    def __init__(self, ticks):
        self._ticks = ticks
        self.waits = 0

    def abortRequested(self):
        return self.waits >= self._ticks

    def waitForAbort(self, _seconds):
        self.waits += 1
        return False

    def has_pending_scan(self, _debounce):
        return False

    def clear_scan_request(self):
        pass


def _run_daemon_ticks(monkeypatch, scan_side_effect, ticks=5):
    """Drive daemon.run() for `ticks` iterations with a failing scan.

    Returns the mock standing in for _run_scan.
    """
    scan_mock = MagicMock(side_effect=scan_side_effect)
    monitor = _FakeMonitor(ticks)

    with patch.object(daemon, "MetaMendMonitor", return_value=monitor), \
            patch.object(daemon, "TrackerDB"), \
            patch.object(daemon, "_run_scan", scan_mock), \
            patch.object(daemon, "_load_settings",
                         return_value={"tmdb_api_key": "k",
                                       "resume_interrupted": True,
                                       "show_notifications": False,
                                       "orphan_retention_days": 30,
                                       "ui_language": "en-US"}), \
            patch.object(daemon, "_sync_libraries", return_value=LibraryConfig()), \
            patch.object(daemon, "acquire_scan_lock", return_value=True), \
            patch.object(daemon, "release_scan_lock"), \
            patch.object(daemon, "get_int_setting", return_value=24), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon.xbmc, "getCondVisibility", return_value=False):
        daemon.run()

    return scan_mock


def test_unexpected_scan_failure_backs_off(monkeypatch, kodi_stubs):
    """An unanticipated exception must arm the failure backoff.

    Without it the loop re-runs a full library scan every
    SERVICE_SLEEP_INTERVAL_SEC (1 second) for as long as the fault persists.
    """
    scan_mock = _run_daemon_ticks(monkeypatch, RuntimeError("boom"), ticks=5)

    assert scan_mock.call_count == 1


def test_kodi_query_failure_backs_off(monkeypatch, kodi_stubs):
    """The already-handled failure class stays throttled (regression guard)."""
    from resources.lib.data.kodi import KodiQueryError

    scan_mock = _run_daemon_ticks(monkeypatch, KodiQueryError("rpc"), ticks=5)

    assert scan_mock.call_count == 1


def _complete_stats(aborted=False, tmdb_answered=True, tvdb_answered=True,
                    fanart_answered=False):
    s = {"processed": 1, "skipped": 0, "updated": 1, "errors": 0,
         "fields": {}, "aborted": aborted,
         "tmdb_answered": tmdb_answered, "tvdb_answered": tvdb_answered,
         "fanart_answered": fanart_answered}
    return {"movie": s, "tvshow": dict(s), "episode": dict(s)}


def _run_one_cycle(all_stats, resume_interrupted=True):
    """Drive daemon.run() for one tick with a scan returning `all_stats`.

    Returns the TrackerDB instance mock so cycle calls can be asserted.
    """
    monitor = _FakeMonitor(1)
    tracker_instance = MagicMock()

    with patch.object(daemon, "MetaMendMonitor", return_value=monitor), \
            patch.object(daemon, "TrackerDB", return_value=tracker_instance), \
            patch.object(daemon, "_run_scan", return_value=all_stats), \
            patch.object(daemon, "_load_settings",
                         return_value={"tmdb_api_key": "k",
                                       "resume_interrupted": resume_interrupted,
                                       "show_notifications": False,
                                       "orphan_retention_days": 30,
                                       "ui_language": "en-US"}), \
            patch.object(daemon, "_sync_libraries", return_value=LibraryConfig()), \
            patch.object(daemon, "acquire_scan_lock", return_value=True), \
            patch.object(daemon, "release_scan_lock"), \
            patch.object(daemon, "get_int_setting", return_value=24), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon.xbmc, "getCondVisibility", return_value=False):
        daemon.run()

    return tracker_instance


def test_scan_opens_a_cycle_through_begin_cycle(kodi_stubs):
    """The daemon must not gate start_new_cycle on resume_interrupted alone:
    that leaves cycle_processed set forever and skips the whole library."""
    tracker = _run_one_cycle(_complete_stats())

    tracker.begin_cycle.assert_called_once_with(True)


def test_completed_scan_marks_cycle_complete(kodi_stubs):
    tracker = _run_one_cycle(_complete_stats(aborted=False))

    tracker.mark_cycle_complete.assert_called_once()


def test_aborted_scan_does_not_mark_cycle_complete(kodi_stubs):
    """An interrupted pass did not cover the library, so the cycle stays open
    and the resume watermark must not advance."""
    stats = _complete_stats()
    stats["episode"]["aborted"] = True

    tracker = _run_one_cycle(stats)

    tracker.mark_cycle_complete.assert_not_called()


def test_aborted_scan_does_not_prune_orphans(kodi_stubs):
    """prune_orphans assumes a full pass refreshed last_seen for the whole
    library; an aborted pass breaks that invariant."""
    stats = _complete_stats()
    stats["movie"]["aborted"] = True

    tracker = _run_one_cycle(stats)

    tracker.prune_orphans.assert_not_called()


def _notified_ids(all_stats):
    """Run one daemon cycle; return the string ids passed to notify."""
    notify = MagicMock()
    with patch.object(daemon, "notify", notify), \
            patch.object(daemon, "lang", side_effect=lambda i: "s{}".format(i)):
        _run_one_cycle(all_stats)
    return [c.args[0] for c in notify.call_args_list]


def test_no_tvdb_message_without_auth_failure(kodi_stubs):
    assert "s32352" not in _notified_ids(_complete_stats())


def test_tvdb_auth_failed_helper():
    assert daemon.tvdb_auth_failed([{"tvdb_auth_failed": False}, {"tvdb_auth_failed": True}])
    assert not daemon.tvdb_auth_failed([{}, {"updated": 1}])


def _run_daemon_locked(ticks=5):
    """Drive daemon.run() with the scan lock always held elsewhere.

    Returns (acquire mock, clear_file_lock mock).
    """
    acquire = MagicMock(return_value=False)
    clear = MagicMock()
    with patch.object(daemon, "MetaMendMonitor", return_value=_FakeMonitor(ticks)), \
            patch.object(daemon, "TrackerDB"), \
            patch.object(daemon, "_run_scan"), \
            patch.object(daemon, "_load_settings",
                         return_value={"tmdb_api_key": "k",
                                       "resume_interrupted": True,
                                       "show_notifications": False,
                                       "orphan_retention_days": 30,
                                       "ui_language": "en-US"}), \
            patch.object(daemon, "_sync_libraries", return_value=LibraryConfig()), \
            patch.object(daemon, "acquire_scan_lock", acquire), \
            patch.object(daemon, "release_scan_lock"), \
            patch.object(daemon, "clear_file_lock", clear), \
            patch.object(daemon, "get_int_setting", return_value=24), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon.xbmc, "getCondVisibility", return_value=False):
        daemon.run()
    return acquire, clear


def test_held_lock_is_retried_after_a_pause(kodi_stubs):
    """A scan running elsewhere is not polled every service tick."""
    acquire, _clear = _run_daemon_locked(ticks=5)

    assert acquire.call_count == 1


def test_service_start_clears_a_lock_file_left_by_a_killed_kodi(kodi_stubs):
    _acquire, clear = _run_daemon_locked(ticks=1)

    clear.assert_called_once_with()


def test_every_backfill_field_has_a_toggle():
    """A field no toggle enables is never in enabled_fields, so the scan
    never manages it. Votes are written with the rating."""
    toggled = frozenset().union(*FIELD_TOGGLES.values())
    for media_type in (MOVIE, TVSHOW, SEASON, EPISODE):
        # A movie or show title has its own toggle, apart from episode titles.
        own = {"title": "main_title"} if media_type is not EPISODE else {}
        needed = {own.get(f, f) for f in media_type.backfill_fields} - {"votes"}
        missing = needed - toggled
        assert not missing, (media_type.name, sorted(missing))


def test_certification_settings_shipped():
    import xml.etree.ElementTree as ET  # the repo's own settings.xml

    root = ET.parse("resources/settings.xml").getroot()
    ids = {s.get("id"): s for s in root.iter("setting")}
    assert "retry_days" not in ids and "enable_kijkwijzer" not in ids
    for sid in ("enable_fsk", "enable_bbfc", "enable_medieraadet"):
        assert ids[sid].findtext("default") == "true"
        assert ids[sid].findtext("visible") is None


def test_load_settings_cert_sites(kodi_stubs, settings_mock):
    values = {"enable_fsk": "true", "enable_bbfc": "false", "enable_medieraadet": "true"}
    settings_mock.side_effect = lambda sid: values.get(sid, "")
    assert daemon._load_settings()["cert_sites"] == frozenset({"fsk", "medieraadet"})


class _PendingMonitor(_FakeMonitor):
    """A monitor with a library update waiting on every tick."""

    def __init__(self, ticks):
        super().__init__(ticks)
        self.cleared = 0

    def has_pending_scan(self, _debounce):
        return True

    def clear_scan_request(self):
        self.cleared += 1


def _run_gated(setup_complete, ticks):
    monitor = _PendingMonitor(ticks)
    scan = MagicMock(return_value=_complete_stats())
    notify = MagicMock()
    with patch.object(daemon, "MetaMendMonitor", return_value=monitor), \
            patch.object(daemon, "TrackerDB"), \
            patch.object(daemon, "_run_scan", scan), \
            patch.object(daemon, "_load_settings",
                         return_value={"tmdb_api_key": "k",
                                       "resume_interrupted": True,
                                       "show_notifications": False,
                                       "orphan_retention_days": 30,
                                       "ui_language": "en-US"}), \
            patch.object(daemon, "_sync_libraries", return_value=LibraryConfig()), \
            patch.object(daemon, "acquire_scan_lock", return_value=True), \
            patch.object(daemon, "release_scan_lock"), \
            patch.object(daemon, "get_int_setting", return_value=24), \
            patch.object(daemon, "is_setup_complete", return_value=setup_complete), \
            patch.object(daemon, "notify", notify), \
            patch.object(daemon.xbmc, "getCondVisibility", return_value=False):
        daemon.run()
    return scan, notify, monitor


def test_no_scan_before_setup(kodi_stubs):
    scan, notify, monitor = _run_gated(False, ticks=4)
    assert scan.call_count == 0
    assert notify.call_count == 0  # the attention window says it instead
    assert monitor.cleared >= 1


def test_scans_once_setup_is_complete(kodi_stubs):
    scan, notify, _ = _run_gated(True, ticks=2)
    assert scan.call_count >= 1
    assert notify.call_count == 0


def test_setup_settings_shipped():
    import xml.etree.ElementTree as ET
    root = ET.parse("resources/settings.xml").getroot()
    by_id = {s.get("id"): s for s in root.iter("setting")}
    hidden = by_id["setup_complete"]
    assert hidden.get("type") == "boolean"
    assert hidden.findtext("level") == "4"
    assert hidden.findtext("default") == "false"
    assert by_id["configure_sources"].findtext("data") == "RunScript(script.metamender,libraries)"
    assert by_id["run_setup_wizard"].findtext("data") == "RunScript(script.metamender,wizard)"


def test_every_setting_has_a_control():
    # Kodi drops a setting without <control> ("missing <control> tag"): it is
    # not shown and getSetting returns "" instead of its default, so a
    # boolean that defaults to true reads as false on a fresh install.
    import xml.etree.ElementTree as ET
    root = ET.parse("resources/settings.xml").getroot()
    expected = {"boolean": "toggle", "string": "edit"}
    for setting in root.iter("setting"):
        control = setting.find("control")
        assert control is not None, setting.get("id")
        want = expected.get(setting.get("type", ""))
        if want:
            assert control.get("type") == want, setting.get("id")


def test_setup_buttons_close_the_settings_dialog():
    # Without it Kodi routes the wizard's setSetting calls into the still
    # open settings dialog instead of saving them.
    import xml.etree.ElementTree as ET
    root = ET.parse("resources/settings.xml").getroot()
    by_id = {s.get("id"): s for s in root.iter("setting")}
    for setting_id in ("run_setup_wizard", "configure_sources"):
        assert by_id[setting_id].find("control").findtext("close") == "true"


# --- quiet scans and the attention window -------------------------------------

import json  # noqa: E402

from resources.lib.service import attention  # noqa: E402
from resources.lib.utils import ApiKeyError  # noqa: E402


class _Service:
    """daemon.run() for a few ticks with the problem record kept in a dict,
    so several runs share it like the real tracker."""

    def __init__(self, store=None):
        self.store = {} if store is None else store
        self.notify = MagicMock()
        self.window = MagicMock(return_value="later")
        self.builtin = MagicMock()
        self.check_folders = MagicMock(return_value={})
        self.window_ticks = []
        # The hidden boolean settings the service reads and writes.
        self.settings: dict = {}
        # None: no sources.json at all, the libraries are an empty config.
        # A list: the real sources.json in addon_data, with these sources
        # discovered in Kodi.
        self.discovered = None
        # The personal fanart.tv key the scan settings carry.
        self.fanart_key = ""

    def run(self, ticks=2, scan=None, setup=True, key="k", notifications=False,
            playing=lambda svc: False, monitor=None, aborting=lambda: False):
        monitor = monitor or _FakeMonitor(ticks)
        self.monitor = monitor
        self.playing_now = False
        tracker = MagicMock()
        tracker.get_scan_state.side_effect = lambda k: self.store.get(k)
        tracker.set_scan_state.side_effect = lambda k, v: self.store.__setitem__(k, v)
        scan = scan or MagicMock(return_value=_complete_stats())

        def cond(_name):
            return playing(self)

        def window(main, others):
            self.window_ticks.append(monitor.waits)
            return self.window(main, others)

        real_abort = monitor.abortRequested
        monitor.abortRequested = lambda: real_abort() or aborting()
        with patch.object(daemon, "MetaMendMonitor", return_value=monitor), \
                patch.object(daemon, "TrackerDB", return_value=tracker), \
                patch.object(daemon, "_run_scan", scan), \
                patch.object(daemon, "_load_settings",
                             return_value={"tmdb_api_key": key,
                                           "resume_interrupted": True,
                                           "show_notifications": notifications,
                                           "orphan_retention_days": 30,
                                           "fanart_client_key": self.fanart_key,
                                           "ui_language": "en-US"}), \
                (patch.object(daemon, "_sync_libraries", return_value=LibraryConfig())
                 if self.discovered is None else
                 patch.object(daemon, "discover_folders",
                              side_effect=lambda: list(self.discovered))), \
                patch.object(daemon, "acquire_scan_lock", return_value=True), \
                patch.object(daemon, "release_scan_lock"), \
                patch.object(daemon, "get_int_setting", return_value=24), \
                patch.object(daemon, "is_setup_complete", return_value=setup), \
                patch.object(daemon, "get_bool_setting",
                             side_effect=lambda k: self.settings.get(k) is True), \
                patch.object(daemon, "set_bool_setting",
                             side_effect=self.settings.__setitem__), \
                patch.object(daemon, "notify", self.notify), \
                patch.object(daemon, "lang", side_effect=lambda i: "s{}".format(i)), \
                patch.object(daemon, "show_attention_window", side_effect=window), \
                patch.object(daemon, "_check_folders", self.check_folders), \
                patch.object(daemon.xbmc, "executebuiltin", self.builtin), \
                patch.object(daemon.xbmc, "getCondVisibility", side_effect=cond):
            daemon.run()
        return self

    @property
    def notified(self):
        return [c.args[0] for c in self.notify.call_args_list]

    def record(self):
        return attention.Attention.from_json(self.store.get("attention"))

    def streak(self):
        return json.loads(self.store["attention"])["streak"]["count"]


def _stats_updated(n):
    stats = _complete_stats()
    for s in stats.values():
        s["updated"] = 0
    stats["movie"]["updated"] = n
    return stats


def test_no_changes_scan_is_silent(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_stats_updated(0)),
                         notifications=True)
    assert svc.notified == []


def test_updates_toast_only_when_not_playing(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_stats_updated(3)),
                         notifications=True)
    assert svc.notified == ["s32349"]

    svc = _Service()

    def scan_then_play(*a, **kw):
        svc.playing_now = True
        return _stats_updated(3)

    svc.run(ticks=1, scan=MagicMock(side_effect=scan_then_play), notifications=True,
            playing=lambda s: s.playing_now)
    assert svc.notified == []


def test_updates_toast_follows_the_setting(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_stats_updated(3)),
                         notifications=False)
    assert svc.notified == []


def test_setup_incomplete_raises_setup_problem_not_a_toast(kodi_stubs):
    svc = _Service().run(ticks=4, setup=False)
    assert svc.notified == []
    assert svc.window.call_count == 1
    main, others = svc.window.call_args.args
    assert main.kind == attention.SETUP and others == []


def test_window_waits_for_playback_to_stop(kodi_stubs):
    svc = _Service().run(ticks=5, setup=False, playing=lambda s: s.monitor.waits < 2)
    assert svc.window.call_count == 1
    assert svc.window_ticks[0] >= 2


def test_open_setup_runs_the_wizard(kodi_stubs):
    svc = _Service()
    svc.window.return_value = "setup"
    svc.run(ticks=2, setup=False)
    svc.builtin.assert_called_once_with("RunScript(script.metamender,wizard)")


def test_no_key_raises_the_no_key_problem(kodi_stubs):
    svc = _Service().run(ticks=2, key="")
    assert svc.window.call_args.args[0].kind == attention.NO_KEY


def test_api_key_error_raises_key_rejected_and_completed_scan_clears_it(kodi_stubs):
    svc = _Service().run(ticks=2, scan=MagicMock(side_effect=ApiKeyError("tmdb")))
    assert "s32341" not in svc.notified
    assert svc.window.call_args.args[0].kind == attention.KEY_REJECTED
    assert svc.streak() == 0  # a rejected key is its own problem
    svc.run(ticks=1)
    assert svc.record().pending_window() is None
    assert attention.KEY_REJECTED not in json.loads(svc.store["attention"])["open"]


def test_failed_and_aborted_scans_extend_the_streak(kodi_stubs):
    stats = _complete_stats(aborted=True)
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=stats))
    assert svc.streak() == 1
    svc.run(ticks=1, scan=MagicMock(side_effect=RuntimeError("boom")))
    assert svc.streak() == 2
    svc.run(ticks=1)
    assert svc.streak() == 0


def test_shutdown_abort_does_not_count_as_failure(kodi_stubs):
    svc = _Service()
    state = {"down": False}

    def scan(*a, **kw):
        state["down"] = True
        return _complete_stats(aborted=True)

    svc.run(ticks=1, scan=MagicMock(side_effect=scan), aborting=lambda: state["down"])
    assert json.loads(svc.store.get("attention", '{"streak": {"count": 0}}'))["streak"]["count"] == 0


def test_tvdb_toast_once_across_cycles(kodi_stubs):
    auth = _complete_stats()
    auth["episode"]["tvdb_auth_failed"] = True
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=auth))
    assert svc.notified.count("s32352") == 1
    svc.run(ticks=1, scan=MagicMock(return_value=auth))
    assert svc.notified.count("s32352") == 1
    svc.run(ticks=1)
    svc.run(ticks=1, scan=MagicMock(return_value=auth))
    assert svc.notified.count("s32352") == 2


def test_folders_checked_after_full_scan_only(kodi_stubs):
    svc = _Service().run(ticks=2, monitor=_PendingMonitor(2))
    assert svc.check_folders.call_count == 1


def test_check_folders_asks_the_folders_in_use(kodi_stubs):
    config = LibraryConfig([_lib("smb://a/"), _lib("smb://b/", included=False)])
    with patch.object(daemon, "library_folders_in_use",
                      return_value=["smb://a/"]) as in_use, \
            patch.object(daemon.xbmcvfs, "exists", return_value=False):
        assert daemon._check_folders(config) == {"smb://a/": False}
    in_use.assert_called_once_with(config)


def test_a_cycle_that_never_asked_tmdb_keeps_the_key_problem(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(side_effect=ApiKeyError("tmdb")))
    svc.run(ticks=1, scan=MagicMock(return_value=_complete_stats(tmdb_answered=False)))
    assert attention.KEY_REJECTED in json.loads(svc.store["attention"])["open"]
    assert svc.window.call_count == 1  # not shown again


def test_tvdb_toast_not_repeated_after_a_cycle_without_tvdb(kodi_stubs):
    auth = _complete_stats()
    auth["episode"]["tvdb_auth_failed"] = True
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=auth))
    svc.run(ticks=1, scan=MagicMock(return_value=_complete_stats(tvdb_answered=False)))
    svc.run(ticks=1, scan=MagicMock(return_value=auth))
    assert svc.notified.count("s32352") == 1


def _fanart_rejected():
    stats = _complete_stats()
    stats["tvshow"]["fanart_auth_failed"] = True
    return stats


def test_fanart_auth_failed_helper():
    assert daemon.fanart_auth_failed([{"fanart_auth_failed": False}, {"fanart_auth_failed": True}])
    assert not daemon.fanart_auth_failed([{}, {"updated": 1}])


def test_fanart_toast_names_the_personal_key_when_one_is_set(kodi_stubs):
    svc = _Service()
    svc.fanart_key = "mine"
    svc.run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    assert svc.notified.count("s32742") == 1
    assert "s32743" not in svc.notified
    assert attention.FANART_AUTH in json.loads(svc.store["attention"])["open"]
    assert attention.FANART_AUTH not in attention.WINDOW_KINDS


def test_fanart_toast_names_the_add_on_key_when_none_is_set(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    assert svc.notified.count("s32743") == 1
    assert "s32742" not in svc.notified


def test_fanart_toast_once_per_problem(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    svc.run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    assert svc.notified.count("s32743") == 1


def test_fanart_problem_survives_a_cycle_that_never_asked_fanart(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    svc.run(ticks=1, scan=MagicMock(return_value=_complete_stats(fanart_answered=False)))
    assert attention.FANART_AUTH in json.loads(svc.store["attention"])["open"]
    svc.run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    assert svc.notified.count("s32743") == 1


def test_fanart_problem_clears_when_fanart_answers(kodi_stubs):
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    svc.run(ticks=1, scan=MagicMock(return_value=_complete_stats(fanart_answered=True)))
    assert attention.FANART_AUTH not in json.loads(svc.store["attention"])["open"]
    svc.run(ticks=1, scan=MagicMock(return_value=_fanart_rejected()))
    assert svc.notified.count("s32743") == 2


def test_a_failing_window_does_not_stop_the_service(kodi_stubs):
    svc = _Service()
    svc.window.side_effect = RuntimeError("skin could not load the window")
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=4, setup=False)
    assert svc.window.call_count == 1  # marked shown, not retried every second
    svc.window.side_effect = None
    svc.run(ticks=2, scan=scan)
    assert scan.call_count == 1



def test_window_shown_once_per_session_even_if_saving_fails(kodi_stubs):
    svc = _Service()

    class WritesFailAfterTheWindow(dict):
        def __setitem__(self, key, value):
            # From the window on, set_scan_state swallows an sqlite error.
            if not svc.window.called:
                super().__setitem__(key, value)

    svc.store = WritesFailAfterTheWindow()
    svc.run(ticks=4, setup=False)
    assert svc.window.call_count == 1


def test_no_window_while_kodi_is_quitting(kodi_stubs):
    calls = {"n": 0}

    def quitting_after_the_loop_check():
        # The loop's own check passes once; Kodi starts quitting right after.
        calls["n"] += 1
        return calls["n"] > 1

    svc = _Service().run(ticks=3, setup=False, aborting=quitting_after_the_loop_check)
    assert svc.window.call_count == 0


def test_query_failure_during_shutdown_does_not_count(kodi_stubs):
    from resources.lib.data.kodi import KodiQueryError
    svc = _Service()
    state = {"down": False}

    def scan(*a, **kw):
        state["down"] = True
        raise KodiQueryError("rpc gone")

    svc.run(ticks=1, scan=MagicMock(side_effect=scan), aborting=lambda: state["down"])
    assert json.loads(svc.store.get("attention", '{"streak": {"count": 0}}'))["streak"]["count"] == 0


def test_updated_text_singular_and_plural():
    with patch.object(daemon, "lang", side_effect=lambda i: {32349: "{} items updated",
                                                             32504: "1 item updated"}[i]):
        assert daemon.updated_text(1) == "1 item updated"
        assert daemon.updated_text(12) == "12 items updated"


# --- libraries: conversion, new libraries, items in no library ---------------

import pytest  # noqa: E402

from resources.lib.data.libraries import (  # noqa: E402
    add_new_libraries,
    load_libraries,
    save_libraries,
    scan_settings,
)
from resources.lib.data.upgrade import COPY_FILE_NAME  # noqa: E402
from resources.lib.engine.scan import settings_fingerprint  # noqa: E402

MOVIES = {"path": "smb://nas/Movies/", "label": "Movies"}
SERIES = {"path": "smb://nas/Series/", "label": "Series"}

# A 1.1 install: one folder with its own language, the rest on the default.
_OLD_SOURCES = {"default_language": "nl-NL", "fallback_language": "en-US",
                "sources": [{"path": "smb://nas/Movies/", "language": "de-DE"}]}
_OLD_SETTINGS = {"enable_plots": "true", "enable_ratings": "false",
                 "enable_certifications": "false", "rating_source": "1",
                 "country": "3", "replace_incorrect": "false"}


@pytest.fixture
def addon_data(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "_addon_data_file", lambda name: str(tmp_path / name))
    monkeypatch.setattr(daemon, "kodi_ui_language", lambda: "en-US")
    return tmp_path


def _write_v11(addon_data, sources=None, settings=None):
    (addon_data / "sources.json").write_text(json.dumps(sources or _OLD_SOURCES))
    values = _OLD_SETTINGS if settings is None else settings
    (addon_data / "settings.xml").write_text("<settings version=\"2\">{}</settings>".format(
        "".join('<setting id="{}">{}</setting>'.format(k, v) for k, v in values.items())))


def _libraries_file(addon_data):
    return str(addon_data / "sources.json")


def test_scan_cycle_passes_libraries_to_run_scan(kodi_stubs, addon_data):
    config = LibraryConfig([_lib("smb://nas/Movies/", label="Movies")])
    save_libraries(_libraries_file(addon_data), config)
    svc = _Service()
    svc.discovered = [MOVIES]
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=1, scan=scan)
    assert scan.call_args.args[1] == config


def test_conversion_runs_once_and_notifies(kodi_stubs, addon_data):
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = [MOVIES, SERIES]
    scan = MagicMock(return_value=_complete_stats())
    with patch.object(daemon, "convert", wraps=daemon.convert) as conv:
        svc.run(ticks=1, scan=scan)
        svc.run(ticks=1, scan=scan)
    assert conv.call_count == 1
    assert scan.call_count == 2
    assert json.loads((addon_data / "sources.json").read_text())["version"] == 2
    assert not (addon_data / COPY_FILE_NAME).exists()
    assert svc.window.call_count == 1
    assert svc.window.call_args.args[0].kind == attention.LIBRARIES_CONVERTED
    movies, series = scan.call_args.args[1].libraries
    assert (movies.included, movies.language, movies.toggles) == (True, "de-DE", {"plots"})
    # A library 1.1 had no entry for is included on the old default language.
    assert (series.included, series.language, series.fallback_language) == (
        True, "nl-NL", "en-US")


def test_conversion_deferred_when_discovery_empty(kodi_stubs, addon_data):
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = []
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=3, scan=scan)
    assert scan.call_count == 0
    assert "version" not in json.loads((addon_data / "sources.json").read_text())
    assert (addon_data / COPY_FILE_NAME).exists()
    assert svc.window.call_count == 0
    assert svc.store.get("attention") is None or svc.streak() == 0

    svc.discovered = [MOVIES]
    svc.run(ticks=1, scan=scan)
    assert scan.call_count == 1
    assert load_libraries(_libraries_file(addon_data)) is not None
    assert not (addon_data / COPY_FILE_NAME).exists()


def test_conversion_reads_the_copy_when_kodi_dropped_the_ids(kodi_stubs, addon_data):
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = []
    svc.run(ticks=1)
    (addon_data / "settings.xml").write_text("<settings version=\"2\"></settings>")
    svc.discovered = [MOVIES]
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=1, scan=scan)
    (movies,) = scan.call_args.args[1].libraries
    assert movies.toggles == {"plots"} and movies.rating_source == 1


def test_failed_save_keeps_the_copy_and_converts_again(kodi_stubs, addon_data):
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = [MOVIES]
    scan = MagicMock(return_value=_complete_stats())
    with patch.object(daemon, "save_libraries", return_value=False):
        svc.run(ticks=1, scan=scan)
    assert scan.call_count == 0
    assert (addon_data / COPY_FILE_NAME).exists()
    assert svc.window.call_count == 0
    svc.run(ticks=1, scan=scan)
    assert scan.call_count == 1
    assert not (addon_data / COPY_FILE_NAME).exists()


def test_fresh_install_without_setup_does_not_convert(kodi_stubs, addon_data):
    svc = _Service()
    svc.discovered = [MOVIES]
    scan = MagicMock(return_value=_complete_stats())
    with patch.object(daemon, "convert") as conv:
        svc.run(ticks=3, setup=False, scan=scan)
        with patch.object(daemon, "is_setup_complete", return_value=False):
            assert daemon._load_libraries(MagicMock()) is None
    conv.assert_not_called()
    scan.assert_not_called()
    assert not (addon_data / "sources.json").exists()
    assert not (addon_data / COPY_FILE_NAME).exists()
    assert svc.window.call_args.args[0].kind == attention.SETUP


def test_lost_settings_switch_nothing_on(kodi_stubs, addon_data):
    """Kodi writes every setting, defaults included, so a settings file
    without any moved id and no copy means Kodi dropped them: the 1.1
    defaults are not assumed, nothing is switched on, the languages stay."""
    _write_v11(addon_data, settings={})
    svc = _Service()
    svc.discovered = [MOVIES, SERIES]
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=1, scan=scan)
    movies, series = scan.call_args.args[1].libraries
    assert (movies.included, movies.toggles, movies.language) == (True, frozenset(), "de-DE")
    assert (series.included, series.toggles, series.language) == (True, frozenset(), "nl-NL")
    assert svc.window.call_args.args[0] == attention.Problem(
        attention.LIBRARIES_CONVERTED, attention.SETTINGS_UNREAD)
    assert svc.settings == {"libraries_converted": True}


def test_copy_made_at_service_start_before_any_gate(kodi_stubs, addon_data):
    """Kodi drops the moved ids when it next saves the add-on settings,
    which can come before the first scan cycle: the copy cannot wait for
    playback to stop or a key to be set."""
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = [MOVIES]
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=1, scan=scan, key="", playing=lambda svc: True)
    scan.assert_not_called()
    assert json.loads((addon_data / COPY_FILE_NAME).read_text()) == _OLD_SETTINGS


def test_no_copy_before_setup_or_after_conversion(kodi_stubs, addon_data):
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = [MOVIES]
    svc.run(ticks=1, setup=False)
    assert not (addon_data / COPY_FILE_NAME).exists()
    (addon_data / "sources.json").unlink()
    svc.settings["libraries_converted"] = True
    svc.run(ticks=1, key="")
    assert not (addon_data / COPY_FILE_NAME).exists()


def test_conversion_records_that_it_ran(kodi_stubs, addon_data):
    _write_v11(addon_data)
    svc = _Service()
    svc.discovered = [MOVIES]
    with patch.object(daemon, "save_libraries", return_value=False):
        svc.run(ticks=1)
    assert svc.settings == {}
    svc.run(ticks=1)
    assert svc.settings == {"libraries_converted": True}


def test_missing_file_after_conversion_starts_afresh(kodi_stubs, addon_data):
    """sources.json deleted after the libraries were converted: the 1.1
    values in Kodi's file are stale, so nothing is converted and every
    library is found again as new, not included."""
    _write_v11(addon_data)
    (addon_data / "sources.json").unlink()
    svc = _Service()
    svc.settings["libraries_converted"] = True
    svc.discovered = [MOVIES, SERIES]
    scan = MagicMock(return_value=_complete_stats())
    with patch.object(daemon, "convert") as conv:
        svc.run(ticks=1, scan=scan)
    conv.assert_not_called()
    stored = load_libraries(_libraries_file(addon_data))
    assert stored is not None
    assert [(lib.path, lib.included, lib.toggles) for lib in stored.libraries] == [
        (MOVIES["path"], False, frozenset()), (SERIES["path"], False, frozenset())]
    assert scan.call_args.args[1] == stored
    assert svc.window.call_args.args[0] == attention.Problem(
        attention.NEW_LIBRARY, "Movies, Series")
    assert not (addon_data / COPY_FILE_NAME).exists()


def test_discovery_error_starts_nothing_afresh(kodi_stubs, addon_data):
    with patch.object(daemon, "discover_folders", return_value=None), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon, "get_bool_setting", return_value=True):
        assert daemon._load_libraries(MagicMock()) is None
    assert not (addon_data / "sources.json").exists()


def test_discovery_error_defers_the_conversion(kodi_stubs, addon_data):
    _write_v11(addon_data)
    with patch.object(daemon, "discover_folders", return_value=None), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon, "convert") as conv:
        assert daemon._load_libraries(MagicMock()) is None
    conv.assert_not_called()
    assert "version" not in json.loads((addon_data / "sources.json").read_text())


def test_discovery_error_adds_no_library(kodi_stubs, addon_data):
    config = LibraryConfig([_lib("smb://nas/Movies/", label="Movies")])
    save_libraries(_libraries_file(addon_data), config)
    before = (addon_data / "sources.json").read_text()
    tracker = MagicMock()
    with patch.object(daemon, "discover_folders", return_value=None):
        assert daemon._sync_libraries(tracker, "en-US") == config
    assert (addon_data / "sources.json").read_text() == before
    tracker.set_scan_state.assert_not_called()


def test_damaged_libraries_file_is_never_converted_over(kodi_stubs, addon_data):
    _write_v11(addon_data)
    damaged = json.dumps({"version": 2, "libraries": [{"label": "no path"}]})
    (addon_data / "sources.json").write_text(damaged)
    svc = _Service()
    svc.discovered = [MOVIES]
    scan = MagicMock(return_value=_complete_stats())
    with patch.object(daemon, "convert") as conv, \
            patch.object(daemon, "log") as log:
        svc.run(ticks=3, scan=scan)
    conv.assert_not_called()
    scan.assert_not_called()
    assert (addon_data / "sources.json").read_text() == damaged
    assert [c.kwargs["event"] for c in log.error.call_args_list] == ["libraries.damaged"]
    assert svc.streak() == 1  # one attempt: retried after a pause, not every tick


def test_new_library_added_not_included_and_notified(kodi_stubs, addon_data):
    save_libraries(_libraries_file(addon_data),
                   LibraryConfig([_lib("smb://nas/Movies/", label="Movies")]))
    svc = _Service()
    svc.discovered = [MOVIES, SERIES]
    scan = MagicMock(return_value=_complete_stats())
    svc.run(ticks=1, scan=scan)
    stored = load_libraries(_libraries_file(addon_data))
    series = stored.by_path("smb://nas/Series/")
    assert series is not None and not series.included and series.toggles == frozenset()
    assert series.label == "Series" and series.language == "en-US"
    assert svc.window.call_args.args[0] == attention.Problem(attention.NEW_LIBRARY, "Series")
    # Known now: no second notice, and a source inside a library is not a
    # library of its own.
    svc.discovered = [MOVIES, SERIES, {"path": "smb://nas/Movies/Kids/", "label": "Kids"}]
    svc.run(ticks=1, scan=scan)
    assert svc.window.call_count == 1
    assert len(load_libraries(_libraries_file(addon_data)).libraries) == 2


def test_add_new_libraries_skips_known_paths_however_spelled():
    config = LibraryConfig([_lib("smb://nas/Movies/")])
    added = add_new_libraries(
        config, [{"path": "smb://nas/Movies", "label": "M"},
                 {"path": "smb://nas/TV/", "label": "TV"},
                 {"path": "smb://nas/TV/", "label": "TV again"}], "nl-NL")
    assert [(lib.path, lib.included, lib.language) for lib in added] == [
        ("smb://nas/TV/", False, "nl-NL")]
    assert config.libraries[-1] is added[0]


def test_unmatched_count_raises_notice(kodi_stubs):
    stats = _complete_stats()
    stats["movie"]["unmatched"] = 2
    stats["episode"]["unmatched"] = 3
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=stats))
    assert svc.window.call_args.args[0] == attention.Problem(attention.UNMATCHED, "5")
    svc.run(ticks=1)
    assert attention.UNMATCHED not in json.loads(svc.store["attention"])["open"]


def test_unmatched_not_judged_from_a_partial_scan(kodi_stubs):
    stats = _complete_stats()
    stats["movie"]["unmatched"] = 2
    svc = _Service().run(ticks=1, scan=MagicMock(return_value=stats))
    svc.run(ticks=1, scan=MagicMock(return_value=_complete_stats(aborted=True)))
    assert json.loads(svc.store["attention"])["open"][attention.UNMATCHED] == "2"


def test_open_libraries_answer_opens_the_library_list(kodi_stubs):
    stats = _complete_stats()
    stats["movie"]["unmatched"] = 1
    svc = _Service()
    svc.window.return_value = "libraries"
    svc.run(ticks=1, scan=MagicMock(return_value=stats))
    svc.builtin.assert_called_once_with("RunScript(script.metamender,libraries)")


@pytest.mark.parametrize("country, ui_language, code", [("3", "en-US", "DE"),
                                                        ("0", "nl-NL", "NL")])
def test_conversion_does_not_change_fingerprints(
        kodi_stubs, addon_data, monkeypatch, country, ui_language, code):
    """Every field's fingerprint under the converted library equals the one
    1.1 recorded, so the first scan after the upgrade re-fetches nothing."""
    monkeypatch.setattr(daemon, "kodi_ui_language", lambda: ui_language)
    _write_v11(addon_data, settings={
        "enable_plots": "true", "enable_ratings": "true", "enable_certifications": "true",
        "enable_main_titles": "true", "enable_details": "true", "rating_source": "1",
        "text_source_order": "1", "fallback_order": "1", "country": country,
        "override_prefix": "true", "rating_prefix": "FSK ", "replace_incorrect": "false",
        "enable_fallback": "true", "fallback_rating": "NR"})
    sites = frozenset({"fsk"})
    # The settings dict 1.1's _load_settings built from those values.
    old = {"rating_source": 1, "text_source_order": 1, "fallback_order": 1,
           "cert_country": code, "cert_prefix_override": "FSK ", "cert_sites": sites,
           "cert_replace_foreign": False, "cert_not_found": "NR"}
    with patch.object(daemon, "discover_folders", return_value=[MOVIES, SERIES]), \
            patch.object(daemon, "is_setup_complete", return_value=True):
        config = daemon._load_libraries(MagicMock())
    assert config is not None
    # 1.1 languages: the folder's own entry, else the default.
    for file, old_language in (("smb://nas/Movies/a.mkv", "de-DE"),
                               ("smb://nas/Series/Show/s01e01.mkv", "nl-NL")):
        lib = config.for_file(file)
        _included, language, fallback = config.folder_settings(file, lib)
        new = scan_settings(lib, {"cert_sites": sites}, ui_language)
        for media_type in (MOVIE, TVSHOW, EPISODE):
            for field in sorted(media_type.backfill_fields | {"mpaa"}):
                assert settings_fingerprint(field, new, language, media_type.name, fallback) \
                    == settings_fingerprint(field, old, old_language, media_type.name,
                                            "en-US"), (file, media_type.name, field)


def test_item_in_a_library_without_old_entry_is_scanned_after_conversion(
        kodi_stubs, addon_data, tmp_path):
    from resources.lib.data.nfo import NO_NFO
    from resources.lib.data.tracker import TrackerDB

    _write_v11(addon_data)
    with patch.object(daemon, "discover_folders", return_value=[MOVIES, SERIES]), \
            patch.object(daemon, "is_setup_complete", return_value=True):
        libraries = daemon._load_libraries(MagicMock())
    movie = {"movieid": 7, "title": "Docu", "plot": "", "rating": 0, "votes": "0",
             "uniqueid": {"tmdb": "42"}, "file": "smb://nas/Series/Docu/docu.mkv",
             "dateadded": "2026-01-01 12:00:00", "premiered": "2025-06-15"}
    tracker = TrackerDB(str(tmp_path / "t.db"))
    try:
        with patch("resources.lib.data.kodi.get_all_items",
                   side_effect=lambda mt, **kw: [movie] if mt is MOVIE else []), \
                patch("resources.lib.engine.scan.nfo_state", return_value=NO_NFO), \
                patch("resources.lib.providers.tmdb.resolve_id", return_value=None), \
                patch("resources.lib.providers.tmdb.fetch_movie",
                      return_value={"plot": "Ein Film", "original_title": "Docu"}), \
                patch("resources.lib.data.kodi.update_item", return_value=True) as update:
            stats = daemon._run_scan(tracker, libraries, {"tmdb_api_key": "k",
                                                          "rate_limit": 0.0,
                                                          "ui_language": "en-US"},
                                     _AbortMonitor())
    finally:
        tracker.close()
    assert stats["movie"]["unmatched"] == 0 and stats["movie"]["excluded"] == 0
    update.assert_called_once()
    assert update.call_args.kwargs == {"plot": "Ein Film"}


def test_prune_keeps_items_of_excluded_library(kodi_stubs, tmp_path):
    from resources.lib.data.identity import compute_identity
    from resources.lib.data.tracker import TrackerDB

    kept = {"movieid": 1, "title": "Kept", "uniqueid": {"tmdb": "1"},
            "file": "smb://nas/Kids/kept.mkv"}
    libraries = LibraryConfig([_lib("smb://nas/Kids/", included=False,
                                    toggles=("plots",))])
    tracker = TrackerDB(str(tmp_path / "t.db"))
    try:
        for identity in (compute_identity(kept, MOVIE), "gone"):
            tracker.upsert_item("movie", identity, kodi_id=1)
            tracker.update_last_seen("movie", identity, "2020-01-01")
        tracker.commit()
        with patch("resources.lib.data.kodi.get_all_items",
                   side_effect=lambda mt, **kw: [kept] if mt is MOVIE else []):
            stats = daemon._run_scan(tracker, libraries, {"tmdb_api_key": "k",
                                                          "ui_language": "en-US"},
                                     _AbortMonitor())
        tracker.commit()
        daemon._maybe_prune_orphans(tracker, {"orphan_retention_days": 30}, targeted=False)
        assert stats["movie"]["excluded"] == 1
        assert tracker.get_item("movie", compute_identity(kept, MOVIE)) is not None
        assert tracker.get_item("movie", "gone") is None
    finally:
        tracker.close()


def test_unwritable_copy_leaves_the_old_settings_and_waits(kodi_stubs, addon_data):
    _write_v11(addon_data)
    with patch.object(daemon, "ensure_copy", return_value=None), \
            patch.object(daemon, "discover_folders", return_value=[MOVIES]), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon, "convert") as conv:
        assert daemon._load_libraries(MagicMock()) is None
    conv.assert_not_called()
    assert "version" not in json.loads((addon_data / "sources.json").read_text())


def test_conversion_does_not_overwrite_one_saved_meanwhile(kodi_stubs, addon_data):
    """The other process writes version 2 while this one converts: its
    result stands and the copy this one made is removed."""
    _write_v11(addon_data)
    path = _libraries_file(addon_data)
    theirs = LibraryConfig([_lib("smb://nas/Movies/", label="Theirs")])
    real_convert = daemon.convert

    def convert_while_they_save(*args, **kwargs):
        result = real_convert(*args, **kwargs)
        save_libraries(path, theirs)
        return result

    with patch.object(daemon, "discover_folders", return_value=[MOVIES]), \
            patch.object(daemon, "convert", side_effect=convert_while_they_save), \
            patch.object(daemon, "save_libraries", wraps=daemon.save_libraries) as save:
        tracker = MagicMock()
        assert daemon._convert_libraries(tracker, path) == theirs
    save.assert_not_called()
    assert load_libraries(path) == theirs
    assert not (addon_data / COPY_FILE_NAME).exists()
    tracker.set_scan_state.assert_not_called()


def test_conversion_skipped_when_already_converted(kodi_stubs, addon_data):
    _write_v11(addon_data)
    path = _libraries_file(addon_data)
    theirs = LibraryConfig([_lib("smb://nas/Movies/", label="Theirs")])
    save_libraries(path, theirs)
    with patch.object(daemon, "convert") as conv:
        assert daemon._convert_libraries(MagicMock(), path) == theirs
    conv.assert_not_called()
    assert not (addon_data / COPY_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# Library-event (targeted) scans and the daily scan's schedule and cycle
# ---------------------------------------------------------------------------

_HOUR = 3600.0
_T0 = 1_800_000_000.0


class _ClockMonitor(_FakeMonitor):
    """One tick per entry of ``hours`` (hours after _T0); a library update
    is waiting on the ticks in ``events`` until a scan clears it."""

    def __init__(self, hours, events=()):
        super().__init__(len(hours))
        self.hours = hours
        self.events = set(events)
        self.cleared_at = set()

    def now(self):
        return _T0 + self.hours[min(self.waits, len(self.hours) - 1)] * _HOUR

    def has_pending_scan(self, _debounce):
        return self.waits in self.events and self.waits not in self.cleared_at

    def clear_scan_request(self):
        self.cleared_at.add(self.waits)


def _run_on_clock(db_path, hours, events=(), scan=None):
    """Drive daemon.run() over a real tracker at ``db_path``, one tick per
    entry of ``hours``. Returns the _run_scan mock."""
    import time as _time
    from types import SimpleNamespace

    from resources.lib.data.tracker import TrackerDB

    monitor = _ClockMonitor(hours, events)
    scan = scan or MagicMock(return_value=_complete_stats())
    clock = SimpleNamespace(time=monitor.now, perf_counter=_time.perf_counter,
                            strftime=_time.strftime)
    with patch.object(daemon, "MetaMendMonitor", return_value=monitor), \
            patch.object(daemon, "TrackerDB", lambda _p: TrackerDB(db_path)), \
            patch.object(daemon, "time", clock), \
            patch.object(daemon, "_run_scan", scan), \
            patch.object(daemon, "_load_settings",
                         return_value={"tmdb_api_key": "k",
                                       "resume_interrupted": True,
                                       "show_notifications": False,
                                       "orphan_retention_days": 30,
                                       "ui_language": "en-US"}), \
            patch.object(daemon, "_sync_libraries", return_value=LibraryConfig()), \
            patch.object(daemon, "_check_folders", return_value={}), \
            patch.object(daemon, "show_attention_window", return_value="later"), \
            patch.object(daemon, "acquire_scan_lock", return_value=True), \
            patch.object(daemon, "release_scan_lock"), \
            patch.object(daemon, "get_int_setting", return_value=24), \
            patch.object(daemon, "is_setup_complete", return_value=True), \
            patch.object(daemon.xbmc, "getCondVisibility", return_value=False):
        daemon.run()
    return scan


def _seed_cycle(db_path, complete, processed=("a", "b"), unprocessed=("c",)):
    """A tracker that a full scan has seen, its cycle open or complete."""
    from resources.lib.data.tracker import TrackerDB

    tracker = TrackerDB(db_path)
    for identity in (*processed, *unprocessed):
        tracker.upsert_item("movie", identity, kodi_id=len(identity))
    for identity in processed:
        tracker.mark_cycle_processed("movie", identity)
    tracker.set_scan_state("last_scan_completed", "2026-11-27 03:00:00")
    tracker.set_scan_state("cycle_complete", "1" if complete else "0")
    tracker.commit()
    tracker.close()


def _cycle_state(db_path):
    from resources.lib.data.tracker import TrackerDB

    tracker = TrackerDB(db_path)
    try:
        processed = sorted(
            r[0] for r in tracker._conn.execute(
                "SELECT identity FROM items WHERE cycle_processed = 1"))
        return tracker.get_scan_state("cycle_complete"), processed
    finally:
        tracker.close()


def _new_only_flags(scan):
    return [c.kwargs.get("new_only", False) for c in scan.call_args_list]


def test_targeted_scan_does_not_move_the_scheduled_scan(kodi_stubs, tmp_path):
    """Full scan at T, a library update at T+10 h: the daily scan still
    comes due at T+24 h, not at T+34 h."""
    db = str(tmp_path / "t.db")
    scan = _run_on_clock(db, hours=[0, 10, 23.9, 24], events={1})
    assert _new_only_flags(scan) == [False, True, False]


def test_targeted_scan_keeps_the_cycle_open(kodi_stubs, tmp_path):
    """An interrupted full cycle keeps its resume point through a library
    event scan: still open, its processed rows still processed."""
    db = str(tmp_path / "t.db")
    _seed_cycle(db, complete=False)
    # The first tick is the startup scan, stopped early again, so the cycle
    # stays open; the library update comes an hour later.
    aborted = _complete_stats(aborted=True)
    scan = MagicMock(side_effect=[aborted, _complete_stats()])
    _run_on_clock(db, hours=[0, 1], events={1}, scan=scan)
    assert _new_only_flags(scan) == [False, True]
    assert _cycle_state(db) == ("0", ["a", "b"])


def test_targeted_scan_does_not_complete_a_cycle(kodi_stubs, tmp_path):
    """After a completed full cycle a library event scan neither opens a new
    cycle (which clears every cycle_processed flag) nor closes one."""
    db = str(tmp_path / "t.db")
    _seed_cycle(db, complete=True)

    def full_pass(tracker, *_args, **_kwargs):
        # The startup scan is a full one: it processes every row, as a real
        # full pass does, and completes the cycle.
        for identity in ("a", "b", "c"):
            tracker.mark_cycle_processed("movie", identity)
        return _complete_stats()

    passes = iter([full_pass, lambda *_a, **_kw: _complete_stats()])
    scan = MagicMock(side_effect=lambda *a, **kw: next(passes)(*a, **kw))
    _run_on_clock(db, hours=[0, 1], events={1}, scan=scan)
    assert _new_only_flags(scan) == [False, True]
    assert _cycle_state(db) == ("1", ["a", "b", "c"])


def test_full_scan_still_resets_last_scan_and_completes_the_cycle(kodi_stubs, tmp_path):
    db = str(tmp_path / "t.db")
    _seed_cycle(db, complete=False)
    scan = _run_on_clock(db, hours=[0, 23.9, 24], events=())
    # Startup scan at T, nothing at T+23.9 h, the daily scan at T+24 h.
    assert _new_only_flags(scan) == [False, False]
    # The daily scan opened a fresh cycle and ran it to the end.
    assert _cycle_state(db) == ("1", [])

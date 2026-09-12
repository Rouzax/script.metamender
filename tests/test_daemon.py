"""Tests for the MetaMender service daemon helpers."""
from datetime import date, timedelta
from unittest.mock import MagicMock

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
    """A full scan (kodi_filter=None) with retention > 0 prunes every type."""
    tracker = MagicMock()
    tracker.prune_orphans.return_value = 2
    settings = {"orphan_retention_days": 30}

    daemon._maybe_prune_orphans(tracker, settings, kodi_filter=None)

    expected_cutoff = (date.today() - timedelta(days=30)).isoformat()
    called = {
        c.args[0] for c in tracker.prune_orphans.call_args_list
    }
    assert called == {"movie", "tvshow", "episode"}
    for c in tracker.prune_orphans.call_args_list:
        assert c.kwargs["cutoff"] == expected_cutoff


def test_maybe_prune_orphans_targeted_scan_never_prunes():
    """A targeted scan (kodi_filter set) must NEVER prune.

    A targeted scan only stamps last_seen on the few items it sees, so
    pruning then would delete everything else in the library.
    """
    tracker = MagicMock()
    settings = {"orphan_retention_days": 30}

    daemon._maybe_prune_orphans(
        tracker,
        settings,
        kodi_filter={"field": "dateadded", "operator": "after", "value": "x"},
    )

    tracker.prune_orphans.assert_not_called()


def test_maybe_prune_orphans_retention_zero_never_prunes():
    """Retention of 0 means never prune."""
    tracker = MagicMock()
    settings = {"orphan_retention_days": 0}

    daemon._maybe_prune_orphans(tracker, settings, kodi_filter=None)

    tracker.prune_orphans.assert_not_called()


def test_load_settings_has_default_source_and_schedule(kodi_stubs, settings_mock):
    """_load_settings must include default_source and refetch_schedule (raw string),
    and must NOT include min_votes_movie, min_votes_episode, or max_retries."""
    s = daemon._load_settings()

    assert "default_source" in s
    assert isinstance(s["default_source"], int)

    assert "refetch_schedule" in s
    assert isinstance(s["refetch_schedule"], str)

    assert "max_retries" not in s
    assert "min_votes_movie" not in s
    assert "min_votes_episode" not in s


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
    from unittest.mock import patch

    scan_mock = MagicMock(side_effect=scan_side_effect)
    monitor = _FakeMonitor(ticks)

    with patch.object(daemon, "MetaMendMonitor", return_value=monitor), \
            patch.object(daemon, "TrackerDB"), \
            patch.object(daemon, "_run_scan", scan_mock), \
            patch.object(daemon, "_load_settings",
                         return_value={"tmdb_api_key": "k",
                                       "resume_interrupted": True,
                                       "show_notifications": False,
                                       "orphan_retention_days": 30}), \
            patch.object(daemon, "load_source_config", return_value={}), \
            patch.object(daemon, "acquire_scan_lock", return_value=True), \
            patch.object(daemon, "release_scan_lock"), \
            patch.object(daemon, "get_int_setting", return_value=24), \
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

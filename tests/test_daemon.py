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

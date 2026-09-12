"""Tests for the manual scan entry point (resume + empty-key guard, A16)."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from resources.lib.service import manual


def _settings(**overrides: Any) -> Dict[str, Any]:
    """Build a complete-enough settings dict for manual.run()."""
    base: Dict[str, Any] = {
        "tmdb_api_key": "k",
        "rating_source": 0,
        "plot_source": 0,
        "enabled_fields": frozenset({"plot"}),
        "min_votes_movie": 50,
        "min_votes_episode": 20,
        "stale_threshold_days": 365,
        "max_retries": 3,
        "rate_limit": 0.25,
        "scan_interval": 24,
        "show_notifications": False,
        "resume_interrupted": False,
    }
    base.update(overrides)
    return base


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Dict[str, MagicMock]:
    """Patch everything manual.run() touches so it can run in isolation."""
    tracker = MagicMock()
    tracker_cls = MagicMock(return_value=tracker)
    run_scan = MagicMock(return_value={"updated": 0})

    monkeypatch.setattr(manual, "TrackerDB", tracker_cls)
    monkeypatch.setattr(manual, "run_scan", run_scan)
    tracker.get_show_id_map = MagicMock(return_value={})
    monkeypatch.setattr(manual, "load_source_config", MagicMock(return_value={}))
    monkeypatch.setattr(manual, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(
        manual, "_get_sources_config_path", MagicMock(return_value="sources.json")
    )
    monkeypatch.setattr(manual, "notify", MagicMock())
    monkeypatch.setattr(manual, "acquire_scan_lock", MagicMock(return_value=True))
    monkeypatch.setattr(manual, "release_scan_lock", MagicMock())
    # Logger init reads Kodi settings; neutralize it.
    monkeypatch.setattr(manual.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(manual.StructuredLogger, "start_scan_log", MagicMock())
    monkeypatch.setattr(manual.StructuredLogger, "stop_scan_log", MagicMock())
    monkeypatch.setattr(manual, "get_bool_setting", MagicMock(return_value=False))

    return {
        "tracker": tracker,
        "tracker_cls": tracker_cls,
        "run_scan": run_scan,
    }


def test_manual_skips_when_no_api_key(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    monkeypatch.setattr(
        manual, "_load_settings", MagicMock(return_value=_settings(tmdb_api_key=""))
    )
    warnings = []
    monkeypatch.setattr(
        manual.log,
        "warning",
        lambda *a, **k: warnings.append((a, k)),
    )

    manual.run()

    patched["run_scan"].assert_not_called()
    patched["tracker_cls"].assert_not_called()
    assert any(k.get("event") == "manual.skip_no_key" for _a, k in warnings)


def test_manual_resume_true_opens_cycle_with_resume(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    """resume_interrupted is passed to begin_cycle, which decides whether the
    previous cycle is resumable. It must not gate cycle creation outright: that
    left cycle_processed set forever and skipped the whole library."""
    monkeypatch.setattr(
        manual,
        "_load_settings",
        MagicMock(return_value=_settings(resume_interrupted=True)),
    )

    manual.run()

    patched["tracker"].begin_cycle.assert_called_once_with(True)
    assert patched["run_scan"].called


def test_manual_resume_false_opens_fresh_cycle(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    monkeypatch.setattr(
        manual,
        "_load_settings",
        MagicMock(return_value=_settings(resume_interrupted=False)),
    )

    manual.run()

    patched["tracker"].begin_cycle.assert_called_once_with(False)
    assert patched["run_scan"].called


def test_manual_completed_scan_marks_cycle_complete(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    monkeypatch.setattr(
        manual,
        "_load_settings",
        MagicMock(return_value=_settings(resume_interrupted=True)),
    )

    manual.run()

    patched["tracker"].mark_cycle_complete.assert_called_once()


def test_manual_aborted_scan_leaves_cycle_open(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    monkeypatch.setattr(
        manual,
        "_load_settings",
        MagicMock(return_value=_settings(resume_interrupted=True)),
    )
    patched["run_scan"].return_value = {
        "processed": 1, "skipped": 0, "updated": 0, "errors": 0,
        "fields": {}, "aborted": True,
    }

    manual.run()

    patched["tracker"].mark_cycle_complete.assert_not_called()

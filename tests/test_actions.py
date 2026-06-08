"""Tests for entry-point action handlers (reset_exhausted, A20/D12)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resources.lib.service import actions


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch):
    """Patch everything actions.reset_exhausted() touches."""
    tracker = MagicMock()
    tracker.reset_exhausted_fields = MagicMock(return_value=3)
    tracker_cls = MagicMock(return_value=tracker)
    notify = MagicMock()

    monkeypatch.setattr(actions, "TrackerDB", tracker_cls)
    monkeypatch.setattr(actions, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(actions, "notify", notify)
    monkeypatch.setattr(actions.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(actions, "get_bool_setting", MagicMock(return_value=False))

    return {"tracker": tracker, "tracker_cls": tracker_cls, "notify": notify}


def test_reset_exhausted_calls_tracker_and_notifies(patched):
    actions.reset_exhausted()
    patched["tracker"].reset_exhausted_fields.assert_called_once_with()
    patched["notify"].assert_called_once()
    message = patched["notify"].call_args[0][0]
    assert "3" in message
    patched["tracker"].close.assert_called_once_with()


def test_reset_exhausted_closes_tracker_on_error(patched):
    patched["tracker"].reset_exhausted_fields.side_effect = RuntimeError("boom")
    actions.reset_exhausted()  # should not raise
    patched["tracker"].close.assert_called_once_with()
    patched["notify"].assert_not_called()

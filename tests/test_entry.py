"""Tests for the add-on's script entry point and the setup launcher."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import default
from resources.lib.service import setup


@pytest.fixture
def entry(monkeypatch: pytest.MonkeyPatch):
    mocks = {
        "open_setup": MagicMock(return_value=True),
        "run": MagicMock(),
        "update_now": MagicMock(),
        "reset_tracker": MagicMock(),
        "is_setup_complete": MagicMock(return_value=True),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(default, name, mock)
    return mocks


def test_no_argument_scans_when_set_up(entry):
    default.main([])
    entry["run"].assert_called_once()
    entry["open_setup"].assert_not_called()


def test_no_argument_opens_wizard_before_setup(entry):
    entry["is_setup_complete"].return_value = False
    default.main([])
    entry["open_setup"].assert_called_once_with("wizard")
    entry["run"].assert_not_called()


@pytest.mark.parametrize("mode", ["wizard", "folders"])
def test_setup_actions(entry, mode):
    default.main([mode])
    entry["open_setup"].assert_called_once_with(mode)
    entry["run"].assert_not_called()


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch):
    mocks = {
        "run_setup": MagicMock(return_value=True),
        "manual_run": MagicMock(),
        "is_setup_complete": MagicMock(return_value=False),
    }
    monkeypatch.setattr(setup, "run_setup", mocks["run_setup"])
    monkeypatch.setattr(setup.manual, "run", mocks["manual_run"])
    monkeypatch.setattr(setup, "is_setup_complete", mocks["is_setup_complete"])
    monkeypatch.setattr(setup, "_get_sources_config_path", MagicMock(return_value="s.json"))
    return mocks


def test_first_setup_starts_one_scan(launcher):
    assert setup.open_setup("wizard") is True
    launcher["run_setup"].assert_called_once_with("wizard", "s.json")
    launcher["manual_run"].assert_called_once()


def test_rerun_starts_no_scan(launcher):
    launcher["is_setup_complete"].return_value = True
    setup.open_setup("wizard")
    launcher["manual_run"].assert_not_called()


def test_cancelled_first_setup_starts_no_scan(launcher):
    launcher["run_setup"].return_value = False
    assert setup.open_setup("wizard") is False
    launcher["manual_run"].assert_not_called()


def test_folder_dialog_starts_no_scan(launcher):
    setup.open_setup("folders")
    launcher["manual_run"].assert_not_called()

"""Tests for the add-on's script entry point and the setup launcher."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resources.lib.data.libraries import LibraryConfig
from resources.lib.service import entry as default
from resources.lib.service import wizard as setup


@pytest.fixture
def entry(monkeypatch: pytest.MonkeyPatch):
    mocks = {
        "open_setup": MagicMock(return_value=True),
        "run": MagicMock(),
        "update_now": MagicMock(),
        "reset_tracker": MagicMock(),
        "open_details": MagicMock(),
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


@pytest.mark.parametrize("arg,mode", [("wizard", "wizard"), ("libraries", "libraries"),
                                      ("folders", "libraries")])
def test_setup_actions(entry, arg, mode):
    default.main([arg])
    entry["open_setup"].assert_called_once_with(mode)
    entry["run"].assert_not_called()


def test_details_action_opens_the_details_window(entry):
    default.main(["details", "movie", "7"])
    entry["open_details"].assert_called_once_with("movie", "7")
    entry["run"].assert_not_called()


@pytest.fixture
def launcher(monkeypatch: pytest.MonkeyPatch):
    mocks = {
        "run_setup": MagicMock(return_value=True),
        "manual_run": MagicMock(),
        "is_setup_complete": MagicMock(return_value=False),
        "libraries_seen": MagicMock(),
        "convert_pending": MagicMock(return_value=False),
    }
    monkeypatch.setattr(setup, "_convert_pending", mocks["convert_pending"])
    monkeypatch.setattr(setup, "_libraries_seen", mocks["libraries_seen"])
    monkeypatch.setattr(setup, "run_setup", mocks["run_setup"])
    monkeypatch.setattr(setup.manual, "run", mocks["manual_run"])
    monkeypatch.setattr(setup, "is_setup_complete", mocks["is_setup_complete"])
    monkeypatch.setattr(setup, "_get_sources_config_path", MagicMock(return_value="s.json"))
    return mocks


def test_first_setup_starts_one_scan(launcher):
    assert setup.open_setup("wizard") is True
    launcher["run_setup"].assert_called_once_with("wizard", "s.json", None)
    launcher["convert_pending"].assert_called_once_with("s.json", True)
    launcher["manual_run"].assert_called_once()


def test_first_setup_after_a_conversion_answers_its_notice(launcher):
    launcher["convert_pending"].return_value = True
    setup.open_setup("wizard")
    launcher["run_setup"].assert_called_once_with("wizard", "s.json",
                                                  launcher["libraries_seen"])


def test_rerun_starts_no_scan(launcher):
    launcher["is_setup_complete"].return_value = True
    setup.open_setup("wizard")
    launcher["manual_run"].assert_not_called()


def test_cancelled_first_setup_starts_no_scan(launcher):
    launcher["run_setup"].return_value = False
    assert setup.open_setup("wizard") is False
    launcher["manual_run"].assert_not_called()


def test_libraries_dialog_starts_no_scan(launcher):
    setup.open_setup("libraries")
    launcher["manual_run"].assert_not_called()


@pytest.mark.parametrize("mode", ["wizard", "libraries"])
def test_the_library_list_clears_their_notices(launcher, mode):
    launcher["is_setup_complete"].return_value = True
    setup.open_setup(mode)
    launcher["run_setup"].assert_called_once_with(mode, "s.json", launcher["libraries_seen"])
    launcher["convert_pending"].assert_called_once_with("s.json", False)


def test_first_setup_has_no_notices_to_clear(launcher):
    setup.open_setup("wizard")
    assert launcher["run_setup"].call_args.args[2] is None


def test_libraries_seen_clears_the_notices(monkeypatch, tmp_path):
    from resources.lib.data.tracker import TrackerDB
    from resources.lib.service import attention

    db = str(tmp_path / "t.db")
    tracker = TrackerDB(db)
    attention.update(tracker, lambda a: a.add_new_libraries(["Kids"]))
    attention.update(tracker, lambda a: a.raise_problem(attention.LIBRARIES_CONVERTED))
    tracker.close()
    monkeypatch.setattr(setup, "_get_db_path", lambda: db)
    setup._libraries_seen()
    tracker = TrackerDB(db)
    try:
        assert attention.load(tracker).pending_window() is None
    finally:
        tracker.close()


@pytest.mark.parametrize("content,first,converts", [
    ('{"sources": []}', False, True),
    ('{"sources": []}', True, False),
    ('{"default_language": "de-DE", "sources": [{"path": "/m/", "language": "nl-NL"}]}',
     True, True),
    ('{"version": 2, "libraries": []}', False, False),
    ('{"version": 2, "libraries": []}', True, False),
    (None, False, False),
    (None, True, False),
])
def test_convert_pending_only_for_a_1_1_file(monkeypatch, tmp_path, content, first,
                                             converts):
    """Before the first setup only a 1.1 file with folder entries is worth
    converting; after it, any 1.1 file is."""
    path = tmp_path / "sources.json"
    if content is not None:
        path.write_text(content)
    convert = MagicMock(return_value=LibraryConfig())
    monkeypatch.setattr(setup, "_convert_libraries", convert)
    monkeypatch.setattr(setup, "_get_db_path", lambda: str(tmp_path / "t.db"))
    assert setup._convert_pending(str(path), first) is converts
    assert convert.called is converts


def test_convert_pending_deferred_reports_no_conversion(monkeypatch, tmp_path):
    path = tmp_path / "sources.json"
    path.write_text('{"sources": [{"path": "/m/", "language": "nl-NL"}]}')
    monkeypatch.setattr(setup, "_convert_libraries", MagicMock(return_value=None))
    monkeypatch.setattr(setup, "_get_db_path", lambda: str(tmp_path / "t.db"))
    assert setup._convert_pending(str(path), True) is False


def test_first_setup_converts_a_1_1_file_with_folder_languages(monkeypatch, tmp_path):
    """Real conversion on the first run: the folder's language survives."""
    from resources.lib.data.libraries import load_libraries
    from resources.lib.service import daemon

    path = tmp_path / "sources.json"
    path.write_text('{"default_language": "de-DE", "sources": '
                    '[{"path": "/m/", "language": "nl-NL"}]}')
    monkeypatch.setattr(daemon, "_addon_data_file", lambda name: str(tmp_path / name))
    monkeypatch.setattr(daemon, "kodi_ui_language", lambda: "en-US")
    monkeypatch.setattr(daemon, "discover_folders",
                        lambda: [{"path": "/m/", "label": "M"}, {"path": "/s/", "label": "S"}])
    monkeypatch.setattr(setup, "_get_db_path", lambda: str(tmp_path / "t.db"))
    assert setup._convert_pending(str(path), True) is True
    config = load_libraries(str(path))
    assert config is not None
    assert [(lib.path, lib.language) for lib in config.libraries] == [
        ("/m/", "nl-NL"), ("/s/", "de-DE")]

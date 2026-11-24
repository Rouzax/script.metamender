"""Tests for the manual scan entry point (resume + empty-key guard, A16)."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from resources.lib.data.libraries import Library, LibraryConfig
from resources.lib.data.media_types import EPISODE, MOVIE, SEASON, TVSHOW
from resources.lib.service import manual

_LIBRARIES = LibraryConfig([
    Library(path="smb://nas/Movies/", label="Movies", included=True, language="en-US",
            fallback_language="", toggles=frozenset({"ratings"}), rating_source=0),
    Library(path="smb://nas/Kids/", label="Kids", included=False, language="en-US",
            fallback_language="", toggles=frozenset({"ratings"}), rating_source=0),
])


def _settings(**overrides: Any) -> Dict[str, Any]:
    """Build a complete-enough settings dict for manual.run()."""
    base: Dict[str, Any] = {
        "tmdb_api_key": "k",
        "ui_language": "en-US",
        "stale_threshold_days": 365,
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
    sync = MagicMock(return_value=_LIBRARIES)
    monkeypatch.setattr(manual, "_sync_libraries", sync)
    monkeypatch.setattr(manual, "_prepare_imdb_ratings", MagicMock(return_value=False))
    monkeypatch.setattr(manual, "_get_db_path", MagicMock(return_value=":memory:"))
    monkeypatch.setattr(manual, "notify", MagicMock())
    monkeypatch.setattr(manual, "acquire_scan_lock", MagicMock(return_value=True))
    monkeypatch.setattr(manual, "release_scan_lock", MagicMock())
    # Logger init reads Kodi settings; neutralize it.
    monkeypatch.setattr(manual.StructuredLogger, "initialize", MagicMock())
    monkeypatch.setattr(manual.StructuredLogger, "start_scan_log", MagicMock())
    monkeypatch.setattr(manual.StructuredLogger, "stop_scan_log", MagicMock())
    monkeypatch.setattr(manual, "get_bool_setting", MagicMock(return_value=False))
    monkeypatch.setattr(manual, "is_setup_complete", MagicMock(return_value=True))

    return {
        "tracker": tracker,
        "tracker_cls": tracker_cls,
        "run_scan": run_scan,
        "sync": sync,
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


def test_manual_tvdb_auth_failure_notified_once(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    monkeypatch.setattr(manual, "lang", lambda i: "s{}".format(i))
    patched["run_scan"].return_value = {"updated": 0, "tvdb_auth_failed": True}

    manual.run()

    notified = [c.args[0] for c in manual.notify.call_args_list]
    assert notified.count("s32352") == 1
    assert "s32341" not in notified


@pytest.mark.parametrize("key, text", [("mine", "s32742"), ("", "s32743")])
def test_manual_fanart_auth_failure_names_the_key_in_use(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock], key: str, text: str
) -> None:
    monkeypatch.setattr(
        manual, "_load_settings", MagicMock(return_value=_settings(fanart_client_key=key)))
    monkeypatch.setattr(manual, "lang", lambda i: "s{}".format(i))
    patched["run_scan"].return_value = {"updated": 0, "fanart_auth_failed": True}

    manual.run()

    notified = [c.args[0] for c in manual.notify.call_args_list]
    assert notified.count(text) == 1


def test_manual_does_not_scan_before_setup(patched, monkeypatch):
    monkeypatch.setattr(manual, "is_setup_complete", MagicMock(return_value=False))
    manual.run()
    patched["run_scan"].assert_not_called()
    manual.notify.assert_called_once()


def _scan_state_store(tracker):
    """Back the tracker mock's scan_state with a dict, as the real table."""
    store = {}
    tracker.get_scan_state.side_effect = store.get
    tracker.set_scan_state.side_effect = store.__setitem__
    return store


def _open_problems(store):
    import json
    return set(json.loads(store.get("attention", "{}")).get("open", {}))


def test_manual_rejected_key_is_recorded(monkeypatch, patched):
    from resources.lib.utils import ApiKeyError
    store = _scan_state_store(patched["tracker"])
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    monkeypatch.setattr(manual, "lang", lambda i: "s{}".format(i))
    patched["run_scan"].side_effect = ApiKeyError("tmdb")
    manual.run()
    assert "key_rejected" in _open_problems(store)
    assert "s32341" in [c.args[0] for c in manual.notify.call_args_list]


def test_manual_working_key_clears_the_problem(monkeypatch, patched):
    store = _scan_state_store(patched["tracker"])
    store["attention"] = '{"open": {"key_rejected": ""}}'
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    monkeypatch.setattr(manual, "lang", lambda i: "s{}".format(i))
    manual.run()
    assert "key_rejected" in _open_problems(store)  # TMDB was never asked
    patched["run_scan"].return_value = {"updated": 0, "tmdb_answered": True}
    manual.run()
    assert "key_rejected" not in _open_problems(store)


def test_manual_scans_with_the_libraries(monkeypatch, patched):
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    manual.run()
    patched["sync"].assert_called_once_with(patched["tracker"], "en-US")
    calls = patched["run_scan"].call_args_list
    assert [c.kwargs["libraries"] for c in calls] == [_LIBRARIES] * 3
    # No library manages posters: no season pass.
    assert [c.kwargs["media_type"] for c in calls] == [MOVIE, TVSHOW, EPISODE]
    prepared = manual._prepare_imdb_ratings.call_args.args[0]
    assert [lib.path for lib in prepared] == ["smb://nas/Movies/"]


def test_manual_scans_seasons_between_shows_and_episodes(monkeypatch, patched):
    libraries = LibraryConfig([Library(
        path="smb://nas/Series/", label="Series", included=True, language="en-US",
        fallback_language="", toggles=frozenset({"posters"}))])
    patched["sync"].return_value = libraries
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    manual.run()
    calls = patched["run_scan"].call_args_list
    # Seasons after the shows, whose ids they use, and before the episodes.
    assert [c.kwargs["media_type"] for c in calls] == [MOVIE, TVSHOW, SEASON, EPISODE]
    # One cache per cycle: a show's answers serve its season posters.
    assert len({id(c.kwargs["fanart_cache"]) for c in calls}) == 1
    assert len({id(c.kwargs["show_cache"]) for c in calls}) == 1


def test_manual_without_libraries_scans_nothing(monkeypatch, patched):
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    patched["sync"].return_value = None
    manual.run()
    patched["run_scan"].assert_not_called()
    patched["tracker"].begin_cycle.assert_not_called()
    manual.release_scan_lock.assert_called_once_with()



@pytest.fixture
def stop_window(monkeypatch: pytest.MonkeyPatch) -> Any:
    from resources.lib.constants import STOP_SCAN_PROPERTY  # noqa: F401
    from resources.lib.service import stopscan
    from tests.test_stop_scan import _Window
    win = _Window()
    monkeypatch.setattr(stopscan.xbmcgui, "Window", lambda _id: win)
    return win


def test_manual_scan_ends_after_a_stop_and_leaves_the_cycle_open(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock], stop_window: Any
) -> None:
    from resources.lib.constants import STOP_SCAN_PROPERTY
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    # The request lands after the movie pass's last per-item check.
    def scan_pass(**kw):
        stop_window.setProperty(STOP_SCAN_PROPERTY, "1")
        return {"updated": 0}
    patched["run_scan"].side_effect = scan_pass

    manual.run()

    assert [c.kwargs["media_type"] for c in patched["run_scan"].call_args_list] == [MOVIE]
    patched["tracker"].get_show_id_map.assert_not_called()
    patched["tracker"].mark_cycle_complete.assert_not_called()


def test_manual_scan_clears_a_stale_stop_when_it_starts_and_ends(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock], stop_window: Any
) -> None:
    from resources.lib.constants import STOP_SCAN_PROPERTY
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))
    stop_window.setProperty(STOP_SCAN_PROPERTY, "1")
    seen = []
    def scan_pass(**kw):
        seen.append(stop_window.getProperty(STOP_SCAN_PROPERTY))
        if kw["media_type"] is TVSHOW:
            stop_window.setProperty(STOP_SCAN_PROPERTY, "1")
        return {"updated": 0}
    patched["run_scan"].side_effect = scan_pass

    manual.run()

    assert seen == ["", ""]
    assert stop_window.getProperty(STOP_SCAN_PROPERTY) == ""


def test_manual_scan_checks_local_art_now(
    monkeypatch: pytest.MonkeyPatch, patched: Dict[str, MagicMock]
) -> None:
    """Opening MetaMender checks every local art file at once, so deleted
    files are filled without waiting for the weekly check."""
    monkeypatch.setattr(manual, "_load_settings", MagicMock(return_value=_settings()))

    manual.run()

    calls = patched["run_scan"].call_args_list
    assert calls
    assert all(c.kwargs["settings"]["check_local_art_now"] is True for c in calls)

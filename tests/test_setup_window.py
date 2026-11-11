"""Tests for the setup wizard controller (the window itself needs Kodi)."""
from __future__ import annotations

from typing import Any

import pytest

from resources.lib.providers.tmdb import KEY_OK, KEY_REFUSED, KEY_UNCHECKED
from resources.lib.ui.setup_model import FEATURES, initial_state
from resources.lib.ui.setup_window import SetupController

_MP = "multipath://%2fa%2f/%2fb%2f/"
_DISCOVERED = [{"path": _MP, "label": "Kids"}, {"path": "/c/", "label": "Films"}]


class FakeDialogs:
    def __init__(self, inputs=(), selects=()):
        self.inputs = list(inputs)
        self.selects = list(selects)
        self.oks: list[str] = []

    def input(self, heading: str, default: str) -> str:
        self.defaults = getattr(self, "defaults", []) + [default]
        return self.inputs.pop(0) if self.inputs else ""

    def select(self, heading: str, options: list[str], preselect: int) -> int:
        self.options = options
        return self.selects.pop(0) if self.selects else -1

    def ok(self, heading: str, message: str) -> None:
        self.oks.append(message)


class Recorder:
    def __init__(self, save_ok: bool = True, key: str = KEY_OK):
        self.saved: list[dict[str, Any]] = []
        self.written: list[dict[str, Any]] = []
        self.checked: list[str] = []
        self._save_ok = save_ok
        self._key = key

    def save(self, path: str, config: dict[str, Any]) -> bool:
        self.saved.append(config)
        return self._save_ok

    def write(self, values: dict[str, Any]) -> None:
        self.written.append(values)

    def check(self, key: str) -> str:
        self.checked.append(key)
        return self._key


def _controller(mode="wizard", key="", dialogs=None, rec=None, first_run=True,
                certifications=True):
    settings: dict[str, Any] = {f: False for f in FEATURES}
    settings.update({"enable_ratings": True, "enable_certifications": certifications,
                     "tmdb_api_key": key, "country": 0})
    state = initial_state(settings, {"sources": []}, _DISCOVERED, "nl-NL")
    rec = rec or Recorder()
    c = SetupController(
        mode, state, {"sources": []}, _DISCOVERED, "nl-NL", "/x/sources.json",
        first_run, dialogs=dialogs or FakeDialogs(), key_checker=rec.check,
        save_config=rec.save, settings_writer=rec.write, name=lambda code: code,
    )
    return c, rec


def _to_summary(c):
    while c.step != "summary":
        assert c.on_next() is False


def test_nothing_written_until_finish():
    dialogs = FakeDialogs(inputs=["abc"], selects=[3])
    c, rec = _controller(dialogs=dialogs)
    assert c.on_next() is False           # key entered and checked
    c.activate(0)                         # language picked
    c.on_next()
    c.activate(1)                         # folder Films: select cancelled
    _to_summary(c)
    assert rec.saved == [] and rec.written == []
    assert c.finished is False


def test_refused_key_stays_on_key_step():
    dialogs = FakeDialogs(inputs=["bad"])
    c, rec = _controller(dialogs=dialogs, rec=Recorder(key=KEY_REFUSED))
    assert c.on_next() is False
    assert c.step == "key" and len(dialogs.oks) == 1
    assert c.state.key_status == ""


def test_unchecked_key_warns_and_advances():
    dialogs = FakeDialogs(inputs=["abc"])
    c, rec = _controller(dialogs=dialogs, rec=Recorder(key=KEY_UNCHECKED))
    c.on_next()
    assert c.step == "language" and len(dialogs.oks) == 1
    assert c.state.key_status == KEY_UNCHECKED


def test_stored_key_is_not_rechecked_until_edited():
    dialogs = FakeDialogs(inputs=["new"])
    c, rec = _controller(key="old", dialogs=dialogs)
    c.on_next()
    assert rec.checked == [] and c.step == "language"
    c.on_back()
    c.activate(0)
    c.on_next()
    assert rec.checked == ["new"]


def test_finish_saves_sources_then_settings():
    c, rec = _controller(key="abc")
    _to_summary(c)
    assert c.on_next() is True
    assert len(rec.saved) == 1 and rec.saved[0]["default_language"] == "nl-NL"
    assert rec.written[0]["setup_complete"] is True
    assert c.finished is True


def test_save_failure_writes_no_settings():
    dialogs = FakeDialogs()
    c, rec = _controller(key="abc", dialogs=dialogs, rec=Recorder(save_ok=False))
    _to_summary(c)
    assert c.on_next() is False
    assert rec.written == [] and c.finished is False
    assert len(dialogs.oks) == 1


def test_folders_mode_saves_only_sources():
    dialogs = FakeDialogs(selects=[2, 0])
    c, rec = _controller(mode="folders", key="abc", dialogs=dialogs)
    c.activate(0)
    assert c.state.exceptions[_MP][1] == ""
    assert c.on_next() is True
    assert len(rec.saved) == 1 and rec.written == []


def test_folder_toggle_removes_exception():
    dialogs = FakeDialogs(selects=[2, 0])
    c, _ = _controller(mode="folders", dialogs=dialogs)
    c.activate(1)
    assert "/c/" in c.state.exceptions and c.rows()[1].chosen
    c.activate(1)
    assert "/c/" not in c.state.exceptions and not c.rows()[1].chosen


def test_fallback_none_is_first_option():
    dialogs = FakeDialogs(selects=[0])
    c, _ = _controller(key="abc", dialogs=dialogs)
    c.on_next()
    c.state.fallback_language = "de-DE"
    c.activate(1)
    assert c.state.fallback_language == ""


def test_back_on_first_step_returns_false():
    c, _ = _controller()
    assert c.on_back() is False and not c.has_back()


def test_feature_rows_toggle():
    c, _ = _controller(key="abc")
    while c.step != "features":
        c.on_next()
    before = c.state.features["enable_details"]
    c.activate(FEATURES.index("enable_details"))
    assert c.state.features["enable_details"] is (not before)


@pytest.mark.parametrize("certifications,expected", [(True, 6), (False, 5)])
def test_step_count_follows_certifications(certifications, expected):
    c, _ = _controller(key="abc", certifications=certifications)
    assert c.flow.total(c.state) == expected


def test_stored_key_is_never_prefilled_and_empty_entry_keeps_it():
    dialogs = FakeDialogs(inputs=[""])
    c, _ = _controller(key="secret1234", dialogs=dialogs)
    c.activate(0)
    assert dialogs.defaults == [""]
    assert c.state.api_key == "secret1234" and c.state.key_status == KEY_OK


def test_country_without_preset_explains_on_next():
    dialogs = FakeDialogs()
    c, _ = _controller(key="abc", dialogs=dialogs)
    c._ui_language = "xx-ZZ"
    c.flow = type(c.flow)("wizard", "xx-ZZ")
    while c.step != "country":
        c.on_next()
    assert c.on_next() is False
    assert c.step == "country" and len(dialogs.oks) == 1

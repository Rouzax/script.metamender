"""The window colour themes and the setting that picks one."""
import pytest

from resources.lib.ui import attention_window, progress_window, setup_window, theme
from resources.lib.ui.setup_window import GOLDEN_HOUR

KEYS = {"MM.Accent", "MM.ButtonFocus", "MM.ButtonTextFocused"}


def _pick(monkeypatch, index):
    monkeypatch.setattr(theme, "get_int_setting", lambda _id, default=0: index)


def test_every_theme_defines_the_three_argb_colours():
    assert sorted(theme.THEMES) == [0, 1, 2, 3]
    for colours in theme.THEMES.values():
        assert set(colours) == KEYS
        for value in colours.values():
            assert len(value) == 8 and value.startswith("FF")
            int(value, 16)


def test_golden_hour_is_theme_zero():
    assert theme.THEMES[0] == GOLDEN_HOUR


def test_theme_colours_follow_the_setting(monkeypatch):
    _pick(monkeypatch, 2)
    assert theme.theme_colours()["MM.Accent"] == "FFF87171"


@pytest.mark.parametrize("value", [-1, 4, 99])
def test_unknown_theme_falls_back_to_golden_hour(monkeypatch, value):
    _pick(monkeypatch, value)
    assert theme.theme_colours() == GOLDEN_HOUR


def test_theme_colours_returns_a_copy(monkeypatch):
    _pick(monkeypatch, 0)
    theme.theme_colours()["MM.Accent"] = "x"
    assert theme.THEMES[0]["MM.Accent"] == "FFF5A623"


def test_setup_window_uses_the_chosen_theme(monkeypatch):
    monkeypatch.setattr(setup_window, "theme_colours", lambda: theme.THEMES[2])
    c = type("C", (), {
        "mode": "x", "heading": lambda s: "", "explanation": lambda s: "",
        "next_label": lambda s: "", "has_back": lambda s: False,
        "marker_kind": lambda s: ""})()
    monkeypatch.setattr(setup_window, "lang", lambda i: "")
    assert setup_window.window_properties(c)["MM.Accent"] == "FFF87171"


def test_attention_window_uses_the_chosen_theme(monkeypatch):
    from resources.lib.service.attention import SETUP, Problem
    monkeypatch.setattr(attention_window, "theme_colours", lambda: theme.THEMES[2])
    monkeypatch.setattr(attention_window, "lang", lambda i: "")
    props = attention_window.attention_properties(Problem(SETUP, ""), [])
    assert props["MM.Accent"] == "FFF87171"


def test_progress_window_has_no_hardcoded_palette():
    assert not hasattr(progress_window, "GOLDEN_HOUR")
    assert progress_window.theme_colours is theme.theme_colours

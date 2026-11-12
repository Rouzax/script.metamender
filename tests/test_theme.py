"""The window colour themes and the setting that picks one."""
import pytest

from resources.lib.service.refresh import RefreshProgress
from resources.lib.ui import attention_window, progress_window, setup_window, theme

GOLDEN_HOUR = theme.THEMES[0]

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


class _StepController:
    """The parts of a SetupController window_properties reads."""

    mode = "settings"

    def heading(self):
        return ""

    def explanation(self):
        return ""

    def next_label(self):
        return ""

    def has_back(self):
        return False

    def marker_kind(self):
        return ""


def test_setup_window_uses_the_chosen_theme(monkeypatch):
    monkeypatch.setattr(setup_window, "theme_colours", lambda: theme.THEMES[2])
    monkeypatch.setattr(setup_window, "lang", lambda i: "")
    props = setup_window.window_properties(_StepController())  # type: ignore[arg-type]
    assert props["MM.Accent"] == "FFF87171"


def test_attention_window_uses_the_chosen_theme(monkeypatch):
    from resources.lib.service.attention import SETUP, Problem
    monkeypatch.setattr(attention_window, "theme_colours", lambda: theme.THEMES[2])
    monkeypatch.setattr(attention_window, "lang", lambda i: "")
    props = attention_window.attention_properties(Problem(SETUP, ""), [])
    assert props["MM.Accent"] == "FFF87171"


def test_progress_window_uses_the_chosen_theme(monkeypatch):
    _pick(monkeypatch, 3)
    monkeypatch.setattr(progress_window, "lang", lambda i: "{}")
    props = progress_window.progress_properties(RefreshProgress(), "Show", 0.0)
    assert props["MM.Accent"] == theme.THEMES[3]["MM.Accent"]


def test_theme_setting_has_its_own_appearance_group():
    import re
    import xml.etree.ElementTree as ET  # the repo's own settings.xml
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent
    root = ET.parse(root_dir / "resources/settings.xml").getroot()
    group = next(g for g in root.iter("group") if g.get("id") == "appearance")
    assert [s.get("id") for s in group.iter("setting")] == ["theme"]
    assert group.find("setting/control") is not None
    po = (root_dir / "resources/language/resource.language.en_gb/strings.po").read_text()
    assert re.search(r'msgctxt "#{}"\nmsgid "Appearance"'.format(group.get("label")), po)

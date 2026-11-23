"""The window colour themes and the setting that picks one."""
import pytest

from resources.lib.ui import theme

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


def test_every_window_publishes_the_theme_before_it_opens():
    # A window module that opens a window without publishing first shows the
    # colours of whatever theme the last window published, or none at all.
    from pathlib import Path

    ui = Path(__file__).resolve().parent.parent / "resources/lib/ui"
    modules = sorted(ui.glob("*_window.py"))
    assert modules
    for path in modules:
        lines = [line.strip() for line in path.read_text().splitlines()]
        opens = [i for i, line in enumerate(lines) if line == "window.doModal()"]
        assert opens, path.name
        for i in opens:
            assert lines[i - 1] == "publish_theme()", path.name


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


class _Home:
    def __init__(self) -> None:
        self.props: dict[str, str] = {}

    def setProperty(self, name: str, value: str) -> None:
        self.props[name] = value


def test_publish_theme_sets_the_chosen_colours_on_the_home_window(monkeypatch):
    _pick(monkeypatch, 2)
    home = _Home()
    opened = []
    monkeypatch.setattr(theme.xbmcgui, "Window",
                        lambda window_id: opened.append(window_id) or home)
    theme.publish_theme()
    assert opened == [10000]
    assert home.props == {"MetaMender.Accent": "FFF87171",
                          "MetaMender.ButtonFocus": "FFEF4444",
                          "MetaMender.ButtonTextFocused": "FFFFFFFF"}


def test_skin_colours_never_read_the_topmost_window():
    # Kodi reads a colour's $INFO without a window context, so an unqualified
    # Window.Property() comes from whichever dialog is on top: every library
    # write redraws the Videos window behind ours, and for that frame the
    # accent is empty. Colours name the Home window instead.
    import re
    from pathlib import Path

    skins = Path(__file__).resolve().parent.parent / "resources/skins/Default/1080i"
    colour = re.compile(
        r'(?:colordiffuse="[^"]*"|<(textcolor|focusedcolor|selectedcolor|disabledcolor|'
        r'shadowcolor|invalidcolor|colordiffuse)>[^<]*<)')
    files = sorted(skins.glob("*.xml"))
    assert files
    themed = 0
    for path in files:
        for match in colour.finditer(path.read_text()):
            text = match.group(0)
            assert "Window.Property(" not in text, (path.name, text)
            themed += "Window(Home).Property(MetaMender." in text
    assert themed

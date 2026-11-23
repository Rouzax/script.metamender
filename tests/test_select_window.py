"""Tests for ui/select_window.py: MetaMender's own picker."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from resources.lib.ui import select_window
from resources.lib.ui.select_window import (
    MAX_ROWS,
    ROW_HEIGHT,
    select_items,
    select_layout,
    select_properties,
)
from resources.lib.ui.theme import THEMES

_ROOT = Path(__file__).resolve().parent.parent
_SKIN = _ROOT / "resources/skins/Default/1080i/script-metamender-select.xml"
_PO = (_ROOT / "resources/language/resource.language.en_gb/strings.po").read_text()
_STRINGS = {int(k): v for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', _PO)}


@pytest.fixture(autouse=True)
def strings(monkeypatch):
    monkeypatch.setattr(select_window, "lang", lambda i: _STRINGS.get(i, ""))


def test_layout_fits_the_options_up_to_a_scrolling_maximum():
    two = select_layout(2)
    assert two.rows == 2 and two.list_height == 2 * ROW_HEIGHT
    many = select_layout(40)
    assert many.rows == MAX_ROWS and many.list_height == MAX_ROWS * ROW_HEIGHT
    assert select_layout(0).rows == 1


def test_layout_is_centred_and_grows_with_the_list():
    two, seven = select_layout(2), select_layout(7)
    assert seven.panel_height - two.panel_height == 5 * ROW_HEIGHT
    for layout in (two, seven):
        assert layout.top * 2 + layout.panel_height in (1080, 1079)
        assert layout.top > 0


def test_choices_mark_the_current_option():
    items = select_items(["a", "b", "c"], None, 1, marked=True)
    assert [(i.label, i.chosen, i.marker) for i in items] == [
        ("a", False, "radio"), ("b", True, "radio"), ("c", False, "radio")]
    assert all(i.detail == "" for i in items)


def test_actions_show_no_marker():
    items = select_items(["Change settings", "Leave out"], None, 0, marked=False)
    assert [(i.chosen, i.marker) for i in items] == [(False, ""), (False, "")]


def test_sub_lines_go_with_their_option():
    items = select_items(["a", "b"], ["sub a", "sub b"], 0, marked=True)
    assert [(i.label, i.detail) for i in items] == [("a", "sub a"), ("b", "sub b")]


def test_properties_carry_heading_and_cancel():
    props = select_properties("Language")
    assert props["MM.Heading"] == "Language"
    assert props["MM.Cancel"] == _STRINGS[32408]
    assert not set(THEMES[0]) & set(props)  # theme colours live on the Home window


def test_skin_has_the_list_cancel_and_both_markers():
    xml = _SKIN.read_text(encoding="utf-8")
    assert '<control type="list" id="%d">' % select_window.LIST in xml
    assert '<control type="button" id="%d">' % select_window.CANCEL in xml
    assert "<showonepage>false</showonepage>" in xml
    for group in (select_window.PANEL, select_window.PANEL_IMAGE, select_window.FRAME_IMAGE):
        assert 'id="%d"' % group in xml
    for layout in ("itemlayout", "focusedlayout"):
        assert '<%s width="%d" height="%d">' % (layout, select_window.LIST_WIDTH, ROW_HEIGHT) in xml
    assert xml.count("String.IsEqual(ListItem.Property(marker),radio)") == 4
    # Kodi resolves no include or named colour from a script's skin folder.
    assert "<include" not in xml
    assert not re.search(r"<font>(?!font1[023]<)", xml)

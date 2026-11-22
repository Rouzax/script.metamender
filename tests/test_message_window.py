"""Tests for ui/message_window.py: MetaMender's own questions and messages."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from resources.lib.ui import message_window
from resources.lib.ui.message_window import (
    LINE_HEIGHT,
    MAX_LINES,
    message_layout,
    message_lines,
    message_properties,
)
from resources.lib.ui.theme import THEMES

_ROOT = Path(__file__).resolve().parent.parent
_SKIN = _ROOT / "resources/skins/Default/1080i/script-metamender-message.xml"
_KODI = {106: "No", 107: "Yes", 186: "OK"}


@pytest.fixture(autouse=True)
def kodi_strings(monkeypatch):
    monkeypatch.setattr(message_window, "kodi_lang", lambda i: _KODI[i])


def test_short_message_is_one_line():
    assert message_lines("Save your changes?") == 1


def test_long_message_wraps_and_each_line_break_counts():
    text = " ".join(["word"] * 60)
    assert message_lines(text) > 2
    assert message_lines("one\ntwo\nthree") == 3


def test_lines_are_capped():
    assert message_lines("x\n" * 40) == MAX_LINES


def test_layout_grows_with_the_text_and_stays_centred():
    one, four = message_layout(1), message_layout(4)
    assert four.text_height - one.text_height == 3 * LINE_HEIGHT
    assert four.panel_height - one.panel_height == 3 * LINE_HEIGHT
    for layout in (one, four):
        assert layout.top * 2 + layout.panel_height in (1080, 1079)
        assert layout.buttons_top + 72 < layout.panel_height


def test_question_has_no_and_yes():
    props = message_properties("Libraries", "Save your changes?", question=True)
    assert props["MM.Heading"] == "Libraries"
    assert props["MM.Message"] == "Save your changes?"
    assert (props["MM.Primary"], props["MM.Secondary"]) == ("Yes", "No")
    assert THEMES[0].items() <= props.items()


def test_message_has_only_ok():
    props = message_properties("TMDB API key", "Refused.", question=False)
    assert (props["MM.Primary"], props["MM.Secondary"]) == ("OK", "")


def test_skin_has_the_message_and_both_buttons():
    xml = _SKIN.read_text(encoding="utf-8")
    for control in (message_window.PANEL, message_window.PANEL_IMAGE,
                    message_window.FRAME_IMAGE, message_window.MESSAGE,
                    message_window.PRIMARY, message_window.SECONDARY):
        assert 'id="%d"' % control in xml
    assert "<wrapmultiline>true</wrapmultiline>" in xml
    assert "<include" not in xml
    assert not re.search(r"<font>(?!font1[023]<)", xml)

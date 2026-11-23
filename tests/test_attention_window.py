"""Tests for ui/attention_window.py: what the attention window shows."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from resources.lib.service.attention import (
    FOLDER_OFFLINE,
    KEY_REJECTED,
    LIBRARIES_CONVERTED,
    NEW_LIBRARY,
    NO_KEY,
    SETTINGS_UNREAD,
    SETUP,
    UNMATCHED,
    WINDOW_KINDS,
    Problem,
)
from resources.lib.ui import attention_window
from resources.lib.ui.attention_window import (
    ALSO_TEXT,
    FULL_TEXT,
    attention_properties,
)
from resources.lib.ui.theme import THEMES

GOLDEN_HOUR = THEMES[0]

_ROOT = Path(__file__).resolve().parent.parent
_PO = (_ROOT / "resources/language/resource.language.en_gb/strings.po").read_text()
_STRINGS = {int(k): v for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', _PO)}


@pytest.fixture(autouse=True)
def strings(monkeypatch):
    monkeypatch.setattr(attention_window, "lang", lambda i: _STRINGS.get(i, ""))


def test_main_problem_with_fix_and_an_also_line():
    props = attention_properties(Problem(KEY_REJECTED, ""), [Problem(FOLDER_OFFLINE, "smb://nas/")])
    assert props["MM.Problem"] == _STRINGS[FULL_TEXT[KEY_REJECTED]]
    assert props["MM.Also"] == "Also: A folder is unreachable: smb://nas/"
    assert props["MM.Action"] == "Open setup"
    assert props["MM.Heading"] == "MetaMender needs you"
    assert props["MM.Later"] == "Later"


def test_offline_folder_has_no_fix_button_and_names_the_folder():
    props = attention_properties(Problem(FOLDER_OFFLINE, "smb://nas/Series/"), [])
    assert props["MM.Action"] == ""
    assert props["MM.Also"] == ""
    assert "smb://nas/Series/" in props["MM.Problem"]


def test_several_others_one_line_each():
    props = attention_properties(Problem(SETUP, ""), [Problem(NO_KEY, ""), Problem(KEY_REJECTED, "")])
    assert props["MM.Also"].split("\n") == ["Also: No TMDB key", "Also: The TMDB key was rejected"]


def test_every_window_kind_has_both_texts():
    for kind in WINDOW_KINDS:
        assert _STRINGS.get(FULL_TEXT[kind]), kind
        assert _STRINGS.get(ALSO_TEXT[kind]), kind


def test_accent_colours_are_not_window_properties():
    props = attention_properties(Problem(SETUP, ""), [])
    assert not set(GOLDEN_HOUR) & set(props)  # theme colours live on the Home window


def test_window_xml_reads_every_property():
    xml = (_ROOT / "resources/skins/Default/1080i/script-metamender-attention.xml").read_text()
    for key in attention_properties(Problem(SETUP, ""), []):
        assert "Window.Property(%s)" % key in xml, key


def test_new_kinds_offer_open_libraries():
    for kind, detail in ((NEW_LIBRARY, "Kids, Sport"), (UNMATCHED, "4"),
                         (LIBRARIES_CONVERTED, "")):
        props = attention_properties(Problem(kind, detail), [])
        assert props["MM.Action"] == "Open Libraries", kind
        assert detail in props["MM.Problem"], kind
        assert attention_window.fix_for(kind) == "libraries"
    assert attention_window.fix_for(SETUP) == "setup"
    assert attention_window.fix_for(FOLDER_OFFLINE) == ""



@pytest.mark.parametrize("problem,full,short", [
    (Problem(NEW_LIBRARY, "Kids"),
     "MetaMender found a new library, Kids. It stays out until you include it.",
     "New library found: Kids"),
    (Problem(NEW_LIBRARY, "Kids, Sport"),
     "MetaMender found new libraries: Kids, Sport. They stay out until you include them.",
     "New libraries found: Kids, Sport"),
    (Problem(UNMATCHED, "1"),
     "1 item is in no MetaMender library, so it is not being updated.",
     "1 item is in no MetaMender library"),
    (Problem(UNMATCHED, "12"),
     "12 items are in no MetaMender library, so they are not being updated.",
     "12 items are in no MetaMender library"),
    (Problem(LIBRARIES_CONVERTED, ""),
     "MetaMender now has settings per library. Your settings were copied to each library.",
     "Settings are now per library"),
    (Problem(LIBRARIES_CONVERTED, SETTINGS_UNREAD),
     ("MetaMender now has settings per library. Your earlier choices could not be read, "
      "so nothing is switched on yet: open Libraries to choose."),
     "Settings are now per library, nothing switched on"),
])
def test_library_notices_read_for_one_or_several(problem, full, short):
    assert attention_window.problem_text(problem, short=False) == full
    assert attention_window.problem_text(problem, short=True) == short
    props = attention_properties(Problem(SETUP, ""), [problem])
    assert props["MM.Also"].endswith(short)

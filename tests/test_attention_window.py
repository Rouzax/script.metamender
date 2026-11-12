"""Tests for ui/attention_window.py: what the attention window shows."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from resources.lib.service.attention import (
    FOLDER_OFFLINE,
    KEY_REJECTED,
    NO_KEY,
    SETUP,
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


def test_accent_colours_are_set():
    props = attention_properties(Problem(SETUP, ""), [])
    for key, value in GOLDEN_HOUR.items():
        assert props[key] == value


def test_window_xml_reads_every_property():
    xml = (_ROOT / "resources/skins/Default/1080i/script-metamender-attention.xml").read_text()
    for key in attention_properties(Problem(SETUP, ""), []):
        assert "Window.Property(%s)" % key in xml, key

"""Tests for ui/details_window.py: what the details window shows per row."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from resources.lib.ui import details_window
from resources.lib.ui.details_model import DetailRow
from resources.lib.ui.details_window import (
    details_properties,
    first_field_position,
    header_properties,
    list_entries,
    skip_heading,
)
from resources.lib.ui.setup_window import GOLDEN_HOUR

_ROOT = Path(__file__).resolve().parent.parent
_PO = (_ROOT / "resources/language/resource.language.en_gb/strings.po").read_text()
_STRINGS = {
    int(k): v.replace('\\"', '"')
    for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', _PO)
}
_XML = _ROOT / "resources/skins/Default/1080i/script-metamender-details.xml"

PLOT = "Agenten Mardik en Shannon worden opgeroepen voor een melding op Amsterdam Centraal."


@pytest.fixture(autouse=True)
def strings(monkeypatch):
    monkeypatch.setattr(details_window, "lang", lambda i: _STRINGS.get(i, ""))


def _rows():
    """The mockup's list: Ids, three updated fields, two left, one waiting."""
    ids = DetailRow("ids", "Ids", "", "matched by number", "Ids",
                    [("TMDB", "4104123, matched by season and episode number"),
                     ("", "Ids MetaMender found stay in its own records.")], "")
    plot = DetailRow("plot", "Plot", "updated", "TMDB", "Plot", [
        ("", PLOT),
        ("From", "TMDB, in Dutch"),
        ("Replaced", "your scraper's value"),
        ("Last checked", "12 Nov 2026"),
        ("Next check", "19 Nov 2026"),
    ], "restore")
    rating = DetailRow("rating", "Rating", "updated", "IMDb", "Rating",
                       [("", "7.4 (1234 votes)")], "")
    runtime = DetailRow("runtime", "Runtime", "updated", "TMDB", "Runtime",
                        [("", "45 min")], "")
    title = DetailRow("title", "Title", "left", "scraper", "Title",
                      [("", "Amsterdam Centraal")], "")
    aired = DetailRow("firstaired", "Aired", "left", "locked", "Aired",
                      [("", "1 Nov 2026")], "unlock")
    director = DetailRow("director", "Director", "waiting", "15 Nov", "Director",
                         [("", "Empty")], "")
    return [ids, plot, rating, runtime, title, aired, director]


def test_list_has_ids_first_then_a_heading_per_group():
    entries = list_entries(_rows())
    assert [(e.label, e.heading) for e in entries] == [
        ("Ids", False),
        ("Updated by MetaMender", True), ("Plot", False), ("Rating", False),
        ("Runtime", False),
        ("Left as it is", True), ("Title", False), ("Aired", False),
        ("Waiting", True), ("Director", False),
    ]
    assert entries[2].label2 == "TMDB"
    assert entries[1].row is None and entries[2].row.key == "plot"


def test_an_empty_group_gets_no_heading():
    rows = [r for r in _rows() if r.group != "left"]
    labels = [e.label for e in list_entries(rows)]
    assert "Left as it is" not in labels


def test_focus_starts_on_the_first_field():
    entries = list_entries(_rows())
    assert first_field_position(entries) == 2
    assert first_field_position(list_entries(_rows()[:1])) == 0


def test_moving_onto_a_heading_steps_past_it():
    entries = list_entries(_rows())
    # Down from Runtime lands on "Left as it is": on to Title.
    assert skip_heading(entries, 5, 1) == 6
    # Up from Title lands on the heading: on to Runtime.
    assert skip_heading(entries, 5, -1) == 4
    # Up from Plot lands on the first heading: on to Ids.
    assert skip_heading(entries, 1, -1) == 0
    # A field stays where it is.
    assert skip_heading(entries, 3, 1) == 3


def test_skip_heading_turns_back_at_the_ends():
    entries = list_entries(_rows()[1:])  # no Ids: a heading comes first
    assert skip_heading(entries, 0, -1) == 1


def test_mockup_plot_row_properties():
    rows = _rows()
    props = details_properties(rows, 1)
    assert props["MM.RowTitle"] == "Plot"
    assert props["MM.RowValue"] == PLOT
    lines = props["MM.RowLines"].split("\n")
    assert len(lines) == 4
    assert "From" in lines[0] and lines[0].endswith("TMDB, in Dutch")
    assert lines[3].endswith("19 Nov 2026")
    assert props["MM.RowAction"] == "Restore original"


def test_unlock_row_offers_the_way_back_and_no_action_row_none():
    rows = _rows()
    assert details_properties(rows, 5)["MM.RowAction"] == "Let MetaMender update this again"
    assert details_properties(rows, 2)["MM.RowAction"] == ""


def test_ids_row_has_no_value_box_and_keeps_its_note():
    props = details_properties(_rows(), 0)
    assert props["MM.RowValue"] == ""
    lines = props["MM.RowLines"].split("\n")
    assert lines[-1] == "Ids MetaMender found stay in its own records."
    assert "TMDB" in lines[0]


def test_header_properties_carry_colours_and_labels():
    props = header_properties("Het gouden uur · Season 1, episode 1",
                              "Dutch folder · last looked at 12 Nov 2026")
    assert props["MM.Heading"] == "Het gouden uur · Season 1, episode 1"
    assert props["MM.Sub"] == "Dutch folder · last looked at 12 Nov 2026"
    assert props["MM.UpdateNow"] == "Update now"
    assert props["MM.Close"] == "Close"
    for key, value in GOLDEN_HOUR.items():
        assert props[key] == value


def test_window_xml_parses_and_reads_every_property():
    xml = _XML.read_text()
    root = ET.fromstring(xml)
    ids = {c.get("id") for c in root.iter("control") if c.get("id")}
    assert {"50", "31", "32", "33"} <= ids
    keys = list(header_properties("h", "s")) + list(details_properties(_rows(), 1))
    for key in keys:
        assert "Window.Property(%s)" % key in xml, key
    assert "ListItem.Property(heading)" in xml


def _list_control():
    root = ET.fromstring(_XML.read_text())
    return next(c for c in root.iter("control") if c.get("id") == "50"), root


def test_headings_switch_by_visibility_in_one_layout():
    # Kodi 22 ignored layouts chosen by a condition, so each layout shows
    # the heading or the row parts by ListItem.Property(heading).
    lst, _ = _list_control()
    for tag in ("itemlayout", "focusedlayout"):
        layouts = lst.findall(tag)
        assert len(layouts) == 1 and layouts[0].get("condition") is None
        visible = [v.text for v in layouts[0].iter("visible")]
        assert any(v.startswith("!String.IsEmpty(ListItem.Property(heading))") for v in visible)
        assert any(v.startswith("String.IsEmpty(ListItem.Property(heading))") for v in visible)


def test_right_from_the_list_goes_to_the_action_button_when_there_is_one():
    lst, _ = _list_control()
    targets = {(e.get("condition") or ""): e.text for e in lst.findall("onright")}
    assert targets == {
        "!String.IsEmpty(Window.Property(MM.RowAction))": "31",
        "String.IsEmpty(Window.Property(MM.RowAction))": "32",
    }


def test_action_button_fits_the_unlock_label():
    _, root = _list_control()
    button = next(c for c in root.iter("control") if c.get("id") == "31")
    assert int(button.findtext("width")) >= 640

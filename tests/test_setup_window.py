"""Tests for the setup wizard controller (the window itself needs Kodi)."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from resources.lib.data.libraries import (
    FIELD_TOGGLES,
    TITLE_LANGUAGE,
    TITLE_ORIGINAL,
    LibraryConfig,
    new_library,
)
from resources.lib.providers.tmdb import KEY_OK, KEY_REFUSED, KEY_UNCHECKED
from resources.lib.ui import setup_window
from resources.lib.ui.setup_model import TOGGLES, initial_state
from resources.lib.ui.setup_window import (
    _COUNTRY_OPTIONS,
    HEADER,
    PAGE,
    Row,
    SetupController,
    list_position,
    window_properties,
)
from resources.lib.ui.theme import THEMES

GOLDEN_HOUR = THEMES[0]
_ROOT = Path(__file__).resolve().parents[1]
_SKIN = _ROOT / "resources/skins/Default/1080i/script-metamender-setup.xml"

_MP = "multipath://%2fa%2f/%2fb%2f/"
_DISCOVERED = [{"path": _MP, "label": "Kids"}, {"path": "/c/", "label": "Films"}]


class FakeDialogs:
    def __init__(self, inputs=(), selects=()):
        self.inputs = list(inputs)
        self.selects = list(selects)
        self.oks: list[str] = []
        self.defaults: list[str] = []
        self.headings: list[str] = []

    def input(self, heading: str, default: str) -> str:
        self.defaults.append(default)
        return self.inputs.pop(0) if self.inputs else ""

    def select(self, heading: str, options: list[str], preselect: int) -> int:
        self.headings.append(heading)
        self.options = options
        self.preselect = preselect
        return self.selects.pop(0) if self.selects else -1

    def ok(self, heading: str, message: str) -> None:
        self.oks.append(message)


class Recorder:
    def __init__(self, save_ok: bool = True, key: str = KEY_OK):
        self.saved: list[LibraryConfig] = []
        self.written: list[dict[str, Any]] = []
        self.checked: list[str] = []
        self.seen = 0
        self._save_ok = save_ok
        self._key = key

    def save(self, path: str, config: LibraryConfig) -> bool:
        self.saved.append(config)
        return self._save_ok

    def write(self, values: dict[str, Any]) -> None:
        self.written.append(values)

    def check(self, key: str) -> str:
        self.checked.append(key)
        return self._key

    def libraries_seen(self) -> None:
        self.seen += 1


def _controller(mode="wizard", key="", dialogs=None, rec=None, first_run=True,
                libraries=None, ui="nl-NL", save=None):
    state = initial_state({"tmdb_api_key": key}, libraries, _DISCOVERED, ui)
    rec = rec or Recorder()
    c = SetupController(
        mode, state, ui, "/x/sources.json", first_run, dialogs=dialogs or FakeDialogs(),
        key_checker=rec.check, save=save or rec.save, settings_writer=rec.write,
        name=lambda code: code, libraries_seen=rec.libraries_seen,
    )
    return c, rec


@pytest.fixture
def ids(monkeypatch):
    monkeypatch.setattr(setup_window, "lang", lambda i: "#%d" % i)


@pytest.fixture
def strings(monkeypatch):
    po = (_ROOT / "resources/language/resource.language.en_gb/strings.po").read_text()
    table = {int(k): v for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', po)}
    monkeypatch.setattr(setup_window, "lang", lambda i: table.get(i, ""))


def _lib(c, path):
    lib = c.state.libraries.by_path(path)
    assert lib is not None
    return lib


def _films(**changes):
    return replace(new_library("/c/", "Films", "en-US"), **changes)


def _open_page(c, path="/c/", **changes):
    c.flow.current = "libraries"
    if changes:
        c.state.libraries.replace(replace(_lib(c, path), **changes))
    c.state.open_library = path


def _row(c, key):
    return next(i for i, row in enumerate(c.rows()) if row.key == key)


# -- key step ------------------------------------------------------------------


def test_refused_key_stays_on_key_step():
    dialogs = FakeDialogs(inputs=["bad"])
    c, _ = _controller(dialogs=dialogs, rec=Recorder(key=KEY_REFUSED))
    assert c.on_next() is False
    assert c.step == "key" and len(dialogs.oks) == 1
    assert c.state.key_status == ""


def test_unchecked_key_warns_and_advances():
    dialogs = FakeDialogs(inputs=["abc"])
    c, _ = _controller(dialogs=dialogs, rec=Recorder(key=KEY_UNCHECKED))
    c.on_next()
    assert c.step == "libraries" and len(dialogs.oks) == 1
    assert c.state.key_status == KEY_UNCHECKED


def test_stored_key_is_not_rechecked_until_edited():
    dialogs = FakeDialogs(inputs=["new"])
    c, rec = _controller(key="old", dialogs=dialogs)
    c.on_next()
    assert rec.checked == [] and c.step == "libraries"
    c.on_back()
    c.activate(0)
    c.on_next()
    assert rec.checked == ["new"]


def test_stored_key_is_never_prefilled_and_empty_entry_keeps_it():
    dialogs = FakeDialogs(inputs=[""])
    c, _ = _controller(key="secret1234", dialogs=dialogs)
    c.activate(0)
    assert dialogs.defaults == [""]
    assert c.state.api_key == "secret1234" and c.state.key_status == KEY_OK


def test_back_on_first_step_returns_false():
    c, _ = _controller()
    assert c.on_back() is False and not c.has_back()


# -- the library list ----------------------------------------------------------


def test_list_rows_per_state(strings):
    stored = LibraryConfig([
        _films(included=True, toggles=frozenset({"ratings", "plots", "taglines",
                                                 "certifications"})),
        replace(new_library(_MP, "Kids", "nl-NL"), included=True,
                toggles=frozenset({"plots", "main_titles"}), title_mode=TITLE_ORIGINAL),
    ])
    c, _ = _controller(key="abc", libraries=stored)
    c.flow.current = "libraries"
    c.state.libraries.libraries.append(_films(path="/new/", label="New", included=True))
    c.state.libraries.libraries.append(_films(path="/out/", label="Out"))
    rows = c.rows()
    assert [(r.label, r.detail, r.chosen) for r in rows] == [
        ("Films", "en-US · ratings, plots, taglines, age ratings (US)", True),
        ("Kids", "nl-NL · plots, titles kept original", True),
        ("New", "Nothing switched on yet", True),
        ("Out", "Not included: MetaMender never touches it", False),
    ]
    assert c.marker_kind() == "check" and all(r.marker == "" for r in rows)
    assert c.heading() == "Which libraries MetaMender looks after"


def test_list_line_without_a_country(strings):
    stored = LibraryConfig([_films(included=True, language="xx",
                                   toggles=frozenset({"certifications", "main_titles"}))])
    c, _ = _controller(key="abc", libraries=stored, ui="yy")
    c.flow.current = "libraries"
    assert c.rows()[0].detail == "xx · titles, age ratings"


def test_including_a_library_opens_its_page(ids):
    dialogs = FakeDialogs(selects=[0])
    c, _ = _controller(key="abc", dialogs=dialogs)
    c.flow.current = "libraries"
    c.activate(1)
    assert dialogs.options == ["#32663", "#32664"] and dialogs.headings == ["Films"]
    assert _lib(c, "/c/").included and c.state.open_library == "/c/"
    assert c.view == PAGE and c.heading() == "Films"


def test_leave_out_on_an_unticked_row_changes_nothing():
    c, _ = _controller(key="abc", dialogs=FakeDialogs(selects=[1, -1]))
    c.flow.current = "libraries"
    before = list(c.state.libraries.libraries)
    c.activate(1)
    c.activate(1)
    assert c.state.libraries.libraries == before and c.state.open_library is None


def test_ticked_row_offers_change_settings_and_leave_out(ids):
    stored = LibraryConfig([_films(included=True, toggles=frozenset({"plots"}),
                                   rating_source=1, title_mode=TITLE_ORIGINAL)])
    dialogs = FakeDialogs(selects=[1, 0])
    c, _ = _controller(key="abc", dialogs=dialogs, libraries=stored)
    c.flow.current = "libraries"
    c.activate(0)
    assert dialogs.options == ["#32665", "#32664"]
    left = _lib(c, "/c/")
    assert not left.included and c.state.open_library is None
    assert left.toggles == {"plots"} and left.rating_source == 1
    # Including it again restores every setting.
    c.activate(0)
    assert _lib(c, "/c/") == stored.libraries[0]
    assert c.state.open_library == "/c/"


def test_change_settings_opens_the_page():
    stored = LibraryConfig([_films(included=True)])
    c, _ = _controller(key="abc", dialogs=FakeDialogs(selects=[0]), libraries=stored)
    c.flow.current = "libraries"
    c.activate(0)
    assert c.state.open_library == "/c/" and _lib(c, "/c/").included


def test_list_shows_once_and_tells_the_notices(ids):
    c, rec = _controller(key="abc")
    c.shown()
    assert rec.seen == 0
    c.on_next()
    c.shown()
    c.shown()
    assert rec.seen == 1


def test_empty_list_says_kodi_has_no_sources(ids):
    c, _ = _controller(key="abc")
    c.flow.current = "libraries"
    c.state.libraries = LibraryConfig()
    assert c.explanation() == "#32427" and c.rows() == []


# -- the library page -----------------------------------------------------------


def test_new_library_page_has_language_and_fields_with_the_hint(ids):
    c, _ = _controller(key="abc")
    _open_page(c, included=True)
    rows = c.rows()
    headers = [r.label for r in rows if r.marker == HEADER]
    assert headers == ["#32422", "#32430"]
    assert [r.key for r in rows] == ["", "language", "fallback", ""] + [
        "toggle:" + t for t in TOGGLES]
    assert all(not r.chosen for r in rows)
    assert c.explanation() == "#32666\n#32667"
    assert c.next_label() == "#32687" and not c.has_back()


def test_headers_are_not_activated():
    dialogs = FakeDialogs(selects=[0])
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True)
    before = _lib(c, "/c/")
    c.activate(0)
    c.activate(3)
    assert _lib(c, "/c/") == before and dialogs.headings == []


def test_page_markers_are_checkboxes_and_pickers_only(ids):
    c, _ = _controller(key="abc")
    _open_page(c, included=True, toggles=frozenset(FIELD_TOGGLES))
    assert c.marker_kind() == ""
    markers = {r.key: r.marker for r in c.rows() if r.key}
    assert set(markers.values()) == {"", "check"}
    assert {k for k, m in markers.items() if m == "check"} == {
        "toggle:" + t for t in TOGGLES} | {"replace"}
    assert "radio" not in {r.marker for r in c.rows()}


@pytest.mark.parametrize("toggle,group,keys", [
    ("ratings", "#32668", ["rating"]),
    ("plots", "#32669", ["text", "fallback_order"]),
    ("main_titles", "#32670", ["title_mode"]),
    ("certifications", "#32671", ["country", "replace", "prefix", "not_found"]),
])
def test_ticking_a_field_adds_its_group(ids, toggle, group, keys):
    c, _ = _controller(key="abc")
    _open_page(c, included=True)
    c.activate(_row(c, "toggle:" + toggle))
    assert toggle in _lib(c, "/c/").toggles
    rows = c.rows()
    assert group in [r.label for r in rows if r.marker == HEADER]
    assert set(keys) <= {r.key for r in rows}
    assert c.explanation() == "#32666"
    c.activate(_row(c, "toggle:" + toggle))
    assert group not in [r.label for r in c.rows() if r.marker == HEADER]


def test_language_pickers():
    dialogs = FakeDialogs(selects=[1, 0])
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, fallback_language="de-DE")
    c.activate(_row(c, "language"))
    codes = sorted(dialogs.options)
    assert _lib(c, "/c/").language == dialogs.options[1] and len(codes) > 5
    c.activate(_row(c, "fallback"))
    assert _lib(c, "/c/").fallback_language == ""


def test_language_picker_offers_the_current_code():
    dialogs = FakeDialogs()
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, language="nl-BE")
    c.activate(_row(c, "language"))
    assert dialogs.options[dialogs.preselect] == "nl-BE"
    assert _lib(c, "/c/").language == "nl-BE"


@pytest.mark.parametrize("key,pick,field,value", [
    ("rating", 2, "rating_source", 1),
    ("text", 1, "text_source_order", 0),
    ("fallback_order", 1, "fallback_order", 1),
    ("title_mode", 1, "title_mode", TITLE_ORIGINAL),
    ("country", 3, "cert_country", 3),
])
def test_option_pickers_set_their_value(key, pick, field, value):
    dialogs = FakeDialogs(selects=[pick, -1])
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, toggles=frozenset(FIELD_TOGGLES))
    c.activate(_row(c, key))
    assert getattr(_lib(c, "/c/"), field) == value
    # Cancelling the picker keeps the value.
    c.activate(_row(c, key))
    assert getattr(_lib(c, "/c/"), field) == value


def test_rating_picker_lists_the_five_options(ids):
    dialogs = FakeDialogs()
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, toggles=frozenset({"ratings"}), rating_source=0)
    c.activate(_row(c, "rating"))
    assert dialogs.options == ["#32632", "#32030", "#32031", "#32032", "#32033"]
    assert dialogs.preselect == 1
    assert c.rows()[_row(c, "rating")].detail == "#32030"


def test_title_mode_rows(ids):
    c, _ = _controller(key="abc", dialogs=FakeDialogs(selects=[1]))
    _open_page(c, included=True, toggles=frozenset({"main_titles"}))
    row = c.rows()[_row(c, "title_mode")]
    assert (row.label, row.detail) == ("#32672", "#32673")
    c.activate(_row(c, "title_mode"))
    assert c.rows()[_row(c, "title_mode")].detail == "#32674"
    assert _lib(c, "/c/").title_mode == TITLE_ORIGINAL
    assert TITLE_LANGUAGE == new_library("/x/", "", "").title_mode


def test_country_picker_lists_every_country(strings):
    dialogs = FakeDialogs()
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, language="en-US", toggles=frozenset({"certifications"}))
    c.activate(_row(c, "country"))
    assert len(dialogs.options) == _COUNTRY_OPTIONS + 1
    # Automatic follows the library's language region (en-US), not Kodi's.
    assert dialogs.options[0] == "Automatic: US - MPAA"
    assert dialogs.options[1] == "NL - Kijkwijzer"


def test_replace_toggles():
    c, _ = _controller(key="abc")
    _open_page(c, included=True, toggles=frozenset({"certifications"}))
    assert c.rows()[_row(c, "replace")].chosen is True
    c.activate(_row(c, "replace"))
    assert _lib(c, "/c/").cert_replace_foreign is False
    assert c.rows()[_row(c, "replace")].chosen is False


def test_prefix_picker(ids):
    dialogs = FakeDialogs(selects=[2, 2, 1, 0], inputs=["de:", ""])
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, toggles=frozenset({"certifications"}))
    assert c.rows()[_row(c, "prefix")].detail == "#32690"
    c.activate(_row(c, "prefix"))
    assert _lib(c, "/c/").cert_prefix_override == "de:"
    assert c.rows()[_row(c, "prefix")].detail == "de:"
    c.activate(_row(c, "prefix"))          # empty entry keeps the prefix
    assert _lib(c, "/c/").cert_prefix_override == "de:"
    assert dialogs.defaults == ["", "de:"]
    c.activate(_row(c, "prefix"))
    assert _lib(c, "/c/").cert_prefix_override == ""
    assert c.rows()[_row(c, "prefix")].detail == "#32701"
    c.activate(_row(c, "prefix"))
    assert _lib(c, "/c/").cert_prefix_override is None


def test_not_found_picker(ids):
    dialogs = FakeDialogs(selects=[1, 0], inputs=[" Unrated "])
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, toggles=frozenset({"certifications"}))
    assert c.rows()[_row(c, "not_found")].detail == "#32692"
    c.activate(_row(c, "not_found"))
    assert _lib(c, "/c/").cert_not_found == "Unrated" and dialogs.defaults == ["NR"]
    c.activate(_row(c, "not_found"))
    assert _lib(c, "/c/").cert_not_found is None


def test_done_returns_to_the_list():
    c, _ = _controller(key="abc")
    _open_page(c, included=True)
    assert c.on_next() is False
    assert c.state.open_library is None and c.view == "libraries"
    _open_page(c)
    assert c.on_back() is True and c.state.open_library is None


def test_done_needs_a_country_for_age_ratings():
    dialogs = FakeDialogs()
    c, _ = _controller(key="abc", dialogs=dialogs, ui="yy")
    _open_page(c, included=True, language="xx", toggles=frozenset({"certifications"}))
    c.on_next()
    assert c.state.open_library == "/c/" and len(dialogs.oks) == 1
    c.state.libraries.replace(replace(_lib(c, "/c/"), cert_country=1))
    c.on_next()
    assert c.state.open_library is None


# -- summary and finish ---------------------------------------------------------


def _to_summary(c):
    while c.step != "summary":
        assert c.on_next() is False


def test_summary_blocks_then_left_out(ids):
    stored = LibraryConfig([
        _films(included=True, toggles=frozenset(FIELD_TOGGLES), title_mode=TITLE_ORIGINAL,
               cert_replace_foreign=False),
        replace(new_library(_MP, "Kids", "nl-NL"), included=False),
    ])
    c, _ = _controller(key="abc", libraries=stored)
    _to_summary(c)
    rows = [(r.label, r.detail, r.marker) for r in c.rows()]
    assert rows[0][0] == "#32458"
    assert rows[1] == ("Films", "", HEADER)
    assert [label for label, _, _ in rows[2:10]] == [
        "#32422", "#32462", "#32648", "#32649", "#32672", "#32450", "#32645", "#32688"]
    assert rows[6][1] == "#32674" and rows[8][1] == "#32647"
    assert rows[9] == ("#32688", "", HEADER)
    assert rows[10] == ("Kids", "#32661", "")
    assert not c.explanation().startswith("#32675")


def test_summary_of_a_library_with_few_fields(ids):
    stored = LibraryConfig([_films(included=True, toggles=frozenset({"credits"}))])
    c, _ = _controller(key="abc", libraries=stored)
    _to_summary(c)
    assert [r.label for r in c.rows()] == ["#32458", "Films", "#32422", "#32462", "#32688",
                                           "Kids"]


def test_summary_warns_when_nothing_would_change(ids):
    c, _ = _controller(key="abc")
    _to_summary(c)
    assert c.explanation() == "#32675\n#32456"
    c.state.libraries.replace(_films(included=True))
    assert c.explanation().startswith("#32675")
    c.state.libraries.replace(_films(included=True, toggles=frozenset({"plots"})))
    assert c.explanation() == "#32456"


def test_nothing_written_until_finish():
    dialogs = FakeDialogs(inputs=["abc"], selects=[0, 0])
    c, rec = _controller(dialogs=dialogs)
    c.on_next()
    c.activate(1)
    c.activate(_row(c, "toggle:plots"))
    c.on_next()
    _to_summary(c)
    assert rec.saved == [] and rec.written == []
    assert c.on_next() is True
    assert _lib(c, "/c/").toggles == {"plots"}
    assert rec.saved[0].libraries == c.state.libraries.libraries
    assert rec.written == [{"tmdb_api_key": "abc", "setup_complete": True}]
    assert c.finished


def test_save_failure_writes_no_settings():
    dialogs = FakeDialogs()
    c, rec = _controller(key="abc", dialogs=dialogs, rec=Recorder(save_ok=False))
    _to_summary(c)
    assert c.on_next() is False
    assert rec.written == [] and c.finished is False
    assert len(dialogs.oks) == 1


def test_libraries_mode_saves_only_libraries(ids):
    c, rec = _controller(mode="libraries", key="abc")
    assert c.step == "libraries" and c.next_label() == "#32407"
    assert c.on_next() is True
    assert len(rec.saved) == 1 and rec.written == []


def test_first_setup_writes_v2_and_raises_no_conversion_notice(tmp_path, monkeypatch):
    from resources.lib.data.libraries import FILE_OK, read_libraries, save_libraries
    from resources.lib.data.tracker import TrackerDB
    from resources.lib.service import attention, daemon

    path = str(tmp_path / "sources.json")
    c, rec = _controller(key="abc", save=save_libraries)
    c._config_path = path
    _to_summary(c)
    assert c.on_next() is True
    assert read_libraries(path)[1] == FILE_OK
    monkeypatch.setattr(daemon, "_get_sources_config_path", lambda: path)
    monkeypatch.setattr(daemon, "is_setup_complete", lambda: True)
    monkeypatch.setattr(daemon, "discover_folders", lambda: _DISCOVERED)
    tracker = TrackerDB(str(tmp_path / "t.db"))
    try:
        libraries = daemon._sync_libraries(tracker, "nl-NL")
        assert libraries is not None and len(libraries.libraries) == 2
        assert attention.load(tracker).pending_window() is None
    finally:
        tracker.close()


# -- window properties and skin ---------------------------------------------------


def test_golden_hour_values():
    assert GOLDEN_HOUR == {"MM.Accent": "FFF5A623",
                           "MM.ButtonFocus": "FFD4912A", "MM.ButtonTextFocused": "FF0D1117"}


def test_window_properties_always_carry_every_key():
    c, _ = _controller(key="abc")
    props = window_properties(c)
    assert set(props) == {"MM.Heading", "MM.Text", "MM.Next", "MM.Back", "MM.Cancel",
                          "MM.StepIndex", "MM.StepTotal", *GOLDEN_HOUR}
    assert props["MM.StepIndex"] == "1" and props["MM.StepTotal"] == "3"
    assert props["MM.Back"] == "" and c.marker_kind() == ""


def test_page_keeps_the_libraries_dot():
    c, _ = _controller(key="abc")
    _open_page(c, included=True)
    props = window_properties(c)
    assert props["MM.StepIndex"] == "2" and props["MM.Back"] == ""


def test_libraries_dialog_has_no_dots():
    c, _ = _controller(mode="libraries")
    props = window_properties(c)
    assert props["MM.StepIndex"] == "" and props["MM.StepTotal"] == ""
    assert c.marker_kind() == "check"


def test_view_key_changes_per_page():
    c, _ = _controller(key="abc")
    _open_page(c)
    films = c.view_key()
    _open_page(c, _MP)
    assert c.view_key() != films and c.view_key().startswith(PAGE)


def test_list_position_starts_on_the_first_row_that_is_no_heading():
    rows = [Row("a", marker=HEADER), Row("b"), Row("c")]
    assert list_position("libraries", "page:/c/", 2, rows) == 1
    assert list_position(None, "key", 0, [Row("k")]) == 0
    assert list_position("page:/c/", "page:/c/", 2, rows) == 2
    assert list_position("page:/c/", "page:/c/", 9, rows) == 2
    assert list_position("libraries", "libraries", -1, rows) == 0


def test_skin_has_a_dot_slot_per_wizard_step():
    xml = _SKIN.read_text(encoding="utf-8")
    for n in range(1, 4):
        assert "Integer.IsGreaterOrEqual(Window.Property(MM.StepTotal),%d)" % n in xml
        assert "Integer.IsGreaterOrEqual(Window.Property(MM.StepIndex),%d)" % n in xml
    assert "Integer.IsGreaterOrEqual(Window.Property(MM.StepTotal),4)" not in xml


def test_skin_markers_follow_row_property():
    xml = _SKIN.read_text(encoding="utf-8")
    assert "Window.Property(MM.Marker)" not in xml
    for kind in ("check", "radio"):
        assert xml.count("String.IsEqual(ListItem.Property(marker),%s)" % kind) == 4
    # The labels move right of a marker, but not on a heading.
    assert xml.count("!String.IsEmpty(ListItem.Property(marker)) + "
                     "!String.IsEqual(ListItem.Property(marker),header)") == 2


def test_skin_draws_header_rows_in_both_layouts():
    xml = _SKIN.read_text(encoding="utf-8")
    for layout in ("itemlayout", "focusedlayout"):
        body = xml[xml.index("<%s" % layout):xml.index("</%s>" % layout)]
        accent_label = re.search(
            r"<control type=\"label\">(?:(?!</control>).)*MM\.Accent(?:(?!</control>).)*"
            r"String\.IsEqual\(ListItem\.Property\(marker\),header\)", body, re.DOTALL)
        assert accent_label, layout

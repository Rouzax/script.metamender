"""Tests for the setup wizard controller (the window itself needs Kodi)."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any, ClassVar

import pytest

from resources.lib.constants import COUNTRY_CODES
from resources.lib.data.libraries import (
    FIELD_TOGGLES,
    FILE_DAMAGED,
    FILE_LEGACY,
    FILE_MISSING,
    FILE_OK,
    TITLE_LANGUAGE,
    TITLE_ORIGINAL,
    LibraryConfig,
    new_library,
    read_libraries,
    save_libraries,
)
from resources.lib.data.tracker import TrackerDB
from resources.lib.providers.tmdb import KEY_OK, KEY_REFUSED, KEY_UNCHECKED
from resources.lib.service import attention, daemon
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
    def __init__(self, inputs=(), selects=(), answers=()):
        self.inputs = list(inputs)
        self.selects = list(selects)
        self.answers = list(answers)
        self.questions: list[str] = []
        self.oks: list[str] = []
        self.defaults: list[str] = []
        self.headings: list[str] = []

    def input(self, heading: str, default: str) -> str:
        self.defaults.append(default)
        return self.inputs.pop(0) if self.inputs else ""

    def select(self, heading: str, options: list[str], preselect: int,
               details: list[str] | None = None, marked: bool = True) -> int:
        self.headings.append(heading)
        self.options = options
        self.preselect = preselect
        self.details = details
        self.marked = marked
        return self.selects.pop(0) if self.selects else -1

    def ok(self, heading: str, message: str) -> None:
        self.oks.append(message)

    def yesno(self, heading: str, message: str) -> bool:
        self.questions.append(message)
        return self.answers.pop(0)


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
    # Actions, not a choice: no option shows as the current one.
    assert dialogs.marked is False
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
    assert dialogs.details == ["#32633", "#32634", "#32635", "#32636", "#32637"]
    assert dialogs.preselect == 1
    assert c.rows()[_row(c, "rating")].detail == "#32030"


def test_text_order_picker_lists_the_options_with_their_sub_lines(ids):
    dialogs = FakeDialogs()
    c, _ = _controller(key="abc", dialogs=dialogs)
    _open_page(c, included=True, toggles=frozenset({"plots"}))
    c.activate(_row(c, "text"))
    assert dialogs.options == ["#32632", "#32392", "#32393"]
    assert dialogs.details == ["#32640", "#32641", "#32642"]
    c.activate(_row(c, "fallback_order"))
    assert dialogs.details is None


def test_select_opens_metamenders_own_picker(monkeypatch):
    calls = []

    def show_select(heading, options, preselect, details=None, marked=True):
        calls.append((heading, options, preselect, details, marked))
        return 1

    monkeypatch.setattr(setup_window, "show_select", show_select)
    dialogs = setup_window.Dialogs()
    assert dialogs.select("h", ["a", "b"], 0, ["sub a", "sub b"]) == 1
    dialogs.select("h", ["a"], 0, marked=False)
    assert calls == [("h", ["a", "b"], 0, ["sub a", "sub b"], True),
                     ("h", ["a"], 0, None, False)]


def test_messages_open_metamenders_own_window(monkeypatch):
    calls = []

    def show_message(heading, message, question=False):
        calls.append((heading, message, question))
        return True

    monkeypatch.setattr(setup_window, "show_message", show_message)
    dialogs = setup_window.Dialogs()
    assert dialogs.yesno("h", "Save?") is True
    dialogs.ok("h", "Saved.")
    assert calls == [("h", "Save?", True), ("h", "Saved.", False)]


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


# -- leaving with unsaved changes ------------------------------------------------


def _include_films(c):
    """Include Films from the list; its page opens, Back returns to the list."""
    c.activate(_row(c, "/c/"))
    assert c.view == PAGE
    assert c.on_back() is True and c.view == "libraries"


def test_leaving_the_list_with_changes_asks_and_yes_saves(ids):
    dialogs = FakeDialogs(selects=[0], answers=[True])
    c, rec = _controller(mode="libraries", key="abc", dialogs=dialogs, first_run=False)
    _include_films(c)
    assert c.on_back() is False
    assert c.on_cancel() is True
    assert dialogs.questions == ["#32708"]
    assert c.finished and len(rec.saved) == 1 and rec.written == []
    assert rec.saved[0].by_path("/c/").included


def test_leaving_the_list_with_changes_no_discards(ids):
    dialogs = FakeDialogs(selects=[0], answers=[False])
    c, rec = _controller(mode="libraries", key="abc", dialogs=dialogs, first_run=False)
    _include_films(c)
    assert c.on_cancel() is True
    assert dialogs.questions == ["#32708"]
    assert not c.finished and rec.saved == [] and rec.written == []


def test_leaving_without_changes_asks_nothing():
    dialogs = FakeDialogs()
    c, rec = _controller(mode="libraries", key="abc", dialogs=dialogs, first_run=False)
    assert c.on_cancel() is True
    c, rec = _controller(key="abc", dialogs=dialogs)
    assert c.on_cancel() is True
    assert dialogs.questions == [] and rec.saved == []


def test_leaving_from_a_page_asks_too(ids):
    dialogs = FakeDialogs(selects=[0], answers=[True])
    c, rec = _controller(mode="libraries", key="abc", dialogs=dialogs, first_run=False)
    c.activate(_row(c, "/c/"))
    assert c.view == PAGE
    assert c.on_cancel() is True
    assert len(rec.saved) == 1 and c.finished


def test_saving_on_leave_keeps_the_page_open_without_a_country(ids):
    dialogs = FakeDialogs(selects=[0], answers=[True])
    c, rec = _controller(mode="libraries", key="abc", dialogs=dialogs, first_run=False,
                         ui="yy")
    c.activate(_row(c, "/c/"))
    c.state.libraries.replace(replace(_lib(c, "/c/"), language="xx",
                                      toggles=frozenset({"certifications"})))
    assert c.on_cancel() is False
    assert c.view == PAGE and rec.saved == [] and len(dialogs.oks) == 1


def test_wizard_cancel_with_changes_yes_finishes(ids):
    dialogs = FakeDialogs(selects=[0], answers=[True])
    c, rec = _controller(key="abc", dialogs=dialogs)
    c.on_next()
    _include_films(c)
    assert c.on_cancel() is True
    assert c.finished and len(rec.saved) == 1
    assert rec.written == [{"tmdb_api_key": "abc", "libraries_converted": True,
                            "setup_complete": True}]


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
    assert rec.written == [{"tmdb_api_key": "abc", "libraries_converted": True,
                            "setup_complete": True}]
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


def test_list_follows_the_explanation_whatever_its_length():
    # The explanation box takes the height of its text, and the list sits
    # below it in one grouplist, so a one-line explanation leaves no gap.
    xml = _SKIN.read_text(encoding="utf-8")
    box = re.search(r'<control type="textbox">.*?</control>', xml, re.DOTALL).group(0)
    assert re.search(r'<height min="\d+" max="\d+">auto</height>', box)
    column = xml[xml.index('<control type="grouplist" id="%d">' % setup_window._COLUMN):]
    assert column.index('<control type="textbox">') < column.index('<control type="list" id="100">')


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


# -- run_setup ------------------------------------------------------------------


class _RunDialogs:
    instances: ClassVar[list[_RunDialogs]] = []
    answer = True

    def __init__(self):
        self.oks: list[str] = []
        self.questions: list[str] = []
        _RunDialogs.instances.append(self)

    def ok(self, heading, message):
        self.oks.append(message)

    def yesno(self, heading, message):
        self.questions.append(message)
        return _RunDialogs.answer


class _Window:
    opened: ClassVar[list[SetupController]] = []

    def __init__(self, *args):
        pass

    def doModal(self):
        _Window.opened.append(self.controller)


@pytest.fixture
def run(monkeypatch, ids):
    """run_setup with Kodi replaced: ``run(found, complete)`` gives the
    file state read_libraries reports and whether setup was finished."""
    _RunDialogs.instances, _Window.opened = [], []
    monkeypatch.setattr(setup_window, "Dialogs", _RunDialogs)
    monkeypatch.setattr(setup_window, "SetupWindow", _Window)
    monkeypatch.setattr(setup_window, "kodi_ui_language", lambda: "nl-NL")
    monkeypatch.setattr(setup_window, "discover_folders", lambda: _DISCOVERED)
    monkeypatch.setattr(setup_window, "get_setting", lambda key: "abc")

    class _Addon:
        def getAddonInfo(self, key):
            return ""

    monkeypatch.setattr(setup_window, "get_addon", _Addon)

    def go(found, complete, mode="libraries", answer=True):
        _RunDialogs.answer = answer
        monkeypatch.setattr(setup_window, "read_libraries", lambda path: (None, found))
        monkeypatch.setattr(setup_window, "get_bool_setting", lambda key: complete)
        return setup_window.run_setup(mode, "/x/sources.json")

    return go


def _messages():
    return [m for d in _RunDialogs.instances for m in d.oks + d.questions]


@pytest.mark.parametrize("mode", ["wizard", "libraries"])
def test_unconverted_1_1_file_after_setup_refuses_to_open(run, mode):
    assert run(FILE_LEGACY, True, mode) is False
    assert _messages() == ["#32696"] and _Window.opened == []


def test_first_run_with_a_1_1_file_starts_from_kodi_sources(run):
    """Before the first setup the service never converts (and open_setup
    has already converted a file with folder languages): the wizard opens
    on the discovered libraries, none included."""
    assert run(FILE_LEGACY, False, "wizard") is False
    assert _messages() == []
    (c,) = _Window.opened
    assert [lib.path for lib in c.state.libraries.libraries] == [_MP, "/c/"]
    assert not any(lib.included for lib in c.state.libraries.libraries)


@pytest.mark.parametrize("mode", ["wizard", "libraries"])
def test_damaged_file_opens_only_when_the_user_starts_afresh(run, mode):
    assert run(FILE_DAMAGED, True, mode, answer=False) is False
    assert _messages() == ["#32702"] and _Window.opened == []

    _RunDialogs.instances = []
    run(FILE_DAMAGED, True, mode, answer=True)
    assert _messages() == ["#32702"]
    (c,) = _Window.opened
    assert len(c.state.libraries.libraries) == 2


def test_readable_file_opens_without_asking(run):
    run(FILE_OK, True)
    run(FILE_MISSING, False, "wizard")
    assert _messages() == [] and len(_Window.opened) == 2


def test_failed_discovery_opens_on_the_stored_libraries(run, monkeypatch):
    stored = LibraryConfig([_films(included=True)])
    monkeypatch.setattr(setup_window, "discover_folders", lambda: None)
    monkeypatch.setattr(setup_window, "read_libraries", lambda path: (stored, FILE_OK))
    monkeypatch.setattr(setup_window, "get_bool_setting", lambda key: True)
    setup_window.run_setup("libraries", "/x/sources.json")
    (c,) = _Window.opened
    assert c.state.libraries == stored


def test_country_labels_follow_country_codes():
    # cert_country is an index into COUNTRY_CODES, so the picker's labels
    # must be in the same order or one country is shown while another's
    # certifications are written.

    po = Path("resources/language/resource.language.en_gb/strings.po").read_text(encoding="utf-8")
    strings = {int(k): v for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', po)}
    labels = setup_window._COUNTRY_LABELS
    assert len(labels) == len(COUNTRY_CODES)
    for string_id, code in zip(labels, COUNTRY_CODES):
        assert strings[string_id].startswith(code + " "), (string_id, code)

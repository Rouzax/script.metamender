"""Tests for the setup wizard model."""
from __future__ import annotations

from dataclasses import replace

import pytest

from resources.lib.data.libraries import (
    FIELD_TOGGLES,
    TITLE_ORIGINAL,
    LibraryConfig,
    load_libraries,
    new_library,
    save_libraries,
)
from resources.lib.providers.tmdb import KEY_OK, KEY_UNCHECKED
from resources.lib.ui.setup_model import (
    LIBRARIES,
    TOGGLES,
    WIZARD,
    SetupFlow,
    SetupState,
    changes_nothing,
    country_missing,
    initial_state,
    libraries_to_write,
    mask_key,
    page_groups,
    settings_to_write,
)

_MP = "multipath://%2fa%2f/%2fb%2f/"
_DISCOVERED = [{"path": _MP, "label": "Kids"}, {"path": "/c/", "label": "Films"}]


def _state(key: str = "", libraries: LibraryConfig | None = None) -> SetupState:
    return initial_state({"tmdb_api_key": key}, libraries, _DISCOVERED, "nl-NL")


def test_toggles_follow_the_library_model():
    assert TOGGLES == tuple(FIELD_TOGGLES)
    assert TOGGLES[0] == "ratings" and TOGGLES[-1] == "details"


def test_first_run_adds_every_discovered_library_left_out():
    state = _state()
    assert state.api_key == "" and state.key_status == ""
    assert state.open_library is None
    libs = state.libraries.libraries
    assert [(lib.path, lib.label) for lib in libs] == [(_MP, "Kids"), ("/c/", "Films")]
    assert all(not lib.included and not lib.toggles and lib.language == "nl-NL"
               for lib in libs)


def test_stored_libraries_are_kept_and_new_ones_added():
    stored = replace(new_library("/c/", "Old name", "en-US"), included=True,
                     toggles=frozenset({"plots"}), title_mode=TITLE_ORIGINAL)
    gone = new_library("/gone/", "Gone", "en-US")
    state = _state(key="abc", libraries=LibraryConfig([stored, gone]))
    assert state.key_status == KEY_OK
    films = state.libraries.by_path("/c/")
    assert films is not None
    # Kodi's current label wins; everything else is kept.
    assert films == replace(stored, label="Films")
    assert state.libraries.by_path("/gone/") == gone
    kids = state.libraries.by_path(_MP)
    assert kids is not None and not kids.included


def test_a_source_inside_a_library_is_not_added():
    parent = replace(new_library("/c/", "Films", "en-US"), included=True)
    state = initial_state({}, LibraryConfig([parent]),
                          [{"path": "/c/kids/", "label": "Kids"}], "nl-NL")
    assert [lib.path for lib in state.libraries.libraries] == ["/c/"]


def test_initial_state_does_not_change_the_stored_config():
    stored = LibraryConfig([new_library("/c/", "Old", "en-US")])
    _state(libraries=stored)
    assert [lib.label for lib in stored.libraries] == ["Old"]


@pytest.mark.parametrize("mode,steps", [
    (WIZARD, ["key", "libraries", "summary"]),
    (LIBRARIES, ["libraries"]),
])
def test_steps_per_mode(mode, steps):
    flow = SetupFlow(mode)
    assert flow.steps(_state()) == steps
    assert flow.current == steps[0]


def test_wizard_walks_forward_and_back():
    flow = SetupFlow(WIZARD)
    state = _state(key="k")
    assert not flow.back(state)
    assert flow.next(state) and flow.current == "libraries"
    assert flow.index(state) == 2 and flow.total(state) == 3
    assert flow.next(state) and flow.current == "summary"
    assert not flow.next(state)
    assert flow.back(state) and flow.current == "libraries"


def test_key_step_blocks_until_checked():
    flow = SetupFlow(WIZARD)
    state = _state()
    assert not flow.can_advance(state)
    state.api_key, state.key_status = "abc", ""
    assert not flow.can_advance(state)
    state.key_status = KEY_UNCHECKED
    assert flow.can_advance(state)
    state.key_status = KEY_OK
    assert flow.can_advance(state)


def test_libraries_step_always_advances():
    flow = SetupFlow(WIZARD)
    flow.current = "libraries"
    state = _state()
    assert changes_nothing(state.libraries)
    assert flow.can_advance(state)


def _lib(*toggles: str):
    return replace(new_library("/c/", "Films", "nl-NL"), included=True,
                   toggles=frozenset(toggles))


@pytest.mark.parametrize("toggles,groups", [
    ((), ["language", "fields"]),
    (("ratings",), ["language", "fields", "rating"]),
    (("plots",), ["language", "fields", "text"]),
    (("episode_titles",), ["language", "fields", "text"]),
    (("main_titles",), ["language", "fields", "text", "titles"]),
    (("certifications",), ["language", "fields", "age_ratings"]),
    (("credits", "trailers", "taglines", "details"), ["language", "fields"]),
    (tuple(FIELD_TOGGLES),
     ["language", "fields", "rating", "text", "titles", "age_ratings"]),
])
def test_page_groups_follow_ticks(toggles, groups):
    assert page_groups(_lib(*toggles)) == groups


def test_country_missing_only_with_certifications():
    lib = replace(_lib("certifications"), language="xx-ZZ")
    assert country_missing(lib, "yy-QQ")
    assert not country_missing(lib, "nl-NL")
    assert not country_missing(replace(lib, cert_country=1), "yy-QQ")
    assert not country_missing(_lib("plots"), "yy-QQ")


def test_changes_nothing_needs_an_included_library_with_a_field():
    assert changes_nothing(LibraryConfig([_lib()]))
    assert changes_nothing(LibraryConfig([replace(_lib("plots"), included=False)]))
    assert not changes_nothing(LibraryConfig([_lib("plots")]))


def test_settings_to_write_ends_with_setup_complete():
    out = settings_to_write(_state(key=" abc "))
    assert out == {"tmdb_api_key": "abc", "setup_complete": True}
    assert list(out)[-1] == "setup_complete"


def test_libraries_to_write_round_trips(tmp_path):
    state = _state()
    state.libraries.replace(replace(state.libraries.libraries[0], included=True,
                                    toggles=frozenset({"ratings", "main_titles"}),
                                    title_mode=TITLE_ORIGINAL, cert_not_found="NR"))
    out = libraries_to_write(state)
    assert out.libraries == state.libraries.libraries
    assert out.libraries is not state.libraries.libraries
    path = str(tmp_path / "sources.json")
    assert save_libraries(path, out)
    loaded = load_libraries(path)
    assert loaded is not None and loaded.libraries == out.libraries


def test_mask_key_hides_the_length():
    assert mask_key("abcdef1234") == "****1234"
    assert mask_key("x" * 28 + "1234") == "****1234"
    assert mask_key("abc") == "****"

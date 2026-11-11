"""Tests for the setup wizard model."""
from __future__ import annotations

from typing import Any

from resources.lib.providers.tmdb import KEY_OK, KEY_UNCHECKED
from resources.lib.ui.setup_model import (
    FEATURES,
    SetupFlow,
    config_to_write,
    initial_state,
    mask_key,
    settings_to_write,
    summary_lines,
)

_MP = "multipath://%2fa%2f/%2fb%2f/"
_DISCOVERED = [{"path": _MP, "label": "Kids"}, {"path": "/c/", "label": "Films"}]


def _settings(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {f: False for f in FEATURES}
    base.update({"enable_ratings": True, "enable_plots": True,
                 "enable_certifications": True, "tmdb_api_key": "", "country": 0})
    base.update(overrides)
    return base


def _state(**overrides: Any):
    return initial_state(_settings(**overrides), {"sources": []}, _DISCOVERED, "nl-NL")


def test_features_are_the_nine_toggles_in_settings_order():
    assert FEATURES == (
        "enable_ratings", "enable_plots", "enable_episode_titles",
        "enable_main_titles", "enable_certifications", "enable_credits",
        "enable_trailers", "enable_taglines", "enable_details",
    )


def test_first_run_state_uses_ui_language_and_no_key():
    state = _state()
    assert state.default_language == "nl-NL"
    assert state.fallback_language == ""
    assert state.api_key == "" and state.key_status == ""
    assert state.exceptions == {}
    assert state.features["enable_ratings"] and not state.features["enable_details"]


def test_rerun_state_reproduces_stored_values():
    config = {"default_language": "en-US", "fallback_language": "de-DE",
              "sources": [{"path": "/a/", "language": "nl-NL"}]}
    state = initial_state(
        _settings(tmdb_api_key="abcdef1234", enable_details=True, country=3),
        config, _DISCOVERED, "nl-NL")
    assert state.api_key == "abcdef1234" and state.key_status == KEY_OK
    assert state.default_language == "en-US" and state.fallback_language == "de-DE"
    assert state.exceptions == {_MP: ("nl-NL", "")}
    assert state.features["enable_details"] is True
    assert state.country == 3


def test_country_step_only_with_certifications():
    flow = SetupFlow("wizard", "nl-NL")
    state = _state()
    assert flow.steps(state) == ["key", "language", "folders", "features", "country", "summary"]
    state.features["enable_certifications"] = False
    assert flow.steps(state) == ["key", "language", "folders", "features", "summary"]


def test_back_from_summary_skips_country_after_unticking():
    flow = SetupFlow("wizard", "nl-NL")
    state = _state(tmdb_api_key="k")
    while flow.current != "summary":
        assert flow.next(state)
    state.features["enable_certifications"] = False
    assert flow.back(state)
    assert flow.current == "features"
    assert flow.index(state) == 4 and flow.total(state) == 5


def test_first_step_has_no_back_and_last_has_no_next():
    flow = SetupFlow("wizard", "nl-NL")
    state = _state(tmdb_api_key="k")
    assert not flow.back(state)
    while flow.next(state):
        pass
    assert flow.current == "summary"


def test_key_step_blocks_until_checked():
    flow = SetupFlow("wizard", "nl-NL")
    state = _state()
    assert not flow.can_advance(state)
    state.api_key, state.key_status = "abc", ""
    assert not flow.can_advance(state)
    state.key_status = KEY_UNCHECKED
    assert flow.can_advance(state)
    state.key_status = KEY_OK
    assert flow.can_advance(state)


def test_country_step_blocks_on_automatic_without_preset():
    state = _state(tmdb_api_key="k")
    for ui, ok in (("xx-ZZ", False), ("nl-NL", True)):
        flow = SetupFlow("wizard", ui)
        while flow.current != "country":
            flow.next(state)
        assert flow.can_advance(state) is ok
    state.country = 1
    assert flow.can_advance(state)


def test_folders_mode_has_one_step():
    flow = SetupFlow("folders", "nl-NL")
    assert flow.steps(_state()) == ["folders"]
    assert flow.current == "folders"


def test_settings_to_write():
    state = _state(tmdb_api_key=" abc ")
    out = settings_to_write(state)
    assert out["setup_complete"] is True
    assert out["tmdb_api_key"] == "abc"
    assert set(out) == {"tmdb_api_key", "country", "setup_complete", *FEATURES}


def test_config_to_write_applies_languages():
    state = _state()
    state.fallback_language = "en-US"
    state.exceptions = {_MP: ("de-DE", "")}
    out = config_to_write(state, {"sources": []}, _DISCOVERED)
    assert out["default_language"] == "nl-NL"
    assert out["fallback_language"] == "en-US"
    assert {"path": "/a/", "language": "de-DE"} in out["sources"]


def test_mask_key():
    assert mask_key("abcdef1234") == "******1234"
    assert mask_key("abc") == "***"


def test_summary_lines():
    state = _state(tmdb_api_key="abcdef1234")
    state.exceptions = {"/c/": ("de-DE", "en-US")}
    lines = dict(summary_lines(state, _DISCOVERED))
    assert lines["key"] == "******1234"
    assert lines["key_status"] == KEY_OK
    assert lines["language"] == "nl-NL"
    assert lines["folder:Films"] == "de-DE,en-US"
    assert lines["features"] == "enable_ratings,enable_plots,enable_certifications"
    assert lines["country"] == "0"
    state.features["enable_certifications"] = False
    assert "country" not in dict(summary_lines(state, _DISCOVERED))

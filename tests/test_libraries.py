"""Tests for the library model and the sources.json v2 file."""
from __future__ import annotations

import json

from resources.lib.constants import COUNTRY_CODES
from resources.lib.data.libraries import (
    FIELD_TOGGLES,
    Library,
    LibraryConfig,
    legacy_config,
    load_libraries,
    new_library,
    resolve_country,
    save_libraries,
    scan_settings,
)


def _lib(path: str = "/m/", language: str = "en-US", **kw) -> Library:
    return Library(path=path, label="L", included=True, language=language,
                   fallback_language="", toggles=frozenset(), **kw)


def test_new_library_has_nothing_on():
    lib = new_library("smb://s/kids/", "Kids", "nl-NL")
    assert lib.included is False
    assert lib.toggles == frozenset()
    assert lib.fields() == frozenset()
    assert lib.language == "nl-NL"
    assert lib.title_mode == "language"


def test_field_toggle_names():
    assert set(FIELD_TOGGLES) == {
        "ratings", "plots", "episode_titles", "main_titles", "certifications",
        "credits", "trailers", "taglines", "details"}


def test_fields_expand_toggles():
    lib = Library(path="/m/", label="L", included=True, language="en-US",
                  fallback_language="", toggles=frozenset({"main_titles", "details"}))
    assert lib.fields() == frozenset(
        {"main_title", "genre", "studio", "country", "runtime", "firstaired"})


def test_round_trip_v2(tmp_path):
    p = str(tmp_path / "sources.json")
    cfg = LibraryConfig([
        Library(path="/m/", label="M", included=True, language="nl-NL",
                fallback_language="en-US", toggles=frozenset({"plots", "ratings"}),
                cert_prefix_override="X", cert_not_found="NR",
                subfolders=({"path": "/m/sport/", "enabled": False},)),
        new_library("/t/", "T", "en-US"),
    ])
    assert save_libraries(p, cfg) is True
    with open(p, encoding="utf-8") as fh:
        raw = json.load(fh)
    assert raw["version"] == 2
    assert raw["libraries"][0]["toggles"] == ["plots", "ratings"]
    assert load_libraries(p) == cfg


def test_load_without_version_is_none(tmp_path):
    p = tmp_path / "sources.json"
    p.write_text(json.dumps({"sources": []}))
    assert load_libraries(str(p)) is None


def test_load_missing_file_is_none(tmp_path):
    assert load_libraries(str(tmp_path / "nope.json")) is None


def test_load_unreadable_is_none(tmp_path):
    p = tmp_path / "sources.json"
    p.write_text("{not json")
    assert load_libraries(str(p)) is None


def test_for_file_longest_match():
    outer, kids = _lib("/m/"), _lib("/m/kids/")
    cfg = LibraryConfig([outer, kids])
    assert cfg.for_file("/m/kids/a.mkv") is kids
    assert cfg.for_file("/m/b.mkv") is outer


def test_for_file_none_outside_libraries():
    assert LibraryConfig([_lib("/m/")]).for_file("/x/a.mkv") is None


def test_for_file_matches_multipath_and_trailing_slash():
    lib = _lib("multipath://smb%3a%2f%2fs%2fa%2f/smb%3a%2f%2fs%2fb/")
    cfg = LibraryConfig([lib])
    assert cfg.for_file("smb://s/a/x.mkv") is lib
    assert cfg.for_file("smb://s/b/y.mkv") is lib
    assert cfg.for_file("smb://s/bb/z.mkv") is None


def test_by_path_and_replace():
    a, b = _lib("/a/"), _lib("/b/")
    cfg = LibraryConfig([a, b])
    assert cfg.by_path("/b/") is b
    assert cfg.by_path("/c/") is None
    b2 = Library(**{**b.__dict__, "label": "B2"})
    cfg.replace(b2)
    assert cfg.libraries == [a, b2]


def test_subfolder_exclusion_and_language():
    lib = Library(
        path="/m/", label="L", included=True, language="en-US",
        fallback_language="de-DE", toggles=frozenset(),
        subfolders=({"path": "/m/sport/", "enabled": False},
                    {"path": "/m/nl/", "language": "nl-NL"}))
    cfg = LibraryConfig([lib])
    assert cfg.folder_settings("/m/sport/a.mkv", lib)[0] is False
    assert cfg.folder_settings("/m/nl/b.mkv", lib) == (True, "nl-NL", "")
    assert cfg.folder_settings("/m/c.mkv", lib) == (True, "en-US", "de-DE")


def test_resolve_country_follows_library_language():
    lib = _lib(language="nl-NL")
    assert resolve_country(lib, "en-US") == "NL"
    lib = _lib(language="en")
    assert resolve_country(lib, "de-DE") == "DE"
    lib = Library(**{**_lib().__dict__, "cert_country": 2})
    assert resolve_country(lib, "en-US") == COUNTRY_CODES[1]


def test_scan_settings_merges_general():
    lib = Library(**{**_lib(language="nl-NL").__dict__, "cert_not_found": "NR",
                     "rating_source": 1})
    general = {"cert_sites": frozenset({"fsk"}), "rate_limit": 0.5}
    out = scan_settings(lib, general, "en-US")
    assert out["cert_sites"] == frozenset({"fsk"})
    assert out["rate_limit"] == 0.5
    assert out["rating_source"] == 1
    assert out["cert_country"] == "NL"
    assert out["cert_not_found"] == "NR"
    assert out["title_mode"] == "language"
    assert out["cert_replace_foreign"] is True
    assert general == {"cert_sites": frozenset({"fsk"}), "rate_limit": 0.5}


def _legacy_settings(**kw):
    settings = {
        "enabled_fields": frozenset({"rating", "plot", "director", "writer"}),
        "rating_source": 2, "text_source_order": 1, "fallback_order": 1,
        "cert_country": "DE", "cert_prefix_override": "FSK ",
        "cert_replace_foreign": False, "cert_not_found": "NR",
    }
    settings.update(kw)
    return settings


def test_legacy_config_one_library_per_entry():
    config = legacy_config({
        "default_language": "nl-NL", "fallback_language": "en-US",
        "sources": [
            {"path": "/m/", "language": "de-DE"},
            {"path": "/s/", "enabled": False},
            {"path": "/k/", "language": "fr-FR", "fallback_language": ""},
        ],
    }, _legacy_settings(), "en-US")
    m, s, k = config.libraries
    assert [lib.path for lib in config.libraries] == ["/m/", "/s/", "/k/"]
    assert (m.included, m.language, m.fallback_language) == (True, "de-DE", "en-US")
    assert (s.included, s.language) == (False, "nl-NL")
    assert (k.language, k.fallback_language) == ("fr-FR", "")
    assert m.toggles == {"ratings", "plots", "credits"}
    assert (m.rating_source, m.text_source_order, m.fallback_order) == (2, 1, 1)
    assert (m.cert_prefix_override, m.cert_replace_foreign, m.cert_not_found) == (
        "FSK ", False, "NR")
    assert scan_settings(m, {}, "en-US")["cert_country"] == "DE"
    assert config.for_file("/elsewhere/a.mkv") is None


def test_legacy_config_keeps_an_unconfigured_language_and_no_country():
    config = legacy_config({"sources": [{"path": "/m/"}]},
                           _legacy_settings(cert_country=None), "nl-NL")
    lib = config.libraries[0]
    assert lib.language == ""
    assert scan_settings(lib, {}, "nl-NL")["cert_country"] is None

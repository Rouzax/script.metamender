"""Tests for the library model and the sources.json v2 file."""
from __future__ import annotations

import json

from resources.lib.constants import COUNTRY_CODES
from resources.lib.data.libraries import (
    FIELD_TOGGLES,
    Library,
    LibraryConfig,
    FILE_DAMAGED,
    FILE_LEGACY,
    FILE_MISSING,
    FILE_OK,
    library_from_legacy_settings,
    load_libraries,
    new_library,
    resolve_country,
    read_libraries,
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
        "credits", "trailers", "taglines", "posters", "episode_thumbs", "details"}


def test_field_toggles_include_art():
    assert FIELD_TOGGLES["posters"] == frozenset({"poster"})
    assert FIELD_TOGGLES["episode_thumbs"] == frozenset({"thumb"})
    assert list(FIELD_TOGGLES)[-1] == "details"


def _write_with_source(tmp_path, **extra):
    p = tmp_path / "sources.json"
    entry = {"path": "/m/", "label": "M", "included": True, "language": "en-US"}
    entry.update(extra)
    p.write_text(json.dumps({"version": 2, "libraries": [entry]}), encoding="utf-8")
    return load_libraries(str(p)).libraries[0]


def test_poster_source_defaults_to_tmdb_for_old_file(tmp_path):
    assert _write_with_source(tmp_path).poster_source == 0


def test_poster_source_round_trips(tmp_path):
    p = str(tmp_path / "sources.json")
    lib = Library(path="/m/", label="M", included=True, language="en-US",
                  fallback_language="", toggles=frozenset(), poster_source=1)
    assert save_libraries(p, LibraryConfig([lib])) is True
    assert load_libraries(p).libraries[0].poster_source == 1


def test_poster_source_out_of_range_reads_tmdb(tmp_path):
    for bad in (2, -1, "1", None, True):
        assert _write_with_source(tmp_path, poster_source=bad).poster_source == 0


def test_scan_settings_poster_source():
    for value, name in ((0, "tmdb"), (1, "fanart")):
        lib = Library(**{**_lib().__dict__, "poster_source": value})
        assert scan_settings(lib, {}, "en-US")["poster_source"] == name


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


def test_resolve_country_region_without_preset_is_no_country():
    # The library language has a region, so Kodi's interface language is
    # not consulted, even when the region has no rating preset.
    assert resolve_country(_lib(language="es-ES"), "en-US") is None
    assert resolve_country(_lib(language="nl-NL"), "de-DE") == "NL"
    assert resolve_country(_lib(language="en"), "de-DE") == "DE"


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


def test_library_from_legacy_settings_carries_the_global_options():
    m = library_from_legacy_settings(
        _legacy_settings(), path="/m/", label="M", included=True,
        language="de-DE", fallback_language="en-US")
    assert (m.path, m.label, m.included, m.language, m.fallback_language) == (
        "/m/", "M", True, "de-DE", "en-US")
    assert m.toggles == {"ratings", "plots", "credits"}
    assert (m.rating_source, m.text_source_order, m.fallback_order) == (2, 1, 1)
    assert (m.cert_prefix_override, m.cert_replace_foreign, m.cert_not_found) == (
        "FSK ", False, "NR")
    assert scan_settings(m, {}, "en-US")["cert_country"] == "DE"


def test_library_from_legacy_settings_without_country_stays_off():
    lib = library_from_legacy_settings(
        _legacy_settings(cert_country=None), path="/m/", label="", included=True,
        language="", fallback_language="")
    assert scan_settings(lib, {}, "nl-NL")["cert_country"] is None


def test_read_libraries_tells_the_file_states_apart(tmp_path):
    p = tmp_path / "sources.json"
    assert read_libraries(str(p)) == (None, FILE_MISSING)
    p.write_text(json.dumps({"sources": [{"path": "/m/"}]}))
    assert read_libraries(str(p)) == (None, FILE_LEGACY)
    for damaged in ("{not json", "[]", json.dumps({"version": 3, "libraries": []}),
                    json.dumps({"version": 2, "libraries": [{"label": "no path"}]}),
                    json.dumps({"version": 2, "libraries": ["x"]})):
        p.write_text(damaged)
        assert read_libraries(str(p)) == (None, FILE_DAMAGED), damaged
    cfg = LibraryConfig([new_library("/m/", "M", "en-US")])
    assert save_libraries(str(p), cfg)
    assert read_libraries(str(p)) == (cfg, FILE_OK)


def test_failed_save_leaves_the_old_file_whole(tmp_path, monkeypatch):
    p = str(tmp_path / "sources.json")
    old = LibraryConfig([new_library("/m/", "M", "en-US")])
    assert save_libraries(p, old)

    real_dump = json.dump

    def dump_then_fail(data, fh, **kw):
        real_dump({"version": 2, "partial": True}, fh, **kw)
        raise OSError("disk full")

    monkeypatch.setattr(json, "dump", dump_then_fail)
    assert save_libraries(p, LibraryConfig([new_library("/t/", "T", "en-US")])) is False
    monkeypatch.undo()
    assert load_libraries(p) == old
    assert sorted(f.name for f in tmp_path.iterdir()) == ["sources.json"]


def test_included_for_needs_library_and_subfolder_included():
    config = LibraryConfig([
        _lib("/m/", subfolders=({"path": "/m/kids/", "enabled": False},)),
        Library(**{**_lib("/t/").__dict__, "included": False}),
    ])
    assert config.included_for("/m/a.mkv") is config.libraries[0]
    assert config.included_for("/m/kids/a.mkv") is None
    assert config.included_for("/t/a.mkv") is None
    assert config.included_for("/x/a.mkv") is None

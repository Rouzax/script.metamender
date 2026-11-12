"""Tests for source configuration."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

from resources.lib.data.sources import (
    apply_folder_languages,
    discover_folders,
    folder_exceptions,
    kodi_ui_language,
    library_folders,
    load_source_config,
    match_source,
    save_source_config,
    source_paths,
)


def test_load_default_when_missing(tmp_path):
    # No default_language: the scan then treats every folder as
    # unconfigured (fill-only, Kodi's UI language).
    config = load_source_config(str(tmp_path / "sources.json"))
    assert config == {"sources": []}


def test_save_and_load(tmp_path):
    path = str(tmp_path / "sources.json")
    config = {
        "sources": [
            {"path": "smb://server/movies/", "label": "Movies",
             "enabled": True, "language": "en-US"}
        ],
        "default_language": "en-US",
    }
    save_source_config(path, config)
    loaded = load_source_config(path)
    assert len(loaded["sources"]) == 1
    assert loaded["sources"][0]["path"] == "smb://server/movies/"


def test_save_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "subdir" / "sources.json")
    save_source_config(path, {"sources": [], "default_language": "en-US"})
    assert os.path.exists(path)


def test_match_source_exact_prefix():
    sources = [
        {"path": "smb://server/movies/", "enabled": True, "language": "en-US"},
        {"path": "smb://server/tv/nl/", "enabled": True, "language": "nl-NL"},
    ]
    result = match_source("smb://server/tv/nl/show/s01e01.mkv", sources)
    assert result["language"] == "nl-NL"


def test_match_source_longest_prefix():
    sources = [
        {"path": "smb://server/tv/", "enabled": True, "language": "en-US"},
        {"path": "smb://server/tv/nl/", "enabled": True, "language": "nl-NL"},
    ]
    result = match_source("smb://server/tv/nl/show/file.mkv", sources)
    assert result["language"] == "nl-NL"


def test_match_source_no_match():
    sources = [
        {"path": "smb://server/movies/", "enabled": True, "language": "en-US"},
    ]
    result = match_source("smb://other/tv/file.mkv", sources)
    assert result == {"enabled": True}


def test_match_source_disabled():
    sources = [
        {"path": "smb://server/sports/", "enabled": False, "language": "en-US"},
    ]
    result = match_source("smb://server/sports/game.mkv", sources)
    assert result["enabled"] is False


def test_match_source_respects_path_boundary():
    # A source "/media/movies" (no trailing slash) must NOT match a sibling
    # directory "/media/movies_extra"; it must match "/media/movies/...".
    sources = [
        {"path": "/media/movies", "enabled": True, "language": "fr-FR"},
    ]
    sibling = match_source("/media/movies_extra/film.mkv", sources)
    assert "language" not in sibling  # fell through to the default

    inside = match_source("/media/movies/film.mkv", sources)
    assert inside["language"] == "fr-FR"


def test_kodi_ui_language_with_region():
    with patch("resources.lib.data.sources.xbmc.getLanguage", return_value="nl-NL") as get:
        assert kodi_ui_language() == "nl-NL"
    assert get.call_args.kwargs == {"region": True}


def test_kodi_ui_language_without_region_gets_the_primary_one():
    # Kodi answers "en-" when no region is set; that is no language code.
    for answer, expected in (("en-", "en-US"), ("nl-", "nl-NL"), ("de", "de-DE")):
        with patch("resources.lib.data.sources.xbmc.getLanguage", return_value=answer):
            assert kodi_ui_language() == expected


def test_kodi_ui_language_falls_back_to_en_us():
    for answer in ("", None, 3):
        with patch("resources.lib.data.sources.xbmc.getLanguage", return_value=answer):
            assert kodi_ui_language() == "en-US"


# =============================================================================
# Wizard helpers
# =============================================================================

_MP = "multipath://%2fa%2f/%2fb%2f/"


def test_source_paths_multipath():
    mp = "multipath://%2fmnt%2fa%2fFilms%2f/smb%3a%2f%2fnas%2fFilms%2f/"
    assert source_paths(mp) == ["/mnt/a/Films/", "smb://nas/Films/"]
    assert source_paths("/mnt/b/") == ["/mnt/b/"]


def test_apply_sets_defaults_and_exceptions_for_every_multipath_part():
    out = apply_folder_languages(
        {"sources": []},
        [{"path": _MP, "label": "Kids"}, {"path": "/c/", "label": "Films"}],
        "en-US", "", {_MP: ("nl-NL", "en-US")},
    )
    assert out["default_language"] == "en-US" and "fallback_language" not in out
    assert {"path": "/a/", "language": "nl-NL", "fallback_language": "en-US"} in out["sources"]
    assert {"path": "/b/", "language": "nl-NL", "fallback_language": "en-US"} in out["sources"]
    assert not any(e["path"] == "/c/" for e in out["sources"])


def test_apply_sets_top_level_fallback_and_omits_empty_entry_fallback():
    out = apply_folder_languages(
        {"sources": [], "fallback_language": "de-DE"},
        [{"path": "/c/", "label": "Films"}], "nl-NL", "en-US", {"/c/": ("fr-FR", "")},
    )
    assert out["fallback_language"] == "en-US"
    assert out["sources"] == [{"path": "/c/", "language": "fr-FR"}]


def test_apply_keeps_disabled_and_foreign_entries():
    cfg = {"sources": [{"path": "/c/", "enabled": False, "language": "de-DE"},
                       {"path": "/c/sub/", "language": "fr-FR"}]}
    out = apply_folder_languages(cfg, [{"path": "/c/", "label": "Films"}],
                                 "en-US", "", {})
    assert {"path": "/c/", "enabled": False} in out["sources"]
    assert {"path": "/c/sub/", "language": "fr-FR"} in out["sources"]


def test_apply_drops_emptied_entries_and_does_not_mutate_input():
    cfg = {"sources": [{"path": "/c/", "label": "Films", "enabled": True,
                        "language": "de-DE"}]}
    out = apply_folder_languages(cfg, [{"path": "/c/", "label": "Films"}],
                                 "en-US", "", {})
    assert out["sources"] == []
    assert cfg["sources"][0]["language"] == "de-DE"


def test_folder_exceptions_round_trip():
    disc = [{"path": _MP, "label": "Kids"}, {"path": "/c/", "label": "Films"}]
    out = apply_folder_languages({"sources": []}, disc, "en-US", "", {_MP: ("nl-NL", "")})
    assert folder_exceptions(out, disc) == {_MP: ("nl-NL", "")}


def test_save_source_config_reports_failure(tmp_path):
    target = tmp_path / "dir"
    target.mkdir()
    assert save_source_config(str(target), {"sources": []}) is False
    assert save_source_config(str(tmp_path / "s.json"), {"sources": []}) is True


def test_library_folders_from_show_and_movie_paths():
    movies = [
        "smb://h/Data/Movies/Alien (1979)/Alien.mkv",       # own folder
        "smb://h/Data/Movies/Heat (1995)/Heat.mkv",
        "smb://h/Data/Flat/a.mkv",                         # shared folder
        "smb://h/Data/Flat/b.mkv",
    ]
    shows = ["smb://h/Data/TV/EN/Lost/", "smb://h/Data/TV/NL/Flikken/",
             "smb://h/Data/TV/EN/Lost/Specials/"]
    assert library_folders(movies, shows) == [
        {"path": "smb://h/Data/Flat/", "label": "Data/Flat"},
        {"path": "smb://h/Data/Movies/", "label": "Data/Movies"},
        {"path": "smb://h/Data/TV/EN/", "label": "TV/EN"},
        {"path": "smb://h/Data/TV/NL/", "label": "TV/NL"},
    ]


def test_library_folders_windows_paths_and_stacks():
    movies = ["stack://D:\\Films\\X (1990)\\x-cd1.avi , D:\\Films\\X (1990)\\x-cd2.avi"]
    shows = ["D:\\Series\\Show\\"]
    assert library_folders(movies, shows) == [
        {"path": "D:\\Films\\", "label": "D:/Films"},
        {"path": "D:\\Series\\", "label": "D:/Series"},
    ]


def test_library_folders_skips_unusable_paths():
    assert library_folders(["", "plugin://x/y", "noslash.mkv"], [""]) == []


def _rpc(responses):
    def answer(raw):
        return json.dumps(responses[json.loads(raw)["method"]])
    return answer


def test_discover_folders_prefers_kodi_sources():
    rpc = _rpc({"Files.GetSources": {"result": {"sources": [
        {"file": "/m/", "label": "Films"}]}}})
    with patch("resources.lib.data.sources.xbmc.executeJSONRPC", side_effect=rpc):
        assert discover_folders() == [{"path": "/m/", "label": "Films"}]


def test_discover_folders_falls_back_to_the_library():
    rpc = _rpc({
        "Files.GetSources": {"result": {"sources": []}},
        "VideoLibrary.GetMovies": {"result": {"movies": [
            {"file": "/data/Movies/A (1)/a.mkv"}, {"file": "/data/Movies/B (2)/b.mkv"}]}},
        "VideoLibrary.GetTVShows": {"result": {"tvshows": [{"file": "/data/TV/Show/"}]}},
    })
    with patch("resources.lib.data.sources.xbmc.executeJSONRPC", side_effect=rpc):
        assert discover_folders() == [
            {"path": "/data/Movies/", "label": "data/Movies"},
            {"path": "/data/TV/", "label": "data/TV"},
        ]


def test_discover_folders_empty_library():
    rpc = _rpc({
        "Files.GetSources": {"result": {"sources": []}},
        "VideoLibrary.GetMovies": {"result": {"limits": {"total": 0}}},
        "VideoLibrary.GetTVShows": {"error": {"code": -32100}},
    })
    with patch("resources.lib.data.sources.xbmc.executeJSONRPC", side_effect=rpc):
        assert discover_folders() == []


def test_library_folders_in_use_expands_multipath_and_skips_empty_and_disabled(monkeypatch):
    from urllib.parse import quote

    from resources.lib.data import sources
    multi = "multipath://{}/{}/".format(
        quote("smb://user:secret@nas/Series/", safe=""), quote("smb://nas/Docs/", safe=""))
    monkeypatch.setattr(sources, "discover_folders", lambda: [
        {"path": multi, "label": "TV"},
        {"path": "smb://nas/Movies/", "label": "Movies"},
        {"path": "/media/usb/", "label": "USB stick"},
    ])
    files = {"movies": ["smb://nas/Movies/Alien (1979)/Alien.mkv"],
             "tvshows": ["smb://user:secret@nas/Series/Lost/"]}
    monkeypatch.setattr(sources, "_library_files", lambda method, key: files[key])
    config = [{"path": "smb://nas/Movies/", "enabled": False}]
    assert sources.library_folders_in_use(config) == ["smb://user:secret@nas/Series/"]
    assert sources.library_folders_in_use([]) == [
        "smb://user:secret@nas/Series/", "smb://nas/Movies/"]

"""Tests for source configuration."""
from __future__ import annotations

import json
from unittest.mock import patch

from resources.lib.data.sources import (
    discover_folders,
    kodi_ui_language,
    library_folders,
    load_source_config,
    source_paths,
)


def test_load_reads_a_1_1_file(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({"default_language": "en-US", "sources": [
        {"path": "smb://server/movies/", "language": "en-US"}]}))
    loaded = load_source_config(str(path))
    assert loaded["sources"][0]["path"] == "smb://server/movies/"


def test_load_default_when_missing(tmp_path):
    # No default_language: the scan then treats every folder as
    # unconfigured (fill-only, Kodi's UI language).
    config = load_source_config(str(tmp_path / "sources.json"))
    assert config == {"sources": []}


def test_path_matches_respects_the_path_boundary():
    # A folder "/media/movies" (no trailing slash) must NOT match a sibling
    # directory "/media/movies_extra"; it must match "/media/movies/...".
    from resources.lib.data.sources import _path_matches

    assert not _path_matches("/media/movies_extra/film.mkv", "/media/movies")
    assert _path_matches("/media/movies/film.mkv", "/media/movies")
    assert _path_matches("smb://s/m/film.mkv", "smb://s/m/")


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

def test_source_paths_multipath():
    mp = "multipath://%2fmnt%2fa%2fFilms%2f/smb%3a%2f%2fnas%2fFilms%2f/"
    assert source_paths(mp) == ["/mnt/a/Films/", "smb://nas/Films/"]
    assert source_paths("/mnt/b/") == ["/mnt/b/"]


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


def test_offline_check_uses_included_libraries(monkeypatch):
    from urllib.parse import quote

    from resources.lib.data import sources
    from resources.lib.data.libraries import Library, LibraryConfig, new_library

    def lib(path, included=True, subfolders=()):
        return Library(path=path, label="", included=included, language="en-US",
                       fallback_language="", toggles=frozenset({"plots"}),
                       subfolders=subfolders)

    multi = "multipath://{}/{}/".format(
        quote("smb://user:secret@nas/Series/", safe=""), quote("smb://nas/Docs/", safe=""))
    files = {"movies": ["smb://nas/Movies/Alien (1979)/Alien.mkv",
                        "smb://nas/Kids/Up (2009)/Up.mkv",
                        "smb://nas/Sport/Final.mkv"],
             "tvshows": ["smb://user:secret@nas/Series/Lost/"]}
    monkeypatch.setattr(sources, "_library_files", lambda method, key: files[key])
    config = LibraryConfig([
        lib(multi),
        lib("smb://nas/Movies/"),
        lib("smb://nas/Kids/", included=False),
        lib("/media/usb/"),
        new_library("smb://nas/New/", "New", "en-US"),
        lib("smb://nas/Sport/", subfolders=({"path": "smb://nas/Sport/", "enabled": False},)),
    ])
    assert sources.library_folders_in_use(config) == [
        "smb://user:secret@nas/Series/", "smb://nas/Movies/"]

"""Tests for source configuration."""
from __future__ import annotations

import os

from unittest.mock import patch

from resources.lib.data.sources import (
    kodi_ui_language,
    load_source_config,
    match_source,
    save_source_config,
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


def test_kodi_ui_language_falls_back_to_en_us():
    for answer in ("", None, 3):
        with patch("resources.lib.data.sources.xbmc.getLanguage", return_value=answer):
            assert kodi_ui_language() == "en-US"

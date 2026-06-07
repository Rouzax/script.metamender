"""Tests for source configuration."""
from __future__ import annotations

import os

from resources.lib.data.sources import (
    load_source_config,
    match_source,
    save_source_config,
)


def test_load_default_when_missing(tmp_path):
    config = load_source_config(str(tmp_path / "sources.json"))
    assert config["sources"] == []
    assert config["default_language"] == "en-US"


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
    assert result["enabled"] is True
    assert result["language"] == "en-US"


def test_match_source_disabled():
    sources = [
        {"path": "smb://server/sports/", "enabled": False, "language": "en-US"},
    ]
    result = match_source("smb://server/sports/game.mkv", sources)
    assert result["enabled"] is False

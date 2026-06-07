"""Tests for Kodi JSON-RPC data layer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from resources.lib.data.kodi import get_all_items, update_item, _build_properties
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW


def test_get_all_movies(kodi_stubs):
    response = {
        "result": {
            "movies": [
                {
                    "movieid": 1,
                    "title": "Test Movie",
                    "rating": 7.5,
                    "votes": "1000",
                    "plot": "A test plot",
                    "uniqueid": {"tmdb": "12345", "imdb": "tt1234567"},
                    "file": "smb://server/movies/test.mkv",
                    "dateadded": "2026-01-01 12:00:00",
                    "premiered": "2025-06-15",
                    "mpaa": "",
                    "tagline": "",
                    "director": [],
                    "writer": [],
                    "genre": ["Action"],
                    "studio": [],
                    "country": [],
                    "trailer": "",
                    "runtime": 120,
                },
            ],
            "limits": {"start": 0, "end": 1, "total": 1},
        }
    }
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(MOVIE)
    assert len(items) == 1
    assert items[0]["title"] == "Test Movie"
    assert items[0]["uniqueid"]["tmdb"] == "12345"


def test_get_all_movies_empty(kodi_stubs):
    response = {"result": {"movies": [], "limits": {"start": 0, "end": 0, "total": 0}}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(MOVIE)
    assert items == []


def test_get_all_movies_no_result_key(kodi_stubs):
    response = {"result": {"limits": {"start": 0, "end": 0, "total": 0}}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(MOVIE)
    assert items == []


def test_get_all_episodes(kodi_stubs):
    response = {
        "result": {
            "episodes": [
                {
                    "episodeid": 100,
                    "title": "Pilot",
                    "showtitle": "Test Show",
                    "season": 1,
                    "episode": 1,
                    "rating": 8.0,
                    "votes": "500",
                    "plot": "First episode",
                    "uniqueid": {"tmdb": "99999"},
                    "file": "smb://server/tv/test/s01e01.mkv",
                    "dateadded": "2026-01-01 12:00:00",
                    "firstaired": "2025-03-01",
                    "mpaa": "",
                    "director": [],
                    "writer": [],
                    "runtime": 45,
                },
            ],
            "limits": {"start": 0, "end": 1, "total": 1},
        }
    }
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(EPISODE)
    assert len(items) == 1
    assert items[0]["showtitle"] == "Test Show"
    assert items[0]["episodeid"] == 100


def test_update_item_success(kodi_stubs):
    response = {"result": "OK"}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    result = update_item(1, MOVIE, title="Test Movie", rating=7.5, plot="Updated plot")
    assert result is True
    call_args = kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0]
    payload = json.loads(call_args)
    assert payload["method"] == "VideoLibrary.SetMovieDetails"
    assert payload["params"]["movieid"] == 1
    assert payload["params"]["rating"] == 7.5
    assert payload["params"]["plot"] == "Updated plot"


def test_update_item_failure(kodi_stubs):
    response = {"error": {"code": -1, "message": "Invalid params"}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    result = update_item(1, MOVIE, title="Test Movie", rating=7.5)
    assert result is False


def test_build_properties_movie():
    props = _build_properties(MOVIE)
    assert "title" in props
    assert "premiered" in props
    assert "tagline" in props
    assert "trailer" in props
    assert "runtime" in props
    assert "genre" in props
    assert "firstaired" not in props
    assert "showtitle" not in props


def test_build_properties_tvshow():
    props = _build_properties(TVSHOW)
    assert "title" in props
    assert "premiered" in props
    assert "genre" in props
    assert "tagline" not in props
    assert "trailer" not in props
    assert "showtitle" not in props


def test_build_properties_episode():
    props = _build_properties(EPISODE)
    assert "title" in props
    assert "firstaired" in props
    assert "showtitle" in props
    assert "season" in props
    assert "episode" in props
    assert "director" in props
    assert "writer" in props
    assert "runtime" in props
    assert "premiered" not in props
    assert "tagline" not in props


def test_get_all_items_with_filter(kodi_stubs):
    response = {
        "result": {
            "movies": [
                {"movieid": 1, "title": "Air"},
            ],
            "limits": {"start": 0, "end": 1, "total": 1},
        }
    }
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(
        MOVIE,
        kodi_filter={"field": "title", "operator": "is", "value": "Air"},
    )
    assert len(items) == 1
    call_args = kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0]
    payload = json.loads(call_args)
    assert "filter" in payload["params"]
    assert payload["params"]["filter"]["value"] == "Air"


def test_get_all_items_with_tvshowid(kodi_stubs):
    response = {
        "result": {
            "episodes": [
                {"episodeid": 1, "title": "Pilot"},
            ],
            "limits": {"start": 0, "end": 1, "total": 1},
        }
    }
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(EPISODE, tvshowid=2, season=1)
    assert len(items) == 1
    call_args = kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0]
    payload = json.loads(call_args)
    assert payload["params"]["tvshowid"] == 2
    assert payload["params"]["season"] == 1
    assert "filter" not in payload["params"]


def test_get_all_items_no_filter(kodi_stubs):
    response = {"result": {"movies": [], "limits": {"start": 0, "end": 0, "total": 0}}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    get_all_items(MOVIE)
    call_args = kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0]
    payload = json.loads(call_args)
    assert "filter" not in payload["params"]
    assert "tvshowid" not in payload["params"]

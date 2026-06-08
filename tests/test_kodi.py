"""Tests for Kodi JSON-RPC data layer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from resources.lib.data.kodi import (
    get_all_items,
    get_item_by_id,
    update_item,
    _build_properties,
)
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


def test_get_item_by_id_returns_item(kodi_stubs):
    response = {
        "result": {
            "moviedetails": {
                "movieid": 7,
                "title": "Air",
                "plot": "Existing plot",
                "uniqueid": {"tmdb": "12345"},
            }
        }
    }
    mock = MagicMock(return_value=json.dumps(response))
    kodi_stubs["xbmc"].executeJSONRPC = mock
    item = get_item_by_id(MOVIE, 7)
    assert item is not None
    assert item["title"] == "Air"
    assert item["movieid"] == 7
    # The request must use the GetMovieDetails method with the movieid param.
    sent = json.loads(mock.call_args[0][0])
    assert sent["method"] == "VideoLibrary.GetMovieDetails"
    assert sent["params"]["movieid"] == 7


def test_get_item_by_id_missing_returns_none(kodi_stubs):
    response = {"error": {"code": -32602, "message": "Invalid params"}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps(response))
    assert get_item_by_id(MOVIE, 999) is None


def test_get_item_by_id_episode_detail_key(kodi_stubs):
    response = {"result": {"episodedetails": {"episodeid": 5, "title": "Pilot"}}}
    mock = MagicMock(return_value=json.dumps(response))
    kodi_stubs["xbmc"].executeJSONRPC = mock
    item = get_item_by_id(EPISODE, 5)
    assert item["title"] == "Pilot"
    sent = json.loads(mock.call_args[0][0])
    assert sent["method"] == "VideoLibrary.GetEpisodeDetails"
    assert sent["params"]["episodeid"] == 5


def test_get_all_items_raises_on_rpc_error(kodi_stubs):
    from resources.lib.utils import KodiQueryError
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps({"error": {"code": -32603, "message": "boom"}})
    )
    with pytest.raises(KodiQueryError):
        get_all_items(MOVIE)


def test_get_all_items_raises_on_exception(kodi_stubs):
    from resources.lib.utils import KodiQueryError
    def boom(_):
        raise RuntimeError("rpc down")
    kodi_stubs["xbmc"].executeJSONRPC = boom
    with pytest.raises(KodiQueryError):
        get_all_items(MOVIE)


def test_get_all_items_empty_is_not_error(kodi_stubs):
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps({"result": {"movies": []}})
    )
    assert get_all_items(MOVIE) == []


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
    result = update_item(1, MOVIE, log_title="Test Movie", rating=7.5, plot="Updated plot")
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
    result = update_item(1, MOVIE, log_title="Test Movie", rating=7.5)
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


def test_get_all_movies_requests_ratings_property(kodi_stubs):
    response = {"result": {"movies": []}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    get_all_items(MOVIE)
    call_args = kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0]
    payload = json.loads(call_args)
    props = payload["params"]["properties"]
    assert "ratings" in props
    # plot must remain requested: it is a backfill field and the empty-only
    # overwrite guard depends on reading the current value (regression guard).
    assert "plot" in props


def test_get_all_items_warns_at_ceiling(kodi_stubs, monkeypatch):
    import json as _j
    from resources.lib.data import kodi
    from resources.lib.data.media_types import MOVIE
    big = {"result": {"movies": [{"movieid": i} for i in range(5)],
                      "limits": {"start": 0, "end": 99999, "total": 99999}}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=_j.dumps(big))
    warnings = []
    monkeypatch.setattr(kodi.log, "warning", lambda *a, **k: warnings.append((a, k)))
    kodi.get_all_items(MOVIE)
    assert any(k.get("event") == "kodi.truncated" for a, k in warnings)

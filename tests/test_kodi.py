"""Tests for Kodi JSON-RPC data layer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from resources.lib.data.kodi import (
    get_all_items,
    get_item_by_id,
    flatten_art,
    update_item,
    _build_properties,
)
from resources.lib.data.media_types import EPISODE, MOVIE, SEASON, SET, TVSHOW


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
    assert "tvshowid" in props
    assert "season" in props
    assert "episode" in props
    assert "director" in props
    assert "writer" in props
    assert "runtime" in props
    assert "premiered" not in props
    assert "tagline" not in props


def test_get_all_items_ids_only_asks_for_no_properties(kodi_stubs):
    response = {
        "result": {
            "movies": [{"movieid": 1, "label": "Air"}],
            "limits": {"start": 0, "end": 1, "total": 1},
        }
    }
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps(response)
    )
    items = get_all_items(MOVIE, ids_only=True)
    assert items == [{"movieid": 1, "label": "Air"}]
    payload = json.loads(kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0])
    assert payload["params"]["properties"] == []


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


def _answers(by_method):
    def answer(raw):
        return json.dumps(by_method.get(json.loads(raw)["method"], {"error": {"code": -32602}}))
    return MagicMock(side_effect=answer)


def test_get_season_info(kodi_stubs):
    from resources.lib.data.kodi import get_season_info
    kodi_stubs["xbmc"].executeJSONRPC = _answers({"VideoLibrary.GetSeasonDetails": {
        "result": {"seasondetails": {"seasonid": 31, "tvshowid": 7, "season": 2}}}})
    assert get_season_info(31) == (7, 2)


def test_get_season_info_missing(kodi_stubs):
    from resources.lib.data.kodi import get_season_info
    kodi_stubs["xbmc"].executeJSONRPC = _answers({})
    assert get_season_info(31) is None


def test_get_episode_show_id(kodi_stubs):
    from resources.lib.data.kodi import get_episode_show_id
    kodi_stubs["xbmc"].executeJSONRPC = _answers({"VideoLibrary.GetEpisodeDetails": {
        "result": {"episodedetails": {"episodeid": 100, "tvshowid": 7}}}})
    assert get_episode_show_id(100) == 7


def test_build_properties_season_has_no_file_or_uniqueid():
    assert _build_properties(SEASON) == [
        "season", "showtitle", "tvshowid", "art", "title",
    ]


@pytest.mark.parametrize("media_type", [MOVIE, TVSHOW, EPISODE])
def test_build_properties_request_art(media_type):
    assert "art" in _build_properties(media_type)


def test_flatten_art_unwraps_poster():
    item = {"art": {"poster": "image://https%3a%2f%2fa%2fb.jpg/", "fanart": "x"}}
    assert flatten_art(item, MOVIE)["poster"] == "https://a/b.jpg"
    assert flatten_art({}, MOVIE)["poster"] == ""
    assert flatten_art({"art": {"thumb": "t"}}, EPISODE)["thumb"] == "t"
    assert "thumb" not in flatten_art({"art": {"thumb": "t"}}, MOVIE)


def test_get_all_items_flattens_art(kodi_stubs):
    response = {"result": {"movies": [
        {"movieid": 1, "art": {"poster": "image://https%3a%2f%2fa%2fb.jpg/"}},
    ]}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps(response))
    assert get_all_items(MOVIE)[0]["poster"] == "https://a/b.jpg"


def test_get_item_by_id_flattens_art(kodi_stubs):
    response = {"result": {"seasondetails": {
        "seasonid": 5, "art": {"poster": "image://https%3a%2f%2fa%2fb.jpg/"},
    }}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps(response))
    assert get_item_by_id(SEASON, 5)["poster"] == "https://a/b.jpg"


def _sent_params(kodi_stubs):
    return json.loads(kodi_stubs["xbmc"].executeJSONRPC.call_args[0][0])["params"]


def test_update_item_sends_art_param(kodi_stubs):
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps({"result": "OK"}))
    assert update_item(1, MOVIE, "x", poster="https://a/b.jpg", plot="p") is True
    assert _sent_params(kodi_stubs) == {
        "movieid": 1, "plot": "p", "art": {"poster": "https://a/b.jpg"},
    }


def test_update_item_empty_art_sends_null(kodi_stubs):
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps({"result": "OK"}))
    update_item(2, EPISODE, "x", thumb="")
    assert _sent_params(kodi_stubs) == {"episodeid": 2, "art": {"thumb": None}}


def test_get_all_seasons_drops_all_seasons_entry(kodi_stubs):
    response = {"result": {"seasons": [
        {"seasonid": -1, "season": -1, "art": {}},
        {"seasonid": 7, "season": 1, "art": {"poster": "p"}},
    ]}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps(response))
    items = get_all_items(SEASON, tvshowid=3)
    assert [i["seasonid"] for i in items] == [7]
    assert items[0]["poster"] == "p"


_SHOWS = {"result": {"tvshows": [
    {"tvshowid": 3, "title": "Lost", "file": "smb://nas/Series/Lost/", "art": {}},
    {"tvshowid": 4, "title": "Dark", "file": "smb://nas/Series/Dark/", "art": {}},
]}}
_SEASONS = {"result": {"seasons": [
    {"seasonid": 30, "season": 0, "tvshowid": 3, "showtitle": "Lost", "art": {}},
    {"seasonid": 31, "season": 1, "tvshowid": 3, "showtitle": "Lost", "art": {}},
    {"seasonid": 41, "season": 1, "tvshowid": 4, "showtitle": "Dark", "art": {}},
]}}


def test_get_seasons_for_scan_enriches_with_the_show(kodi_stubs):
    from resources.lib.data.kodi import get_seasons_for_scan
    kodi_stubs["xbmc"].executeJSONRPC = _answers({
        "VideoLibrary.GetTVShows": _SHOWS, "VideoLibrary.GetSeasons": _SEASONS})
    items = get_seasons_for_scan({3: "4607"}, show_tvdb_map={3: "73739"})
    assert [(i["seasonid"], i["file"], i["show_tmdb_id"], i["show_tvdb_id"])
            for i in items] == [
        (30, "smb://nas/Series/Lost/", "4607", "73739"),
        (31, "smb://nas/Series/Lost/", "4607", "73739"),
        (41, "smb://nas/Series/Dark/", None, None),
    ]
    # Kodi has no date on a season: the scan supplies it.
    assert all("firstaired" not in i for i in items)


def test_seasons_for_scan_list_show_seasons(kodi_stubs):
    from resources.lib.data.kodi import get_seasons_for_scan
    seasons = {"result": {"seasons": _SEASONS["result"]["seasons"] + [
        {"seasonid": 32, "season": -1, "tvshowid": 3, "showtitle": "Lost", "art": {}},
        {"seasonid": 33, "season": 2, "tvshowid": 3, "showtitle": "Lost", "art": {}},
    ]}}
    kodi_stubs["xbmc"].executeJSONRPC = _answers({
        "VideoLibrary.GetTVShows": _SHOWS, "VideoLibrary.GetSeasons": seasons})
    items = get_seasons_for_scan({3: "4607"})
    assert {i["seasonid"]: i["show_seasons"] for i in items} == {
        30: [0, 1, 2], 31: [0, 1, 2], 33: [0, 1, 2], 41: [1]}


def test_get_seasons_for_scan_of_one_show(kodi_stubs):
    from resources.lib.data.kodi import get_seasons_for_scan
    show = {"result": {"tvshowdetails": _SHOWS["result"]["tvshows"][0]}}
    seasons = {"result": {"seasons": _SEASONS["result"]["seasons"][:2]}}
    kodi_stubs["xbmc"].executeJSONRPC = _answers({
        "VideoLibrary.GetTVShowDetails": show, "VideoLibrary.GetSeasons": seasons})
    items = get_seasons_for_scan({3: "4607"}, tvshowid=3)
    sent = [json.loads(c.args[0]) for c in kodi_stubs["xbmc"].executeJSONRPC.call_args_list]
    assert [s["params"].get("tvshowid") for s in sent] == [3, 3]
    assert [i["show_tmdb_id"] for i in items] == ["4607", "4607"]


@pytest.mark.parametrize("media_type", [MOVIE, TVSHOW, EPISODE])
def test_build_properties_without_art(media_type):
    props = _build_properties(media_type, art=False)
    assert "art" not in props
    assert props == [p for p in _build_properties(media_type) if p != "art"]


def test_get_all_items_without_art_requests_and_flattens_none(kodi_stubs):
    response = {"result": {"episodes": [{"episodeid": 1}]}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps(response))
    items = get_all_items(EPISODE, art=False)
    assert "art" not in _sent_params(kodi_stubs)["properties"]
    assert "thumb" not in items[0]


def test_get_item_by_id_without_art(kodi_stubs):
    response = {"result": {"moviedetails": {"movieid": 1}}}
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(return_value=json.dumps(response))
    item = get_item_by_id(MOVIE, 1, art=False)
    assert "art" not in _sent_params(kodi_stubs)["properties"]
    assert "poster" not in item


def test_get_seasons_for_scan_lists_shows_without_art(kodi_stubs):
    from resources.lib.data.kodi import get_seasons_for_scan
    kodi_stubs["xbmc"].executeJSONRPC = _answers({
        "VideoLibrary.GetTVShows": _SHOWS, "VideoLibrary.GetSeasons": _SEASONS})
    get_seasons_for_scan({3: "4607"})
    sent = {json.loads(c.args[0])["method"]: json.loads(c.args[0])["params"]["properties"]
            for c in kodi_stubs["xbmc"].executeJSONRPC.call_args_list}
    assert "art" not in sent["VideoLibrary.GetTVShows"]
    assert "art" in sent["VideoLibrary.GetSeasons"]


def test_flatten_art_exposes_fanart_but_not_extra_fanart():
    item = {"art": {"fanart": "image://https%3a%2f%2fa%2fbd.jpg/", "fanart1": "smb://x/f1.jpg",
                    "clearlogo": "image://https%3a%2f%2fa%2flogo.png/"}}
    flat = flatten_art(item, MOVIE)
    assert flat["fanart"] == "https://a/bd.jpg"
    assert flat["clearlogo"] == "https://a/logo.png"
    assert flat["banner"] == ""
    assert "fanart1" not in flat


def test_update_item_sends_only_the_given_art_keys(kodi_stubs, monkeypatch):
    # monkeypatch puts the shared stub back, so later tests do not get "OK"
    # for a listing.
    monkeypatch.setattr(kodi_stubs["xbmc"], "executeJSONRPC",
                        MagicMock(return_value=json.dumps({"result": "OK"})))
    assert update_item(1, MOVIE, "x", fanart="https://a/bd.jpg", clearlogo="") is True
    assert _sent_params(kodi_stubs) == {
        "movieid": 1, "art": {"fanart": "https://a/bd.jpg", "clearlogo": None},
    }


def test_build_properties_set():
    assert _build_properties(SET) == ["title", "plot", "art"]


def test_get_set_members_filters_unset(kodi_stubs):
    from resources.lib.data.kodi import get_set_members
    kodi_stubs["xbmc"].executeJSONRPC = _answers({"VideoLibrary.GetMovies": {"result": {
        "movies": [
            {"movieid": 1, "setid": 4, "set": "Shrek Collection", "file": "a.mkv"},
            {"movieid": 2, "setid": 0, "set": "", "file": "b.mkv"},
            {"movieid": 3, "set": "", "file": "c.mkv"},
        ]}}})
    members = get_set_members()
    sent = json.loads(kodi_stubs["xbmc"].executeJSONRPC.call_args.args[0])
    assert sent["params"]["properties"] == [
        "title", "set", "setid", "file", "uniqueid", "premiered"]
    assert [m["movieid"] for m in members] == [1]


def test_get_set_members_raises_on_error(kodi_stubs):
    from resources.lib.data.kodi import get_set_members
    from resources.lib.utils import KodiQueryError
    kodi_stubs["xbmc"].executeJSONRPC = _answers({})
    with pytest.raises(KodiQueryError):
        get_set_members()


def test_get_movie_setid(kodi_stubs):
    from resources.lib.data.kodi import get_movie_setid
    kodi_stubs["xbmc"].executeJSONRPC = _answers({"VideoLibrary.GetMovieDetails": {
        "result": {"moviedetails": {"movieid": 7, "setid": 4}}}})
    assert get_movie_setid(7) == 4
    kodi_stubs["xbmc"].executeJSONRPC = _answers({"VideoLibrary.GetMovieDetails": {
        "result": {"moviedetails": {"movieid": 7, "setid": 0}}}})
    assert get_movie_setid(7) == 0
    kodi_stubs["xbmc"].executeJSONRPC = _answers({})
    assert get_movie_setid(7) is None

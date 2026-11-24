"""Tests for the fanart.tv provider module (HTTP helper mocked)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from resources.lib.engine.art import Poster
from resources.lib.providers import fanart
from resources.lib.utils import ApiKeyError, FanartAuthError, ProviderAuthError

_GET = "resources.lib.providers.fanart.provider_get"
_FIXTURES = Path(__file__).parent / "fixtures" / "fanart"

_MOVIE_KEYS = {"poster", "fanart", "clearlogo", "clearart", "landscape", "banner", "discart"}
_SHOW_KEYS = {"poster", "fanart", "clearlogo", "clearart", "landscape", "banner", "characterart"}


def _item(url, lang="en", likes="3", **extra):
    return {"id": "1", "url": url, "lang": lang, "likes": likes, **extra}


def test_fanart_movie_posters_parse():
    body = {
        "movieposter": [_item("https://a/1.jpg", "00", "1"), _item("https://a/2.jpg", "nl", "12")],
        "hdmovielogo": [_item("https://a/logo.png")],
    }
    with patch(_GET, return_value=body) as get:
        result = fanart.fetch_movie_art(550, "tt0137523", "pk", "")
    assert get.call_args.args[0].endswith("/movies/550")
    assert result["poster"] == [
        Poster("https://a/1.jpg", "fanart", None, (1,)),
        Poster("https://a/2.jpg", "fanart", "nl", (12,)),
    ]
    assert result["clearlogo"] == [Poster("https://a/logo.png", "fanart", "en", (3,))]


def test_fanart_movie_falls_back_to_imdb_id():
    with patch(_GET, return_value={}) as get:
        assert fanart.fetch_movie_art(None, "tt0137523", "pk", "")["poster"] == []
    assert get.call_args.args[0].endswith("/movies/tt0137523")


def test_fanart_movie_without_any_id_is_none():
    with patch(_GET) as get:
        assert fanart.fetch_movie_art(None, None, "pk", "") is None
    get.assert_not_called()


def test_fanart_show_posters_split_by_season():
    body = {
        "tvposter": [_item("https://a/s.jpg", "", "4")],
        "seasonposter": [
            _item("https://a/1.jpg", "en", "2", season="1"),
            _item("https://a/1b.jpg", "00", "5", season="1"),
            _item("https://a/2.jpg", "en", "1", season="2"),
            _item("https://a/all.jpg", "en", "9", season="all"),
        ],
    }
    with patch(_GET, return_value=body) as get:
        result = fanart.fetch_show_art(81189, "pk", "")
    assert get.call_args.args[0].endswith("/tv/81189")
    assert result["show"]["poster"] == [Poster("https://a/s.jpg", "fanart", None, (4,))]
    assert result["seasons"] == {
        1: [Poster("https://a/1.jpg", "fanart", "en", (2,)),
            Poster("https://a/1b.jpg", "fanart", None, (5,))],
        2: [Poster("https://a/2.jpg", "fanart", "en", (1,))],
    }


def test_fanart_unknown_id_is_no_posters():
    with patch(_GET, return_value={}):
        shows = fanart.fetch_show_art(1, "pk", "")
        movies = fanart.fetch_movie_art(1, None, "pk", "")
    assert shows["seasons"] == {} and set(shows["show"]) == _SHOW_KEYS
    assert not any(shows["show"].values()) and not any(movies.values())


def test_fanart_404_returns_none():
    with patch(_GET, return_value=None):
        assert fanart.fetch_show_art(1, "pk", "") is None
        assert fanart.fetch_movie_art(1, None, "pk", "") is None


@pytest.mark.parametrize("client_key, expected", [
    ("", {"api_key": "pk"}),
    ("ck", {"api_key": "pk", "client_key": "ck"}),
])
def test_fanart_sends_client_key_only_when_set(client_key, expected):
    with patch(_GET, return_value={}) as get:
        fanart.fetch_show_art(1, "pk", client_key)
    assert get.call_args.kwargs["params"] == expected


def test_fanart_401_raises_fanart_auth_error():
    with patch(_GET, side_effect=ApiKeyError("fanart")), \
            pytest.raises(FanartAuthError) as info:
        fanart.fetch_movie_art(1, None, "pk", "")
    assert isinstance(info.value, ProviderAuthError)
    assert info.value.provider == "fanart"


def test_fanart_bad_items_are_skipped():
    body = {"movieposter": [_item(""), _item("https://a/x.jpg", likes="many"),
                            _item("https://a/ok.jpg", likes="0")]}
    with patch(_GET, return_value=body):
        result = fanart.fetch_movie_art(1, None, "pk", "")
    assert result["poster"] == [Poster("https://a/ok.jpg", "fanart", "en", (0,))]


def test_fanart_non_object_body_is_transient():
    from resources.lib.utils import TransientProviderError
    with patch(_GET, return_value=["x"]), pytest.raises(TransientProviderError):
        fanart.fetch_show_art(1, "pk", "")


def _fixture(name):
    return json.loads((_FIXTURES / name).read_text())


def test_fanart_movie_art_maps_every_type():
    with patch(_GET, return_value=_fixture("movie_art.json")):
        art = fanart.fetch_movie_art(603, None, "pk", "")
    assert set(art) == _MOVIE_KEYS
    assert all(art[key] for key in _MOVIE_KEYS)
    # hdmovielogo first, the SD movielogo after it.
    assert [p.sd for p in art["clearlogo"]] == [False, False, False, True]
    assert [p.sd for p in art["clearart"]] == [False, False, False, True]
    assert not any(p.sd for key in ("poster", "fanart", "landscape", "banner", "discart")
                   for p in art[key])


def test_fanart_show_art_keeps_seasons_and_characterart():
    with patch(_GET, return_value=_fixture("show_art.json")):
        art = fanart.fetch_show_art(81189, "pk", "")
    assert set(art["show"]) == _SHOW_KEYS
    assert all(art["show"][key] for key in _SHOW_KEYS)
    assert [p.sd for p in art["show"]["clearlogo"]] == [False] * 3 + [True] * 3
    assert art["seasons"] and all(art["seasons"].values())


def test_fanart_background_00_is_textless():
    body = {"moviebackground": [_item("https://a/b0.jpg", "", "1"),
                                _item("https://a/b1.jpg", "00", "2"),
                                _item("https://a/b2.jpg", "en", "3")]}
    with patch(_GET, return_value=body):
        art = fanart.fetch_movie_art(1, None, "pk", "")
    assert [p.lang for p in art["fanart"]] == [None, None, "en"]


def test_fanart_collection_art_is_asked_on_the_movies_endpoint():
    with patch(_GET, return_value={}) as get:
        fanart.fetch_movie_art("86311", None, "pk", "")
    assert get.call_args.args[0].endswith("/movies/86311")

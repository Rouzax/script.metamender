"""Tests for the fanart.tv provider module (HTTP helper mocked)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from resources.lib.engine.art import Poster
from resources.lib.providers import fanart
from resources.lib.utils import ApiKeyError, FanartAuthError, ProviderAuthError

_GET = "resources.lib.providers.fanart.provider_get"


def _item(url, lang="en", likes="3", **extra):
    return {"id": "1", "url": url, "lang": lang, "likes": likes, **extra}


def test_fanart_movie_posters_parse():
    body = {
        "movieposter": [_item("https://a/1.jpg", "00", "1"), _item("https://a/2.jpg", "nl", "12")],
        "hdmovielogo": [_item("https://a/logo.png")],
    }
    with patch(_GET, return_value=body) as get:
        result = fanart.fetch_movie_posters(550, "tt0137523", "pk", "")
    assert get.call_args.args[0].endswith("/movies/550")
    assert result == [
        Poster("https://a/1.jpg", "fanart", None, (1,)),
        Poster("https://a/2.jpg", "fanart", "nl", (12,)),
    ]


def test_fanart_movie_falls_back_to_imdb_id():
    with patch(_GET, return_value={}) as get:
        assert fanart.fetch_movie_posters(None, "tt0137523", "pk", "") == []
    assert get.call_args.args[0].endswith("/movies/tt0137523")


def test_fanart_movie_without_any_id_is_none():
    with patch(_GET) as get:
        assert fanart.fetch_movie_posters(None, None, "pk", "") is None
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
        result = fanart.fetch_show_posters(81189, "pk", "")
    assert get.call_args.args[0].endswith("/tv/81189")
    assert result == {
        "show": [Poster("https://a/s.jpg", "fanart", None, (4,))],
        "seasons": {
            1: [Poster("https://a/1.jpg", "fanart", "en", (2,)),
                Poster("https://a/1b.jpg", "fanart", None, (5,))],
            2: [Poster("https://a/2.jpg", "fanart", "en", (1,))],
        },
    }


def test_fanart_unknown_id_is_no_posters():
    with patch(_GET, return_value={}):
        assert fanart.fetch_show_posters(1, "pk", "") == {"show": [], "seasons": {}}
        assert fanart.fetch_movie_posters(1, None, "pk", "") == []


def test_fanart_404_returns_none():
    with patch(_GET, return_value=None):
        assert fanart.fetch_show_posters(1, "pk", "") is None
        assert fanart.fetch_movie_posters(1, None, "pk", "") is None


@pytest.mark.parametrize("client_key, expected", [
    ("", {"api_key": "pk"}),
    ("ck", {"api_key": "pk", "client_key": "ck"}),
])
def test_fanart_sends_client_key_only_when_set(client_key, expected):
    with patch(_GET, return_value={}) as get:
        fanart.fetch_show_posters(1, "pk", client_key)
    assert get.call_args.kwargs["params"] == expected


def test_fanart_401_raises_fanart_auth_error():
    with patch(_GET, side_effect=ApiKeyError("fanart")), \
            pytest.raises(FanartAuthError) as info:
        fanart.fetch_movie_posters(1, None, "pk", "")
    assert isinstance(info.value, ProviderAuthError)
    assert info.value.provider == "fanart"


def test_fanart_bad_items_are_skipped():
    body = {"movieposter": [_item(""), _item("https://a/x.jpg", likes="many"),
                            _item("https://a/ok.jpg", likes="0")]}
    with patch(_GET, return_value=body):
        result = fanart.fetch_movie_posters(1, None, "pk", "")
    assert result == [Poster("https://a/ok.jpg", "fanart", "en", (0,))]


def test_fanart_non_object_body_is_transient():
    from resources.lib.utils import TransientProviderError
    with patch(_GET, return_value=["x"]), pytest.raises(TransientProviderError):
        fanart.fetch_show_posters(1, "pk", "")

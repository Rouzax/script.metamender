"""Tests for the TheTVDB v4 provider module (HTTP helpers mocked)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from resources.lib import constants
from resources.lib.providers import tvdb
from resources.lib.utils import ApiKeyError, TransientProviderError, TvdbAuthError

LOGIN_OK = {"status": "success", "data": {"token": "tok-1"}}


@pytest.fixture(autouse=True)
def _fresh_token():
    tvdb._reset_token()
    yield
    tvdb._reset_token()


def _router(routes: Dict[str, Any], calls: Optional[List[Any]] = None):
    """provider_get stand-in: the first route whose key is in the URL wins."""

    def fake(url, logger, label, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        for key, value in routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                if callable(value):
                    return value(url)
                return value
        raise AssertionError("unrouted url " + url)

    return fake


def _post_ok(*args, **kwargs):
    return LOGIN_OK


def test_login_once_and_reuse_token():
    gets: List[Any] = []
    with patch.object(tvdb, "provider_post", side_effect=_post_ok) as post, \
            patch.object(tvdb, "provider_get", side_effect=_router(
                {"/series/1": {"data": {"name": "A"}}}, gets)):
        tvdb._get("/series/1")
        tvdb._get("/series/1")
    assert post.call_count == 1
    assert post.call_args[0][3] == {"apikey": constants.TVDB_API_KEY}
    assert post.call_args[1]["provider"] == "tvdb"
    assert all(g[1]["headers"] == {"Authorization": "Bearer tok-1"} for g in gets)


def test_401_relogs_once_then_succeeds():
    seen = {"n": 0}

    def series(url):
        seen["n"] += 1
        if seen["n"] == 1:
            raise ApiKeyError("tvdb")
        return {"data": {}}

    with patch.object(tvdb, "provider_post", side_effect=_post_ok) as post, \
            patch.object(tvdb, "provider_get", side_effect=_router({"/series/1": series})):
        assert tvdb._get("/series/1") == {"data": {}}
    assert post.call_count == 2


def test_401_relogs_once_then_auth_error():
    with patch.object(tvdb, "provider_post", side_effect=_post_ok) as post, \
            patch.object(tvdb, "provider_get",
                         side_effect=_router({"/series/1": ApiKeyError("tvdb")})):
        with pytest.raises(TvdbAuthError):
            tvdb._get("/series/1")
    assert post.call_count == 2


def test_login_rejected_is_auth_error():
    with patch.object(tvdb, "provider_post", side_effect=ApiKeyError("tvdb")):
        with pytest.raises(TvdbAuthError):
            tvdb._get("/series/1")


def test_login_without_token_is_transient():
    with patch.object(tvdb, "provider_post", return_value={"data": {}}):
        with pytest.raises(TransientProviderError):
            tvdb._get("/series/1")


def test_login_no_data_is_transient():
    with patch.object(tvdb, "provider_post", return_value=None):
        with pytest.raises(TransientProviderError):
            tvdb._get("/series/1")


SERIES = {"data": {"id": 80767, "name": "Pat a Mat", "overview": "cs plot"}}
TRANS = {
    "nld": {"data": {"name": "Buurman en Buurman", "overview": "nl plot", "language": "nld"}},
    "eng": {"data": {"name": "Pat & Mat", "overview": "en plot", "language": "eng"}},
}
EXTENDED = {"data": {"aliases": [
    {"language": "eng", "name": "Les nouvelles aventures de Pat et Mat"},
    {"language": "deu", "name": "Pat und Mat"},
]}}


def _series_routes(nld_trans=None):
    return {
        "/translations/nld": nld_trans if nld_trans is not None else TRANS["nld"],
        "/translations/eng": TRANS["eng"],
        "/extended": EXTENDED,
        "/series/80767": SERIES,
    }


def test_series_names_include_translation_and_aliases():
    gets: List[Any] = []
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get",
                         side_effect=_router(_series_routes(), gets)):
        result = tvdb.fetch_series("80767", "nld")
    assert result["name"] == "Buurman en Buurman"
    assert result["overview"] == "nl plot"
    assert set(result["names"]) == {
        "Pat a Mat", "Buurman en Buurman", "Pat & Mat",
        "Les nouvelles aventures de Pat et Mat", "Pat und Mat",
    }
    assert any("/extended?short=true" in g[0] for g in gets)


def test_series_without_translation_has_empty_text_but_keeps_names():
    routes = _series_routes()
    routes["/translations/nld"] = None
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router(routes)):
        result = tvdb.fetch_series("80767", "nld")
    assert result["name"] == ""
    assert result["overview"] == ""
    assert "Pat a Mat" in result["names"]


def test_series_404_returns_none():
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router({"/series/9": None})):
        assert tvdb.fetch_series("9", "nld") is None


def _page(ids, nxt):
    return {
        "data": {"episodes": [
            {"id": i, "name": "T%d" % i, "overview": None if i % 2 else "P%d" % i}
            for i in ids
        ]},
        "links": {"next": nxt},
    }


def test_episodes_paged_all_pages_merged():
    pages = {"page=0": _page([1, 2], "x?page=1"), "page=1": _page([3], None)}
    gets: List[Any] = []
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router(pages, gets)):
        result = tvdb.fetch_series_episodes("80767", "nld")
    assert result == {
        "1": {"title": "T1", "plot": ""},
        "2": {"title": "T2", "plot": "P2"},
        "3": {"title": "T3", "plot": ""},
    }
    assert [g[0].split("?")[1] for g in gets] == ["page=0", "page=1"]


def test_episode_page_failure_raises_transient_no_partial():
    def second(url):
        raise TransientProviderError("http 500")

    pages = {"page=0": _page([1], "x?page=1"), "page=1": second}
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router(pages)):
        with pytest.raises(TransientProviderError):
            tvdb.fetch_series_episodes("80767", "nld")


def test_episode_later_page_404_is_transient_not_partial():
    pages = {"page=0": _page([1], "x?page=1"), "page=1": None}
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router(pages)):
        with pytest.raises(TransientProviderError):
            tvdb.fetch_series_episodes("80767", "nld")


def test_episodes_404_returns_none():
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router({"page=0": None})):
        assert tvdb.fetch_series_episodes("9", "nld") is None


def test_400_is_transient():
    gets: List[Any] = []
    posts: List[Any] = []

    def post(*args, **kwargs):
        posts.append(kwargs)
        return LOGIN_OK

    with patch.object(tvdb, "provider_post", side_effect=post), \
            patch.object(tvdb, "provider_get", side_effect=_router(
                {"page=0": _page([1], None)}, gets)):
        tvdb.fetch_series_episodes("80767", "nld")
    assert gets[0][1]["transient_400"] is True
    assert gets[0][1]["provider"] == "tvdb"
    assert posts[0]["transient_400"] is True


def test_episode_without_translation_yields_empty_text():
    page = {"data": {"episodes": [
        {"id": 5, "name": None, "overview": None},
        {"id": 6, "name": "Titel", "overview": "Plot"},
    ]}, "links": {"next": None}}
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router({"page=0": page})):
        result = tvdb.fetch_series_episodes("1", "nld")
    assert result == {
        "5": {"title": "", "plot": ""},
        "6": {"title": "Titel", "plot": "Plot"},
    }


def test_episode_without_id_is_skipped():
    page = {"data": {"episodes": [{"name": "x"}, {"id": 2, "name": "y"}]},
            "links": {"next": None}}
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router({"page=0": page})):
        assert list(tvdb.fetch_series_episodes("1", "nld")) == ["2"]


def test_translation_transient_error_propagates():
    routes = _series_routes(TransientProviderError("http 500"))
    with patch.object(tvdb, "provider_post", side_effect=_post_ok), \
            patch.object(tvdb, "provider_get", side_effect=_router(routes)):
        with pytest.raises(TransientProviderError):
            tvdb.fetch_series("80767", "nld")


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def test_project_key_not_logged(caplog):
    """Drive the real http helpers over a failing transport: the key and the
    token must not reach log text."""
    import requests

    caplog.set_level(logging.DEBUG)
    key, token = constants.TVDB_API_KEY, "tok-secret"

    def fake_post(url, json=None, **kwargs):
        return _Resp(200, {"data": {"token": token}})

    def fake_get(url, **kwargs):
        raise requests.ConnectionError(
            "failed {} Authorization: Bearer {} apikey={}".format(url, token, key)
        )

    with patch("resources.lib.providers.http.requests.post", side_effect=fake_post), \
            patch("resources.lib.providers.http.requests.get", side_effect=fake_get):
        with pytest.raises(TransientProviderError) as exc:
            tvdb.fetch_series("80767", "nld")
    assert key not in caplog.text and token not in caplog.text
    assert key not in str(exc.value) and token not in str(exc.value)

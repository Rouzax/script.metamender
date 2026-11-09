"""Tests for the shared provider HTTP helper."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resources.lib.providers.http import provider_get, provider_post
from resources.lib.utils import ApiKeyError, TransientProviderError
import resources.lib.providers.http as http_mod


def _resp(status, json_data=None, raise_json=False):
    r = MagicMock()
    r.status_code = status
    if raise_json:
        r.json.side_effect = ValueError("bad json")
    else:
        r.json.return_value = json_data or {}
    return r


def _patch_get(monkeypatch, resp=None, exc=None):
    def fake_get(url, params=None, timeout=None, headers=None):
        assert headers and "User-Agent" in headers  # UA always sent
        if exc:
            raise exc
        return resp
    monkeypatch.setattr(http_mod.requests, "get", fake_get)


def test_provider_get_200_returns_json(monkeypatch):
    _patch_get(monkeypatch, resp=_resp(200, {"ok": 1}))
    assert provider_get("http://x", logger=MagicMock(), label="t") == {"ok": 1}


def test_provider_get_404_returns_none(monkeypatch):
    _patch_get(monkeypatch, resp=_resp(404))
    assert provider_get("http://x", logger=MagicMock(), label="t") is None


def test_provider_get_401_raises_apikey(monkeypatch):
    _patch_get(monkeypatch, resp=_resp(401))
    with pytest.raises(ApiKeyError):
        provider_get("http://x", logger=MagicMock(), label="t", provider="tmdb")


def test_provider_get_429_raises_transient(monkeypatch):
    _patch_get(monkeypatch, resp=_resp(429))
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")


def test_provider_get_500_raises_transient(monkeypatch):
    _patch_get(monkeypatch, resp=_resp(503))
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")


def test_provider_get_timeout_raises_transient(monkeypatch):
    import requests
    _patch_get(monkeypatch, exc=requests.Timeout())
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")


def test_provider_get_malformed_json_raises_transient(monkeypatch):
    _patch_get(monkeypatch, resp=_resp(200, raise_json=True))
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")


def test_provider_get_403_raises_transient(monkeypatch):
    # 403 is bot-protection (e.g. Akamai); a persistent block must trip the breaker.
    _patch_get(monkeypatch, resp=_resp(403))
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")


def test_provider_get_other_4xx_returns_none(monkeypatch):
    # A non-transient 4xx (e.g. 400) is "no data", not a provider failure.
    _patch_get(monkeypatch, resp=_resp(400))
    assert provider_get("http://x", logger=MagicMock(), label="t") is None


def _patch_post(monkeypatch, resp=None, exc=None, seen=None):
    def fake_post(url, json=None, timeout=None, headers=None):
        assert headers and "User-Agent" in headers
        if seen is not None:
            seen.update(url=url, json=json, headers=headers)
        if exc:
            raise exc
        return resp
    monkeypatch.setattr(http_mod.requests, "post", fake_post)


def _call(method, **kw):
    if method == "get":
        return provider_get("http://x", logger=MagicMock(), label="t", **kw)
    return provider_post(
        "http://x", logger=MagicMock(), label="t", json_body={"a": 1}, **kw
    )


def _patch(method, monkeypatch, resp=None, exc=None):
    (_patch_get if method == "get" else _patch_post)(monkeypatch, resp=resp, exc=exc)


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "status,outcome",
    [(200, {"ok": 1}), (404, None), (401, ApiKeyError), (503, TransientProviderError),
     (429, TransientProviderError), (418, None)],
)
def test_post_classifies_like_get(monkeypatch, method, status, outcome):
    _patch(method, monkeypatch, resp=_resp(status, {"ok": 1}))
    if isinstance(outcome, type):
        with pytest.raises(outcome):
            _call(method)
    else:
        assert _call(method) == outcome


@pytest.mark.parametrize("method", ["get", "post"])
def test_transport_error_transient_and_redacted(monkeypatch, method):
    exc = http_mod.requests.ConnectionError("Bearer topsecrettoken failed")
    _patch(method, monkeypatch, exc=exc)
    with pytest.raises(TransientProviderError) as info:
        _call(method)
    assert "topsecrettoken" not in str(info.value)


@pytest.mark.parametrize("method", ["get", "post"])
def test_400_transient_when_flagged(monkeypatch, method):
    _patch(method, monkeypatch, resp=_resp(400))
    with pytest.raises(TransientProviderError):
        _call(method, transient_400=True)


@pytest.mark.parametrize("method", ["get", "post"])
def test_400_nodata_by_default(monkeypatch, method):
    _patch(method, monkeypatch, resp=_resp(400))
    assert _call(method) is None


def test_extra_headers_sent_on_get(monkeypatch):
    seen = {}

    def fake_get(url, params=None, timeout=None, headers=None):
        seen.update(headers=headers)
        return _resp(200, {})
    monkeypatch.setattr(http_mod.requests, "get", fake_get)
    _call("get", headers={"Authorization": "Bearer t"})
    assert seen["headers"]["Authorization"] == "Bearer t"
    assert "User-Agent" in seen["headers"]


def test_extra_headers_and_body_sent_on_post(monkeypatch):
    seen = {}
    _patch_post(monkeypatch, resp=_resp(200, {}), seen=seen)
    _call("post", headers={"Authorization": "Bearer t"})
    assert seen["headers"]["Authorization"] == "Bearer t"
    assert "User-Agent" in seen["headers"]
    assert seen["json"] == {"a": 1}

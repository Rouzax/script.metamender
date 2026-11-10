"""Tests for the shared provider HTTP helper."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from resources.lib.providers.http import provider_get, provider_get_text, provider_post
from resources.lib.utils import ApiKeyError, TransientProviderError
import resources.lib.providers.http as http_mod


class _FakeResp:
    """A streamed response: the body arrives through iter_content."""

    def __init__(self, status, body=b"", chunks=None):
        self.status_code = status
        self.encoding = "utf-8"
        self._chunks = chunks if chunks is not None else [body]
        self.closed = False

    def iter_content(self, chunk_size):
        yield from self._chunks

    def close(self):
        self.closed = True


def _resp(status, json_data=None, raise_json=False):
    if raise_json:
        return _FakeResp(status, b"{not json")
    return _FakeResp(status, json.dumps(json_data or {}).encode())


class _FakeSession:
    def __init__(self, handler):
        self._handler = handler

    def request(self, method, url, **kw):
        assert kw["headers"] and "User-Agent" in kw["headers"]  # UA always sent
        assert kw["allow_redirects"] is False and kw["stream"] is True
        assert kw["timeout"] == (http_mod.HTTP_CONNECT_TIMEOUT, http_mod.HTTP_READ_TIMEOUT)
        return self._handler(method, url, kw)


def _install(monkeypatch, handler):
    monkeypatch.setattr(http_mod, "_session_for", lambda url: _FakeSession(handler))


def _patch_get(monkeypatch, resp=None, exc=None, seen=None):
    def handler(method, url, kw):
        assert method == "GET"
        if seen is not None:
            seen.update(url=url, **kw)
        if exc:
            raise exc
        return resp
    _install(monkeypatch, handler)


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
    def handler(method, url, kw):
        assert method == "POST"
        if seen is not None:
            seen.update(url=url, **kw)
        if exc:
            raise exc
        return resp
    _install(monkeypatch, handler)


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
    _patch_get(monkeypatch, resp=_resp(200, {}), seen=seen)
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


# =============================================================================
# Sessions, redirects, limits, text responses
# =============================================================================


def test_redirect_is_transient(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(302))
    with pytest.raises(TransientProviderError) as info:
        provider_get("http://x", logger=MagicMock(), label="t")
    assert info.value.status == 302


def test_body_over_cap_is_transient(monkeypatch):
    monkeypatch.setattr(http_mod, "HTTP_MAX_BODY_BYTES", 10)
    resp = _FakeResp(200, chunks=[b"{\"a\":", b" 12345678}"])
    _patch_get(monkeypatch, resp=resp)
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")
    assert resp.closed


def test_deadline_is_transient(monkeypatch):
    ticks = iter([0.0, 0.0, http_mod.HTTP_DEADLINE_SEC + 1])
    monkeypatch.setattr(http_mod.time, "monotonic", lambda: next(ticks))
    _patch_get(monkeypatch, resp=_FakeResp(200, chunks=[b"{", b"}"]))
    with pytest.raises(TransientProviderError):
        provider_get("http://x", logger=MagicMock(), label="t")


def test_get_text_returns_body(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(200, "<html>caf\u00e9</html>".encode()))
    assert provider_get_text("http://x", logger=MagicMock(), label="t") == "<html>caf\u00e9</html>"


def test_get_text_404_is_none(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(404))
    assert provider_get_text("http://x", logger=MagicMock(), label="t") is None


def test_429_status_attached(monkeypatch):
    _patch_get(monkeypatch, resp=_FakeResp(429))
    with pytest.raises(TransientProviderError) as info:
        provider_get_text("http://x", logger=MagicMock(), label="t")
    assert info.value.status == 429


def test_session_reused_per_host():
    http_mod._SESSIONS.clear()
    a = http_mod._session_for("https://api.example.org/a")
    assert http_mod._session_for("https://API.example.org/b?x=1") is a
    assert http_mod._session_for("https://other.example.org/") is not a
    http_mod._SESSIONS.clear()


def test_text_errors_are_redacted(monkeypatch):
    exc = http_mod.requests.ConnectionError("failed https://x/?api_key=SECRETKEY123")
    _patch_get(monkeypatch, exc=exc)
    with pytest.raises(TransientProviderError) as info:
        provider_get_text("http://x", logger=MagicMock(), label="t")
    assert "SECRETKEY123" not in str(info.value)

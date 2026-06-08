"""Tests for the shared provider HTTP helper."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from resources.lib.providers.http import provider_get
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

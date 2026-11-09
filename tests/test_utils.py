"""Smoke tests for utility infrastructure."""
from __future__ import annotations

from resources.lib.utils import get_logger


def test_get_logger_returns_logger(kodi_stubs):
    log = get_logger("test")
    assert log is not None


def test_logger_prefix(kodi_stubs):
    log = get_logger("test")
    log.info("hello", event="test.smoke")


# A TMDB v3 key has this shape: 32 hex characters. Not a real key.
_FAKE_KEY = "0123456789abcdef0123456789abcdef"
# str() of a requests ConnectionError for a TMDB call: the prepared URL, key
# included, sits inside the message.
_CONN_ERROR = (
    "HTTPSConnectionPool(host='api.themoviedb.org', port=443): Max retries "
    "exceeded with url: /3/movie/603?api_key={}&language=en-US&append_to_"
    "response=credits (Caused by NameResolutionError(\"Failed to resolve "
    "'api.themoviedb.org'\"))".format(_FAKE_KEY)
)


def _last_kodi_line(kodi_stubs):
    return kodi_stubs["xbmc"].log.call_args[0][0]


def test_api_key_in_logged_value_is_redacted(kodi_stubs):
    get_logger("test").warning("Provider request failed", event="t", error=_CONN_ERROR)
    line = _last_kodi_line(kodi_stubs)
    assert _FAKE_KEY not in line
    assert "api_key=***" in line
    assert "language=en-US" in line


def test_api_key_in_traceback_is_redacted(kodi_stubs):
    log = get_logger("test")
    try:
        raise ConnectionError(_CONN_ERROR)
    except ConnectionError:
        log.exception("Provider call failed", event="t")
    assert _FAKE_KEY not in _last_kodi_line(kodi_stubs)


def test_api_key_cut_by_truncation_leaves_no_fragment(kodi_stubs):
    # Truncation at the value-length limit must not leave a prefix of the key.
    value = "x" * 170 + "/3/movie/1?api_key=" + _FAKE_KEY  # key spans char 200
    get_logger("test").warning("Provider request failed", event="t", error=value)
    assert _FAKE_KEY[:6] not in _last_kodi_line(kodi_stubs)


def test_api_key_in_message_text_is_redacted(kodi_stubs):
    get_logger("test").error("GET /3/movie/1?api_key=" + _FAKE_KEY, event="t")
    assert _FAKE_KEY not in _last_kodi_line(kodi_stubs)

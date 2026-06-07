"""Smoke tests for utility infrastructure."""
from __future__ import annotations

from resources.lib.utils import get_logger


def test_get_logger_returns_logger(kodi_stubs):
    log = get_logger("test")
    assert log is not None


def test_logger_prefix(kodi_stubs):
    log = get_logger("test")
    log.info("hello", event="test.smoke")

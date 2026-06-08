"""Tests for the cycle-level circuit breaker."""
from __future__ import annotations

from resources.lib.engine.breaker import CircuitBreaker


def test_trips_after_threshold():
    cb = CircuitBreaker(threshold=3)
    assert cb.record_failure("tmdb") is False
    assert cb.record_failure("tmdb") is False
    assert cb.record_failure("tmdb") is True   # tripped on the 3rd
    assert cb.is_disabled("tmdb") is True


def test_success_resets_counter():
    cb = CircuitBreaker(threshold=3)
    cb.record_failure("tmdb")
    cb.record_failure("tmdb")
    cb.record_success("tmdb")
    assert cb.record_failure("tmdb") is False  # counter reset, only 1 now
    assert cb.is_disabled("tmdb") is False


def test_independent_providers():
    cb = CircuitBreaker(threshold=2)
    cb.record_failure("tmdb"); cb.record_failure("tmdb")
    assert cb.is_disabled("tmdb") is True
    assert cb.is_disabled("imdb") is False
    assert cb.both_disabled() is False


def test_both_disabled():
    cb = CircuitBreaker(threshold=1)
    cb.record_failure("tmdb"); cb.record_failure("imdb")
    assert cb.both_disabled() is True


def test_trip_reported_once():
    cb = CircuitBreaker(threshold=1)
    assert cb.record_failure("tmdb") is True   # newly tripped
    assert cb.record_failure("tmdb") is False  # already disabled, not "newly" tripped

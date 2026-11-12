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
    assert cb.all_disabled(("tmdb", "imdb")) is False


def test_unknown_provider_counts_failures():
    cb = CircuitBreaker(threshold=5)
    for _ in range(4):
        assert cb.record_failure("tvdb") is False
    assert cb.is_disabled("tvdb") is False
    assert cb.record_failure("tvdb") is True
    assert cb.is_disabled("tvdb") is True


def test_success_on_unseen_provider_is_harmless():
    cb = CircuitBreaker(threshold=2)
    cb.record_success("tvdb")
    assert cb.is_disabled("tvdb") is False


def test_disable_returns_true_once():
    cb = CircuitBreaker(threshold=5)
    assert cb.disable("tvdb") is True
    assert cb.is_disabled("tvdb") is True
    assert cb.disable("tvdb") is False


def test_all_disabled_requires_every_named_provider():
    cb = CircuitBreaker(threshold=1)
    cb.record_failure("tmdb")
    assert cb.all_disabled(("tmdb",)) is True
    assert cb.all_disabled(("tmdb", "tvdb")) is False
    cb.disable("tvdb")
    assert cb.all_disabled(("tmdb", "tvdb")) is True


def test_all_disabled_empty_is_false():
    cb = CircuitBreaker(threshold=1)
    assert cb.all_disabled(()) is False


def test_trip_reported_once():
    cb = CircuitBreaker(threshold=1)
    assert cb.record_failure("tmdb") is True   # newly tripped
    assert cb.record_failure("tmdb") is False  # already disabled, not "newly" tripped


def test_disabled_lists_providers_sorted():
    cb = CircuitBreaker(threshold=1)
    assert cb.disabled() == []
    cb.disable("tvdb")
    cb.record_failure("tmdb")
    assert cb.disabled() == ["tmdb", "tvdb"]


def test_auth_rejection_is_remembered_apart_from_trips():
    cb = CircuitBreaker(threshold=1)
    cb.record_failure("tmdb")
    assert cb.auth_rejected() == []
    assert cb.disable("tvdb", auth=True) is True
    assert cb.disable("tvdb", auth=True) is False
    assert cb.is_disabled("tvdb")
    assert cb.auth_rejected() == ["tvdb"]


def test_plain_disable_is_not_an_auth_rejection():
    cb = CircuitBreaker()
    cb.disable("tvdb")
    assert cb.auth_rejected() == []


def test_answered_records_a_real_answer_per_provider():
    breaker = CircuitBreaker()
    assert not breaker.answered("tmdb")
    breaker.record_failure("tmdb")
    assert not breaker.answered("tmdb")
    breaker.record_success("tmdb")
    assert breaker.answered("tmdb") and not breaker.answered("tvdb")

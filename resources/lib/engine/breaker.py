"""Cycle-level circuit breaker for provider availability.

One instance is created per scan cycle and shared across the movie/tvshow/
episode passes, so a provider that fails repeatedly stays disabled for the
rest of the cycle instead of being re-hammered once per media type.

Logging:
    None (callers log trips with their own structured logger).
"""
from __future__ import annotations

from typing import Dict

from resources.lib.constants import PROVIDER_CIRCUIT_BREAKER_THRESHOLD


class CircuitBreaker:
    """Tracks consecutive provider failures and disables a provider on threshold."""

    def __init__(self, threshold: int = PROVIDER_CIRCUIT_BREAKER_THRESHOLD) -> None:
        self._threshold = threshold
        self._failures: Dict[str, int] = {"tmdb": 0, "imdb": 0}
        self._disabled: Dict[str, bool] = {"tmdb": False, "imdb": False}

    def is_disabled(self, provider: str) -> bool:
        return self._disabled.get(provider, False)

    def both_disabled(self) -> bool:
        return self._disabled["tmdb"] and self._disabled["imdb"]

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0

    def record_failure(self, provider: str) -> bool:
        """Increment the provider's failure count. Return True only on the
        transition that newly disables the provider (so callers log once)."""
        if self._disabled.get(provider, False):
            return False
        self._failures[provider] += 1
        if self._failures[provider] >= self._threshold:
            self._disabled[provider] = True
            return True
        return False

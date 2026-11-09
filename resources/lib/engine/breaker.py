"""Cycle-level circuit breaker for provider availability.

One instance is created per scan cycle and shared across the movie/tvshow/
episode passes, so a provider that fails repeatedly stays disabled for the
rest of the cycle instead of being re-hammered once per media type.

Logging:
    None (callers log trips with their own structured logger).
"""
from __future__ import annotations

from typing import Dict, Iterable, Set

from resources.lib.constants import PROVIDER_CIRCUIT_BREAKER_THRESHOLD


class CircuitBreaker:
    """Tracks consecutive provider failures and disables a provider on threshold."""

    def __init__(self, threshold: int = PROVIDER_CIRCUIT_BREAKER_THRESHOLD) -> None:
        self._threshold = threshold
        self._failures: Dict[str, int] = {}
        self._disabled: Set[str] = set()

    def is_disabled(self, provider: str) -> bool:
        return provider in self._disabled

    def all_disabled(self, providers: Iterable[str]) -> bool:
        """True when every named provider is disabled; False for none named."""
        names = list(providers)
        return bool(names) and all(p in self._disabled for p in names)

    def disable(self, provider: str) -> bool:
        """Disable a provider outright (e.g. rejected credentials). Return
        True only on the transition, so callers log once."""
        if provider in self._disabled:
            return False
        self._disabled.add(provider)
        return True

    def record_success(self, provider: str) -> None:
        self._failures[provider] = 0

    def record_failure(self, provider: str) -> bool:
        """Increment the provider's failure count. Return True only on the
        transition that newly disables the provider (so callers log once)."""
        if provider in self._disabled:
            return False
        self._failures[provider] = self._failures.get(provider, 0) + 1
        if self._failures[provider] >= self._threshold:
            return self.disable(provider)
        return False

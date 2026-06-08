"""
Gap detection for metadata fields.

Determines which fields on a Kodi library item are missing or need
backfilling based on enabled field groups and current values.
"""
from __future__ import annotations

import re
from typing import Any

_NUMERIC_FIELDS = frozenset({"rating", "votes", "runtime"})

_LIST_FIELDS = frozenset({"director", "writer", "genre", "studio", "country"})

_PLACEHOLDER_RE = re.compile(
    r"^(?:episode|aflevering|folge|afsnit|avsnitt|[ée]pisode)(?:\s+\d+)?$"
    r"|^\d+$|^$",
    re.IGNORECASE,
)


def is_field_empty(field: str, value: Any) -> bool:
    """Check if a field value is effectively empty.

    Args:
        field: The field name (used to determine numeric/list semantics).
        value: The current value from Kodi.

    Returns:
        True if the value is considered empty for backfill purposes.
    """
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if field in _NUMERIC_FIELDS and isinstance(value, (int, float)) and value == 0:
        return True
    if field in _LIST_FIELDS and isinstance(value, list) and len(value) == 0:
        return True
    return False


def is_placeholder_title(title: str) -> bool:
    """Check if an episode title is a placeholder.

    Matches (case-insensitive):
        - "Episode N" or "Episode" (English)
        - "Aflevering N" or "Aflevering" (Dutch)
        - "Folge N" or "Folge" (German)
        - "Afsnit N" or "Afsnit" (Danish)
        - "Avsnitt N" or "Avsnitt" (Swedish)
        - "Épisode N" or "Épisode" or "Episode N" (French)
        - Bare number ("1", "23")
        - Empty string

    Args:
        title: The episode title to check.

    Returns:
        True if the title matches a placeholder pattern.
    """
    return _PLACEHOLDER_RE.match(title) is not None

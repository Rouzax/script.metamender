"""
Gap detection for metadata fields.

Determines which fields on a Kodi library item are missing or need
backfilling based on enabled field groups and current values.
"""
from __future__ import annotations

from typing import Any

_NUMERIC_FIELDS = frozenset({"rating", "votes", "runtime"})

_LIST_FIELDS = frozenset({"director", "writer", "genre", "studio", "country"})


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

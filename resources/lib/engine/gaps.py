"""
Gap detection for metadata fields.

Determines which fields on a Kodi library item are missing or need
backfilling based on enabled field groups and current values.

Logging:
    Logger: 'gaps'
    Key events:
        - gaps.detected (DEBUG): Gaps found for an item
"""
from __future__ import annotations

import re
from typing import Any, Dict, FrozenSet, List

from resources.lib.data.media_types import MediaType
from resources.lib.utils import get_logger

log = get_logger('gaps')

_NUMERIC_FIELDS = frozenset({"rating", "votes", "runtime"})

_LIST_FIELDS = frozenset({"director", "writer", "genre", "studio", "country"})

_PLACEHOLDER_RE = re.compile(
    r"^(?:episode|aflevering|folge)(?:\s+\d+)?$|^\d+$|^$",
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
        - "Episode N" or "Episode"
        - "Aflevering N" or "Aflevering"
        - "Folge N" or "Folge"
        - Bare number ("1", "23")
        - Empty string

    Args:
        title: The episode title to check.

    Returns:
        True if the title matches a placeholder pattern.
    """
    return _PLACEHOLDER_RE.match(title) is not None


def detect_gaps(
    item: Dict[str, Any],
    enabled_fields: FrozenSet[str],
    media_type: MediaType,
) -> List[str]:
    """Detect which fields on a library item need backfilling.

    For each field in *enabled_fields* that is also in
    *media_type.backfill_fields*, checks whether the item's current
    value is empty. The "title" field is special-cased: it is only
    counted as a gap for episodes with placeholder titles.

    Args:
        item: Kodi library item dict with current field values.
        enabled_fields: Fields the user has enabled for backfill.
        media_type: The MediaType definition for this item.

    Returns:
        List of field names that need backfilling.
    """
    candidate_fields = enabled_fields & media_type.backfill_fields
    gaps: List[str] = []

    for field in candidate_fields:
        if field == "title":
            if media_type.name == "episode" and is_placeholder_title(
                item.get("title", "")
            ):
                gaps.append(field)
            continue

        if is_field_empty(field, item.get(field)):
            gaps.append(field)

    log.debug(
        "Gaps found",
        event="gaps.detected",
        title=item.get("title", ""),
        media_type=media_type.name,
        gaps=gaps,
    )
    return gaps

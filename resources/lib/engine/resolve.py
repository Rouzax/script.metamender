"""
Source priority resolution for MetaMender.

Resolves per-field values between TMDB and IMDb data based on user
preference settings. Handles four modes: Prefer TMDB, Prefer IMDb,
TMDB only, IMDb only.

Logging:
    Logger: 'resolve'
    Key events:
        - resolve.field (DEBUG): Field resolved from a specific source
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from resources.lib.utils import get_logger

log = get_logger('resolve')

# Preference values matching settings.xml integer options
PREFER_TMDB = 0
PREFER_IMDB = 1
TMDB_ONLY = 2
IMDB_ONLY = 3

# Fields where rating_source preference applies (always paired)
_RATING_FIELDS = frozenset({"rating", "votes"})

# Fields where plot_source preference applies
_PLOT_FIELDS = frozenset({"plot"})


def _is_empty(value: Any) -> bool:
    """Check whether a value is effectively empty."""
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (int, float)) and value == 0:
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def resolve_field(
    field: str,
    tmdb_value: Any,
    imdb_value: Any,
    preference: int,
) -> Tuple[Any, str]:
    """Resolve a single field between TMDB and IMDb values.

    Returns (value, source_name). source_name is "tmdb", "imdb", or
    "" if both are empty.
    """
    if preference == TMDB_ONLY:
        if not _is_empty(tmdb_value):
            return (tmdb_value, "tmdb")
        return (None, "")

    if preference == IMDB_ONLY:
        if not _is_empty(imdb_value):
            return (imdb_value, "imdb")
        return (None, "")

    if preference == PREFER_TMDB:
        if not _is_empty(tmdb_value):
            return (tmdb_value, "tmdb")
        if not _is_empty(imdb_value):
            return (imdb_value, "imdb")
        return (None, "")

    if preference == PREFER_IMDB:
        if not _is_empty(imdb_value):
            return (imdb_value, "imdb")
        if not _is_empty(tmdb_value):
            return (tmdb_value, "tmdb")
        return (None, "")

    # Unknown preference, default to prefer TMDB
    if not _is_empty(tmdb_value):
        return (tmdb_value, "tmdb")
    if not _is_empty(imdb_value):
        return (imdb_value, "imdb")
    return (None, "")


def resolve_metadata(
    tmdb_data: Optional[Dict[str, Any]],
    imdb_data: Optional[Dict[str, Any]],
    gap_fields: List[str],
    rating_pref: int,
    plot_pref: int,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Resolve all gap fields between TMDB and IMDb data.

    Args:
        tmdb_data: Dict from TMDB provider, or None.
        imdb_data: Dict from IMDb provider, or None.
        gap_fields: List of field names that need values.
        rating_pref: Preference for rating and votes fields.
        plot_pref: Preference for plot field.

    Returns:
        Tuple of (merged_data, source_map).
        merged_data: Dict with the same keys as TMDB provider output,
            containing the best value per field.
        source_map: Dict mapping field name to source name ("tmdb" or
            "imdb") for fields that got a value.
    """
    tmdb = tmdb_data or {}
    imdb = imdb_data or {}

    merged = {}  # type: Dict[str, Any]
    sources = {}  # type: Dict[str, str]

    for field in gap_fields:
        if field in _RATING_FIELDS:
            pref = rating_pref
        elif field in _PLOT_FIELDS:
            pref = plot_pref
        else:
            pref = PREFER_TMDB

        tmdb_val = tmdb.get(field)
        imdb_val = imdb.get(field)

        value, source = resolve_field(field, tmdb_val, imdb_val, pref)

        if not _is_empty(value):
            merged[field] = value
            sources[field] = source

    return (merged, sources)

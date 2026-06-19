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

from resources.lib.utils import get_logger, is_empty

log = get_logger('resolve')

# Preference values matching settings.xml integer options
PREFER_TMDB = 0
PREFER_IMDB = 1
TMDB_ONLY = 2
IMDB_ONLY = 3

# Fields where plot_source preference applies
_PLOT_FIELDS = frozenset({"plot"})


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
        if not is_empty(tmdb_value):
            return (tmdb_value, "tmdb")
        return (None, "")

    if preference == IMDB_ONLY:
        if not is_empty(imdb_value):
            return (imdb_value, "imdb")
        return (None, "")

    if preference == PREFER_TMDB:
        if not is_empty(tmdb_value):
            return (tmdb_value, "tmdb")
        if not is_empty(imdb_value):
            return (imdb_value, "imdb")
        return (None, "")

    if preference == PREFER_IMDB:
        if not is_empty(imdb_value):
            return (imdb_value, "imdb")
        if not is_empty(tmdb_value):
            return (tmdb_value, "tmdb")
        return (None, "")

    # Unknown preference, default to prefer TMDB
    if not is_empty(tmdb_value):
        return (tmdb_value, "tmdb")
    if not is_empty(imdb_value):
        return (imdb_value, "imdb")
    return (None, "")


def resolve_metadata(
    tmdb_data: Optional[Dict[str, Any]],
    imdb_data: Optional[Dict[str, Any]],
    gap_fields: List[str],
    plot_pref: int,
    default_pref: int,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Resolve all gap fields between TMDB and IMDb data.

    Args:
        tmdb_data: Dict from TMDB provider, or None.
        imdb_data: Dict from IMDb provider, or None.
        gap_fields: List of field names that need values.
        plot_pref: Preference for the plot field.
        default_pref: Preference for all other fields.

    Returns:
        Tuple of (merged_data, source_map).
        merged_data: Dict containing the best value per field.
        source_map: Dict mapping field name to source name ("tmdb" or
            "imdb") for fields that got a value.
    """
    tmdb = tmdb_data or {}
    imdb = imdb_data or {}

    merged = {}  # type: Dict[str, Any]
    sources = {}  # type: Dict[str, str]

    for field in gap_fields:
        pref = plot_pref if field in _PLOT_FIELDS else default_pref
        value, source = resolve_field(field, tmdb.get(field), imdb.get(field), pref)
        if not is_empty(value):
            merged[field] = value
            sources[field] = source

    return (merged, sources)


def wants_imdb(default_pref: int, rating_pref: int, plot_pref: int) -> bool:
    """Return True if any source setting would consult IMDb."""
    return any(
        p in (PREFER_TMDB, PREFER_IMDB, IMDB_ONLY)
        for p in (default_pref, rating_pref, plot_pref)
    )


# Kodi rating slot names for our two managed providers
_TMDB_SLOT = "themoviedb"
_IMDB_SLOT = "imdb"

# Decimal places used to compare ratings (Kodi stores its own float; compare
# with tolerance so provider 7.28 vs stored 7.28000020980835 is "unchanged").
_RATING_PRECISION = 2


def _norm_rating(value: Any) -> Optional[float]:
    """Round a rating for comparison/storage, or None if not a valid number."""
    try:
        return round(float(value), _RATING_PRECISION)
    except (TypeError, ValueError):
        return None


def _ratings_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Compare two ratings maps by slot set, normalized rating, votes, default."""
    if set(a.keys()) != set(b.keys()):
        return False
    for name in a:
        ea, eb = a[name], b[name]
        if _norm_rating(ea.get("rating")) != _norm_rating(eb.get("rating")):
            return False
        if int(ea.get("votes") or 0) != int(eb.get("votes") or 0):
            return False
        if bool(ea.get("default")) != bool(eb.get("default")):
            return False
    return True


def build_ratings(
    tmdb_data: Dict[str, Any],
    imdb_data: Dict[str, Any],
    current_ratings: Optional[Dict[str, Any]],
    rating_pref: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[int]]:
    """Build the Kodi ratings map to write, or (None, None, None) if unchanged.

    Writes the TMDB rating into the `themoviedb` slot and the IMDb rating into
    the `imdb` slot (subject to rating_pref), preserves all other existing slots
    (e.g. trakt, user), and sets `default` on the preferred source's slot. Kodi
    rejects ratings <= 0 or > 10, so those candidates are dropped. Returns the
    full merged map (for a merging SetXDetails call), the chosen default source
    name, and that source's vote count (for field_status/staleness).
    """
    current = current_ratings or {}

    # Collect in-range candidates honoring the preference's source filter
    use_tmdb = rating_pref in (PREFER_TMDB, PREFER_IMDB, TMDB_ONLY)
    use_imdb = rating_pref in (PREFER_TMDB, PREFER_IMDB, IMDB_ONLY)
    candidates = {}  # type: Dict[str, Tuple[float, int]]
    if use_tmdb:
        r = _norm_rating(tmdb_data.get("rating"))
        if r is not None and 0 < r <= 10:
            candidates[_TMDB_SLOT] = (r, int(tmdb_data.get("votes") or 0))
    if use_imdb:
        r = _norm_rating(imdb_data.get("rating"))
        if r is not None and 0 < r <= 10:
            candidates[_IMDB_SLOT] = (r, int(imdb_data.get("votes") or 0))

    if not candidates:
        return (None, None, None)

    # Build the new map: copy existing slots (default cleared), then overlay ours
    new_map = {}  # type: Dict[str, Any]
    for name, entry in current.items():
        new_map[name] = {
            "rating": entry.get("rating"),
            "votes": int(entry.get("votes") or 0),
            "default": False,
        }
    for name, (r, v) in candidates.items():
        new_map[name] = {"rating": r, "votes": v, "default": False}

    # Choose the default slot by preference, falling back to whichever exists
    order = [_IMDB_SLOT, _TMDB_SLOT] if rating_pref in (PREFER_IMDB, IMDB_ONLY) \
        else [_TMDB_SLOT, _IMDB_SLOT]
    default_source = next((s for s in order if s in candidates), None)
    if default_source is not None:
        new_map[default_source]["default"] = True

    if _ratings_equal(new_map, current):
        return (None, None, None)

    chosen_votes = new_map[default_source]["votes"] if default_source else None
    log.debug(
        "Ratings map built",
        event="resolve.ratings",
        default_source=default_source,
        slots=sorted(new_map.keys()),
    )
    return (new_map, default_source, chosen_votes)

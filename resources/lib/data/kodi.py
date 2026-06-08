"""
Kodi JSON-RPC data access layer.

All library reads and writes go through this module.

Logging:
    Logger: 'kodi'
    Key events:
        - kodi.query (INFO): Library bulk query with count and timing
        - kodi.update (DEBUG): Field written to an item
        - kodi.update_fail (WARNING): Failed to write a field
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import xbmc

from resources.lib.data.media_types import MediaType
from resources.lib.utils import get_logger, log_timing

log = get_logger('kodi')

# Base properties requested for all media types
_BASE_PROPERTIES = [
    "title", "rating", "votes", "plot", "ratings", "uniqueid",
    "file", "dateadded",
]

# Extra properties per media type name
_EXTRA_PROPERTIES = {
    "movie": [
        "mpaa", "tagline", "director", "writer", "genre",
        "studio", "country", "trailer", "runtime",
    ],
    "tvshow": [
        "mpaa", "genre",
    ],
    "episode": [
        "showtitle", "season", "episode",
        "director", "writer", "runtime",
    ],
}


def _build_properties(media_type: MediaType) -> List[str]:
    """Build the JSON-RPC property list for a media type.

    Combines the base properties with media-type-specific extras
    and the date field (premiered or firstaired).
    """
    props = list(_BASE_PROPERTIES)
    props.append(media_type.date_field)
    extras = _EXTRA_PROPERTIES.get(media_type.name, [])
    props.extend(extras)
    return props


def get_all_items(
    media_type: MediaType,
    kodi_filter: Optional[Dict[str, Any]] = None,
    tvshowid: Optional[int] = None,
    season: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Query Kodi library for items of the given media type.

    Without filter arguments, returns all items. With filters, returns
    only matching items (efficient for targeted scans).

    Args:
        media_type: The media type to query (MOVIE, TVSHOW, or EPISODE).
        kodi_filter: Kodi JSON-RPC filter dict, e.g.
            ``{"field": "title", "operator": "is", "value": "Air"}``.
        tvshowid: For episodes, limit to this TV show (Kodi param, not filter).
        season: For episodes, limit to this season (requires tvshowid).

    Returns:
        List of item dicts from the Kodi library.
    """
    properties = _build_properties(media_type)
    params = {
        "properties": properties,
        "limits": {"start": 0, "end": 99999},
    }  # type: Dict[str, Any]

    if kodi_filter is not None:
        params["filter"] = kodi_filter
    if tvshowid is not None:
        params["tvshowid"] = tvshowid
    if season is not None:
        params["season"] = season

    query = {
        "jsonrpc": "2.0",
        "method": media_type.kodi_list_method,
        "params": params,
        "id": 1,
    }

    try:
        rpc_start = time.perf_counter()
        with log_timing(log, "get_all_items", media_type=media_type.name) as timer:
            raw = xbmc.executeJSONRPC(json.dumps(query))
            timer.mark("rpc")
            data = json.loads(raw)
            timer.mark("parse")
        rpc_ms = int((time.perf_counter() - rpc_start) * 1000)
    except Exception:
        log.exception(
            "Failed to query library",
            event="kodi.query",
            media_type=media_type.name,
        )
        return []

    result = data.get("result", {})
    items = result.get(media_type.kodi_result_key, [])

    log.info(
        "Library queried",
        event="kodi.query",
        media_type=media_type.name,
        count=len(items),
        elapsed_ms=rpc_ms,
    )

    return items


def update_item(
    item_id: int,
    media_type: MediaType,
    log_title: str = "",
    **fields: Any,
) -> bool:
    """Write fields back to a Kodi library item via JSON-RPC.

    Args:
        item_id: The Kodi database ID for the item.
        media_type: The media type of the item.
        log_title: The item title (for logging only, not written to Kodi).
        **fields: Field names and values to set (including "title" for episodes).

    Returns:
        True on success, False on failure.
    """
    params = {media_type.kodi_id_field: item_id}
    params.update(fields)

    query = {
        "jsonrpc": "2.0",
        "method": media_type.kodi_set_method,
        "params": params,
        "id": 1,
    }

    try:
        with log_timing(log, "update_item", media_type=media_type.name, title=log_title):
            raw = xbmc.executeJSONRPC(json.dumps(query))
            data = json.loads(raw)
    except Exception:
        log.exception(
            "Failed to update item",
            event="kodi.update_fail",
            media_type=media_type.name,
            title=log_title,
            item_id=item_id,
        )
        return False

    if "error" in data:
        log.warning(
            "Failed to update item",
            event="kodi.update_fail",
            media_type=media_type.name,
            title=log_title,
            item_id=item_id,
            error=str(data["error"]),
        )
        return False

    log.debug(
        "Item updated",
        event="kodi.update",
        media_type=media_type.name,
        title=log_title,
        item_id=item_id,
        fields=", ".join(fields.keys()),
    )
    return True

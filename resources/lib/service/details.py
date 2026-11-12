"""Service side of the item details window: restore and unlock a field."""
from __future__ import annotations

from typing import Any

from resources.lib.data import kodi
from resources.lib.data.media_types import MediaType
from resources.lib.data.tracker import REASON_LOCKED, TrackerDB
from resources.lib.engine.protect import fingerprint
from resources.lib.engine.scanctx import _kodi_form
from resources.lib.utils import get_logger

log = get_logger('details')

# The rating slots are rebuilt from several providers on every scan, so a
# single kept value is neither meaningful to restore nor stable to lock.
RESTORABLE_EXCLUDED = frozenset({"rating", "votes", "ratings"})

RESTORED = "restored"
CHANGED = "changed"
FAILED = "failed"


def can_restore(
    field: str, current: Any, fs: dict[str, Any], keep: dict[str, Any]
) -> bool:
    """An original is kept and Kodi still holds MetaMender's own value.

    ``keep`` is the item's get_keep() map. The value is MetaMender's own when
    its fingerprint equals the one recorded at write time; the field_status
    ``written_value`` is the fallback because Reset tracker deletes
    field_status but keeps field_keep.
    """
    if field in RESTORABLE_EXCLUDED:
        return False
    entry = keep.get(field) or {}
    if not entry.get("has_original"):
        return False
    written = entry.get("written_fp") or fs.get("written_value")
    return written is not None and fingerprint(_kodi_form(current)) == written


def restore_field(
    tracker: TrackerDB, media_type: MediaType, kodi_id: int, identity: str, field: str
) -> str:
    """Write the kept original back to Kodi and lock the field.

    Kodi is re-read first: the window may have been open while the user or a
    scraper changed the value, and overwriting that would lose it. Returns
    "restored", "changed" (nothing written) or "failed" (Kodi refused the
    write; nothing locked). Provenance is left alone: the field now holds the
    original, not a value MetaMender wrote.
    """
    keep = tracker.get_keep(media_type.name, identity)
    fs = tracker.get_field_status_map(media_type.name, identity).get(field, {})
    item = kodi.get_item_by_id(media_type, kodi_id) or {}
    if not can_restore(field, item.get(field), fs, keep):
        log.info(
            "Restore refused, value is no longer MetaMender's",
            event="details.restore_changed", identity=identity,
            media_type=media_type.name, field=field,
        )
        return CHANGED
    original = keep[field]["original"]
    if not kodi.update_item(
        kodi_id, media_type, str(item.get("title") or ""), **{field: original}
    ):
        log.warning(
            "Kodi refused the restore",
            event="details.restore_failed", identity=identity,
            media_type=media_type.name, field=field,
        )
        return FAILED
    tracker.set_locked(media_type.name, identity, field, True)
    tracker.record_field_reason(media_type.name, identity, field, REASON_LOCKED)
    tracker.commit()
    log.info(
        "Original restored and field locked",
        event="details.restored", identity=identity,
        media_type=media_type.name, field=field,
    )
    return RESTORED


def unlock_field(
    tracker: TrackerDB, media_type: MediaType, identity: str, field: str
) -> None:
    """Let the scan treat the field normally again; the kept original stays."""
    tracker.set_locked(media_type.name, identity, field, False)
    tracker.commit()
    log.info(
        "Field unlocked",
        event="details.unlocked", identity=identity,
        media_type=media_type.name, field=field,
    )

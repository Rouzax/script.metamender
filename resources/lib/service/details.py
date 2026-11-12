"""Service side of the item details window: open it for a library item,
and restore or unlock a field from it.

Logging:
    Logger: 'details'
    Key events:
        - details.open_invalid (WARNING): Details asked for an unsupported
          type or a bad id
        - details.open_notfound (WARNING): The item is not in the library
        - details.locked (INFO): Restore skipped, a scan is running
        - details.restored / details.restore_changed / details.restore_failed
        - details.unlocked (INFO)
        - details.error (ERROR): Reading the item or a window action raised
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from resources.lib.data import kodi
from resources.lib.data.identity import compute_identity
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW, MediaType
from resources.lib.data.sources import (
    kodi_ui_language,
    load_source_config,
    match_source,
)
from resources.lib.data.tracker import REASON_LOCKED, TrackerDB
from resources.lib.engine.protect import fingerprint
from resources.lib.engine.scan import _candidate_fields, _item_fields
from resources.lib.engine.scanctx import _kodi_form
from resources.lib.service import actions
from resources.lib.service.actions import _init_logging
from resources.lib.service.daemon import (
    _get_db_path,
    _get_sources_config_path,
    _load_settings,
)
from resources.lib.service.lock import acquire_scan_lock, release_scan_lock
from resources.lib.ui.setup_window import language_name
from resources.lib.utils import get_logger, lang, notify

if TYPE_CHECKING:
    from resources.lib.ui.details_model import DetailRow

log = get_logger('details')

_DBTYPE_TO_MEDIA_TYPE = {"movie": MOVIE, "episode": EPISODE, "tvshow": TVSHOW}
# Certifications are scanned for movies and shows only (scan._cert_config).
_CERT_TYPES = frozenset({"movie", "tvshow"})

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
    """Let MetaMender update the field again from the next scan on; the kept
    original stays. The field may be settled, and a lock flag alone would
    leave it settled forever, so it is made due at the next scan."""
    tracker.set_locked(media_type.name, identity, field, False)
    tracker.forget_field_fingerprint(media_type.name, identity, field)
    tracker.commit()
    log.info(
        "Field unlocked",
        event="details.unlocked", identity=identity,
        media_type=media_type.name, field=field,
    )


@dataclass
class DetailsView:
    """What the window shows: the item, the header's short line, the rows."""

    heading: str
    sub: str
    rows: list[DetailRow]


def _read_identity(
    tracker: TrackerDB, media_type: MediaType, item: dict[str, Any], kodi_id: int
) -> str:
    """The item's tracker key, found the way scan._tracker_identity finds it
    but without its writes: opening the window must change nothing, so a
    row the next scan would adopt is read under its current identity."""
    identity = compute_identity(item, media_type)
    own = "{}|{}".format(identity, item.get("file", ""))
    if tracker.get_item(media_type.name, own) is not None:
        return own
    # An id written back to Kodi changes the identity the item computes; the
    # row stays under the old one until the next scan adopts it. Restore and
    # Unlock on the old identity are carried along by that adoption.
    adoptable = tracker.adoptable_identity(media_type.name, identity, kodi_id)
    if adoptable is not None:
        return adoptable
    row = tracker.get_item(media_type.name, identity)
    holder = row.get("kodi_id") if row else None
    if holder is None or holder == kodi_id:
        return identity
    other = kodi.get_item_by_id(media_type, holder)
    if other and compute_identity(other, media_type) == identity:
        return own
    return identity


def managed_fields(
    media_type: MediaType, enabled_fields: frozenset[str], identity: str
) -> list[str]:
    """The fields a scan manages for this item under the current settings."""
    fields = _candidate_fields(media_type, enabled_fields)
    if "mpaa" in enabled_fields and media_type.name in _CERT_TYPES:
        fields = sorted(set(fields) | {"mpaa"})
    return _item_fields(fields, media_type, identity)


def _heading(media_type: MediaType, item: dict[str, Any]) -> str:
    if media_type.name == "episode":
        return lang(32585).format(
            item.get("showtitle", ""), item.get("season", 0), item.get("episode", 0)
        )
    return str(item.get("title") or "")


class DetailsSession:
    """One item in the details window: reads it for each view and runs the
    window's buttons. ``identity`` is the tracker key of the last load."""

    def __init__(self, dbtype: str, media_type: MediaType, kodi_id: int) -> None:
        self.dbtype = dbtype
        self.media_type = media_type
        self.kodi_id = kodi_id
        self.identity = ""

    def load(self) -> DetailsView | None:
        """Read the item from Kodi and the tracker; None when it is gone.
        Reads only."""
        # details_model imports this module (can_restore), so it is imported
        # here rather than at the top.
        from resources.lib.ui.details_model import build_rows, header_line

        item = kodi.get_item_by_id(self.media_type, self.kodi_id)
        if not item:
            return None
        settings = _load_settings()
        config = load_source_config(_get_sources_config_path())
        source = match_source(item.get("file", ""), config.get("sources", []))
        # The scan's language for the folder (scan.run_scan): the folder's
        # own, else the configured default; with neither, the folder has no
        # language and text is filled in Kodi's interface language.
        code = source.get("language") or config.get("default_language") or ""
        folder = language_name(code) if code else ""
        text_language = folder or language_name(kodi_ui_language())
        tracker = TrackerDB(_get_db_path())
        try:
            identity = _read_identity(tracker, self.media_type, item, self.kodi_id)
            item_row = tracker.get_item(self.media_type.name, identity)
            fs_map = tracker.get_field_status_map(self.media_type.name, identity)
            keep = tracker.get_keep(self.media_type.name, identity)
        finally:
            tracker.close()
        self.identity = identity
        rows = build_rows(
            self.media_type, item, item_row, fs_map, keep, date.today().isoformat(),
            language=text_language,
            fields=managed_fields(self.media_type, settings["enabled_fields"], identity),
            threshold_days=settings["stale_threshold_days"],
        )
        return DetailsView(
            _heading(self.media_type, item), header_line(item_row, fs_map, folder), rows
        )

    def reload(self) -> DetailsView | None:
        """load(), with a missing item or a failed read told to the user and
        logged here; None then, and the window has nothing to show."""
        try:
            view = self.load()
        except Exception:
            log.exception("Reading the item's details failed", event="details.error",
                          media_type=self.media_type.name, item_id=self.kodi_id)
            notify(lang(32592))  # Could not read this item's details
            return None
        if view is None:
            log.warning("Details could not find the item", event="details.open_notfound",
                        media_type=self.media_type.name, item_id=self.kodi_id)
            notify(lang(32345))  # Item not found
        return view

    def update_now(self) -> None:
        """The context menu's Update now: one movie or episode, or a show's
        refresh with its own progress window. It takes the scan lock and
        reports the result itself."""
        actions.update_now(self.dbtype, str(self.kodi_id))

    def run_action(self, row: DetailRow) -> None:
        """The row's button: restore the original (under the scan lock, as it
        writes Kodi) or unlock the field."""
        from resources.lib.ui.details_model import ACTION_RESTORE, ACTION_UNLOCK

        if row.action == ACTION_UNLOCK:
            tracker = None
            try:
                tracker = TrackerDB(_get_db_path())
                unlock_field(tracker, self.media_type, self.identity, row.key)
            except Exception:
                log.exception("Unlock failed", event="details.error", field=row.key)
            finally:
                if tracker is not None:
                    tracker.close()
            return
        if row.action != ACTION_RESTORE:
            return
        if not acquire_scan_lock():
            log.info("A scan is already running, skipping restore", event="details.locked")
            notify(lang(32344))  # A scan is already running
            return
        tracker = None
        result = FAILED
        try:
            tracker = TrackerDB(_get_db_path())
            result = restore_field(
                tracker, self.media_type, self.kodi_id, self.identity, row.key
            )
        except Exception:
            log.exception("Restore failed", event="details.error", field=row.key)
        finally:
            release_scan_lock()
            if tracker is not None:
                tracker.close()
        if result == CHANGED:
            notify(lang(32590))  # The value changed since this window opened
        elif result == FAILED:
            notify(lang(32591))  # Kodi did not accept the restore


def open_details(dbtype: str, dbid: str) -> None:
    """Open the details window for a library item (context menu "MetaMender:
    Details"). Opening writes nothing; only the window's buttons do.

    Args:
        dbtype: Kodi ListItem.DBTYPE ("movie", "tvshow" or "episode").
        dbid: Kodi database ID as a string (from $INFO[ListItem.DBID]).
    """
    from resources.lib.ui.details_window import show_details_window

    _init_logging()
    media_type = _DBTYPE_TO_MEDIA_TYPE.get(dbtype)
    if media_type is None:
        log.warning("Details asked for an unsupported type",
                    event="details.open_invalid", dbtype=dbtype)
        notify(lang(32342))  # Cannot update this item type
        return
    try:
        kodi_id = int(dbid)
    except (TypeError, ValueError):
        log.warning("Details asked with an invalid id",
                    event="details.open_invalid", dbid=str(dbid))
        notify(lang(32343))  # Invalid item
        return
    session = DetailsSession(dbtype, media_type, kodi_id)
    view = session.reload()
    if view is None:
        return
    show_details_window(session, view)

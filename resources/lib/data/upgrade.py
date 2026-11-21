"""
Conversion of 1.1 global settings to per-library settings.

The pure parts live here; the service reads Kodi's settings file, writes
sources.json and only then removes the moved ids from Kodi's settings.

Logging:
    Logger: 'upgrade'
    Key events:
        - upgrade.copy_written (INFO): the moved settings were saved aside
        - upgrade.copy_fail (ERROR): the safe copy could not be written
        - upgrade.dropped_entry (WARNING): an old source entry matches no library
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Optional

from resources.lib.data.libraries import (
    FIELD_TOGGLES,
    LibraryConfig,
    library_from_legacy_settings,
)
from resources.lib.data.presets import country_for_setting
from resources.lib.data.sources import _path_matches, source_paths
from resources.lib.utils import get_logger

log = get_logger('upgrade')

COPY_FILE_NAME = "legacy-settings.json"

MOVED_IDS: tuple[str, ...] = (
    "rating_source", "text_source_order", "fallback_order",
    "enable_ratings", "enable_plots", "enable_episode_titles",
    "enable_main_titles", "enable_certifications", "enable_credits",
    "enable_trailers", "enable_taglines", "enable_details",
    "country", "override_prefix", "rating_prefix", "replace_incorrect",
    "enable_fallback", "fallback_rating",
)

# The 1.1 defaults, used for an id Kodi did not write to the settings file.
_DEFAULT_TEXT = {"rating_prefix": "", "fallback_rating": "NR"}
_DEFAULT_INT = {"rating_source": 4, "text_source_order": 2, "fallback_order": 0, "country": 0}
_DEFAULT_BOOL = {
    "enable_ratings": True, "enable_plots": True, "enable_episode_titles": False,
    "enable_main_titles": False, "enable_certifications": True,
    "enable_credits": False, "enable_trailers": False, "enable_taglines": False,
    "enable_details": False, "override_prefix": False, "replace_incorrect": True,
    "enable_fallback": False,
}


def read_user_settings(path: str) -> dict[str, str]:
    """Setting id to text from Kodi's userdata settings.xml; {} when the
    file is missing or unreadable."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    return {
        str(node.get("id")): node.text or ""
        for node in root.iter("setting") if node.get("id")
    }


def _read_copy(copy_path: str) -> Optional[dict[str, str]]:
    try:
        with open(copy_path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return {str(k): str(v) for k, v in raw.items()}


def ensure_copy(user_settings_path: str, copy_path: str) -> Optional[dict[str, str]]:
    """The moved settings, kept in a file of our own. An existing copy wins:
    after a partial run Kodi's file may already lack the ids. None when
    neither source has any moved id (a fresh install) or the copy cannot be
    written (the old settings then stay where they are)."""
    existing = _read_copy(copy_path)
    if existing is not None:
        return existing
    user = read_user_settings(user_settings_path)
    moved = {key: user[key] for key in MOVED_IDS if key in user}
    if not moved:
        return None
    try:
        parent = os.path.dirname(copy_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(copy_path, "w", encoding="utf-8") as fh:
            json.dump(moved, fh, indent=2)
    except OSError:
        log.exception("Failed to write the settings copy", event="upgrade.copy_fail",
                      path=copy_path)
        return None
    log.info("Settings copy written", event="upgrade.copy_written", path=copy_path,
             setting_count=len(moved))
    return moved


def _int(legacy: dict[str, str], key: str) -> int:
    try:
        return int(float(legacy[key]))
    except (KeyError, ValueError, TypeError):
        return _DEFAULT_INT[key]


def _bool(legacy: dict[str, str], key: str) -> bool:
    if key not in legacy:
        return _DEFAULT_BOOL[key]
    return legacy[key] == "true"


def _text(legacy: dict[str, str], key: str) -> str:
    return legacy.get(key, _DEFAULT_TEXT[key])


def _scan_shaped(legacy: dict[str, str], ui_language: str) -> dict[str, Any]:
    """The 1.1 values in the shape daemon._load_settings produced, so the
    mapping to a library is the one the scan adapter uses."""
    enabled: set[str] = set()
    for name, field_names in FIELD_TOGGLES.items():
        if _bool(legacy, "enable_" + name):
            enabled.update(field_names)
    return {
        "enabled_fields": frozenset(enabled),
        "rating_source": _int(legacy, "rating_source"),
        "text_source_order": _int(legacy, "text_source_order"),
        "fallback_order": _int(legacy, "fallback_order"),
        "cert_country": country_for_setting(_int(legacy, "country"), ui_language),
        "cert_prefix_override": (
            _text(legacy, "rating_prefix") if _bool(legacy, "override_prefix") else None
        ),
        "cert_replace_foreign": _bool(legacy, "replace_incorrect"),
        "cert_not_found": (
            _text(legacy, "fallback_rating").strip() or None
            if _bool(legacy, "enable_fallback") else None
        ),
    }


def _norm(path: str) -> str:
    return path.rstrip("/\\")


def _folders(path: str) -> list[str]:
    return [_norm(f) for f in source_paths(path)]


def _owner(entry_path: str, discovered: list[dict[str, str]]) -> tuple[Optional[int], bool]:
    """(index of the library the old entry belongs to, whether the entry is
    that library's own folder). An exact folder match beats containment;
    among containing folders the longest wins."""
    target = _norm(entry_path)
    for i, src in enumerate(discovered):
        if target in _folders(src["path"]):
            return i, True
    best: Optional[int] = None
    best_len = -1
    for i, src in enumerate(discovered):
        for folder in _folders(src["path"]):
            if _path_matches(target, folder) and len(folder) > best_len:
                best, best_len = i, len(folder)
    return best, False


def convert(legacy: dict[str, str], old_sources: dict[str, Any],
            discovered: list[dict[str, str]], ui_language: str) -> LibraryConfig:
    """One library per discovered source, each carrying the 1.1 global
    settings. A library the old config disabled is left out but keeps them.
    Hand-written entries inside a library's folder survive as subfolders."""
    settings = _scan_shaped(legacy, ui_language)
    default_language = str(old_sources.get("default_language") or ui_language)
    default_fallback = str(old_sources.get("fallback_language") or "")

    own: dict[int, dict[str, Any]] = {}
    subs: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(discovered))}
    for entry in old_sources.get("sources") or []:
        path = str(entry.get("path") or "")
        if not path:
            continue
        idx, is_own = _owner(path, discovered)
        if idx is None:
            log.warning("Old source entry matches no library",
                        event="upgrade.dropped_entry", path=path)
        elif is_own:
            own.setdefault(idx, entry)
        else:
            sub = dict(entry)
            if sub.get("language") and "fallback_language" not in sub:
                sub["fallback_language"] = default_fallback
            subs[idx].append(sub)

    libraries = []
    for i, src in enumerate(discovered):
        entry = own.get(i, {})
        libraries.append(library_from_legacy_settings(
            settings,
            path=src["path"],
            label=src.get("label", ""),
            included=entry.get("enabled", True) is not False,
            language=str(entry.get("language") or default_language),
            fallback_language=str(entry.get("fallback_language", default_fallback) or ""),
            subfolders=tuple(subs[i]),
        ))
    return LibraryConfig(libraries)

"""
Library model and the sources.json v2 file.

A library is one Kodi video source, identified by its path string. Its
settings (language, fields, rating source, certification options) live in
sources.json next to a ``version`` number.

Logging:
    Logger: 'libraries'
    Key events:
        - libraries.load_fail (ERROR): sources.json could not be read
        - libraries.save (INFO): sources.json written
        - libraries.save_fail (ERROR): sources.json could not be written
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from resources.lib.data.presets import country_for_setting
from resources.lib.data.sources import _path_matches, source_paths
from resources.lib.utils import get_logger

log = get_logger('libraries')

VERSION = 2

TITLE_LANGUAGE = "language"
TITLE_ORIGINAL = "original"

# Toggle name -> the fields it switches on. "main_title" stands for the
# "title" field of shows and movies, so the episode-title toggle does not
# switch them (see candidate_fields).
FIELD_TOGGLES: dict[str, frozenset[str]] = {
    "ratings": frozenset({"rating"}),
    "plots": frozenset({"plot"}),
    "episode_titles": frozenset({"title"}),
    "main_titles": frozenset({"main_title"}),
    "certifications": frozenset({"mpaa"}),
    "credits": frozenset({"director", "writer"}),
    "trailers": frozenset({"trailer"}),
    "taglines": frozenset({"tagline"}),
    "details": frozenset({"genre", "studio", "country", "runtime", "firstaired"}),
}


@dataclass(frozen=True)
class Library:
    path: str
    label: str
    included: bool
    language: str
    fallback_language: str
    toggles: frozenset[str]
    rating_source: int = 4
    text_source_order: int = 2
    fallback_order: int = 0
    title_mode: str = TITLE_LANGUAGE
    cert_country: int = 0
    cert_replace_foreign: bool = True
    cert_prefix_override: Optional[str] = None
    cert_not_found: Optional[str] = None
    # Hand-written entries for folders inside the library: path, optional
    # enabled False, language, fallback_language. Not shown in the UI.
    subfolders: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def fields(self) -> frozenset[str]:
        out: set[str] = set()
        for name in self.toggles:
            out |= FIELD_TOGGLES.get(name, frozenset())
        return frozenset(out)


@dataclass
class LibraryConfig:
    libraries: list[Library] = field(default_factory=list)

    def for_file(self, file_path: str) -> Optional[Library]:
        """The library whose folder is the longest prefix of file_path."""
        best: Optional[Library] = None
        best_len = -1
        for lib in self.libraries:
            for folder in source_paths(lib.path):
                if _path_matches(file_path, folder) and len(folder) > best_len:
                    best, best_len = lib, len(folder)
        return best

    def folder_settings(self, file_path: str, lib: Library) -> tuple[bool, str, str]:
        """(included here, language, fallback language) for a file in lib. A
        subfolder entry overrides the library; the longest prefix wins."""
        best: Optional[dict[str, Any]] = None
        best_len = -1
        for entry in lib.subfolders:
            sub = str(entry.get("path", ""))
            if _path_matches(file_path, sub) and len(sub) > best_len:
                best, best_len = entry, len(sub)
        if best is None:
            return True, lib.language, lib.fallback_language
        enabled = best.get("enabled", True) is not False
        if "language" in best:
            return enabled, str(best["language"]), str(best.get("fallback_language", ""))
        return enabled, lib.language, lib.fallback_language

    def by_path(self, path: str) -> Optional[Library]:
        for lib in self.libraries:
            if lib.path == path:
                return lib
        return None

    def replace(self, lib: Library) -> None:
        """Swap in lib for the entry with the same path."""
        for i, old in enumerate(self.libraries):
            if old.path == lib.path:
                self.libraries[i] = lib
                return


def new_library(path: str, label: str, ui_language: str) -> Library:
    """A library found but not yet set up: not included, nothing switched on."""
    return Library(path=path, label=label, included=False, language=ui_language,
                   fallback_language="", toggles=frozenset())


def _to_dict(lib: Library) -> dict[str, Any]:
    return {
        "path": lib.path,
        "label": lib.label,
        "included": lib.included,
        "language": lib.language,
        "fallback_language": lib.fallback_language,
        "toggles": sorted(lib.toggles),
        "rating_source": lib.rating_source,
        "text_source_order": lib.text_source_order,
        "fallback_order": lib.fallback_order,
        "title_mode": lib.title_mode,
        "cert_country": lib.cert_country,
        "cert_replace_foreign": lib.cert_replace_foreign,
        "cert_prefix_override": lib.cert_prefix_override,
        "cert_not_found": lib.cert_not_found,
        "subfolders": list(lib.subfolders),
    }


def _from_dict(raw: dict[str, Any]) -> Library:
    base = new_library(str(raw["path"]), str(raw.get("label", "")), str(raw.get("language", "")))
    return Library(
        path=base.path,
        label=base.label,
        included=bool(raw.get("included", False)),
        language=base.language,
        fallback_language=str(raw.get("fallback_language", "")),
        toggles=frozenset(t for t in raw.get("toggles", []) if t in FIELD_TOGGLES),
        rating_source=int(raw.get("rating_source", base.rating_source)),
        text_source_order=int(raw.get("text_source_order", base.text_source_order)),
        fallback_order=int(raw.get("fallback_order", base.fallback_order)),
        title_mode=str(raw.get("title_mode", base.title_mode)),
        cert_country=int(raw.get("cert_country", base.cert_country)),
        cert_replace_foreign=bool(raw.get("cert_replace_foreign", base.cert_replace_foreign)),
        cert_prefix_override=raw.get("cert_prefix_override"),
        cert_not_found=raw.get("cert_not_found"),
        subfolders=tuple(dict(e) for e in raw.get("subfolders", [])),
    )


def load_libraries(path: str) -> Optional[LibraryConfig]:
    """The v2 config, or None when the file is missing, unreadable or not
    version 2 (a 1.1 file is converted elsewhere)."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        if not isinstance(raw, dict) or raw.get("version") != VERSION:
            return None
        return LibraryConfig([_from_dict(e) for e in raw.get("libraries", [])])
    except (OSError, ValueError, KeyError, TypeError):
        log.exception("Failed to read libraries", event="libraries.load_fail", path=path)
        return None


def save_libraries(path: str, config: LibraryConfig) -> bool:
    """Write the v2 file, creating parent directories. False on failure."""
    data = {"version": VERSION, "libraries": [_to_dict(lib) for lib in config.libraries]}
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except OSError:
        log.exception("Failed to save libraries", event="libraries.save_fail", path=path)
        return False
    log.info("Libraries saved", event="libraries.save", path=path,
             library_count=len(config.libraries))
    return True


def resolve_country(lib: Library, ui_language: str) -> Optional[str]:
    """The certification country: Automatic follows the library's language
    region, then Kodi's interface language."""
    if lib.cert_country == 0:
        return (country_for_setting(0, lib.language)
                or country_for_setting(0, ui_language))
    return country_for_setting(lib.cert_country, ui_language)


def scan_settings(lib: Library, general: dict[str, Any], ui_language: str) -> dict[str, Any]:
    """The general settings plus the library's own, in the shape the scan
    engine reads. cert_sites stays a general setting."""
    return {
        **general,
        "rating_source": lib.rating_source,
        "text_source_order": lib.text_source_order,
        "fallback_order": lib.fallback_order,
        "title_mode": lib.title_mode,
        "cert_country": resolve_country(lib, ui_language),
        "cert_prefix_override": lib.cert_prefix_override,
        "cert_replace_foreign": lib.cert_replace_foreign,
        "cert_not_found": lib.cert_not_found,
    }

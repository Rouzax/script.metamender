"""
Per-source configuration for MetaMender.

Manages which Kodi video sources are enabled for backfill
and what metadata language to use for each.

Logging:
    Logger: 'sources'
    Key events:
        - sources.load (INFO): Source config loaded
        - sources.discover (INFO): Video sources discovered from Kodi
        - sources.save (INFO): Source config saved
        - sources.match (DEBUG): Item matched to a source
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

import xbmc

from resources.lib.utils import get_logger, log_timing

log = get_logger('sources')


def _default_config() -> Dict[str, Any]:
    """Return a fresh default config (no shared mutable state)."""
    return {"sources": [], "default_language": "en-US"}


def _default_match() -> Dict[str, Any]:
    """Return a fresh default match result."""
    return {"enabled": True, "language": "en-US"}


def load_source_config(config_path: str) -> Dict[str, Any]:
    """Load source configuration from a JSON file.

    Args:
        config_path: Absolute path to the sources.json file.

    Returns:
        The parsed config dict. If the file does not exist or cannot
        be read, returns a default config with an empty sources list
        and "en-US" language.
    """
    if not os.path.exists(config_path):
        log.info(
            "No source config found, using defaults",
            event="sources.load",
            path=config_path,
        )
        return _default_config()

    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            config = json.load(fh)
    except (json.JSONDecodeError, IOError, OSError):
        log.exception(
            "Failed to read source config, using defaults",
            event="sources.load",
            path=config_path,
        )
        return _default_config()

    log.info(
        "Source config loaded",
        event="sources.load",
        path=config_path,
        source_count=len(config.get("sources", [])),
    )
    return config


def save_source_config(config_path: str, config: Dict[str, Any]) -> None:
    """Write source configuration to a JSON file.

    Creates parent directories if they do not exist.

    Args:
        config_path: Absolute path to the sources.json file.
        config: The config dict to write.
    """
    parent = os.path.dirname(config_path)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    try:
        with open(config_path, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
    except (IOError, OSError):
        log.exception(
            "Failed to save source config",
            event="sources.save_fail",
            path=config_path,
        )
        return

    log.info(
        "Source config saved",
        event="sources.save",
        path=config_path,
    )


def discover_sources() -> List[Dict[str, str]]:
    """Discover video sources from Kodi via JSON-RPC.

    Calls Files.GetSources with media="video" and returns a list of
    dicts with "path" and "label" keys.

    Returns:
        List of source dicts, or empty list on failure.
    """
    query = {
        "jsonrpc": "2.0",
        "method": "Files.GetSources",
        "params": {"media": "video"},
        "id": 1,
    }

    try:
        with log_timing(log, "discover_sources"):
            raw = xbmc.executeJSONRPC(json.dumps(query))
            data = json.loads(raw)
    except Exception:
        log.exception(
            "Failed to discover video sources",
            event="sources.discover",
        )
        return []

    result = data.get("result", {})
    sources = result.get("sources", [])

    discovered = [
        {"path": s.get("file", ""), "label": s.get("label", "")}
        for s in sources
    ]

    log.info(
        "Video sources discovered",
        event="sources.discover",
        count=len(discovered),
    )

    return discovered


def match_source(
    file_path: str,
    sources: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Match a file path to the most specific configured source.

    Uses longest-prefix matching: the source whose path is the longest
    prefix of file_path wins.

    Args:
        file_path: The Kodi file path for a library item.
        sources: List of source config dicts with at least "path" key.

    Returns:
        The matching source config dict, or a default dict with
        enabled=True and language="en-US" if no source matches.
    """
    best_match = None  # type: Any
    best_length = 0

    for source in sources:
        source_path = source.get("path", "")
        if file_path.startswith(source_path) and len(source_path) > best_length:
            best_match = source
            best_length = len(source_path)

    if best_match is not None:
        log.debug(
            "Item matched to source",
            event="sources.match",
            file_path=file_path,
            source_path=best_match.get("path", ""),
            language=best_match.get("language", ""),
        )
        return best_match

    log.debug(
        "No source match, using defaults",
        event="sources.match",
        file_path=file_path,
    )
    return _default_match()

"""
Shared utilities for MetaMender addon.

Logging:
    This module contains the StructuredLogger implementation used by all
    other modules. See the logging doc for complete guidelines.

    Key exports:
        - StructuredLogger: Thread-safe structured logging class
        - get_logger(module_name): Factory function to get module loggers
        - log_timing: Context manager for timing operations
"""
from __future__ import annotations

import os
import re
import threading
import time
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime as dt
from typing import Any, TextIO

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.constants import (
    ADDON_PREFIX,
    DEFAULT_ADDON_ID,
    LOG_DIR_NAME,
    LOG_FILE_PREFIX,
    LOG_MAX_FILES,
    LOG_MAX_VALUE_LENGTH,
    LOG_TIMESTAMP_FORMAT,
    LOG_TIMESTAMP_TRIM,
)

# TMDB v3 takes its key as a query parameter, and requests puts the full URL
# into its exception messages, so a key reaches log text through error= values
# and tracebacks. Covers both the query form (api_key=...) and a repr'd params
# dict ('api_key': '...'). Also covers JSON bodies ({"apikey": "..."}) and
# bearer tokens ("Authorization: Bearer ..."), which TVDB uses.
_SECRET_RE = re.compile(
    r"""(?i)((?:api_?key(?:=|['"]?\s*:\s*['"]?))|(?:\bbearer\s+))[^&\s'",}]+"""
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", text)


def is_empty(value: Any) -> bool:
    """Whether a value is effectively empty (None, "", 0, 0.0, or [])."""
    if value is None:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (int, float)) and value == 0:
        return True
    return isinstance(value, list) and len(value) == 0


# Singleton addon instance
_addon: xbmcaddon.Addon | None = None


def get_addon() -> xbmcaddon.Addon:
    """Get the addon instance (cached)."""
    global _addon
    if _addon is None:
        try:
            _addon = xbmcaddon.Addon()
        except RuntimeError:
            _addon = xbmcaddon.Addon(DEFAULT_ADDON_ID)
    return _addon


def get_setting(setting_id: str) -> str:
    """Get a setting value as string."""
    return get_addon().getSetting(setting_id)


def get_bool_setting(setting_id: str) -> bool:
    """Get a boolean setting value."""
    return get_setting(setting_id) == 'true'


def get_int_setting(setting_id: str, default: int = 0) -> int:
    """Get an integer setting value."""
    try:
        return int(float(get_setting(setting_id)))
    except (ValueError, TypeError):
        return default


def get_float_setting(setting_id: str, default: float = 0.0) -> float:
    """Get a float setting value."""
    try:
        return float(get_setting(setting_id))
    except (ValueError, TypeError):
        return default


def lang(string_id: int) -> str:
    """Get localized string."""
    return get_addon().getLocalizedString(string_id)


class ApiKeyError(Exception):
    """Raised when a provider returns HTTP 401 (invalid API key)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__("Invalid API key for {}".format(provider))


class TvdbAuthError(Exception):
    """TheTVDB login failed, or a request was still refused with 401 after one
    fresh login. TVDB is unusable for the rest of the cycle; other providers
    are unaffected."""


class TransientProviderError(Exception):
    """A provider request failed transiently (429, 5xx, timeout, connection,
    malformed JSON). Counts toward the circuit breaker; the item is retried.
    ``status`` is the HTTP status when one caused it, so a caller can tell a
    rate limit or a block (429, 403) from an outage."""

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class KodiQueryError(Exception):
    """A Kodi JSON-RPC query failed (RPC error or parse failure), as opposed
    to returning an empty result. Callers must not treat this as 'no items'."""


# =============================================================================
# Structured Logger
# =============================================================================


class StructuredLogger:
    """
    Structured logging with dual output: Kodi log + private file.

    Output Routing:
        - ERROR/WARNING/INFO: Kodi log (always) + scan log file (if open)
        - DEBUG: scan log file only (never pollutes Kodi log)

    File lifecycle:
        Call ``start_scan_log()`` at the beginning of each scan to open a
        new timestamped log file. Call ``stop_scan_log()`` when the scan
        finishes. Between scans no file is open and DEBUG lines are
        discarded. Old log files beyond LOG_MAX_FILES are cleaned up
        automatically when a new file is opened.

    Thread Safety:
        All file operations protected by threading.Lock().
    """

    _log_file: TextIO | None = None
    _log_file_path: str | None = None
    _log_dir: str | None = None
    _addon_id: str = DEFAULT_ADDON_ID
    _debug_enabled: bool = False
    _initialized: bool = False
    _lock: threading.Lock = threading.Lock()

    def __init__(self, module_name: str) -> None:
        self.module = module_name

    @classmethod
    def initialize(cls, debug_enabled: bool, addon_id: str = DEFAULT_ADDON_ID) -> None:
        """Configure the logging system. Does NOT open a file.

        Call ``start_scan_log()`` separately to open a per-scan log file.
        """
        with cls._lock:
            cls._addon_id = addon_id
            cls._debug_enabled = debug_enabled
            cls._initialized = True

            if debug_enabled:
                cls._ensure_log_dir()

    @classmethod
    def reconfigure(cls, debug_enabled: bool, addon_id: str = DEFAULT_ADDON_ID) -> None:
        """Re-run initialization, discarding any prior configuration.

        Use this from one-shot entry points (service, manual scan, action
        handlers) instead of poking the private ``_initialized`` flag. Resetting
        and re-initializing both happen under the class lock.
        """
        with cls._lock:
            cls._initialized = False
        cls.initialize(debug_enabled=debug_enabled, addon_id=addon_id)

    @classmethod
    def _ensure_log_dir(cls) -> None:
        """Create the log directory if needed. Must hold lock."""
        try:
            log_dir = xbmcvfs.translatePath(
                "special://profile/addon_data/{}/{}/".format(cls._addon_id, LOG_DIR_NAME)
            )
            if not xbmcvfs.exists(log_dir):
                xbmcvfs.mkdirs(log_dir)
            cls._log_dir = log_dir
        except (OSError, IOError) as e:
            xbmc.log(
                "[{}.logging] Failed to create log directory: {}".format(ADDON_PREFIX, e),
                xbmc.LOGWARNING,
            )
            cls._log_dir = None

    @classmethod
    def start_scan_log(cls) -> str | None:
        """Open a new timestamped log file for this scan.

        Cleans up old files beyond LOG_MAX_FILES. Returns the file path
        or None if logging is disabled or the directory is unavailable.
        """
        with cls._lock:
            if not cls._debug_enabled or cls._log_dir is None:
                return None

            if cls._log_file is not None:
                try:
                    cls._log_file.close()
                except (OSError, IOError):
                    pass
                cls._log_file = None

            timestamp = dt.now().strftime("%Y-%m-%d_%H%M%S")
            filename = "{}_{}.log".format(LOG_FILE_PREFIX, timestamp)
            log_path = os.path.join(cls._log_dir, filename)

            try:
                cls._log_file = open(log_path, "w", encoding="utf-8")
                cls._log_file_path = log_path
            except (OSError, IOError) as e:
                xbmc.log(
                    "[{}.logging] Failed to open scan log: {}".format(ADDON_PREFIX, e),
                    xbmc.LOGWARNING,
                )
                cls._log_file = None
                return None

            cls._cleanup_old_logs()
            return log_path

    @classmethod
    def stop_scan_log(cls) -> None:
        """Close the current scan log file."""
        with cls._lock:
            if cls._log_file is not None:
                try:
                    cls._log_file.close()
                except (OSError, IOError):
                    pass
                cls._log_file = None

    @classmethod
    def _cleanup_old_logs(cls) -> None:
        """Delete log files beyond LOG_MAX_FILES. Must hold lock."""
        if cls._log_dir is None:
            return
        try:
            files = sorted(
                [
                    f for f in os.listdir(cls._log_dir)
                    if f.startswith(LOG_FILE_PREFIX) and f.endswith(".log")
                ],
                reverse=True,
            )
            for old_file in files[LOG_MAX_FILES:]:
                old_path = os.path.join(cls._log_dir, old_file)
                try:
                    os.remove(old_path)
                except (OSError, IOError):
                    pass
        except (OSError, IOError):
            pass

    @classmethod
    def shutdown(cls) -> None:
        """Close log file and reset state. Call when service stops."""
        with cls._lock:
            if cls._log_file is not None:
                try:
                    cls._log_file.close()
                except (OSError, IOError):
                    pass
                cls._log_file = None
            cls._initialized = False

    def _format_message(self, message: str, **kwargs: Any) -> str:
        """Format: [MetaMend.module] message | key=value, ...

        Secrets are redacted here so every sink is covered, and before
        truncation so a cut cannot leave part of a key behind.
        """
        base = "[{}.{}] {}".format(ADDON_PREFIX, self.module, _redact(message))
        if kwargs:
            pairs = []
            for k, v in kwargs.items():
                str_v = _redact(str(v))
                if k != 'trace' and len(str_v) > LOG_MAX_VALUE_LENGTH:
                    str_v = str_v[:LOG_MAX_VALUE_LENGTH] + "..."
                pairs.append("{}={}".format(k, str_v))
            return "{} | {}".format(base, ", ".join(pairs))
        return base

    def _format_file_line(self, level: str, formatted_message: str) -> str:
        """Add timestamp for file output."""
        timestamp = dt.now().strftime(LOG_TIMESTAMP_FORMAT)[:LOG_TIMESTAMP_TRIM]
        return "{} [{:5}] {}\n".format(timestamp, level, formatted_message)

    def _write_to_file(self, level: str, formatted_message: str) -> None:
        """Write to scan log file if one is open."""
        if StructuredLogger._log_file is None:
            return

        line = self._format_file_line(level, formatted_message)

        with StructuredLogger._lock:
            try:
                if StructuredLogger._log_file is None:
                    return
                StructuredLogger._log_file.write(line)
                StructuredLogger._log_file.flush()
            except (IOError, OSError):
                pass

    def _ensure_event(self, level: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Inject placeholder event= for INFO/WARNING/ERROR if missing."""
        if "event" not in kwargs:
            kwargs["event"] = "misc.{}".format(level)
            kwargs["_missing_event"] = True
        return kwargs

    def debug(self, message: str, **kwargs: Any) -> None:
        """DEBUG: file only when a scan log is open. No event= required."""
        if StructuredLogger._debug_enabled and StructuredLogger._log_file is not None:
            formatted = self._format_message(message, **kwargs)
            self._write_to_file("DEBUG", formatted)

    def info(self, message: str, **kwargs: Any) -> None:
        """INFO: always Kodi log + scan log file if open. Include event=."""
        kwargs = self._ensure_event("info", kwargs)
        formatted = self._format_message(message, **kwargs)
        xbmc.log(formatted, xbmc.LOGINFO)
        self._write_to_file("INFO", formatted)

    def warning(self, message: str, **kwargs: Any) -> None:
        """WARNING: always Kodi log + scan log file if open. Include event=."""
        kwargs = self._ensure_event("warning", kwargs)
        formatted = self._format_message(message, **kwargs)
        xbmc.log(formatted, xbmc.LOGWARNING)
        self._write_to_file("WARN", formatted)

    def error(self, message: str, **kwargs: Any) -> None:
        """ERROR: always Kodi log + scan log file if open. Include event=."""
        kwargs = self._ensure_event("error", kwargs)
        formatted = self._format_message(message, **kwargs)
        xbmc.log(formatted, xbmc.LOGERROR)
        self._write_to_file("ERROR", formatted)

    def exception(self, message: str, **kwargs: Any) -> None:
        """ERROR + automatic stack trace. Use in except blocks."""
        kwargs["trace"] = traceback.format_exc()
        self.error(message, **kwargs)


def get_logger(module_name: str) -> StructuredLogger:
    """
    Get a logger for a module. Auto-initializes on first call.

    Args:
        module_name: Name to identify the module (e.g., 'service', 'data').
    """
    if not StructuredLogger._initialized:
        try:
            debug_enabled = get_bool_setting('logging')
        except RuntimeError:
            debug_enabled = False

        try:
            addon_id = get_addon().getAddonInfo('id')
        except RuntimeError:
            addon_id = DEFAULT_ADDON_ID

        StructuredLogger.initialize(debug_enabled=debug_enabled, addon_id=addon_id)

    return StructuredLogger(module_name)


_notify_log = StructuredLogger('notify')


def notify(message: str, time_ms: int = 5000) -> None:
    """Show a Kodi notification with the addon icon."""
    try:
        addon = get_addon()
        heading = addon.getAddonInfo('name')
        icon = addon.getAddonInfo('icon')
        xbmcgui.Dialog().notification(heading, message, icon, time_ms)
        _notify_log.debug("Notification shown", text=message)
    except RuntimeError:
        pass


# =============================================================================
# Timing
# =============================================================================


class TimedOperation:
    """Timer with phase markers for breaking down an operation's duration."""

    def __init__(self, start_time: float) -> None:
        self._start = start_time
        self._phases: dict[str, float] = {}
        self._last_mark = start_time

    def mark(self, phase_name: str) -> None:
        """Record elapsed time for a phase (time since previous mark or start)."""
        now = time.perf_counter()
        self._phases[phase_name] = now - self._last_mark
        self._last_mark = now

    def _get_phase_kwargs(self) -> dict[str, int]:
        return {
            "{}_ms".format(name): int(duration * 1000)
            for name, duration in self._phases.items()
        }


@contextmanager
def log_timing(
    logger: StructuredLogger,
    operation: str,
    **context: Any
) -> Generator[TimedOperation, None, None]:
    """
    Time a code block with optional phase breakdown.

    Example:
        with log_timing(log, "scan", show_count=100) as timer:
            query_shows()
            timer.mark("query")
            process_shows()
            timer.mark("process")
    """
    start = time.perf_counter()
    timer = TimedOperation(start)
    try:
        yield timer
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        phase_kwargs = timer._get_phase_kwargs()
        logger.debug(
            "{} completed".format(operation),
            duration_ms=elapsed_ms,
            **phase_kwargs,
            **context
        )


# =============================================================================
# JSON-RPC
# =============================================================================



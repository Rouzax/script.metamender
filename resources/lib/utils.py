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

import json
import os
import ssl
import threading
import time
import traceback
import unicodedata
from contextlib import contextmanager
from datetime import datetime as dt
from typing import TYPE_CHECKING, Any, Dict, Generator, Optional, TextIO, Union

if TYPE_CHECKING:
    import requests

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.constants import (
    ADDON_PREFIX,
    DEFAULT_ADDON_ID,
    LOG_DIR_NAME,
    LOG_FILENAME,
    LOG_MAX_ROTATED_FILES,
    LOG_MAX_SIZE_BYTES,
    LOG_MAX_VALUE_LENGTH,
    LOG_TIMESTAMP_FORMAT,
    LOG_TIMESTAMP_TRIM,
)


_TITLE_SEPARATORS = frozenset((" ", ":", "-"))


def normalize_for_compare(text: str) -> str:
    """Strip diacritics and lowercase for accent-insensitive comparison."""
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def title_matches(result_title: str, search_title: str) -> bool:
    """Check if result_title matches search_title.

    Tries exact match first (preserving diacritics), then prefix match
    with separator awareness (space, colon, dash). Falls back to
    accent-normalized comparison for both.
    """
    s_lower = search_title.lower()
    r_lower = result_title.lower()

    if r_lower == s_lower:
        return True

    if len(r_lower) > len(s_lower) and r_lower.startswith(s_lower):
        if r_lower[len(s_lower)] in _TITLE_SEPARATORS:
            return True

    s_norm = normalize_for_compare(search_title)
    r_norm = normalize_for_compare(result_title)

    if r_norm == s_norm:
        return True

    if len(r_norm) > len(s_norm) and r_norm.startswith(s_norm):
        if r_norm[len(s_norm)] in _TITLE_SEPARATORS:
            return True

    return False


def create_scraper_session() -> "requests.Session":
    """Create a requests session with browser-like TLS settings.

    OpenSSL 3.0 SECLEVEL=2 restricts the cipher list to ~17 entries,
    producing a JA3 fingerprint that Akamai flags as non-browser.
    SECLEVEL=1 restores the full ~60-cipher set.
    """
    import requests
    from requests.adapters import HTTPAdapter

    class _BrowserTLSAdapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):  # type: ignore[override]
            try:
                ctx = ssl.create_default_context()
                ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
                kwargs["ssl_context"] = ctx
            except (ssl.SSLError, ValueError):
                pass
            return super().init_poolmanager(*args, **kwargs)

    session = requests.Session()
    session.mount("https://", _BrowserTLSAdapter())
    return session


# Singleton addon instance
_addon: Optional[xbmcaddon.Addon] = None


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


def get_country_code() -> str:
    """Get the selected country code from the settings dropdown."""
    from resources.lib.constants import COUNTRY_CODES, DEFAULT_COUNTRY_CODE
    index = get_int_setting('country', 0)
    if 0 <= index < len(COUNTRY_CODES):
        return COUNTRY_CODES[index]
    return DEFAULT_COUNTRY_CODE


class ApiKeyError(Exception):
    """Raised when a provider returns HTTP 401 (invalid API key)."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__("Invalid API key for {}".format(provider))


# =============================================================================
# Structured Logger
# =============================================================================


class StructuredLogger:
    """
    Structured logging with dual output: Kodi log + private file.

    Output Routing:
        - ERROR/WARNING/INFO: Kodi log (always) + addon log (if debug enabled)
        - DEBUG: addon log only (never pollutes Kodi log)

    Thread Safety:
        All file operations protected by threading.Lock().
    """

    _log_file: Optional[TextIO] = None
    _log_file_path: Optional[str] = None
    _log_file_size: int = 0
    _addon_id: str = DEFAULT_ADDON_ID
    _debug_enabled: bool = False
    _initialized: bool = False
    _lock: threading.Lock = threading.Lock()

    def __init__(self, module_name: str) -> None:
        self.module = module_name

    @classmethod
    def initialize(cls, debug_enabled: bool, addon_id: str = DEFAULT_ADDON_ID) -> None:
        """Initialize the logging system. Idempotent."""
        with cls._lock:
            cls._addon_id = addon_id
            cls._debug_enabled = debug_enabled

            if cls._initialized:
                return

            cls._initialized = True

            if debug_enabled:
                cls._init_log_file()

    @classmethod
    def _init_log_file(cls) -> None:
        """Set up log file with rotation check. Must hold lock."""
        try:
            log_dir = xbmcvfs.translatePath(
                "special://profile/addon_data/{}/{}/".format(cls._addon_id, LOG_DIR_NAME)
            )

            if not xbmcvfs.exists(log_dir):
                xbmcvfs.mkdirs(log_dir)

            log_path = os.path.join(log_dir, LOG_FILENAME)

            existing_size = 0
            if xbmcvfs.exists(log_path):
                try:
                    stat_result = xbmcvfs.Stat(log_path)
                    existing_size = stat_result.st_size()
                except (OSError, AttributeError):
                    existing_size = 0

                if existing_size > LOG_MAX_SIZE_BYTES:
                    cls._rotate_logs(log_dir)
                    existing_size = 0

            cls._log_file_path = log_path
            try:
                cls._log_file = open(log_path, "a", encoding="utf-8")
                cls._log_file_size = existing_size
            except (OSError, IOError):
                cls._log_file = None
                raise
        except (OSError, IOError) as e:
            xbmc.log(
                "[{}.logging] Failed to initialize log file: {}".format(ADDON_PREFIX, e),
                xbmc.LOGWARNING
            )
            cls._log_file = None

    @classmethod
    def _rotate_logs(cls, log_dir: str) -> None:
        """Rotate log files. Must hold lock."""
        try:
            base = LOG_FILENAME.replace(".log", "")

            oldest = os.path.join(log_dir, "{}.{}.log".format(base, LOG_MAX_ROTATED_FILES))
            if xbmcvfs.exists(oldest):
                xbmcvfs.delete(oldest)

            for i in range(LOG_MAX_ROTATED_FILES - 1, 0, -1):
                src = os.path.join(log_dir, "{}.{}.log".format(base, i))
                dst = os.path.join(log_dir, "{}.{}.log".format(base, i + 1))
                if xbmcvfs.exists(src):
                    xbmcvfs.rename(src, dst)

            current = os.path.join(log_dir, LOG_FILENAME)
            if xbmcvfs.exists(current):
                xbmcvfs.rename(current, os.path.join(log_dir, "{}.1.log".format(base)))
        except (OSError, IOError):
            pass

    @classmethod
    def shutdown(cls) -> None:
        """Close log file. Call when service stops."""
        with cls._lock:
            if cls._log_file:
                try:
                    cls._log_file.close()
                except (OSError, IOError):
                    pass
                cls._log_file = None
            cls._initialized = False

    def _format_message(self, message: str, **kwargs: Any) -> str:
        """Format: [MetaMend.module] message | key=value, ..."""
        base = "[{}.{}] {}".format(ADDON_PREFIX, self.module, message)
        if kwargs:
            pairs = []
            for k, v in kwargs.items():
                str_v = str(v)
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
        """Write to file if enabled, with thread safety and mid-session rotation."""
        if not StructuredLogger._debug_enabled or StructuredLogger._log_file is None:
            return

        line = self._format_file_line(level, formatted_message)

        with StructuredLogger._lock:
            try:
                if StructuredLogger._log_file is None:
                    return

                StructuredLogger._log_file.write(line)
                StructuredLogger._log_file.flush()
                StructuredLogger._log_file_size += len(line.encode("utf-8"))

                if StructuredLogger._log_file_size > LOG_MAX_SIZE_BYTES:
                    StructuredLogger._log_file.close()
                    log_dir = os.path.dirname(StructuredLogger._log_file_path or "")
                    if log_dir:
                        StructuredLogger._rotate_logs(log_dir)
                    StructuredLogger._init_log_file()
            except (IOError, OSError):
                pass

    def _ensure_event(self, level: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Inject placeholder event= for INFO/WARNING/ERROR if missing."""
        if "event" not in kwargs:
            kwargs["event"] = "misc.{}".format(level)
            kwargs["_missing_event"] = True
        return kwargs

    def debug(self, message: str, **kwargs: Any) -> None:
        """DEBUG: file only when enabled. No event= required."""
        if StructuredLogger._debug_enabled:
            formatted = self._format_message(message, **kwargs)
            self._write_to_file("DEBUG", formatted)

    def info(self, message: str, **kwargs: Any) -> None:
        """INFO: always Kodi log + file when enabled. Include event=."""
        kwargs = self._ensure_event("info", kwargs)
        formatted = self._format_message(message, **kwargs)
        xbmc.log(formatted, xbmc.LOGINFO)
        self._write_to_file("INFO", formatted)

    def warning(self, message: str, **kwargs: Any) -> None:
        """WARNING: always Kodi log + file when enabled. Include event=."""
        kwargs = self._ensure_event("warning", kwargs)
        formatted = self._format_message(message, **kwargs)
        xbmc.log(formatted, xbmc.LOGWARNING)
        self._write_to_file("WARN", formatted)

    def error(self, message: str, **kwargs: Any) -> None:
        """ERROR: always Kodi log + file when enabled. Include event=."""
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
        self._phases: Dict[str, float] = {}
        self._last_mark = start_time

    def mark(self, phase_name: str) -> None:
        """Record elapsed time for a phase (time since previous mark or start)."""
        now = time.perf_counter()
        self._phases[phase_name] = now - self._last_mark
        self._last_mark = now

    def _get_phase_kwargs(self) -> Dict[str, int]:
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


def json_query(query: Union[Dict[str, Any], str], return_result: bool = True) -> Dict[str, Any]:
    """
    Execute a JSON-RPC query against Kodi.

    Args:
        query: The JSON-RPC query dict (or pre-serialized string).
        return_result: If True, return only the 'result' key.
    """
    try:
        request = json.dumps(query) if isinstance(query, dict) else query
        response = xbmc.executeJSONRPC(request)
        data = json.loads(response)

        if return_result:
            return data.get('result', {})
        return data
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}

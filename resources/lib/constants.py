"""Centralized constants for MetaMender addon."""
from __future__ import annotations

from typing import Tuple

# =============================================================================
# Addon Identity
# =============================================================================

DEFAULT_ADDON_ID = "script.metamender"
ADDON_PREFIX = "MetaMend"

# =============================================================================
# Logging
# =============================================================================

LOG_DIR_NAME = "logs"
LOG_FILE_PREFIX = "metamender"
LOG_MAX_FILES = 10
LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
LOG_TIMESTAMP_TRIM = -3  # Trim microseconds to milliseconds
LOG_MAX_VALUE_LENGTH = 200

# =============================================================================
# Service
# =============================================================================

KODI_HOME_WINDOW_ID = 10000

# Default scan interval in hours
DEFAULT_SCAN_INTERVAL_HOURS = 24

# Sleep granularity: how often the service loop checks for abort (seconds)
SERVICE_SLEEP_INTERVAL_SEC = 1

# Days to retry unresolved items before applying fallback
DEFAULT_RETRY_DAYS = 30

# Seconds to wait after library scan/clean finishes before triggering a rating scan
LIBRARY_SCAN_DEBOUNCE_SEC = 300

# Backoff before retrying a scan that aborted on a Kodi query failure.
# Short relative to the full scan interval so a transient RPC failure recovers
# quickly without re-hammering Kodi every loop iteration.
SCAN_FAILURE_BACKOFF_SEC = 300

# =============================================================================
# Rating Defaults
# =============================================================================

DEFAULT_FALLBACK_RATING = "NR"
KNOWN_RATING_PREFIXES = ("Rated ",)

# =============================================================================
# Country Presets
# =============================================================================

PRESETS_DIR = "presets"
DEFAULT_COUNTRY_CODE = "NL"
COUNTRY_CODES = ("NL", "BE", "DE", "AT", "GB", "US", "FR", "DK", "SE")

# =============================================================================
# Backfill Tracker (SQLite)
# =============================================================================

BACKFILL_DB_FILENAME = "backfill.db"
SOURCES_CONFIG_FILENAME = "sources.json"

# Tracker SQLite schema version. Bump when the schema changes so future
# migrations have a marker to branch on. Stored in scan_state on init.
SCHEMA_VERSION = 2

# Retry intervals in days (attempt 1 -> 7d, attempt 2 -> 30d, attempt 3 -> 90d)
RETRY_INTERVALS = (7, 30, 90)
DEFAULT_MAX_RETRIES = 4

# Days after a field exhausts before it gets ONE automatic re-attempt; if that
# also finds nothing, the field is permanently exhausted (next_retry cleared).
EXHAUSTED_AUTORETRY_DAYS = 180

# Default refetch schedule in days (attempt 1 -> 7d, attempt 2 -> 30d, attempt 3 -> 90d)
DEFAULT_REFETCH_SCHEDULE = (7, 30, 90)  # type: Tuple[int, ...]


def parse_refetch_schedule(value: str) -> Tuple[int, ...]:
    """Parse a 'comma,separated,days' schedule string, dropping non-positive
    entries. Falls back to DEFAULT_REFETCH_SCHEDULE on empty or malformed input."""
    try:
        parts = [int(p.strip()) for p in value.split(",") if p.strip()]
    except (ValueError, AttributeError):
        return DEFAULT_REFETCH_SCHEDULE
    parts = [p for p in parts if p > 0]
    return tuple(parts) if parts else DEFAULT_REFETCH_SCHEDULE


# Disable a provider after this many consecutive failures in a single scan
PROVIDER_CIRCUIT_BREAKER_THRESHOLD = 5

# =============================================================================
# External APIs
# =============================================================================

TMDB_BASE_URL = "https://api.themoviedb.org/3"
IMDBAPI_BASE_URL = "https://api.imdbapi.dev"
FSK_API_URL = "https://www.fsk.de/fskapi/ReleaseSearch"
BBFC_SEARCH_URL = "https://www.bbfc.co.uk/search"
KIJKWIJZER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

# Explicit User-Agent for provider API calls. imdbapi.dev filters by UA (a bare
# urllib UA is rejected); a browser-like UA is reliably accepted.
METAMENDER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# =============================================================================
# Scraper Canaries
# =============================================================================

SCRAPER_CANARIES = {
    "fsk": {"title": "The Matrix", "expected": "16", "year": 1999},
    "bbfc": {"title": "Interstellar", "expected": "12", "year": 2014},
    "medieraadet": {"title": "Interstellar", "expected": "11", "year": 2014},
    "kijkwijzer": {"title": "Interstellar", "expected": "12", "year": 2014},
}

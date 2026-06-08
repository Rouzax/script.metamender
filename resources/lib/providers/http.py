"""Shared HTTP helper for provider API calls.

Centralizes the outbound request, the User-Agent, and HTTP error
classification so every provider fetch handles failures the same way.

Classification:
    200            -> parsed JSON (dict)
    404            -> None (legitimate "no data found")
    401            -> ApiKeyError (fatal for that provider)
    429/5xx        -> TransientProviderError (counts toward circuit breaker)
    timeout/conn   -> TransientProviderError
    malformed JSON -> TransientProviderError

Logging:
    Uses the caller's logger; events use the caller's domain via `label`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from resources.lib.constants import METAMENDER_USER_AGENT
from resources.lib.utils import ApiKeyError, TransientProviderError

_HEADERS = {"User-Agent": METAMENDER_USER_AGENT}


def provider_get(
    url: str,
    logger: Any,
    label: str,
    params: Optional[Dict[str, Any]] = None,
    provider: str = "",
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """GET a provider URL and classify the outcome.

    Returns parsed JSON on 200, None on 404. Raises ApiKeyError on 401 and
    TransientProviderError on 429/5xx/timeout/connection/malformed-JSON.
    `label` is used as the log event domain (e.g. "tmdb.movie").
    """
    try:
        resp = requests.get(url, params=params, timeout=timeout, headers=_HEADERS)
    except requests.RequestException as exc:
        logger.warning(
            "Transient request failure",
            event="{}.transient".format(label),
            error=str(exc),
        )
        raise TransientProviderError(str(exc))

    status = resp.status_code
    if status == 200:
        try:
            return resp.json()
        except ValueError:
            logger.warning(
                "Malformed JSON",
                event="{}.transient".format(label),
            )
            raise TransientProviderError("malformed json")
    if status == 404:
        return None
    if status == 401:
        raise ApiKeyError(provider or label)
    if status == 429 or status >= 500:
        logger.warning(
            "Transient HTTP status",
            event="{}.transient".format(label),
            status_code=status,
        )
        raise TransientProviderError("http {}".format(status))
    # Other 4xx: treat as no data (not retryable as a provider failure)
    logger.warning(
        "Unexpected status, treating as no data",
        event="{}.nodata".format(label),
        status_code=status,
    )
    return None

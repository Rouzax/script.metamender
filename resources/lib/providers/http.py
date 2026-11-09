"""Shared HTTP helper for provider API calls.

Centralizes the outbound request, the User-Agent, and HTTP error
classification so every provider fetch handles failures the same way.

Classification:
    200             -> parsed JSON (dict)
    404            -> None (legitimate "no data found")
    401            -> ApiKeyError (fatal for that provider)
    403/408/425/429/5xx -> TransientProviderError (counts toward circuit breaker)
    400            -> TransientProviderError when the caller sets
                      transient_400 (TVDB answers 400 for request problems that
                      are not a "no data" result), else None
    other 4xx      -> None (no data; not a provider failure)
    timeout/conn   -> TransientProviderError
    malformed JSON -> TransientProviderError

403 is treated as transient because it is how bot-protection layers (e.g. Akamai)
reject automated requests; if that becomes persistent it should trip the breaker
and stop the cycle rather than silently spin through the whole library.

Logging:
    Request bodies and headers are never logged. Exception text from requests
    can embed a URL, and is redacted before it enters a raised error.
    Uses the caller's logger; events use the caller's domain via `label`.
"""
from __future__ import annotations

from typing import Any, Dict, NoReturn, Optional

import requests

from resources.lib.constants import METAMENDER_USER_AGENT
from resources.lib.utils import ApiKeyError, TransientProviderError, _redact

_HEADERS = {"User-Agent": METAMENDER_USER_AGENT}


def provider_get(
    url: str,
    logger: Any,
    label: str,
    params: Optional[Dict[str, Any]] = None,
    provider: str = "",
    timeout: int = 10,
    headers: Optional[Dict[str, str]] = None,
    transient_400: bool = False,
) -> Optional[Dict[str, Any]]:
    """GET a provider URL and classify the outcome.

    Returns parsed JSON on 200, None on 404. Raises ApiKeyError on 401 and
    TransientProviderError on 429/5xx/timeout/connection/malformed-JSON, and
    on 400 when `transient_400` is set. `headers` are merged over the default
    User-Agent. `label` is used as the log event domain (e.g. "tmdb.movie").
    """
    try:
        resp = requests.get(
            url, params=params, timeout=timeout, headers=_merge_headers(headers)
        )
    except requests.RequestException as exc:
        _raise_transport_error(logger, label, exc)
    return _classify(resp, logger, label, provider, transient_400)


def provider_post(
    url: str,
    logger: Any,
    label: str,
    json_body: Dict[str, Any],
    provider: str = "",
    timeout: int = 10,
    headers: Optional[Dict[str, str]] = None,
    transient_400: bool = False,
) -> Optional[Dict[str, Any]]:
    """POST a JSON body to a provider URL; classified exactly like provider_get."""
    try:
        resp = requests.post(
            url, json=json_body, timeout=timeout, headers=_merge_headers(headers)
        )
    except requests.RequestException as exc:
        _raise_transport_error(logger, label, exc)
    return _classify(resp, logger, label, provider, transient_400)


def _merge_headers(extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    merged = dict(_HEADERS)
    if extra:
        merged.update(extra)
    return merged


def _raise_transport_error(logger: Any, label: str, exc: Exception) -> NoReturn:
    # Deliberate warning() not exception(): this fires on every transient
    # failure (429/timeout/conn reset), so a stack trace per occurrence
    # would be log noise. The error= kwarg carries the cause.
    cause = _redact(str(exc))
    logger.warning(
        "Transient request failure",
        event="{}.transient".format(label),
        error=cause,
    )
    raise TransientProviderError(cause)


def _classify(
    resp: Any, logger: Any, label: str, provider: str, transient_400: bool
) -> Optional[Dict[str, Any]]:
    """Map a response to parsed JSON, None, or a raised provider error."""
    status = resp.status_code
    if status == 200:
        try:
            return resp.json()
        except ValueError:
            # Deliberate warning() not exception(): malformed JSON is a
            # transient provider hiccup that can recur; a stack trace per
            # occurrence would be log noise.
            logger.warning(
                "Malformed JSON",
                event="{}.transient".format(label),
            )
            raise TransientProviderError("malformed json")
    if status == 404:
        logger.debug(
            "No data found (404)",
            event="{}.nodata".format(label),
        )
        return None
    if status == 401:
        raise ApiKeyError(provider or label)
    # 403 (bot block), 408 (request timeout), 425 (too early), 429 (rate limit)
    # and all 5xx are transient: they should count toward the breaker.
    if status in (403, 408, 425, 429) or status >= 500 or (
        status == 400 and transient_400
    ):
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

"""
Staleness detection and retry scheduling.

Determines when existing ratings should be refreshed based on content
age, vote count, and time since last fetch. Computes retry dates using
exponential backoff.

Logging:
    No logging in this module (pure computation).
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional

from resources.lib.constants import DEFAULT_MAX_RETRIES, RETRY_INTERVALS


def is_rating_stale(
    premiered: Optional[str],
    vote_count: int,
    last_fetched: Optional[str],
    min_votes: int,
    stale_threshold_days: int,
) -> bool:
    """Check whether a rating should be refreshed.

    A rating is stale when ALL of these are true:

    1. *premiered* is known and the content was released less than
       *stale_threshold_days* ago.
    2. *vote_count* is below *min_votes*.
    3. *last_fetched* is known and at least 7 days have passed since
       the last fetch.

    Args:
        premiered: Release date as YYYY-MM-DD, or None/empty.
        vote_count: Current number of votes on the rating.
        last_fetched: Date the rating was last fetched (YYYY-MM-DD),
            or None if never fetched.
        min_votes: Minimum vote threshold for the rating to be
            considered stable.
        stale_threshold_days: Number of days after premiere during
            which the rating may still be volatile.

    Returns:
        True if the rating is stale and should be refreshed.
    """
    if not premiered or not last_fetched:
        return False

    today = date.today()

    try:
        premiered_date = date.fromisoformat(premiered)
    except ValueError:
        return False

    content_age = (today - premiered_date).days
    if content_age >= stale_threshold_days:
        return False

    if vote_count >= min_votes:
        return False

    try:
        fetched_date = date.fromisoformat(last_fetched)
    except ValueError:
        return False

    days_since_fetch = (today - fetched_date).days
    if days_since_fetch < 7:
        return False

    return True


def compute_next_retry(
    attempt_count: int,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Optional[str]:
    """Compute the next retry date based on the attempt count.

    Uses the intervals defined in ``RETRY_INTERVALS``. The attempt
    count is 1-based (first attempt = 1).

    Args:
        attempt_count: The current attempt number (after incrementing).
        max_retries: Maximum number of retries before giving up.

    Returns:
        The next retry date as a YYYY-MM-DD string, or None if retries
        are exhausted.
    """
    if attempt_count >= max_retries:
        return None

    index = min(attempt_count - 1, len(RETRY_INTERVALS) - 1)
    base_days = RETRY_INTERVALS[index]
    jitter = random.randint(0, max(1, base_days // 4))
    return (date.today() + timedelta(days=base_days + jitter)).isoformat()

"""Pure per-field maturity decisions (Phase 7c).

Maturity is computed from facts, never stored. A field is FINAL once its content
is past the stability age; before that it is sampled on the refetch schedule.

Logging: none (pure computation).
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Optional, Tuple

FETCH_NOW = "fetch_now"
SKIP_NOT_DUE = "skip_not_due"
FINAL = "final"


def decide_field(
    content_age_days: Optional[int],
    fetch_count: int,
    next_retry: Optional[str],
    today: str,
    threshold_days: int,
) -> str:
    """Decide what to do with a field this scan, before any API call."""
    if fetch_count == 0:
        return FETCH_NOW
    if content_age_days is not None and content_age_days >= threshold_days:
        return FINAL
    # young / unknown-age content, still volatile: fetch when due
    if next_retry is not None and next_retry <= today:
        return FETCH_NOW
    return SKIP_NOT_DUE


def compute_next_due(
    fetch_count: int,
    schedule: Tuple[int, ...],
    premiered: Optional[str],
    threshold_days: int,
    today: str,
    jitter: int = -1,
) -> Optional[str]:
    """Date of the next sample for a field that stays volatile after a fetch.

    On the schedule: today + schedule[fetch_count-1] + jitter. After the schedule
    is exhausted: the settle-capture at premiered + threshold_days. Returns None
    if the settle date cannot be computed (no premiered date)."""
    today_d = date.fromisoformat(today)
    if fetch_count <= len(schedule):
        base = schedule[fetch_count - 1]
        j = random.randint(0, max(1, base // 4)) if jitter < 0 else jitter
        return (today_d + timedelta(days=base + j)).isoformat()
    # settle-capture: due when content crosses the stability age
    if not premiered:
        return None
    try:
        prem_d = date.fromisoformat(premiered)
    except ValueError:
        return None
    return (prem_d + timedelta(days=threshold_days)).isoformat()

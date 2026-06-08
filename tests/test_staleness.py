"""Tests for staleness detection."""
from __future__ import annotations

from datetime import date, timedelta

from resources.lib.engine.staleness import compute_next_retry, is_rating_stale


class TestIsRatingStale:
    def test_recent_content_low_votes_old_fetch_is_stale(self):
        today = date.today()
        premiered = (today - timedelta(days=30)).isoformat()
        last_fetched = (today - timedelta(days=10)).isoformat()
        assert is_rating_stale(premiered, 5, last_fetched, 50, 365) is True

    def test_old_content_is_not_stale(self):
        today = date.today()
        premiered = (today - timedelta(days=400)).isoformat()
        last_fetched = (today - timedelta(days=10)).isoformat()
        assert is_rating_stale(premiered, 5, last_fetched, 50, 365) is False

    def test_enough_votes_is_not_stale(self):
        today = date.today()
        premiered = (today - timedelta(days=30)).isoformat()
        last_fetched = (today - timedelta(days=10)).isoformat()
        assert is_rating_stale(premiered, 500, last_fetched, 50, 365) is False

    def test_recent_fetch_is_not_stale(self):
        today = date.today()
        premiered = (today - timedelta(days=30)).isoformat()
        last_fetched = (today - timedelta(days=3)).isoformat()
        assert is_rating_stale(premiered, 5, last_fetched, 50, 365) is False

    def test_no_premiered_is_not_stale(self):
        last_fetched = (date.today() - timedelta(days=10)).isoformat()
        assert is_rating_stale(None, 5, last_fetched, 50, 365) is False
        assert is_rating_stale("", 5, last_fetched, 50, 365) is False

    def test_no_last_fetched_is_not_stale(self):
        premiered = (date.today() - timedelta(days=30)).isoformat()
        assert is_rating_stale(premiered, 5, None, 50, 365) is False

    def test_exactly_at_threshold_is_not_stale(self):
        today = date.today()
        premiered = (today - timedelta(days=365)).isoformat()
        last_fetched = (today - timedelta(days=10)).isoformat()
        assert is_rating_stale(premiered, 5, last_fetched, 50, 365) is False

    def test_exactly_7_days_since_fetch_is_stale(self):
        today = date.today()
        premiered = (today - timedelta(days=30)).isoformat()
        last_fetched = (today - timedelta(days=7)).isoformat()
        assert is_rating_stale(premiered, 5, last_fetched, 50, 365) is True


class TestComputeNextRetry:
    def test_first_attempt_within_jitter_range(self):
        result = compute_next_retry(1)
        assert result is not None
        result_date = date.fromisoformat(result)
        min_date = date.today() + timedelta(days=7)
        max_date = date.today() + timedelta(days=7 + 7 // 4)
        assert min_date <= result_date <= max_date

    def test_second_attempt_within_jitter_range(self):
        result = compute_next_retry(2)
        assert result is not None
        result_date = date.fromisoformat(result)
        min_date = date.today() + timedelta(days=30)
        max_date = date.today() + timedelta(days=30 + 30 // 4)
        assert min_date <= result_date <= max_date

    def test_third_attempt_within_jitter_range(self):
        result = compute_next_retry(3)
        assert result is not None
        result_date = date.fromisoformat(result)
        min_date = date.today() + timedelta(days=90)
        max_date = date.today() + timedelta(days=90 + 90 // 4)
        assert min_date <= result_date <= max_date

    def test_fourth_attempt_exhausted(self):
        assert compute_next_retry(4) is None

    def test_beyond_max_exhausted(self):
        assert compute_next_retry(5) is None
        assert compute_next_retry(10) is None

    def test_custom_max_retries(self):
        assert compute_next_retry(2, max_retries=2) is None
        assert compute_next_retry(1, max_retries=2) is not None

    def test_jitter_varies(self):
        results = set()
        for _ in range(20):
            results.add(compute_next_retry(2))
        assert len(results) >= 2, "jitter should produce varied dates"

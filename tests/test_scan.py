"""Tests for scan orchestrator."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict  # noqa: F401 (used in type comments)
from unittest.mock import MagicMock, patch

import pytest

from resources.lib.data.identity import compute_identity
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW
from resources.lib.engine.scan import (
    _extract_fields,
    _item_title,
    run_scan,
)


@pytest.fixture(autouse=True)
def _mock_imdb_providers():
    """Patch IMDb provider functions to return None by default.

    Tests that need IMDb data override these with per-test patches.
    """
    with patch("resources.lib.engine.scan.imdb_fetch_movie", return_value=None), \
         patch("resources.lib.engine.scan.imdb_fetch_episode", return_value=None), \
         patch("resources.lib.engine.scan.tmdb_fetch_episode_ids", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tracker(tmp_path):
    """Create a real TrackerDB for testing."""
    from resources.lib.data.tracker import TrackerDB
    return TrackerDB(str(tmp_path / "test.db"))


def _make_source_config():
    # type: () -> Dict[str, Any]
    return {"sources": [], "default_language": "en-US"}


def _make_settings():
    # type: () -> Dict[str, Any]
    return {
        "min_votes_movie": 50,
        "min_votes_episode": 20,
        "stale_threshold_days": 365,
        "max_retries": 4,
        "rate_limit": 0.0,  # no delay in tests
    }


def _make_movie_item(
    title="Test Movie",
    tmdb_id="12345",
    rating=0,
    plot="",
    movie_id=1,
):
    # type: (...) -> Dict[str, Any]
    return {
        "movieid": movie_id,
        "title": title,
        "rating": rating,
        "votes": "0",
        "plot": plot,
        "uniqueid": {"tmdb": tmdb_id, "imdb": "tt0000001"},
        "file": "smb://server/movies/test.mkv",
        "dateadded": "2026-01-01 12:00:00",
        "premiered": "2025-06-15",
        "mpaa": "",
        "tagline": "",
        "director": [],
        "writer": [],
        "genre": ["Action"],
        "studio": [],
        "country": [],
        "trailer": "",
        "runtime": 0,
    }


# Identity the scan computes for the default movie fixture (tmdb uniqueid wins).
_MOVIE_IDENTITY = compute_identity(_make_movie_item(), MOVIE)


def _make_tvshow_item(title="Test Show", tmdb_id="99999"):
    # type: (...) -> Dict[str, Any]
    return {
        "tvshowid": 10,
        "title": title,
        "rating": 0,
        "votes": "0",
        "plot": "",
        "uniqueid": {"tmdb": tmdb_id},
        "file": "smb://server/shows/test/",
        "dateadded": "2026-01-01 12:00:00",
        "premiered": "2025-01-01",
        "mpaa": "",
        "genre": [],
    }


def _make_episode_item(
    showtitle="Test Show",
    season=1,
    episode=1,
    title="Episode 1",
    tmdb_id="99999",
):
    # type: (...) -> Dict[str, Any]
    return {
        "episodeid": 100,
        "title": title,
        "showtitle": showtitle,
        "season": season,
        "episode": episode,
        "rating": 0,
        "votes": "0",
        "plot": "",
        "uniqueid": {"tmdb": tmdb_id},
        "file": "smb://server/shows/test/S01E01.mkv",
        "dateadded": "2026-01-01 12:00:00",
        "firstaired": "",
        "director": [],
        "writer": [],
        "runtime": 0,
    }


def _tmdb_movie_response(**overrides):
    # type: (...) -> Dict[str, Any]
    base = {
        "rating": 7.5,
        "votes": 1000,
        "plot": "Great movie",
        "tagline": "Wow",
        "directors": ["Nolan"],
        "writers": ["Nolan"],
        "genres": ["Action"],
        "studios": ["WB"],
        "countries": ["US"],
        "trailer": "plugin://plugin.video.youtube/?action=play_video&videoid=abc",
        "runtime": 120,
        "premiered": "2025-06-15",
        "original_title": "Test",
        "certifications": {},
    }
    base.update(overrides)
    return base


def _tmdb_tvshow_response(**overrides):
    # type: (...) -> Dict[str, Any]
    base = {
        "rating": 8.0,
        "votes": 5000,
        "plot": "Great show",
        "genres": ["Drama"],
        "certifications": {},
    }
    base.update(overrides)
    return base


def _tmdb_episode_data(**overrides):
    # type: (...) -> Dict[str, Any]
    base = {
        "tmdb_id": "99999",
        "episode_number": 1,
        "season_number": 1,
        "title": "Pilot",
        "rating": 8.5,
        "votes": 200,
        "plot": "The beginning",
        "directors": ["Director A"],
        "writers": ["Writer B"],
        "firstaired": "2025-01-15",
        "runtime": 45,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Unit tests: _field_due
# ---------------------------------------------------------------------------


def test_field_due_no_row_is_due():
    from resources.lib.engine.scan import _field_due
    assert _field_due(None, "2026-06-08") is True


def test_field_due_empty_future_retry_not_due():
    from resources.lib.engine.scan import _field_due
    assert _field_due({"status": "empty", "next_retry": "2026-12-01"}, "2026-06-08") is False


def test_field_due_empty_past_retry_is_due():
    from resources.lib.engine.scan import _field_due
    assert _field_due({"status": "empty", "next_retry": "2026-01-01"}, "2026-06-08") is True


def test_field_due_exhausted_permanent_not_due():
    from resources.lib.engine.scan import _field_due
    assert _field_due({"status": "exhausted", "next_retry": None}, "2026-06-08") is False


def test_field_due_exhausted_autoretry_due():
    from resources.lib.engine.scan import _field_due
    assert _field_due({"status": "exhausted", "next_retry": "2026-01-01"}, "2026-06-08") is True


def test_field_due_filled_not_due():
    from resources.lib.engine.scan import _field_due
    assert _field_due({"status": "filled", "next_retry": None}, "2026-06-08") is False


# ---------------------------------------------------------------------------
# Unit tests: _item_title
# ---------------------------------------------------------------------------


class TestItemTitle:
    def test_movie_uses_title(self):
        item = {"title": "My Movie"}
        assert _item_title(item, MOVIE) == "My Movie"

    def test_tvshow_uses_title(self):
        item = {"title": "My Show"}
        assert _item_title(item, TVSHOW) == "My Show"

    def test_episode_formats_season_episode(self):
        item = {"showtitle": "Breaking Bad", "season": 3, "episode": 7}
        assert _item_title(item, EPISODE) == "Breaking Bad S03E07"

    def test_episode_pads_single_digits(self):
        item = {"showtitle": "Lost", "season": 1, "episode": 1}
        assert _item_title(item, EPISODE) == "Lost S01E01"


# ---------------------------------------------------------------------------
# Unit tests: _extract_fields
# ---------------------------------------------------------------------------


class TestExtractFields:
    def test_extracts_requested_fields(self):
        tmdb_data = _tmdb_movie_response()
        fields = _extract_fields(tmdb_data, ["rating", "plot"], MOVIE, {})
        # rating is no longer emitted by _extract_fields; it goes through the
        # named-slot ratings path (build_ratings) instead.
        assert "rating" not in fields
        assert fields["plot"] == "Great movie"
        assert "tagline" not in fields

    def test_skips_empty_string(self):
        tmdb_data = _tmdb_movie_response(tagline="")
        fields = _extract_fields(tmdb_data, ["tagline"], MOVIE, {})
        assert "tagline" not in fields

    def test_skips_empty_list(self):
        tmdb_data = _tmdb_movie_response(directors=[])
        fields = _extract_fields(tmdb_data, ["director"], MOVIE, {})
        assert "director" not in fields

    def test_skips_none(self):
        tmdb_data = _tmdb_movie_response(trailer=None)
        fields = _extract_fields(tmdb_data, ["trailer"], MOVIE, {})
        assert "trailer" not in fields

    def test_skips_zero_runtime(self):
        tmdb_data = _tmdb_movie_response(runtime=0)
        fields = _extract_fields(tmdb_data, ["runtime"], MOVIE, {})
        assert "runtime" not in fields

    def test_does_not_emit_votes(self):
        # votes is no longer emitted by _extract_fields; it lives inside the
        # named-slot ratings map (build_ratings) instead.
        tmdb_data = _tmdb_movie_response(votes=1000)
        fields = _extract_fields(tmdb_data, ["votes"], MOVIE, {})
        assert "votes" not in fields

    def test_episode_title_replaces_placeholder(self):
        tmdb_data = _tmdb_episode_data(title="Real Title")
        item = {"title": "Episode 1"}
        fields = _extract_fields(tmdb_data, ["title"], EPISODE, item)
        assert fields["title"] == "Real Title"

    def test_episode_title_skips_non_placeholder(self):
        tmdb_data = _tmdb_episode_data(title="Different Title")
        item = {"title": "Call Tracking"}
        fields = _extract_fields(tmdb_data, ["title"], EPISODE, item)
        assert "title" not in fields

    def test_episode_title_skips_same_as_current(self):
        tmdb_data = _tmdb_episode_data(title="Episode 1")
        item = {"title": "Episode 1"}
        fields = _extract_fields(tmdb_data, ["title"], EPISODE, item)
        assert "title" not in fields

    def test_does_not_include_mpaa(self):
        # mpaa is not in _TMDB_TO_KODI, so even if requested it is not extracted
        tmdb_data = _tmdb_movie_response()
        fields = _extract_fields(tmdb_data, ["mpaa"], MOVIE, {})
        assert "mpaa" not in fields


def test_extract_fields_converts_runtime_minutes_to_seconds():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields({"runtime": 114}, ["runtime"], MOVIE, {"runtime": 0})
    assert out["runtime"] == 114 * 60


def test_extract_fields_runtime_same_value_skipped():
    from resources.lib.engine.scan import _extract_fields
    # Kodi already has 114 min stored as 6840 s; provider says 114 min
    out = _extract_fields({"runtime": 114}, ["runtime"], MOVIE, {"runtime": 6840})
    assert "runtime" not in out


def test_extract_fields_does_not_overwrite_nonempty_plot():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"plot": "English plot"}, ["plot"], MOVIE, {"plot": "Bestaande NL plot"}
    )
    assert "plot" not in out  # non-empty current value is preserved (A2)


def test_extract_fields_fills_empty_plot():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields({"plot": "New plot"}, ["plot"], MOVIE, {"plot": ""})
    assert out["plot"] == "New plot"


def test_extract_fields_does_not_overwrite_nonempty_list_field():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"directors": ["TMDB Director"]}, ["director"], MOVIE,
        {"director": ["Curated Director"]},
    )
    assert "director" not in out  # curated list preserved (A2)


def test_extract_fields_skips_rating_and_votes():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"rating": 7.5, "votes": 100}, ["rating", "votes"], MOVIE,
        {"rating": 0, "votes": "0"},
    )
    assert "rating" not in out and "votes" not in out  # handled by ratings path


def test_extract_fields_episode_title_placeholder_still_overwritten():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"title": "Real Title"}, ["title"], EPISODE, {"title": "Episode 1"}
    )
    assert out["title"] == "Real Title"


def test_extract_fields_skips_bare_year_firstaired():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields({"firstaired": "2019"}, ["firstaired"], EPISODE, {"firstaired": ""})
    assert "firstaired" not in out


def test_extract_fields_writes_full_firstaired():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields({"firstaired": "2019-06-04"}, ["firstaired"], EPISODE, {"firstaired": ""})
    assert out["firstaired"] == "2019-06-04"


@patch("resources.lib.engine.scan.get_all_items")
def test_run_scan_propagates_kodi_query_error(mock_get_all, tmp_path, kodi_stubs):
    """A failed Kodi query must abort the pass, not be treated as empty."""
    from resources.lib.utils import KodiQueryError

    mock_get_all.side_effect = KodiQueryError("rpc failed")

    tracker = _make_tracker(tmp_path)
    try:
        with pytest.raises(KodiQueryError):
            run_scan(
                MOVIE, tracker, _make_source_config(),
                frozenset({"rating"}),
                "fake_key", _make_settings(),
            )
    finally:
        tracker.close()


# ---------------------------------------------------------------------------
# Integration tests: run_scan
# ---------------------------------------------------------------------------


class TestRunScanMovies:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_processes_movie_with_gaps(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["processed"] >= 1
        assert stats["updated"] >= 1
        assert mock_fetch.called
        assert mock_update.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_skips_item_without_ids(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        item = _make_movie_item()
        item["uniqueid"] = {}
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["skipped"] >= 1
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_skips_disabled_source(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        source_config = {
            "sources": [{"path": "smb://server/movies/", "enabled": False, "language": "en-US"}],
            "default_language": "en-US",
        }

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, source_config,
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["skipped"] >= 1
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_skips_item_with_no_gaps_and_checked_fields(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """Item with filled data AND field_status entries should be skipped."""
        item = _make_movie_item(rating=7.5, plot="A great plot")
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        # Pre-populate field_status so first-encounter check passes
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "checked")
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "plot", "checked")

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_abort_requested_stops_scan(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [
            _make_movie_item(),
            _make_movie_item(title="Movie 2", tmdb_id="99999"),
        ]
        monitor = MagicMock()
        monitor.abortRequested.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            monitor=monitor,
        )
        tracker.close()

        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_stats_count_fields(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["fields"]["ratings"] >= 1
        assert stats["fields"]["plot"] >= 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_tmdb_returns_none_records_field_attempt(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """No data: the per-field attempt is recorded on the field, not the item."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item is not None
        # Item-level attempt_count is now vestigial (per-field model)
        assert item["attempt_count"] == 0
        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["attempt_count"] == 1
        assert field["next_retry"] is not None
        assert field["status"] == "empty"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_update_failure_counts_error(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = False

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["errors"] >= 1
        assert stats["updated"] == 0

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_complete_item_sets_tracker_status(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """When all gaps are filled, item status is set to complete."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item is not None
        assert item["status"] == "complete"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_partial_fill_records_field_attempt(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """When TMDB fills some but not all gaps, only the unfilled field retries.

        The filled field is 'filled'; the unfilled field records a per-field
        attempt; the item rolls up to 'partial'.
        """
        mock_get_all.return_value = [_make_movie_item()]
        # TMDB returns rating but not plot
        mock_fetch.return_value = _tmdb_movie_response(plot="")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item is not None
        assert item["status"] == "partial"
        rating = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert rating is not None
        assert rating["status"] == "filled"
        plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert plot is not None
        assert plot["status"] == "empty"
        assert plot["attempt_count"] == 1
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_field_status_set_for_filled(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "filled"
        # Rating now carries the chosen named slot as its source (themoviedb),
        # and the vote_count comes from that slot (staleness provenance).
        assert field["source"] == "themoviedb"
        assert field["vote_count"] == 1000
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_no_gap_item_marked_complete_without_fetch(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Items with no gaps and all fields checked are marked complete."""
        item = _make_movie_item(rating=8.0, plot="Filled")
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        # Pre-populate field_status so first-encounter doesn't trigger
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "checked")
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "plot", "checked")

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

        db_item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert db_item is not None
        assert db_item["status"] == "complete"
        assert not mock_fetch.called
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.resolve_id")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_resolves_tmdb_id_when_missing(
        self, mock_fetch, mock_update, mock_resolve, mock_get_all,
        tmp_path, kodi_stubs
    ):
        item = _make_movie_item(tmdb_id=None)
        item["uniqueid"] = {"imdb": "tt1234567"}
        mock_get_all.return_value = [item]
        mock_resolve.return_value = "55555"
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        mock_resolve.assert_called_once()
        mock_fetch.assert_called_once_with("55555", "fake_key", "en-US")

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_tmdb_returns_none_falls_back_to_imdb(
        self, mock_tmdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """When TMDB returns None but IMDb has data, uses IMDb data."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = None
        mock_update.return_value = True

        with patch(
            "resources.lib.engine.scan.imdb_fetch_movie",
            return_value={"rating": 8.0, "votes": 5000, "plot": "IMDb plot"},
        ):
            tracker = _make_tracker(tmp_path)
            stats = run_scan(
                MOVIE, tracker, _make_source_config(),
                frozenset({"rating", "plot"}),
                "fake_key", _make_settings(),
            )

            assert stats["updated"] >= 1
            field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
            assert field is not None
            assert field["source"] == "imdb"
            tracker.close()
        assert stats["updated"] >= 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_run_scan_writes_ratings_map_not_flat(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Rating is written via the named-slot ratings map, not flat fields."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response(rating=7.5, votes=1000)
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert mock_update.called
        _, kwargs = mock_update.call_args
        assert "ratings" in kwargs
        assert "themoviedb" in kwargs["ratings"]
        assert kwargs["ratings"]["themoviedb"]["rating"] == 7.5
        assert kwargs["ratings"]["themoviedb"]["votes"] == 1000
        assert kwargs["ratings"]["themoviedb"]["default"] is True
        # No flat rating/votes are emitted
        assert "rating" not in kwargs
        assert "votes" not in kwargs

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_run_scan_rating_unchanged_is_idempotent(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """An unchanged rating produces no ratings write on a second pass."""
        item = _make_movie_item(rating=7.28)
        item["ratings"] = {
            "themoviedb": {"rating": 7.28, "votes": 2331, "default": True},
        }
        mock_get_all.return_value = [item]
        mock_fetch.return_value = _tmdb_movie_response(rating=7.28, votes=2331)
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        # No update_item call should carry a ratings change
        for _, kwargs in mock_update.call_args_list:
            assert "ratings" not in kwargs
        # Rating is recorded as checked (confirmed unchanged), not failed
        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "checked"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_run_scan_write_failure_does_not_mark_complete(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A failed write must not mark the item complete (A9)."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = False

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

        assert stats["errors"] >= 1
        assert stats["updated"] == 0
        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item is not None
        assert item["status"] != "complete"
        # A failed write leaves the field unresolved -> a per-field attempt is
        # recorded so it is retried (A9).
        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "empty"
        assert field["attempt_count"] == 1
        tracker.close()


class TestRunScanTVShows:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_processes_tvshow(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_tvshow_item()]
        mock_fetch.return_value = _tmdb_tvshow_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(),
            frozenset({"rating", "plot", "genre"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["updated"] >= 1
        mock_fetch.assert_called_once()


class TestRunScanEpisodes:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_processes_episode(
        self, mock_fetch_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_episode_item()]
        mock_fetch_season.return_value = [_tmdb_episode_data()]
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        show_map = {"Test Show": "55555"}
        stats = run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating", "plot", "title"}),
            "fake_key", _make_settings(),
            show_id_map=show_map,
        )
        tracker.close()

        assert stats["updated"] >= 1
        mock_fetch_season.assert_called_once()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_caches_season_data(
        self, mock_fetch_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Two episodes from the same season should only call fetch_season once."""
        ep1 = _make_episode_item(episode=1, title="Episode 1", tmdb_id="11111")
        ep1["episodeid"] = 100
        ep2 = _make_episode_item(episode=2, title="Episode 2", tmdb_id="22222")
        ep2["episodeid"] = 101

        mock_get_all.return_value = [ep1, ep2]
        mock_fetch_season.return_value = [
            _tmdb_episode_data(tmdb_id="11111", episode_number=1),
            _tmdb_episode_data(tmdb_id="22222", episode_number=2, title="Second"),
        ]
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        show_map = {"Test Show": "55555"}
        run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating", "plot", "title"}),
            "fake_key", _make_settings(),
            show_id_map=show_map,
        )
        tracker.close()

        # fetch_season called only once for the same show+season
        assert mock_fetch_season.call_count == 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_not_found_by_tmdb_id(
        self, mock_fetch_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Episode with tmdb_id not matching any episode in season data."""
        ep = _make_episode_item(episode=1, tmdb_id="00000")
        mock_get_all.return_value = [ep]
        mock_fetch_season.return_value = [_tmdb_episode_data(tmdb_id="99999")]
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        show_map = {"Test Show": "55555"}
        stats = run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            show_id_map=show_map,
        )

        item = tracker.get_item("episode", "Test Show S01E01")
        assert item is not None
        # Per-field model: the rating field records the retry attempt.
        field = tracker.get_field_status("episode", "Test Show S01E01", "rating")
        assert field is not None
        assert field["attempt_count"] == 1
        assert stats["updated"] == 0
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    def test_no_id_episode_skipped_not_attempted(
        self, mock_get_all, tmp_path, kodi_stubs
    ):
        """Episode with no tmdb/imdb id (tvdb only): skipped cleanly.

        No attempt recorded, no error, not exhausted; re-checked for free
        on the next scan (D4/A11).
        """
        ep = _make_episode_item(tmdb_id=None)
        ep["uniqueid"] = {"tvdb": "12345"}
        mock_get_all.return_value = [ep]

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            show_id_map={"Test Show": "55555"},
        )

        assert stats["skipped"] >= 1
        assert stats["errors"] == 0

        # The item is upserted (it has a tvdb id) but no attempt was recorded
        # and it is not exhausted.
        item = tracker.get_item("episode", "Test Show S01E01")
        assert item is not None
        assert item["attempt_count"] == 0
        assert item["status"] != "exhausted"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_not_found_in_season_not_counted_as_error(
        self, mock_fetch_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Episode has a tmdb id absent from the fetched season: safe skip.

        Not a provider error (data reality), so errors stays 0. A retry
        attempt may still be recorded (A11).
        """
        ep = _make_episode_item(episode=1, tmdb_id="00000")
        mock_get_all.return_value = [ep]
        mock_fetch_season.return_value = [_tmdb_episode_data(tmdb_id="99999")]
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        show_map = {"Test Show": "55555"}
        stats = run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            show_id_map=show_map,
        )

        assert stats["errors"] == 0
        assert stats["updated"] == 0
        tracker.close()


class TestRunScanSourceLanguage:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_uses_source_language(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Source language is passed to TMDB fetch."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        source_config = {
            "sources": [{"path": "smb://server/movies/", "enabled": True, "language": "nl-NL"}],
            "default_language": "en-US",
        }

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, source_config,
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        mock_fetch.assert_called_with("12345", "fake_key", "nl-NL")


class TestRunScanApiKeyError:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_api_key_error_stops_scan(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import ApiKeyError

        mock_get_all.return_value = [
            _make_movie_item(title="Movie 1", tmdb_id="111", movie_id=1),
            _make_movie_item(title="Movie 2", tmdb_id="222", movie_id=2),
        ]
        mock_fetch.side_effect = ApiKeyError("tmdb")

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "bad_key", _make_settings(),
        )
        tracker.close()

        # An invalid API key is a fatal config error, not a per-item
        # processing error, so it does not bump stats["errors"]; it stops
        # the pass immediately.
        assert stats["errors"] == 0
        # Second movie should not be attempted
        assert mock_fetch.call_count == 1


class TestRunScanMultipleItems:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_processes_multiple_movies(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [
            _make_movie_item(title="Movie A", tmdb_id="111", movie_id=1),
            _make_movie_item(title="Movie B", tmdb_id="222", movie_id=2),
        ]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["processed"] == 2
        assert stats["updated"] == 2
        assert mock_fetch.call_count == 2


# ---------------------------------------------------------------------------
# Integration tests: staleness and retry gating
# ---------------------------------------------------------------------------


class TestRunScanStaleness:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_skips_permanently_exhausted_field(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A permanently-exhausted field (next_retry None) is not re-attempted."""
        mock_get_all.return_value = [_make_movie_item()]
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # The only enabled field is permanently exhausted.
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "exhausted")
        tracker.set_field_next_retry("movie", _MOVIE_IDENTITY, "rating", None)

        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["skipped"] >= 1
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_skips_field_before_retry_date(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A field whose next_retry is in the future is not re-attempted."""
        mock_get_all.return_value = [_make_movie_item()]
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # The rating field has a far-future retry date.
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "empty")
        tracker.set_field_next_retry("movie", _MOVIE_IDENTITY, "rating", "2099-01-01")

        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert stats["skipped"] >= 1
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_processes_field_past_retry_date(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A field whose next_retry has passed is re-attempted."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # The rating field is empty with a past retry date -> due.
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "empty")
        tracker.set_field_next_retry("movie", _MOVIE_IDENTITY, "rating", "2020-01-01")

        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert mock_fetch.called
        assert stats["updated"] >= 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_field_marks_exhausted_after_max_retries(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """A field reaching max_retries with no data becomes exhausted.

        Exhaustion is per-field. The field gets ONE auto-retry scheduled
        (next_retry ~EXHAUSTED_AUTORETRY_DAYS out, not None). When the only
        gap field is exhausted, the item rolls up to 'exhausted'.
        """
        from resources.lib.constants import EXHAUSTED_AUTORETRY_DAYS

        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None  # TMDB returns nothing

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Simulate 3 previous field attempts (max is 4); the 4th exhausts.
        for _ in range(3):
            tracker.record_field_attempt(
                "movie", _MOVIE_IDENTITY, "rating", next_retry="2020-01-01"
            )

        settings = _make_settings()
        settings["max_retries"] = 4

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", settings,
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "exhausted"
        assert field["attempt_count"] == 4
        # One auto-retry is scheduled (not permanent yet).
        assert field["next_retry"] is not None
        expected = (date.today() + timedelta(days=EXHAUSTED_AUTORETRY_DAYS)).isoformat()
        assert field["next_retry"] == expected

        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item["status"] == "exhausted"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_field_attempt_count_increments_correctly(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """The per-field attempt_count increments each time the field retries."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")

        # First scan: rating is a first-encounter gap, no data -> attempt 1.
        run_scan(MOVIE, tracker, _make_source_config(),
                 frozenset({"rating"}), "fake_key", _make_settings())

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field["attempt_count"] == 1
        assert field["next_retry"] is not None

        # Back-date the field's next_retry so the next scan re-attempts it.
        tracker.set_field_next_retry("movie", _MOVIE_IDENTITY, "rating", "2020-01-01")

        # Start new cycle so cycle_processed is reset.
        tracker.start_new_cycle()
        run_scan(MOVIE, tracker, _make_source_config(),
                 frozenset({"rating"}), "fake_key", _make_settings())

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        # 1 from first scan + 1 from second scan.
        assert field["attempt_count"] == 2
        tracker.close()


# ---------------------------------------------------------------------------
# Integration tests: per-field retry and exhaustion (D2)
# ---------------------------------------------------------------------------


class TestRunScanPerFieldRetry:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_one_dead_field_does_not_block_a_good_field(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A field with no data records its own attempt and stays empty while a
        different field that has data is filled. The item rolls up to partial."""
        mock_get_all.return_value = [_make_movie_item()]
        # TMDB provides plot but no tagline.
        mock_fetch.return_value = _tmdb_movie_response(tagline="")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot", "tagline"}),
            "fake_key", _make_settings(),
        )

        plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert plot is not None
        assert plot["status"] == "filled"
        tagline = tracker.get_field_status("movie", _MOVIE_IDENTITY, "tagline")
        assert tagline is not None
        assert tagline["status"] == "empty"
        assert tagline["attempt_count"] == 1
        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item["status"] == "partial"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_permanently_exhausted_field_is_not_a_gap_and_does_not_block_complete(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A permanently-exhausted field is not re-attempted (not a gap), so it
        does not count against rollup: when the only remaining gap fills, the
        item is complete and the dead field is left exhausted."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response(tagline="")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # tagline already permanently exhausted; plot is fresh.
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "tagline", "exhausted")
        tracker.set_field_next_retry("movie", _MOVIE_IDENTITY, "tagline", None)

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot", "tagline"}),
            "fake_key", _make_settings(),
        )

        plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert plot is not None
        assert plot["status"] == "filled"
        # tagline is left untouched (not a gap; permanently exhausted).
        tagline = tracker.get_field_status("movie", _MOVIE_IDENTITY, "tagline")
        assert tagline is not None
        assert tagline["status"] == "exhausted"
        assert tagline["next_retry"] is None
        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item["status"] == "complete"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_all_gap_fields_exhausted_rolls_up_to_exhausted(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """When every gap field exhausts in the same pass, item -> exhausted."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Both fields are at attempt 3 (next attempt exhausts at max 4).
        for fld in ("plot", "tagline"):
            for _ in range(3):
                tracker.record_field_attempt(
                    "movie", _MOVIE_IDENTITY, fld, next_retry="2020-01-01"
                )

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot", "tagline"}),
            "fake_key", _make_settings(),
        )

        for fld in ("plot", "tagline"):
            field = tracker.get_field_status("movie", _MOVIE_IDENTITY, fld)
            assert field["status"] == "exhausted"
        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item["status"] == "exhausted"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_field_exhausts_while_sibling_still_retries(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """One field reaching max_retries exhausts; a sibling with fewer attempts
        keeps retrying (records a normal attempt, stays empty)."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # tagline is one attempt from exhaustion; plot is fresh-ish.
        for _ in range(3):
            tracker.record_field_attempt(
                "movie", _MOVIE_IDENTITY, "tagline", next_retry="2020-01-01"
            )
        tracker.record_field_attempt(
            "movie", _MOVIE_IDENTITY, "plot", next_retry="2020-01-01"
        )

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot", "tagline"}),
            "fake_key", _make_settings(),
        )

        tagline = tracker.get_field_status("movie", _MOVIE_IDENTITY, "tagline")
        assert tagline["status"] == "exhausted"
        assert tagline["attempt_count"] == 4
        plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert plot["status"] == "empty"
        assert plot["attempt_count"] == 2
        assert plot["next_retry"] is not None
        # Mixed: one exhausted, one still retryable -> item is partial.
        item = tracker.get_item("movie", _MOVIE_IDENTITY)
        assert item["status"] == "partial"
        tracker.close()


# ---------------------------------------------------------------------------
# Integration tests: one-time auto-retry of exhausted fields (D3)
# ---------------------------------------------------------------------------


class TestRunScanExhaustedAutoRetry:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_exhausted_field_schedules_autoretry(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """A field reaching max_retries with no data exhausts but keeps a
        next_retry well in the future (the one-time auto-retry), not None."""
        from datetime import timedelta as _td

        from resources.lib.constants import EXHAUSTED_AUTORETRY_DAYS

        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None  # provider finds nothing

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Seed to one attempt below max (4): the next attempt exhausts.
        for _ in range(3):
            tracker.record_field_attempt(
                "movie", _MOVIE_IDENTITY, "rating", next_retry="2020-01-01"
            )

        settings = _make_settings()
        settings["max_retries"] = 4

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", settings,
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "exhausted"
        # The auto-retry is scheduled, not permanent.
        assert field["next_retry"] is not None
        # It is well in the future (~EXHAUSTED_AUTORETRY_DAYS out).
        floor = (date.today() + _td(days=150)).isoformat()
        assert field["next_retry"] > floor
        expected = (
            date.today() + timedelta(days=EXHAUSTED_AUTORETRY_DAYS)
        ).isoformat()
        assert field["next_retry"] == expected
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_autoretry_still_empty_goes_permanent(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """An exhausted field whose auto-retry has come due and still finds no
        data goes permanently exhausted (next_retry None) and is not attempted
        again on a later scan."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None  # still nothing on the auto-retry

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Already exhausted with the auto-retry now in the past (due).
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "exhausted")
        tracker.set_field_next_retry(
            "movie", _MOVIE_IDENTITY, "rating", "2020-01-01"
        )

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        # The auto-retry ran (provider was called) and found nothing -> permanent.
        assert mock_fetch.called
        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "exhausted"
        assert field["next_retry"] is None

        # A later scan must not attempt the now-permanent field again.
        mock_fetch.reset_mock()
        tracker.start_new_cycle()
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        assert not mock_fetch.called
        field2 = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field2["status"] == "exhausted"
        assert field2["next_retry"] is None
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_autoretry_with_data_fills(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """An exhausted field whose auto-retry comes due and now finds data is
        filled (and the value is written)."""
        mock_get_all.return_value = [_make_movie_item()]
        # Provider now returns a rating for the field that was exhausted.
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Exhausted with the auto-retry now due.
        tracker.set_field_status("movie", _MOVIE_IDENTITY, "rating", "exhausted")
        tracker.set_field_next_retry(
            "movie", _MOVIE_IDENTITY, "rating", "2020-01-01"
        )

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        assert mock_fetch.called
        # The value was written to Kodi.
        assert mock_update.called
        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["status"] == "filled"
        tracker.close()


# ---------------------------------------------------------------------------
# Tests: pre-built items parameter
# ---------------------------------------------------------------------------


class TestRunScanWithItems:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_items_param_skips_kodi_query(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """When items= is provided, get_all_items should NOT be called."""
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
            items=[_make_movie_item()],
        )
        tracker.close()

        assert not mock_get_all.called
        assert stats["processed"] >= 1
        assert stats["updated"] >= 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_empty_items_list_processes_nothing(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            items=[],
        )
        tracker.close()

        assert not mock_get_all.called
        assert not mock_fetch.called
        assert stats["processed"] == 0


class TestRunScanCircuitBreaker:
    """Provider-failure taxonomy + cycle-level breaker wiring."""

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_transient_error_does_not_count_as_error(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker
        from resources.lib.utils import TransientProviderError

        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.side_effect = TransientProviderError("boom")

        tracker = _make_tracker(tmp_path)
        breaker = CircuitBreaker(threshold=5)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            breaker=breaker,
        )
        tracker.close()

        # A transient failure leaves both providers None -> no-data path:
        # the attempt is recorded but it is not an "error".
        assert stats["errors"] == 0
        assert stats["processed"] == 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_transient_error_trips_breaker_after_threshold(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker
        from resources.lib.utils import TransientProviderError

        # Three distinct movies all fail transiently.
        mock_get_all.return_value = [
            _make_movie_item(title="M1", tmdb_id="1", movie_id=1),
            _make_movie_item(title="M2", tmdb_id="2", movie_id=2),
            _make_movie_item(title="M3", tmdb_id="3", movie_id=3),
        ]
        mock_fetch.side_effect = TransientProviderError("boom")

        tracker = _make_tracker(tmp_path)
        breaker = CircuitBreaker(threshold=3)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            breaker=breaker,
        )
        tracker.close()

        assert breaker.is_disabled("tmdb") is True
        # Breaker tripped on the 3rd failure, so all 3 fetches were attempted.
        assert mock_fetch.call_count == 3

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_none_return_does_not_trip_breaker(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker

        mock_get_all.return_value = [
            _make_movie_item(title="M1", tmdb_id="1", movie_id=1),
            _make_movie_item(title="M2", tmdb_id="2", movie_id=2),
            _make_movie_item(title="M3", tmdb_id="3", movie_id=3),
        ]
        mock_fetch.return_value = None  # no-data, not a failure

        tracker = _make_tracker(tmp_path)
        breaker = CircuitBreaker(threshold=1)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            breaker=breaker,
        )
        tracker.close()

        assert breaker.is_disabled("tmdb") is False
        assert stats["errors"] == 0

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_both_disabled_stops_pass(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker

        mock_get_all.return_value = [
            _make_movie_item(title="M1", tmdb_id="1", movie_id=1),
            _make_movie_item(title="M2", tmdb_id="2", movie_id=2),
        ]
        mock_fetch.return_value = _tmdb_movie_response()

        tracker = _make_tracker(tmp_path)
        breaker = CircuitBreaker(threshold=1)
        # Pre-disable both providers: the pass must stop before any fetch.
        breaker.record_failure("tmdb")
        breaker.record_failure("imdb")

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            breaker=breaker,
        )
        tracker.close()

        assert mock_fetch.call_count == 0

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_breaker_persists_across_two_run_scan_calls(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker
        from resources.lib.utils import TransientProviderError

        mock_fetch.side_effect = TransientProviderError("boom")

        tracker = _make_tracker(tmp_path)
        breaker = CircuitBreaker(threshold=2)

        # First pass: one failing movie -> 1 failure, not yet disabled.
        mock_get_all.return_value = [_make_movie_item(title="M1", tmdb_id="1", movie_id=1)]
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            breaker=breaker,
        )
        assert breaker.is_disabled("tmdb") is False

        # Second pass sharing the SAME breaker: second failure trips it.
        mock_get_all.return_value = [_make_movie_item(title="M2", tmdb_id="2", movie_id=2)]
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            breaker=breaker,
        )
        tracker.close()

        assert breaker.is_disabled("tmdb") is True


def test_same_title_movies_get_separate_tracker_rows(tmp_path):
    # Two movies named "Dune", different years and tmdb ids
    from resources.lib.data.tracker import TrackerDB
    db = TrackerDB(str(tmp_path / "backfill.db"))
    a = {"title": "Dune", "year": 1984, "uniqueid": {"tmdb": "841"}}
    b = {"title": "Dune", "year": 2021, "uniqueid": {"tmdb": "438631"}}
    db.upsert_item("movie", compute_identity(a, MOVIE), title="Dune", kodi_id=1)
    db.upsert_item("movie", compute_identity(b, MOVIE), title="Dune", kodi_id=2)
    assert db.get_item("movie", "tmdb:841")["kodi_id"] == 1
    assert db.get_item("movie", "tmdb:438631")["kodi_id"] == 2
    db.close()

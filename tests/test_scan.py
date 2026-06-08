"""Tests for scan orchestrator."""
from __future__ import annotations

from typing import Any, Dict  # noqa: F401 (used in type comments)
from unittest.mock import MagicMock, patch

import pytest

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
        assert fields["rating"] == 7.5
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

    def test_converts_votes_to_string(self):
        tmdb_data = _tmdb_movie_response(votes=1000)
        fields = _extract_fields(tmdb_data, ["votes"], MOVIE, {})
        assert fields["votes"] == "1000"

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
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
        tracker.set_field_status("movie", "Test Movie", "rating", "checked")
        tracker.set_field_status("movie", "Test Movie", "plot", "checked")

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

        assert stats["fields"]["rating"] >= 1
        assert stats["fields"]["plot"] >= 1

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_tmdb_returns_none_records_attempt(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        item = tracker.get_item("movie", "Test Movie")
        assert item is not None
        assert item["attempt_count"] == 1
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

        item = tracker.get_item("movie", "Test Movie")
        assert item is not None
        assert item["status"] == "complete"
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_partial_fill_records_attempt(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """When TMDB fills some but not all gaps, attempt is recorded."""
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

        item = tracker.get_item("movie", "Test Movie")
        assert item is not None
        assert item["attempt_count"] == 1
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

        field = tracker.get_field_status("movie", "Test Movie", "rating")
        assert field is not None
        assert field["status"] == "filled"
        assert field["source"] == "tmdb"
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
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
        tracker.set_field_status("movie", "Test Movie", "rating", "checked")
        tracker.set_field_status("movie", "Test Movie", "plot", "checked")

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

        db_item = tracker.get_item("movie", "Test Movie")
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
            field = tracker.get_field_status("movie", "Test Movie", "rating")
            assert field is not None
            assert field["source"] == "imdb"
            tracker.close()
        assert stats["updated"] >= 1


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
        assert item["attempt_count"] == 1
        assert stats["updated"] == 0
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    def test_episode_without_imdb_or_tmdb_id_records_attempt(
        self, mock_get_all, tmp_path, kodi_stubs
    ):
        """Episode with only TVDB ID records attempt and eventually exhausts."""
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

        item = tracker.get_item("episode", "Test Show S01E01")
        assert item is not None
        assert item["attempt_count"] == 1
        assert stats["processed"] >= 1
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

        assert stats["errors"] >= 1
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
    def test_skips_exhausted_item(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        tracker = _make_tracker(tmp_path)
        # Pre-seed tracker as exhausted
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
        tracker.update_item_status("movie", "Test Movie", "exhausted")

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
    def test_skips_item_before_retry_date(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
        # Set next_retry to far future
        tracker.record_attempt("movie", "Test Movie", next_retry="2099-01-01")

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
    def test_processes_item_past_retry_date(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
        # Set next_retry to past
        tracker.record_attempt("movie", "Test Movie", next_retry="2020-01-01")

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
    def test_marks_exhausted_after_max_retries(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None  # TMDB returns nothing

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")
        # Simulate 3 previous attempts (max is 4)
        for _ in range(3):
            tracker.record_attempt("movie", "Test Movie", next_retry="2020-01-01")

        settings = _make_settings()
        settings["max_retries"] = 4

        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", settings,
        )

        item = tracker.get_item("movie", "Test Movie")
        assert item["status"] == "exhausted"
        assert item["attempt_count"] == 4
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_attempt_count_increments_correctly(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", "Test Movie", kodi_id=1, tmdb_id="12345")

        # First scan
        run_scan(MOVIE, tracker, _make_source_config(),
                 frozenset({"rating"}), "fake_key", _make_settings())

        item = tracker.get_item("movie", "Test Movie")
        assert item["attempt_count"] == 1
        assert item["next_retry"] is not None

        # Set next_retry to past so second scan processes the item
        tracker.record_attempt("movie", "Test Movie", next_retry="2020-01-01")
        # attempt_count is now 2 from the manual record_attempt

        # Start new cycle so cycle_processed is reset
        tracker.start_new_cycle()
        run_scan(MOVIE, tracker, _make_source_config(),
                 frozenset({"rating"}), "fake_key", _make_settings())

        item = tracker.get_item("movie", "Test Movie")
        # Should be 3: 1 from first scan + 1 manual + 1 from second scan
        assert item["attempt_count"] == 3
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

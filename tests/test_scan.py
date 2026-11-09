"""Tests for scan orchestrator."""
from __future__ import annotations

from datetime import date
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
        "tvshowid": 10,
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
# Unit tests: _content_age_days / _structurally_absent
# ---------------------------------------------------------------------------


def test_content_age_days_old_content():
    from resources.lib.engine.scan import _content_age_days
    item = {"premiered": "2000-01-01"}
    assert _content_age_days(item, MOVIE, "2020-01-01") == (
        date.fromisoformat("2020-01-01") - date.fromisoformat("2000-01-01")
    ).days


def test_content_age_days_missing_date_is_none():
    from resources.lib.engine.scan import _content_age_days
    assert _content_age_days({"premiered": ""}, MOVIE, "2020-01-01") is None
    assert _content_age_days({}, MOVIE, "2020-01-01") is None


def test_content_age_days_bare_year_is_none():
    from resources.lib.engine.scan import _content_age_days
    assert _content_age_days({"premiered": "2019"}, MOVIE, "2020-01-01") is None


def test_structurally_absent_imdb_only_tmdb_field():
    from resources.lib.engine.scan import _structurally_absent
    from resources.lib.engine.resolve import IMDB_ONLY, PREFER_TMDB
    assert _structurally_absent("tagline", IMDB_ONLY) is True
    assert _structurally_absent("studio", IMDB_ONLY) is True
    assert _structurally_absent("plot", IMDB_ONLY) is False
    assert _structurally_absent("tagline", PREFER_TMDB) is False


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

    def test_episode_title_overwrites_non_placeholder(self):
        # Maturity model: episode titles just take the latest provider value;
        # there is no placeholder gating any more.
        tmdb_data = _tmdb_episode_data(title="Different Title")
        item = {"title": "Call Tracking"}
        fields = _extract_fields(tmdb_data, ["title"], EPISODE, item)
        assert fields["title"] == "Different Title"

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


def test_extract_fields_overwrites_nonempty_plot_on_diff():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"plot": "English plot"}, ["plot"], MOVIE, {"plot": "Bestaande NL plot"}
    )
    # Maturity model: a differing provider value overwrites a non-empty value.
    assert out["plot"] == "English plot"


def test_extract_fields_fills_empty_plot():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields({"plot": "New plot"}, ["plot"], MOVIE, {"plot": ""})
    assert out["plot"] == "New plot"


def test_extract_fields_overwrites_nonempty_list_field_on_diff():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"directors": ["TMDB Director"]}, ["director"], MOVIE,
        {"director": ["Curated Director"]},
    )
    assert out["director"] == ["TMDB Director"]


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


def test_extract_fields_still_skips_same_value():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"plot": "Same plot"}, ["plot"], MOVIE,
        {"plot": "Same plot"},
    )
    assert "plot" not in out  # identical value is never rewritten


def test_extract_fields_overwrites_nonplaceholder_episode_title():
    from resources.lib.engine.scan import _extract_fields
    out = _extract_fields(
        {"title": "Real Title"}, ["title"], EPISODE,
        {"title": "Curated Episode Title"},
    )
    assert out["title"] == "Real Title"


@patch("resources.lib.engine.scan.update_item")
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
def test_refresh_overwrites_nonempty_plot_when_volatile(mock_fetch, mock_update, tmp_path, kodi_stubs):
    from resources.lib.data.tracker import TrackerDB
    from datetime import date
    item = _make_movie_item(plot="Old plot")
    item["premiered"] = date.today().isoformat()  # brand new -> volatile
    mock_fetch.return_value = _tmdb_movie_response(plot="Fresh plot")
    mock_update.return_value = True
    tracker = TrackerDB(str(tmp_path / "t.db"))
    run_scan(MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
             "key", _make_settings(), items=[item])
    tracker.close()
    _, kwargs = mock_update.call_args
    assert kwargs.get("plot") == "Fresh plot"  # overwrote a non-empty value


@patch("resources.lib.engine.scan.update_item")
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
def test_old_content_finalizes_after_one_fetch(mock_fetch, mock_update, tmp_path, kodi_stubs):
    from resources.lib.data.tracker import TrackerDB
    item = _make_movie_item(plot="")
    item["premiered"] = "2000-01-01"  # old -> stable
    mock_fetch.return_value = _tmdb_movie_response(plot="A plot")
    mock_update.return_value = True
    tracker = TrackerDB(str(tmp_path / "t.db"))
    run_scan(MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
             "key", _make_settings(), items=[item])
    # second scan: field is final (old content, sampled once) -> no second fetch
    mock_fetch.reset_mock()
    run_scan(MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
             "key", _make_settings(), items=[item])
    tracker.close()
    assert not mock_fetch.called


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
# Rate-limit pacing (characterization: sleep BEFORE a call only when armed)
# ---------------------------------------------------------------------------


@patch("resources.lib.engine.scan.update_item", return_value=True)
@patch("resources.lib.engine.scan.imdb_fetch_movie", return_value=None)
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
@patch("resources.lib.engine.scan.get_all_items")
def test_run_scan_paces_between_provider_calls(
    mock_get_all, mock_tmdb, _mock_imdb, _mock_update, tmp_path, kodi_stubs
):
    """The default movie makes two provider calls (tmdb then imdb). The first
    is not preceded by a sleep (pacer not yet armed); the second sleeps once."""
    mock_get_all.return_value = [_make_movie_item()]
    mock_tmdb.return_value = _tmdb_movie_response()
    settings = _make_settings()
    settings["rate_limit"] = 0.25

    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.scan.time.sleep") as mock_sleep:
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", settings,
        )
    tracker.close()

    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_once_with(0.25)


@patch("resources.lib.engine.scan.update_item", return_value=True)
@patch("resources.lib.engine.scan.imdb_fetch_movie", return_value=None)
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
@patch("resources.lib.engine.scan.get_all_items")
def test_run_scan_no_sleep_when_rate_limit_zero(
    mock_get_all, mock_tmdb, _mock_imdb, _mock_update, tmp_path, kodi_stubs
):
    """rate_limit=0 (the test default) never sleeps."""
    mock_get_all.return_value = [_make_movie_item()]
    mock_tmdb.return_value = _tmdb_movie_response()

    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.scan.time.sleep") as mock_sleep:
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )
    tracker.close()

    mock_sleep.assert_not_called()


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
    def test_skips_item_when_all_fields_final(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """Old content already sampled once is final -> no fetch this scan."""
        item = _make_movie_item(rating=7.5, plot="A great plot")
        item["premiered"] = "2000-01-01"  # old -> stable
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        # Pre-record one sample for each field: fetch_count 1 + old content = final.
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "rating", next_retry=None)
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "plot", next_retry=None)
        tracker.commit()

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
    def test_tmdb_returns_none_still_records_sample(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A clean no-data answer is still a sample: fetch_count advances and a
        next_retry is scheduled (volatile content)."""
        item = _make_movie_item()
        item["premiered"] = date.today().isoformat()  # young -> volatile
        mock_get_all.return_value = [item]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["fetch_count"] == 1
        assert field["next_retry"] is not None  # still volatile -> scheduled
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
    def test_filled_fields_record_one_sample_each(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A scan that fills every field records a fetch_count for each."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

        rating = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert rating is not None and rating["fetch_count"] == 1
        assert plot is not None and plot["fetch_count"] == 1
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_each_field_sampled_independently(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Both fields are sampled in one fetch even when one has no data."""
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

        rating = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert rating is not None
        assert rating["fetch_count"] == 1
        plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert plot is not None
        assert plot["fetch_count"] == 1  # sampled even though no value was found
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_field_records_source_and_votes_for_rating(
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
        assert field["fetch_count"] == 1
        # Rating carries the chosen named slot as its source (themoviedb),
        # and the vote_count comes from that slot (staleness provenance).
        assert field["source"] == "themoviedb"
        assert field["vote_count"] == 1000
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_final_fields_skip_fetch(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Old content already sampled once is final -> no fetch."""
        item = _make_movie_item(rating=8.0, plot="Filled")
        item["premiered"] = "2000-01-01"
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "rating", next_retry=None)
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "plot", next_retry=None)
        tracker.commit()

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating", "plot"}),
            "fake_key", _make_settings(),
        )

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
        run_scan(
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
        # The rating was still sampled (a clean answer), confirming it unchanged.
        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None
        assert field["fetch_count"] == 1
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_run_scan_write_failure_counts_error(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A failed Kodi write counts an error and is not counted as updated."""
        item = _make_movie_item()
        item["premiered"] = date.today().isoformat()  # young -> stays volatile
        mock_get_all.return_value = [item]
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
        show_map = {10: "55555"}
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
        show_map = {10: "55555"}
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
        show_map = {10: "55555"}
        stats = run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
            show_id_map=show_map,
        )

        item = tracker.get_item("episode", "Test Show S01E01")
        assert item is not None
        # Per-field model: the rating field still records the sample (clean,
        # the episode simply was not in the fetched season data).
        field = tracker.get_field_status("episode", "Test Show S01E01", "rating")
        assert field is not None
        assert field["fetch_count"] == 1
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
            show_id_map={10: "55555"},
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
        show_map = {10: "55555"}
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
    def test_api_key_error_propagates(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import ApiKeyError

        mock_get_all.return_value = [
            _make_movie_item(title="Movie 1", tmdb_id="111", movie_id=1),
            _make_movie_item(title="Movie 2", tmdb_id="222", movie_id=2),
        ]
        mock_fetch.side_effect = ApiKeyError("tmdb")

        tracker = _make_tracker(tmp_path)
        # An invalid key is terminal: run_scan re-raises so the caller
        # (daemon/manual/actions) can surface it and hold the watermark,
        # instead of silently finishing the pass as a no-op.
        with pytest.raises(ApiKeyError):
            run_scan(
                MOVIE, tracker, _make_source_config(),
                frozenset({"rating"}),
                "bad_key", _make_settings(),
            )
        tracker.close()

        # The pass stops at the first item (second movie not attempted).
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
# Integration tests: maturity gating (due / not-due / final)
# ---------------------------------------------------------------------------


class TestRunScanMaturity:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_skips_final_old_sampled_field(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """Old content already sampled once is final -> not fetched again."""
        item = _make_movie_item()
        item["premiered"] = "2000-01-01"
        mock_get_all.return_value = [item]
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "rating", next_retry=None)
        tracker.commit()

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
    def test_skips_volatile_field_before_due_date(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A young field whose next_retry is in the future is not resampled."""
        item = _make_movie_item()
        item["premiered"] = date.today().isoformat()  # young -> volatile
        mock_get_all.return_value = [item]
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Already sampled once with a far-future due date.
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "rating", next_retry="2099-01-01"
        )
        tracker.commit()

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
    def test_resamples_volatile_field_past_due_date(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A young field whose next_retry has passed is resampled."""
        item = _make_movie_item()
        item["premiered"] = date.today().isoformat()  # young -> volatile
        mock_get_all.return_value = [item]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "rating", next_retry="2020-01-01"
        )
        tracker.commit()

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )
        tracker.close()

        assert mock_fetch.called

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_first_encounter_always_fetches(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A never-sampled field (fetch_count 0) is always due, any age."""
        item = _make_movie_item()
        item["premiered"] = "2000-01-01"  # old, but never sampled
        mock_get_all.return_value = [item]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"rating"}),
            "fake_key", _make_settings(),
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field is not None and field["fetch_count"] == 1
        assert mock_fetch.called
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_structurally_absent_field_never_fetched(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """IMDb-only run never samples a TMDB-only field (tagline)."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = _tmdb_movie_response()
        mock_update.return_value = True
        settings = _make_settings()
        settings["default_source"] = 3  # IMDB_ONLY

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"tagline"}),
            "fake_key", settings,
        )

        # tagline is structurally absent under IMDb-only -> final_empty, no fetch.
        tagline = tracker.get_field_status("movie", _MOVIE_IDENTITY, "tagline")
        assert tagline is None
        assert not mock_fetch.called
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_fetch_count_increments_across_volatile_scans(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """Each resample of a volatile field bumps fetch_count."""
        item = _make_movie_item()
        item["premiered"] = date.today().isoformat()  # young -> volatile
        mock_get_all.return_value = [item]
        mock_fetch.return_value = None

        tracker = _make_tracker(tmp_path)
        run_scan(MOVIE, tracker, _make_source_config(),
                 frozenset({"rating"}), "fake_key", _make_settings())

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        assert field["fetch_count"] == 1
        assert field["next_retry"] is not None

        # Back-date the field's next_retry so the next scan resamples it.
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "rating", next_retry="2020-01-01"
        )
        tracker.commit()

        tracker.start_new_cycle()
        run_scan(MOVIE, tracker, _make_source_config(),
                 frozenset({"rating"}), "fake_key", _make_settings())

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        # 1 (first scan) + 1 (manual record_field_fetch) + 1 (second scan).
        assert field["fetch_count"] == 3
        tracker.close()


class TestRunScanPerFieldRefresh:
    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_one_empty_field_does_not_block_a_good_field(
        self, mock_fetch, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """A field with no data is still sampled while a sibling with data is
        written; both record a fetch."""
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
        assert plot is not None and plot["fetch_count"] == 1
        tagline = tracker.get_field_status("movie", _MOVIE_IDENTITY, "tagline")
        assert tagline is not None and tagline["fetch_count"] == 1
        tracker.close()

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_transient_failure_does_not_advance_counter(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        """A transient provider failure is not a clean answer: fetch_count is
        not advanced, so the field is retried next scan."""
        from resources.lib.engine.breaker import CircuitBreaker
        from resources.lib.utils import TransientProviderError

        item = _make_movie_item()
        item["premiered"] = date.today().isoformat()
        mock_get_all.return_value = [item]
        mock_fetch.side_effect = TransientProviderError("boom")

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            breaker=CircuitBreaker(threshold=5),
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        # No clean answer -> no field_status row was recorded for this scan.
        assert field is None
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


# ---------------------------------------------------------------------------
# run_scan reset path (D3 "Update now")
# ---------------------------------------------------------------------------


class TestRunScanReset:
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_reset_overwrites_nonempty_plot(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """A reset run overwrites an existing non-empty plot."""
        item = _make_movie_item(plot="Existing curated plot")
        mock_fetch.return_value = _tmdb_movie_response(plot="Fresh provider plot")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item], reset=True,
        )
        tracker.close()

        assert stats["updated"] >= 1
        _, kwargs = mock_update.call_args
        assert kwargs.get("plot") == "Fresh provider plot"

    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_reset_refetches_final_field(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Reset ignores maturity: a field a normal scan would treat as final
        (old content, already sampled) is refetched and overwritten."""
        item = _make_movie_item(plot="Existing curated plot")
        item["premiered"] = "2000-01-01"  # old -> would be final after one sample
        mock_fetch.return_value = _tmdb_movie_response(plot="Recovered plot")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "plot", next_retry=None)
        tracker.commit()

        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item], reset=True,
        )

        assert mock_fetch.called
        assert stats["updated"] >= 1
        _, kwargs = mock_update.call_args
        assert kwargs.get("plot") == "Recovered plot"
        tracker.close()

    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_normal_scan_skips_final_field(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Control: without reset, a final field (old content, already sampled)
        is not fetched."""
        item = _make_movie_item(plot="Existing curated plot")
        item["premiered"] = "2000-01-01"
        mock_fetch.return_value = _tmdb_movie_response(plot="Recovered plot")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "plot", next_retry=None)
        tracker.commit()

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item],
        )
        tracker.close()

        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_reset_rebaselines_field_clock(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """A reset run re-baselines the field clock (fetch_count 1, next_retry
        set to a future schedule date) regardless of prior samples."""
        from datetime import date as _date
        item = _make_movie_item(plot="")
        item["premiered"] = "2000-01-01"
        mock_fetch.return_value = _tmdb_movie_response(plot="Fresh plot")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        # Several prior samples.
        for _ in range(3):
            tracker.record_field_fetch(
                "movie", _MOVIE_IDENTITY, "plot", next_retry="2099-01-01"
            )
        tracker.commit()

        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item], reset=True,
        )

        field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert field is not None
        assert field["fetch_count"] == 1  # re-baselined
        # next_retry must be a future date (not None) so young content is
        # re-sampled on the normal schedule after a manual update.
        assert field["next_retry"] is not None
        assert field["next_retry"] > _date.today().isoformat()
        tracker.close()

    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_reset_ignores_cycle_processed(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Reset bypasses the cycle-processed resume skip."""
        item = _make_movie_item(plot="Old plot")
        mock_fetch.return_value = _tmdb_movie_response(plot="New plot")
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.mark_cycle_processed("movie", _MOVIE_IDENTITY)

        stats = run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item], reset=True,
        )
        tracker.close()

        assert mock_fetch.called
        assert stats["updated"] >= 1

    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_reset_bypasses_disabled_source(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Reset skips the disabled-source gate: Update now must run even when
        the item's source is toggled off in the source config."""
        item = _make_movie_item(plot="")
        item["file"] = "smb://disabled-share/movies/test.mkv"
        mock_fetch.return_value = _tmdb_movie_response(plot="Fetched plot")
        mock_update.return_value = True

        source_config = {
            "sources": [
                {
                    "path": "smb://disabled-share/",
                    "enabled": False,
                    "language": "en-US",
                }
            ],
            "default_language": "en-US",
        }

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, source_config,
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item], reset=True,
        )
        tracker.close()

        assert mock_fetch.called, "Provider should be called even with disabled source"
        assert stats["updated"] >= 1

    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_normal_scan_skips_disabled_source(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Control: without reset, a disabled source causes the item to be
        skipped and the provider is not called."""
        item = _make_movie_item(plot="")
        item["file"] = "smb://disabled-share/movies/test.mkv"
        mock_fetch.return_value = _tmdb_movie_response(plot="Fetched plot")
        mock_update.return_value = True

        source_config = {
            "sources": [
                {
                    "path": "smb://disabled-share/",
                    "enabled": False,
                    "language": "en-US",
                }
            ],
            "default_language": "en-US",
        }

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, source_config,
            frozenset({"plot"}),
            "fake_key", _make_settings(),
            items=[item],
        )
        tracker.close()

        assert not mock_fetch.called
        assert stats["skipped"] >= 1


class TestCycleCompletionReporting:
    """run_scan must tell the caller whether it reached the end of the library.

    The daemon may only mark a cycle complete when it did; otherwise the resume
    watermark advances over items the scan never visited.
    """

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_completed_scan_reports_not_aborted(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_fetch.return_value = {"plot": "A plot"}
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "fake_key", _make_settings(),
        )
        tracker.close()
        assert stats["aborted"] is False

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_aborted_scan_reports_aborted(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [
            _make_movie_item(),
            _make_movie_item(title="Movie 2", tmdb_id="99999"),
        ]
        monitor = MagicMock()
        monitor.abortRequested.return_value = True
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "fake_key", _make_settings(), monitor=monitor,
        )
        tracker.close()
        assert stats["aborted"] is True


class TestFailedWriteLeavesClockAlone:
    """A field whose Kodi write failed was not filled, so its maturity clock
    must not advance: otherwise aged content goes FINAL and is abandoned."""

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.imdb_fetch_movie")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_failed_write_does_not_advance_fetch_count(
        self, mock_tmdb, mock_imdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = {"plot": "A plot"}
        mock_imdb.return_value = None
        mock_update.return_value = False  # Kodi rejected the write

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "fake_key", _make_settings(),
        )
        fs_map = tracker.get_field_status_map(
            "movie", compute_identity(_make_movie_item(), MOVIE)
        )
        tracker.close()

        assert stats["errors"] == 1
        assert fs_map.get("plot", {}).get("fetch_count", 0) == 0

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.imdb_fetch_movie")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_successful_write_does_advance_fetch_count(
        self, mock_tmdb, mock_imdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = {"plot": "A plot"}
        mock_imdb.return_value = None
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "fake_key", _make_settings(),
        )
        fs_map = tracker.get_field_status_map(
            "movie", compute_identity(_make_movie_item(), MOVIE)
        )
        tracker.close()

        assert fs_map["plot"]["fetch_count"] == 1


class TestEpisodeTransientDoesNotAdvanceClock:
    """A TMDB transient failure on the episode path is not an answer, so it
    must not advance the maturity clock (the _TRANSIENT sentinel contract)."""

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.imdb_fetch_episode")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_season_transient_leaves_fetch_count_at_zero(
        self, mock_fetch_season, mock_imdb, mock_update, mock_get_all,
        tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        mock_get_all.return_value = [_make_episode_item()]
        mock_fetch_season.side_effect = TransientProviderError("http 503")
        mock_imdb.return_value = None
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"rating", "plot", "title"}),
            "fake_key", _make_settings(),
            show_id_map={10: "55555"},
        )
        fs_map = tracker.get_field_status_map(
            "episode", compute_identity(_make_episode_item(), EPISODE)
        )
        tracker.close()

        assert fs_map == {}


class TestConsecutiveCycles:
    """End-to-end guard for the defect where the shipped default configuration
    never started a second cycle: cycle_processed stayed 1 for every item, so
    the whole library was skipped from cycle 2 on and last_seen froze."""

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.imdb_fetch_movie")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_second_cycle_processes_the_library_again(
        self, mock_tmdb, mock_imdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = {"plot": "A plot"}
        mock_imdb.return_value = None
        mock_update.return_value = True

        tracker = _make_tracker(tmp_path)
        fields = frozenset({"plot"})
        settings = _make_settings()

        # Cycle 1, run to completion.
        tracker.begin_cycle(resume_interrupted=True)
        first = run_scan(
            MOVIE, tracker, _make_source_config(), fields, "k", settings,
        )
        tracker.mark_cycle_complete()

        # Cycle 2, with resume_interrupted still at its shipped default.
        # Backdate last_seen so a refresh in cycle 2 is observable.
        identity = compute_identity(_make_movie_item(), MOVIE)
        tracker._conn.execute(
            "UPDATE items SET last_seen = ?", ("2000-01-01",),
        )
        tracker._conn.commit()

        tracker.begin_cycle(resume_interrupted=True)
        run_scan(
            MOVIE, tracker, _make_source_config(), fields, "k", settings,
        )
        last_seen_after_cycle_2 = tracker.get_item("movie", identity)["last_seen"]
        tracker.close()

        # Cycle 2 need not re-fetch (the field is not due yet), but it MUST
        # reach the item: with the cycle never reopening, the resume check
        # short-circuited above update_last_seen, last_seen froze at cycle 1,
        # and prune_orphans then deleted the rows of items still in the library.
        assert first["processed"] == 1
        assert last_seen_after_cycle_2 == date.today().isoformat()


def _tripped_breaker(provider):
    # type: (str) -> Any
    from resources.lib.engine.breaker import CircuitBreaker
    breaker = CircuitBreaker()
    while not breaker.is_disabled(provider):
        breaker.record_failure(provider)
    return breaker


class TestUnheardProviderDoesNotAdvanceClock:
    """A provider the item would have consulted but did not hear from, because
    its breaker was already open or an earlier step of the same provider failed
    transiently, is a missing answer, not an empty one. Recording it as a fetch
    would finalize old content on nothing (the maturity-clock invariant)."""

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.imdb_fetch_movie")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_open_imdb_breaker_leaves_movie_clock_at_zero(
        self, mock_tmdb, mock_imdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = _tmdb_movie_response()

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot", "rating"}),
            "fake_key", _make_settings(), breaker=_tripped_breaker("imdb"),
        )
        fs_map = tracker.get_field_status_map("movie", _MOVIE_IDENTITY)
        tracker.close()

        mock_imdb.assert_not_called()
        assert all(row["fetch_count"] == 0 for row in fs_map.values())

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.imdb_fetch_movie", return_value=None)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_open_tmdb_breaker_leaves_movie_clock_at_zero(
        self, mock_tmdb, mock_imdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot", "rating"}),
            "fake_key", _make_settings(), breaker=_tripped_breaker("tmdb"),
        )
        fs_map = tracker.get_field_status_map("movie", _MOVIE_IDENTITY)
        tracker.close()

        mock_tmdb.assert_not_called()
        assert all(row["fetch_count"] == 0 for row in fs_map.values())

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.imdb_fetch_movie", return_value=None)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.resolve_id")
    def test_transient_resolve_id_leaves_clock_at_zero(
        self, mock_resolve, mock_tmdb, mock_imdb, mock_update, mock_get_all,
        tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        item = _make_movie_item()
        item["uniqueid"] = {"imdb": "tt0000001"}
        mock_get_all.return_value = [item]
        mock_resolve.side_effect = TransientProviderError("http 503")

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot", "rating"}),
            "fake_key", _make_settings(),
        )
        fs_map = tracker.get_field_status_map("movie", compute_identity(item, MOVIE))
        tracker.close()

        mock_tmdb.assert_not_called()
        assert all(row["fetch_count"] == 0 for row in fs_map.values())

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.imdb_fetch_episode")
    def test_open_imdb_breaker_leaves_episode_clock_at_zero(
        self, mock_imdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        item = _make_episode_item()
        item["uniqueid"] = {"imdb": "tt0000002"}
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"plot", "rating"}),
            "fake_key", _make_settings(), breaker=_tripped_breaker("imdb"),
            show_id_map={10: "55555"},
        )
        fs_map = tracker.get_field_status_map("episode", compute_identity(item, EPISODE))
        tracker.close()

        mock_imdb.assert_not_called()
        assert all(row["fetch_count"] == 0 for row in fs_map.values())


class TestEpisodeShowLookup:
    """Episodes reach their show through the Kodi tvshowid, never the title."""

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_show_lookup_miss_is_transient_not_clean(
        self, mock_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        item = _make_episode_item()
        item["tvshowid"] = 99
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"plot", "title"}),
            "fake_key", _make_settings(), show_id_map={10: "55555"},
        )
        fs_map = tracker.get_field_status_map("episode", compute_identity(item, EPISODE))
        tracker.close()

        mock_season.assert_not_called()
        assert all(row["fetch_count"] == 0 for row in fs_map.values())

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_without_tvshowid_is_transient(
        self, mock_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        item = _make_episode_item()
        del item["tvshowid"]
        mock_get_all.return_value = [item]

        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"plot", "title"}),
            "fake_key", _make_settings(), show_id_map={10: "55555"},
        )
        fs_map = tracker.get_field_status_map("episode", compute_identity(item, EPISODE))
        tracker.close()

        mock_season.assert_not_called()
        assert all(row["fetch_count"] == 0 for row in fs_map.values())

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_uses_its_own_show_when_titles_collide(
        self, mock_season, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        item = _make_episode_item(showtitle="The Office")
        item["tvshowid"] = 2
        mock_get_all.return_value = [item]
        mock_season.return_value = [_tmdb_episode_data()]

        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"plot", "title"}),
            "fake_key", _make_settings(), show_id_map={1: "2316", 2: "2996"},
        )
        tracker.close()

        assert mock_season.call_args[0][0] == "2996"

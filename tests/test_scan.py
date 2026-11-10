"""Tests for scan orchestrator."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict  # noqa: F401 (used in type comments)
from unittest.mock import MagicMock, patch

import pytest

from resources.lib.constants import FALLBACK_RECHECK_DAYS, FALLBACK_RECHECKS
from resources.lib.data.identity import compute_identity
from resources.lib.data.media_types import EPISODE, MOVIE, TVSHOW
from resources.lib.data.nfo import NFO, NO_NFO, UNREACHABLE
from resources.lib.engine.scan import (
    _extract_fields,
    _extract_ids,
    _item_title,
    _plan_writes,
    run_scan,
)


@pytest.fixture(autouse=True)
def _mock_secondary_providers():
    """Keep TVDB, the IMDb ratings store and the TMDB id lookups off the
    network: each answers "nothing" unless a test patches it. NFO detection
    reports a reachable item without an NFO.
    """
    with patch("resources.lib.engine.scan.imdb_rating_lookup", return_value=None), \
         patch("resources.lib.engine.scan.nfo_state", return_value=NO_NFO), \
         patch("resources.lib.engine.scan.tvdb_fetch_series", return_value=None), \
         patch("resources.lib.engine.scan.tvdb_fetch_series_episodes", return_value=None), \
         patch("resources.lib.engine.scan.tmdb_fetch_episode_ids", return_value=None), \
         patch("resources.lib.engine.scan.tmdb_fetch_tvshow_ids", return_value=None), \
         patch("resources.lib.engine.scan.tmdb_find_episode", return_value=None), \
         patch("resources.lib.engine.scan.resolve_id", return_value=None):
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
        "imdb_ready": True,
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


def _fp(field, media_type=MOVIE, language="en-US", settings=None):
    """Settings fingerprint of a field sampled under the test settings, for
    rows seeded as if an earlier scan had recorded them."""
    from resources.lib.engine.scan import settings_fingerprint
    return settings_fingerprint(field, settings or _make_settings(), language, media_type.name)


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
# Unit tests: _content_age_days
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
        fields = _extract_fields(tmdb_data, ["rating", "plot"])
        # rating is no longer emitted by _extract_fields; it goes through the
        # named-slot ratings path (build_ratings) instead.
        assert "rating" not in fields
        assert fields["plot"] == "Great movie"
        assert "tagline" not in fields

    def test_skips_empty_string(self):
        tmdb_data = _tmdb_movie_response(tagline="")
        fields = _extract_fields(tmdb_data, ["tagline"])
        assert "tagline" not in fields

    def test_skips_empty_list(self):
        tmdb_data = _tmdb_movie_response(directors=[])
        fields = _extract_fields(tmdb_data, ["director"])
        assert "director" not in fields

    def test_skips_none(self):
        tmdb_data = _tmdb_movie_response(trailer=None)
        fields = _extract_fields(tmdb_data, ["trailer"])
        assert "trailer" not in fields

    def test_skips_zero_runtime(self):
        tmdb_data = _tmdb_movie_response(runtime=0)
        fields = _extract_fields(tmdb_data, ["runtime"])
        assert "runtime" not in fields

    def test_does_not_emit_votes(self):
        # votes is no longer emitted by _extract_fields; it lives inside the
        # named-slot ratings map (build_ratings) instead.
        tmdb_data = _tmdb_movie_response(votes=1000)
        fields = _extract_fields(tmdb_data, ["votes"])
        assert "votes" not in fields

    def test_does_not_include_mpaa(self):
        # mpaa is not in _TMDB_TO_KODI, so even if requested it is not extracted
        tmdb_data = _tmdb_movie_response()
        fields = _extract_fields(tmdb_data, ["mpaa"])
        assert "mpaa" not in fields


def test_extract_fields_converts_runtime_minutes_to_seconds():
    out = _extract_fields({"runtime": 114}, ["runtime"])
    assert out["runtime"] == 114 * 60


def test_extract_fields_skips_rating_and_votes():
    out = _extract_fields({"rating": 7.5, "votes": 100}, ["rating", "votes"])
    assert "rating" not in out and "votes" not in out  # handled by ratings path


def test_extract_fields_skips_bare_year_firstaired():
    out = _extract_fields({"firstaired": "2019"}, ["firstaired"])
    assert "firstaired" not in out


def test_extract_fields_writes_full_firstaired():
    out = _extract_fields({"firstaired": "2019-06-04"}, ["firstaired"])
    assert out["firstaired"] == "2019-06-04"


# ---------------------------------------------------------------------------
# Unit tests: _plan_writes (overwrite-on-diff, NFO protection)
# ---------------------------------------------------------------------------


def _plan(values, item, nfo_managed=False, fs_map=None):
    # type: (Dict[str, Any], Dict[str, Any], bool, Any) -> Any
    return _plan_writes(values, item, nfo_managed, fs_map or {}, "id")


class TestPlanWrites:
    def test_episode_title_replaces_placeholder(self):
        to_write, _ = _plan({"title": "Real Title"}, {"title": "Episode 1"})
        assert to_write["title"] == "Real Title"

    def test_episode_title_overwrites_non_placeholder(self):
        # Maturity model: episode titles just take the latest provider value;
        # there is no placeholder gating any more.
        to_write, _ = _plan({"title": "Different Title"}, {"title": "Call Tracking"})
        assert to_write["title"] == "Different Title"

    def test_same_value_is_held_not_rewritten(self):
        to_write, held = _plan({"plot": "Same plot"}, {"plot": "Same plot"})
        assert to_write == {} and held == {"plot"}

    def test_runtime_same_value_skipped(self):
        # Kodi already has 114 min stored as 6840 s
        to_write, held = _plan({"runtime": 6840}, {"runtime": 6840})
        assert to_write == {} and held == {"runtime"}

    def test_overwrites_nonempty_plot_on_diff(self):
        to_write, _ = _plan({"plot": "English plot"}, {"plot": "Bestaande NL plot"})
        assert to_write["plot"] == "English plot"

    def test_overwrites_nonempty_list_field_on_diff(self):
        to_write, _ = _plan(
            {"director": ["TMDB Director"]}, {"director": ["Curated Director"]},
        )
        assert to_write["director"] == ["TMDB Director"]

    def test_nfo_value_is_neither_written_nor_held(self):
        to_write, held = _plan(
            {"plot": "English plot"}, {"plot": "NFO plot"}, nfo_managed=True,
        )
        assert to_write == {} and held == set()

    def test_nfo_placeholder_title_is_filled(self):
        to_write, _ = _plan(
            {"title": "Real Title"}, {"title": "Aflevering 3"}, nfo_managed=True,
        )
        assert to_write == {"title": "Real Title"}


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
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
@patch("resources.lib.engine.scan.get_all_items")
def test_run_scan_paces_between_provider_calls(
    mock_get_all, mock_tmdb, _mock_update, tmp_path, kodi_stubs
):
    """Two movies make two TMDB calls. The first is not preceded by a sleep
    (pacer not yet armed); the second sleeps once. The IMDb ratings store is
    local and never paced."""
    mock_get_all.return_value = [
        _make_movie_item(), _make_movie_item(title="Other", tmdb_id="67890", movie_id=2),
    ]
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

    assert mock_tmdb.call_count == 2
    mock_sleep.assert_called_once_with(0.25)


@patch("resources.lib.engine.scan.update_item", return_value=True)
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
@patch("resources.lib.engine.scan.get_all_items")
def test_run_scan_no_sleep_when_rate_limit_zero(
    mock_get_all, mock_tmdb, _mock_update, tmp_path, kodi_stubs
):
    """rate_limit=0 (the test default) never sleeps."""
    mock_get_all.return_value = [
        _make_movie_item(), _make_movie_item(title="Other", tmdb_id="67890", movie_id=2),
    ]
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
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "rating", next_retry=None, settings_fp=_fp("rating"),
        )
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "plot", next_retry=None, settings_fp=_fp("plot"),
        )
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
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "rating", next_retry=None, settings_fp=_fp("rating"),
        )
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "plot", next_retry=None, settings_fp=_fp("plot"),
        )
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

        mock_resolve.assert_called_once_with(
            "fake_key", "movie", tvdb_id=None, imdb_id="tt1234567",
        )
        mock_fetch.assert_called_once_with("55555", "fake_key", "en-US")

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.update_item")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_tmdb_returns_none_rating_from_imdb_store(
        self, mock_tmdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        """TMDB has nothing; the IMDb ratings store supplies the rating."""
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = None
        mock_update.return_value = True

        with patch(
            "resources.lib.engine.scan.imdb_rating_lookup", return_value=(8.0, 5000),
        ):
            tracker = _make_tracker(tmp_path)
            stats = run_scan(
                MOVIE, tracker, _make_source_config(),
                frozenset({"rating", "plot"}),
                "fake_key", _make_settings(),
            )

            field = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
            plot = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
            tracker.close()
        assert stats["updated"] == 1
        assert field is not None and field["source"] == "imdb"
        assert plot is not None and plot["fetch_count"] == 1

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
        ep = _make_episode_item(episode=1, tmdb_id="11111")
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
    def test_tvdb_only_episode_processed_not_skipped(
        self, mock_get_all, tmp_path, kodi_stubs
    ):
        """Episode with no tmdb/imdb id (tvdb only) is processed: its TMDB id
        is looked up by TVDB id, and a "not found" is an answer.

        No attempt recorded, no error, not exhausted.
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

        assert stats["processed"] == 1
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

    @patch("resources.lib.engine.scan.log")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_api_key_error_left_to_the_caller_to_log(
        self, mock_movie, mock_season, mock_log, tmp_path, kodi_stubs
    ):
        # The caller that stops the scan logs the invalid key once; a scan
        # line as well would report the same failure twice.
        from resources.lib.utils import ApiKeyError

        mock_movie.side_effect = ApiKeyError("tmdb")
        mock_season.side_effect = ApiKeyError("tmdb")
        cases = (
            (MOVIE, [_make_movie_item()], {}),
            (EPISODE, [_make_episode_item()], {"show_id_map": {10: "55555"}}),
        )
        for media_type, items, extra in cases:
            (tmp_path / media_type.name).mkdir()
            tracker = _make_tracker(tmp_path / media_type.name)
            with pytest.raises(ApiKeyError):
                run_scan(
                    media_type, tracker, _make_source_config(),
                    frozenset({"plot"}), "bad_key", _make_settings(),
                    items=items, **extra,
                )
            tracker.close()
        assert mock_season.called
        events = [c.kwargs.get("event") for c in mock_log.method_calls]
        assert "scan.apikey" not in events


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
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "rating", next_retry=None, settings_fp=_fp("rating"),
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
            "movie", _MOVIE_IDENTITY, "rating", next_retry="2099-01-01", settings_fp=_fp("rating"),
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
            "movie", _MOVIE_IDENTITY, "rating", next_retry="2020-01-01", settings_fp=_fp("rating"),
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
            "movie", _MOVIE_IDENTITY, "rating", next_retry="2020-01-01", settings_fp=_fp("rating"),
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
    def test_tmdb_disabled_stops_pass(
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
        # Pre-disable TMDB: the pass must stop before any fetch.
        breaker.record_failure("tmdb")

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
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "plot", next_retry=None, settings_fp=_fp("plot"),
        )
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
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "plot", next_retry=None, settings_fp=_fp("plot"),
        )
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
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_failed_write_does_not_advance_fetch_count(
        self, mock_tmdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = {"plot": "A plot"}
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
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_successful_write_does_advance_fetch_count(
        self, mock_tmdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = {"plot": "A plot"}
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
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_season_transient_leaves_fetch_count_at_zero(
        self, mock_fetch_season, mock_update, mock_get_all,
        tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        mock_get_all.return_value = [_make_episode_item()]
        mock_fetch_season.side_effect = TransientProviderError("http 503")
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
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_second_cycle_processes_the_library_again(
        self, mock_tmdb, mock_update, mock_get_all, tmp_path, kodi_stubs
    ):
        mock_get_all.return_value = [_make_movie_item()]
        mock_tmdb.return_value = {"plot": "A plot"}
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
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_open_tmdb_breaker_leaves_movie_clock_at_zero(
        self, mock_tmdb, mock_update, mock_get_all, tmp_path, kodi_stubs
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
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.resolve_id")
    def test_transient_resolve_id_leaves_clock_at_zero(
        self, mock_resolve, mock_tmdb, mock_update, mock_get_all,
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


# ---------------------------------------------------------------------------
# Phase 7d: TMDB/TVDB text chain, IMDb ratings module, per-field verdict
# ---------------------------------------------------------------------------

_SHOWS = "smb://server/shows/"


def _nl_source_config():
    # type: () -> Dict[str, Any]
    return {
        "sources": [{"path": _SHOWS, "language": "nl-NL", "enabled": True}],
        "default_language": "en-US",
        "fallback_language": "en-US",
    }


def _nl_movie_source_config():
    # type: () -> Dict[str, Any]
    return {
        "sources": [{"path": "smb://server/movies/", "language": "nl-NL", "enabled": True}],
        "default_language": "en-US",
        "fallback_language": "en-US",
    }


def _tvdb_episode(title="", ep_id="888"):
    # type: (str, Any) -> Any
    """Build a fetch_series_episodes stand-in keyed by language code."""
    return {ep_id: {"title": title, "plot": ""}}


def _register_show(tracker, tvdb_id="777", tmdb_id="55555"):
    tracker.upsert_item("tvshow", "show-10", kodi_id=10, tmdb_id=tmdb_id, tvdb_id=tvdb_id)
    tracker.commit()


def _nl_episode(**overrides):
    # type: (...) -> Dict[str, Any]
    item = _make_episode_item(title="Aflevering 1")
    item["uniqueid"] = {"tmdb": "99999", "tvdb": "888"}
    item.update(overrides)
    return item


def _season_by_lang(by_lang):
    # type: (Dict[str, str]) -> Any
    """tmdb_fetch_season stand-in: the episode title depends on the language."""
    def fetch(show_id, season, key, language):
        return [_tmdb_episode_data(title=by_lang.get(language, ""), plot="")]
    return fetch


def _fs(tracker, media_type, item):
    return tracker.get_field_status_map(
        media_type.name, compute_identity(item, media_type)
    )


def _count(fs_map, field):
    return int(fs_map.get(field, {}).get("fetch_count") or 0)


def _written(mock_update, field):
    for c in mock_update.call_args_list:
        if field in c.kwargs:
            return c.kwargs[field]
    return None


class TestTextChain:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_tvdb_down_does_not_write_english_over_dutch(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _nl_episode()
        mock_season.side_effect = _season_by_lang(
            {"nl-NL": "Aflevering 1", "en-US": "Pilot"}
        )
        mock_tvdb_eps.return_value = _tvdb_episode("Pilot EN")
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
            breaker=_tripped_breaker("tvdb"),
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "title") is None
        assert _count(fs_map, "title") == 0
        mock_tvdb_eps.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_text_order_tvdb_first(
        self, mock_tmdb, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item()
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
        mock_tmdb.return_value = _tmdb_tvshow_response(plot="TMDB plot")
        mock_tvdb.return_value = {"name": "Show", "overview": "TVDB plot", "names": []}
        settings = _make_settings()
        settings["text_source_order"] = 1

        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot"}),
            "k", settings, items=[item],
        )
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        assert _written(mock_update, "plot") == "TVDB plot"
        assert fs_map["plot"]["source"] == "tvdb"
        assert fs_map["plot"]["fetch_count"] == 1
        mock_tvdb.assert_called_once_with("777", "eng")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_text_order_default_tmdb_first_skips_tvdb(
        self, mock_tmdb, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item()
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
        mock_tmdb.return_value = _tmdb_tvshow_response(plot="TMDB plot")

        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        tracker.close()

        assert _written(mock_update, "plot") == "TMDB plot"
        mock_tvdb.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_fallback_order_default_tvdb_first(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _nl_episode()
        mock_season.side_effect = _season_by_lang({"en-US": "Pilot TMDB"})
        mock_tvdb_eps.side_effect = lambda tid, lang: (
            _tvdb_episode("Pilot TVDB") if lang == "eng" else _tvdb_episode("")
        )
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "title") == "Pilot TVDB"
        assert fs_map["title"]["from_fallback"] == 1
        assert fs_map["title"]["source"] == "tvdb"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_fallback_order_tmdb_first(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _nl_episode()
        mock_season.side_effect = _season_by_lang({"en-US": "Pilot TMDB"})
        mock_tvdb_eps.side_effect = lambda tid, lang: (
            _tvdb_episode("Pilot TVDB") if lang == "eng" else _tvdb_episode("")
        )
        settings = _make_settings()
        settings["fallback_order"] = 1
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title"}),
            "k", settings, items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "title") == "Pilot TMDB"
        assert fs_map["title"]["source"] == "tmdb"
        assert [c.args[1] for c in mock_tvdb_eps.call_args_list] == ["nld"]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_folder_fallback_language_used(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _nl_episode()
        mock_season.side_effect = _season_by_lang({})
        mock_tvdb_eps.side_effect = lambda tid, lang: (
            _tvdb_episode("Die Folge") if lang == "deu" else _tvdb_episode("")
        )
        config = _nl_source_config()
        config["fallback_language"] = "de-DE"
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, config, frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        tracker.close()

        assert _written(mock_update, "title") == "Die Folge"
        langs = [c.args[1] for c in mock_tvdb_eps.call_args_list]
        assert langs == ["nld", "deu"]
        assert "en-US" not in [c.args[3] for c in mock_season.call_args_list]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_movies_text_tmdb_only(
        self, mock_tmdb, mock_tvdb, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_movie_item(plot="")
        item["uniqueid"] = {"tmdb": "12345", "imdb": "tt0000001", "tvdb": "5"}
        mock_tmdb.side_effect = lambda tid, key, language: _tmdb_movie_response(
            plot="English plot" if language == "en-US" else "",
        )

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        fs_map = _fs(tracker, MOVIE, item)
        tracker.close()

        assert _written(mock_update, "plot") == "English plot"
        assert fs_map["plot"]["from_fallback"] == 1
        assert [c.args[2] for c in mock_tmdb.call_args_list] == ["nl-NL", "en-US"]
        mock_tvdb.assert_not_called()
        mock_tvdb_eps.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_tvdb_auth_error_disables_tvdb_not_scan(
        self, mock_tmdb, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker
        from resources.lib.utils import TvdbAuthError

        item = _make_tvshow_item()
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
        mock_tmdb.return_value = _tmdb_tvshow_response(plot="TMDB plot")
        mock_tvdb.side_effect = TvdbAuthError("rejected")
        settings = _make_settings()
        settings["text_source_order"] = 1
        breaker = CircuitBreaker()

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot", "genre"}),
            "k", settings, items=[item], breaker=breaker,
        )
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        assert breaker.is_disabled("tvdb")
        assert not breaker.is_disabled("tmdb")
        assert stats["tvdb_auth_failed"] is True
        assert _written(mock_update, "genre") == ["Drama"]
        assert _written(mock_update, "plot") is None
        assert _count(fs_map, "plot") == 0
        assert _count(fs_map, "genre") == 1
        assert stats["aborted"] is False

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_scan_stops_when_all_needed_providers_down(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        breaker = _tripped_breaker("tmdb")
        while not breaker.is_disabled("tvdb"):
            breaker.record_failure("tvdb")
        item = _make_tvshow_item()
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot", "genre"}),
            "k", _make_settings(), items=[item], breaker=breaker,
        )
        tracker.close()

        assert stats["aborted"] is True
        mock_tmdb.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_scan_continues_on_tvdb_when_tmdb_down(
        self, mock_tmdb, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item()
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
        mock_tvdb.return_value = {"name": "Show", "overview": "TVDB plot", "names": []}
        settings = _make_settings()
        settings["text_source_order"] = 1

        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot", "genre"}),
            "k", settings, items=[item], breaker=_tripped_breaker("tmdb"),
        )
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        assert stats["aborted"] is False
        assert _written(mock_update, "plot") == "TVDB plot"
        assert _count(fs_map, "plot") == 1
        assert _count(fs_map, "genre") == 0

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_movie_pass_stops_when_tmdb_down(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_make_movie_item()],
            breaker=_tripped_breaker("tmdb"),
        )
        tracker.close()

        assert stats["aborted"] is True


class TestRatingsVerdict:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_imdb_store_not_loaded_holds_only_rating(
        self, mock_season, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_episode_item(title="")
        item["uniqueid"] = {"tmdb": "99999", "imdb": "tt0000002"}
        mock_season.return_value = [_tmdb_episode_data()]
        settings = _make_settings()
        settings["imdb_ready"] = False

        tracker = _make_tracker(tmp_path)
        with patch("resources.lib.engine.scan.imdb_rating_lookup") as mock_lookup:
            run_scan(
                EPISODE, tracker, _make_source_config(),
                frozenset({"rating", "votes", "plot", "title"}),
                "k", settings, items=[item], show_id_map={10: "55555"},
            )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        mock_lookup.assert_not_called()
        assert _written(mock_update, "ratings") is None
        assert _count(fs_map, "rating") == 0
        assert _count(fs_map, "votes") == 0
        assert _count(fs_map, "plot") == 1
        assert _count(fs_map, "title") == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_imdb_lookup_transient_holds_only_rating(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        mock_tmdb.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        with patch(
            "resources.lib.engine.scan.imdb_rating_lookup",
            side_effect=TransientProviderError("store not loaded"),
        ):
            run_scan(
                MOVIE, tracker, _make_source_config(), frozenset({"plot", "rating"}),
                "k", _make_settings(), items=[_make_movie_item()],
            )
        fs_map = _fs(tracker, MOVIE, _make_movie_item())
        tracker.close()

        assert _count(fs_map, "rating") == 0
        assert _count(fs_map, "plot") == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_imdb_not_consulted_cannot_hold_rating(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        mock_tmdb.return_value = _tmdb_movie_response()
        settings = _make_settings()
        settings["rating_source"] = 2  # TMDB only
        settings["imdb_ready"] = False

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"rating"}),
            "k", settings, items=[_make_movie_item()],
        )
        fs_map = _fs(tracker, MOVIE, _make_movie_item())
        tracker.close()

        assert _count(fs_map, "rating") == 1
        assert "themoviedb" in _written(mock_update, "ratings")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_enrichment_transient_holds_rating(
        self, mock_season, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        item = _make_episode_item()
        mock_season.return_value = [_tmdb_episode_data()]

        tracker = _make_tracker(tmp_path)
        with patch(
            "resources.lib.engine.scan.tmdb_fetch_episode_ids",
            side_effect=TransientProviderError("http 503"),
        ):
            run_scan(
                EPISODE, tracker, _make_source_config(),
                frozenset({"rating", "plot"}),
                "k", _make_settings(), items=[item], show_id_map={10: "55555"},
            )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "ratings") is None
        assert _count(fs_map, "rating") == 0
        assert _count(fs_map, "plot") == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_rating_pref_imdb_only_uses_ratings_module(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        mock_tmdb.return_value = _tmdb_movie_response(rating=6.0, votes=10)
        settings = _make_settings()
        settings["rating_source"] = 3  # IMDb only

        tracker = _make_tracker(tmp_path)
        with patch(
            "resources.lib.engine.scan.imdb_rating_lookup", return_value=(8.1, 12345),
        ) as mock_lookup:
            run_scan(
                MOVIE, tracker, _make_source_config(), frozenset({"rating"}),
                "k", settings, items=[_make_movie_item()],
            )
        fs_map = _fs(tracker, MOVIE, _make_movie_item())
        tracker.close()

        mock_lookup.assert_called_once_with("tt0000001")
        ratings = _written(mock_update, "ratings")
        assert ratings["imdb"] == {"rating": 8.1, "votes": 12345, "default": True}
        assert "themoviedb" not in ratings
        assert fs_map["rating"]["source"] == "imdb"
        assert fs_map["rating"]["vote_count"] == 12345


class TestTextChainIds:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_placeholder_never_written_and_answered_empty(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_episode_item(title="")
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "888"}
        mock_season.return_value = [_tmdb_episode_data(title="Episode 1")]
        mock_tvdb_eps.return_value = _tvdb_episode("")
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "title") is None
        assert _count(fs_map, "title") == 1
        mock_tvdb_eps.assert_called_once_with("777", "eng")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_episode_without_show_tvdb_id_answers_nothing(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_episode_item(title="")
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "888"}
        mock_season.return_value = [_tmdb_episode_data(title="")]
        tracker = _make_tracker(tmp_path)
        # The show's lookups answered and found no TVDB id.
        _register_checked_show(tracker, tvdb_id=None, tmdb_id="55555")

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        mock_tvdb_eps.assert_not_called()
        assert _count(fs_map, "title") == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_tvdb_unsupported_language_answers_nothing(
        self, mock_tmdb, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item()
        item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
        mock_tmdb.return_value = _tmdb_tvshow_response(plot="TMDB plot")
        settings = _make_settings()
        settings["text_source_order"] = 1
        config = {
            "sources": [{"path": _SHOWS, "language": "xx-XX", "enabled": True}],
            "default_language": "en-US",
        }

        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, config, frozenset({"plot"}),
            "k", settings, items=[item],
        )
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        mock_tvdb.assert_not_called()
        assert _written(mock_update, "plot") == "TMDB plot"
        assert _count(fs_map, "plot") == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_tvdb_episode_list_fetched_once_per_show_and_language(
        self, mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
    ):
        first = _make_episode_item(title="")
        first["uniqueid"] = {"tmdb": "1", "tvdb": "888"}
        second = _make_episode_item(title="", episode=2)
        second["episodeid"] = 101
        second["uniqueid"] = {"tmdb": "2", "tvdb": "889"}
        mock_season.return_value = []
        mock_tvdb_eps.return_value = {
            "888": {"title": "One", "plot": ""}, "889": {"title": "Two", "plot": ""},
        }
        settings = _make_settings()
        settings["text_source_order"] = 1
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", settings, items=[first, second], show_id_map={10: "55555"},
        )
        tracker.close()

        mock_tvdb_eps.assert_called_once_with("777", "eng")
        titles = [c.kwargs.get("title") for c in mock_update.call_args_list]
        assert titles == ["One", "Two"]


# ---------------------------------------------------------------------------
# Phase 7d: unknown-type TVDB ids and TMDB cross-references
# ---------------------------------------------------------------------------

_BUURMAN_NAMES = ["Pat a Mat", "Buurman en Buurman"]


def _today_minus(days):
    # type: (int) -> str
    from datetime import timedelta
    return (date.today() - timedelta(days=days)).isoformat()


def _nl_show(title="Buurman en Buurman", uniqueid=None):
    # type: (str, Any) -> Dict[str, Any]
    item = _make_tvshow_item(title=title)
    item["uniqueid"] = uniqueid if uniqueid is not None else {"unknown": "80767"}
    return item


def _register_checked_show(tracker, tvdb_id="80767", tmdb_id=None, checked=True):
    tracker.upsert_item(
        "tvshow", "show-10", kodi_id=10, tmdb_id=tmdb_id, tvdb_id=tvdb_id,
        ids_checked=date.today().isoformat() if checked else None,
    )
    tracker.commit()


def _episode(ep_id, uniqueid, season=1, episode=1, title=""):
    # type: (int, Dict[str, str], int, int, str) -> Dict[str, Any]
    item = _make_episode_item(season=season, episode=episode, title=title)
    item["episodeid"] = ep_id
    item["uniqueid"] = uniqueid
    return item


def _row(tracker, media_type, item):
    return tracker.get_item(media_type.name, compute_identity(item, media_type))


class TestUnknownIds:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.resolve_id", return_value=None)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    def test_unknown_show_id_accepted_and_stored_as_tvdb(
        self, mock_tvdb, mock_resolve, mock_update, tmp_path, kodi_stubs
    ):
        item = _nl_show()
        mock_tvdb.return_value = {
            "name": "Buurman en Buurman", "overview": "Twee buren klussen.",
            "names": _BUURMAN_NAMES,
        }
        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _nl_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        row = _row(tracker, TVSHOW, item)
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        assert row["tvdb_id"] == "80767"
        assert row["ids_checked"] == date.today().isoformat()
        assert mock_resolve.call_args.kwargs["tvdb_id"] == "80767"
        assert _written(mock_update, "plot") == "Twee buren klussen."
        assert fs_map["plot"]["source"] == "tvdb"
        assert fs_map["plot"]["fetch_count"] == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.resolve_id", return_value=None)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    def test_unknown_show_id_rejected_when_name_differs(
        self, mock_tvdb, mock_resolve, mock_update, tmp_path, kodi_stubs
    ):
        item = _nl_show()
        mock_tvdb.return_value = {
            "name": "", "overview": "Something else.", "names": ["Pat a Mat", "Pat & Mat"],
        }
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _nl_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        row = _row(tracker, TVSHOW, item)
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        assert row["tvdb_id"] is None
        mock_resolve.assert_not_called()
        mock_update.assert_not_called()
        assert stats["skipped"] == 1 and stats["processed"] == 0
        assert _count(fs_map, "plot") == 0

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    def test_unknown_show_verification_transient_skips_without_answer(
        self, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        item = _nl_show()
        mock_tvdb.side_effect = TransientProviderError("503")
        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _nl_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        row = _row(tracker, TVSHOW, item)
        fs_map = _fs(tracker, TVSHOW, item)
        tracker.close()

        assert row["ids_checked"] is None
        assert _count(fs_map, "plot") == 0
        mock_update.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    def test_unknown_episode_accepted_only_if_in_series_list(
        self, mock_eps, mock_find, mock_update, tmp_path, kodi_stubs
    ):
        listed = _episode(100, {"unknown": "340249"}, episode=1)
        unlisted = _episode(101, {"unknown": "999"}, episode=2)
        mock_eps.return_value = {"340249": {"title": "Het schilderij", "plot": ""}}
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker)

        stats = run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[listed, unlisted], show_id_map={},
        )
        listed_row = _row(tracker, EPISODE, listed)
        unlisted_row = _row(tracker, EPISODE, unlisted)
        tracker.close()

        assert listed_row["tvdb_id"] == "340249"
        assert unlisted_row["tvdb_id"] is None
        assert [c.args[0] for c in mock_update.call_args_list] == [100]
        assert _written(mock_update, "title") == "Het schilderij"
        assert stats["processed"] == 1 and stats["skipped"] == 1
        # One episode list for the show serves both verifications.
        assert mock_eps.call_count == 1
        # The show answered "not on TMDB": no per-episode /find.
        mock_find.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_episode_ids")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    def test_tvdb_only_episode_gets_tmdb_and_imdb_via_find(
        self, mock_find, mock_season, mock_ep_ids, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "888"})
        mock_find.return_value = {
            "tmdb_id": "99999", "show_tmdb_id": "55555", "season": 1, "episode": 1,
        }
        mock_season.return_value = [_tmdb_episode_data()]
        mock_ep_ids.return_value = {"imdb_id": "tt0777", "tvdb_id": "888"}
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        row = _row(tracker, EPISODE, item)
        tracker.close()

        mock_find.assert_called_once_with("k", "888")
        assert mock_ep_ids.call_args.args[:3] == ("55555", 1, 1)
        assert row["tmdb_id"] == "99999"
        assert row["imdb_id"] == "tt0777"
        assert row["ids_checked"] == date.today().isoformat()
        assert _written(mock_update, "title") == "Pilot"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow_ids")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_tmdb_only_show_gets_tvdb_via_external_ids(
        self, mock_tmdb, mock_ids, mock_tvdb, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item()
        mock_tmdb.return_value = _tmdb_tvshow_response(plot="")
        mock_ids.return_value = {"tvdb_id": "777"}
        mock_tvdb.return_value = {"name": "Show", "overview": "TVDB plot", "names": []}
        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        row = _row(tracker, TVSHOW, item)
        tracker.close()

        mock_ids.assert_called_once_with("k", "99999")
        assert row["tvdb_id"] == "777"
        mock_tvdb.assert_called_once_with("777", "eng")
        assert _written(mock_update, "plot") == "TVDB plot"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    def test_cross_reference_transient_holds_dependent_fields(
        self, mock_find, mock_season, mock_eps, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        item = _episode(100, {"tvdb": "888"})
        mock_find.side_effect = TransientProviderError("503")
        mock_eps.return_value = {"888": {"title": "", "plot": "TVDB plot"}}
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _make_source_config(),
            frozenset({"title", "plot", "runtime"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        row = _row(tracker, EPISODE, item)
        tracker.close()

        mock_season.assert_not_called()
        mock_update.assert_not_called()
        assert _count(fs_map, "title") == 0
        assert _count(fs_map, "plot") == 0
        assert _count(fs_map, "runtime") == 0
        assert row["ids_checked"] is None

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow_ids")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    @patch("resources.lib.engine.scan.resolve_id", return_value="99999")
    def test_verified_ids_not_written_to_kodi(
        self, mock_resolve, mock_tmdb, mock_ids, mock_tvdb, mock_update,
        tmp_path, kodi_stubs
    ):
        accepted = _nl_show()
        accepted["tvshowid"] = 10
        crossref = _make_tvshow_item(title="Other", tmdb_id="12")
        crossref["tvshowid"] = 11
        mock_tvdb.return_value = {
            "name": "Buurman en Buurman", "overview": "", "names": _BUURMAN_NAMES,
        }
        mock_tmdb.return_value = _tmdb_tvshow_response(imdb_id="tt0009")
        mock_ids.return_value = {"tvdb_id": "777"}
        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _nl_source_config(), frozenset({"plot", "genre"}),
            "k", _make_settings(), items=[accepted, crossref],
        )
        tracker.close()

        uids = [c.kwargs["uniqueid"] for c in mock_update.call_args_list if "uniqueid" in c.kwargs]
        assert uids, "the IMDb id write-back still happens"
        for uid in uids:
            assert "tvdb" not in uid and "thetvdb" not in uid
            assert "777" not in uid.values()
        assert {"unknown": "80767", "imdb": "tt0009"} in uids
        assert {"tmdb": "12", "imdb": "tt0009"} in uids


class TestIdResolutionOrder:
    """Episodes depend on their show's ids; the rulings that keep a show that
    is not on TMDB from blocking TVDB text forever."""

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    def test_show_answered_no_tmdb_id_episode_falls_through_to_tvdb(
        self, mock_eps, mock_find, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "340249"})
        mock_eps.return_value = {"340249": {"title": "Het schilderij", "plot": ""}}
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title", "runtime"}),
            "k", _make_settings(), items=[item], show_id_map={},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        mock_find.assert_not_called()
        assert _written(mock_update, "title") == "Het schilderij"
        assert fs_map["title"]["source"] == "tvdb"
        assert _count(fs_map, "title") == 1
        assert _count(fs_map, "runtime") == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    def test_show_not_resolved_holds_episode_tmdb_steps(
        self, mock_eps, mock_find, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "340249"})
        mock_eps.return_value = {"340249": {"title": "Het schilderij", "plot": ""}}
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker, checked=False)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title", "runtime"}),
            "k", _make_settings(), items=[item], show_id_map={},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        mock_find.assert_not_called()
        mock_update.assert_not_called()
        assert _count(fs_map, "title") == 0
        assert _count(fs_map, "runtime") == 0

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_show_without_tvdb_not_resolved_holds_episode_tvdb_step(
        self, mock_season, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tmdb": "99999", "tvdb": "888"})
        mock_season.return_value = [_tmdb_episode_data(title="")]
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker, tvdb_id=None, tmdb_id="55555", checked=False)

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _count(fs_map, "title") == 0

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow_ids")
    @patch("resources.lib.engine.scan.resolve_id", return_value=None)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    def test_show_ids_resolved_even_when_no_field_due(
        self, mock_tvdb, mock_resolve, mock_ids, mock_tmdb, mock_update,
        tmp_path, kodi_stubs
    ):
        unknown = _nl_show()
        tmdb_only = _make_tvshow_item(title="Other", tmdb_id="12")
        tmdb_only["tvshowid"] = 11
        mock_tvdb.return_value = {"name": "", "overview": "", "names": _BUURMAN_NAMES}
        mock_ids.return_value = None
        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _nl_source_config(), frozenset(),
            "k", _make_settings(), items=[unknown, tmdb_only],
        )
        unknown_row = _row(tracker, TVSHOW, unknown)
        tmdb_row = _row(tracker, TVSHOW, tmdb_only)
        checked = tracker.get_checked_show_ids()
        tracker.close()

        assert unknown_row["tvdb_id"] == "80767"
        mock_resolve.assert_called_once()
        mock_ids.assert_called_once_with("k", "12")
        assert tmdb_row["tvdb_id"] is None
        assert checked == frozenset({10, 11})
        mock_tmdb.assert_not_called()
        mock_update.assert_not_called()


class TestCrossReferenceRecheck:
    @pytest.mark.parametrize("age_days,expect_call", [(10, False), (29, False), (30, True)])
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_find_episode", return_value=None)
    def test_answered_empty_find_rechecked_after_30_days(
        self, mock_find, mock_update, age_days, expect_call, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "888"})
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)
        tracker.upsert_item(
            "episode", compute_identity(item, EPISODE), kodi_id=100,
            ids_checked=_today_minus(age_days),
        )
        tracker.commit()

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"runtime"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        row = _row(tracker, EPISODE, item)
        tracker.close()

        assert mock_find.called is expect_call
        # Either way TMDB has answered "no such episode": the field advances.
        assert _count(fs_map, "runtime") == 1
        expected = date.today().isoformat() if expect_call else _today_minus(age_days)
        assert row["ids_checked"] == expected

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_find_episode", return_value=None)
    def test_answered_empty_find_not_repeated_next_scan(
        self, mock_find, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "888"})
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)
        for _ in range(2):
            tracker.start_new_cycle()
            run_scan(
                EPISODE, tracker, _make_source_config(), frozenset({"runtime"}),
                "k", _make_settings(), items=[item], show_id_map={10: "55555"},
                reset=True,
            )
        tracker.close()

        assert mock_find.call_count == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow", return_value=None)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow_ids")
    def test_recently_checked_show_not_looked_up(
        self, mock_ids, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item()
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item(
            "tvshow", compute_identity(item, TVSHOW), kodi_id=10,
            ids_checked=_today_minus(3),
        )
        tracker.commit()
        run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        tracker.close()

        mock_ids.assert_not_called()


class TestImdbStoreErrorLogging:
    @patch("resources.lib.engine.scan.log")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.imdb_rating_lookup")
    def test_store_error_logged_once_per_scan(
        self, mock_lookup, mock_tmdb, mock_update, mock_log, tmp_path, kodi_stubs
    ):
        mock_lookup.side_effect = RuntimeError("store broken")
        mock_tmdb.return_value = _tmdb_movie_response()
        items = []
        for n in range(3):
            movie = _make_movie_item(tmdb_id=str(100 + n), movie_id=n + 1)
            items.append(movie)
        settings = _make_settings()
        settings["rating_source"] = 3  # IMDb only

        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"rating"}),
            "k", settings, items=items,
        )
        tracker.close()

        failures = [
            c for c in mock_log.exception.call_args_list
            if c.kwargs.get("event") == "scan.imdb_lookup_failed"
        ]
        assert len(failures) == 1
        assert mock_lookup.call_count == 3


class TestDeferredLookupsNotRecorded:
    """A lookup deferred because the show is not resolved yet did not answer,
    so it must not close the 30-day gate (ids_checked)."""

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_find_episode", return_value=None)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    def test_tmdb_deferred_for_unresolved_show_is_retried_next_scan(
        self, mock_eps, mock_find, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"unknown": "340249"})
        mock_eps.return_value = {"340249": {"title": "", "plot": ""}}
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker, checked=False)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"runtime", "title"}),
            "k", _make_settings(), items=[item], show_id_map={},
        )
        after_first = _row(tracker, EPISODE, item)
        first_counts = _fs(tracker, EPISODE, item)

        # The show resolves between the scans.
        _register_checked_show(tracker, tmdb_id="55555")
        tracker.start_new_cycle()
        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"runtime", "title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        tracker.close()

        assert after_first["tvdb_id"] == "340249"
        assert after_first["ids_checked"] is None
        assert _count(first_counts, "runtime") == 0
        mock_find.assert_called_once_with("k", "340249")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_unknown_verification_deferred_keeps_gate_open(
        self, mock_season, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tmdb": "99999", "unknown": "340249"})
        mock_season.return_value = [_tmdb_episode_data()]
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker, tvdb_id=None, tmdb_id="55555", checked=False)

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"runtime"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        row = _row(tracker, EPISODE, item)
        tracker.close()

        assert row["ids_checked"] is None


class TestFoundEpisodeSeason:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_episode_ids")
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    def test_found_episode_fetched_by_tmdb_numbering(
        self, mock_find, mock_season, mock_ep_ids, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "888"}, season=1, episode=1)
        mock_find.return_value = {
            "tmdb_id": "99999", "show_tmdb_id": "55555", "season": 2, "episode": 5,
        }
        mock_season.side_effect = lambda show, season, key, lang: (
            [_tmdb_episode_data(episode_number=5, season_number=2)] if season == 2 else []
        )
        mock_ep_ids.return_value = {"imdb_id": "tt0777"}
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        for _ in range(2):
            tracker.start_new_cycle()
            run_scan(
                EPISODE, tracker, _make_source_config(), frozenset({"title"}),
                "k", _make_settings(), items=[item], show_id_map={10: "55555"},
                reset=True,
            )
        row = _row(tracker, EPISODE, item)
        tracker.close()

        assert mock_find.call_count == 1
        assert {c.args[1] for c in mock_season.call_args_list} == {2}
        assert mock_ep_ids.call_args.args[:3] == ("55555", 2, 5)
        assert row["tmdb_season"] == 2
        assert _written(mock_update, "title") == "Pilot"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season", return_value=[])
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    def test_found_episode_missing_from_its_season_answers_nothing(
        self, mock_find, mock_season, mock_update, tmp_path, kodi_stubs
    ):
        item = _episode(100, {"tvdb": "888"})
        mock_find.return_value = {
            "tmdb_id": "99999", "show_tmdb_id": "55555", "season": 2, "episode": 5,
        }
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"runtime"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs_map = _fs(tracker, EPISODE, item)
        tracker.close()

        assert mock_season.call_args.args[1] == 2
        assert _count(fs_map, "runtime") == 1


class TestShowYearVerification:
    @pytest.mark.parametrize("premiered,tvdb_year,accepted", [
        ("1976-06-01", 1976, True),
        ("2025-01-01", 1976, False),
        ("", 1976, True),
        ("2025-01-01", None, True),
    ])
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series")
    def test_show_year_must_match_when_both_known(
        self, mock_tvdb, mock_update, premiered, tvdb_year, accepted,
        tmp_path, kodi_stubs
    ):
        item = _nl_show()
        item["premiered"] = premiered
        mock_tvdb.return_value = {
            "name": "", "overview": "", "names": _BUURMAN_NAMES, "year": tvdb_year,
        }
        tracker = _make_tracker(tmp_path)
        run_scan(
            TVSHOW, tracker, _nl_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        row = _row(tracker, TVSHOW, item)
        tracker.close()

        assert (row["tvdb_id"] == "80767") is accepted


# ---------------------------------------------------------------------------
# NFO protection: NFO values are never changed; metamender fills empty fields
# and keeps refreshing what it wrote itself.
# ---------------------------------------------------------------------------


def _young_movie(**overrides):
    # type: (...) -> Dict[str, Any]
    item = _make_movie_item(**overrides)
    item["premiered"] = date.today().isoformat()  # volatile: re-sampled when due
    return item


def _seed_field(
    tracker, field, written_value, media_type=MOVIE, identity=_MOVIE_IDENTITY, settings=None,
):
    """A field sampled once before, now due again, with the given provenance."""
    tracker.upsert_item(media_type.name, identity, kodi_id=1, tmdb_id="12345")
    tracker.record_field_fetch(
        media_type.name, identity, field, next_retry="2020-01-01",
        written_value=written_value, settings_fp=_fp(field, media_type, settings=settings),
    )
    tracker.commit()


class TestNfoProtection:
    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_nfo_title_never_overwritten(
        self, mock_season, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        item = _make_episode_item(title="De Eerste Aflevering")
        mock_season.return_value = [_tmdb_episode_data(title="Pilot")]
        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "title") is None
        # Answered and protected for good: the clock advances, no provenance.
        assert _count(fs, "title") == 1
        assert fs["title"]["written_value"] is None
        assert fs["title"]["source"] is None

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_empty_plot_filled(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        mock_fetch.return_value = _tmdb_movie_response(plot="Een mooie film")
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_make_movie_item(plot="")],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        tracker.close()

        assert _written(mock_update, "plot") == "Een mooie film"
        assert row["written_value"] == fingerprint("Een mooie film")
        assert row["source"] == "tmdb"

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_field_we_wrote_is_refreshed(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        mock_fetch.return_value = _tmdb_movie_response(plot="Fresh plot")
        tracker = _make_tracker(tmp_path)
        _seed_field(tracker, "plot", fingerprint("Old plot"))
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_young_movie(plot="Old plot")],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        tracker.close()

        assert _written(mock_update, "plot") == "Fresh plot"
        assert row["written_value"] == fingerprint("Fresh plot")
        assert row["fetch_count"] == 2

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_field_changed_after_our_write_is_left(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        mock_fetch.return_value = _tmdb_movie_response(plot="Fresh plot")
        tracker = _make_tracker(tmp_path)
        _seed_field(tracker, "plot", fingerprint("Old plot"))
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_young_movie(plot="Edited by the user")],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        tracker.close()

        assert _written(mock_update, "plot") is None
        assert not mock_update.called
        assert row["fetch_count"] == 2
        assert row["written_value"] == fingerprint("Old plot")

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.imdb_rating_lookup", return_value=(7.0, 80))
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_ratings_imdb_slot_refreshed_default_kept(
        self, mock_fetch, mock_update, _mock_imdb, _mock_nfo, tmp_path, kodi_stubs
    ):
        import json

        from resources.lib.engine.resolve import slot_fingerprint

        nfo_slot = {"rating": 7.2, "votes": 300, "default": True}
        our_slot = {"rating": 6.5, "votes": 50, "default": False}
        item = _young_movie()
        item["ratings"] = {"themoviedb": nfo_slot, "imdb": our_slot}
        mock_fetch.return_value = _tmdb_movie_response(rating=7.5, votes=1000)
        tracker = _make_tracker(tmp_path)
        _seed_field(tracker, "rating", json.dumps({"imdb": slot_fingerprint(our_slot)}))
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"rating"}),
            "k", _make_settings(), items=[item],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        tracker.close()

        ratings = _written(mock_update, "ratings")
        assert ratings["imdb"] == {"rating": 7.0, "votes": 80, "default": False}
        assert ratings["themoviedb"] == nfo_slot
        owned = json.loads(row["written_value"])
        assert owned == {"imdb": slot_fingerprint({"rating": 7.0, "votes": 80})}
        assert row["fetch_count"] == 2

    @patch("resources.lib.engine.scan.nfo_state", return_value=UNREACHABLE)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_unreachable_media_skips_item_without_clock_advance(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        item = _make_movie_item()
        del item["uniqueid"]["tmdb"]  # would trigger an id lookup if processed
        mock_fetch.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        with patch("resources.lib.engine.scan.resolve_id") as mock_resolve:
            stats = run_scan(
                MOVIE, tracker, _make_source_config(), frozenset({"plot", "rating"}),
                "k", _make_settings(), items=[item],
            )
        fs = _fs(tracker, MOVIE, item)
        row = _row(tracker, MOVIE, item)
        tracker.close()

        assert not mock_fetch.called and not mock_resolve.called
        assert not mock_update.called
        assert fs == {}
        assert row["ids_checked"] is None
        assert not row["cycle_processed"]
        assert stats["unreachable"] == 1 and stats["skipped"] == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_detection_cached_per_scan(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        due_a = _make_movie_item(tmdb_id="1", movie_id=1)
        due_b = _make_movie_item(tmdb_id="2", movie_id=2)
        due_b["file"] = "smb://server/movies/b.mkv"
        final = _make_movie_item(tmdb_id="3", movie_id=3)
        final["premiered"] = "2000-01-01"
        mock_fetch.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        fields = frozenset({"plot", "rating", "genre"})
        run_scan(
            MOVIE, tracker, _make_source_config(), fields,
            "k", _make_settings(), items=[final],
        )
        final["plot"] = "Great movie"  # what Kodi now holds
        tracker.start_new_cycle()
        with patch("resources.lib.engine.scan.nfo_state", return_value=NO_NFO) as mock_nfo:
            run_scan(
                MOVIE, tracker, _make_source_config(), fields,
                "k", _make_settings(), items=[due_a, due_b, final],
            )
        tracker.close()

        # One check per item with work to do; the fully final item is not
        # looked at, so a sleeping share is not woken for it.
        assert sorted(c.args for c in mock_nfo.call_args_list) == [
            ("movie", "smb://server/movies/b.mkv"),
            ("movie", "smb://server/movies/test.mkv"),
        ]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_written_value_recorded_without_nfo_as_written_to_kodi(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Provenance exists for every write, so an NFO added later still
        lets metamender refresh its own values. Runtime is fingerprinted in
        the seconds Kodi stores, not the provider's minutes."""
        import json

        from resources.lib.engine.protect import fingerprint
        from resources.lib.engine.resolve import slot_fingerprint

        mock_fetch.return_value = _tmdb_movie_response(runtime=120, rating=7.5, votes=1000)
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(),
            frozenset({"runtime", "rating", "votes"}),
            "k", _make_settings(), items=[_make_movie_item()],
        )
        fs = _fs(tracker, MOVIE, _make_movie_item())
        tracker.close()

        assert _written(mock_update, "runtime") == 7200
        assert fs["runtime"]["written_value"] == fingerprint(7200)
        expected = {"themoviedb": slot_fingerprint({"rating": 7.5, "votes": 1000})}
        assert json.loads(fs["rating"]["written_value"]) == expected
        assert json.loads(fs["votes"]["written_value"]) == expected

    @patch("resources.lib.engine.scan.update_item", return_value=False)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_rejected_write_records_no_provenance(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="A plot")
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_young_movie()],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        tracker.close()

        assert mock_update.called
        assert row is None

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.imdb_rating_lookup", return_value=(7.0, 80))
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_ratings_unreadable_provenance_protects_slot(
        self, mock_fetch, mock_update, _mock_imdb, _mock_nfo, tmp_path, kodi_stubs
    ):
        item = _young_movie()
        item["ratings"] = {"imdb": {"rating": 6.5, "votes": 50, "default": True}}
        mock_fetch.return_value = _tmdb_movie_response(rating=0, votes=0)
        tracker = _make_tracker(tmp_path)
        _seed_field(tracker, "rating", "not json")
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"rating"}),
            "k", _make_settings(), items=[item],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        tracker.close()

        assert _written(mock_update, "ratings") is None
        assert row["fetch_count"] == 2
        assert row["written_value"] == "not json"

    @patch("resources.lib.engine.scan.imdb_rating_lookup", return_value=(7.0, 80))
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_rating_provenance_keeps_slots_not_written_this_scan(
        self, mock_fetch, mock_update, _mock_imdb, tmp_path, kodi_stubs
    ):
        """A slot metamender owns stays owned when only another slot changed."""
        import json

        from resources.lib.engine.resolve import slot_fingerprint

        tmdb_slot = {"rating": 7.5, "votes": 1000, "default": True}
        item = _young_movie()
        item["ratings"] = {
            "themoviedb": tmdb_slot,
            "imdb": {"rating": 6.5, "votes": 50, "default": False},
        }
        mock_fetch.return_value = _tmdb_movie_response(rating=7.5, votes=1000)
        tracker = _make_tracker(tmp_path)
        settings = _make_settings()
        settings["rating_source"] = 3  # IMDb only: the TMDB slot is not written
        _seed_field(tracker, "rating", json.dumps({
            "themoviedb": slot_fingerprint(tmdb_slot),
            "imdb": slot_fingerprint({"rating": 6.5, "votes": 50}),
        }), settings=settings)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"rating"}),
            "k", settings, items=[item],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        tracker.close()

        assert _written(mock_update, "ratings")["imdb"]["rating"] == 7.0
        assert json.loads(row["written_value"]) == {
            "themoviedb": slot_fingerprint(tmdb_slot),
            "imdb": slot_fingerprint({"rating": 7.0, "votes": 80}),
        }

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_update_now_keeps_nfo_protection_and_records_provenance(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        item = _young_movie(plot="")
        item["tagline"] = "Van het NFO"
        mock_fetch.return_value = _tmdb_movie_response(plot="A plot", tagline="From TMDB")
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot", "tagline"}),
            "k", _make_settings(), items=[item], reset=True,
        )
        fs = _fs(tracker, MOVIE, item)
        tracker.close()

        assert _written(mock_update, "plot") == "A plot"
        assert _written(mock_update, "tagline") is None
        assert fs["plot"]["written_value"] == fingerprint("A plot")
        assert fs["tagline"]["written_value"] is None


class TestSourceFromWrittenValue:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    def test_bare_year_firstaired_records_no_source(
        self, mock_season, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_episode_item()
        mock_season.return_value = [_tmdb_episode_data(firstaired="2025")]
        tracker = _make_tracker(tmp_path)
        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"firstaired"}),
            "k", _make_settings(), items=[item], show_id_map={10: "55555"},
        )
        fs = _fs(tracker, EPISODE, item)
        tracker.close()

        assert _written(mock_update, "firstaired") is None
        assert _count(fs, "firstaired") == 1
        assert fs["firstaired"]["source"] is None
        assert fs["firstaired"]["written_value"] is None

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_value_already_in_kodi_records_source_not_provenance(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="Same plot")
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_make_movie_item(plot="Same plot")],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        tracker.close()

        assert not mock_update.called
        assert row["source"] == "tmdb"
        assert row["written_value"] is None


class TestKodiStoredForm:
    def test_extract_fields_trims_text_like_kodi(self):
        out = _extract_fields({"plot": "  Een film.\n", "tagline": " \n"}, ["plot", "tagline"])
        assert out == {"plot": "Een film."}

    def test_extract_fields_list_padded_and_empty_entries(self):
        out = _extract_fields(
            {"genres": [" Drama ", "", "Actie / Avontuur", "  "]}, ["genre"],
        )
        assert out["genre"] == ["Drama", "Actie", "Avontuur"]

    def test_extract_fields_list_of_only_empty_entries_dropped(self):
        assert _extract_fields({"directors": ["", " "]}, ["director"]) == {}

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_padded_overview_written_trimmed_and_recognised_next_scan(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        mock_fetch.return_value = _tmdb_movie_response(plot="Een film.\n")
        tracker = _make_tracker(tmp_path)
        item = _young_movie(plot="")
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        first = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        assert _written(mock_update, "plot") == "Een film."
        assert first["written_value"] == fingerprint("Een film.")

        def rescan(provider_plot):
            # Kodi returns the trimmed value it stored; the field is due again.
            tracker._conn.execute("UPDATE field_status SET next_retry = '2020-01-01'")
            tracker.start_new_cycle()
            mock_update.reset_mock()
            mock_fetch.return_value = _tmdb_movie_response(plot=provider_plot)
            run_scan(
                MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
                "k", _make_settings(), items=[_young_movie(plot="Een film.")],
            )

        rescan("Een film.\n")
        assert not mock_update.called  # same-value skip matches
        rescan("Een betere film.\n")  # still recognised as ours, so refreshed
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")
        tracker.close()
        assert _written(mock_update, "plot") == "Een betere film."
        assert row["written_value"] == fingerprint("Een betere film.")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_list_written_and_fingerprinted_in_kodi_form(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        mock_fetch.return_value = _tmdb_movie_response(genres=[" Drama ", "", "Komedie"])
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"genre"}),
            "k", _make_settings(), items=[_make_movie_item()],
        )
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "genre")
        tracker.close()

        assert _written(mock_update, "genre") == ["Drama", "Komedie"]
        assert row["written_value"] == fingerprint(["Drama", "Komedie"])


class TestRatingProvenanceRows:
    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.imdb_rating_lookup", return_value=(7.0, 80))
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_newer_votes_row_provenance_wins_over_stale_rating_row(
        self, mock_fetch, mock_update, _mock_imdb, _mock_nfo, tmp_path, kodi_stubs
    ):
        """Only the votes field is managed now, so only its row holds the JSON
        of the last write; the rating row's older JSON must not protect the
        slot metamender wrote."""
        import json

        from resources.lib.engine.resolve import slot_fingerprint

        our_slot = {"rating": 6.5, "votes": 50, "default": False}
        item = _young_movie()
        item["ratings"] = {"imdb": our_slot}
        mock_fetch.return_value = _tmdb_movie_response(rating=0, votes=0)
        tracker = _make_tracker(tmp_path)
        _seed_field(tracker, "rating", json.dumps(
            {"imdb": slot_fingerprint({"rating": 6.0, "votes": 10})},
        ))
        _seed_field(tracker, "votes", json.dumps({"imdb": slot_fingerprint(our_slot)}))
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"votes"}),
            "k", _make_settings(), items=[item],
        )
        votes = tracker.get_field_status("movie", _MOVIE_IDENTITY, "votes")
        tracker.close()

        assert _written(mock_update, "ratings")["imdb"]["rating"] == 7.0
        assert json.loads(votes["written_value"]) == {
            "imdb": slot_fingerprint({"rating": 7.0, "votes": 80}),
        }

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.imdb_rating_lookup", return_value=(7.0, 80))
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_no_row_matching_kodi_slot_protects_it(
        self, mock_fetch, mock_update, _mock_imdb, _mock_nfo, tmp_path, kodi_stubs
    ):
        import json

        from resources.lib.engine.resolve import slot_fingerprint

        item = _young_movie()
        item["ratings"] = {"imdb": {"rating": 6.5, "votes": 50, "default": True}}
        mock_fetch.return_value = _tmdb_movie_response(rating=0, votes=0)
        tracker = _make_tracker(tmp_path)
        stale = json.dumps({"imdb": slot_fingerprint({"rating": 6.0, "votes": 10})})
        _seed_field(tracker, "rating", stale)
        _seed_field(tracker, "votes", "not json")
        run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"rating", "votes"}),
            "k", _make_settings(), items=[item],
        )
        tracker.close()

        assert _written(mock_update, "ratings") is None


# ---------------------------------------------------------------------------
# Fallback recheck (spec section 8) and settings fingerprint (section 9)
# ---------------------------------------------------------------------------

_MOVIES = "smb://server/movies/"


def _today_plus(days):
    # type: (int) -> str
    from datetime import timedelta
    return (date.today() + timedelta(days=days)).isoformat()


def _old_movie(plot="", **overrides):
    # type: (str, Any) -> Dict[str, Any]
    """Content past the stability age: final after one answered sample."""
    item = _make_movie_item(plot=plot, **overrides)
    item["premiered"] = "2000-01-01"
    return item


def _plot_by_lang(by_lang):
    # type: (Dict[str, str]) -> Any
    """tmdb_fetch_movie stand-in: the plot depends on the language."""
    return lambda tid, key, language: _tmdb_movie_response(plot=by_lang.get(language, ""))


def _seed_fallback_plot(tracker, value, checks=0, next_retry="2020-01-01", settings=None):
    """A plot metamender wrote from the fallback language, sampled since
    ``checks`` times, stamped with the current settings."""
    from resources.lib.engine.protect import fingerprint
    from resources.lib.engine.scan import settings_fingerprint

    tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
    fp = settings_fingerprint("plot", settings or _make_settings(), "nl-NL", "movie", "en-US")
    tracker.record_field_fetch(
        "movie", _MOVIE_IDENTITY, "plot", next_retry=next_retry, source="tmdb",
        written_value=fingerprint(value), from_fallback=True, settings_fp=fp,
    )
    tracker._conn.execute("UPDATE field_status SET fallback_checks = ?", (checks,))
    tracker.commit()


def _plot_row(tracker):
    return tracker.get_field_status("movie", _MOVIE_IDENTITY, "plot")


class TestFallbackRecheck:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_fallback_write_schedules_monthly_recheck(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie()],
        )
        row = _plot_row(tracker)
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        # Old content would be final after one sample; a fallback value is not.
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") == "English plot"
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 0)
        assert row["next_retry"] == _today_plus(FALLBACK_RECHECK_DAYS)
        assert not mock_fetch.called  # not due yet: skipped, not final

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_due_recheck_without_native_value_counts_once(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=3)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert not mock_update.called
        # Fill-only: the fallback language is not asked for a filled field.
        assert [c.args[2] for c in mock_fetch.call_args_list] == ["nl-NL"]
        assert row["fetch_count"] == 2
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 4)
        assert row["next_retry"] == _today_plus(FALLBACK_RECHECK_DAYS)

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_fallback_recheck_not_due_is_skipped(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", next_retry=_today_plus(5))
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert not mock_fetch.called
        assert row["fetch_count"] == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_fallback_after_limit_is_final_in_scan(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=FALLBACK_RECHECKS)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        tracker.close()

        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_native_value_replaces_fallback_and_clears_flag(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint

        mock_fetch.side_effect = _plot_by_lang(
            {"nl-NL": "Nederlandse plot", "en-US": "English plot"},
        )
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=4)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        row = _plot_row(tracker)
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="Nederlandse plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") == "Nederlandse plot"
        assert (row["from_fallback"], row["fallback_checks"]) == (0, 0)
        assert row["written_value"] == fingerprint("Nederlandse plot")
        assert row["fetch_count"] == 2
        # Normal maturity from here: old content is final.
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_native_value_equal_to_fallback_value_clears_flag(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """The folder language now has the same text: nothing to write, but
        the value is no longer fallback-only, so the rechecks stop."""
        mock_fetch.side_effect = _plot_by_lang({"nl-NL": "Same plot"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "Same plot", checks=2)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="Same plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert not mock_update.called
        assert row["fetch_count"] == 2
        assert (row["from_fallback"], row["fallback_checks"]) == (0, 0)
        assert row["source"] == "tmdb"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_transient_recheck_records_nothing(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.breaker import CircuitBreaker
        from resources.lib.utils import TransientProviderError

        mock_fetch.side_effect = TransientProviderError("boom")
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=3)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
            breaker=CircuitBreaker(threshold=5),
        )
        row = _plot_row(tracker)
        tracker.close()

        assert mock_fetch.called
        assert row["fetch_count"] == 1
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 3)
        assert row["next_retry"] == "2020-01-01"

    @patch("resources.lib.engine.scan.update_item", return_value=False)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_failed_write_of_fallback_value_records_nothing(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie()],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == "English plot"
        assert row is None

    @patch("resources.lib.engine.scan.update_item", return_value=False)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_failed_write_of_native_value_keeps_fallback_flag(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"nl-NL": "Nederlandse plot"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=3)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert row["fetch_count"] == 1
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 3)

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_update_now_restarts_recheck_count_and_keeps_provenance(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint
        from resources.lib.engine.scan import settings_fingerprint

        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=7)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")], reset=True,
        )
        row = _plot_row(tracker)
        tracker.close()

        assert not mock_update.called
        assert row["fetch_count"] == 1
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 0)
        assert row["written_value"] == fingerprint("English plot")
        assert row["settings_fp"] == settings_fingerprint(
            "plot", _make_settings(), "nl-NL", "movie", "en-US",
        )
        assert row["next_retry"] <= _today_plus(FALLBACK_RECHECK_DAYS)

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_update_now_fallback_write_is_flagged(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie()], reset=True,
        )
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == "English plot"
        assert (row["from_fallback"], row["source"]) == (1, "tmdb")


def _two_folder_config(lang_a, lang_b):
    # type: (str, str) -> Dict[str, Any]
    return {
        "sources": [
            {"path": _MOVIES + "a/", "language": lang_a, "enabled": True},
            {"path": _MOVIES + "b/", "language": lang_b, "enabled": True},
        ],
        "default_language": "en-US",
    }


def _movie_in(folder, movie_id, tmdb_id):
    item = _old_movie(plot="Plot", tmdb_id=tmdb_id, movie_id=movie_id)
    item["file"] = _MOVIES + folder + "/film.mkv"
    item["genre"] = ["Drama"]
    item["uniqueid"] = {"tmdb": tmdb_id}
    return item


class TestSettingsFingerprint:
    def test_inputs_per_field(self):
        from resources.lib.engine.scan import settings_fingerprint as sfp

        base = {"rating_source": 0, "text_source_order": 0, "fallback_order": 0}

        def changed(field, media, key, value):
            if key == "fallback_language":
                return (sfp(field, base, "nl-NL", media, "en-US")
                        != sfp(field, base, "nl-NL", media, value))
            other = dict(base)
            other[key] = value
            return sfp(field, base, "nl-NL", media) != sfp(field, other, "nl-NL", media)

        assert changed("rating", "movie", "rating_source", 1)
        assert not changed("rating", "movie", "text_source_order", 1)
        assert not changed("votes", "episode", "fallback_language", "de-DE")
        assert sfp("rating", base, "nl-NL", "movie") == sfp("rating", base, "de-DE", "movie")
        for media in ("tvshow", "episode"):
            assert changed("plot", media, "text_source_order", 1)
            assert changed("plot", media, "fallback_order", 1)
            assert changed("title", media, "fallback_language", "de-DE")
            assert sfp("plot", base, "nl-NL", media) != sfp("plot", base, "de-DE", media)
        assert not changed("plot", "movie", "text_source_order", 1)
        assert not changed("plot", "movie", "fallback_order", 1)
        assert changed("plot", "movie", "fallback_language", "de-DE")
        assert sfp("plot", base, "nl-NL", "movie") != sfp("plot", base, "de-DE", "movie")
        # Single-step TMDB fields depend only on the folder language.
        assert not changed("genre", "episode", "text_source_order", 1)
        assert not changed("genre", "episode", "fallback_language", "de-DE")
        assert sfp("genre", base, "nl-NL", "movie") != sfp("genre", base, "de-DE", "movie")

    def test_missing_settings_use_scan_defaults(self):
        from resources.lib.engine.scan import settings_fingerprint as sfp

        explicit = {"rating_source": 0, "text_source_order": 0, "fallback_order": 0}
        for field in ("rating", "plot", "genre"):
            assert sfp(field, {}, "nl-NL", "episode") == sfp(field, explicit, "nl-NL", "episode")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_unchanged_settings_do_not_refetch(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="Plot")
        tracker = _make_tracker(tmp_path)
        fields = frozenset({"plot", "rating", "genre"})
        run_scan(MOVIE, tracker, _nl_movie_source_config(), fields, "k",
                 _make_settings(), items=[_old_movie(plot="Plot")])
        assert mock_fetch.called
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        run_scan(MOVIE, tracker, _nl_movie_source_config(), fields, "k",
                 _make_settings(), items=[_old_movie(plot="Plot")])
        tracker.close()

        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_rating_source_change_refetches_ratings_only(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.scan import settings_fingerprint

        mock_fetch.return_value = _tmdb_movie_response(plot="Plot")
        tracker = _make_tracker(tmp_path)
        fields = frozenset({"plot", "rating", "votes", "genre"})
        run_scan(MOVIE, tracker, _nl_movie_source_config(), fields, "k",
                 _make_settings(), items=[_old_movie(plot="Plot")])
        before = _fs(tracker, MOVIE, _old_movie())
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        settings = _make_settings()
        settings["rating_source"] = 2  # TMDB only
        run_scan(MOVIE, tracker, _nl_movie_source_config(), fields, "k",
                 settings, items=[_old_movie(plot="Plot")])
        after = _fs(tracker, MOVIE, _old_movie())
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        run_scan(MOVIE, tracker, _nl_movie_source_config(), fields, "k",
                 settings, items=[_old_movie(plot="Plot")])
        tracker.close()

        assert after["rating"]["settings_fp"] == settings_fingerprint(
            "rating", settings, "nl-NL", "movie",
        )
        assert after["votes"]["settings_fp"] != before["votes"]["settings_fp"]
        for f in ("rating", "votes"):
            assert after[f]["fetched_at"] == date.today().isoformat()
            assert after[f]["fetch_count"] == 1  # re-baselined
        for f in ("plot", "genre"):
            assert after[f] == before[f]
        # Once re-checked, the new settings are the baseline.
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_folder_language_change_refetches_text_in_folder_only(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="Plot", genres=["Drama"])
        tracker = _make_tracker(tmp_path)
        fields = frozenset({"plot", "genre", "rating"})
        movie_a = _movie_in("a", 1, "111")
        movie_b = _movie_in("b", 2, "222")
        run_scan(MOVIE, tracker, _two_folder_config("nl-NL", "en-US"), fields, "k",
                 _make_settings(), items=[movie_a, movie_b])
        before_b = _fs(tracker, MOVIE, movie_b)
        rating_a = _fs(tracker, MOVIE, movie_a)["rating"]
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        run_scan(MOVIE, tracker, _two_folder_config("de-DE", "en-US"), fields, "k",
                 _make_settings(), items=[movie_a, movie_b])
        after_a = _fs(tracker, MOVIE, movie_a)
        after_b = _fs(tracker, MOVIE, movie_b)
        tracker.close()

        assert [(c.args[0], c.args[2]) for c in mock_fetch.call_args_list] == [
            ("111", "de-DE"),
        ]
        assert after_a["plot"]["fetch_count"] == 1
        assert after_a["genre"]["settings_fp"] != rating_a["settings_fp"]
        assert after_a["rating"] == rating_a
        assert after_b == before_b

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_text_order_change_refetches_show_text_not_movies(
        self, mock_show, mock_movie, mock_update, tmp_path, kodi_stubs
    ):
        mock_show.return_value = _tmdb_tvshow_response(plot="Plot", genres=["Drama"])
        mock_movie.return_value = _tmdb_movie_response(plot="Plot")
        show = _make_tvshow_item()
        show.update(plot="Plot", genre=["Drama"], premiered="2000-01-01")
        movie = _old_movie(plot="Plot")
        fields = frozenset({"plot", "genre"})
        tracker = _make_tracker(tmp_path)

        def scan_both(settings):
            run_scan(TVSHOW, tracker, _make_source_config(), fields, "k",
                     settings, items=[show])
            run_scan(MOVIE, tracker, _make_source_config(), fields, "k",
                     settings, items=[movie])

        scan_both(_make_settings())
        genre_before = _fs(tracker, TVSHOW, show)["genre"]
        tracker.start_new_cycle()
        mock_show.reset_mock()
        mock_movie.reset_mock()
        settings = _make_settings()
        settings["text_source_order"] = 1
        scan_both(settings)
        after = _fs(tracker, TVSHOW, show)
        tracker.close()

        assert mock_show.called
        assert not mock_movie.called
        assert after["plot"]["fetch_count"] == 1
        assert after["plot"]["fetched_at"] == date.today().isoformat()
        assert after["genre"] == genre_before

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_settings_change_transient_keeps_old_fingerprint(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        fields = frozenset({"rating"})
        run_scan(MOVIE, tracker, _make_source_config(), fields, "k",
                 _make_settings(), items=[_old_movie()])
        before = _fs(tracker, MOVIE, _old_movie())["rating"]
        tracker.start_new_cycle()
        settings = _make_settings()
        settings["rating_source"] = 3  # IMDb only, store not loaded: transient
        settings["imdb_ready"] = False
        run_scan(MOVIE, tracker, _make_source_config(), fields, "k",
                 settings, items=[_old_movie()])
        held = _fs(tracker, MOVIE, _old_movie())["rating"]
        tracker.start_new_cycle()
        settings["imdb_ready"] = True
        run_scan(MOVIE, tracker, _make_source_config(), fields, "k",
                 settings, items=[_old_movie()])
        after = _fs(tracker, MOVIE, _old_movie())["rating"]
        tracker.close()

        assert held == before
        assert after["settings_fp"] != before["settings_fp"]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_row_without_fingerprint_is_rechecked_once(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="Plot")
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch("movie", _MOVIE_IDENTITY, "plot", next_retry=None)
        tracker.commit()
        run_scan(MOVIE, tracker, _make_source_config(), frozenset({"plot"}), "k",
                 _make_settings(), items=[_old_movie(plot="Plot")])
        first = mock_fetch.called
        row = _plot_row(tracker)
        tracker.start_new_cycle()
        mock_fetch.reset_mock()
        run_scan(MOVIE, tracker, _make_source_config(), frozenset({"plot"}), "k",
                 _make_settings(), items=[_old_movie(plot="Plot")])
        tracker.close()

        assert first
        assert row["settings_fp"] is not None
        assert not mock_fetch.called

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_settings_change_restarts_young_field_schedule(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Re-baselined like Update now: the refetch schedule starts over
        instead of continuing at the settle date."""
        mock_fetch.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        for _ in range(3):
            tracker.record_field_fetch(
                "movie", _MOVIE_IDENTITY, "rating", next_retry="2099-01-01",
                settings_fp=_fp("rating"),
            )
        tracker.commit()
        settings = _make_settings()
        settings["rating_source"] = 2
        run_scan(MOVIE, tracker, _make_source_config(), frozenset({"rating"}), "k",
                 settings, items=[_young_movie()])
        row = tracker.get_field_status("movie", _MOVIE_IDENTITY, "rating")
        tracker.close()

        assert row["fetch_count"] == 1
        # First schedule step (7 days plus jitter), not the settle date.
        assert row["next_retry"] in (_today_plus(7), _today_plus(8))


class TestExtractIds:
    """Kodi uniqueid values that cannot be an id are dropped, so they are
    never stored in the tracker or sent to a provider."""

    def test_valid_ids_kept(self):
        item = {"uniqueid": {
            "tmdb": "123", "imdb": "tt0012345", "tvdb": "80767", "unknown": "42",
        }}
        assert _extract_ids(item) == ("123", "tt0012345", "80767", "42")

    @pytest.mark.parametrize("bogus", [
        "", "None", "0", "none", "abc", "12a", " ", "-1", "00",
        # Non-ASCII digits: str.isdigit accepts them, int() raises on "²"
        # and reads "١٢" as 12; neither is an id.
        "²", "١٢", "①",
        # Far longer than any provider id; int() on it raises ValueError
        # on Python 3.11+ (integer string conversion limit).
        "9" * 5000,
    ])
    def test_bogus_numeric_ids_dropped(self, bogus):
        item = {"uniqueid": {"tmdb": bogus, "tvdb": bogus, "unknown": bogus}}
        assert _extract_ids(item) == (None, None, None, None)

    @pytest.mark.parametrize("bogus", ["", "None", "0", "12345", "nm0000001", "tt", "ttabc", "tt١٢"])
    def test_bogus_imdb_ids_dropped(self, bogus):
        assert _extract_ids({"uniqueid": {"imdb": bogus}})[1] is None

    def test_alternate_keys_and_surrounding_space(self):
        item = {"uniqueid": {"themoviedb": " 77 ", "thetvdb": "88"}}
        assert _extract_ids(item) == ("77", None, "88", None)

    def test_bogus_primary_key_falls_back_to_alternate(self):
        item = {"uniqueid": {"tmdb": "None", "themoviedb": "77"}}
        assert _extract_ids(item)[0] == "77"

    def test_integer_ids_accepted(self):
        assert _extract_ids({"uniqueid": {"tmdb": 77}})[0] == "77"

    def test_no_uniqueid(self):
        assert _extract_ids({}) == (None, None, None, None)
        assert _extract_ids({"uniqueid": None}) == (None, None, None, None)

    @patch("resources.lib.engine.scan.get_all_items")
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_show_with_only_none_strings_is_skipped(
        self, mock_fetch, mock_get_all, tmp_path, kodi_stubs,
    ):
        show = {
            "tvshowid": 7, "title": "De 12 van Schouwendam", "premiered": "2019-01-01",
            "plot": "", "rating": 0, "votes": "0", "file": "smb://s/shows/x/",
            "uniqueid": {"imdb": "None", "tvdb": "None", "tvmaze": "1234"},
        }
        mock_get_all.return_value = [show]
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"plot"}),
            "fake_key", _make_settings(),
        )
        assert stats["skipped"] == 1
        assert tracker.get_show_tvdb_map() == {}
        assert not mock_fetch.called
        tracker.close()


class TestUnmappedLanguage:
    """A folder language TVDB has no id for is logged once per code per scan."""

    @patch("resources.lib.engine.scan.log")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    @patch("resources.lib.engine.scan.tmdb_fetch_season", return_value=[])
    def test_logged_once_per_code_per_scan(
        self, mock_season, mock_tvdb_eps, mock_update, mock_log, tmp_path, kodi_stubs,
    ):
        first = _make_episode_item(title="")
        first["uniqueid"] = {"tmdb": "1", "tvdb": "888"}
        second = _make_episode_item(title="", episode=2)
        second["episodeid"] = 101
        second["uniqueid"] = {"tmdb": "2", "tvdb": "889"}
        config = {
            "sources": [{"path": _SHOWS, "language": "dutch", "enabled": True}],
            "default_language": "en-US",
            "fallback_language": "dutch",
        }
        settings = _make_settings()
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        def warnings():
            return [
                c for c in mock_log.warning.call_args_list
                if c.kwargs.get("event") == "scan.language_unmapped"
            ]

        for _ in range(2):
            tracker.start_new_cycle()
            run_scan(
                EPISODE, tracker, config, frozenset({"title"}),
                "k", settings, items=[first, second], show_id_map={10: "55555"},
                reset=True,
            )
        tracker.close()

        mock_tvdb_eps.assert_not_called()
        logged = warnings()
        assert len(logged) == 2  # once in each scan
        assert all(c.kwargs["language"] == "dutch" for c in logged)


def _complete_kwargs(mock_log):
    calls = [
        c for c in mock_log.info.call_args_list
        if c.kwargs.get("event") == "scan.complete"
    ]
    assert len(calls) == 1
    return calls[0].kwargs


class TestPerItemOutcomeCounts:
    """Per-item id outcomes log at DEBUG and are counted in scan.complete."""

    @patch("resources.lib.engine.scan.log")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tvdb_fetch_series_episodes")
    def test_unknown_ids_accepted_counted(
        self, mock_eps, mock_update, mock_log, tmp_path, kodi_stubs
    ):
        items = [
            _episode(100, {"unknown": "340249"}, episode=1),
            _episode(101, {"unknown": "340250"}, episode=2),
        ]
        mock_eps.return_value = {
            "340249": {"title": "A", "plot": ""}, "340250": {"title": "B", "plot": ""},
        }
        tracker = _make_tracker(tmp_path)
        _register_checked_show(tracker)

        run_scan(
            EPISODE, tracker, _nl_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=items, show_id_map={},
        )
        tracker.close()

        assert _complete_kwargs(mock_log)["unknown_ids_accepted"] == 2
        assert not [
            c for c in mock_log.info.call_args_list
            if c.kwargs.get("event") == "scan.unknown_id_accepted"
        ]

    @patch("resources.lib.engine.scan.log")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_season")
    @patch("resources.lib.engine.scan.tmdb_find_episode")
    def test_episodes_found_by_tvdb_counted(
        self, mock_find, mock_season, mock_update, mock_log, tmp_path, kodi_stubs
    ):
        mock_find.return_value = {
            "tmdb_id": "99999", "show_tmdb_id": "55555", "season": 1, "episode": 1,
        }
        mock_season.return_value = [_tmdb_episode_data()]
        tracker = _make_tracker(tmp_path)
        _register_show(tracker)

        run_scan(
            EPISODE, tracker, _make_source_config(), frozenset({"title"}),
            "k", _make_settings(), items=[_episode(100, {"tvdb": "888"})],
            show_id_map={10: "55555"},
        )
        tracker.close()

        assert _complete_kwargs(mock_log)["episodes_found_by_tvdb"] == 1


class TestTvdbAuthStats:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_auth_failure_from_an_earlier_pass_is_reported(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        """The flag reflects the cycle's breaker, so a pass that never called
        TVDB itself still reports a rejection an earlier pass hit."""
        from resources.lib.engine.breaker import CircuitBreaker

        breaker = CircuitBreaker()
        breaker.disable("tvdb", auth=True)
        mock_tmdb.return_value = _tmdb_tvshow_response()
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"genre"}),
            "k", _make_settings(), items=[_make_tvshow_item()], breaker=breaker,
        )
        tracker.close()
        assert stats["tvdb_auth_failed"] is True

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    def test_tripped_tvdb_is_not_an_auth_failure(
        self, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        breaker = _tripped_breaker("tvdb")
        mock_tmdb.return_value = _tmdb_tvshow_response()
        tracker = _make_tracker(tmp_path)
        stats = run_scan(
            TVSHOW, tracker, _make_source_config(), frozenset({"genre"}),
            "k", _make_settings(), items=[_make_tvshow_item()], breaker=breaker,
        )
        tracker.close()
        assert stats["tvdb_auth_failed"] is False


class TestFallbackSettingChange:
    """A changed fallback setting reopens a value metamender filled from the
    old fallback, so the new setting can replace it; nothing else."""

    @staticmethod
    def _german_config():
        config = _nl_movie_source_config()
        config["fallback_language"] = "de-DE"
        return config

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_new_fallback_language_replaces_old_fallback_value(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang(
            {"en-US": "English plot", "de-DE": "Deutsche Handlung"},
        )
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == "Deutsche Handlung"
        assert row["from_fallback"] == 1

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_edited_fallback_value_is_not_reopened(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang(
            {"en-US": "English plot", "de-DE": "Deutsche Handlung"},
        )
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="My own plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") is None
        assert [c.args[2] for c in mock_fetch.call_args_list] == ["nl-NL"]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_native_value_is_not_reopened(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.engine.protect import fingerprint
        from resources.lib.engine.scan import settings_fingerprint

        mock_fetch.side_effect = _plot_by_lang({"de-DE": "Deutsche Handlung"})
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, "plot", next_retry="2020-01-01", source="tmdb",
            written_value=fingerprint("Nederlandse plot"), from_fallback=False,
            settings_fp=settings_fingerprint("plot", _make_settings(), "nl-NL", "movie", "en-US"),
        )
        tracker.commit()
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="Nederlandse plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") is None
        assert [c.args[2] for c in mock_fetch.call_args_list] == ["nl-NL"]

    @patch("resources.lib.engine.scan.nfo_state", return_value=NFO)
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_nfo_item_fallback_value_written_by_metamender_is_replaced(
        self, mock_fetch, mock_update, _mock_nfo, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"de-DE": "Deutsche Handlung"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") == "Deutsche Handlung"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_untrimmed_kodi_value_still_counts_as_written(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"de-DE": "Deutsche Handlung"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot=" English plot ")],
        )
        tracker.close()

        assert _written(mock_update, "plot") == "Deutsche Handlung"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_transient_fallback_step_keeps_value_and_old_settings(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        from resources.lib.utils import TransientProviderError

        def fetch(tid, key, language):
            if language == "de-DE":
                raise TransientProviderError("http 503")
            return _tmdb_movie_response(plot="")

        mock_fetch.side_effect = fetch
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        before = _plot_row(tracker)
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        after = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") is None
        # Not an answer: the field stays reopened for the next scan.
        assert after["settings_fp"] == before["settings_fp"]
        assert after["fetch_count"] == before["fetch_count"]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_no_answer_under_new_setting_clears_old_fallback_value(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        # The value was only there because of the old fallback language.
        from resources.lib.engine.protect import fingerprint
        from resources.lib.engine.scan import settings_fingerprint

        mock_fetch.side_effect = _plot_by_lang({})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, self._german_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == ""
        assert row["settings_fp"] == settings_fingerprint(
            "plot", _make_settings(), "nl-NL", "movie", "de-DE",
        )
        assert row["from_fallback"] == 0
        assert row["written_value"] == fingerprint("")

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_removed_fallback_clears_its_value(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        config = _nl_movie_source_config()
        del config["fallback_language"]
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, config, frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="English plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") == ""
        assert [c.args[2] for c in mock_fetch.call_args_list] == ["nl-NL"]

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_removed_fallback_keeps_an_edited_value(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        config = _nl_movie_source_config()
        del config["fallback_language"]
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot")
        run_scan(
            MOVIE, tracker, config, frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="My own plot")],
        )
        tracker.close()

        assert _written(mock_update, "plot") is None

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_source_fallback_overrides_the_default(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"de-DE": "Deutsche Handlung",
                                               "en-US": "English plot"})
        config = _nl_movie_source_config()
        config["sources"][0]["fallback_language"] = "de-DE"
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, config, frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="")],
        )
        tracker.close()

        assert _written(mock_update, "plot") == "Deutsche Handlung"

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_no_fallback_by_default(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        config = _nl_movie_source_config()
        del config["fallback_language"]
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, config, frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="")],
        )
        tracker.close()

        assert _written(mock_update, "plot") is None
        assert [c.args[2] for c in mock_fetch.call_args_list] == ["nl-NL"]


class TestFallbackRecheckCounting:
    """Only a monthly recheck (a sample that replaced a FINAL verdict) spends
    one of a fallback value's FALLBACK_RECHECKS; ordinary samples of young
    content on the refetch schedule do not."""

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_dense_schedule_does_not_spend_rechecks(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
        settings = _make_settings()
        settings["refetch_schedule"] = "1,1,1,1,1,1,1,1"
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=0, settings=settings)
        for _ in range(4):
            tracker._conn.execute("UPDATE field_status SET next_retry = '2020-01-01'")
            tracker.commit()
            tracker.start_new_cycle()
            run_scan(
                MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
                "k", settings, items=[_young_movie(plot="English plot")],
            )
        row = _plot_row(tracker)
        tracker.close()

        assert row["fetch_count"] == 5  # every scheduled sample was taken
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 0)

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_young_fallback_write_keeps_count(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """A young item whose fallback value changes is rewritten from the
        fallback again; that is a schedule sample, not a recheck."""
        mock_fetch.side_effect = _plot_by_lang({"en-US": "New English plot"})
        tracker = _make_tracker(tmp_path)
        _seed_fallback_plot(tracker, "English plot", checks=2)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_young_movie(plot="")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == "New English plot"
        assert (row["from_fallback"], row["fallback_checks"]) == (1, 2)


class TestSettingsChangeWrites:
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_settings_change_recheck_writes_differing_answer(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        """Old content is final, but a changed folder language re-checks it,
        and an answer that differs from Kodi's value is written."""
        mock_fetch.side_effect = _plot_by_lang({
            "nl-NL": "Nederlandse plot", "de-DE": "Deutsche Handlung",
        })
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, _nl_movie_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie()],
        )
        tracker.start_new_cycle()
        mock_update.reset_mock()
        german = {
            "sources": [{"path": "smb://server/movies/", "language": "de-DE", "enabled": True}],
            "default_language": "en-US",
        }
        run_scan(
            MOVIE, tracker, german, frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="Nederlandse plot")],
        )
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == "Deutsche Handlung"
        assert row["fetch_count"] == 1
        assert row["source"] == "tmdb"
        assert row["from_fallback"] == 0


def _buurman_show():
    # type: () -> Dict[str, Any]
    """Buurman en Buurman as the library holds it: an untyped TVDB id and a
    Kodi year (1976) TVDB does not share (1979)."""
    item = _nl_show()
    item["premiered"] = "1976-01-01"
    return item


def _kodi_episodes(unknown_ids):
    # type: (Any) -> Any
    return [
        _episode(200 + n, {"unknown": uid}, episode=n + 1)
        for n, uid in enumerate(unknown_ids)
    ]


def _tvdb_list(ids):
    # type: (Any) -> Dict[str, Dict[str, str]]
    return {i: {"title": "", "plot": ""} for i in ids}


_BUURMAN_SERIES = {
    "name": "Buurman en Buurman", "overview": "Twee buren klussen.",
    "names": _BUURMAN_NAMES, "year": 1979,
}


class TestShowIdConfirmedByEpisodes:
    """A show id whose names or year do not match the Kodi show is accepted
    when the show's own untyped episode ids are in that series' episode
    list."""

    def _scan(self, tmp_path, kodi_episodes, tvdb_episodes, item=None):
        tracker = _make_tracker(tmp_path)
        with patch("resources.lib.engine.scan.tvdb_fetch_series",
                   return_value=_BUURMAN_SERIES), \
                patch("resources.lib.engine.scan.tvdb_fetch_series_episodes",
                      **tvdb_episodes) as mock_eps, \
                patch("resources.lib.engine.scan.get_all_items",
                      **kodi_episodes) as mock_kodi, \
                patch("resources.lib.engine.scan.update_item", return_value=True), \
                patch("resources.lib.engine.scan.log") as mock_log:
            stats = run_scan(
                TVSHOW, tracker, _nl_source_config(), frozenset({"plot"}),
                "k", _make_settings(), items=[item or _buurman_show()],
            )
        row = _row(tracker, TVSHOW, item or _buurman_show())
        fs_map = _fs(tracker, TVSHOW, item or _buurman_show())
        tracker.close()
        return stats, row, fs_map, mock_eps, mock_kodi, mock_log

    @staticmethod
    def _reasons(mock_log, event):
        return [
            c.kwargs.get("reason")
            for level in (mock_log.info, mock_log.debug)
            for c in level.call_args_list
            if c.kwargs.get("event") == event
        ]

    def test_accepted_when_episode_ids_confirm(self, tmp_path, kodi_stubs):
        stats, row, fs_map, mock_eps, mock_kodi, mock_log = self._scan(
            tmp_path,
            {"return_value": _kodi_episodes(["1", "2", "3"])},
            {"return_value": _tvdb_list(["1", "2", "3", "4"])},
        )

        assert row["tvdb_id"] == "80767"
        assert mock_kodi.call_args.kwargs == {"tvshowid": 10}
        assert mock_kodi.call_args.args[0] is EPISODE
        assert mock_eps.call_args.args == ("80767", "nld")
        assert self._reasons(mock_log, "scan.unknown_id_accepted") == ["accepted_by_episodes"]
        assert fs_map["plot"]["source"] == "tvdb"

    def test_rejected_when_only_one_episode_confirms(self, tmp_path, kodi_stubs):
        _stats, row, _fs_map, _eps, _kodi, mock_log = self._scan(
            tmp_path,
            {"return_value": _kodi_episodes(["1"])},
            {"return_value": _tvdb_list(["1", "2"])},
        )

        assert row["tvdb_id"] is None
        assert self._reasons(mock_log, "scan.unknown_id_rejected") == ["year_mismatch"]

    def test_rejected_without_a_majority(self, tmp_path, kodi_stubs):
        _stats, row, _fs_map, _eps, _kodi, _log = self._scan(
            tmp_path,
            {"return_value": _kodi_episodes(["1", "2", "8", "9"])},
            {"return_value": _tvdb_list(["1", "2", "3"])},
        )

        assert row["tvdb_id"] is None

    def test_rejected_when_show_has_no_unknown_episode_ids(self, tmp_path, kodi_stubs):
        _stats, row, _fs_map, mock_eps, _kodi, mock_log = self._scan(
            tmp_path,
            {"return_value": [_episode(200, {"tvdb": "1"})]},
            {"return_value": _tvdb_list(["1"])},
        )

        assert row["tvdb_id"] is None
        assert row["ids_checked"] == date.today().isoformat()
        mock_eps.assert_not_called()
        assert self._reasons(mock_log, "scan.unknown_id_rejected") == ["year_mismatch"]

    def test_transient_episode_list_is_not_a_rejection(self, tmp_path, kodi_stubs):
        from resources.lib.utils import TransientProviderError

        _stats, row, fs_map, _eps, _kodi, mock_log = self._scan(
            tmp_path,
            {"return_value": _kodi_episodes(["1", "2"])},
            {"side_effect": TransientProviderError("503")},
        )

        assert row["tvdb_id"] is None
        assert row["ids_checked"] is None  # retried next scan
        assert _count(fs_map, "plot") == 0
        assert self._reasons(mock_log, "scan.unknown_id_rejected") == []

    def test_kodi_query_failure_is_transient(self, tmp_path, kodi_stubs):
        from resources.lib.utils import KodiQueryError

        _stats, row, _fs_map, mock_eps, _kodi, mock_log = self._scan(
            tmp_path,
            {"side_effect": KodiQueryError("rpc")},
            {"return_value": _tvdb_list(["1", "2"])},
        )

        assert row["tvdb_id"] is None
        assert row["ids_checked"] is None
        mock_eps.assert_not_called()
        assert self._reasons(mock_log, "scan.unknown_id_rejected") == []

    def test_name_mismatch_confirmed_by_episodes(self, tmp_path, kodi_stubs):
        # TheTVDB names the show "Buurman en Buurman"; Kodi has a title none
        # of its names match. The show's own episode ids settle it.
        item = _nl_show(title="Buurman & Buurman de klusjesmannen")
        _stats, row, _fs_map, mock_eps, mock_kodi, mock_log = self._scan(
            tmp_path,
            {"return_value": _kodi_episodes(["1", "2", "3"])},
            {"return_value": _tvdb_list(["1", "2", "3"])},
            item=item,
        )

        assert row["tvdb_id"] == "80767"
        assert mock_kodi.call_args.kwargs == {"tvshowid": 10}
        assert self._reasons(mock_log, "scan.unknown_id_accepted") == ["accepted_by_episodes"]

    def test_name_mismatch_rejected_without_episode_evidence(self, tmp_path, kodi_stubs):
        item = _nl_show(title="Alfred Jodocus Kwak")
        item["premiered"] = "1976-01-01"
        _stats, row, _fs_map, _eps, _kodi, mock_log = self._scan(
            tmp_path,
            {"return_value": _kodi_episodes(["1"])},
            {"return_value": _tvdb_list(["1", "2", "3"])},
            item=item,
        )

        assert row["tvdb_id"] is None
        assert self._reasons(mock_log, "scan.unknown_id_rejected") == ["name_mismatch"]

    def test_matching_year_needs_no_episode_fetch(self, tmp_path, kodi_stubs):
        item = _nl_show()
        item["premiered"] = "1979-03-01"
        _stats, row, _fs_map, _eps, mock_kodi, _log = self._scan(
            tmp_path, {"return_value": []}, {"return_value": None}, item=item,
        )

        assert row["tvdb_id"] == "80767"
        mock_kodi.assert_not_called()


class TestIdentitySurvivesIdWriteBack:
    """A show keyed on "title (year)" gets an IMDb id written back to Kodi;
    the next scan computes imdb:<id> and must keep the same tracker row, or
    its clocks restart and its provenance is orphaned."""

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow_ids", return_value={})
    @patch("resources.lib.engine.scan.tmdb_fetch_tvshow")
    @patch("resources.lib.engine.scan.resolve_id", return_value="99999")
    def test_second_scan_keeps_the_tracker_row(
        self, mock_resolve, mock_tmdb, mock_ids, mock_update, tmp_path, kodi_stubs
    ):
        item = _make_tvshow_item(title="De Snorkels")
        item["uniqueid"] = {"tvdb": "777"}
        item["premiered"] = "1984-09-15"
        mock_tmdb.return_value = _tmdb_tvshow_response(imdb_id="tt0086798")
        tracker = _make_tracker(tmp_path)
        fields = frozenset({"plot"})
        assert compute_identity(item, TVSHOW) == "De Snorkels (1984)"

        run_scan(TVSHOW, tracker, _make_source_config(), fields, "k",
                 _make_settings(), items=[item])
        written = [c.kwargs["uniqueid"] for c in mock_update.call_args_list
                   if "uniqueid" in c.kwargs]
        assert written == [{"tvdb": "777", "imdb": "tt0086798"}]

        item["uniqueid"] = dict(written[0])
        item["plot"] = "Great show"  # what Kodi now holds
        tracker.begin_cycle(resume_interrupted=False)
        run_scan(TVSHOW, tracker, _make_source_config(), fields, "k",
                 _make_settings(), items=[item])

        rows = tracker._conn.execute(
            "SELECT identity FROM items WHERE media_type = 'tvshow'"
        ).fetchall()
        fs = tracker.get_field_status("tvshow", "imdb:tt0086798", "plot")
        tracker.close()
        assert [r[0] for r in rows] == ["imdb:tt0086798"]
        assert fs is not None and fs["fetch_count"] == 1

class TestNewOnlyScan:
    """The library-event scan processes the items the tracker has not seen
    under their current Kodi id. Kodi's dateadded is the file's date, so a
    file older than the last scan is still new to the library."""

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.get_item_by_id")
    @patch("resources.lib.engine.scan.get_all_items")
    def test_only_unknown_kodi_ids_are_processed(
        self, mock_all, mock_by_id, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        known = _make_movie_item(movie_id=1, tmdb_id="11")
        new = _make_movie_item(movie_id=2, tmdb_id="22")
        new["dateadded"] = "2001-01-01 00:00:00"
        mock_all.return_value = [{"movieid": 1}, {"movieid": 2}, {"movieid": 3}]
        mock_by_id.side_effect = lambda mt, i: {2: new}.get(i)
        mock_tmdb.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        tracker.upsert_item("movie", compute_identity(known, MOVIE), kodi_id=1)
        tracker.commit()

        stats = run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}), "k",
            _make_settings(), new_only=True,
        )
        tracker.close()

        assert mock_all.call_args.kwargs == {"ids_only": True}
        assert sorted(c.args[1] for c in mock_by_id.call_args_list) == [2, 3]
        assert stats["processed"] == 1
        assert [c.args[0] for c in mock_tmdb.call_args_list] == ["22"]


class TestDuplicateIdentities:
    """Two Kodi items that compute the same identity (two files of one movie,
    each with tmdb 170) need their own tracker rows: with one shared row the
    second is skipped as already processed in this cycle."""

    @staticmethod
    def _pair():
        a = _make_movie_item(title="A Monster in Paris", tmdb_id="170", movie_id=5)
        a["file"] = "smb://server/movies/nl/monster.mkv"
        b = _make_movie_item(title="A Monster in Paris", tmdb_id="170", movie_id=9)
        b["file"] = "smb://server/movies/en/monster.mkv"
        return a, b

    def _scan(self, tracker, items, **kw):
        return run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}), "k",
            _make_settings(), items=items, **kw,
        )

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.get_item_by_id")
    def test_both_items_processed_and_stable(
        self, mock_by_id, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        a, b = self._pair()
        mock_by_id.side_effect = lambda mt, i: {5: a, 9: b}.get(i)
        mock_tmdb.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)

        stats = self._scan(tracker, [a, b])
        assert stats["processed"] == 2
        assert sorted(c.args[0] for c in mock_update.call_args_list) == [5, 9]

        tracker.begin_cycle(resume_interrupted=False)
        self._scan(tracker, [b, a])
        rows = dict(tracker._conn.execute(
            "SELECT identity, kodi_id FROM items"
        ).fetchall())
        tracker.close()
        assert rows == {
            "tmdb:170": 5,
            "tmdb:170|smb://server/movies/en/monster.mkv": 9,
        }

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.get_item_by_id")
    def test_single_item_update_keeps_the_duplicate_row(
        self, mock_by_id, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        a, b = self._pair()
        mock_by_id.side_effect = lambda mt, i: {5: a, 9: b}.get(i)
        mock_tmdb.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        self._scan(tracker, [a, b])

        self._scan(tracker, [b], reset=True)
        rows = dict(tracker._conn.execute(
            "SELECT identity, kodi_id FROM items"
        ).fetchall())
        tracker.close()
        assert rows["tmdb:170"] == 5
        assert rows["tmdb:170|smb://server/movies/en/monster.mkv"] == 9

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.get_item_by_id")
    def test_replaced_file_takes_over_the_row(
        self, mock_by_id, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        # The old file is gone (its Kodi id no longer exists): the new item
        # with the same identity keeps the row and its clocks.
        a, _b = self._pair()
        mock_tmdb.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        mock_by_id.side_effect = lambda mt, i: {5: a}.get(i)
        self._scan(tracker, [a])

        upgraded = _make_movie_item(title="A Monster in Paris", tmdb_id="170", movie_id=12)
        mock_by_id.side_effect = lambda mt, i: {12: upgraded}.get(i)
        tracker.begin_cycle(resume_interrupted=False)
        self._scan(tracker, [upgraded])
        rows = dict(tracker._conn.execute(
            "SELECT identity, kodi_id FROM items"
        ).fetchall())
        tracker.close()
        assert rows == {"tmdb:170": 12}

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    @patch("resources.lib.engine.scan.get_item_by_id")
    def test_rebuilt_library_keeps_rows(
        self, mock_by_id, mock_tmdb, mock_update, tmp_path, kodi_stubs
    ):
        # After a library rebuild the old Kodi id belongs to another movie.
        a, _b = self._pair()
        other = _make_movie_item(title="Other", tmdb_id="999", movie_id=5)
        mock_tmdb.return_value = _tmdb_movie_response()
        tracker = _make_tracker(tmp_path)
        mock_by_id.side_effect = lambda mt, i: {5: a}.get(i)
        self._scan(tracker, [a])

        a["movieid"] = 40
        mock_by_id.side_effect = lambda mt, i: {5: other, 40: a}.get(i)
        tracker.begin_cycle(resume_interrupted=False)
        self._scan(tracker, [other, a])
        rows = dict(tracker._conn.execute(
            "SELECT identity, kodi_id FROM items"
        ).fetchall())
        tracker.close()
        assert rows == {"tmdb:170": 40, "tmdb:999": 5}


class TestUnconfiguredLanguage:
    """A folder no sources.json entry and no default_language configure has
    no known language: its text is fetched in Kodi's UI language and only
    fills empty fields, so a first scan cannot replace a library's text with
    another language's."""

    @staticmethod
    def _item():
        item = _make_movie_item(plot="Een Nederlandse plot")
        item["genre"] = ["Avontuur"]
        item["runtime"] = 5400
        return item

    @patch("resources.lib.engine.scan.kodi_ui_language", return_value="nl-NL")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_text_fills_only_in_ui_language(
        self, mock_tmdb, mock_update, _ui, tmp_path, kodi_stubs
    ):
        mock_tmdb.return_value = _tmdb_movie_response(
            plot="An English plot", genres=["Adventure"], runtime=100,
        )
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, {"sources": []}, frozenset({"plot", "tagline", "genre", "runtime"}),
            "k", _make_settings(), items=[self._item()],
        )
        tracker.close()

        assert mock_tmdb.call_args.args[2] == "nl-NL"
        written = mock_update.call_args.kwargs
        assert "plot" not in written
        assert "genre" not in written
        assert written["tagline"] == "Wow"
        assert written["runtime"] == 6000

    @patch("resources.lib.engine.scan.kodi_ui_language", return_value="nl-NL")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_configured_folder_still_replaces_text(
        self, mock_tmdb, mock_update, _ui, tmp_path, kodi_stubs
    ):
        mock_tmdb.return_value = _tmdb_movie_response(plot="An English plot")
        config = {"sources": [
            {"path": "smb://server/movies/", "enabled": True, "language": "en-US"},
        ]}
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, config, frozenset({"plot"}),
            "k", _make_settings(), items=[self._item()],
        )
        tracker.close()

        assert mock_tmdb.call_args.args[2] == "en-US"
        assert mock_update.call_args.kwargs["plot"] == "An English plot"

    @patch("resources.lib.engine.scan.kodi_ui_language", return_value="nl-NL")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_explicit_default_language_counts_as_configured(
        self, mock_tmdb, mock_update, _ui, tmp_path, kodi_stubs
    ):
        mock_tmdb.return_value = _tmdb_movie_response(plot="An English plot")
        tracker = _make_tracker(tmp_path)
        run_scan(
            MOVIE, tracker, {"sources": [], "default_language": "en-US"},
            frozenset({"plot"}), "k", _make_settings(), items=[self._item()],
        )
        tracker.close()

        assert mock_tmdb.call_args.args[2] == "en-US"
        assert mock_update.call_args.kwargs["plot"] == "An English plot"

    @patch("resources.lib.engine.scan.kodi_ui_language", return_value="nl-NL")
    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_own_earlier_fill_is_refreshed(
        self, mock_tmdb, mock_update, _ui, tmp_path, kodi_stubs
    ):
        # A value metamender itself filled may still be refreshed.
        from resources.lib.engine.protect import fingerprint

        item = self._item()
        item["plot"] = "Oude plot van metamender"
        tracker = _make_tracker(tmp_path)
        identity = compute_identity(item, MOVIE)
        tracker.upsert_item("movie", identity, kodi_id=1)
        tracker.record_field_fetch(
            "movie", identity, "plot", next_retry="2000-01-01", source="tmdb",
            written_value=fingerprint("Oude plot van metamender"),
        )
        tracker.commit()
        mock_tmdb.return_value = _tmdb_movie_response(plot="Nieuwe plot")
        run_scan(
            MOVIE, tracker, {"sources": []}, frozenset({"plot"}),
            "k", _make_settings(), items=[item],
        )
        tracker.close()

        assert mock_update.call_args.kwargs["plot"] == "Nieuwe plot"


@patch("resources.lib.engine.scan.update_item", return_value=True)
@patch("resources.lib.engine.scan.tmdb_fetch_movie")
def test_empty_source_fallback_turns_off_the_default(
    mock_fetch, mock_update, tmp_path, kodi_stubs
):
    mock_fetch.side_effect = _plot_by_lang({"en-US": "English plot"})
    config = _nl_movie_source_config()
    config["sources"][0]["fallback_language"] = ""
    tracker = _make_tracker(tmp_path)
    run_scan(
        MOVIE, tracker, config, frozenset({"plot"}),
        "k", _make_settings(), items=[_old_movie(plot="")],
    )
    tracker.close()

    assert _written(mock_update, "plot") is None
    assert [c.args[2] for c in mock_fetch.call_args_list] == ["nl-NL"]


class TestRefillBlankedFinal:
    """A FINAL field Kodi now holds blank, although metamender wrote a value
    there (a Kodi refresh or rescrape wiped it), gets one fetch and is FINAL
    again. A field that was always blank stays final."""

    @staticmethod
    def _seed(tracker, written, field="plot"):
        from resources.lib.engine.protect import fingerprint

        tracker.upsert_item("movie", _MOVIE_IDENTITY, kodi_id=1, tmdb_id="12345")
        tracker.record_field_fetch(
            "movie", _MOVIE_IDENTITY, field, next_retry="2020-01-01", source="tmdb",
            written_value=None if written is None else fingerprint(written),
            settings_fp=_fp(field),
        )
        tracker.commit()

    def _scan(self, tracker):
        return run_scan(
            MOVIE, tracker, _make_source_config(), frozenset({"plot"}),
            "k", _make_settings(), items=[_old_movie(plot="")],
        )

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_blanked_value_is_refilled_once(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="The plot")
        tracker = _make_tracker(tmp_path)
        self._seed(tracker, "The plot")
        self._scan(tracker)
        row = _plot_row(tracker)
        tracker.close()

        assert _written(mock_update, "plot") == "The plot"
        assert row["fetch_count"] == 2

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_no_answer_does_not_loop(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        mock_fetch.return_value = _tmdb_movie_response(plot="")
        tracker = _make_tracker(tmp_path)
        self._seed(tracker, "The plot")
        self._scan(tracker)
        tracker.begin_cycle(resume_interrupted=False)
        self._scan(tracker)
        row = _plot_row(tracker)
        tracker.close()

        assert mock_fetch.call_count == 1
        assert row["fetch_count"] == 2

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_always_blank_field_stays_final(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        tracker = _make_tracker(tmp_path)
        self._seed(tracker, None)
        self._scan(tracker)
        tracker.close()

        mock_fetch.assert_not_called()

    @patch("resources.lib.engine.scan.update_item", return_value=True)
    @patch("resources.lib.engine.scan.tmdb_fetch_movie")
    def test_value_metamender_cleared_stays_final(
        self, mock_fetch, mock_update, tmp_path, kodi_stubs
    ):
        # A value metamender cleared itself (a removed fallback) is blank by
        # design, not wiped.
        tracker = _make_tracker(tmp_path)
        self._seed(tracker, "")
        self._scan(tracker)
        tracker.close()

        mock_fetch.assert_not_called()

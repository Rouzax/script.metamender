"""Tests for gap detection."""
from __future__ import annotations

from resources.lib.data.media_types import EPISODE, MOVIE
from resources.lib.engine.gaps import detect_gaps, is_field_empty, is_placeholder_title


class TestIsFieldEmpty:
    def test_none_is_empty(self):
        assert is_field_empty("plot", None) is True

    def test_empty_string_is_empty(self):
        assert is_field_empty("plot", "") is True

    def test_zero_rating_is_empty(self):
        assert is_field_empty("rating", 0) is True
        assert is_field_empty("rating", 0.0) is True

    def test_nonzero_rating_is_not_empty(self):
        assert is_field_empty("rating", 7.5) is False

    def test_empty_list_is_empty(self):
        assert is_field_empty("director", []) is True

    def test_nonempty_list_is_not_empty(self):
        assert is_field_empty("director", ["Nolan"]) is False

    def test_nonempty_string_is_not_empty(self):
        assert is_field_empty("plot", "A great movie.") is False

    def test_zero_runtime_is_empty(self):
        assert is_field_empty("runtime", 0) is True

    def test_nonzero_runtime_is_not_empty(self):
        assert is_field_empty("runtime", 120) is False


class TestIsPlaceholderTitle:
    def test_episode_n(self):
        assert is_placeholder_title("Episode 1") is True
        assert is_placeholder_title("Episode 23") is True
        assert is_placeholder_title("episode 5") is True

    def test_episode_no_number(self):
        assert is_placeholder_title("Episode") is True

    def test_aflevering(self):
        assert is_placeholder_title("Aflevering 3") is True
        assert is_placeholder_title("aflevering 10") is True

    def test_folge(self):
        assert is_placeholder_title("Folge 7") is True

    def test_bare_number(self):
        assert is_placeholder_title("1") is True
        assert is_placeholder_title("23") is True

    def test_empty_string(self):
        assert is_placeholder_title("") is True

    def test_real_title(self):
        assert is_placeholder_title("The Pilot") is False
        assert is_placeholder_title("Call Tracking") is False

    def test_episode_in_real_title(self):
        assert is_placeholder_title("The Episode That Changed Everything") is False

    def test_peaky_blinders_style(self):
        # Some shows genuinely name episodes "Episode 1"
        # but that IS a placeholder pattern; the engine checks TMDB
        # to see if it has a different title before overwriting
        assert is_placeholder_title("Episode 1") is True

    def test_placeholder_danish_afsnit(self):
        assert is_placeholder_title("Afsnit 4") is True
        assert is_placeholder_title("Afsnit") is True

    def test_placeholder_swedish_avsnitt(self):
        assert is_placeholder_title("Avsnitt 2") is True

    def test_placeholder_french_episode_accented(self):
        assert is_placeholder_title("Épisode 6") is True
        assert is_placeholder_title("Episode 6") is True

    def test_placeholder_real_title_not_matched(self):
        assert is_placeholder_title("Afsnittet om kärlek") is False
        assert is_placeholder_title("The Pilot") is False


class TestDetectGaps:
    def test_empty_rating_is_gap(self):
        item = {"rating": 0, "plot": "A plot", "tagline": "Tag", "mpaa": "PG"}
        gaps = detect_gaps(item, frozenset({"rating", "plot", "tagline"}), MOVIE)
        assert "rating" in gaps
        assert "plot" not in gaps

    def test_empty_plot_is_gap(self):
        item = {"rating": 7.5, "plot": "", "mpaa": "PG"}
        gaps = detect_gaps(item, frozenset({"rating", "plot"}), MOVIE)
        assert "plot" in gaps
        assert "rating" not in gaps

    def test_disabled_field_not_reported(self):
        item = {"rating": 0, "plot": ""}
        gaps = detect_gaps(item, frozenset({"rating"}), MOVIE)
        assert "rating" in gaps
        assert "plot" not in gaps

    def test_field_not_in_media_type_excluded(self):
        item = {"rating": 0, "tagline": ""}
        # TVSHOW doesn't have tagline in backfill_fields
        gaps = detect_gaps(item, frozenset({"rating", "tagline"}), MOVIE)
        assert "tagline" in gaps
        # But for TVSHOW:
        from resources.lib.data.media_types import TVSHOW
        gaps2 = detect_gaps(item, frozenset({"rating", "tagline"}), TVSHOW)
        assert "tagline" not in gaps2

    def test_placeholder_title_is_gap_for_episodes(self):
        item = {"title": "Episode 1", "rating": 8.0, "plot": "A plot"}
        gaps = detect_gaps(item, frozenset({"title", "rating", "plot"}), EPISODE)
        assert "title" in gaps

    def test_real_title_not_gap_for_episodes(self):
        item = {"title": "Call Tracking", "rating": 8.0, "plot": "A plot"}
        gaps = detect_gaps(item, frozenset({"title", "rating", "plot"}), EPISODE)
        assert "title" not in gaps

    def test_title_not_checked_for_movies(self):
        # Movies don't have "title" in backfill_fields
        item = {"title": "", "rating": 0}
        gaps = detect_gaps(item, frozenset({"title", "rating"}), MOVIE)
        assert "title" not in gaps

    def test_all_filled_returns_empty(self):
        item = {"rating": 7.5, "plot": "Good movie", "tagline": "Tag", "mpaa": "PG-13"}
        gaps = detect_gaps(item, frozenset({"rating", "plot", "tagline"}), MOVIE)
        assert gaps == []

    def test_multiple_gaps(self):
        item = {"rating": 0, "plot": "", "tagline": "", "mpaa": ""}
        gaps = detect_gaps(item, frozenset({"rating", "plot", "tagline", "mpaa"}), MOVIE)
        assert set(gaps) == {"rating", "plot", "tagline", "mpaa"}

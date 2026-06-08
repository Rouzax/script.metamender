"""Tests for gap detection."""
from __future__ import annotations

from resources.lib.engine.gaps import is_field_empty, is_placeholder_title


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

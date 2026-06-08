"""Tests for the source priority resolution module."""
from __future__ import annotations

from resources.lib.engine.resolve import (
    IMDB_ONLY,
    PREFER_IMDB,
    PREFER_TMDB,
    TMDB_ONLY,
    resolve_field,
    resolve_metadata,
)


# =============================================================================
# Tests: resolve_field
# =============================================================================


class TestResolveField:
    def test_prefer_tmdb_uses_tmdb(self) -> None:
        value, source = resolve_field("rating", 7.5, 8.0, PREFER_TMDB)
        assert value == 7.5
        assert source == "tmdb"

    def test_prefer_tmdb_falls_back_to_imdb(self) -> None:
        value, source = resolve_field("rating", 0.0, 8.0, PREFER_TMDB)
        assert value == 8.0
        assert source == "imdb"

    def test_prefer_tmdb_both_empty(self) -> None:
        value, source = resolve_field("rating", 0.0, 0.0, PREFER_TMDB)
        assert value is None
        assert source == ""

    def test_prefer_imdb_uses_imdb(self) -> None:
        value, source = resolve_field("rating", 7.5, 8.0, PREFER_IMDB)
        assert value == 8.0
        assert source == "imdb"

    def test_prefer_imdb_falls_back_to_tmdb(self) -> None:
        value, source = resolve_field("rating", 7.5, 0.0, PREFER_IMDB)
        assert value == 7.5
        assert source == "tmdb"

    def test_prefer_imdb_both_empty(self) -> None:
        value, source = resolve_field("plot", "", "", PREFER_IMDB)
        assert value is None
        assert source == ""

    def test_tmdb_only_uses_tmdb(self) -> None:
        value, source = resolve_field("plot", "TMDB plot", "IMDb plot", TMDB_ONLY)
        assert value == "TMDB plot"
        assert source == "tmdb"

    def test_tmdb_only_ignores_imdb(self) -> None:
        value, source = resolve_field("plot", "", "IMDb plot", TMDB_ONLY)
        assert value is None
        assert source == ""

    def test_imdb_only_uses_imdb(self) -> None:
        value, source = resolve_field("plot", "TMDB plot", "IMDb plot", IMDB_ONLY)
        assert value == "IMDb plot"
        assert source == "imdb"

    def test_imdb_only_ignores_tmdb(self) -> None:
        value, source = resolve_field("plot", "TMDB plot", "", IMDB_ONLY)
        assert value is None
        assert source == ""

    def test_empty_string_is_empty(self) -> None:
        value, source = resolve_field("plot", "", "fallback", PREFER_TMDB)
        assert value == "fallback"
        assert source == "imdb"

    def test_none_is_empty(self) -> None:
        value, source = resolve_field("plot", None, "fallback", PREFER_TMDB)
        assert value == "fallback"
        assert source == "imdb"

    def test_zero_int_is_empty(self) -> None:
        value, source = resolve_field("votes", 0, 500, PREFER_TMDB)
        assert value == 500
        assert source == "imdb"

    def test_zero_float_is_empty(self) -> None:
        value, source = resolve_field("rating", 0.0, 7.5, PREFER_TMDB)
        assert value == 7.5
        assert source == "imdb"

    def test_empty_list_is_empty(self) -> None:
        value, source = resolve_field("directors", [], ["Someone"], PREFER_TMDB)
        assert value == ["Someone"]
        assert source == "imdb"

    def test_non_empty_list_is_not_empty(self) -> None:
        value, source = resolve_field("genres", ["Action"], ["Drama"], PREFER_TMDB)
        assert value == ["Action"]
        assert source == "tmdb"


# =============================================================================
# Tests: resolve_metadata
# =============================================================================


class TestResolveMetadata:
    def test_rating_pref_applied_to_rating_and_votes(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100, "plot": "TMDB plot"}
        imdb = {"rating": 8.0, "votes": 5000, "plot": "IMDb plot"}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating", "votes", "plot"],
            rating_pref=PREFER_IMDB, plot_pref=PREFER_TMDB,
        )

        assert merged["rating"] == 8.0
        assert sources["rating"] == "imdb"
        assert merged["votes"] == 5000
        assert sources["votes"] == "imdb"

    def test_plot_pref_applied_independently(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100, "plot": "TMDB plot"}
        imdb = {"rating": 8.0, "votes": 5000, "plot": "IMDb plot"}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating", "votes", "plot"],
            rating_pref=PREFER_IMDB, plot_pref=PREFER_TMDB,
        )

        assert merged["plot"] == "TMDB plot"
        assert sources["plot"] == "tmdb"

    def test_other_fields_prefer_tmdb(self) -> None:
        tmdb = {"directors": ["TMDB Dir"], "genres": ["TMDB Genre"]}
        imdb = {"directors": ["IMDb Dir"], "genres": ["IMDb Genre"]}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["directors", "genres"],
            rating_pref=PREFER_IMDB, plot_pref=PREFER_IMDB,
        )

        assert merged["directors"] == ["TMDB Dir"]
        assert sources["directors"] == "tmdb"
        assert merged["genres"] == ["TMDB Genre"]
        assert sources["genres"] == "tmdb"

    def test_other_fields_fall_back_to_imdb(self) -> None:
        tmdb = {"directors": []}
        imdb = {"directors": ["IMDb Dir"]}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["directors"],
            rating_pref=PREFER_TMDB, plot_pref=PREFER_TMDB,
        )

        assert merged["directors"] == ["IMDb Dir"]
        assert sources["directors"] == "imdb"

    def test_handles_none_tmdb_data(self) -> None:
        imdb = {"rating": 8.0, "votes": 5000, "plot": "IMDb plot"}

        merged, sources = resolve_metadata(
            None, imdb, ["rating", "votes", "plot"],
            rating_pref=PREFER_TMDB, plot_pref=PREFER_TMDB,
        )

        assert merged["rating"] == 8.0
        assert sources["rating"] == "imdb"

    def test_handles_none_imdb_data(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100}

        merged, sources = resolve_metadata(
            tmdb, None, ["rating", "votes"],
            rating_pref=PREFER_IMDB, plot_pref=PREFER_TMDB,
        )

        assert merged["rating"] == 7.0
        assert sources["rating"] == "tmdb"

    def test_handles_both_none(self) -> None:
        merged, sources = resolve_metadata(
            None, None, ["rating", "plot"],
            rating_pref=PREFER_TMDB, plot_pref=PREFER_TMDB,
        )

        assert merged == {}
        assert sources == {}

    def test_only_includes_gap_fields(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100, "plot": "Plot", "tagline": "Tag"}
        imdb = {"rating": 8.0, "votes": 5000}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating"],
            rating_pref=PREFER_TMDB, plot_pref=PREFER_TMDB,
        )

        assert "rating" in merged
        assert "votes" not in merged
        assert "plot" not in merged
        assert "tagline" not in merged

    def test_tmdb_only_mode(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100}
        imdb = {"rating": 8.0, "votes": 5000}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating", "votes"],
            rating_pref=TMDB_ONLY, plot_pref=TMDB_ONLY,
        )

        assert merged["rating"] == 7.0
        assert sources["rating"] == "tmdb"

    def test_imdb_only_mode(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100}
        imdb = {"rating": 8.0, "votes": 5000}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating", "votes"],
            rating_pref=IMDB_ONLY, plot_pref=IMDB_ONLY,
        )

        assert merged["rating"] == 8.0
        assert sources["rating"] == "imdb"

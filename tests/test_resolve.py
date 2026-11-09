"""Tests for the source priority resolution module."""
from __future__ import annotations

from resources.lib.engine.protect import fingerprint
from resources.lib.engine.resolve import (
    IMDB_ONLY,
    PREFER_IMDB,
    PREFER_TMDB,
    TMDB_ONLY,
    build_ratings,
    resolve_field,
    resolve_metadata,
    slot_fingerprint,
    wants_imdb,
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
    def test_default_pref_applied_to_rating_and_votes(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100, "plot": "TMDB plot"}
        imdb = {"rating": 8.0, "votes": 5000, "plot": "IMDb plot"}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating", "votes", "plot"],
            plot_pref=PREFER_TMDB, default_pref=PREFER_IMDB,
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
            plot_pref=PREFER_TMDB, default_pref=PREFER_IMDB,
        )

        assert merged["plot"] == "TMDB plot"
        assert sources["plot"] == "tmdb"

    def test_other_fields_use_default_pref(self) -> None:
        tmdb = {"directors": ["TMDB Dir"], "genres": ["TMDB Genre"]}
        imdb = {"directors": ["IMDb Dir"], "genres": ["IMDb Genre"]}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["directors", "genres"],
            plot_pref=PREFER_IMDB, default_pref=PREFER_TMDB,
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
            plot_pref=PREFER_TMDB, default_pref=PREFER_TMDB,
        )

        assert merged["directors"] == ["IMDb Dir"]
        assert sources["directors"] == "imdb"

    def test_handles_none_tmdb_data(self) -> None:
        imdb = {"rating": 8.0, "votes": 5000, "plot": "IMDb plot"}

        merged, sources = resolve_metadata(
            None, imdb, ["rating", "votes", "plot"],
            plot_pref=PREFER_TMDB, default_pref=PREFER_TMDB,
        )

        assert merged["rating"] == 8.0
        assert sources["rating"] == "imdb"

    def test_handles_none_imdb_data(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100}

        merged, sources = resolve_metadata(
            tmdb, None, ["rating", "votes"],
            plot_pref=PREFER_TMDB, default_pref=PREFER_IMDB,
        )

        assert merged["rating"] == 7.0
        assert sources["rating"] == "tmdb"

    def test_handles_both_none(self) -> None:
        merged, sources = resolve_metadata(
            None, None, ["rating", "plot"],
            plot_pref=PREFER_TMDB, default_pref=PREFER_TMDB,
        )

        assert merged == {}
        assert sources == {}

    def test_only_includes_gap_fields(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100, "plot": "Plot", "tagline": "Tag"}
        imdb = {"rating": 8.0, "votes": 5000}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating"],
            plot_pref=PREFER_TMDB, default_pref=PREFER_TMDB,
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
            plot_pref=TMDB_ONLY, default_pref=TMDB_ONLY,
        )

        assert merged["rating"] == 7.0
        assert sources["rating"] == "tmdb"

    def test_imdb_only_mode(self) -> None:
        tmdb = {"rating": 7.0, "votes": 100}
        imdb = {"rating": 8.0, "votes": 5000}

        merged, sources = resolve_metadata(
            tmdb, imdb, ["rating", "votes"],
            plot_pref=IMDB_ONLY, default_pref=IMDB_ONLY,
        )

        assert merged["rating"] == 8.0
        assert sources["rating"] == "imdb"


# =============================================================================
# Tests: build_ratings
# =============================================================================


def test_build_ratings_adds_both_slots_default_tmdb():
    m, src, votes, _fp = build_ratings(
        {"rating": 7.28, "votes": 2331}, (8.1, 450000),
        {}, PREFER_TMDB,
    )
    assert m["themoviedb"] == {"rating": 7.28, "votes": 2331, "default": True}
    assert m["imdb"] == {"rating": 8.1, "votes": 450000, "default": False}
    assert src == "themoviedb"
    assert votes == 2331


def test_build_ratings_default_follows_imdb_preference():
    m, src, votes, _fp = build_ratings(
        {"rating": 7.0, "votes": 10}, (8.0, 9000),
        {}, PREFER_IMDB,
    )
    assert m["imdb"]["default"] is True
    assert m["themoviedb"]["default"] is False
    assert src == "imdb"
    assert votes == 9000


def test_build_ratings_preserves_other_sources():
    current = {"trakt": {"rating": 6.45, "votes": 13378, "default": True}}
    m, src, votes, _fp = build_ratings(
        {"rating": 6.6, "votes": 2558}, None, current, PREFER_TMDB,
    )
    assert "trakt" in m
    assert m["trakt"]["rating"] == 6.45 and m["trakt"]["votes"] == 13378
    assert m["trakt"]["default"] is False  # default moved to our slot
    assert m["themoviedb"]["default"] is True


def test_build_ratings_tmdb_only_omits_imdb_slot():
    m, src, votes, _fp = build_ratings(
        {"rating": 7.0, "votes": 100}, (8.0, 5000),
        {}, TMDB_ONLY,
    )
    assert "themoviedb" in m
    assert "imdb" not in m


def test_build_ratings_no_change_returns_none():
    current = {"themoviedb": {"rating": 7.28, "votes": 2331, "default": True}}
    m, src, votes, _fp = build_ratings(
        {"rating": 7.28, "votes": 2331}, None, current, PREFER_TMDB,
    )
    assert m is None  # idempotent: same value -> no write (A3/A4)


def test_build_ratings_tolerates_float_precision():
    # Kodi stored 7.28 as 7.28000020980835; provider says 7.28 -> no change
    current = {"themoviedb": {"rating": 7.28000020980835, "votes": 2331, "default": True}}
    m, src, votes, _fp = build_ratings(
        {"rating": 7.28, "votes": 2331}, None, current, PREFER_TMDB,
    )
    assert m is None


def test_build_ratings_detects_real_change():
    current = {"themoviedb": {"rating": 7.0, "votes": 100, "default": True}}
    m, src, votes, _fp = build_ratings(
        {"rating": 7.5, "votes": 5000}, None, current, PREFER_TMDB,
    )
    assert m is not None
    assert m["themoviedb"]["rating"] == 7.5


def test_build_ratings_skips_zero_and_out_of_range():
    # 0.0 rating is "no data"; Kodi also rejects <=0 / >10
    m, src, votes, _fp = build_ratings(
        {"rating": 0.0, "votes": 0}, None, {}, PREFER_TMDB,
    )
    assert m is None


def test_build_ratings_no_candidates_returns_none():
    m, src, votes, _fp = build_ratings({}, {}, None, {"trakt": {"rating": 6.0, "votes": 5}}, PREFER_TMDB)
    assert m is None


def test_unmanaged_ratings_unchanged_behaviour():
    current = {"imdb": {"rating": 1.0, "votes": 1, "default": True}}
    m, src, votes, fps = build_ratings(
        {"rating": 7.0, "votes": 10}, (8.0, 9000), current, PREFER_TMDB,
    )
    assert m["themoviedb"]["default"] is True
    assert m["imdb"] == {"rating": 8.0, "votes": 9000, "default": False}
    assert src == "themoviedb" and votes == 10
    assert set(fps) == {"themoviedb", "imdb"}


def test_build_ratings_returns_slot_fingerprints():
    m, _s, _v, fps = build_ratings({"rating": 7.28, "votes": 5}, None, {}, PREFER_TMDB)
    assert fps == {"themoviedb": fingerprint({"rating": 7.28, "votes": 5})}


def test_fingerprint_ignores_default_flag_and_float_noise():
    entry = {"rating": 7.28000020980835, "votes": 5, "default": True}
    assert slot_fingerprint(entry) == fingerprint({"rating": 7.28, "votes": 5})


def test_nfo_ratings_keep_default():
    # The NFO's rating is the default; our new slots are added without taking it.
    current = {"nfo": {"rating": 6.0, "votes": 10, "default": True}}
    m, src, votes, fps = build_ratings(
        {"rating": 7.0, "votes": 20}, (8.0, 900), current, PREFER_IMDB,
        nfo_managed=True,
    )
    assert m["nfo"] == {"rating": 6.0, "votes": 10, "default": True}
    assert m["imdb"]["default"] is False
    assert m["themoviedb"]["default"] is False
    assert src is None and votes is None
    assert set(fps) == {"imdb", "themoviedb"}


def test_nfo_ratings_update_owned_imdb_slot():
    imdb_old = {"rating": 7.0, "votes": 100, "default": False}
    current = {
        "nfo": {"rating": 6.0, "votes": 10, "default": True},
        "imdb": imdb_old,
    }
    owned = {"imdb": slot_fingerprint(imdb_old)}
    m, _s, _v, fps = build_ratings(
        {}, (8.0, 900), current, PREFER_IMDB, nfo_managed=True, owned_slots=owned,
    )
    assert m["imdb"] == {"rating": 8.0, "votes": 900, "default": False}
    assert m["nfo"]["default"] is True
    assert fps == {"imdb": slot_fingerprint({"rating": 8.0, "votes": 900})}


def test_nfo_ratings_never_touch_nfo_slot():
    # The NFO supplied the imdb slot itself; metamender never wrote it.
    current = {"imdb": {"rating": 7.0, "votes": 100, "default": True}}
    m, _s, _v, fps = build_ratings(
        {"rating": 6.5, "votes": 20}, (8.0, 900), current, PREFER_TMDB,
        nfo_managed=True, owned_slots={},
    )
    assert m["imdb"] == {"rating": 7.0, "votes": 100, "default": True}
    assert m["themoviedb"]["rating"] == 6.5
    assert "imdb" not in fps


def test_nfo_ratings_edited_owned_slot_is_protected():
    edited = {"rating": 9.9, "votes": 1, "default": False}
    current = {"imdb": edited}
    m, *_ = build_ratings(
        {}, (8.0, 900), current, PREFER_IMDB, nfo_managed=True,
        owned_slots={"imdb": slot_fingerprint({"rating": 7.0, "votes": 100})},
    )
    assert m is None


def test_nfo_ratings_nothing_to_change_returns_none():
    current = {"themoviedb": {"rating": 7.0, "votes": 20, "default": False}}
    m, src, votes, fps = build_ratings(
        {"rating": 7.0, "votes": 20}, None, current, PREFER_TMDB,
        nfo_managed=True,
        owned_slots={"themoviedb": slot_fingerprint(current["themoviedb"])},
    )
    assert (m, src, votes, fps) == (None, None, None, {})


# =============================================================================
# Tests: wants_imdb
# =============================================================================


def test_default_pref_governs_nonplot_fields():
    merged, src = resolve_metadata(
        {"genres": ["A"]}, {"genres": ["B"]}, ["genres", "plot"],
        plot_pref=PREFER_TMDB, default_pref=PREFER_IMDB,
    )
    assert merged["genres"] == ["B"] and src["genres"] == "imdb"


def test_plot_pref_independent_of_default():
    merged, src = resolve_metadata(
        {"plot": "T"}, {"plot": "I"}, ["plot"],
        plot_pref=PREFER_IMDB, default_pref=TMDB_ONLY,
    )
    assert merged["plot"] == "I"


def test_wants_imdb_true_if_any_setting_wants_it():
    assert wants_imdb(TMDB_ONLY, TMDB_ONLY, PREFER_IMDB) is True
    assert wants_imdb(TMDB_ONLY, TMDB_ONLY, TMDB_ONLY) is False
    assert wants_imdb(PREFER_TMDB, TMDB_ONLY, TMDB_ONLY) is True

"""Tests for rating resolution."""
from __future__ import annotations

from resources.lib.engine.protect import fingerprint
from resources.lib.engine.resolve import (
    FOLLOW_SCRAPER,
    IMDB_ONLY,
    PREFER_IMDB,
    PREFER_TMDB,
    TMDB_ONLY,
    build_ratings,
    effective_rating_pref,
    rating_sources,
    slot_fingerprint,
)


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
    m, src, votes, _fp = build_ratings(
        {}, None, {"trakt": {"rating": 6.0, "votes": 5}}, PREFER_TMDB,
    )
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


def test_nfo_item_without_ratings_gets_the_preferred_default():
    # Nothing in the NFO says which rating to show, so the preference does;
    # otherwise Kodi picks one itself.
    m, src, votes, _fps = build_ratings(
        {"rating": 7.0, "votes": 20}, (8.0, 900), {}, PREFER_TMDB, nfo_managed=True,
    )
    assert m["themoviedb"]["default"] is True
    assert m["imdb"]["default"] is False
    assert (src, votes) == ("themoviedb", 20)

    m, src, _votes, _fps = build_ratings(
        {"rating": 7.0, "votes": 20}, (8.0, 900), None, PREFER_IMDB, nfo_managed=True,
    )
    assert m["imdb"]["default"] is True
    assert src == "imdb"


def test_nfo_item_default_set_by_metamender_is_kept_next_scan():
    first, *_ = build_ratings(
        {"rating": 7.0, "votes": 20}, (8.0, 900), {}, PREFER_TMDB, nfo_managed=True,
    )
    owned = {name: slot_fingerprint(entry) for name, entry in first.items()}
    again = build_ratings(
        {"rating": 7.0, "votes": 20}, (8.0, 900), first, PREFER_TMDB,
        nfo_managed=True, owned_slots=owned,
    )
    assert again == (None, None, None, {})


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
# Tests: rating_sources
# =============================================================================


def test_rating_sources_per_preference():
    assert rating_sources(PREFER_TMDB) == (True, True)
    assert rating_sources(PREFER_IMDB) == (True, True)
    assert rating_sources(TMDB_ONLY) == (True, False)
    assert rating_sources(IMDB_ONLY) == (False, True)


# =============================================================================
# Tests: the TMDB slot name
# =============================================================================


def test_tmdb_rating_goes_into_existing_tmdb_slot():
    # Kodi's TMDB TV scraper names its slot "tmdb", the movie scraper
    # "themoviedb"; a second TMDB slot would show the same rating twice.
    current = {"tmdb": {"rating": 6.5, "votes": 100, "default": True}}
    m, src, votes, fps = build_ratings(
        {"rating": 7.2, "votes": 150}, None, current, PREFER_TMDB,
    )
    assert set(m) == {"tmdb"}
    assert m["tmdb"] == {"rating": 7.2, "votes": 150, "default": True}
    assert src == "tmdb"
    assert votes == 150
    assert set(fps) == {"tmdb"}


def test_tmdb_slot_unchanged_when_tmdb_slot_already_current():
    current = {"tmdb": {"rating": 7.2, "votes": 150, "default": True}}
    assert build_ratings(
        {"rating": 7.2, "votes": 150}, None, current, TMDB_ONLY,
    ) == (None, None, None, {})


def test_themoviedb_slot_wins_when_both_exist():
    current = {
        "themoviedb": {"rating": 6.0, "votes": 10, "default": False},
        "tmdb": {"rating": 6.0, "votes": 10, "default": True},
    }
    m, src, _votes, _fps = build_ratings(
        {"rating": 7.0, "votes": 20}, None, current, PREFER_TMDB,
    )
    assert src == "themoviedb"
    assert m["themoviedb"]["rating"] == 7.0
    assert m["tmdb"] == {"rating": 6.0, "votes": 10, "default": False}


def test_nfo_owned_tmdb_slot_is_updated():
    current = {"tmdb": {"rating": 6.5, "votes": 100, "default": True}}
    owned = {"tmdb": slot_fingerprint(current["tmdb"])}
    m, src, _votes, fps = build_ratings(
        {"rating": 7.2, "votes": 150}, None, current, PREFER_TMDB,
        nfo_managed=True, owned_slots=owned,
    )
    assert set(m) == {"tmdb"}
    assert m["tmdb"]["rating"] == 7.2
    assert src is None
    assert set(fps) == {"tmdb"}


def test_nfo_tmdb_slot_from_nfo_is_never_touched():
    current = {"tmdb": {"rating": 6.5, "votes": 100, "default": True}}
    assert build_ratings(
        {"rating": 7.2, "votes": 150}, None, current, PREFER_TMDB,
        nfo_managed=True, owned_slots={},
    ) == (None, None, None, {})


# =============================================================================
# Tests: Follow the scraper
# =============================================================================


def test_effective_pref_follows_imdb_default():
    cur = {"imdb": {"rating": 7.0, "votes": 10, "default": True},
           "themoviedb": {"rating": 6.0, "votes": 5, "default": False}}
    assert effective_rating_pref(FOLLOW_SCRAPER, cur) == PREFER_IMDB


def test_effective_pref_unmanaged_or_missing_default_is_prefer_tmdb():
    trakt = {"trakt": {"rating": 8.0, "votes": 3, "default": True}}
    assert effective_rating_pref(FOLLOW_SCRAPER, trakt) == PREFER_TMDB
    assert effective_rating_pref(FOLLOW_SCRAPER, {}) == PREFER_TMDB
    tmdb = {"tmdb": {"rating": 8.0, "votes": 3, "default": True}}
    assert effective_rating_pref(FOLLOW_SCRAPER, tmdb) == PREFER_TMDB


def test_effective_pref_fixed_options_unchanged():
    for p in (PREFER_TMDB, PREFER_IMDB, TMDB_ONLY, IMDB_ONLY):
        assert effective_rating_pref(p, {"imdb": {"default": True}}) == p


def test_follow_keeps_trakt_default_and_refreshes_ours():
    cur = {"trakt": {"rating": 8.0, "votes": 3, "default": True}}
    new, source, votes, fps = build_ratings(
        {"rating": 7.5, "votes": 100}, (7.9, 2000), cur, PREFER_TMDB,
        keep_default_mark=True)
    assert new["trakt"]["default"] is True
    assert new["themoviedb"] == {"rating": 7.5, "votes": 100, "default": False}
    assert new["imdb"]["default"] is False
    assert source is None and votes is None
    assert set(fps) == {"themoviedb", "imdb"}


def test_follow_keeps_imdb_default_and_reports_it_as_source():
    cur = {"imdb": {"rating": 7.0, "votes": 10, "default": True}}
    new, source, votes, _ = build_ratings(
        {"rating": 7.5, "votes": 100}, (7.9, 2000), cur, PREFER_IMDB,
        keep_default_mark=True)
    assert new["imdb"]["default"] is True
    assert new["themoviedb"]["default"] is False
    assert source == "imdb" and votes == 2000


def test_follow_with_no_ratings_sets_tmdb_default():
    new, source, _, _ = build_ratings(
        {"rating": 7.5, "votes": 100}, None, {}, PREFER_TMDB,
        keep_default_mark=True)
    assert source == "themoviedb" and new["themoviedb"]["default"] is True


def test_follow_with_ratings_but_no_default_sets_preferred():
    cur = {"trakt": {"rating": 8.0, "votes": 3, "default": False}}
    new, source, _, _ = build_ratings(
        {"rating": 7.5, "votes": 100}, None, cur, PREFER_TMDB,
        keep_default_mark=True)
    assert source == "themoviedb" and new["themoviedb"]["default"] is True
    assert new["trakt"]["default"] is False


def test_rating_sources_follow_consults_both():
    assert rating_sources(FOLLOW_SCRAPER) == (True, True)

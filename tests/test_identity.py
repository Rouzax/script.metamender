"""Tests for stable tracker identity computation."""
from __future__ import annotations

from resources.lib.data.identity import compute_identity
from resources.lib.data.media_types import MOVIE, TVSHOW, EPISODE


# NB: items here carry `premiered`/`firstaired` (the date fields the scan
# requests), NOT a synthetic `year` key. Kodi does not return `year` unless
# asked and the scan never asks, so compute_identity derives the year from the
# date field. Feeding a `year` key here would mask that path.


def test_movie_prefers_tmdb_id():
    item = {"title": "Dune", "premiered": "2021-10-22", "uniqueid": {"tmdb": "438631", "imdb": "tt1160419"}}
    assert compute_identity(item, MOVIE) == "tmdb:438631"


def test_movie_falls_back_to_imdb_id():
    item = {"title": "Dune", "premiered": "2021-10-22", "uniqueid": {"imdb": "tt1160419"}}
    assert compute_identity(item, MOVIE) == "imdb:tt1160419"


def test_movie_falls_back_to_title_year():
    item = {"title": "Dune", "premiered": "1984-12-14", "uniqueid": {}}
    assert compute_identity(item, MOVIE) == "Dune (1984)"


def test_movie_remakes_get_distinct_identities():
    a = {"title": "Dune", "premiered": "1984-12-14", "uniqueid": {}}
    b = {"title": "Dune", "premiered": "2021-10-22", "uniqueid": {}}
    assert compute_identity(a, MOVIE) != compute_identity(b, MOVIE)


def test_movie_title_only_when_no_date():
    item = {"title": "Some Doc", "uniqueid": {}}
    assert compute_identity(item, MOVIE) == "Some Doc"


def test_movie_title_only_when_date_malformed():
    item = {"title": "Some Doc", "premiered": "", "uniqueid": {}}
    assert compute_identity(item, MOVIE) == "Some Doc"


def test_movie_accepts_themoviedb_alias():
    item = {"title": "X", "uniqueid": {"themoviedb": "99"}}
    assert compute_identity(item, MOVIE) == "tmdb:99"


def test_tvshow_uses_same_rules_as_movie():
    item = {"title": "Show", "premiered": "2020-01-01", "uniqueid": {"tmdb": "555"}}
    assert compute_identity(item, TVSHOW) == "tmdb:555"


def test_tvshow_falls_back_to_title_year_from_premiered():
    # The live bug: no external ID, year must come from `premiered`.
    item = {"title": "Bassie & Adriaan", "premiered": "1977-01-10", "uniqueid": {"unknown": "263272"}}
    assert compute_identity(item, TVSHOW) == "Bassie & Adriaan (1977)"


def test_episode_uses_composite_key():
    item = {"showtitle": "Bluey", "season": 1, "episode": 5, "title": "Episode 5"}
    assert compute_identity(item, EPISODE) == "Bluey S01E05"


def test_episode_pads_numbers():
    item = {"showtitle": "Show", "season": 12, "episode": 3}
    assert compute_identity(item, EPISODE) == "Show S12E03"

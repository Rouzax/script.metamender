"""Tests for stable tracker identity computation."""
from __future__ import annotations

from resources.lib.data.identity import compute_identity
from resources.lib.data.media_types import MOVIE, TVSHOW, EPISODE


def test_movie_prefers_tmdb_id():
    item = {"title": "Dune", "year": 2021, "uniqueid": {"tmdb": "438631", "imdb": "tt1160419"}}
    assert compute_identity(item, MOVIE) == "tmdb:438631"


def test_movie_falls_back_to_imdb_id():
    item = {"title": "Dune", "year": 2021, "uniqueid": {"imdb": "tt1160419"}}
    assert compute_identity(item, MOVIE) == "imdb:tt1160419"


def test_movie_falls_back_to_title_year():
    item = {"title": "Dune", "year": 1984, "uniqueid": {}}
    assert compute_identity(item, MOVIE) == "Dune (1984)"


def test_movie_remakes_get_distinct_identities():
    a = {"title": "Dune", "year": 1984, "uniqueid": {}}
    b = {"title": "Dune", "year": 2021, "uniqueid": {}}
    assert compute_identity(a, MOVIE) != compute_identity(b, MOVIE)


def test_movie_title_only_when_no_year():
    item = {"title": "Some Doc", "uniqueid": {}}
    assert compute_identity(item, MOVIE) == "Some Doc"


def test_movie_accepts_themoviedb_alias():
    item = {"title": "X", "uniqueid": {"themoviedb": "99"}}
    assert compute_identity(item, MOVIE) == "tmdb:99"


def test_tvshow_uses_same_rules_as_movie():
    item = {"title": "Show", "year": 2020, "uniqueid": {"tmdb": "555"}}
    assert compute_identity(item, TVSHOW) == "tmdb:555"


def test_episode_uses_composite_key():
    item = {"showtitle": "Bluey", "season": 1, "episode": 5, "title": "Episode 5"}
    assert compute_identity(item, EPISODE) == "Bluey S01E05"


def test_episode_pads_numbers():
    item = {"showtitle": "Show", "season": 12, "episode": 3}
    assert compute_identity(item, EPISODE) == "Show S12E03"

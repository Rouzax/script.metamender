"""Tests for the FSK certification lookup (recorded API responses)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from resources.lib.providers import fsk
from resources.lib.utils import TransientProviderError

_FIX = Path(__file__).parent / "fixtures" / "fsk"


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _lookup(response, *args, **kwargs):
    with patch("resources.lib.providers.fsk.provider_get", return_value=response) as get:
        result = fsk.lookup(*args, **kwargs)
    return result, get


def test_the_matrix_found():
    rating, get = _lookup(_load("matrix_movie.json"), "The Matrix", "", 1999, "movie")
    assert rating == "16"
    params = get.call_args.kwargs["params"]
    assert params["superType"] == "single" and params["singleOptions[]"] == "SP"
    assert "ratingReleaseDateFrom" not in params
    assert get.call_args.kwargs["provider"] == "fsk"


def test_original_title_matches_german_listing():
    rating, _ = _lookup(_load("matrix_movie.json"), "Matrix", "The Matrix", 1999, "movie")
    assert rating == "16"


def test_year_two_off_is_no_match():
    rating, _ = _lookup(_load("matrix_movie.json"), "The Matrix", "", 2001, "movie")
    assert rating is None


def test_imdb_id_wins():
    data = _load("matrix_movie.json")
    data["data"]["docs"][3]["subproducts"] = [{"imdbId": "tt10838180"}]
    data["data"]["docs"][3]["__rating"] = 12
    rating, _ = _lookup(data, "Something Else", "", None, "movie", imdb_id="tt10838180")
    assert rating == "12"


def test_series_first_season():
    rating, get = _lookup(_load("dark_serial.json"), "Dark", "", 2017, "tvshow")
    assert rating == "12"
    params = get.call_args.kwargs["params"]
    assert params["superType"] == "serial" and params["serialOptions[]"] == "TVSR"
    assert params["ratingReleaseDateFrom"] == "2016-01-01"


def test_series_does_not_take_a_namesake():
    # "DARK BLUE - SEASON 1 - EPISODE 4" (2009) is another show than "Dark".
    rating, _ = _lookup(_load("dark_serial.json"), "Dark", "", 2009, "tvshow")
    assert rating is None


def test_series_lowest_season_within_the_year():
    rating, _ = _lookup(_load("dark_serial.json"), "Dark Matter", "", 2015, "tvshow")
    assert rating == "16"  # season 2 (2016) is the earliest listed


def test_structure_miss_is_transient():
    with pytest.raises(TransientProviderError):
        _lookup({"success": False}, "The Matrix", "", 1999, "movie")
    with pytest.raises(TransientProviderError):
        _lookup({"success": True, "data": {}}, "The Matrix", "", 1999, "movie")


def test_no_answer_is_none():
    assert _lookup(None, "The Matrix", "", 1999, "movie")[0] is None


def test_rating_outside_the_list_is_ignored():
    data = _load("matrix_movie.json")
    for doc in data["data"]["docs"]:
        doc["__rating"] = 99
    assert _lookup(data, "The Matrix", "", 1999, "movie")[0] is None

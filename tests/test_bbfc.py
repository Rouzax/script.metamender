"""Tests for the BBFC certification lookup (recorded search pages)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from resources.lib.providers import bbfc
from resources.lib.utils import TransientProviderError

_FIX = Path(__file__).parent / "fixtures" / "bbfc"


def _lookup(page, *args):
    body = (_FIX / page).read_text(encoding="utf-8") if page else None
    with patch("resources.lib.providers.bbfc.provider_get_text", return_value=body) as get:
        result = bbfc.lookup(*args)
    return result, get


def test_film_found_past_a_namesake():
    rating, get = _lookup("interstellar.html", "Interstellar", "", 2014, "movie")
    assert rating == "12"  # not "Interstellar Wars (2016)"
    assert get.call_args.kwargs["params"] == {"q": "Interstellar", "t[]": "Film"}
    assert get.call_args.kwargs["provider"] == "bbfc"


def test_year_mismatch_is_no_match():
    assert _lookup("interstellar.html", "Interstellar", "", 2010, "movie")[0] is None


def test_tv_show():
    rating, get = _lookup("sherlock.html", "Sherlock", "", 2010, "tvshow")
    assert rating == "15"
    assert get.call_args.kwargs["params"]["t[]"] == "TV Show"


def test_tv_filter_ignores_films():
    assert _lookup("interstellar.html", "Interstellar", "", 2014, "tvshow")[0] is None


def test_page_without_next_data_is_transient():
    with pytest.raises(TransientProviderError):
        _lookup("challenge.html", "Interstellar", "", 2014, "movie")


def test_no_page_is_none():
    assert _lookup(None, "Interstellar", "", 2014, "movie")[0] is None


def test_original_title_searched_when_local_title_finds_nothing():
    page = (_FIX / "interstellar.html").read_text(encoding="utf-8")
    empty = page.replace('"results": [', '"results": [] , "x": [')

    def fake_get(url, **kwargs):
        return page if kwargs["params"]["q"] == "Interstellar" else empty

    with patch("resources.lib.providers.bbfc.provider_get_text", side_effect=fake_get) as get:
        rating = bbfc.lookup("Interstellaire", "Interstellar", 2014, "movie")
    assert rating == "12"
    assert [c.kwargs["params"]["q"] for c in get.call_args_list] == [
        "Interstellaire", "Interstellar"]

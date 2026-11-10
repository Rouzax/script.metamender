"""Tests for the Medieraadet certification lookup (recorded API answers)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from resources.lib.providers import medieraadet
from resources.lib.utils import TransientProviderError

_FIX = Path(__file__).parent / "fixtures" / "medieraadet"


def _load(name):
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _film(film_id, title, year, code, original=""):
    return {"Id": film_id, "Title": title, "DanishTitle": title,
            "OriginalTitle": original or title, "ReleaseYear": year, "AssesmentCode": code}


def _run(responses, *args):
    """responses: callable(url, params) -> JSON, or raises."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs.get("params")))
        return responses(url, kwargs.get("params"))

    with patch("resources.lib.providers.medieraadet.provider_get", side_effect=fake_get):
        return medieraadet.lookup(*args), calls


def _recorded(url, params):
    if url.endswith("/film"):
        return _load("search_interstellar.json")
    return _load("film_91076.json")


def test_interstellar():
    rating, calls = _run(_recorded, "Interstellar", "", 2014, "movie")
    assert rating == "11"
    assert calls[0][1]["Title"] == "Interstellar" and calls[0][1]["Take"] == 10
    assert calls[1][0].endswith("/film/91076")  # the year match is fetched first


def test_tv_show_makes_no_request():
    rating, calls = _run(_recorded, "Interstellar", "", 2014, "tvshow")
    assert rating is None and calls == []


def test_at_most_three_detail_fetches():
    hits = [{"Id": i, "Title": "Dune", "ReleaseYear": 2021} for i in range(10)]

    def answers(url, params):
        if url.endswith("/film"):
            return {"FilmList": hits}
        return _film(int(url.rsplit("/", 1)[1]), "Something else", 2021, "to11")

    rating, calls = _run(answers, "Dune", "", 2021, "movie")
    assert rating is None
    assert len([c for c in calls if not c[0].endswith("/film")]) == medieraadet.MAX_DETAILS == 3


def test_detail_failure_is_transient():
    def answers(url, params):
        if url.endswith("/film"):
            return _load("search_interstellar.json")
        raise TransientProviderError("http 503", status=503)

    with pytest.raises(TransientProviderError):
        _run(answers, "Interstellar", "", 2014, "movie")


def test_article_retry():
    def answers(url, params):
        if url.endswith("/film"):
            if params["Title"] == "The Hunt":
                return {"FilmList": []}
            return {"FilmList": [{"Id": 7, "Title": "The Hunt", "ReleaseYear": 2020}]}
        return _film(7, "The Hunt", 2020, "to15")

    rating, calls = _run(answers, "The Hunt", "", 2020, "movie")
    assert rating == "15"
    assert [c[1]["Title"] for c in calls if c[0].endswith("/film")] == ["The Hunt", "Hunt"]


def test_unknown_code_is_none():
    def answers(url, params):
        if url.endswith("/film"):
            return _load("search_interstellar.json")
        return _film(91076, "Interstellar", 2014, "xyz")

    assert _run(answers, "Interstellar", "", 2014, "movie")[0] is None


def test_search_without_film_list_is_transient():
    with pytest.raises(TransientProviderError):
        _run(lambda url, params: {"Error": "x"}, "Interstellar", "", 2014, "movie")


def test_code_map():
    assert medieraadet.CODE_MAP == {"tfa": "A", "fr.u7": "7", "to7": "7", "to11": "11",
                                    "to15": "15"}

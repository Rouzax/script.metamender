"""A library page shows only the toggles for what the library holds.

Kodi keeps each source's content type in its video database but gives it
to no add-on, so the library's items stand in for it: movies under the
folder make a movies library, shows a TV shows library, and a folder with
neither (nothing scanned yet) or with both keeps every toggle.
"""
from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from resources.lib.data.libraries import FIELD_TOGGLES, LibraryConfig, new_library
from resources.lib.data.media_types import EPISODE, MOVIE, SET, TVSHOW
from resources.lib.data.sources import (
    CONTENT_MIXED,
    CONTENT_MOVIES,
    CONTENT_TVSHOWS,
    CONTENT_UNKNOWN,
    library_content,
)
from resources.lib.ui import setup_window
from resources.lib.ui.setup_model import (
    MOVIE_TOGGLES,
    TOGGLES,
    TV_TOGGLES,
    page_groups,
    page_toggles,
    shown_toggles,
)
from resources.lib.ui.setup_window import HEADER
from tests.test_setup_window import _controller, _films, _open_page, _to_summary

_ALL = frozenset(FIELD_TOGGLES)


def _rpc(totals, sent=None):
    """A JSON-RPC stub: totals maps (method, folder) to an item count; a
    pair it lacks answers with an error."""
    def call(raw):
        query = json.loads(raw)
        if sent is not None:
            sent.append(query)
        key = (query["method"], query["params"]["filter"]["value"])
        if key not in totals:
            return json.dumps({"error": {"code": -32602, "message": "Invalid params"}})
        return json.dumps({"result": {"limits": {"start": 0, "end": 1, "total": totals[key]}}})
    return call


def _content(path, totals, sent=None):
    with patch("resources.lib.data.sources.xbmc.executeJSONRPC", side_effect=_rpc(totals, sent)):
        return library_content(path)


_M, _S = "VideoLibrary.GetMovies", "VideoLibrary.GetTVShows"


def test_movies_under_the_folder_make_a_movies_library():
    sent: list = []
    assert _content("/films/", {(_M, "/films/"): 12, (_S, "/films/"): 0}, sent) == CONTENT_MOVIES
    assert sent[0]["params"] == {
        "filter": {"field": "path", "operator": "startswith", "value": "/films/"},
        "limits": {"end": 1}}


def test_shows_under_the_folder_make_a_tv_library():
    assert _content("/tv/", {(_M, "/tv/"): 0, (_S, "/tv/"): 3}) == CONTENT_TVSHOWS


def test_an_unscanned_folder_is_unknown():
    assert _content("/new/", {(_M, "/new/"): 0, (_S, "/new/"): 0}) == CONTENT_UNKNOWN


def test_a_multipath_library_looks_in_every_folder():
    path = "multipath://%2fa%2f/%2fb%2f/"
    totals = {(_M, "/a/"): 0, (_S, "/a/"): 0, (_M, "/b/"): 0, (_S, "/b/"): 4}
    assert _content(path, totals) == CONTENT_TVSHOWS
    totals[(_M, "/a/")] = 1
    assert _content(path, totals) == CONTENT_MIXED


def test_a_failed_query_is_unknown(kodi_stubs):
    assert _content("/films/", {(_M, "/films/"): 12}) == CONTENT_UNKNOWN


# -- which toggles a page lists ------------------------------------------


def _fields(*types):
    out = set()
    for media_type in types:
        out |= media_type.backfill_fields
    return out


@pytest.mark.parametrize("toggle", sorted(MOVIE_TOGGLES))
def test_movie_toggles_fill_nothing_in_a_tv_library(toggle):
    fields = FIELD_TOGGLES[toggle] - {"movie_set"}
    assert not fields & _fields(TVSHOW, EPISODE) - {"title"}
    assert toggle == "movie_sets" or fields & _fields(MOVIE, SET)


@pytest.mark.parametrize("toggle", sorted(TV_TOGGLES))
def test_tv_toggles_fill_nothing_in_a_movies_library(toggle):
    # The episode-title toggle fills "title" on episodes only; movies take
    # theirs from the main-title toggle (libraries.candidate_fields).
    fields = FIELD_TOGGLES[toggle]
    assert fields & _fields(EPISODE)
    assert toggle == "episode_titles" or not fields & _fields(MOVIE, SET)


def test_every_other_toggle_applies_to_both():
    for toggle in set(TOGGLES) - MOVIE_TOGGLES - TV_TOGGLES:
        fields = FIELD_TOGGLES[toggle] | ({"title"} if toggle == "main_titles" else set())
        assert fields & _fields(MOVIE, SET), toggle
        assert fields & _fields(TVSHOW, EPISODE), toggle


def test_page_toggles_per_content():
    assert page_toggles(CONTENT_MOVIES) == tuple(t for t in TOGGLES if t not in TV_TOGGLES)
    assert page_toggles(CONTENT_TVSHOWS) == tuple(t for t in TOGGLES if t not in MOVIE_TOGGLES)
    assert page_toggles(CONTENT_UNKNOWN) == TOGGLES
    assert page_toggles(CONTENT_MIXED) == TOGGLES


def test_hidden_ticks_open_no_group():
    lib = replace(new_library("/c/", "Films", "en-US"), toggles=frozenset({"episode_titles"}))
    assert shown_toggles(lib, CONTENT_MOVIES) == frozenset()
    assert page_groups(lib, CONTENT_MOVIES) == ["language", "fields"]
    assert page_groups(lib, CONTENT_TVSHOWS) == ["language", "fields", "text"]


# -- the window ----------------------------------------------------------


@pytest.fixture
def ids(monkeypatch):
    monkeypatch.setattr(setup_window, "lang", lambda i: "#%d" % i)


def _toggle_rows(c):
    return {r.key[len("toggle:"):]: r for r in c.rows() if r.key.startswith("toggle:")}


def test_tv_library_page(ids):
    c, _ = _controller(key="abc", content=CONTENT_TVSHOWS)
    _open_page(c, included=True, toggles=_ALL)
    rows = _toggle_rows(c)
    assert tuple(rows) == page_toggles(CONTENT_TVSHOWS)
    assert rows["details"].detail == "#32782" and rows["other_art"].detail == "#32784"
    assert rows["plots"].detail == "#32435"
    assert c.heading() == "#32778"
    assert c.explanation() == "#32666"


def test_movies_library_page(ids):
    c, _ = _controller(key="abc", content=CONTENT_MOVIES)
    _open_page(c, included=True)
    rows = _toggle_rows(c)
    assert tuple(rows) == page_toggles(CONTENT_MOVIES)
    assert rows["details"].detail == "#32781" and rows["other_art"].detail == "#32783"
    assert c.heading() == "#32777"
    # Nothing ticked yet: the hint that more choices follow comes last.
    assert c.explanation() == "#32666\n#32667"


def test_heading_names_the_library(monkeypatch):
    monkeypatch.setattr(setup_window, "lang",
                        lambda i: "{} (TV shows library)" if i == 32778 else "#%d" % i)
    c, _ = _controller(key="abc", content=CONTENT_TVSHOWS)
    _open_page(c, included=True)
    assert c.heading() == "Films (TV shows library)"


@pytest.mark.parametrize("content, note", [(CONTENT_UNKNOWN, "#32779"),
                                           (CONTENT_MIXED, "#32780")])
def test_page_without_one_kind_shows_everything_and_says_why(ids, content, note):
    c, _ = _controller(key="abc", content=content)
    _open_page(c, included=True, toggles=frozenset({"plots"}))
    assert tuple(_toggle_rows(c)) == TOGGLES
    assert _toggle_rows(c)["details"].detail == "#32449"
    assert c.heading() == "Films"
    assert c.explanation() == "#32666\n" + note


def test_hidden_ticks_are_kept_and_not_counted(ids):
    c, rec = _controller(key="abc", content=CONTENT_TVSHOWS)
    _open_page(c, included=True, toggles=frozenset({"taglines", "trailers"}))
    assert "#32667" in c.explanation()
    assert c.state.libraries.by_path("/c/").toggles == {"taglines", "trailers"}
    c.state.open_library = None
    assert c.rows()[1].detail == "#32660"


def test_toggling_a_shown_tick_keeps_the_hidden_ones(ids):
    c, _ = _controller(key="abc", content=CONTENT_TVSHOWS)
    _open_page(c, included=True, toggles=frozenset({"taglines"}))
    c.activate(next(i for i, r in enumerate(c.rows()) if r.key == "toggle:plots"))
    assert c.state.libraries.by_path("/c/").toggles == {"taglines", "plots"}


def test_summary_names_only_the_ticks_that_apply(ids):
    stored = LibraryConfig([_films(included=True, toggles=frozenset({"taglines", "episode_titles"}))])
    c, _ = _controller(key="abc", libraries=stored, content=CONTENT_MOVIES)
    _to_summary(c)
    rows = [(r.label, r.detail, r.marker) for r in c.rows()]
    assert ("Films", "", HEADER) in rows
    assert ("#32462", "#32685", "") in rows
    # The text sources row belongs to the episode-title tick, hidden here.
    assert "#32649" not in [label for label, _, _ in rows]


def test_content_is_asked_once_per_library(ids):
    asked: list[str] = []

    def content_of(path):
        asked.append(path)
        return CONTENT_TVSHOWS

    c, _ = _controller(key="abc", content=content_of)
    _open_page(c, included=True)
    for _ in range(3):
        c.rows()
        c.heading()
        c.explanation()
    assert asked == ["/c/"]

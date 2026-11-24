"""Scan tests for the movie set pass: member collections, duplicate-set
merges, and the set's name, plot and art (engine/sets.py prepare_sets and
run_scan on SET). Kodi and the providers are faked."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from resources.lib.data import kodi
from resources.lib.data.libraries import ART_SOURCE_FANART, Library, LibraryConfig
from resources.lib.data.localart import EXISTS
from resources.lib.data.media_types import MOVIE, SET
from resources.lib.data.nfo import NFO, NO_NFO, UNREACHABLE
from resources.lib.data.tracker import REASON_CHANGED_OUTSIDE
from resources.lib.engine import sets
from resources.lib.engine.art import Poster
from resources.lib.engine.scan import run_scan
from resources.lib.engine.sets import pick_set_name, set_nfo_state
from resources.lib.utils import TransientProviderError
from tests.test_scan import (  # noqa: F401 (autouse fixture)
    _make_settings,
    _make_tracker,
    _mock_secondary_providers,
)

LIB = "smb://nas/Movies/"
IDENTITY = "collection:86066"
EN_NAME = "Despicable Me Collection"
NL_NAME = "Verschrikkelijke Ikke Collectie"
DE_NAME = "Ich - Einfach unverbesserlich Filmreihe"
EN_PLOT = "Gru wants to steal the moon."
NL_PLOT = "Gru wil de maan stelen."
TMDB_POSTER = "https://image.tmdb.org/t/p/original/set_nl.jpg"
TMDB_BACKDROP = "https://image.tmdb.org/t/p/original/set_bg.jpg"
FANART_POSTER = "https://assets.fanart.tv/fanart/movies/86066/movieposter/nl.jpg"
# New content: its fields are checked again on later scans rather than
# settling after the first.
RECENT = (date.today() - timedelta(days=30)).isoformat()


def _library(toggles=("movie_sets",), language="nl-NL", fallback="", **kw: Any) -> LibraryConfig:
    return LibraryConfig([Library(
        path=LIB, label="Kids", included=True, language=language,
        fallback_language=fallback, toggles=frozenset(toggles), **kw,
    )])


class FakeKodi:
    """Kodi's sets and movies, with SetMovieDetails' set rules: a movie
    filed under a name joins the set of that name (case-insensitive, as
    AddSet's LIKE) or a new one, and AddSet overwrites that set's plot with
    the moved movie's old set overview."""

    def __init__(self) -> None:
        self.sets: dict[int, dict[str, Any]] = {}
        self.movies: dict[int, dict[str, Any]] = {}
        self.land_in: int | None = None
        self.update = MagicMock(side_effect=self._update)

    def add_set(self, setid: int, title: str, plot: str = "", art=None) -> None:
        self.sets[setid] = {"setid": setid, "title": title, "plot": plot,
                            "art": dict(art or {})}

    def add_movie(self, movieid: int, setid: int, tmdb: str, premiered=RECENT) -> None:
        self.movies[movieid] = {
            "movieid": movieid, "title": "Film {}".format(movieid), "set": "",
            "setid": setid, "file": "{}film{}/film.mkv".format(LIB, movieid),
            "uniqueid": {"tmdb": tmdb}, "premiered": premiered,
        }

    def list_items(self, media_type, **_kw):
        assert media_type is SET
        return [kodi.flatten_art(dict(s, art=dict(s["art"])), SET) for s in self.sets.values()]

    def members(self):
        return [dict(m) for m in self.movies.values() if m["setid"] > 0]

    def movie_setid(self, movieid):
        if self.land_in is not None:
            return self.land_in
        return self.movies[movieid]["setid"]

    def details(self, media_type, item_id, art=True):
        assert media_type is SET
        found = self.sets.get(item_id)
        return dict(found) if found else None

    def _update(self, item_id, media_type, log_title="", **fields):
        if media_type is MOVIE:
            movie = self.movies[item_id]
            old = self.sets.get(movie["setid"]) or {}
            name = fields["set"]
            target = next((s for s in self.sets.values()
                           if s["title"].lower() == name.lower()), None)
            if target is None:
                setid = max(self.sets, default=0) + 1
                self.add_set(setid, name)
                target = self.sets[setid]
            if old.get("plot"):
                target["plot"] = old["plot"]
            movie["setid"] = target["setid"]
            return True
        kodi_set = self.sets[item_id]
        for key, value in fields.items():
            if key in ("title", "plot"):
                kodi_set[key] = value.strip()
            else:
                if value:
                    kodi_set["art"][key] = value
                else:
                    kodi_set["art"].pop(key, None)
        return True


@pytest.fixture
def fake():
    fk = FakeKodi()
    with patch("resources.lib.data.kodi.get_all_items", side_effect=fk.list_items), \
            patch("resources.lib.data.kodi.get_set_members", side_effect=fk.members), \
            patch("resources.lib.data.kodi.get_movie_setid", side_effect=fk.movie_setid), \
            patch("resources.lib.data.kodi.get_item_by_id", side_effect=fk.details), \
            patch("resources.lib.data.kodi.update_item", fk.update), \
            patch("resources.lib.engine.sets.nfo_state", return_value=NO_NFO):
        yield fk


def _collection(names=None, plots=None, posters=(), backdrops=()):
    return {
        "names": {"en": EN_NAME, "nl-NL": NL_NAME, "nl": NL_NAME} if names is None else names,
        "plots": {"en": EN_PLOT, "nl-NL": NL_PLOT, "nl": NL_PLOT} if plots is None else plots,
        "posters": list(posters), "backdrops": list(backdrops),
    }


@pytest.fixture
def tmdb_api():
    with patch("resources.lib.providers.tmdb.fetch_collection",
               return_value=_collection()) as coll, \
            patch("resources.lib.providers.tmdb.fetch_movie_collection_id",
                  return_value="86066") as member, \
            patch("resources.lib.providers.fanart.fetch_movie_art", return_value={}) as art:
        yield {"collection": coll, "member": member, "fanart": art}


def _scan(tracker, libraries, **kwargs):
    return run_scan(SET, tracker, libraries, "k", _make_settings(), **kwargs)


def _set_write(fk: FakeKodi, setid: int = 1) -> dict[str, Any]:
    """The fields of the last write to the set, {} when none."""
    for c in reversed(fk.update.call_args_list):
        if c.args[1] is SET and c.args[0] == setid:
            return c.kwargs
    return {}


def _set_writes(fk: FakeKodi) -> list[dict[str, Any]]:
    return [c.kwargs for c in fk.update.call_args_list if c.args[1] is SET]


def _movie_moves(fk: FakeKodi) -> list[tuple[int, str]]:
    return [(c.args[0], c.kwargs["set"]) for c in fk.update.call_args_list
            if c.args[1] is MOVIE]


def _make_due(tracker):
    tracker._conn.execute("UPDATE field_status SET next_retry = '2000-01-01'")
    tracker.commit()
    tracker.start_new_cycle()


def _one_set(fk: FakeKodi, title=EN_NAME, plot=EN_PLOT, art=None) -> None:
    fk.add_set(1, title, plot, art)
    fk.add_movie(1, 1, "20352")
    fk.add_movie(2, 1, "93456")


def _fs(tracker):
    return tracker.get_field_status_map("set", IDENTITY)


# ---------------------------------------------------------------------------
# Name
# ---------------------------------------------------------------------------


def test_set_renamed_once_to_library_language_name(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert _set_write(fake)["title"] == NL_NAME
    assert tracker.get_keep("set", IDENTITY)["title"]["original"] == EN_NAME
    known = tracker.get_set_names(IDENTITY)
    # The Kodi name at first look, TMDB's names and the written one.
    assert {EN_NAME.lower(), NL_NAME.lower()} <= known
    tracker.close()


def test_written_set_name_is_held_on_next_scan(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    # Kodi trims what it stores: the round trip must still match.
    tmdb_api["collection"].return_value = _collection(
        names={"en": EN_NAME, "nl-NL": NL_NAME + " ", "nl": NL_NAME + " "})
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert fake.sets[1]["title"] == NL_NAME
    _make_due(tracker)
    fake.update.reset_mock()
    _scan(tracker, _library())
    assert "title" not in _set_write(fake)
    assert "title" not in tracker.locked_fields("set", IDENTITY)
    tracker.close()


def test_set_name_changed_outside_is_locked(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    fake.sets[1]["title"] = "Minions and Gru"
    _make_due(tracker)
    fake.update.reset_mock()
    _scan(tracker, _library())
    assert "title" not in _set_write(fake)
    assert "title" in tracker.locked_fields("set", IDENTITY)
    assert _fs(tracker)["title"]["last_reason"] == REASON_CHANGED_OUTSIDE
    tracker.close()


def test_set_name_falls_back_then_english(tmp_path, kodi_stubs, fake, tmdb_api):
    names = {"en": EN_NAME, "de-DE": DE_NAME, "de": DE_NAME}
    assert pick_set_name(names, "nl-NL", "de-DE") == (DE_NAME, True)
    assert pick_set_name(names, "nl-NL", None) == (EN_NAME, True)
    assert pick_set_name({"nl": NL_NAME, **names}, "nl-BE", "de-DE") == (NL_NAME, False)
    assert pick_set_name({}, "nl-NL", "de-DE") == (None, False)

    _one_set(fake)
    tmdb_api["collection"].return_value = _collection(names=names)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library(fallback="de-DE"))
    assert _set_write(fake)["title"] == DE_NAME
    assert _fs(tracker)["title"]["from_fallback"] == 1
    tracker.close()


# ---------------------------------------------------------------------------
# Plot and NFO
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plot", ["", EN_PLOT])
def test_set_plot_filled_and_wrong_language_replaced(tmp_path, kodi_stubs, fake, tmdb_api,
                                                     plot):
    _one_set(fake, plot=plot)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert _set_write(fake)["plot"] == NL_PLOT
    tracker.close()


@pytest.mark.parametrize("plot,written", [("", "Gru will den Mond."), (EN_PLOT, None)])
def test_set_plot_fallback_only_fills(tmp_path, kodi_stubs, fake, tmdb_api, plot, written):
    """No Dutch plot: the German one fills an empty plot, read from the same
    answer, and never replaces the English one."""
    plots = {"en": EN_PLOT, "de-DE": "Gru will den Mond.", "de": "Gru will den Mond."}
    tmdb_api["collection"].return_value = _collection(plots=plots)
    _one_set(fake, plot=plot)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library(fallback="de-DE"))
    assert _set_write(fake).get("plot") == written
    assert tmdb_api["collection"].call_count == 1
    tracker.close()


@pytest.mark.parametrize("states,written", [
    ([NFO, NFO], False),
    ([NFO, NO_NFO], True),
])
def test_set_on_all_nfo_members_keeps_name_and_plot(tmp_path, kodi_stubs, fake, tmdb_api,
                                                    states, written):
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.sets.nfo_state", side_effect=list(states)):
        _scan(tracker, _library())
    write = _set_write(fake)
    assert ("title" in write) is written
    assert ("plot" in write) is written
    tracker.close()


def test_set_nfo_state():
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[NFO, NFO]):
        assert set_nfo_state(("a", "b")) == NFO
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[NFO, UNREACHABLE]):
        assert set_nfo_state(("a", "b")) == UNREACHABLE
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[NFO, NO_NFO]):
        assert set_nfo_state(("a", "b")) == NO_NFO
    # A reachable member without an NFO decides the set whatever the
    # unreachable one would say.
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[UNREACHABLE, NO_NFO]):
        assert set_nfo_state(("a", "b")) == NO_NFO
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[NO_NFO, UNREACHABLE]):
        assert set_nfo_state(("a", "b")) == NO_NFO


def test_unreachable_member_leaves_all_nfo_set_untouched(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[NFO, UNREACHABLE]):
        stats = _scan(tracker, _library())
    assert stats["unreachable"] == 1
    assert _set_writes(fake) == []
    assert _fs(tracker) == {}
    tracker.close()


def test_member_whose_video_is_gone_does_not_stall_its_set(tmp_path, kodi_stubs, fake,
                                                          tmdb_api):
    """A replaced release Kodi has not cleaned yet: its file is gone, the
    other member answers without an NFO, so the set is scanned."""
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.sets.nfo_state", side_effect=[UNREACHABLE, NO_NFO]):
        stats = _scan(tracker, _library())
    assert stats["unreachable"] == 0
    assert _set_write(fake)["title"] == NL_NAME
    tracker.close()


def test_set_local_art_is_judged_by_its_folder(tmp_path, kodi_stubs, fake, tmdb_api):
    """A set has no video of its own: its local art is not tied to any
    member's file, so a member whose video is gone cannot make it
    unreachable."""
    local = "smb://nas/MovieSets/Despicable Me/poster.jpg"
    _one_set(fake, art={"poster": local})
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.scan.local_art_state", return_value=EXISTS) as check:
        _scan(tracker, _library(toggles=("movie_sets", "posters")))
    check.assert_called_once_with(local, "")
    tracker.close()


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("toggles,art", [
    (("movie_sets",), set()),
    (("movie_sets", "posters"), {"poster"}),
    (("movie_sets", "posters", "fanart"), {"poster", "fanart"}),
])
def test_set_poster_and_fanart_need_their_ticks(tmp_path, kodi_stubs, fake, tmdb_api,
                                                toggles, art):
    tmdb_api["collection"].return_value = _collection(
        posters=[Poster(TMDB_POSTER, "tmdb", "nl", (6.0, 10))],
        backdrops=[Poster(TMDB_BACKDROP, "tmdb", None, (6.0, 10))])
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library(toggles=toggles))
    write = _set_write(fake)
    assert {k for k in write if k not in ("title", "plot")} == art
    if "poster" in art:
        assert write["poster"] == TMDB_POSTER
    if "fanart" in art:
        assert write["fanart"] == TMDB_BACKDROP
    tracker.close()


def test_set_art_from_fanart_collection(tmp_path, kodi_stubs, fake, tmdb_api):
    tmdb_api["fanart"].return_value = {
        "poster": [Poster(FANART_POSTER, "fanart", "nl", (4,))]}
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    cache: dict = {}
    _scan(tracker, _library(toggles=("movie_sets", "posters"), art_source=ART_SOURCE_FANART),
          fanart_cache=cache)
    assert _set_write(fake)["poster"] == FANART_POSTER
    assert tmdb_api["fanart"].call_args.args[:2] == ("86066", None)
    assert ("collection", "86066") in cache
    tracker.close()


# ---------------------------------------------------------------------------
# Age and member collections
# ---------------------------------------------------------------------------


def test_set_age_follows_newest_member(tmp_path, kodi_stubs, fake, tmdb_api):
    recent = (date.today() - timedelta(days=10)).isoformat()
    fake.add_set(1, EN_NAME, EN_PLOT)
    fake.add_movie(1, 1, "20352", premiered="2010-07-09")
    fake.add_movie(2, 1, "93456", premiered=recent)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert tracker.get_item("set", IDENTITY)["premiered"] == recent
    # New content: the name is checked again within weeks, not a year.
    next_retry = date.fromisoformat(_fs(tracker)["title"]["next_retry"])
    assert next_retry - date.today() < timedelta(days=60)
    tracker.close()


def test_member_collection_fetched_once(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    # The movie pass already stored one member's collection.
    tracker.set_movie_collection("20352", "86066", "2026-11-01")
    _scan(tracker, _library())
    assert [c.args[0] for c in tmdb_api["member"].call_args_list] == ["93456"]
    _make_due(tracker)
    tmdb_api["member"].reset_mock()
    _scan(tracker, _library())
    tmdb_api["member"].assert_not_called()
    assert tracker.get_movie_collection("93456") == "86066"
    tracker.close()


def test_transient_collection_lookup_leaves_clock_alone(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tmdb_api["member"].side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert tracker.get_movie_collection("20352") is None
    assert tracker.get_item("set", IDENTITY) is None
    assert _fs(tracker) == {}
    tmdb_api["collection"].assert_not_called()
    assert _set_writes(fake) == []
    tracker.close()


def test_transient_member_lookup_counts_as_transient(tmp_path, kodi_stubs, fake, tmdb_api):
    """Update now tells "could not check" from "left alone" by this count."""
    _one_set(fake)
    tmdb_api["member"].side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)
    stats = _scan(tracker, _library(), reset=True, only_set=1)
    # One per member TMDB did not answer for.
    assert stats["transient"] == 2
    assert tracker.get_movie_collection("20352") is None
    assert _fs(tracker) == {}
    assert _set_writes(fake) == []
    tracker.close()


def test_transient_collection_fetch_leaves_clock_alone(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tmdb_api["collection"].side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)
    stats = _scan(tracker, _library(toggles=("movie_sets", "posters")))
    fs = _fs(tracker)
    assert all(not int(fs[f].get("fetch_count") or 0) for f in fs)
    assert stats["transient"] >= 2
    assert _set_writes(fake) == []
    tracker.close()


# ---------------------------------------------------------------------------
# Duplicate sets
# ---------------------------------------------------------------------------


def _managed_then_duplicate(fake: FakeKodi, tracker, tmdb_api, premiered=RECENT) -> None:
    """Scan the lone set (renamed to Dutch), then let Kodi's scraper file a
    new movie of the collection under the old English name."""
    fake.add_set(1, EN_NAME, EN_PLOT)
    fake.add_movie(1, 1, "20352", premiered=premiered)
    fake.add_movie(2, 1, "93456", premiered=premiered)
    _scan(tracker, _library())
    assert fake.sets[1]["title"] == NL_NAME
    fake.sets[1]["plot"] = NL_PLOT
    fake.add_set(2, EN_NAME, EN_PLOT)
    fake.add_movie(3, 2, "211672", premiered=premiered)
    _make_due(tracker)
    fake.update.reset_mock()


def test_merge_moves_duplicate_members(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    stats = _scan(tracker, _library())
    assert _movie_moves(fake) == [(3, NL_NAME)]
    assert fake.movies[3]["setid"] == 1
    assert stats["moved"] == 1
    move = tracker.get_set_move("tmdb:211672")
    assert (move["kodi_id"], move["to_setid"], move["from_name"]) == (3, 1, EN_NAME)
    # The duplicate is neither renamed nor deleted.
    assert 2 in fake.sets and _set_write(fake, 2) == {}
    tracker.close()


def test_first_scan_learns_names_and_merges(tmp_path, kodi_stubs, fake, tmdb_api):
    """Two sets of one collection on the first scan: TMDB's names are
    recorded before the merge decision, so the scraper's set is merged."""
    fake.add_set(1, NL_NAME, NL_PLOT)
    fake.add_movie(1, 1, "20352")
    fake.add_movie(2, 1, "93456")
    fake.add_set(2, EN_NAME, EN_PLOT)
    fake.add_movie(3, 2, "211672")
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert _movie_moves(fake) == [(3, NL_NAME)]
    tracker.close()


def test_user_named_set_is_never_merged(tmp_path, kodi_stubs, fake, tmdb_api):
    fake.add_set(1, "Favourites", "")
    fake.add_movie(1, 1, "20352")
    fake.add_movie(2, 1, "93456")
    fake.add_set(2, EN_NAME, EN_PLOT)
    fake.add_movie(3, 2, "211672")
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    assert _movie_moves(fake) == []
    assert _set_write(fake, 1).get("title") is None
    tracker.close()


def test_merge_puts_managed_plot_back(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    # Old movies: the set's plot settled on the first scan, so the only
    # plot write now is the one that puts it back.
    _managed_then_duplicate(fake, tracker, tmdb_api, premiered="2010-07-09")
    log = MagicMock()
    with patch.object(sets, "log", log):
        _scan(tracker, _library())
    assert fake.movies[3]["setid"] == 1
    assert fake.sets[1]["plot"] == NL_PLOT
    assert _set_writes(fake) == [{"plot": NL_PLOT}]
    assert "sets.plot_restored" in [c.kwargs["event"] for c in log.info.call_args_list]
    tracker.close()


def test_refreshed_member_is_merged_again(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    _scan(tracker, _library())
    assert fake.movies[3]["setid"] == 1
    # A refresh deletes and re-adds the movie: new movieid, old set name.
    del fake.movies[3]
    fake.add_movie(30, 2, "211672")
    _make_due(tracker)
    fake.update.reset_mock()
    _scan(tracker, _library())
    assert _movie_moves(fake) == [(30, NL_NAME)]
    move = tracker.get_set_move("tmdb:211672")
    assert (move["kodi_id"], move["locked"]) == (30, 0)
    tracker.close()


def test_user_moved_member_locked_not_moved(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    _scan(tracker, _library())
    # The user puts the same movie back in the English set by hand.
    fake.movies[3]["setid"] = 2
    _make_due(tracker)
    fake.update.reset_mock()
    _scan(tracker, _library())
    assert _movie_moves(fake) == []
    assert tracker.get_set_move("tmdb:211672")["locked"] == 1
    tracker.close()


def test_move_verified_by_setid(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    fake.land_in = 7
    log = MagicMock()
    with patch.object(sets, "log", log):
        stats = _scan(tracker, _library())
    assert _movie_moves(fake) == [(3, NL_NAME)]
    assert tracker.get_set_move("tmdb:211672") is None
    assert stats["moved"] == 0
    assert [c.kwargs["event"] for c in log.warning.call_args_list] == ["sets.move_failed"]
    tracker.close()


def test_new_only_cycle_still_merges(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    calls = tmdb_api["collection"].call_count
    stats = _scan(tracker, _library(), new_only=True)
    assert _movie_moves(fake) == [(3, NL_NAME)]
    assert stats["moved"] == 1
    # The managed set is not new, so it is not scanned.
    assert tmdb_api["collection"].call_count == calls
    tracker.close()


def test_only_set_merges_its_collection_and_scans_its_managed_set(
        tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    # Update now on the duplicate: its movie moves, the managed set is scanned.
    stats = _scan(tracker, _library(), reset=True, only_set=2)
    assert _movie_moves(fake) == [(3, NL_NAME)]
    assert stats["processed"] == 1
    tracker.close()


# ---------------------------------------------------------------------------
# Browsing, stopping and presence
# ---------------------------------------------------------------------------


class _Monitor:
    def abortRequested(self) -> bool:
        return False

    def waitForAbort(self, timeout: float = 0) -> bool:
        return False


def _record_order(fake: FakeKodi):
    """A browse hold stand-in and the list that records it beside each Kodi
    write, in order."""
    order: list[str] = []
    original = fake.update.side_effect

    def update(item_id, media_type, log_title="", **fields):
        order.append("{}:{}".format(media_type.name, ",".join(sorted(fields))))
        return original(item_id, media_type, log_title, **fields)

    fake.update.side_effect = update
    return MagicMock(side_effect=lambda *a: order.append("hold")), order


def test_set_moves_held_while_browsing(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api, premiered="2010-07-09")
    hold, order = _record_order(fake)
    with patch("resources.lib.engine.scan._hold_while_browsing", hold):
        _scan(tracker, _library(), monitor=_Monitor())
    # Held before the move; the plot restore follows the move at once.
    assert order[:3] == ["hold", "movie:set", "set:plot"]
    tracker.close()


def test_update_now_moves_without_hold(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    hold, _order = _record_order(fake)
    with patch("resources.lib.engine.scan._hold_while_browsing", hold), \
            patch("resources.lib.engine.scan._stop_requested", return_value=True):
        _scan(tracker, _library(), monitor=_Monitor(), reset=True, only_set=2)
    hold.assert_not_called()
    assert _movie_moves(fake) == [(3, NL_NAME)]
    tracker.close()


def test_stop_scan_before_a_move_ends_the_set_pass(tmp_path, kodi_stubs, fake, tmdb_api):
    tracker = _make_tracker(tmp_path)
    _managed_then_duplicate(fake, tracker, tmdb_api)
    # Not stopped when the pass starts; stopped by the time a move is due.
    stops = iter([False])
    with patch("resources.lib.engine.scan._stop_requested",
               side_effect=lambda: next(stops, True)):
        stats = _scan(tracker, _library(), monitor=_Monitor())
    assert _movie_moves(fake) == []
    assert stats["stopped"] and stats["aborted"]
    assert stats["processed"] == 0
    tracker.close()


def test_left_alone_managed_set_stays_present(tmp_path, kodi_stubs, fake, tmdb_api):
    _one_set(fake)
    tracker = _make_tracker(tmp_path)
    _scan(tracker, _library())
    tracker._conn.execute("UPDATE items SET last_seen = '2000-01-01' WHERE media_type = 'set'")
    tracker.commit()
    tracker.start_new_cycle()
    # Movie sets unticked in the set's library: the set is left alone.
    _scan(tracker, _library(toggles=("plots",)))
    tracker.prune_orphans("set", cutoff=date.today().isoformat())
    assert tracker.get_item("set", IDENTITY) is not None
    assert tracker.get_keep("set", IDENTITY)["title"]["original"] == EN_NAME
    assert EN_NAME.lower() in tracker.get_set_names(IDENTITY)
    tracker.close()

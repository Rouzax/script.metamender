"""Tests for engine/numbermatch.py: matching id-less episodes by number."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from resources.lib.data.media_types import EPISODE
from resources.lib.data.tracker import TrackerDB
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.ids import _Ids
from resources.lib.engine.numbermatch import match_episode_by_number
from resources.lib.engine.scanctx import _Pacer, _ScanCtx
from resources.lib.utils import TransientProviderError

ID = "Show S01E02"


def _ctx(tracker, reset=False, checked=True, show_tmdb: str | None = "55",
         show_tvdb: str | None = "77", tracked=True):
    return _ScanCtx(
        media_type=EPISODE, tracker=tracker, breaker=CircuitBreaker(),
        pacer=_Pacer(0), tmdb_api_key="k", default_language="en-US",
        default_language_set=True, sources=[], enabled_fields=frozenset({"title"}),
        show_id_map={10: show_tmdb} if show_tmdb else {},
        show_tvdb_map={10: show_tvdb} if show_tvdb else {},
        checked_shows=frozenset({10}) if checked else frozenset(),
        season_cache={}, tvdb_cache={}, logged_once=set(), counts={},
        schedule=(), rating_pref=0, text_order=0, fallback_order=0,
        default_fallback="", imdb_ready=False, reset=reset,
        tracked_shows=frozenset({10}) if tracked else frozenset(),
    )


def _item(title="Amsterdam Centraal", aired="2022-10-23"):
    return {"tvshowid": 10, "showtitle": "Show", "season": 1, "episode": 2,
            "title": title, "firstaired": aired, "uniqueid": {"tvmaze": "9"}}


def _ids(**kw):
    return _Ids(tmdb=kw.get("tmdb"), imdb=kw.get("imdb"), tvdb=kw.get("tvdb"),
                may_look_up=kw.get("may_look_up", True))


def _tmdb_season(rows):
    """fetch_season output: rows of (tmdb_id, number, title, aired)."""
    return [{"tmdb_id": i, "episode_number": n, "season_number": 1, "title": t,
             "firstaired": a} for i, n, t, a in rows]


def _tvdb_numbers(rows):
    """fetch_series_episode_numbers output: rows of (tvdb_id, number, title, aired)."""
    return [{"tvdb_id": i, "season": 1, "number": n, "title": t, "aired": a}
            for i, n, t, a in rows]


AGREE_TMDB = _tmdb_season([("501", 1, "Het begin", "2022-10-16"),
                           ("502", 2, "Amsterdam Centraal", "2022-10-23")])
AGREE_TVDB = _tvdb_numbers([("701", 1, "Het begin", "2022-10-16"),
                            ("702", 2, "Amsterdam Centraal", "2022-10-23")])
# The Office style shift: number 2 is a week later and titled differently.
SHIFTED_TMDB = _tmdb_season([("501", 1, "Amsterdam Centraal", "2022-10-23"),
                             ("502", 2, "Iets anders", "2022-10-30")])


@pytest.fixture
def tracker(tmp_path):
    db = TrackerDB(str(tmp_path / "t.db"))
    yield db
    db.close()


@pytest.fixture
def providers():
    """Every provider the matcher asks, answering a consistent show."""
    with patch("resources.lib.providers.tmdb.fetch_season") as season, \
            patch("resources.lib.providers.tmdb.fetch_tvshow_original_language",
                  return_value="en") as tmdb_lang, \
            patch("resources.lib.providers.tmdb.fetch_episode_external_ids",
                  return_value={"tvdb_id": "702", "imdb_id": "tt2"}) as cross, \
            patch("resources.lib.providers.tvdb.fetch_series_episode_numbers") as numbers, \
            patch("resources.lib.providers.tvdb.fetch_series",
                  return_value={"original_language": "eng"}) as series:
        season.return_value = AGREE_TMDB
        numbers.return_value = AGREE_TVDB
        yield MagicMock(season=season, tmdb_lang=tmdb_lang, cross=cross,
                        numbers=numbers, series=series)


def _run(ctx, item=None, ids=None, stored=None, folder="en-US", log=None,
         kodi_ids=(None, None, None, None)):
    ids = ids or _ids()
    with patch("resources.lib.engine.numbermatch.log", log or MagicMock()):
        match_episode_by_number(ctx, item or _item(), ID, ids, stored or {}, folder,
                                kodi_ids)
    return ids


def _events(log, level="info"):
    return [c.kwargs.get("event") for c in getattr(log, level).call_args_list]


def test_tvmaze_only_episode_matched_on_title_records_ids(tracker, providers):
    ids = _run(_ctx(tracker))
    row = tracker.get_item("episode", ID)
    assert (row["tmdb_id"], row["tvdb_id"], row["imdb_id"], row["tmdb_season"]) == (
        "502", "702", "tt2", 1)
    assert row["number_matched"] == 1
    assert (ids.tmdb, ids.tvdb, ids.imdb, ids.tmdb_season) == ("502", "702", "tt2", 1)
    assert ids.number_matched and ids.looked_up and not ids.lookup_failed
    assert (row["orig_title"], row["orig_aired"]) == ("Amsterdam Centraal", "2022-10-23")


def test_rejected_site_logs_and_records_nothing_for_it(tracker, providers):
    providers.season.return_value = SHIFTED_TMDB
    log = MagicMock()
    ids = _run(_ctx(tracker), log=log)
    row = tracker.get_item("episode", ID)
    assert row["tmdb_id"] is None
    assert row["tvdb_id"] == "702"
    assert ids.tmdb is None and ids.tvdb == "702"
    rejected = [c.kwargs for c in log.info.call_args_list
                if c.kwargs.get("event") == "scan.number_match_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["site"] == "tmdb"
    assert rejected[0]["kodi_title"] == "Amsterdam Centraal"
    assert rejected[0]["site_title"] == "Iets anders"
    assert rejected[0]["site_aired"] == "2022-10-30"
    assert not providers.cross.called


def test_conflicting_cross_id_uses_neither(tracker, providers):
    providers.cross.return_value = {"tvdb_id": "111"}
    log = MagicMock()
    ids = _run(_ctx(tracker), log=log)
    row = tracker.get_item("episode", ID)
    assert (row["tmdb_id"], row["tvdb_id"], row["number_matched"]) == (None, None, 0)
    assert not ids.any()
    assert "scan.number_match_conflict" in _events(log)
    assert ids.looked_up and not ids.lookup_failed


def test_cross_tvdb_id_used_when_tvdb_has_no_list(tracker, providers):
    providers.numbers.return_value = None
    ids = _run(_ctx(tracker))
    assert (ids.tmdb, ids.tvdb, ids.imdb) == ("502", "702", "tt2")


def test_transient_season_list_records_nothing(tracker, providers):
    providers.season.side_effect = TransientProviderError("down")
    ids = _run(_ctx(tracker))
    row = tracker.get_item("episode", ID)
    assert ids.tmdb_transient and ids.lookup_failed
    assert row["tmdb_id"] is None and row["tvdb_id"] is None
    assert row["number_matched"] == 0


def test_unresolved_show_defers(tracker, providers):
    log = MagicMock()
    ids = _run(_ctx(tracker, checked=False, show_tmdb=None, show_tvdb=None), log=log)
    assert ids.tmdb_transient and ids.tvdb_transient and ids.lookup_failed
    assert "scan.number_match_rejected" not in _events(log)
    assert not providers.season.called and not providers.numbers.called


def test_show_without_ids_is_no_route_not_transient(tracker, providers):
    """A show with no ids of its own is skipped before it reaches the
    tracker, so its lookups never answer; its episodes have no route rather
    than waiting forever."""
    ids = _run(_ctx(tracker, checked=False, show_tmdb=None, show_tvdb=None, tracked=False))
    assert not (ids.tmdb_transient or ids.tvdb_transient or ids.lookup_failed)
    assert not ids.looked_up and not ids.any()
    assert not providers.season.called and not providers.numbers.called


def test_recheck_uses_kept_originals(tracker, providers):
    tracker.upsert_item("episode", ID, tmdb_id="502", tvdb_id="702", tmdb_season=1,
                        orig_title="Amsterdam Centraal", orig_aired="2022-10-23",
                        number_matched=1)
    stored = tracker.get_item("episode", ID)
    # Kodi now shows what metamender wrote; only the kept title matches number 2.
    item = _item(title="Iets anders", aired="2022-10-30")
    ids = _run(_ctx(tracker, reset=True), item=item,
               ids=_ids(tmdb="502", tvdb="702"), stored=stored)
    assert (ids.tmdb, ids.tvdb) == ("502", "702")
    assert tracker.get_item("episode", ID)["number_matched"] == 1


def test_failed_recheck_clears_match(tracker, providers):
    tracker.upsert_item("episode", ID, tmdb_id="502", tvdb_id="702", tmdb_season=1,
                        orig_title="Amsterdam Centraal", orig_aired="2022-10-23",
                        number_matched=1)
    stored = tracker.get_item("episode", ID)
    providers.season.return_value = SHIFTED_TMDB
    providers.numbers.return_value = _tvdb_numbers([("702", 2, "Iets anders", "2022-10-30")])
    ids = _run(_ctx(tracker, reset=True), ids=_ids(tmdb="502", tvdb="702"), stored=stored)
    row = tracker.get_item("episode", ID)
    assert (row["tmdb_id"], row["tvdb_id"], row["number_matched"]) == (None, None, 0)
    assert not ids.any()


def test_stored_match_without_reset_is_not_rechecked(tracker, providers):
    stored = {"number_matched": 1, "orig_title": "x", "orig_aired": ""}
    ids = _run(_ctx(tracker), ids=_ids(tmdb="502", tvdb="702"), stored=stored)
    assert ids.number_matched
    assert not providers.season.called and not providers.numbers.called


def test_original_language_titles_count(tracker, providers):
    providers.tmdb_lang.return_value = "de"
    providers.series.return_value = {"original_language": "deu"}

    def season(show, number, key, lang):
        title = "Geheimnisse" if lang.startswith("de") else "Secrets"
        return _tmdb_season([("502", 2, title, "2017-12-01"),
                             ("503", 3, "Other", "2017-12-01")])

    def numbers(show, lang):
        return _tvdb_numbers([("702", 2, "Geheimnisse" if lang == "deu" else "Secrets",
                               "2017-12-01"), ("703", 3, "Other", "2017-12-01")])

    providers.season.side_effect = season
    providers.numbers.side_effect = numbers
    ids = _run(_ctx(tracker), item=_item(title="Geheimnisse", aired="2017-12-01"))
    assert (ids.tmdb, ids.tvdb) == ("502", "702")
    assert {c.args[3] for c in providers.season.call_args_list} == {"en-US", "de-DE"}


def test_episode_with_own_ids_is_left_alone(tracker, providers):
    ids = _run(_ctx(tracker), ids=_ids(tmdb="1"))
    assert not providers.season.called
    assert not ids.number_matched


def test_number_matched_imdb_not_written_to_kodi(tracker):
    from resources.lib.engine.fetch import _episode_cross_ids

    ids = _ids(tmdb="502")
    ids.number_matched = True
    with patch("resources.lib.providers.tmdb.fetch_episode_external_ids",
               return_value={"imdb_id": "tt9"}), \
            patch("resources.lib.data.kodi.update_item") as update:
        _episode_cross_ids(_ctx(tracker), _item(), 100, "t", ID, ids, "55", 1, 2)
    assert not update.called
    assert ids.imdb == "tt9"
    assert tracker.get_item("episode", ID)["imdb_id"] == "tt9"


def _stored_match(tracker, **kw):
    values = {"tmdb_id": "502", "tvdb_id": "702", "tmdb_season": 1,
              "orig_title": "Amsterdam Centraal", "orig_aired": "2022-10-23",
              "number_matched": 1}
    values.update(kw)
    tracker.upsert_item("episode", ID, **values)
    return tracker.get_item("episode", ID)


def test_kodi_typed_ids_clear_a_stored_number_match(tracker, providers):
    """A rescrape gave the episode real ids: the number match is dropped and
    Kodi's ids are used, on Update now and on a scheduled scan alike."""
    for reset in (True, False):
        stored = _stored_match(tracker)
        ids = _run(_ctx(tracker, reset=reset), ids=_ids(tmdb="900", tvdb="702"),
                   stored=stored, kodi_ids=("900", None, None, None))
        assert (ids.tmdb, ids.tvdb, ids.imdb) == ("900", None, None)
        assert not ids.number_matched
        assert tracker.get_item("episode", ID)["number_matched"] == 0
    assert not providers.season.called


def test_recheck_keeps_an_empty_original(tracker, providers):
    """Originals were captured with no date: the date metamender wrote later
    must not confirm the match."""
    providers.season.return_value = SHIFTED_TMDB
    providers.numbers.return_value = None
    providers.cross.return_value = {}
    stored = _stored_match(tracker, orig_title="Aflevering 2", orig_aired="", tvdb_id=None)
    ids = _run(_ctx(tracker, reset=True), item=_item(title="Iets anders", aired="2022-10-30"),
               ids=_ids(tmdb="502"), stored=stored)
    assert not ids.any()
    assert tracker.get_item("episode", ID)["number_matched"] == 0


def test_recheck_transient_keeps_number_matched(tracker, providers):
    providers.numbers.side_effect = TransientProviderError("down")
    stored = _stored_match(tracker)
    ids = _run(_ctx(tracker, reset=True), ids=_ids(tmdb="502", tvdb="702"), stored=stored)
    assert ids.number_matched and ids.lookup_failed


def test_kodi_imdb_id_is_kept(tracker, providers):
    providers.cross.return_value = {"tvdb_id": "702"}
    ids = _run(_ctx(tracker), ids=_ids(imdb="tt5"), kodi_ids=(None, "tt5", None, None))
    assert ids.imdb == "tt5" and ids.tmdb == "502"


def test_one_site_transient_marks_both(tracker, providers):
    providers.numbers.side_effect = TransientProviderError("down")
    ids = _run(_ctx(tracker), ids=_ids(imdb="tt5"), kodi_ids=(None, "tt5", None, None))
    assert ids.tmdb_transient and ids.tvdb_transient and ids.lookup_failed


def test_recheck_drops_match_when_show_lost_its_ids(tracker, providers):
    """Update now on an episode whose show now answers with neither id: the
    stored match has nothing left to rest on and is dropped."""
    stored = _stored_match(tracker)
    ids = _run(_ctx(tracker, reset=True, show_tmdb=None, show_tvdb=None),
               ids=_ids(tmdb="502", tvdb="702"), stored=stored)
    assert not ids.any() and not ids.number_matched
    assert tracker.get_item("episode", ID)["number_matched"] == 0


def test_recheck_drops_match_when_show_is_untracked(tracker, providers):
    stored = _stored_match(tracker)
    ids = _run(_ctx(tracker, reset=True, checked=False, show_tmdb=None, show_tvdb=None,
                    tracked=False), ids=_ids(tmdb="502", tvdb="702"), stored=stored)
    assert not ids.any() and not ids.number_matched
    assert tracker.get_item("episode", ID)["number_matched"] == 0


def test_rejection_writes_a_match_note_and_a_pass_clears_it(tracker, providers):
    import json

    providers.season.return_value = SHIFTED_TMDB
    providers.numbers.return_value = _tvdb_numbers([("702", 2, "Iets anders", "2022-10-30")])
    _run(_ctx(tracker))
    note = json.loads(tracker.get_item("episode", ID)["match_note"])
    assert note == [
        {"site": "tmdb", "site_title": "Iets anders", "site_aired": "2022-10-30",
         "kodi_title": "Amsterdam Centraal", "kodi_aired": "2022-10-23"},
        {"site": "tvdb", "site_title": "Iets anders", "site_aired": "2022-10-30",
         "kodi_title": "Amsterdam Centraal", "kodi_aired": "2022-10-23"},
    ]

    providers.season.return_value = AGREE_TMDB
    providers.numbers.return_value = AGREE_TVDB
    _run(_ctx(tracker, reset=True), stored=tracker.get_item("episode", ID))
    row = tracker.get_item("episode", ID)
    assert row["number_matched"] == 1
    assert row["match_note"] == ""

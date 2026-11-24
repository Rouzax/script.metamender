"""Scan tests for the art fields: episode thumbs and movie, show and season
posters."""
from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from resources.lib.data import kodi
from resources.lib.data.identity import compute_identity
from resources.lib.data.libraries import ART_SOURCE_FANART
from resources.lib.data.localart import EXISTS, MISSING, UNREACHABLE
from resources.lib.data.media_types import EPISODE, MOVIE, SEASON, TVSHOW
from resources.lib.data.nfo import NFO
from resources.lib.data.tracker import (
    REASON_CHANGED_OUTSIDE,
    REASON_FILL_ONLY,
    REASON_LOCAL_ART,
    REASON_NFO,
    REASON_NOTHING,
    REASON_OTHER_ART,
    REASON_UNCHECKED,
)
from resources.lib.engine.art import Poster
from resources.lib.engine.breaker import CircuitBreaker
from resources.lib.engine.protect import fingerprint
from resources.lib.engine.scan import run_scan, settings_fingerprint
from resources.lib.utils import FanartAuthError, TransientProviderError
from tests.conftest import one_library
from tests.test_scan import (  # noqa: F401 (autouse fixture)
    _count,
    _fs,
    _libs,
    _make_episode_item,
    _make_movie_item,
    _make_settings,
    _make_tracker,
    _make_tvshow_item,
    _mock_secondary_providers,
    _register_show,
    _tmdb_episode_data,
    _tmdb_movie_response,
    _tmdb_tvshow_response,
    _written,
)

FRAME_GRAB = "image://video@smb%3a%2f%2fserver%2fshows%2ftest%2fS01E01.mkv/"
STILL = "https://image.tmdb.org/t/p/original/still.jpg"
TVDB_IMAGE = "https://artworks.thetvdb.com/banners/episodes/1/888.jpg"
EN_POSTER = "https://image.tmdb.org/t/p/original/en.jpg"
NL_POSTER = "https://image.tmdb.org/t/p/original/nl.jpg"
USER_POSTER = "https://image.tmdb.org/t/p/original/userpick.jpg"
FANART_NL = "https://assets.fanart.tv/fanart/movies/1/movieposter/nl.jpg"

_DUTCH = {"sources": [], "default_language": "nl-NL", "fallback_language": "en-US"}
_DUTCH_ONLY = {"sources": [], "default_language": "nl-NL", "fallback_language": ""}
FANART_EN = "https://assets.fanart.tv/fanart/movies/1/movieposter/en.jpg"
BETTER_EN = "https://image.tmdb.org/t/p/original/better_en.jpg"


@pytest.fixture(autouse=True)
def _no_fanart():
    """fanart.tv answers "no posters" unless a test patches it."""
    with patch("resources.lib.providers.fanart.fetch_movie_art", return_value={}), \
         patch("resources.lib.providers.fanart.fetch_show_art",
               return_value={"show": {}, "seasons": {}}):
        yield


def _tmdb(url, lang, avg=7.0, votes=20):
    return Poster(url, "tmdb", lang, (avg, votes))


def _fanart(url, lang, likes=3):
    return Poster(url, "fanart", lang, (likes,))


def _movie_fetch(posters, **overrides):
    """tmdb.fetch_movie stand-in: posters only when images were asked for."""
    def fetch(tmdb_id, api_key, language="en-US", image_languages=()):
        data = _tmdb_movie_response(**overrides)
        if image_languages:
            data["posters"] = list(posters)
        return data
    return fetch


def _episode(thumb, **overrides):
    item = _make_episode_item()
    item["thumb"] = thumb
    item.update(overrides)
    return item


def _movie(poster, recent=False):
    item = _make_movie_item()
    item["poster"] = poster
    if recent:
        item["premiered"] = (date.today() - timedelta(days=10)).isoformat()
    return item


def _reason(fs_map, field):
    return fs_map.get(field, {}).get("last_reason")


def _make_due(tracker):
    """Start the next cycle with every field's next check passed."""
    tracker._conn.execute("UPDATE field_status SET next_retry = '2000-01-01'")
    tracker.commit()
    tracker.start_new_cycle()


def _scan_episodes(tracker, fields, items, **kwargs):
    return run_scan(
        EPISODE, tracker, _libs(frozenset(fields)), "k", _make_settings(),
        items=items, show_id_map={10: "55555"}, **kwargs,
    )


def _scan_movies(tracker, libs, items, settings=None, **kwargs):
    return run_scan(MOVIE, tracker, libs, "k", settings or _make_settings(),
                    items=items, **kwargs)


# ---------------------------------------------------------------------------
# Episode thumbs
# ---------------------------------------------------------------------------


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_frame_grab_thumb_replaced_by_tmdb_still(mock_season, mock_update, tmp_path, kodi_stubs):
    item = _episode(FRAME_GRAB)
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    fs_map = _fs(tracker, EPISODE, item)
    keep = tracker.get_keep("episode", compute_identity(item, EPISODE))
    tracker.close()

    assert _written(mock_update, "thumb") == STILL
    assert _count(fs_map, "thumb") == 1
    assert fs_map["thumb"]["source"] == "tmdb"
    assert fs_map["thumb"]["written_value"] == fingerprint(STILL)
    assert keep["thumb"]["original"] == FRAME_GRAB


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_broken_thumb_replaced(mock_season, mock_update, tmp_path, kodi_stubs):
    item = _episode("http://thetvdb.com/banners/")
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    tracker.close()

    assert _written(mock_update, "thumb") == STILL


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_local_thumb_kept_with_reason(mock_season, mock_update, tmp_path, kodi_stubs):
    item = _episode("smb://server/shows/test/S01E01-thumb.jpg")
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    _scan_episodes(tracker, {"thumb"}, [item])
    fs_map = _fs(tracker, EPISODE, item)
    tracker.close()

    mock_season.assert_not_called()
    mock_update.assert_not_called()
    assert _reason(fs_map, "thumb") == REASON_LOCAL_ART
    assert _count(fs_map, "thumb") == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_tmdb_web_thumb_not_ours_kept(mock_season, mock_update, tmp_path, kodi_stubs):
    item = _episode("https://image.tmdb.org/t/p/original/scraper.jpg", plot="")
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb", "plot"}, [item])
    fs_map = _fs(tracker, EPISODE, item)
    tracker.close()

    assert _written(mock_update, "plot") == "The beginning"
    assert _written(mock_update, "thumb") is None
    assert _reason(fs_map, "thumb") == REASON_OTHER_ART
    assert _count(fs_map, "thumb") == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tvdb.fetch_series_episodes")
@patch("resources.lib.providers.tmdb.fetch_season")
def test_thumb_falls_back_to_tvdb_image(
    mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
):
    item = _episode(FRAME_GRAB, uniqueid={"tmdb": "99999", "tvdb": "888"})
    mock_season.return_value = [_tmdb_episode_data(thumb="")]
    mock_tvdb_eps.return_value = {"888": {"title": "", "plot": "", "image": TVDB_IMAGE}}
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    fs_map = _fs(tracker, EPISODE, item)
    tracker.close()

    assert _written(mock_update, "thumb") == TVDB_IMAGE
    assert fs_map["thumb"]["source"] == "tvdb"
    assert fs_map["thumb"]["from_fallback"] == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_thumb_transient_tmdb_leaves_clock_alone(mock_season, mock_update, tmp_path, kodi_stubs):
    item = _episode(FRAME_GRAB)
    mock_season.side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    fs_map = _fs(tracker, EPISODE, item)
    tracker.close()

    mock_update.assert_not_called()
    assert _count(fs_map, "thumb") == 0
    assert _reason(fs_map, "thumb") == REASON_UNCHECKED


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_nfo_item_frame_grab_thumb_filled(mock_season, mock_update, tmp_path, kodi_stubs):
    item = _episode(FRAME_GRAB)
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    with patch("resources.lib.engine.scan.nfo_state", return_value=NFO):
        _scan_episodes(tracker, {"thumb"}, [item])
    tracker.close()

    assert _written(mock_update, "thumb") == STILL


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_nfo_item_own_thumb_still_refreshed(mock_season, mock_update, tmp_path, kodi_stubs):
    """The NFO rule lets MetaMender refresh its own value, as for text."""
    item = _episode(FRAME_GRAB)
    newer = "https://image.tmdb.org/t/p/original/newer.jpg"
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)
    with patch("resources.lib.engine.scan.nfo_state", return_value=NFO):
        _scan_episodes(tracker, {"thumb"}, [item])
        mock_update.reset_mock()
        mock_season.return_value = [_tmdb_episode_data(thumb=newer)]
        _make_due(tracker)
        _scan_episodes(tracker, {"thumb"}, [dict(item, thumb=STILL)])
    tracker.close()

    assert _written(mock_update, "thumb") == newer


def test_restore_original_puts_frame_grab_back(tmp_path, kodi_stubs):
    from resources.lib.service import details

    item = _episode(FRAME_GRAB)
    identity = compute_identity(item, EPISODE)
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)
    with patch("resources.lib.data.kodi.update_item", return_value=True), \
         patch("resources.lib.providers.tmdb.fetch_season",
               return_value=[_tmdb_episode_data(thumb=STILL)]):
        _scan_episodes(tracker, {"thumb"}, [item])

    wrapped = "image://https%3a%2f%2fimage.tmdb.org%2ft%2fp%2foriginal%2fstill.jpg/"
    now = kodi.flatten_art(dict(item, art={"thumb": wrapped}), EPISODE)
    with patch("resources.lib.data.kodi.get_item_by_id", return_value=now), \
         patch("resources.lib.data.kodi.xbmc.executeJSONRPC",
               return_value='{"result": "OK"}') as rpc:
        result = details.restore_field(tracker, EPISODE, 100, identity, "thumb")
    tracker.close()

    assert result == details.RESTORED
    params = json.loads(rpc.call_args.args[0])["params"]
    assert params == {"episodeid": 100, "art": {"thumb": FRAME_GRAB}}


# ---------------------------------------------------------------------------
# Movie collections
# ---------------------------------------------------------------------------


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_movie_pass_records_collection_id(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _make_movie_item(tmdb_id="603")
    mock_movie.return_value = _tmdb_movie_response(collection_id="2344")
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"plot"})), [item])
    tracker.close()
    tracker = _make_tracker(tmp_path)
    assert tracker.get_movie_collection("603") == "2344"
    tracker.close()


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_movie_pass_records_no_collection(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _make_movie_item(tmdb_id="550")
    mock_movie.return_value = _tmdb_movie_response(collection_id="")
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"plot"})), [item])
    assert tracker.get_movie_collection("550") == ""
    tracker.close()


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_transient_movie_answer_records_no_collection(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _make_movie_item(tmdb_id="603")
    mock_movie.side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"plot"})), [item])
    assert tracker.get_movie_collection("603") is None
    tracker.close()


def test_set_candidate_fields():
    from resources.lib.data.media_types import SET
    from resources.lib.engine.scan import candidate_fields, item_fields
    assert candidate_fields(SET, frozenset({"plot", "poster", "main_title"})) == []
    assert candidate_fields(SET, frozenset({"movie_set"})) == ["plot", "title"]
    fields = frozenset({"movie_set", "poster", "fanart", "clearlogo", "characterart",
                        "rating"})
    assert candidate_fields(SET, fields) == ["clearlogo", "fanart", "plot", "poster", "title"]
    assert item_fields(["plot", "title"], SET, "collection:86066") == ["plot", "title"]


def test_scans_sets_needs_an_included_library_with_movie_sets():
    from resources.lib.engine.scan import scans_sets
    assert scans_sets(one_library({"movie_set"})) is True
    assert scans_sets(one_library({"movie_set"}, included=False)) is False
    assert scans_sets(one_library({"plot", "poster"})) is False


# ---------------------------------------------------------------------------
# Posters
# ---------------------------------------------------------------------------


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_poster_first_sight_swaps_to_best_language_poster(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie(EN_POSTER)
    mock_movie.side_effect = _movie_fetch([
        _tmdb(EN_POSTER, "en", 8.0, 50), _tmdb(NL_POSTER, "nl", 6.5, 4),
    ])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    identity = compute_identity(item, MOVIE)
    first = tracker.get_first_fp("movie", identity, "poster")
    tracker.close()

    assert mock_movie.call_args.kwargs["image_languages"] == ("nl", "en")
    assert _written(mock_update, "poster") == NL_POSTER
    assert fs_map["poster"]["source"] == "tmdb"
    assert fs_map["poster"]["from_fallback"] == 0
    assert first == fingerprint(EN_POSTER)


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_written_poster_is_held_on_next_scan(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(EN_POSTER, recent=True)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)
    libs = _libs(frozenset({"poster"}), _DUTCH)
    _scan_movies(tracker, libs, [item])
    assert _written(mock_update, "poster") == NL_POSTER
    mock_update.reset_mock()

    # Kodi hands the written URL back wrapped.
    wrapped = "image://https%3a%2f%2fimage.tmdb.org%2ft%2fp%2foriginal%2fnl.jpg/"
    again = kodi.flatten_art(dict(item, art={"poster": wrapped}), MOVIE)
    _make_due(tracker)
    _scan_movies(tracker, libs, [again])
    fs_map = _fs(tracker, MOVIE, item)
    locked = tracker.locked_fields("movie", compute_identity(item, MOVIE))
    tracker.close()

    assert mock_movie.call_count == 2
    mock_update.assert_not_called()
    assert _count(fs_map, "poster") == 2
    assert fs_map["poster"]["last_reason"] == "unchanged"
    assert "poster" not in locked


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_poster_changed_outside_locks_field(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(EN_POSTER, recent=True)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)
    libs = _libs(frozenset({"poster"}), _DUTCH)
    identity = compute_identity(item, MOVIE)
    _scan_movies(tracker, libs, [item])
    mock_update.reset_mock()
    mock_movie.reset_mock()

    picked = dict(item, poster=USER_POSTER)
    _make_due(tracker)
    _scan_movies(tracker, libs, [picked])
    locked = tracker.locked_fields("movie", identity)
    reason = _reason(_fs(tracker, MOVIE, item), "poster")
    # Still the user's on the scans after.
    _make_due(tracker)
    _scan_movies(tracker, libs, [picked])
    later = _reason(_fs(tracker, MOVIE, item), "poster")
    tracker.close()

    mock_update.assert_not_called()
    mock_movie.assert_not_called()
    assert "poster" in locked
    assert reason == REASON_CHANGED_OUTSIDE
    assert later == REASON_CHANGED_OUTSIDE


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_local_poster_never_fetched(mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs):
    item = _movie("smb://server/movies/poster.jpg")
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster", "plot"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_movie.call_count == 1
    assert "image_languages" not in mock_movie.call_args.kwargs
    mock_fanart.assert_not_called()
    assert _written(mock_update, "plot") == "Great movie"
    assert _written(mock_update, "poster") is None
    assert _reason(fs_map, "poster") == REASON_LOCAL_ART


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_nfo_item_web_poster_kept_without_fetch(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _movie(EN_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)

    with patch("resources.lib.engine.scan.nfo_state", return_value=NFO):
        _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    mock_movie.assert_not_called()
    mock_fanart.assert_not_called()
    mock_update.assert_not_called()
    assert _reason(fs_map, "poster") == REASON_NFO
    assert _count(fs_map, "poster") == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_poster_no_candidate_keeps_current_and_advances_clock(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie(EN_POSTER)
    mock_movie.side_effect = _movie_fetch([])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    mock_update.assert_not_called()
    assert _count(fs_map, "poster") == 1
    assert _reason(fs_map, "poster") == REASON_NOTHING


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart_transient_leaves_poster_clock_alone(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _movie(EN_POSTER)
    item["plot"] = ""
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    mock_fanart.side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster", "plot"}), language="nl-NL",
                       fallback_language="en-US", art_source=ART_SOURCE_FANART)

    _scan_movies(tracker, libs, [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_fanart.called
    assert _written(mock_update, "poster") is None
    assert _written(mock_update, "plot") == "Great movie"
    assert _count(fs_map, "poster") == 0
    assert _reason(fs_map, "poster") == REASON_UNCHECKED
    assert _count(fs_map, "plot") == 1


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart_auth_failure_does_not_stop_scan(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    first = _movie("")
    first["plot"] = ""
    second = dict(_make_movie_item(title="Other", tmdb_id="67890", movie_id=2), poster="")
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    mock_fanart.side_effect = FanartAuthError("rejected")
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster", "plot"}), language="nl-NL",
                       fallback_language="en-US", art_source=ART_SOURCE_FANART)
    breaker = CircuitBreaker()

    stats = _scan_movies(tracker, libs, [first, second], breaker=breaker)
    first_fs = _fs(tracker, MOVIE, first)
    second_fs = _fs(tracker, MOVIE, second)
    tracker.close()

    # One rejected call; the second movie does not ask again.
    assert mock_fanart.call_count == 1
    assert breaker.auth_rejected() == ["fanart"]
    assert not breaker.is_disabled("tmdb")
    assert stats["aborted"] is False
    assert stats["tvdb_auth_failed"] is False
    # The TMDB-only field is written; posters needing fanart.tv wait.
    assert [c.kwargs.get("plot") for c in mock_update.call_args_list] == [
        "Great movie", "Great movie"]
    assert _written(mock_update, "poster") is None
    assert _count(first_fs, "plot") == 1
    assert _count(first_fs, "poster") == 0
    assert _count(second_fs, "poster") == 0
    assert _reason(first_fs, "poster") == REASON_UNCHECKED


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart_auth_failure_leaves_tmdb_answered_pick_alone(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    """A TMDB-source library whose language tier TMDB answers never needs
    fanart.tv, so a rejection earlier in the cycle changes nothing."""
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    breaker = CircuitBreaker()
    breaker.disable("fanart", auth=True)
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item], breaker=breaker)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    mock_fanart.assert_not_called()
    assert _written(mock_update, "poster") == NL_POSTER
    assert _count(fs_map, "poster") == 1


@patch("resources.lib.data.kodi.update_item", return_value=False)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_rejected_poster_write_leaves_clock_and_provenance(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie(EN_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    keep = tracker.get_keep("movie", compute_identity(item, MOVIE))
    tracker.close()

    assert mock_update.call_args.kwargs["poster"] == NL_POSTER
    assert _count(fs_map, "poster") == 0
    assert (fs_map.get("poster") or {}).get("written_value") is None
    assert (keep.get("poster") or {}).get("written_fp") is None


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart_source_poster_preferred(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    mock_fanart.return_value = {"poster": [_fanart(FANART_NL, "nl")]}
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster"}), language="nl-NL",
                       fallback_language="en-US", art_source=ART_SOURCE_FANART)
    settings = dict(_make_settings(), fanart_client_key="personal")

    _scan_movies(tracker, libs, [item], settings=settings)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_fanart.call_args.args[0] == "12345"
    assert mock_fanart.call_args.args[3] == "personal"
    assert _written(mock_update, "poster") == FANART_NL
    assert fs_map["poster"]["source"] == "fanart"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fallback_language_poster_rechecked_later(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie("")  # old title: FINAL after its first sample
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)
    libs = _libs(frozenset({"poster"}), _DUTCH)

    _scan_movies(tracker, libs, [item])
    fs_map = _fs(tracker, MOVIE, item)
    assert _written(mock_update, "poster") == EN_POSTER
    assert fs_map["poster"]["from_fallback"] == 1
    assert fs_map["poster"]["next_retry"] > date.today().isoformat()

    mock_update.reset_mock()
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en"), _tmdb(NL_POSTER, "nl")])
    _make_due(tracker)
    _scan_movies(tracker, libs, [dict(item, poster=EN_POSTER)])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _written(mock_update, "poster") == NL_POSTER
    assert fs_map["poster"]["from_fallback"] == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_unconfigured_language_poster_only_fills(mock_movie, mock_update, tmp_path, kodi_stubs):
    filled = _movie("")
    kept = dict(_make_movie_item(title="Other", tmdb_id="67890", movie_id=2), poster=EN_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "en")])
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster"}), language="")
    settings = dict(_make_settings(), ui_language="en-US")

    _scan_movies(tracker, libs, [filled, kept], settings=settings)
    kept_fs = _fs(tracker, MOVIE, kept)
    tracker.close()

    posters = [c.kwargs.get("poster") for c in mock_update.call_args_list]
    assert posters == [NL_POSTER]
    assert _reason(kept_fs, "poster") == REASON_FILL_ONLY


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_unconfigured_language_web_poster_not_fetched(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    """A poster fill-only could not replace costs no images request."""
    item = _movie(EN_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "en")])
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster", "plot"}), language="",
                       art_source=ART_SOURCE_FANART)
    settings = dict(_make_settings(), ui_language="en-US")

    _scan_movies(tracker, libs, [item], settings=settings)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_movie.call_count == 1
    assert "image_languages" not in mock_movie.call_args.kwargs
    mock_fanart.assert_not_called()
    assert _written(mock_update, "poster") is None
    assert _reason(fs_map, "poster") == REASON_FILL_ONLY
    assert _count(fs_map, "poster") == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_show_art")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_show_poster_uses_fanart_show_posters(
    mock_show, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _make_tvshow_item()
    item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
    item["poster"] = ""
    url = "https://assets.fanart.tv/fanart/tv/777/tvposter/en.jpg"
    mock_show.return_value = dict(_tmdb_tvshow_response(), posters=[])
    mock_fanart.return_value = {"show": {"poster": [_fanart(url, "en")]}, "seasons": {}}
    tracker = _make_tracker(tmp_path)

    run_scan(TVSHOW, tracker, _libs(frozenset({"poster"})), "k", _make_settings(),
             items=[item])
    tracker.close()

    assert mock_show.call_args.kwargs["image_languages"] == ("en",)
    assert mock_fanart.call_args.args[0] == "777"
    assert _written(mock_update, "poster") == url


def test_settings_fingerprint_art_fields():
    settings = {"art_source": "fanart"}
    assert json.loads(settings_fingerprint("thumb", settings, "nl-NL", "episode", "en")) == {}
    assert json.loads(settings_fingerprint("poster", settings, "nl-NL", "movie", "en")) == {
        "art_source": "fanart", "folder_language": "nl-NL", "fallback_language": "en",
    }


# ---------------------------------------------------------------------------
# Season posters
# ---------------------------------------------------------------------------

SEASON_POSTER = "https://image.tmdb.org/t/p/original/season1.jpg"
SPECIALS_POSTER = "https://image.tmdb.org/t/p/original/specials.jpg"
FANART_SEASON = "https://assets.fanart.tv/fanart/tv/777/seasonposter/en.jpg"
SHOW_FOLDER = "smb://server/shows/test/"


def _season(number=1, poster="", **overrides):
    """A season as kodi.get_seasons_for_scan lists it."""
    item = {
        "seasonid": 50 + number, "season": number, "showtitle": "Test Show",
        "tvshowid": 10, "title": "Season {}".format(number), "poster": poster,
        "art": {}, "file": SHOW_FOLDER, "show_tmdb_id": "55555",
        "show_tvdb_id": "777",
    }
    item.update(overrides)
    return item


def _scan_seasons(tracker, items, libs=None, settings=None, **kwargs):
    return run_scan(SEASON, tracker, libs or _libs(frozenset({"poster"})), "k",
                    settings or _make_settings(), items=items,
                    show_id_map={10: "55555"}, **kwargs)


def _season_fs(tracker, number):
    return tracker.get_field_status_map("season", "tmdb:55555/S{:02d}".format(number))


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_without_show_tmdb_id_is_skipped(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.engine.scan.log") as log:
        stats = _scan_seasons(tracker, [_season(show_tmdb_id=None)])
    rows = tracker._conn.execute(
        "SELECT COUNT(*) FROM items WHERE media_type = 'season'").fetchone()[0]
    tracker.close()

    assert stats["skipped"] == 1 and stats["processed"] == 0
    assert rows == 0
    mock_show.assert_not_called()
    mock_posters.assert_not_called()
    mock_update.assert_not_called()
    events = [c.kwargs.get("event") for c in log.debug.call_args_list]
    assert "scan.season_no_show_id" in events


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_empty_season_poster_filled(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    from resources.lib.engine import scan as scan_module

    mock_show.return_value = dict(_tmdb_tvshow_response(), posters=[],
                                  season_air_dates={1: "2025-01-01"})
    mock_posters.return_value = [_tmdb(SEASON_POSTER, "en")]
    tracker = _make_tracker(tmp_path)

    stats = _scan_seasons(tracker, [_season(1), _season(2)])
    fs_map = _season_fs(tracker, 1)
    keep = tracker.get_keep("season", "tmdb:55555/S01")
    tracker.close()

    # One show record for both seasons of the show.
    assert mock_show.call_count == 1
    assert mock_posters.call_args_list[0].args == ("55555", 1, "k", ("en",))
    first = mock_update.call_args_list[0]
    assert first.args[0] == 51 and first.args[1] is SEASON
    assert first.kwargs == {"poster": SEASON_POSTER}
    assert stats["processed"] == 2
    assert _count(fs_map, "poster") == 1
    assert fs_map["poster"]["source"] == "tmdb"
    assert keep["poster"]["original"] == ""
    # A season's NFO is its show's tvshow.nfo.
    scan_module.nfo_state.assert_called_with("tvshow", SHOW_FOLDER)


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_specials_season_gets_poster(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    mock_show.return_value = dict(_tmdb_tvshow_response(), season_air_dates={})
    mock_posters.return_value = [_tmdb(SPECIALS_POSTER, "en")]
    tracker = _make_tracker(tmp_path)

    _scan_seasons(tracker, [_season(0)])
    fs_map = _season_fs(tracker, 0)
    tracker.close()

    assert mock_posters.call_args.args[1] == 0
    assert mock_update.call_args.args[0] == 50
    assert _written(mock_update, "poster") == SPECIALS_POSTER
    assert _count(fs_map, "poster") == 1


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_show_art")
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_poster_from_fanart_when_source_fanart(
    mock_show, mock_posters, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    mock_show.return_value = dict(_tmdb_tvshow_response(), season_air_dates={})
    mock_posters.return_value = [_tmdb(SEASON_POSTER, "en")]
    mock_fanart.return_value = {
        "show": {"poster": [_fanart("https://assets.fanart.tv/show.jpg", "en")]},
        "seasons": {1: [_fanart(FANART_SEASON, "en")],
                    2: [_fanart("https://assets.fanart.tv/s2.jpg", "en")]},
    }
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster"}), art_source=ART_SOURCE_FANART)

    _scan_seasons(tracker, [_season(1), _season(2)], libs=libs)
    fs_map = _season_fs(tracker, 1)
    tracker.close()

    # One fanart.tv call covers every season of the show.
    assert mock_fanart.call_count == 1
    assert mock_fanart.call_args.args[0] == "777"
    assert mock_update.call_args_list[0].kwargs == {"poster": FANART_SEASON}
    assert mock_update.call_args_list[1].kwargs == {
        "poster": "https://assets.fanart.tv/s2.jpg"}
    assert fs_map["poster"]["source"] == "fanart"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_content_age_from_tmdb_air_date(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    recent = (date.today() - timedelta(days=10)).isoformat()
    mock_show.return_value = dict(_tmdb_tvshow_response(),
                                  season_air_dates={1: "2010-01-01", 2: recent})
    mock_posters.return_value = [_tmdb(SEASON_POSTER, "en")]
    tracker = _make_tracker(tmp_path)

    _scan_seasons(tracker, [_season(1), _season(2), _season(3)])
    old = tracker.get_item("season", "tmdb:55555/S01")
    young = tracker.get_item("season", "tmdb:55555/S02")
    undated = tracker.get_item("season", "tmdb:55555/S03")
    assert old["premiered"] == "2010-01-01"
    assert young["premiered"] == recent
    assert not undated["premiered"]

    # Next cycle, every check date passed: the old season is settled; the
    # young one and the one without a date (age unknown) are sampled again.
    mock_posters.reset_mock()
    _make_due(tracker)
    _scan_seasons(tracker, [_season(1, poster=SEASON_POSTER),
                            _season(2, poster=SEASON_POSTER),
                            _season(3, poster=SEASON_POSTER)])
    counts = [_count(_season_fs(tracker, n), "poster") for n in (1, 2, 3)]
    tracker.close()

    assert [c.args[1] for c in mock_posters.call_args_list] == [2, 3]
    assert counts == [1, 2, 2]


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_transient_tmdb_leaves_clock_alone(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    mock_show.side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)

    stats = _scan_seasons(tracker, [_season(1)])
    fs_map = _season_fs(tracker, 1)
    tracker.close()

    mock_posters.assert_not_called()
    mock_update.assert_not_called()
    assert stats["transient"] == 1
    assert _count(fs_map, "poster") == 0
    assert _reason(fs_map, "poster") == REASON_UNCHECKED


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_listing_new_only_skips_known_seasons(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    mock_show.return_value = dict(_tmdb_tvshow_response(), season_air_dates={})
    mock_posters.return_value = [_tmdb(SEASON_POSTER, "en")]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker, tvdb_id="777", tmdb_id="55555")
    tracker.upsert_item("season", "tmdb:55555/S01", kodi_id=51)
    tracker.commit()

    with patch("resources.lib.data.kodi.get_seasons_for_scan",
               return_value=[_season(1), _season(2)]) as listing:
        run_scan(SEASON, tracker, _libs(frozenset({"poster"})), "k", _make_settings(),
                 show_id_map={10: "55555"}, new_only=True)
    tracker.close()

    assert listing.call_args.args == ({10: "55555"},)
    assert listing.call_args.kwargs == {"show_tvdb_map": {10: "777"}}
    assert [c.args[0] for c in mock_update.call_args_list] == [52]


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_show_art")
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_show_and_season_passes_share_one_fanart_call(
    mock_show, mock_posters, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    show = _make_tvshow_item(tmdb_id="55555")
    show["uniqueid"]["tvdb"] = "777"
    show["poster"] = ""
    mock_show.return_value = dict(_tmdb_tvshow_response(), posters=[],
                                  season_air_dates={1: "2025-01-01"})
    mock_posters.return_value = []
    mock_fanart.return_value = {
        "show": {"poster": [_fanart("https://assets.fanart.tv/show.jpg", "en")]},
        "seasons": {1: [_fanart(FANART_SEASON, "en")]},
    }
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"poster"}), art_source=ART_SOURCE_FANART)
    caches = {"fanart_cache": {}, "show_cache": {}}

    run_scan(TVSHOW, tracker, libs, "k", _make_settings(), items=[show], **caches)
    _scan_seasons(tracker, [_season(1)], libs=libs, **caches)
    premiered = tracker.get_item("season", "tmdb:55555/S01")["premiered"]
    tracker.close()

    assert mock_fanart.call_count == 1
    assert mock_show.call_count == 1
    assert premiered == "2025-01-01"
    assert [c.kwargs for c in mock_update.call_args_list] == [
        {"poster": "https://assets.fanart.tv/show.jpg"}, {"poster": FANART_SEASON}]


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_seasons_of_two_copies_of_a_show_both_written(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    mock_show.return_value = dict(_tmdb_tvshow_response(), season_air_dates={})
    mock_posters.return_value = [_tmdb(SEASON_POSTER, "en")]
    first = _season(1)
    second = _season(1, seasonid=61, tvshowid=11, file="smb://server/shows/copy/")
    holder = {k: first[k] for k in ("seasonid", "season", "showtitle", "tvshowid", "title")}
    tracker = _make_tracker(tmp_path)

    with patch("resources.lib.engine.scan.get_item_by_id", return_value=dict(holder)):
        for _ in range(2):  # the second cycle meets both rows again
            run_scan(SEASON, tracker, _libs(frozenset({"poster"})), "k", _make_settings(),
                     items=[dict(first), dict(second)],
                     show_id_map={10: "55555", 11: "55555"})
            tracker.start_new_cycle()
    rows = sorted(r[0] for r in tracker._conn.execute(
        "SELECT identity FROM items WHERE media_type = 'season'"))
    tracker.close()

    assert rows == ["tmdb:55555/S01", "tmdb:55555/S01|smb://server/shows/copy/"]
    assert [c.args[0] for c in mock_update.call_args_list][:2] == [51, 61]


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_posters_transient_after_show_record_leaves_clock_alone(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    mock_show.return_value = dict(_tmdb_tvshow_response(), season_air_dates={1: "2025-01-01"})
    mock_posters.side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)

    stats = _scan_seasons(tracker, [_season(1)])
    fs_map = _season_fs(tracker, 1)
    premiered = tracker.get_item("season", "tmdb:55555/S01")["premiered"]
    tracker.close()

    mock_update.assert_not_called()
    assert stats["transient"] == 1
    assert _count(fs_map, "poster") == 0
    assert _reason(fs_map, "poster") == REASON_UNCHECKED
    # The air date is a real answer and is kept.
    assert premiered == "2025-01-01"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow", return_value=None)
def test_season_of_a_show_tmdb_does_not_have_is_answered_empty(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    tracker = _make_tracker(tmp_path)

    _scan_seasons(tracker, [_season(1)])
    fs_map = _season_fs(tracker, 1)
    tracker.close()

    mock_posters.assert_not_called()
    mock_update.assert_not_called()
    assert _count(fs_map, "poster") == 1
    assert _reason(fs_map, "poster") == REASON_NOTHING


@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_fetch_season_with_tmdb_disabled_is_transient(mock_show, mock_posters):
    from unittest.mock import MagicMock

    from resources.lib.engine.chain import TRANSIENT
    from resources.lib.engine.fetch import _fetch_season
    from resources.lib.engine.ids import _Ids
    from resources.lib.engine.scanctx import _Pacer, _ScanCtx

    breaker = CircuitBreaker()
    breaker.disable("tmdb")
    ctx = _ScanCtx(
        media_type=SEASON, tracker=MagicMock(), breaker=breaker, pacer=_Pacer(0.0),
        tmdb_api_key="k", enabled_fields=frozenset({"poster"}), show_id_map={},
        show_tvdb_map={}, checked_shows=frozenset(), season_cache={}, tvdb_cache={},
        logged_once=set(), counts={}, schedule=(7,), rating_pref=0, text_order=0,
        fallback_order=0, imdb_ready=True,
    )
    primary = _fetch_season(ctx, _season(1), 51, "Test Show S01", "tmdb:55555/S01",
                            _Ids("55555", None, "777", False), "en-US")

    assert primary.tmdb is TRANSIENT
    mock_show.assert_not_called()
    mock_posters.assert_not_called()
    ctx.tracker.upsert_item.assert_not_called()


@pytest.mark.parametrize("fields,media_type,expected", [
    ({"plot"}, MOVIE, False),
    ({"plot", "poster"}, MOVIE, True),
    ({"plot"}, EPISODE, False),
    ({"thumb"}, EPISODE, True),
    # A poster library does not manage an episode's art.
    ({"poster"}, EPISODE, False),
])
def test_scan_listing_requests_art_only_when_the_pass_manages_it(
    fields, media_type, expected, tmp_path, kodi_stubs
):
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.data.kodi.get_all_items", return_value=[]) as listing:
        run_scan(media_type, tracker, _libs(frozenset(fields)), "k", _make_settings())
    tracker.close()
    assert listing.call_args.kwargs == {"art": expected}


def test_new_items_scan_details_follow_the_art_choice(tmp_path, kodi_stubs):
    tracker = _make_tracker(tmp_path)
    with patch("resources.lib.data.kodi.get_all_items",
               return_value=[{"movieid": 9}]) as listing, \
         patch("resources.lib.engine.scan.get_item_by_id", return_value=None) as details:
        run_scan(MOVIE, tracker, _libs(frozenset({"plot"})), "k", _make_settings(),
                 new_only=True)
    tracker.close()
    assert listing.call_args.kwargs == {"ids_only": True}
    assert details.call_args.kwargs == {"art": False}


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season")
def test_nfo_item_own_thumb_refreshed_after_reset_tracker(
    mock_season, mock_update, tmp_path, kodi_stubs
):
    """field_keep's written_fp outlives Reset tracker, so MetaMender's own
    art on an NFO item is still its own."""
    item = _episode(FRAME_GRAB)
    newer = "https://image.tmdb.org/t/p/original/newer.jpg"
    mock_season.return_value = [_tmdb_episode_data(thumb=STILL)]
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)
    with patch("resources.lib.engine.scan.nfo_state", return_value=NFO):
        _scan_episodes(tracker, {"thumb"}, [item])
        mock_update.reset_mock()
        tracker.clear_field_status()
        tracker.start_new_cycle()
        mock_season.return_value = [_tmdb_episode_data(thumb=newer)]
        _scan_episodes(tracker, {"thumb"}, [dict(item, thumb=STILL)])
    tracker.close()

    assert _written(mock_update, "thumb") == newer


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tvdb.fetch_series_episodes")
@patch("resources.lib.providers.tmdb.fetch_season")
def test_broken_tvdb_image_never_replaces_a_frame_grab(
    mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
):
    item = _episode(FRAME_GRAB, uniqueid={"tmdb": "99999", "tvdb": "888"})
    mock_season.return_value = [_tmdb_episode_data(thumb="")]
    mock_tvdb_eps.return_value = {
        "888": {"title": "", "plot": "", "image": "https://artworks.thetvdb.com/banners/"},
    }
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    fs_map = _fs(tracker, EPISODE, item)
    tracker.close()

    assert _written(mock_update, "thumb") is None
    assert _reason(fs_map, "thumb") == REASON_NOTHING


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tvdb.fetch_series_episodes")
@patch("resources.lib.providers.tmdb.fetch_season")
def test_broken_tmdb_still_falls_through_to_tvdb(
    mock_season, mock_tvdb_eps, mock_update, tmp_path, kodi_stubs
):
    item = _episode(FRAME_GRAB, uniqueid={"tmdb": "99999", "tvdb": "888"})
    mock_season.return_value = [_tmdb_episode_data(thumb="https://image.tmdb.org/t/p/original/")]
    mock_tvdb_eps.return_value = {"888": {"title": "", "plot": "", "image": TVDB_IMAGE}}
    tracker = _make_tracker(tmp_path)
    _register_show(tracker)

    _scan_episodes(tracker, {"thumb"}, [item])
    tracker.close()

    assert _written(mock_update, "thumb") == TVDB_IMAGE


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_textless_art_source_records_it(mock_movie, mock_update, tmp_path, kodi_stubs):
    textless = "https://image.tmdb.org/t/p/original/textless.jpg"
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(textless, None)])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _written(mock_update, "poster") == textless
    assert fs_map["poster"]["source"] == "tmdb:textless"
    assert fs_map["poster"]["from_fallback"] == 1


# ---------------------------------------------------------------------------
# English as a last resort
# ---------------------------------------------------------------------------


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_english_fills_empty_poster_in_dutch_library_without_fallback(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH_ONLY), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_movie.call_args.kwargs["image_languages"] == ("nl", "en")
    assert _written(mock_update, "poster") == EN_POSTER
    assert fs_map["poster"]["source"] == "tmdb:english"
    assert fs_map["poster"]["from_fallback"] == 1


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_english_last_resort_never_replaces_existing_art(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _movie(FANART_NL)
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    mock_fanart.return_value = {"poster": [_fanart(FANART_EN, "en", likes=9)]}
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH_ONLY), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert not mock_update.called
    assert _reason(fs_map, "poster") == REASON_NOTHING


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_english_last_resort_also_after_a_fallback_language(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)
    german = dict(_DUTCH_ONLY, fallback_language="de-DE")

    _scan_movies(tracker, _libs(frozenset({"poster"}), german), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_movie.call_args.kwargs["image_languages"] == ("nl", "de", "en")
    assert _written(mock_update, "poster") == EN_POSTER
    assert fs_map["poster"]["source"] == "tmdb:english"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_english_pick_replaced_when_library_language_appears(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)
    libs = _libs(frozenset({"poster"}), _DUTCH_ONLY)
    _scan_movies(tracker, libs, [item])
    assert _written(mock_update, "poster") == EN_POSTER
    mock_update.reset_mock()

    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en"), _tmdb(NL_POSTER, "nl")])
    _make_due(tracker)
    _scan_movies(tracker, libs, [dict(item, poster=EN_POSTER)])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _written(mock_update, "poster") == NL_POSTER
    assert fs_map["poster"]["source"] == "tmdb"
    assert fs_map["poster"]["from_fallback"] == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_english_pick_may_move_to_better_english(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en", 5.0, 2)])
    tracker = _make_tracker(tmp_path)
    libs = _libs(frozenset({"poster"}), _DUTCH_ONLY)
    _scan_movies(tracker, libs, [item])
    assert _written(mock_update, "poster") == EN_POSTER
    mock_update.reset_mock()

    mock_movie.side_effect = _movie_fetch([
        _tmdb(EN_POSTER, "en", 5.0, 2), _tmdb(BETTER_EN, "en", 9.0, 200),
    ])
    _make_due(tracker)
    _scan_movies(tracker, libs, [dict(item, poster=EN_POSTER)])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _written(mock_update, "poster") == BETTER_EN
    assert fs_map["poster"]["source"] == "tmdb:english"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_english_pick_changed_outside_is_not_ours_any_more(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    """A poster set after MetaMender's English pick is existing art: the
    English step no longer applies to it."""
    item = _movie("")
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)
    libs = _libs(frozenset({"poster"}), _DUTCH_ONLY)
    _scan_movies(tracker, libs, [item])
    mock_update.reset_mock()

    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en"), _tmdb(BETTER_EN, "en")])
    _make_due(tracker)
    _scan_movies(tracker, libs, [dict(item, poster=FANART_NL)])
    tracker.close()

    assert _written(mock_update, "poster") is None


def test_english_allowed_rules():
    from resources.lib.engine.scan import _english_allowed

    ours = {"source": "tmdb:english", "written_value": fingerprint(EN_POSTER)}
    assert _english_allowed("poster", {"poster": ""}, {}, {}, set())
    assert _english_allowed("poster", {"poster": FRAME_GRAB}, {}, {}, set())
    assert _english_allowed("poster", {"poster": "smb://nas/poster.jpg"}, {}, {}, {"poster"})
    assert not _english_allowed("poster", {"poster": "smb://nas/poster.jpg"}, {}, {}, set())
    assert not _english_allowed("poster", {"poster": EN_POSTER}, {}, {}, set())
    assert _english_allowed("poster", {"poster": EN_POSTER}, ours, {}, set())
    # A pick MetaMender wrote in another language is not an English pick.
    assert not _english_allowed(
        "poster", {"poster": EN_POSTER}, dict(ours, source="tmdb"), {}, set())
    # Kodi holds something else than the English pick.
    assert not _english_allowed("poster", {"poster": NL_POSTER}, ours, {}, set())
    # field_keep's written_fp outlives field_status's.
    assert _english_allowed(
        "poster", {"poster": NL_POSTER}, ours, {"written_fp": fingerprint(NL_POSTER)}, set())


# ---------------------------------------------------------------------------
# Fanart and other art
# ---------------------------------------------------------------------------

NONE_BD = "https://image.tmdb.org/t/p/original/textless_bd.jpg"
NL_BD = "https://image.tmdb.org/t/p/original/nl_bd.jpg"
EN_LOGO = "https://image.tmdb.org/t/p/original/en_logo.png"
FANART_HD_LOGO = "https://assets.fanart.tv/fanart/movies/1/hdmovielogo/nl.png"
FANART_SD_LOGO = "https://assets.fanart.tv/fanart/movies/1/movielogo/nl.png"
FANART_BANNER = "https://assets.fanart.tv/fanart/movies/1/moviebanner/nl.jpg"
_OTHER = frozenset({
    "clearlogo", "keyart", "landscape", "banner", "discart", "clearart", "characterart",
})


def _art_fetch(**images):
    """tmdb.fetch_movie / fetch_tvshow stand-in: the given image lists
    (posters, backdrops, logos) only when images were asked for."""
    def fetch(tmdb_id, api_key, language="en-US", image_languages=()):
        data = _tmdb_movie_response()
        if image_languages:
            for key in ("posters", "backdrops", "logos"):
                data[key] = list(images.get(key, ()))
        return data
    return fetch


def _art_movie(recent=False, **art):
    item = _movie("", recent=recent)
    item.update(art)
    return item


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart_textless_backdrop_written(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _art_movie(fanart="")
    mock_movie.side_effect = _art_fetch(
        posters=[_tmdb(NL_POSTER, "nl", 9.0, 100)],
        backdrops=[_tmdb(NL_BD, "nl", 9.0, 100), _tmdb(NONE_BD, None, 6.0, 5)],
    )
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"fanart"}), _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_movie.call_args.kwargs["image_languages"] == ("nl", "en")
    assert mock_update.call_args.kwargs == {"fanart": NONE_BD}
    assert fs_map["fanart"]["source"] == "tmdb:textless"
    # A backdrop without text is fanart's first choice, not a fallback.
    assert fs_map["fanart"]["from_fallback"] == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart1_never_touched(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = kodi.flatten_art(dict(_make_movie_item(), art={
        "fanart1": "smb://server/movies/fanart1.jpg",
    }), MOVIE)
    mock_movie.side_effect = _art_fetch(backdrops=[_tmdb(NONE_BD, None)])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(frozenset({"fanart"}), _DUTCH), [item])
    tracker.close()

    assert "fanart1" not in item
    assert mock_update.call_args.kwargs == {"fanart": NONE_BD}


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.provider_get")
def test_clearlogo_skips_to_fanart_hd_when_tmdb_logo_is_svg(
    mock_get, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _art_movie(clearlogo="")
    mock_get.return_value = {"title": "Test Movie", "images": {"logos": [
        {"file_path": "/logo.svg", "iso_639_1": "nl", "vote_average": 9.0, "vote_count": 50},
    ]}}
    mock_fanart.return_value = {"clearlogo": [
        Poster(FANART_HD_LOGO, "fanart", "nl", (1,)),
        Poster(FANART_SD_LOGO, "fanart", "nl", (9,), sd=True),
    ]}
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(_OTHER, _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _written(mock_update, "clearlogo") == FANART_HD_LOGO
    assert fs_map["clearlogo"]["source"] == "fanart"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_keyart_is_a_textless_poster_of_either_site(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    textless = "https://assets.fanart.tv/fanart/movies/1/movieposter/00.jpg"
    item = _art_movie(keyart="")
    mock_movie.side_effect = _art_fetch(posters=[_tmdb(NL_POSTER, "nl", 9.0, 100)])
    mock_fanart.return_value = {"poster": [_fanart(FANART_NL, "nl", 9), _fanart(textless, None)]}
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(_OTHER, _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _written(mock_update, "keyart") == textless
    assert fs_map["keyart"]["from_fallback"] == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_show_art")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_show_characterart_from_fanart_tv(
    mock_show, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    url = "https://assets.fanart.tv/fanart/tv/777/characterart/a.png"
    item = _make_tvshow_item()
    item["uniqueid"] = {"tmdb": "99999", "tvdb": "777"}
    item["characterart"] = ""
    mock_show.return_value = dict(_tmdb_tvshow_response(), posters=[], backdrops=[], logos=[])
    mock_fanart.return_value = {"show": {"characterart": [_fanart(url, None)]}, "seasons": {}}
    tracker = _make_tracker(tmp_path)

    run_scan(TVSHOW, tracker, _libs(_OTHER), "k", _make_settings(), items=[item])
    tracker.close()

    assert mock_fanart.call_args.args[0] == "777"
    assert _written(mock_update, "characterart") == url


def test_discart_not_managed_for_shows():
    assert "discart" not in TVSHOW.backfill_fields
    assert "characterart" not in MOVIE.backfill_fields


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_other_art_local_file_left_alone(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    local = {f: "smb://server/movies/test-{}.png".format(f)
             for f in _OTHER & MOVIE.backfill_fields}
    item = _art_movie(**local)
    mock_movie.side_effect = _art_fetch(logos=[_tmdb(EN_LOGO, "nl")])
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(_OTHER, _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    mock_movie.assert_not_called()
    mock_fanart.assert_not_called()
    mock_update.assert_not_called()
    assert {f: _reason(fs_map, f) for f in local} == {f: REASON_LOCAL_ART for f in local}


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_other_art_changed_outside_locks(mock_movie, mock_update, tmp_path, kodi_stubs):
    nl_logo = "https://image.tmdb.org/t/p/original/nl_logo.png"
    user_logo = "https://image.tmdb.org/t/p/original/user_logo.png"
    item = _art_movie(recent=True, clearlogo=EN_LOGO)
    mock_movie.side_effect = _art_fetch(logos=[_tmdb(nl_logo, "nl")])
    tracker = _make_tracker(tmp_path)
    libs = _libs(_OTHER, _DUTCH)
    identity = compute_identity(item, MOVIE)
    _scan_movies(tracker, libs, [item])
    assert _written(mock_update, "clearlogo") == nl_logo
    mock_update.reset_mock()

    picked = dict(item, clearlogo=user_logo)
    _make_due(tracker)
    _scan_movies(tracker, libs, [picked])
    locked = tracker.locked_fields("movie", identity)
    reason = _reason(_fs(tracker, MOVIE, item), "clearlogo")
    tracker.close()

    assert _written(mock_update, "clearlogo") is None
    assert "clearlogo" in locked
    assert reason == REASON_CHANGED_OUTSIDE


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_fanart_only_type_waits_when_fanart_tv_is_down(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    item = _art_movie(banner="")
    mock_movie.side_effect = _art_fetch()
    mock_fanart.side_effect = TransientProviderError("down")
    tracker = _make_tracker(tmp_path)

    _scan_movies(tracker, _libs(_OTHER, _DUTCH), [item])
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert mock_fanart.called
    assert _written(mock_update, "banner") is None
    assert _count(fs_map, "banner") == 0
    assert _reason(fs_map, "banner") == REASON_UNCHECKED


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_unconfigured_language_other_art_only_fills(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    fanart_bd = "https://assets.fanart.tv/fanart/movies/1/moviebackground/a.jpg"
    keyart = "https://image.tmdb.org/t/p/original/keyart.jpg"
    item = _art_movie(banner=FANART_BANNER, fanart=fanart_bd, keyart=keyart)
    mock_movie.side_effect = _art_fetch(posters=[_tmdb(NONE_BD, None, 9.0, 100)])
    tracker = _make_tracker(tmp_path)
    libs = one_library(frozenset({"fanart"} | _OTHER), language="")
    settings = dict(_make_settings(), ui_language="en-US")

    _scan_movies(tracker, libs, [item], settings=settings)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert _reason(fs_map, "banner") == REASON_FILL_ONLY
    assert _reason(fs_map, "fanart") == REASON_FILL_ONLY
    # Key art has no text, so the guessed language does not matter.
    assert _written(mock_update, "keyart") == NONE_BD


def test_settings_fingerprint_new_art_fields():
    settings = {"art_source": "fanart"}
    both = {"art_source": "fanart", "folder_language": "nl-NL", "fallback_language": "en"}
    for field in ("poster", "fanart", "clearlogo"):
        assert json.loads(settings_fingerprint(field, settings, "nl-NL", "movie", "en")) == both
    assert json.loads(settings_fingerprint("keyart", settings, "nl-NL", "movie", "en")) == {
        "art_source": "fanart",
    }
    for field in ("landscape", "banner", "discart", "clearart", "characterart"):
        assert json.loads(settings_fingerprint(field, settings, "nl-NL", "movie", "en")) == {
            "folder_language": "nl-NL", "fallback_language": "en",
        }


# ---------------------------------------------------------------------------
# Season poster refinements
# ---------------------------------------------------------------------------

def _batch_url(name, stamp):
    return "https://assets.fanart.tv/fanart/tv/777/seasonposter/{}-{:08x}abcde.jpg".format(
        name, stamp)


def _fanart_season_scan(tmp_path, answer, current, number=1, seasons=(1, 2, 3)):
    """Scan one season with Art source fanart.tv; returns the poster written."""
    with patch("resources.lib.data.kodi.update_item", return_value=True) as update, \
            patch("resources.lib.providers.tmdb.fetch_season_posters", return_value=[]), \
            patch("resources.lib.providers.tmdb.fetch_tvshow") as show, \
            patch("resources.lib.providers.fanart.fetch_show_art", return_value=answer):
        show.return_value = dict(_tmdb_tvshow_response(), posters=[], season_air_dates={})
        tracker = _make_tracker(tmp_path)
        libs = one_library(frozenset({"poster"}), art_source=ART_SOURCE_FANART)
        item = _season(number, poster=current, show_seasons=list(seasons))
        _scan_seasons(tracker, [item], libs=libs)
        tracker.close()
    return _written(update, "poster")


def test_season_tie_break_prefers_batch_with_other_seasons(tmp_path, kodi_stubs):
    batch = 0x60000000
    lone, together = _batch_url("a", batch + 10 * 86400), _batch_url("b", batch)
    answer = {"show": {}, "seasons": {
        1: [_fanart(lone, "en", likes=3), _fanart(together, "en", likes=3)],
        2: [_fanart(_batch_url("c", batch + 100), "en", likes=1)],
        3: [_fanart(_batch_url("d", batch + 200), "en", likes=1)],
    }}
    assert _fanart_season_scan(tmp_path, answer, "") == together


def test_season_tie_break_never_beats_more_likes(tmp_path, kodi_stubs):
    batch = 0x60000000
    liked, together = _batch_url("a", batch + 10 * 86400), _batch_url("b", batch)
    answer = {"show": {}, "seasons": {
        1: [_fanart(liked, "en", likes=9), _fanart(together, "en", likes=3)],
        2: [_fanart(_batch_url("c", batch + 100), "en", likes=1)],
    }}
    assert _fanart_season_scan(tmp_path, answer, "", seasons=(1, 2)) == liked


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters")
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_tmdb_season_poster_same_file_as_show_poster_skipped(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    shared = "https://image.tmdb.org/t/p/original/shared.jpg"
    mock_show.return_value = dict(
        _tmdb_tvshow_response(), season_air_dates={},
        posters=[_tmdb("https://image.tmdb.org/t/p/w500/shared.jpg", "en")])
    mock_posters.return_value = [_tmdb(shared, "en", avg=9.0), _tmdb(SEASON_POSTER, "en")]
    tracker = _make_tracker(tmp_path)

    _scan_seasons(tracker, [_season(1)])
    tracker.close()

    assert _written(mock_update, "poster") == SEASON_POSTER


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_season_posters", return_value=[])
@patch("resources.lib.providers.tmdb.fetch_tvshow")
def test_season_refetches_show_images_when_cache_has_none(
    mock_show, mock_posters, mock_update, tmp_path, kodi_stubs
):
    cache = {"55555": dict(_tmdb_tvshow_response(), season_air_dates={})}
    mock_show.return_value = dict(_tmdb_tvshow_response(), posters=[],
                                  season_air_dates={})
    tracker = _make_tracker(tmp_path)

    _scan_seasons(tracker, [_season(1), _season(2)], show_cache=cache)
    tracker.close()

    assert mock_show.call_count == 1
    assert mock_show.call_args.kwargs["image_languages"] == ("en",)
    assert "posters" in cache["55555"]


# ---------------------------------------------------------------------------
# Local art files that no longer exist
# ---------------------------------------------------------------------------

LOCAL_POSTER = "smb://server/movies/poster.jpg"
_LOCAL_STATE = "resources.lib.engine.scan.local_art_state"


def _local_scan(tracker, item, state, libs=None, **kwargs):
    """One movie scan with every local art file in ``state``; returns the
    existence-check stand-in."""
    with patch(_LOCAL_STATE, return_value=state) as check:
        _scan_movies(tracker, libs or _libs(frozenset({"poster"}), _DUTCH), [item], **kwargs)
    return check


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_missing_local_poster_is_filled(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(LOCAL_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)

    check = _local_scan(tracker, item, MISSING)
    ident = compute_identity(item, MOVIE)
    keep = tracker.get_keep("movie", ident)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    check.assert_called_once_with(LOCAL_POSTER, item["file"])
    assert _written(mock_update, "poster") == NL_POSTER
    assert keep["poster"]["original"] == LOCAL_POSTER
    assert _reason(fs_map, "poster") == "updated"


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_missing_local_art_takes_english_last_resort(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(LOCAL_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)

    _local_scan(tracker, item, MISSING, libs=_libs(frozenset({"poster"}), _DUTCH_ONLY))
    tracker.close()

    assert _written(mock_update, "poster") == EN_POSTER


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_unreachable_local_art_is_transient(
    mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs
):
    """A sleeping NAS: neither the art, its folder nor the video answers.
    Nothing is fetched, written or sampled, and the next scan checks again."""
    item = _movie(LOCAL_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)

    _local_scan(tracker, item, UNREACHABLE)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.start_new_cycle()
    again = _local_scan(tracker, item, UNREACHABLE)
    check = tracker.get_art_check("movie", compute_identity(item, MOVIE), "poster")
    tracker.close()

    assert not mock_movie.called
    mock_fanart.assert_not_called()
    mock_update.assert_not_called()
    assert fs_map.get("poster", {}).get("fetch_count", 0) == 0
    assert _reason(fs_map, "poster") == REASON_UNCHECKED
    assert again.call_count == 1
    assert check is None


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_unreachable_final_art_is_left_alone(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie("", recent=False)
    item["premiered"] = "2000-01-01"
    mock_movie.side_effect = _movie_fetch([])
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    before = _fs(tracker, MOVIE, item)["poster"]

    item["poster"] = LOCAL_POSTER
    tracker.start_new_cycle()
    _local_scan(tracker, item, UNREACHABLE)
    after = _fs(tracker, MOVIE, item)["poster"]
    tracker.close()

    assert mock_movie.call_count == 1
    assert after["fetch_count"] == before["fetch_count"] == 1
    assert after["written_value"] == before["written_value"]


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_existing_local_file_checked_weekly(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(LOCAL_POSTER)
    tracker = _make_tracker(tmp_path)
    ident = compute_identity(item, MOVIE)

    def scan_after(days_ago=None):
        if days_ago is not None:
            checked = (date.today() - timedelta(days=days_ago)).isoformat()
            tracker._conn.execute("UPDATE art_checks SET checked_on = ?", (checked,))
            tracker.commit()
        tracker.start_new_cycle()
        return _local_scan(tracker, item, EXISTS).call_count

    first = _local_scan(tracker, item, EXISTS).call_count
    stored = tracker.get_art_check("movie", ident, "poster")
    same_week = scan_after()
    day_six = scan_after(6)
    day_eight = scan_after(8)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    assert (first, same_week, day_six, day_eight) == (1, 0, 0, 1)
    assert stored == (fingerprint(LOCAL_POSTER), date.today().isoformat())
    mock_movie.assert_not_called()
    mock_update.assert_not_called()
    assert _reason(fs_map, "poster") == REASON_LOCAL_ART
    assert _count(fs_map, "poster") == 0


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_new_local_path_is_checked_at_once(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(LOCAL_POSTER)
    tracker = _make_tracker(tmp_path)
    _local_scan(tracker, item, EXISTS)
    item["poster"] = "smb://server/movies/other.jpg"
    tracker.start_new_cycle()
    calls = _local_scan(tracker, item, EXISTS).call_count
    tracker.close()

    assert calls == 1


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_update_now_checks_local_art_at_once(mock_movie, mock_update, tmp_path, kodi_stubs):
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    item = _movie(LOCAL_POSTER)
    tracker = _make_tracker(tmp_path)
    _local_scan(tracker, item, EXISTS)
    check = _local_scan(tracker, item, MISSING, reset=True)
    tracker.close()

    assert check.call_count == 1
    assert _written(mock_update, "poster") == NL_POSTER


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_manual_scan_setting_checks_local_art_at_once(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(LOCAL_POSTER)
    tracker = _make_tracker(tmp_path)
    _local_scan(tracker, item, EXISTS)
    tracker.start_new_cycle()
    settings = dict(_make_settings(), check_local_art_now=True)
    calls = _local_scan(tracker, item, EXISTS, settings=settings).call_count
    tracker.close()

    assert calls == 1


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_web_art_is_never_checked(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(EN_POSTER)
    tracker = _make_tracker(tmp_path)
    check = _local_scan(tracker, item, MISSING)
    tracker.close()

    check.assert_not_called()


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_unticked_and_locked_art_is_never_checked(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _art_movie(poster=LOCAL_POSTER, fanart="smb://server/movies/fanart.jpg")
    tracker = _make_tracker(tmp_path)
    ident = compute_identity(item, MOVIE)
    tracker.upsert_item("movie", ident, kodi_id=1, tmdb_id="12345")
    tracker.set_locked("movie", ident, "fanart", True)
    tracker.commit()
    check = _local_scan(tracker, item, MISSING,
                        libs=_libs(frozenset({"plot", "fanart"}), _DUTCH))
    tracker.close()

    check.assert_not_called()


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_final_missing_art_refilled_once(mock_movie, mock_update, tmp_path, kodi_stubs):
    """An old movie's poster settled with nothing found; a local file set
    since then was deleted. It gets one fetch; when that finds nothing either
    the dead path is recorded, so it is not fetched (or checked) every week."""
    item = _movie("")
    item["premiered"] = "2000-01-01"
    mock_movie.side_effect = _movie_fetch([])
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    assert mock_movie.call_count == 1

    item["poster"] = LOCAL_POSTER
    tracker.start_new_cycle()
    _local_scan(tracker, item, MISSING)
    fs = _fs(tracker, MOVIE, item)["poster"]
    tracker.start_new_cycle()
    later = _local_scan(tracker, item, MISSING)
    tracker.close()

    assert mock_movie.call_count == 2
    mock_update.assert_not_called()
    assert fs["written_value"] == fingerprint(LOCAL_POSTER)
    later.assert_not_called()


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_final_missing_art_filled_when_found(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie("")
    item["premiered"] = "2000-01-01"
    mock_movie.side_effect = _movie_fetch([])
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])

    item["poster"] = LOCAL_POSTER
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker.start_new_cycle()
    _local_scan(tracker, item, MISSING)
    keep = tracker.get_keep("movie", compute_identity(item, MOVIE))
    tracker.close()

    assert _written(mock_update, "poster") == NL_POSTER
    assert keep["poster"]["original"] == LOCAL_POSTER


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_restored_file_after_empty_refill_is_left_alone(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    """The dead path recorded by an empty refill is not MetaMender's own: once
    the file is back, Update now leaves it alone."""
    item = _movie("")
    item["premiered"] = "2000-01-01"
    mock_movie.side_effect = _movie_fetch([])
    tracker = _make_tracker(tmp_path)
    _scan_movies(tracker, _libs(frozenset({"poster"}), _DUTCH), [item])
    item["poster"] = LOCAL_POSTER
    tracker.start_new_cycle()
    _local_scan(tracker, item, MISSING)

    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    _local_scan(tracker, item, EXISTS, reset=True)
    fs_map = _fs(tracker, MOVIE, item)
    tracker.close()

    mock_update.assert_not_called()
    assert _reason(fs_map, "poster") == REASON_LOCAL_ART


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_missing_local_art_on_nfo_item_is_filled(mock_movie, mock_update, tmp_path, kodi_stubs):
    item = _movie(LOCAL_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(NL_POSTER, "nl")])
    tracker = _make_tracker(tmp_path)

    with patch("resources.lib.engine.scan.nfo_state", return_value=NFO):
        _local_scan(tracker, item, MISSING)
    tracker.close()

    assert _written(mock_update, "poster") == NL_POSTER


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_missing_local_art_fills_in_unconfigured_language_folder(
    mock_movie, mock_update, tmp_path, kodi_stubs
):
    """Fill-only art takes a missing file as empty."""
    item = _movie(LOCAL_POSTER)
    mock_movie.side_effect = _movie_fetch([_tmdb(EN_POSTER, "en")])
    tracker = _make_tracker(tmp_path)

    _local_scan(tracker, item, MISSING, libs=_libs(
        frozenset({"poster"}), {"sources": [], "default_language": "", "fallback_language": ""}))
    tracker.close()

    assert _written(mock_update, "poster") == EN_POSTER


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.fanart.fetch_movie_art")
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_missing_other_art_is_filled(mock_movie, mock_fanart, mock_update, tmp_path, kodi_stubs):
    item = _art_movie(landscape="smb://server/movies/landscape.jpg")
    mock_movie.side_effect = _art_fetch()
    mock_fanart.return_value = {"landscape": [_fanart(FANART_NL, "nl")]}
    tracker = _make_tracker(tmp_path)

    _local_scan(tracker, item, MISSING, libs=_libs(_OTHER, _DUTCH))
    tracker.close()

    assert _written(mock_update, "landscape") == FANART_NL

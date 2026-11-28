"""Scan tests for a Kodi re-add (a file upgrade or Refresh gives the item a
new Kodi id and the scraper's values): the scraper's art is not taken for a
user change, and the values MetaMender wrote come back in the same scan
without moving any field along its schedule."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from resources.lib.data.identity import compute_identity
from resources.lib.data.media_types import MOVIE
from resources.lib.data.nfo import NFO
from resources.lib.data.tracker import REASON_CHANGED_OUTSIDE
from resources.lib.engine.art import Poster
from resources.lib.engine.protect import fingerprint
from resources.lib.engine.resolve import slot_fingerprint
from resources.lib.engine.scan import run_scan
from resources.lib.utils import TransientProviderError
from tests.test_scan import (  # noqa: F401 (autouse fixture)
    _kodi_answers,
    _libs,
    _make_movie_item,
    _make_settings,
    _make_tracker,
    _mock_secondary_providers,
    _scan_log,
    _tmdb_movie_response,
)

OLD_ID = 405
NEW_ID = 433
SCRAPER_KEYART = "https://image.tmdb.org/t/p/original/scraper1.jpg"
RESCRAPED_KEYART = "https://image.tmdb.org/t/p/original/scraper2.jpg"
OUR_KEYART = "https://image.tmdb.org/t/p/original/textless.jpg"
OTHER_ART = frozenset({
    "clearlogo", "keyart", "landscape", "banner", "discart", "clearart", "characterart",
})
DETAILS = frozenset({"genre", "studio", "country", "runtime", "firstaired"})


@pytest.fixture(autouse=True)
def _no_fanart():
    with patch("resources.lib.providers.fanart.fetch_movie_art", return_value={}):
        yield


def _movie(kodi_id: int, young: bool = False, **values: Any) -> dict[str, Any]:
    item = _make_movie_item(movie_id=kodi_id)
    item["premiered"] = (
        (date.today() - timedelta(days=10)).isoformat() if young else "2000-01-01")
    item.update(values)
    return item


_IDENTITY = compute_identity(_movie(OLD_ID), MOVIE)


def _fetch(**overrides: Any):
    """tmdb.fetch_movie stand-in: a textless poster when images are asked for."""
    def fetch(tmdb_id, api_key, language="en-US", image_languages=()):
        data = _tmdb_movie_response(**overrides)
        if image_languages:
            data["posters"] = [Poster(OUR_KEYART, "tmdb", None, (7.0, 20))]
        return data
    return fetch


def _scan(tracker, fields, item, kodi_has=None):
    """One full scan of ``item``. Kodi knows the items in ``kodi_has`` by
    id and no other, so a row recording another id sees it gone."""
    tracker.begin_cycle(resume_interrupted=False)
    with patch("resources.lib.engine.scan.lookup_item",
               side_effect=_kodi_answers(kodi_has or {})):
        return run_scan(MOVIE, tracker, _libs(fields), "k", _make_settings(),
                        items=[item])


def _writes(mock_update, kodi_id):
    """Fields written to ``kodi_id``, merged over its update calls."""
    out: dict[str, Any] = {}
    for c in mock_update.call_args_list:
        if c.args[0] == kodi_id:
            out.update(c.kwargs)
    return out


def _row(tracker, field):
    return tracker.get_field_status("movie", _IDENTITY, field) or {}


def _clock(tracker, fields):
    return {f: (_row(tracker, f).get("fetch_count"), _row(tracker, f).get("next_retry"))
            for f in fields}


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_readd_does_not_lock_scraper_art(mock_fetch, mock_update, tmp_path, kodi_stubs):
    mock_fetch.side_effect = _fetch()
    tracker = _make_tracker(tmp_path)
    _scan(tracker, OTHER_ART, _movie(OLD_ID, young=True, keyart=SCRAPER_KEYART))
    assert _writes(mock_update, OLD_ID)["keyart"] == OUR_KEYART
    before = _clock(tracker, OTHER_ART)

    _scan(tracker, OTHER_ART, _movie(NEW_ID, young=True, keyart=RESCRAPED_KEYART))
    locked = tracker.locked_fields("movie", _IDENTITY)
    keyart = _row(tracker, "keyart")
    first = tracker.get_first_fp("movie", _IDENTITY, "keyart")
    after = _clock(tracker, OTHER_ART)
    tracker.close()

    assert "keyart" not in locked
    assert keyart["last_reason"] != REASON_CHANGED_OUTSIDE
    assert first == fingerprint(RESCRAPED_KEYART)
    assert _writes(mock_update, NEW_ID)["keyart"] == OUR_KEYART
    # Nothing moved along its schedule, the slots sampled only because the
    # item was fetched for the re-add included.
    assert after == before


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_readd_rewrites_metamender_values_in_the_same_scan(
    mock_fetch, mock_update, tmp_path, kodi_stubs
):
    mock_fetch.side_effect = _fetch()
    tracker = _make_tracker(tmp_path)
    _scan(tracker, DETAILS, _movie(OLD_ID))
    first = _writes(mock_update, OLD_ID)
    assert first["country"] == ["US"] and first["runtime"] == 7200
    before = _clock(tracker, DETAILS)

    readded = _movie(NEW_ID, country=["United States of America"], runtime=5400,
                     studio=["WB"])
    with _scan_log() as log:
        stats = _scan(tracker, DETAILS, readded)
    after = _clock(tracker, DETAILS)
    tracker.close()

    assert _writes(mock_update, NEW_ID) == {"country": ["US"], "runtime": 7200}
    assert stats["processed"] == 1
    assert after == before
    readds = [c.kwargs for c in log.info.call_args_list
              if c.kwargs.get("event") == "scan.readded"]
    assert readds == [{"event": "scan.readded", "identity": _IDENTITY,
                       "old_kodi_id": OLD_ID, "kodi_id": NEW_ID,
                       "fields_due": ["country", "runtime"]}]


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_readd_keeps_nfo_value(mock_fetch, mock_update, tmp_path, kodi_stubs):
    mock_fetch.side_effect = _fetch()
    tracker = _make_tracker(tmp_path)
    _scan(tracker, DETAILS, _movie(OLD_ID))
    written = _row(tracker, "country")["written_value"]
    before = _clock(tracker, DETAILS)
    mock_fetch.reset_mock()

    with patch("resources.lib.engine.scan.nfo_state", return_value=NFO):
        _scan(tracker, DETAILS, _movie(NEW_ID, country=["Nfoland"], runtime=7200,
                                       studio=["WB"]))
    country = _row(tracker, "country")
    after = _clock(tracker, DETAILS)
    tracker.close()

    # Due for the re-add, fetched, and refused by the NFO rule.
    assert mock_fetch.called
    assert "country" not in _writes(mock_update, NEW_ID)
    assert country["written_value"] == written
    assert after == before


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_readd_keeps_user_locks(mock_fetch, mock_update, tmp_path, kodi_stubs):
    fields = DETAILS | OTHER_ART
    mock_fetch.side_effect = _fetch()
    tracker = _make_tracker(tmp_path)
    _scan(tracker, fields, _movie(OLD_ID, keyart=SCRAPER_KEYART))
    assert _writes(mock_update, OLD_ID)["keyart"] == OUR_KEYART
    tracker.set_locked("movie", _IDENTITY, "country", True)
    tracker.set_locked("movie", _IDENTITY, "keyart", True)
    first = tracker.get_first_fp("movie", _IDENTITY, "keyart")
    tracker.commit()

    _scan(tracker, fields, _movie(NEW_ID, country=["Userland"], runtime=5400,
                                  studio=["WB"], keyart=RESCRAPED_KEYART))
    locked = tracker.locked_fields("movie", _IDENTITY)
    first_after = tracker.get_first_fp("movie", _IDENTITY, "keyart")
    tracker.close()

    writes = _writes(mock_update, NEW_ID)
    assert "country" not in writes and "keyart" not in writes
    assert writes["runtime"] == 7200
    assert {"country", "keyart"} <= locked
    assert first_after == first


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_readd_transient_records_nothing(mock_fetch, mock_update, tmp_path, kodi_stubs):
    mock_fetch.side_effect = _fetch()
    tracker = _make_tracker(tmp_path)
    _scan(tracker, DETAILS, _movie(OLD_ID))
    rows = {f: dict(_row(tracker, f)) for f in DETAILS}

    mock_fetch.side_effect = TransientProviderError("http 503")
    stats = _scan(tracker, DETAILS, _movie(NEW_ID, country=["Elsewhere"], runtime=5400,
                                           studio=["WB"]))
    after = {f: dict(_row(tracker, f)) for f in DETAILS}
    tracker.close()

    assert _writes(mock_update, NEW_ID) == {}
    assert stats["transient"] == 2
    for f in DETAILS:
        for key in ("fetch_count", "fetched_at", "next_retry", "written_value", "source"):
            assert after[f].get(key) == rows[f].get(key), (f, key)


def _ratings_first_scan(tracker, mock_fetch, mock_update):
    mock_fetch.side_effect = _fetch()
    with patch("resources.lib.providers.imdb_ratings.lookup", return_value=(7.0, 80)):
        _scan(tracker, frozenset({"rating"}), _movie(OLD_ID))
    ours = _writes(mock_update, OLD_ID)["ratings"]
    assert ours
    return ours


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_readd_rating_compares_per_slot(mock_fetch, mock_update, tmp_path, kodi_stubs):
    tracker = _make_tracker(tmp_path)
    ours = _ratings_first_scan(tracker, mock_fetch, mock_update)
    before = _clock(tracker, ("rating", "votes"))

    # The scraper's re-add kept every slot MetaMender wrote and added one of
    # its own: nothing of MetaMender's to restore.
    kept = dict(ours, trakt={"rating": 5.0, "votes": 3, "default": False})
    mock_fetch.reset_mock()
    with patch("resources.lib.providers.imdb_ratings.lookup", return_value=(7.0, 80)):
        _scan(tracker, frozenset({"rating"}), _movie(NEW_ID, ratings=kept))
    assert not mock_fetch.called
    assert _writes(mock_update, NEW_ID) == {}

    # A later re-add changed one slot MetaMender wrote: due, written back.
    slot = min(ours)
    changed = dict(ours)
    changed[slot] = dict(ours[slot], rating=1.0, votes=1)
    with patch("resources.lib.providers.imdb_ratings.lookup", return_value=(7.0, 80)):
        _scan(tracker, frozenset({"rating"}), _movie(NEW_ID + 1, ratings=changed))
    rewritten = _writes(mock_update, NEW_ID + 1)["ratings"]
    after = _clock(tracker, ("rating", "votes"))
    tracker.close()

    assert slot_fingerprint(rewritten[slot]) == slot_fingerprint(ours[slot])
    assert after == before


@patch("resources.lib.data.kodi.update_item", return_value=True)
@patch("resources.lib.providers.tmdb.fetch_movie")
def test_no_readd_decisions_unchanged(mock_fetch, mock_update, tmp_path, kodi_stubs):
    """A rescan under the same Kodi id leaves a finished field alone even
    when Kodi's value is no longer MetaMender's, as before."""
    mock_fetch.side_effect = _fetch()
    tracker = _make_tracker(tmp_path)
    _scan(tracker, DETAILS, _movie(OLD_ID))
    mock_fetch.reset_mock()
    mock_update.reset_mock()

    with _scan_log() as log:
        _scan(tracker, DETAILS, _movie(OLD_ID, country=["Userland"], runtime=5400,
                                       studio=["WB"]))
    tracker.close()

    assert not mock_fetch.called
    mock_update.assert_not_called()
    assert not [c for c in log.info.call_args_list
                if c.kwargs.get("event") == "scan.readded"]

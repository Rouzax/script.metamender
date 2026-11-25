"""Kodi shows a video file's own length as its runtime whenever it has read
one, so a provider runtime is never written over it.

Kodi keeps the library's runtime and the file's length apart and reports the
file's (CVideoInfoTag::GetDuration). A listing without stream details carries
only the library's value, so the scan looks the file up before a runtime write.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

from resources.lib.data.kodi import file_runtime
from resources.lib.data.media_types import EPISODE, MOVIE
from resources.lib.data.tracker import REASON_FROM_FILE, REASON_UPDATED
from resources.lib.engine import scan
from resources.lib.engine.scan import run_scan
from tests.test_scan import (  # noqa: F401 - the autouse fixture keeps providers and NFO checks offline
    _DETAILS,
    _MOVIE_IDENTITY,
    _libs,
    _make_movie_item,
    _make_settings,
    _mock_secondary_providers,
    _tmdb_movie_response,
)


def _details(kodi_stubs, detail, key="moviedetails"):
    mock = MagicMock(return_value=json.dumps({"result": {key: detail}}))
    kodi_stubs["xbmc"].executeJSONRPC = mock
    return mock


def test_file_runtime_is_the_length_kodi_shows(kodi_stubs):
    mock = _details(kodi_stubs, {"runtime": 3424,
                                 "streamdetails": {"video": [{"duration": 3424}]}})
    assert file_runtime(MOVIE, 7) == 3424
    sent = json.loads(mock.call_args[0][0])
    assert sent["method"] == "VideoLibrary.GetMovieDetails"
    assert sent["params"] == {"movieid": 7, "properties": ["runtime", "streamdetails"]}


def test_file_runtime_for_an_episode(kodi_stubs):
    mock = _details(kodi_stubs, {"runtime": 2700,
                                 "streamdetails": {"video": [{"duration": 2700}]}},
                    key="episodedetails")
    assert file_runtime(EPISODE, 5) == 2700
    assert json.loads(mock.call_args[0][0])["params"]["episodeid"] == 5


def test_no_file_runtime_without_stream_details(kodi_stubs):
    _details(kodi_stubs, {"runtime": 3360, "streamdetails": {"video": []}})
    assert file_runtime(MOVIE, 7) is None


def test_no_file_runtime_when_kodi_shows_the_library_value(kodi_stubs):
    # Kodi distrusts a file length under 60% of the library's runtime and
    # shows the library's value, so that one is still MetaMender's to fill.
    _details(kodi_stubs, {"runtime": 7200,
                          "streamdetails": {"video": [{"duration": 600}]}})
    assert file_runtime(MOVIE, 7) is None


def test_no_file_runtime_when_the_query_fails(kodi_stubs):
    kodi_stubs["xbmc"].executeJSONRPC = MagicMock(
        return_value=json.dumps({"error": {"code": -32602, "message": "Invalid params"}}))
    assert file_runtime(MOVIE, 7) is None


def _scan(tmp_path, shown):
    from resources.lib.data.tracker import TrackerDB
    item = _make_movie_item()
    item["runtime"] = 3360
    item["premiered"] = date.today().isoformat()
    tracker = TrackerDB(str(tmp_path / "t.db"))
    with patch("resources.lib.providers.tmdb.fetch_movie",
               return_value=_tmdb_movie_response(runtime=57)), \
         patch("resources.lib.data.kodi.update_item", return_value=True) as update, \
         patch.object(scan.kodi, "file_runtime", return_value=shown) as lookup:
        run_scan(MOVIE, tracker, _libs(_DETAILS), "key",
                 _make_settings(), items=[item])
    fs = tracker.get_field_status_map(MOVIE.name, _MOVIE_IDENTITY).get("runtime") or {}
    tracker.close()
    return update, lookup, fs


def test_runtime_not_written_over_the_file_length(tmp_path, kodi_stubs):
    update, lookup, fs = _scan(tmp_path, shown=3424)
    lookup.assert_called_once_with(MOVIE, 1)
    assert all("runtime" not in c.kwargs for c in update.call_args_list)
    assert fs["last_reason"] == REASON_FROM_FILE
    # TMDB answered, so the field is sampled like any other answered one.
    assert fs["fetch_count"] == 1


def test_runtime_written_when_kodi_has_no_file_length(tmp_path, kodi_stubs):
    update, lookup, fs = _scan(tmp_path, shown=None)
    lookup.assert_called_once_with(MOVIE, 1)
    assert update.call_args.kwargs["runtime"] == 57 * 60
    assert fs["last_reason"] == REASON_UPDATED


def test_no_file_lookup_when_the_runtime_already_matches(tmp_path, kodi_stubs):
    from resources.lib.data.tracker import TrackerDB
    item = _make_movie_item()
    item["runtime"] = 57 * 60
    tracker = TrackerDB(str(tmp_path / "t.db"))
    with patch("resources.lib.providers.tmdb.fetch_movie",
               return_value=_tmdb_movie_response(runtime=57)), \
         patch("resources.lib.data.kodi.update_item", return_value=True), \
         patch.object(scan.kodi, "file_runtime") as lookup:
        run_scan(MOVIE, tracker, _libs(_DETAILS), "key",
                 _make_settings(), items=[item])
    tracker.close()
    lookup.assert_not_called()

"""Tests for media type definitions."""
from __future__ import annotations

from resources.lib.data.media_types import EPISODE, MOVIE, SEASON, TVSHOW


def test_movie_fields():
    assert MOVIE.name == "movie"
    assert MOVIE.kodi_list_method == "VideoLibrary.GetMovies"
    assert MOVIE.kodi_set_method == "VideoLibrary.SetMovieDetails"
    assert MOVIE.kodi_id_field == "movieid"
    assert MOVIE.kodi_result_key == "movies"
    assert "rating" in MOVIE.backfill_fields
    assert "plot" in MOVIE.backfill_fields
    assert "tagline" in MOVIE.backfill_fields
    assert "trailer" in MOVIE.backfill_fields
    assert "mpaa" in MOVIE.backfill_fields
    assert MOVIE.date_field == "premiered"


def test_tvshow_fields():
    assert TVSHOW.name == "tvshow"
    assert TVSHOW.kodi_id_field == "tvshowid"
    assert "rating" in TVSHOW.backfill_fields
    assert "mpaa" in TVSHOW.backfill_fields
    assert "trailer" not in TVSHOW.backfill_fields
    assert "tagline" not in TVSHOW.backfill_fields


def test_episode_fields():
    assert EPISODE.name == "episode"
    assert EPISODE.kodi_list_method == "VideoLibrary.GetEpisodes"
    assert EPISODE.kodi_id_field == "episodeid"
    assert "title" in EPISODE.backfill_fields
    assert "rating" in EPISODE.backfill_fields
    assert "plot" in EPISODE.backfill_fields
    assert "tagline" not in EPISODE.backfill_fields
    assert "mpaa" not in EPISODE.backfill_fields
    assert EPISODE.date_field == "firstaired"


def test_media_types_are_frozen():
    import dataclasses
    assert dataclasses.is_dataclass(MOVIE)
    assert MOVIE.__dataclass_params__.frozen


def test_season_media_type_fields():
    assert SEASON.name == "season"
    assert SEASON.label == "Seasons"
    assert SEASON.kodi_list_method == "VideoLibrary.GetSeasons"
    assert SEASON.kodi_set_method == "VideoLibrary.SetSeasonDetails"
    assert SEASON.kodi_get_method == "VideoLibrary.GetSeasonDetails"
    assert SEASON.kodi_id_field == "seasonid"
    assert SEASON.kodi_result_key == "seasons"
    assert SEASON.kodi_detail_key == "seasondetails"
    assert SEASON.backfill_fields == frozenset({"poster"})
    assert SEASON.date_field == "firstaired"


def test_movie_and_show_backfill_poster():
    assert "poster" in MOVIE.backfill_fields
    assert "poster" in TVSHOW.backfill_fields


def test_episode_backfill_thumb():
    assert "thumb" in EPISODE.backfill_fields

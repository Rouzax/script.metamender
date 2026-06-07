"""Media type definitions for MetaMender."""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet


@dataclass(frozen=True)
class MediaType:
    name: str
    label: str
    kodi_list_method: str
    kodi_set_method: str
    kodi_id_field: str
    kodi_result_key: str
    backfill_fields: FrozenSet[str]
    date_field: str


MOVIE = MediaType(
    name="movie",
    label="Movies",
    kodi_list_method="VideoLibrary.GetMovies",
    kodi_set_method="VideoLibrary.SetMovieDetails",
    kodi_id_field="movieid",
    kodi_result_key="movies",
    backfill_fields=frozenset({
        "rating", "votes", "plot", "tagline", "director", "writer",
        "genre", "studio", "country", "trailer", "runtime", "mpaa",
    }),
    date_field="premiered",
)

TVSHOW = MediaType(
    name="tvshow",
    label="TV Shows",
    kodi_list_method="VideoLibrary.GetTVShows",
    kodi_set_method="VideoLibrary.SetTVShowDetails",
    kodi_id_field="tvshowid",
    kodi_result_key="tvshows",
    backfill_fields=frozenset({
        "rating", "votes", "plot", "genre", "mpaa",
    }),
    date_field="premiered",
)

EPISODE = MediaType(
    name="episode",
    label="Episodes",
    kodi_list_method="VideoLibrary.GetEpisodes",
    kodi_set_method="VideoLibrary.SetEpisodeDetails",
    kodi_id_field="episodeid",
    kodi_result_key="episodes",
    backfill_fields=frozenset({
        "rating", "votes", "plot", "title", "director", "writer",
        "firstaired", "runtime",
    }),
    date_field="firstaired",
)

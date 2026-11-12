"""Demo items for the details window scenes. Development only, never shipped.

Every title, name, id and date here is invented, so a screenshot shows the
window and nothing of a real library. The rows come from the add-on's own
details model, so the wording is exactly what a user sees.
"""
from __future__ import annotations

import json
from typing import Any

from resources.lib.data.media_types import EPISODE, MOVIE, MediaType
from resources.lib.engine.protect import fingerprint

# The scenes are dated, so a re-take gives the same "next check" dates.
TODAY = "2026-11-12"

EPISODE_PLOT = ("The night crossing to the islands is delayed when a passenger who "
                "boarded at the harbour cannot be found anywhere on the ferry. The "
                "crew has until dawn to work out who saw her last.")
DUTCH_PLOT = ("Agenten Mardik en Shannon worden opgeroepen voor een melding op "
                "Amsterdam Centraal, waar een verdachte tas is gevonden. Wat begint "
                "als routine, loopt snel uit de hand.")
MOVIE_PLOT = ("A retired lighthouse keeper takes in a runaway who claims to have "
              "seen a ship that sank forty years ago.")


def _fs(reason: str, *, source: str | None = None, count: int = 1,
        next_retry: str | None = None, fetched: str = TODAY,
        written: str | None = None, fallback: int = 0) -> dict[str, Any]:
    return {
        "source": source, "fetch_count": count, "next_retry": next_retry,
        "fetched_at": fetched, "from_fallback": fallback, "written_value": written,
        "settings_fp": "demo", "last_reason": reason,
    }


def episode() -> tuple[MediaType, dict[str, Any], dict[str, Any], dict[str, Any],
                       dict[str, Any], str]:
    """A new episode scraped by TVmaze without a plot, matched by its number."""
    item = {
        "title": "Departure", "showtitle": "The Night Ferry",
        "season": 1, "episode": 1, "firstaired": "2026-11-01",
        "plot": EPISODE_PLOT, "rating": 7.4, "votes": 312, "runtime": 2700,
        "director": [], "writer": ["Sam Okafor"],
        "uniqueid": {"tvmaze": "1000001"},
        "ratings": {"imdb": {"rating": 7.4, "votes": 312, "default": True}},
    }
    item_row = {
        "tmdb_id": "200001", "tvdb_id": "300001", "imdb_id": "tt0000001",
        "tmdb_season": 1, "number_matched": 1, "orig_title": "Departure",
        "orig_aired": "2026-11-01", "ids_checked": TODAY, "last_seen": TODAY,
        "match_note": json.dumps([
            {"site": "tmdb", "matched": "date"}, {"site": "tvdb", "matched": "date"},
        ]),
    }
    fs_map = {
        "plot": _fs("updated", source="tmdb", next_retry="2026-11-19",
                    written=fingerprint(EPISODE_PLOT)),
        "rating": _fs("updated", source="imdb", next_retry="2026-11-19",
                      written=json.dumps({"imdb": "demo"})),
        "votes": _fs("updated", source="imdb", next_retry="2026-11-19",
                     written=json.dumps({"imdb": "demo"})),
        "runtime": _fs("updated", source="tmdb", next_retry="2026-11-19",
                       written=fingerprint(2700)),
        "title": _fs("unchanged", source="tmdb", next_retry="2026-11-19"),
        "firstaired": _fs("unchanged", source="tmdb", count=3),
        "writer": _fs("unchanged", source="tmdb", next_retry="2026-11-19"),
        "director": _fs("nothing", next_retry="2026-11-15"),
    }
    # The scraper had no plot: MetaMender filled the gap.
    keep = {
        "plot": {"original": "", "has_original": True,
                 "locked": False, "written_fp": fingerprint(EPISODE_PLOT)},
    }
    return EPISODE, item, item_row, fs_map, keep, "English"


def episode_dutch() -> tuple[MediaType, dict[str, Any], dict[str, Any], dict[str, Any],
                             dict[str, Any], str]:
    """The same story in a Dutch folder: the scraper's English plot replaced
    by TMDB's Dutch one."""
    media_type, item, item_row, fs_map, _, _ = episode()
    item.update({"title": "Amsterdam Centraal", "showtitle": "Het gouden uur",
                 "plot": DUTCH_PLOT, "writer": ["Noor de Vries"]})
    item_row["orig_title"] = "Amsterdam Centraal"
    fs_map["plot"] = _fs("updated", source="tmdb", next_retry="2026-11-19",
                         written=fingerprint(DUTCH_PLOT))
    keep = {
        "plot": {"original": "Officers Mardik and Shannon are called to Amsterdam "
                             "Central Station.", "has_original": True,
                 "locked": False, "written_fp": fingerprint(DUTCH_PLOT)},
    }
    return media_type, item, item_row, fs_map, keep, "Dutch"


def episode_not_matched() -> tuple[MediaType, dict[str, Any], dict[str, Any],
                                   dict[str, Any], dict[str, Any], str]:
    """An episode whose numbering differs between Kodi and TMDB: nothing used."""
    media_type, item, item_row, _, _, language = episode()
    item.update({"title": "Back from Vacation", "showtitle": "The Branch Office",
                 "season": 3, "episode": 12, "firstaired": "2007-01-04",
                 "plot": "", "runtime": 1320})
    item_row.update({
        "tmdb_id": None, "tvdb_id": None, "imdb_id": None, "number_matched": 0,
        "orig_title": "Back from Vacation", "orig_aired": "2007-01-04",
        "match_note": json.dumps([
            {"site": "tmdb", "site_title": "Traveling Salesmen", "site_aired": "2007-01-11",
             "kodi_title": "Back from Vacation", "kodi_aired": "2007-01-04"},
        ]),
    })
    fs_map = {"plot": _fs("unchecked", next_retry="2026-11-13", count=0)}
    return media_type, item, item_row, fs_map, {}, language


def movie() -> tuple[MediaType, dict[str, Any], dict[str, Any], dict[str, Any],
                     dict[str, Any], str]:
    """An English film whose age rating MetaMender set for the Netherlands."""
    item = {
        "title": "The Lighthouse Keeper", "premiered": "2024-03-08",
        "plot": MOVIE_PLOT, "mpaa": "NL:12", "runtime": 6240,
        "tagline": "", "genre": ["Drama"], "studio": ["Harbour Light Films"],
        "uniqueid": {"tmdb": "400001", "imdb": "tt0000002"},
        "ratings": {"imdb": {"rating": 7.1, "votes": 48210, "default": True}},
        "rating": 7.1, "votes": 48210,
    }
    item_row = {"tmdb_id": "400001", "imdb_id": "tt0000002", "ids_checked": TODAY,
                "last_seen": TODAY, "match_note": ""}
    fs_map = {
        "mpaa": _fs("updated", source="tmdb", next_retry="2026-12-12",
                    written=fingerprint("NL:12")),
        "rating": _fs("updated", source="imdb", next_retry="2026-11-26",
                      written=json.dumps({"imdb": "demo"})),
        "votes": _fs("updated", source="imdb", next_retry="2026-11-26",
                     written=json.dumps({"imdb": "demo"})),
        "plot": _fs("nfo"),
        "genre": _fs("unchanged", source="tmdb", count=4),
        "tagline": _fs("nothing", next_retry="2026-11-26"),
    }
    keep = {"mpaa": {"original": "Rated PG-13", "has_original": True,
                     "locked": False, "written_fp": fingerprint("NL:12")}}
    return MOVIE, item, item_row, fs_map, keep, ""


PRESETS = {"episode": episode, "episode-dutch": episode_dutch, "episode-not-matched": episode_not_matched, "movie": movie}

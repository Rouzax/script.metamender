"""Tests for the TMDB provider module."""
from __future__ import annotations

from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from resources.lib.providers.tmdb import (
    KEY_OK,
    KEY_REFUSED,
    KEY_UNCHECKED,
    _normalize_certs,
    _parse_directors,
    _parse_movie_certifications,
    _parse_trailer,
    _parse_tv_certifications,
    _parse_writers,
    check_api_key,
    fetch_episode_external_ids,
    fetch_movie,
    fetch_season,
    fetch_tvshow,
    fetch_tvshow_external_ids,
    find_episode_by_tvdb,
    resolve_id,
)
from resources.lib.utils import ApiKeyError, TransientProviderError


# =============================================================================
# Fixtures: realistic TMDB API responses
# =============================================================================

MOVIE_RESPONSE = {
    "id": 666277,
    "title": "Past Lives",
    "tagline": "What if?",
    "overview": "After decades apart, childhood friends reunite.",
    "vote_average": 7.702,
    "vote_count": 2330,
    "runtime": 106,
    "release_date": "2023-06-02",
    "original_title": "Past Lives",
    "genres": [
        {"id": 18, "name": "Drama"},
        {"id": 10749, "name": "Romance"},
    ],
    "production_companies": [
        {"name": "A24"},
        {"name": "Killer Films"},
    ],
    "production_countries": [
        {"name": "United States of America"},
        {"name": "South Korea"},
    ],
    "credits": {
        "crew": [
            {
                "name": "Celine Song",
                "job": "Director",
                "department": "Directing",
            },
            {
                "name": "Celine Song",
                "job": "Writer",
                "department": "Writing",
            },
        ],
    },
    "videos": {
        "results": [
            {
                "type": "Trailer",
                "site": "YouTube",
                "key": "kA244xewjcI",
                "name": "Official Trailer",
            },
            {
                "type": "Teaser",
                "site": "YouTube",
                "key": "abc123",
                "name": "Teaser",
            },
        ],
    },
    "release_dates": {
        "results": [
            {
                "iso_3166_1": "US",
                "release_dates": [
                    {"certification": "PG-13", "type": 3},
                ],
            },
            {
                "iso_3166_1": "GB",
                "release_dates": [
                    {"certification": "12A", "type": 3},
                ],
            },
            {
                "iso_3166_1": "DE",
                "release_dates": [
                    {"certification": "6", "type": 3},
                ],
            },
        ],
    },
}

SEASON_RESPONSE = {
    "episodes": [
        {
            "id": 4014249,
            "episode_number": 1,
            "season_number": 2,
            "name": "Call Tracking",
            "overview": "Peter goes AWOL.",
            "vote_average": 6.764,
            "vote_count": 36,
            "air_date": "2025-01-23",
            "runtime": 51,
            "crew": [
                {
                    "name": "Guy Ferland",
                    "job": "Director",
                    "department": "Directing",
                },
                {
                    "name": "Shawn Ryan",
                    "job": "Writer",
                    "department": "Writing",
                },
            ],
        },
        {
            "id": 4053246,
            "episode_number": 2,
            "season_number": 2,
            "name": "Disconnected",
            "overview": "Peter and Rose race.",
            "vote_average": 7.172,
            "vote_count": 32,
            "air_date": "2025-01-23",
            "runtime": 52,
            "crew": [
                {
                    "name": "Guy Ferland",
                    "job": "Director",
                    "department": "Directing",
                },
                {
                    "name": "Tiffany Shaw Ho",
                    "job": "Writer",
                    "department": "Writing",
                },
            ],
        },
    ],
}

TVSHOW_RESPONSE = {
    "id": 129552,
    "name": "The Night Agent",
    "overview": "An FBI agent unravels a conspiracy.",
    "vote_average": 7.713,
    "vote_count": 1162,
    "genres": [
        {"id": 10759, "name": "Action & Adventure"},
        {"id": 18, "name": "Drama"},
    ],
    "content_ratings": {
        "results": [
            {"iso_3166_1": "US", "rating": "TV-MA"},
            {"iso_3166_1": "GB", "rating": "15"},
            {"iso_3166_1": "NL", "rating": "16"},
        ],
    },
}

FIND_RESPONSE_TVDB = {
    "movie_results": [],
    "tv_results": [{"id": 129552}],
}

FIND_RESPONSE_IMDB = {
    "movie_results": [{"id": 666277}],
    "tv_results": [],
}

FIND_RESPONSE_EMPTY = {
    "movie_results": [],
    "tv_results": [],
}


def _mock_response(
    status_code: int = 200,
    json_data: Any = None,
    raise_for_json: bool = False,
) -> MagicMock:
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if raise_for_json:
        resp.json.side_effect = ValueError("No JSON")
    else:
        resp.json.return_value = json_data if json_data is not None else {}
    return resp


# =============================================================================
# Tests: internal helpers
# =============================================================================


class TestParseDirectors:
    def test_extracts_directors(self) -> None:
        credits = {
            "crew": [
                {"name": "Celine Song", "job": "Director", "department": "Directing"},
                {"name": "Someone", "job": "Producer", "department": "Production"},
            ],
        }
        assert _parse_directors(credits) == ["Celine Song"]

    def test_empty_crew(self) -> None:
        assert _parse_directors({}) == []
        assert _parse_directors({"crew": []}) == []


class TestParseWriters:
    def test_extracts_and_deduplicates_writers(self) -> None:
        credits = {
            "crew": [
                {"name": "Celine Song", "job": "Writer", "department": "Writing"},
                {"name": "Celine Song", "job": "Screenplay", "department": "Writing"},
                {"name": "Another Writer", "job": "Writer", "department": "Writing"},
            ],
        }
        result = _parse_writers(credits)
        assert result == ["Celine Song", "Another Writer"]

    def test_empty_crew(self) -> None:
        assert _parse_writers({}) == []


class TestParseTrailer:
    def test_finds_first_youtube_trailer(self) -> None:
        videos = {
            "results": [
                {"type": "Teaser", "site": "YouTube", "key": "abc123"},
                {"type": "Trailer", "site": "YouTube", "key": "kA244xewjcI"},
                {"type": "Trailer", "site": "YouTube", "key": "second"},
            ],
        }
        expected = "plugin://plugin.video.youtube/?action=play_video&videoid=kA244xewjcI"
        assert _parse_trailer(videos) == expected

    def test_returns_none_when_no_trailer(self) -> None:
        videos = {
            "results": [
                {"type": "Teaser", "site": "YouTube", "key": "abc123"},
            ],
        }
        assert _parse_trailer(videos) is None

    def test_returns_none_for_empty_videos(self) -> None:
        assert _parse_trailer({}) is None
        assert _parse_trailer({"results": []}) is None


class TestParseMovieCertifications:
    def test_extracts_certifications(self) -> None:
        release_dates = {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [{"certification": "PG-13", "type": 3}],
                },
                {
                    "iso_3166_1": "GB",
                    "release_dates": [{"certification": "12A", "type": 3}],
                },
            ],
        }
        result = _parse_movie_certifications(release_dates)
        assert result == {"US": "PG-13", "GB": "12A"}

    def test_takes_first_nonempty_certification(self) -> None:
        release_dates = {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"certification": "", "type": 1},
                        {"certification": "R", "type": 3},
                    ],
                },
            ],
        }
        result = _parse_movie_certifications(release_dates)
        assert result == {"US": "R"}

    def test_skips_empty_certifications(self) -> None:
        release_dates = {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [{"certification": "", "type": 3}],
                },
            ],
        }
        result = _parse_movie_certifications(release_dates)
        assert result == {}


class TestParseTvCertifications:
    def test_extracts_certifications(self) -> None:
        content_ratings = {
            "results": [
                {"iso_3166_1": "US", "rating": "TV-MA"},
                {"iso_3166_1": "GB", "rating": "15"},
            ],
        }
        result = _parse_tv_certifications(content_ratings)
        assert result == {"US": "TV-MA", "GB": "15"}

    def test_skips_empty_ratings(self) -> None:
        content_ratings = {
            "results": [
                {"iso_3166_1": "US", "rating": ""},
            ],
        }
        result = _parse_tv_certifications(content_ratings)
        assert result == {}


class TestNormalizeCerts:
    def test_normalizes_se_tv_format(self) -> None:
        certs = {"SE": "Från 7 år", "US": "PG-13"}
        result = _normalize_certs(certs)
        assert result["SE"] == "7"
        assert result["US"] == "PG-13"

    def test_leaves_already_canonical(self) -> None:
        certs = {"SE": "15", "DE": "12"}
        result = _normalize_certs(certs)
        assert result == {"SE": "15", "DE": "12"}


# =============================================================================
# Tests: fetch_movie
# =============================================================================


class TestFetchMovie:
    @patch("resources.lib.providers.http._send")
    def test_returns_all_fields(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)

        result = fetch_movie("666277", "fake_key")

        assert result is not None
        assert result["rating"] == 7.702
        assert result["votes"] == 2330
        assert result["plot"] == "After decades apart, childhood friends reunite."
        assert result["tagline"] == "What if?"
        assert result["runtime"] == 106
        assert result["premiered"] == "2023-06-02"
        assert result["original_title"] == "Past Lives"
        assert result["genres"] == ["Drama", "Romance"]
        assert result["studios"] == ["A24", "Killer Films"]
        assert result["countries"] == [
            "United States of America",
            "South Korea",
        ]

    @patch("resources.lib.providers.http._send")
    def test_directors_parsed(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)
        result = fetch_movie("666277", "fake_key")
        assert result is not None
        assert result["directors"] == ["Celine Song"]

    @patch("resources.lib.providers.http._send")
    def test_writers_parsed_and_deduplicated(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)
        result = fetch_movie("666277", "fake_key")
        assert result is not None
        # Celine Song appears once in fixture with job=Writer, department=Writing
        assert result["writers"] == ["Celine Song"]

    @patch("resources.lib.providers.http._send")
    def test_trailer_formatted_as_youtube_url(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)
        result = fetch_movie("666277", "fake_key")
        assert result is not None
        expected = "plugin://plugin.video.youtube/?action=play_video&videoid=kA244xewjcI"
        assert result["trailer"] == expected

    @patch("resources.lib.providers.http._send")
    def test_trailer_none_when_no_trailer(self, mock_get: MagicMock) -> None:
        response = dict(MOVIE_RESPONSE)
        response["videos"] = {"results": []}
        mock_get.return_value = _mock_response(200, response)
        result = fetch_movie("666277", "fake_key")
        assert result is not None
        assert result["trailer"] is None

    @patch("resources.lib.providers.http._send")
    def test_certifications_parsed(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)
        result = fetch_movie("666277", "fake_key")
        assert result is not None
        assert result["certifications"] == {
            "US": "PG-13",
            "GB": "12A",
            "DE": "6",
        }

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        import requests as req

        mock_get.side_effect = req.ConnectionError("timeout")
        with pytest.raises(TransientProviderError):
            fetch_movie("666277", "fake_key")

    @patch("resources.lib.providers.http._send")
    def test_raises_api_key_error_on_401(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(401)
        with pytest.raises(ApiKeyError) as exc_info:
            fetch_movie("666277", "fake_key")
        assert exc_info.value.provider == "tmdb"

    @patch("resources.lib.providers.http._send")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)
        result = fetch_movie("666277", "fake_key")
        assert result is None

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_invalid_json(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, raise_for_json=True)
        with pytest.raises(TransientProviderError):
            fetch_movie("666277", "fake_key")

    @patch("resources.lib.providers.http._send")
    def test_passes_language_param(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)
        fetch_movie("666277", "fake_key", language="nl-NL")
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["language"] == "nl-NL"

    @patch("resources.lib.providers.http._send")
    def test_se_certifications_normalized(self, mock_get: MagicMock) -> None:
        response = dict(MOVIE_RESPONSE)
        response["release_dates"] = {
            "results": [
                {
                    "iso_3166_1": "SE",
                    "release_dates": [
                        {"certification": "Från 11 år", "type": 3},
                    ],
                },
            ],
        }
        mock_get.return_value = _mock_response(200, response)
        result = fetch_movie("666277", "fake_key")
        assert result is not None
        assert result["certifications"]["SE"] == "11"


# =============================================================================
# Tests: fetch_season
# =============================================================================


class TestFetchSeason:
    @patch("resources.lib.providers.http._send")
    def test_returns_episode_list(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, SEASON_RESPONSE)

        result = fetch_season("129552", 2, "fake_key")

        assert result is not None
        assert len(result) == 2

        ep1 = result[0]
        assert ep1["tmdb_id"] == "4014249"
        assert ep1["episode_number"] == 1
        assert ep1["season_number"] == 2
        assert ep1["title"] == "Call Tracking"
        assert ep1["rating"] == 6.764
        assert ep1["votes"] == 36
        assert ep1["plot"] == "Peter goes AWOL."
        assert ep1["firstaired"] == "2025-01-23"
        assert ep1["runtime"] == 51

    @patch("resources.lib.providers.http._send")
    def test_tmdb_id_per_episode(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, SEASON_RESPONSE)

        result = fetch_season("129552", 2, "fake_key")

        assert result is not None
        assert result[0]["tmdb_id"] == "4014249"
        assert result[1]["tmdb_id"] == "4053246"

    @patch("resources.lib.providers.http._send")
    def test_episode_directors_and_writers(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, SEASON_RESPONSE)

        result = fetch_season("129552", 2, "fake_key")

        assert result is not None
        ep1 = result[0]
        assert ep1["directors"] == ["Guy Ferland"]
        assert ep1["writers"] == ["Shawn Ryan"]

        ep2 = result[1]
        assert ep2["directors"] == ["Guy Ferland"]
        assert ep2["writers"] == ["Tiffany Shaw Ho"]

    @patch("resources.lib.providers.http._send")
    def test_handles_empty_episodes(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {"episodes": []})

        result = fetch_season("129552", 1, "fake_key")

        assert result is not None
        assert result == []

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        import requests as req

        mock_get.side_effect = req.ConnectionError("timeout")
        with pytest.raises(TransientProviderError):
            fetch_season("129552", 2, "fake_key")

    @patch("resources.lib.providers.http._send")
    def test_raises_api_key_error_on_401(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(401)
        with pytest.raises(ApiKeyError):
            fetch_season("129552", 2, "fake_key")

    @patch("resources.lib.providers.http._send")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)
        result = fetch_season("129552", 2, "fake_key")
        assert result is None


# =============================================================================
# Tests: fetch_tvshow
# =============================================================================


class TestFetchTvshow:
    @patch("resources.lib.providers.http._send")
    def test_returns_metadata(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, TVSHOW_RESPONSE)

        result = fetch_tvshow("129552", "fake_key")

        assert result is not None
        assert result["rating"] == 7.713
        assert result["votes"] == 1162
        assert result["plot"] == "An FBI agent unravels a conspiracy."
        assert result["genres"] == ["Action & Adventure", "Drama"]

    @patch("resources.lib.providers.http._send")
    def test_certifications_parsed(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, TVSHOW_RESPONSE)

        result = fetch_tvshow("129552", "fake_key")

        assert result is not None
        assert result["certifications"] == {
            "US": "TV-MA",
            "GB": "15",
            "NL": "16",
        }

    @patch("resources.lib.providers.http._send")
    def test_raises_api_key_error_on_401(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(401)
        with pytest.raises(ApiKeyError):
            fetch_tvshow("129552", "fake_key")

    @patch("resources.lib.providers.http._send")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)
        result = fetch_tvshow("129552", "fake_key")
        assert result is None

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_invalid_json(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, raise_for_json=True)
        with pytest.raises(TransientProviderError):
            fetch_tvshow("129552", "fake_key")

    @patch("resources.lib.providers.http._send")
    def test_se_tv_certs_normalized(self, mock_get: MagicMock) -> None:
        response = dict(TVSHOW_RESPONSE)
        response["content_ratings"] = {
            "results": [
                {"iso_3166_1": "SE", "rating": "Från 15 år"},
                {"iso_3166_1": "US", "rating": "TV-14"},
            ],
        }
        mock_get.return_value = _mock_response(200, response)
        result = fetch_tvshow("129552", "fake_key")
        assert result is not None
        assert result["certifications"]["SE"] == "15"
        assert result["certifications"]["US"] == "TV-14"


# =============================================================================
# Tests: resolve_id
# =============================================================================


class TestResolveId:
    """resolve_id goes through provider_get, so it shares the one HTTP
    classification: 404/no-match is a clean None, but 5xx/timeout/malformed
    JSON raise TransientProviderError and count toward the circuit breaker.
    """

    @patch("resources.lib.providers.http._send")
    def test_resolves_from_tvdb_id(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, FIND_RESPONSE_TVDB)

        result = resolve_id("fake_key", "tvshow", tvdb_id="395557")

        assert result == "129552"
        call_kwargs = mock_get.call_args
        assert "tvdb_id" in call_kwargs[1]["params"]["external_source"]

    @patch("resources.lib.providers.http._send")
    def test_resolves_from_imdb_id(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, FIND_RESPONSE_IMDB)

        result = resolve_id("fake_key", "movie", imdb_id="tt13238346")

        assert result == "666277"
        call_kwargs = mock_get.call_args
        assert "imdb_id" in call_kwargs[1]["params"]["external_source"]

    @patch("resources.lib.providers.http._send")
    def test_prefers_tvdb_over_imdb(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, FIND_RESPONSE_TVDB)

        resolve_id("fake_key", "tvshow", tvdb_id="395557", imdb_id="tt13238346")

        call_kwargs = mock_get.call_args
        assert "tvdb_id" in call_kwargs[1]["params"]["external_source"]
        assert "/find/395557" in call_kwargs[0][0]

    @patch("resources.lib.providers.http._send")
    def test_returns_none_when_not_found(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, FIND_RESPONSE_EMPTY)

        result = resolve_id("fake_key", "tvshow", tvdb_id="999999")

        assert result is None

    @patch("resources.lib.providers.http._send")
    def test_returns_none_when_no_ids_given(self, mock_get: MagicMock) -> None:
        result = resolve_id("fake_key", "movie")
        assert result is None
        mock_get.assert_not_called()

    @patch("resources.lib.providers.http._send")
    def test_returns_none_on_404(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)
        result = resolve_id("fake_key", "tvshow", tvdb_id="395557")
        assert result is None

    @patch("resources.lib.providers.http._send")
    def test_raises_api_key_error_on_401(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(401)
        with pytest.raises(ApiKeyError):
            resolve_id("fake_key", "tvshow", tvdb_id="395557")

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        import requests as req

        mock_get.side_effect = req.ConnectionError("timeout")
        with pytest.raises(TransientProviderError):
            resolve_id("fake_key", "tvshow", tvdb_id="395557")

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_5xx(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(500)
        with pytest.raises(TransientProviderError):
            resolve_id("fake_key", "tvshow", tvdb_id="395557")

    @patch("resources.lib.providers.http._send")
    def test_raises_transient_on_invalid_json(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, raise_for_json=True)
        with pytest.raises(TransientProviderError):
            resolve_id("fake_key", "tvshow", tvdb_id="395557")

    @patch("resources.lib.providers.http._send")
    def test_movie_reads_only_movie_results(self, mock_get: MagicMock) -> None:
        """A TVDB id names a show on TMDB; a movie must not take its id."""
        mock_get.return_value = _mock_response(200, FIND_RESPONSE_TVDB)
        assert resolve_id("fake_key", "movie", tvdb_id="395557") is None

    @patch("resources.lib.providers.http._send")
    def test_show_reads_only_tv_results(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, FIND_RESPONSE_IMDB)
        assert resolve_id("fake_key", "tvshow", imdb_id="tt13238346") is None

    @patch("resources.lib.providers.http._send")
    def test_show_ignores_movie_result_listed_first(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {
            "movie_results": [{"id": 1}], "tv_results": [{"id": 2}],
        })
        assert resolve_id("fake_key", "tvshow", tvdb_id="395557") == "2"

    @patch("resources.lib.providers.http._send")
    def test_imdb_tried_when_tvdb_answers_nothing(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = [
            _mock_response(200, FIND_RESPONSE_EMPTY),
            _mock_response(200, FIND_RESPONSE_IMDB),
        ]
        result = resolve_id("fake_key", "movie", tvdb_id="1", imdb_id="tt13238346")
        assert result == "666277"
        sources = [c[1]["params"]["external_source"] for c in mock_get.call_args_list]
        assert sources == ["tvdb_id", "imdb_id"]

    @patch("resources.lib.providers.http._send")
    def test_imdb_tried_when_tvdb_is_404(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = [
            _mock_response(404),
            _mock_response(200, FIND_RESPONSE_TVDB),
        ]
        assert resolve_id("fake_key", "tvshow", tvdb_id="1", imdb_id="tt9") == "129552"

    @patch("resources.lib.providers.http._send")
    def test_tvdb_transient_does_not_fall_through(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(500)
        with pytest.raises(TransientProviderError):
            resolve_id("fake_key", "tvshow", tvdb_id="1", imdb_id="tt9")
        assert mock_get.call_count == 1


class TestCrossReferences:
    @patch("resources.lib.providers.http._send")
    def test_episode_external_ids_returns_both(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(
            200, {"imdb_id": "tt123", "tvdb_id": 4567},
        )
        assert fetch_episode_external_ids("1", 1, 2, "k") == {
            "imdb_id": "tt123", "tvdb_id": "4567",
        }

    @patch("resources.lib.providers.http._send")
    def test_episode_external_ids_omits_empty(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(
            200, {"imdb_id": "", "tvdb_id": None},
        )
        assert fetch_episode_external_ids("1", 1, 2, "k") is None

    @patch("resources.lib.providers.http._send")
    def test_find_episode_by_tvdb_maps_fields(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {
            "tv_episode_results": [
                {"id": 99, "show_id": 12, "season_number": 3, "episode_number": 4},
            ],
        })
        assert find_episode_by_tvdb("k", "340249") == {
            "tmdb_id": "99", "show_tmdb_id": "12", "season": 3, "episode": 4,
        }
        assert mock_get.call_args[1]["params"]["external_source"] == "tvdb_id"
        assert mock_get.call_args[0][0].endswith("/find/340249")

    @patch("resources.lib.providers.http._send")
    def test_find_episode_by_tvdb_empty_is_none(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {"tv_episode_results": []})
        assert find_episode_by_tvdb("k", "1") is None

    @pytest.mark.parametrize("numbers", [
        {"season_number": None, "episode_number": 4},
        {"season_number": 3},
        {"episode_number": 4},
    ])
    @patch("resources.lib.providers.http._send")
    def test_find_episode_by_tvdb_without_numbers_is_none(
        self, mock_get: MagicMock, numbers: Dict[str, Any],
    ) -> None:
        # A missing number read as 0 would match a Specials episode.
        result = {"id": 99, "show_id": 12}
        result.update(numbers)
        mock_get.return_value = _mock_response(200, {"tv_episode_results": [result]})
        assert find_episode_by_tvdb("k", "340249") is None

    @patch("resources.lib.providers.http._send")
    def test_find_episode_by_tvdb_404_is_none(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)
        assert find_episode_by_tvdb("k", "1") is None

    @patch("resources.lib.providers.http._send")
    def test_tvshow_external_ids(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(
            200, {"imdb_id": "tt9", "tvdb_id": 80767},
        )
        assert fetch_tvshow_external_ids("k", "55") == {
            "imdb_id": "tt9", "tvdb_id": "80767",
        }
        assert mock_get.call_args[0][0].endswith("/tv/55/external_ids")

    @patch("resources.lib.providers.http._send")
    def test_tvshow_external_ids_empty_is_none(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {"imdb_id": None})
        assert fetch_tvshow_external_ids("k", "55") is None

    @pytest.mark.parametrize("call", [
        lambda: fetch_episode_external_ids("1", 1, 2, "k"),
        lambda: find_episode_by_tvdb("k", "1"),
        lambda: fetch_tvshow_external_ids("k", "1"),
    ])
    @patch("resources.lib.providers.http._send")
    def test_transient_propagates(self, mock_get: MagicMock, call: Any) -> None:
        mock_get.return_value = _mock_response(503)
        with pytest.raises(TransientProviderError):
            call()


# =============================================================================
# Tests: titles in the requested language
# =============================================================================


class TestTitles:
    """TMDB answers with the original title when it has no translation in the
    requested language; that is not a title in the language, so it is ""."""

    @staticmethod
    def _movie(**fields: Any) -> dict:
        return dict(MOVIE_RESPONSE, **fields)

    @patch("resources.lib.providers.http._send")
    def test_translated_movie_title(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, self._movie(
            title="Het Monster van Parijs", original_title="Un monstre à Paris",
            original_language="fr",
        ))
        result = fetch_movie("1", "k", language="nl-NL")
        assert result is not None and result["title"] == "Het Monster van Parijs"

    @patch("resources.lib.providers.http._send")
    def test_untranslated_movie_title_is_empty(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, self._movie(
            title="Past Lives", original_title="Past Lives", original_language="en",
        ))
        result = fetch_movie("1", "k", language="nl-NL")
        assert result is not None and result["title"] == ""

    @patch("resources.lib.providers.http._send")
    def test_original_in_the_requested_language_counts(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, self._movie(
            title="Zwartboek", original_title="Zwartboek", original_language="nl",
        ))
        result = fetch_movie("1", "k", language="nl-NL")
        assert result is not None and result["title"] == "Zwartboek"

    @patch("resources.lib.providers.http._send")
    def test_unknown_original_language_keeps_the_title(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, self._movie())
        result = fetch_movie("1", "k", language="nl-NL")
        assert result is not None and result["title"] == "Past Lives"

    @patch("resources.lib.providers.http._send")
    def test_show_title(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, dict(
            TVSHOW_RESPONSE, name="The Night Agent", original_name="The Night Agent",
            original_language="en",
        ))
        assert fetch_tvshow("1", "k", language="nl-NL")["title"] == ""
        assert fetch_tvshow("1", "k", language="en-US")["title"] == "The Night Agent"


# =============================================================================
# Tests: check_api_key
# =============================================================================


@patch("resources.lib.providers.tmdb.provider_get", return_value={"images": {}})
def test_check_api_key_ok(mock_get):
    assert check_api_key("abc") == KEY_OK
    assert mock_get.call_args.kwargs["params"] == {"api_key": "abc"}
    assert mock_get.call_args.args[0].endswith("/configuration")


@patch("resources.lib.providers.tmdb.provider_get", side_effect=ApiKeyError("tmdb"))
def test_check_api_key_refused_on_401(mock_get):
    assert check_api_key("abc") == KEY_REFUSED


@patch("resources.lib.providers.tmdb.provider_get",
       side_effect=TransientProviderError("http 503", status=503))
def test_check_api_key_unchecked_on_transient(mock_get):
    assert check_api_key("abc") == KEY_UNCHECKED


@patch("resources.lib.providers.tmdb.provider_get", return_value=None)
def test_check_api_key_unchecked_on_no_answer(mock_get):
    assert check_api_key("abc") == KEY_UNCHECKED


@patch("resources.lib.providers.tmdb.provider_get", return_value={})
def test_check_api_key_strips_and_rejects_empty(mock_get):
    assert check_api_key("   ") == KEY_REFUSED
    mock_get.assert_not_called()
    check_api_key(" abc ")
    assert mock_get.call_args.kwargs["params"] == {"api_key": "abc"}

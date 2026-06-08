"""Tests for the IMDb API provider module (imdbapi.dev)."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from resources.lib.providers.imdbapi import (
    _extract_names,
    _format_release_date,
    _runtime_minutes,
    fetch_certificates,
    fetch_episode,
    fetch_movie,
    fetch_season,
)
from resources.lib.utils import TransientProviderError


# =============================================================================
# Fixtures: realistic imdbapi.dev responses
# =============================================================================

MOVIE_RESPONSE = {
    "id": "tt0133093",
    "type": "movie",
    "primaryTitle": "The Matrix",
    "startYear": 1999,
    "runtimeSeconds": 8160,
    "genres": ["Action", "Sci-Fi"],
    "rating": {
        "aggregateRating": 8.7,
        "voteCount": 2251728,
    },
    "metacritic": {
        "score": 73,
        "reviewCount": 36,
    },
    "plot": "A computer hacker discovers reality is a simulation.",
    "directors": [
        {"id": "nm0905154", "displayName": "Lana Wachowski"},
        {"id": "nm0905152", "displayName": "Lilly Wachowski"},
    ],
    "writers": [
        {"id": "nm0905152", "displayName": "Lilly Wachowski"},
        {"id": "nm0905154", "displayName": "Lana Wachowski"},
    ],
    "originCountries": [
        {"code": "US", "name": "United States"},
        {"code": "AU", "name": "Australia"},
    ],
}

EPISODE_RESPONSE = {
    "id": "tt27356735",
    "type": "tvEpisode",
    "primaryTitle": "Call Tracking",
    "startYear": 2025,
    "runtimeSeconds": 3000,
    "genres": ["Action", "Drama", "Thriller"],
    "rating": {
        "aggregateRating": 7.2,
        "voteCount": 2690,
    },
    "plot": "Peter goes AWOL to track an American agent.",
    "directors": [
        {"id": "nm0272704", "displayName": "Guy Ferland"},
    ],
    "writers": [
        {"id": "nm0752841", "displayName": "Shawn Ryan"},
        {"id": "nm5094200", "displayName": "Matthew Quirk"},
    ],
}

SEASON_RESPONSE_PAGE1 = {
    "episodes": [
        {
            "id": "tt27356735",
            "title": "Call Tracking",
            "season": "2",
            "episodeNumber": 1,
            "runtimeSeconds": 3000,
            "plot": "Peter goes AWOL.",
            "rating": {"aggregateRating": 7.2, "voteCount": 2738},
            "releaseDate": {"year": 2025, "month": 1, "day": 23},
        },
        {
            "id": "tt31170319",
            "title": "Disconnected",
            "season": "2",
            "episodeNumber": 2,
            "runtimeSeconds": 3060,
            "plot": "Peter and Rose race.",
            "rating": {"aggregateRating": 7.2, "voteCount": 2365},
            "releaseDate": {"year": 2025, "month": 1, "day": 23},
        },
    ],
    "nextPageToken": "page2token",
    "totalCount": 3,
}

SEASON_RESPONSE_PAGE2 = {
    "episodes": [
        {
            "id": "tt31170320",
            "title": "Government Property",
            "season": "2",
            "episodeNumber": 3,
            "runtimeSeconds": 3240,
            "plot": "Catherine aids Peter.",
            "rating": {"aggregateRating": 7.2, "voteCount": 2179},
            "releaseDate": {"year": 2025, "month": 1, "day": 23},
        },
    ],
    "totalCount": 3,
}

CERTIFICATES_RESPONSE = {
    "certificates": [
        {
            "rating": "16",
            "country": {"code": "NL", "name": "Netherlands"},
            "attributes": ["original rating"],
        },
        {
            "rating": "12",
            "country": {"code": "NL", "name": "Netherlands"},
            "attributes": ["re-rating"],
        },
        {
            "rating": "R",
            "country": {"code": "US", "name": "United States"},
            "attributes": ["certificate #36569"],
        },
        {
            "rating": "TV-14",
            "country": {"code": "US", "name": "United States"},
        },
        {
            "rating": "15",
            "country": {"code": "GB", "name": "United Kingdom"},
        },
    ],
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


class TestExtractNames:
    def test_extracts_display_names(self) -> None:
        people = [
            {"id": "nm1", "displayName": "Alice"},
            {"id": "nm2", "displayName": "Bob"},
        ]
        assert _extract_names(people) == ["Alice", "Bob"]

    def test_skips_missing_display_name(self) -> None:
        people = [
            {"id": "nm1", "displayName": "Alice"},
            {"id": "nm2"},
        ]
        assert _extract_names(people) == ["Alice"]

    def test_empty_list(self) -> None:
        assert _extract_names([]) == []

    def test_none(self) -> None:
        assert _extract_names(None) == []


class TestFormatReleaseDate:
    def test_full_date(self) -> None:
        assert _format_release_date({"year": 2025, "month": 1, "day": 23}) == "2025-01-23"

    def test_year_only(self) -> None:
        assert _format_release_date({"year": 2025}) == "2025"

    def test_none(self) -> None:
        assert _format_release_date(None) == ""

    def test_empty_dict(self) -> None:
        assert _format_release_date({}) == ""


class TestRuntimeMinutes:
    def test_converts_seconds(self) -> None:
        assert _runtime_minutes(8160) == 136

    def test_rounds(self) -> None:
        assert _runtime_minutes(3000) == 50

    def test_none(self) -> None:
        assert _runtime_minutes(None) is None

    def test_zero(self) -> None:
        assert _runtime_minutes(0) is None


# =============================================================================
# Tests: fetch_movie
# =============================================================================


class TestFetchMovie:
    @patch("resources.lib.providers.http.requests.get")
    def test_returns_all_fields(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)

        result = fetch_movie("tt0133093")

        assert result is not None
        assert result["rating"] == 8.7
        assert result["votes"] == 2251728
        assert result["plot"] == "A computer hacker discovers reality is a simulation."
        assert result["directors"] == ["Lana Wachowski", "Lilly Wachowski"]
        assert result["writers"] == ["Lilly Wachowski", "Lana Wachowski"]
        assert result["genres"] == ["Action", "Sci-Fi"]
        assert result["countries"] == ["United States", "Australia"]
        assert result["runtime"] == 136
        assert result["premiered"] == "1999"
        assert result["metacritic"] == 73

    @patch("resources.lib.providers.http.requests.get")
    def test_handles_missing_metacritic(self, mock_get: MagicMock) -> None:
        response = dict(MOVIE_RESPONSE)
        response["metacritic"] = None
        mock_get.return_value = _mock_response(200, response)

        result = fetch_movie("tt0133093")

        assert result is not None
        assert result["metacritic"] is None

    @patch("resources.lib.providers.http.requests.get")
    def test_handles_missing_rating(self, mock_get: MagicMock) -> None:
        response = dict(MOVIE_RESPONSE)
        response["rating"] = None
        mock_get.return_value = _mock_response(200, response)

        result = fetch_movie("tt0133093")

        assert result is not None
        assert result["rating"] == 0.0
        assert result["votes"] == 0

    @patch("resources.lib.providers.http.requests.get")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("timeout")

        with pytest.raises(TransientProviderError):
            fetch_movie("tt0133093")

    @patch("resources.lib.providers.http.requests.get")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)

        result = fetch_movie("tt0133093")

        assert result is None

    @patch("resources.lib.providers.http.requests.get")
    def test_raises_transient_on_invalid_json(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, raise_for_json=True)

        with pytest.raises(TransientProviderError):
            fetch_movie("tt0133093")

    @patch("resources.lib.providers.http.requests.get")
    def test_no_auth_headers(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, MOVIE_RESPONSE)

        fetch_movie("tt0133093")

        call_kwargs = mock_get.call_args
        params = call_kwargs[1].get("params") or {}
        assert "api_key" not in params
        assert "apikey" not in params


# =============================================================================
# Tests: fetch_episode
# =============================================================================


class TestFetchEpisode:
    @patch("resources.lib.providers.http.requests.get")
    def test_returns_episode_fields(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, EPISODE_RESPONSE)

        result = fetch_episode("tt27356735")

        assert result is not None
        assert result["rating"] == 7.2
        assert result["votes"] == 2690
        assert result["plot"] == "Peter goes AWOL to track an American agent."
        assert result["title"] == "Call Tracking"
        assert result["directors"] == ["Guy Ferland"]
        assert result["writers"] == ["Shawn Ryan", "Matthew Quirk"]
        assert result["runtime"] == 50

    @patch("resources.lib.providers.http.requests.get")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("timeout")

        with pytest.raises(TransientProviderError):
            fetch_episode("tt27356735")

    @patch("resources.lib.providers.http.requests.get")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)

        result = fetch_episode("tt27356735")

        assert result is None


# =============================================================================
# Tests: fetch_season
# =============================================================================


class TestFetchSeason:
    @patch("resources.lib.providers.http.requests.get")
    def test_returns_episode_list(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {
            "episodes": SEASON_RESPONSE_PAGE1["episodes"],
            "totalCount": 2,
        })

        result = fetch_season("tt13918776", 2)

        assert result is not None
        assert len(result) == 2
        ep1 = result[0]
        assert ep1["imdb_id"] == "tt27356735"
        assert ep1["episode_number"] == 1
        assert ep1["season_number"] == 2
        assert ep1["title"] == "Call Tracking"
        assert ep1["rating"] == 7.2
        assert ep1["votes"] == 2738
        assert ep1["plot"] == "Peter goes AWOL."
        assert ep1["runtime"] == 50
        assert ep1["firstaired"] == "2025-01-23"

    @patch("resources.lib.providers.http.requests.get")
    def test_auto_paginates(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = [
            _mock_response(200, SEASON_RESPONSE_PAGE1),
            _mock_response(200, SEASON_RESPONSE_PAGE2),
        ]

        result = fetch_season("tt13918776", 2)

        assert result is not None
        assert len(result) == 3
        assert result[0]["title"] == "Call Tracking"
        assert result[2]["title"] == "Government Property"
        assert mock_get.call_count == 2

    @patch("resources.lib.providers.http.requests.get")
    def test_pagination_passes_token(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = [
            _mock_response(200, SEASON_RESPONSE_PAGE1),
            _mock_response(200, SEASON_RESPONSE_PAGE2),
        ]

        fetch_season("tt13918776", 2)

        second_call_params = mock_get.call_args_list[1][1]["params"]
        assert second_call_params["pageToken"] == "page2token"

    @patch("resources.lib.providers.http.requests.get")
    def test_handles_empty_season(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {"episodes": []})

        result = fetch_season("tt13918776", 1)

        assert result is not None
        assert result == []

    @patch("resources.lib.providers.http.requests.get")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("timeout")

        with pytest.raises(TransientProviderError):
            fetch_season("tt13918776", 2)

    @patch("resources.lib.providers.http.requests.get")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)

        result = fetch_season("tt13918776", 2)

        assert result is None


# =============================================================================
# Tests: fetch_certificates
# =============================================================================


class TestFetchCertificates:
    @patch("resources.lib.providers.http.requests.get")
    def test_groups_by_country(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, CERTIFICATES_RESPONSE)

        result = fetch_certificates("tt0133093")

        assert result is not None
        assert "NL" in result
        assert "US" in result
        assert "GB" in result

    @patch("resources.lib.providers.http.requests.get")
    def test_preserves_multiple_entries_per_country(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, CERTIFICATES_RESPONSE)

        result = fetch_certificates("tt0133093")

        assert result is not None
        assert len(result["NL"]) == 2
        assert result["NL"][0]["rating"] == "16"
        assert result["NL"][0]["attributes"] == ["original rating"]
        assert result["NL"][1]["rating"] == "12"
        assert result["NL"][1]["attributes"] == ["re-rating"]

    @patch("resources.lib.providers.http.requests.get")
    def test_preserves_attributes(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, CERTIFICATES_RESPONSE)

        result = fetch_certificates("tt0133093")

        assert result is not None
        assert len(result["US"]) == 2
        assert result["US"][0]["attributes"] == ["certificate #36569"]
        assert result["US"][1]["attributes"] == []

    @patch("resources.lib.providers.http.requests.get")
    def test_single_entry_country(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, CERTIFICATES_RESPONSE)

        result = fetch_certificates("tt0133093")

        assert result is not None
        assert len(result["GB"]) == 1
        assert result["GB"][0]["rating"] == "15"

    @patch("resources.lib.providers.http.requests.get")
    def test_handles_empty_certificates(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(200, {"certificates": []})

        result = fetch_certificates("tt0133093")

        assert result is not None
        assert result == {}

    @patch("resources.lib.providers.http.requests.get")
    def test_raises_transient_on_network_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.ConnectionError("timeout")

        with pytest.raises(TransientProviderError):
            fetch_certificates("tt0133093")

    @patch("resources.lib.providers.http.requests.get")
    def test_returns_none_on_non_200(self, mock_get: MagicMock) -> None:
        mock_get.return_value = _mock_response(404)

        result = fetch_certificates("tt0133093")

        assert result is None

"""Tests for the script.module.imdbratings adapter."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from resources.lib.providers import imdb_ratings
from resources.lib.utils import TransientProviderError


@pytest.fixture(autouse=True)
def _reset_warning_flag() -> None:
    imdb_ratings._unavailable_logged = False


def test_prepare_false_before_first_load(imdbratings_stub: MagicMock) -> None:
    imdbratings_stub.dataset_date.return_value = None
    assert imdb_ratings.prepare(lambda: False) is False


def test_prepare_true_after_load(imdbratings_stub: MagicMock) -> None:
    imdbratings_stub.dataset_date.return_value = "Tue, 03 Nov 2026 07:00:00 GMT"
    assert imdb_ratings.prepare(lambda: False) is True


def test_prepare_passes_abort(imdbratings_stub: MagicMock) -> None:
    def abort() -> bool:
        return False

    imdbratings_stub.dataset_date.return_value = "x"
    imdb_ratings.prepare(abort)
    imdbratings_stub.refresh.assert_called_once_with(abort=abort)


def test_prepare_refresh_error_falls_back_to_dataset(imdbratings_stub: MagicMock) -> None:
    imdbratings_stub.refresh.side_effect = RuntimeError("boom")
    imdbratings_stub.dataset_date.return_value = "old"
    with patch.object(imdb_ratings, "log") as log:
        assert imdb_ratings.prepare(lambda: False) is True
    log.exception.assert_called_once()
    assert log.exception.call_args.kwargs["event"] == "imdb_ratings.refresh_failed"
    imdbratings_stub.dataset_date.return_value = None
    assert imdb_ratings.prepare(lambda: False) is False


def test_lookup_absent_is_none(imdbratings_stub: MagicMock) -> None:
    imdbratings_stub.get_rating.return_value = None
    imdbratings_stub.dataset_date.return_value = "loaded"
    assert imdb_ratings.lookup("tt0000001") is None


def test_lookup_store_failing_is_transient(imdbratings_stub: MagicMock) -> None:
    imdbratings_stub.get_rating.return_value = None
    imdbratings_stub.dataset_date.return_value = None
    with pytest.raises(TransientProviderError):
        imdb_ratings.lookup("tt0000001")


def test_lookup_returns_rating_and_votes(imdbratings_stub: MagicMock) -> None:
    imdbratings_stub.get_rating.return_value = (7.4, 1234)
    assert imdb_ratings.lookup("tt0000001") == (7.4, 1234)
    imdbratings_stub.dataset_date.assert_not_called()


def test_module_missing_is_unavailable_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "imdbratings", None)
    with patch.object(imdb_ratings, "log") as log:
        assert imdb_ratings.prepare(lambda: False) is False
        assert imdb_ratings.prepare(lambda: False) is False
    log.warning.assert_called_once()
    assert log.warning.call_args.kwargs["event"] == "imdb_ratings.unavailable"
    with pytest.raises(TransientProviderError):
        imdb_ratings.lookup("tt0000001")

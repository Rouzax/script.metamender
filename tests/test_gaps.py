"""Tests for gap detection."""
from __future__ import annotations

from resources.lib.engine.gaps import is_field_empty


class TestIsFieldEmpty:
    def test_none_is_empty(self):
        assert is_field_empty("plot", None) is True

    def test_empty_string_is_empty(self):
        assert is_field_empty("plot", "") is True

    def test_zero_rating_is_empty(self):
        assert is_field_empty("rating", 0) is True
        assert is_field_empty("rating", 0.0) is True

    def test_nonzero_rating_is_not_empty(self):
        assert is_field_empty("rating", 7.5) is False

    def test_empty_list_is_empty(self):
        assert is_field_empty("director", []) is True

    def test_nonempty_list_is_not_empty(self):
        assert is_field_empty("director", ["Nolan"]) is False

    def test_nonempty_string_is_not_empty(self):
        assert is_field_empty("plot", "A great movie.") is False

    def test_zero_runtime_is_empty(self):
        assert is_field_empty("runtime", 0) is True

    def test_nonzero_runtime_is_not_empty(self):
        assert is_field_empty("runtime", 120) is False

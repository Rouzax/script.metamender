"""Tests for the show and season refresh (Update now on a show or season)."""
from __future__ import annotations

from unittest.mock import patch

from resources.lib.service import refresh
from resources.lib.service.refresh import Scope, resolve_scope, show_id_of

_SHOW = {"tvshowid": 7, "title": "Lost"}


def test_resolve_scope_for_a_show():
    with patch.object(refresh, "get_item_by_id", return_value=_SHOW):
        assert resolve_scope("tvshow", 7) == Scope(7, None, "Lost")


def test_resolve_scope_for_a_season():
    with patch.object(refresh, "get_season_info", return_value=(7, 2)), \
            patch.object(refresh, "get_item_by_id", return_value=_SHOW):
        assert resolve_scope("season", 31) == Scope(7, 2, "Lost")


def test_resolve_scope_unknown_or_missing():
    with patch.object(refresh, "get_season_info", return_value=None):
        assert resolve_scope("season", 31) is None
    with patch.object(refresh, "get_item_by_id", return_value=None):
        assert resolve_scope("tvshow", 7) is None
    assert resolve_scope("movie", 5) is None


def test_show_id_of():
    with patch.object(refresh, "get_season_info", return_value=(7, 2)), \
            patch.object(refresh, "get_episode_show_id", return_value=7):
        assert show_id_of("tvshow", 7) == 7
        assert show_id_of("season", 31) == 7
        assert show_id_of("episode", 100) == 7
        assert show_id_of("movie", 5) is None
    with patch.object(refresh, "get_episode_show_id", return_value=None):
        assert show_id_of("episode", 100) is None

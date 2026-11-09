"""Tests for show-title verification of unknown-type ids."""
from __future__ import annotations

from resources.lib.engine.idverify import normalize_title, show_matches


def test_buurman_matches_via_translation() -> None:
    assert show_matches("Buurman en Buurman", ["Pat a Mat", "Buurman en Buurman"])


def test_woezel_ampersand() -> None:
    assert show_matches("Woezel en Pip", ["Woezel & Pip"])


def test_different_show_rejected() -> None:
    assert not show_matches("Buurman en Buurman", ["Pat a Mat", "Pat & Mat"])


def test_diacritics() -> None:
    assert show_matches("Bébé Lilly", ["Bebe Lilly"])
    assert normalize_title("Ça Gaat Über") == normalize_title("ca gaat uber")


def test_conjunctions_unified_across_languages() -> None:
    expected = normalize_title("Tom and Jerry")
    for title in ("Tom & Jerry", "Tom en Jerry", "Tom und Jerry", "Tom et Jerry"):
        assert normalize_title(title) == expected


def test_punctuation_articles_and_whitespace() -> None:
    assert normalize_title("The  Office!") == normalize_title("Office, The")
    assert normalize_title("De Smurfen") == normalize_title("Smurfen")
    assert normalize_title("Bob's Burgers") == normalize_title("Bobs Burgers")


def test_trailing_year_ignored() -> None:
    assert show_matches("Doctor Who (2005)", ["Doctor Who"])
    assert show_matches("Doctor Who", ["Doctor Who (2005)"])


def test_empty_never_matches() -> None:
    assert not show_matches("", [""])
    assert not show_matches("The", ["A"])
    assert not show_matches("Show", [])

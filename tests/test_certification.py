"""Tests for certification value handling."""
from __future__ import annotations

from resources.lib.engine.certification import (
    classify_current,
    format_mpaa,
    normalize_title,
    parse_mpaa,
    sanitize_prefix,
    titles_match,
    years_match,
)

NL = frozenset({"AL", "6", "9", "12", "14", "16", "18"})
US = frozenset({"G", "PG", "PG-13", "R", "NC-17", "TV-14"})


def test_parse_forms():
    assert parse_mpaa("NL:12") == ("NL:", "12")
    assert parse_mpaa("nl:12") == ("NL:", "12")
    assert parse_mpaa("Rated PG-13") == ("Rated ", "PG-13")
    assert parse_mpaa("rated R") == ("Rated ", "R")
    assert parse_mpaa(" 16 ") == ("", "16")
    assert parse_mpaa("DE:FSK 12") == ("DE:", "FSK 12")
    assert parse_mpaa("") == ("", "")


def test_format():
    assert format_mpaa("12", "NL:") == "NL:12"
    assert format_mpaa("PG-13", "Rated ") == "Rated PG-13"
    assert format_mpaa("12", "") == "12"


def test_classify():
    def c(value, placeholder=False):
        return classify_current(value, NL, "NL:", "NL", placeholder)

    assert c("") == "blank" and c("  ") == "blank"
    assert c("NL:12") == "own" and c("12") == "own" and c("NL:AL") == "own"
    assert c("Rated PG-13") == "foreign" and c("DE:12") == "foreign"
    assert c("16+") == "foreign" and c("Not Rated") == "foreign"
    assert c("NR", placeholder=True) == "placeholder"
    assert classify_current("Rated PG-13", US, "Rated ", "US", False) == "own"
    assert classify_current("US:PG-13", US, "Rated ", "US", False) == "own"
    # A custom prefix the user chose counts as own too.
    assert classify_current("KW 12", NL, "KW ", "NL", False) == "own"


def test_normalize_title():
    assert normalize_title("  Amélie   Poulain ") == "amelie poulain"


def test_titles_match():
    assert titles_match("The Matrix", "the matrix")
    assert titles_match("Amélie", "Amelie")
    assert titles_match("Alien: Romulus", "Alien")
    assert titles_match("Alien - Director's Cut", "Alien")
    assert not titles_match("Aliens", "Alien")
    assert not titles_match("Alien", "Alien: Romulus")
    assert not titles_match("", "Alien") and not titles_match("Alien", "")


def test_years_match():
    assert years_match(1999, 2000) and years_match(2000, 1999)
    assert not years_match(1999, 2001)
    assert years_match(None, 1999) and years_match(1999, None)


def test_sanitize_prefix():
    assert sanitize_prefix("NL: ", "NL:") == "NL: "
    assert sanitize_prefix("", "NL:") == ""
    assert sanitize_prefix("a\nb", "NL:") == "NL:"
    assert sanitize_prefix("123456789", "NL:") == "NL:"

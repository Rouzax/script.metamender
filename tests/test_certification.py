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


def test_classify_reads_the_compact_spelling_as_own():
    from resources.lib.data.presets import load_preset
    au = load_preset("AU")
    assert au is not None
    assert classify_current("AU:MA15+", au.valid, "AU:", "AU", False,
                            canonical=au.canonical) == "own"
    assert classify_current("AU:MA15+", au.valid, "AU:", "AU", False) == "foreign"


def test_normalize_title():
    assert normalize_title("  Amélie   Poulain ") == "amelie poulain"


def test_titles_match():
    assert titles_match("The Matrix", "the matrix")
    assert titles_match("Amélie", "Amelie")
    assert titles_match("Alien: Romulus", "Alien")
    assert titles_match("Alien - Director's Cut", "Alien")
    assert not titles_match("Aliens", "Alien")
    assert titles_match("Avatar - Aufbruch nach Pandora", "Avatar")
    assert not titles_match("Dark Blue", "Dark")
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


def test_best_match_prefers_exact_title():
    from resources.lib.engine.certification import best_match

    cands = [(("Matrix Reloaded",), 2003, "16"), (("The Matrix", "Matrix"), 1998, "16"),
             (("The Matrix",), 1999, "12")]
    assert best_match(("The Matrix",), 1999, cands) == "16"


def test_best_match_prefix_needs_both_years():
    from resources.lib.engine.certification import best_match

    cands = [(("Alien: Romulus",), 2024, "16")]
    assert best_match(("Alien",), 2024, cands) == "16"
    assert best_match(("Alien",), 1979, cands) is None
    assert best_match(("Alien",), None, cands) is None
    assert best_match(("Alien",), 2024, [(("Alien: Romulus",), None, "16")]) is None


def test_best_match_without_year_needs_one_exact_candidate():
    from resources.lib.engine.certification import best_match

    one = [(("Dark",), 2017, "16"), (("Dark Matter",), 2015, "12")]
    assert best_match(("Dark",), None, one) == "16"
    two = [(("Dark",), 2017, "16"), (("Dark",), 2005, "12")]
    assert best_match(("Dark",), None, two) is None
    same = [(("Dark",), 2017, "16"), (("Dark",), 2017, "16")]
    assert best_match(("Dark",), None, same) == "16"


def test_best_match_year_mismatch():
    from resources.lib.engine.certification import best_match

    assert best_match(("Dune",), 2021, [(("Dune",), 1984, "12")]) is None
    assert best_match(("Dune", "Duin"), 2021, [(("Duin",), 2021, "12")]) == "12"


def test_scan_keeps_a_compact_au_rating_in_the_official_spelling():
    """A Kodi value 'AU:MA15+' is Australia's own rating: kept as real and
    written back as 'AU:MA 15+', without any lookup."""
    from types import SimpleNamespace

    from resources.lib.data.presets import load_preset
    from resources.lib.engine import scan
    from resources.lib.engine.scanctx import _CertConfig
    au = load_preset("AU")
    assert au is not None
    cfg = _CertConfig(preset=au, prefix="AU:", replace_foreign=True, not_found=None,
                      gate=None)  # type: ignore[arg-type]
    primary = SimpleNamespace(tmdb={"certifications": {"GB": "18"}})
    answer, value = scan._resolve_cert(
        cfg, {"mpaa": "AU:MA15+", "title": "Heat"}, "tmdb:949", primary,  # type: ignore[arg-type]
        SimpleNamespace(imdb=None), {}, "movie", True, False, False)  # type: ignore[arg-type]
    assert (answer.kind, answer.rating, value) == ("real", "MA 15+", "AU:MA 15+")

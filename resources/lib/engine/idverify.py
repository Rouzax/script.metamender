"""
Show-title matching for verifying `unknown`-type uniqueids.

Kodi items scraped by some tools carry an id of type `unknown`, which in
practice is a TVDB id. Before such an id is used, the TVDB series it names must
be the same show: one of the series' names (primary, translations, aliases)
has to equal the Kodi title once both are normalized.

Normalization makes formatting differences irrelevant without making
different shows equal: case, diacritics, punctuation, articles, a trailing
"(year)" and the spelling of "and" across languages are ignored; every word
that remains must match. A year is compared separately: when both sides
have one (from a trailing "(year)" or passed in) they must be within a year
of each other, so a same-titled remake is not taken for the original. The
tolerance allows for a premiere that falls on either side of New Year in
different countries.

Logging:
    None (pure functions).
"""
from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

# "and" in the languages of the library's sources. Unified rather than
# dropped, so "Tom en Jerry" equals "Tom & Jerry" but not "Tom Jerry".
_CONJUNCTIONS = frozenset({"and", "en", "und", "et", "y", "e", "og", "och"})
_AND = "and"

# Articles are dropped wherever they stand, which also covers the
# "Office, The" sort form. "a" is left alone: it is also a conjunction
# (Czech "Pat a Mat") and would otherwise make "Pat & Mat" equal "Pat Mat".
_ARTICLES = frozenset({
    "the", "an",
    "de", "het", "een",
    "der", "die", "das", "ein", "eine",
    "le", "la", "les", "l", "un", "une",
    "el", "los", "las",
    "il", "lo", "gli",
})

_TRAILING_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")
_YEAR_TOLERANCE = 1
_APOSTROPHES = re.compile(r"['’‘`]")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_title(text: str) -> str:
    """Return the comparison form of a show title."""
    text = _TRAILING_YEAR.sub("", text or "")
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in decomposed if not unicodedata.combining(c))
    text = text.replace("&", " {} ".format(_AND))
    text = _APOSTROPHES.sub("", text)
    words = []  # type: List[str]
    for word in _NON_WORD.sub(" ", text).split():
        if word in _ARTICLES:
            continue
        words.append(_AND if word in _CONJUNCTIONS else word)
    return " ".join(words)


def _title_year(text: str) -> Optional[int]:
    match = _TRAILING_YEAR.search(text or "")
    return int(match.group(1)) if match else None


def _years_agree(a: Optional[int], b: Optional[int]) -> bool:
    return a is None or b is None or abs(a - b) <= _YEAR_TOLERANCE


def show_matches(
    kodi_title: str,
    candidate_names: List[str],
    kodi_year: Optional[int] = None,
    candidate_year: Optional[int] = None,
) -> bool:
    """True when the Kodi title equals one of the candidate names after
    normalization, and the years agree where both sides have one. A year in
    the title wins over the passed year. An empty normalized title never
    matches."""
    wanted = normalize_title(kodi_title)
    if not wanted:
        return False
    own_year = _title_year(kodi_title) or kodi_year
    for name in candidate_names:
        if normalize_title(name) != wanted:
            continue
        if _years_agree(own_year, _title_year(name) or candidate_year):
            return True
    return False

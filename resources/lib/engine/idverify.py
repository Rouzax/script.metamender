"""
Show-title matching for verifying `unknown`-type uniqueids.

Kodi items scraped by some tools carry an id of type `unknown`, which in
practice is a TVDB id. Before such an id is used, the TVDB series it names must
be the same show: one of the series' names (primary, translations, aliases)
has to equal the Kodi title once both are normalized.

Normalization makes formatting differences irrelevant without making
different shows equal: case, diacritics, punctuation, articles, a trailing
"(year)" and the spelling of "and" across languages are ignored; every word
that remains must match.

Logging:
    None (pure functions).
"""
from __future__ import annotations

import re
import unicodedata
from typing import List

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

_TRAILING_YEAR = re.compile(r"\s*\(\d{4}\)\s*$")
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


def show_matches(kodi_title: str, candidate_names: List[str]) -> bool:
    """True when the Kodi title equals one of the candidate names after
    normalization. An empty normalized title never matches."""
    wanted = normalize_title(kodi_title)
    if not wanted:
        return False
    return any(normalize_title(name) == wanted for name in candidate_names)

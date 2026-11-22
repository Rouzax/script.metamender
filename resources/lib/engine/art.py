"""Rules for episode thumbs and posters: classify what Kodi holds, pick a
poster, and decide whether MetaMender may touch an art slot.

Logging:
    Logger: none (pure functions)
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Callable, List, NamedTuple, Optional, Tuple

from resources.lib.engine.chain import TRANSIENT

ART_FIELDS = frozenset({"poster", "thumb"})

EMPTY = "empty"
FRAME_GRAB = "frame_grab"
BROKEN = "broken"
WEB_TMDB = "web_tmdb"
WEB_FANART = "web_fanart"
WEB_OTHER = "web_other"
LOCAL = "local"
OTHER = "other"

GATE_LOCAL = "local"
GATE_OTHER = "other"
GATE_CHANGED = "changed"

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/original"
_IMAGE_PREFIX = "image://"
_FRAME_GRAB_PREFIX = "image://video@"
_LOCAL_PREFIXES = ("smb:", "nfs:", "special:", "/", "\\\\")

# A poster only displaces the one already set when it leads by more than this
# on the provider's own first score (TMDB: the weighted score; fanart.tv:
# likes), so near-ties do not make the artwork flip between refreshes.
POSTER_SWITCH_MARGIN = {"tmdb": 0.15, "fanart": 1}

# Pseudo-votes at the tier mean that pull a poster with few votes toward it.
TMDB_PRIOR_VOTES = 10
_DEFAULT_PRIOR_MEAN = 5.0


class Poster(NamedTuple):
    """One candidate poster. ``score`` is ``(vote_average, vote_count)`` for
    TMDB and ``(likes,)`` for fanart.tv."""
    url: str
    provider: str
    lang: Optional[str]
    score: Tuple[Any, ...]


class PosterPick(NamedTuple):
    url: str
    provider: str
    from_fallback: bool


def unwrap(value: Optional[str]) -> str:
    """The URL inside Kodi's ``image://<urlencoded>/`` wrapper.

    A frame grab (``image://video@...``) is not a URL Kodi fetched, so it is
    returned unchanged for ``classify`` to recognise.
    """
    if not value:
        return ""
    text = value.strip()
    if text.startswith(_FRAME_GRAB_PREFIX):
        return text
    if text.startswith(_IMAGE_PREFIX):
        inner = text[len(_IMAGE_PREFIX):]
        inner = inner.removesuffix("/")
        return urllib.parse.unquote(inner)
    return text


def _is_windows_path(url: str) -> bool:
    return len(url) > 2 and url[0].isalpha() and url[1] == ":" and url[2] in "\\/"


def classify(url: str) -> str:
    """What kind of art an unwrapped value is."""
    if not url:
        return EMPTY
    if url.startswith(_FRAME_GRAB_PREFIX):
        return FRAME_GRAB
    if url.startswith(_LOCAL_PREFIXES) or _is_windows_path(url):
        return LOCAL
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in ("http", "https"):
        # TheTVDB stores an empty banner link for episodes without an image.
        if parsed.path.endswith("/"):
            return BROKEN
        host = (parsed.hostname or "").lower()
        if host == "image.tmdb.org":
            return WEB_TMDB
        if host == "assets.fanart.tv":
            return WEB_FANART
        return WEB_OTHER
    return OTHER


def is_placeholder_art(value: str) -> bool:
    return classify(unwrap(value)) in (EMPTY, FRAME_GRAB, BROKEN)


def tmdb_image_url(path: str) -> str:
    return TMDB_IMAGE_BASE + path


def weighted_score(avg: float, count: int, prior_mean: float) -> float:
    return (count * avg + TMDB_PRIOR_VOTES * prior_mean) / (count + TMDB_PRIOR_VOTES)


def _prior_mean(posters: List[Poster]) -> float:
    votes = sum(p.score[1] for p in posters)
    if votes <= 0:
        return _DEFAULT_PRIOR_MEAN
    return sum(p.score[1] * p.score[0] for p in posters) / votes


def _is_rated(poster: Poster) -> bool:
    return (poster.score[1] if poster.provider == "tmdb" else poster.score[0]) > 0


def _rank(posters: List[Poster]) -> List[Tuple[Poster, float]]:
    """Posters of one provider, best first, with their primary score."""
    if posters[0].provider == "tmdb":
        prior = _prior_mean(posters)
        scored = [(p, weighted_score(p.score[0], p.score[1], prior)) for p in posters]
        scored.sort(key=lambda item: (item[1], item[0].score[1]), reverse=True)
    else:
        scored = [(p, float(p.score[0])) for p in posters]
        scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _tiers(source: str, lang: str, fallback: Optional[str]) -> List[Tuple[Optional[str], str]]:
    other = "fanart" if source == "tmdb" else "tmdb"
    langs: List[Optional[str]] = [lang]
    if fallback is not None and fallback != lang:
        langs.append(fallback)
    langs.append(None)
    return [(code, provider) for code in langs for provider in (source, other)]


def pick_poster(
    tmdb: List[Poster],
    fanart: Callable[[], Any],
    source: str,
    lang: str,
    fallback: Optional[str],
    current: str,
) -> Any:
    """Best poster, or None when there is none, or TRANSIENT when fanart.tv
    could not answer and the pick might change once it does.

    The first tier with candidates wins: library language before fallback
    language before textless, and within a language the chosen source before
    the other one. ``fanart`` is called at most once, and only when a tier or
    the unrated check needs it.
    """
    fetched: List[Any] = []

    def fanart_posters() -> Any:
        if not fetched:
            fetched.append(fanart())
        return fetched[0]

    current_url = unwrap(current)

    def pick_from(poster: Poster) -> PosterPick:
        return PosterPick(poster.url, poster.provider, poster.lang != lang)

    pick: Optional[Poster] = None
    for code, provider in _tiers(source, lang, fallback):
        if provider == "tmdb":
            pool = tmdb
        else:
            pool = fanart_posters()
            if pool is TRANSIENT:
                return TRANSIENT
        tier = [p for p in pool if p.lang == code]
        if not tier:
            continue
        ranked = _rank(tier)
        best, best_score = ranked[0]
        pick = best
        for poster, score in ranked:
            if poster.url == current_url and best_score - score <= POSTER_SWITCH_MARGIN[provider]:
                pick = poster
                break
        break

    if pick is None:
        return None

    if not _is_rated(pick) and current_url and pick.url != current_url:
        # An unrated poster says nothing against the rated one already set.
        for poster in tmdb:
            if poster.url == current_url and _is_rated(poster):
                return pick_from(poster)
        # Fanart is consulted only when it already answered or the current
        # poster could be one of its images; otherwise its failure must not
        # unsettle a pick that does not depend on it.
        if fetched or classify(current_url) == WEB_FANART:
            pool = fanart_posters()
            if pool is TRANSIENT:
                return TRANSIENT
            for poster in pool:
                if poster.url == current_url and _is_rated(poster):
                    return pick_from(poster)
    return pick_from(pick)


def art_gate(
    field: str,
    current: str,
    current_fp: str,
    first_fp: Optional[str],
    written_fp: Optional[str],
) -> Optional[str]:
    """None when MetaMender may work on this art slot, otherwise why not.

    Placeholders (empty, frame grab, broken link) are always fair game, and so
    is a value whose fingerprint is the one MetaMender last wrote. A thumb
    that is anything else came from a scraper or the user. A poster is left
    alone when it is local or from an unknown host, or when it was changed
    since MetaMender first saw it (``first_fp``).
    """
    kind = classify(unwrap(current))
    if kind in (EMPTY, FRAME_GRAB, BROKEN):
        return None
    if written_fp is not None and current_fp == written_fp:
        return None
    if kind == LOCAL:
        return GATE_LOCAL
    if field != "poster" or kind not in (WEB_TMDB, WEB_FANART):
        return GATE_OTHER
    if first_fp is None or first_fp == current_fp:
        return None
    return GATE_CHANGED

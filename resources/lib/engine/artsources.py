"""Candidates for one art field during a scan: the episode thumb from TMDB
or TheTVDB, and the poster picked from TMDB's and fanart.tv's posters.

Whether the slot may be touched at all is decided before any fetch
(art.art_gate in run_scan); this module only answers what the image should be.

Logs under the 'scan' logger; the events are listed in engine/scan.py.
"""
from __future__ import annotations

from typing import Any, Callable

from resources.lib.constants import FANART_API_KEY
from resources.lib.engine.art import pick_poster
from resources.lib.engine.chain import TRANSIENT, FieldOutcome
from resources.lib.engine.fetch import _Primary
from resources.lib.engine.ids import _Ids
from resources.lib.engine.scanctx import _call_provider, _ScanCtx
from resources.lib.engine.text import _TextSources
from resources.lib.providers import fanart
from resources.lib.providers.languages import tmdb_language

_TRANSIENT_OUTCOME = FieldOutcome(transient=True, value=None, from_fallback=False)
_ANSWERED_EMPTY = FieldOutcome(transient=False, value=None, from_fallback=False)


def image_language(kodi_lang: str) -> str:
    """The bare ISO 639-1 code TMDB and fanart.tv tag images with."""
    return tmdb_language(kodi_lang).split("-")[0]


def _fanart_answer(
    ctx: _ScanCtx, key: tuple[str, str], fetch: Callable[[], Any], identity: str,
) -> Any:
    """fanart.tv's answer through the per-scan cache, or TRANSIENT.

    A disabled fanart.tv, tripped or with its keys rejected, is not an
    answer: a poster whose pick needs it waits with its clock untouched,
    as text fields do while TheTVDB is out. A pick TMDB settles on its own
    never asks (art.pick_poster is lazy).
    """
    if key in ctx.fanart_cache:
        return ctx.fanart_cache[key]
    if ctx.breaker.is_disabled("fanart"):
        return TRANSIENT
    answer = _call_provider("fanart", fetch, ctx, identity)
    if answer is not TRANSIENT:
        ctx.fanart_cache[key] = answer
    return answer


def _fanart_posters(ctx: _ScanCtx, primary: _Primary, ids: _Ids, identity: str) -> Any:
    """The item's fanart.tv posters ([] when it has none or no id to ask
    with), or TRANSIENT."""
    if ctx.media_type.name == "movie":
        tmdb_id, imdb_id = primary.tmdb_id, primary.imdb_id
        if not (tmdb_id or imdb_id):
            return []
        answer = _fanart_answer(
            ctx, ("movie", str(tmdb_id or imdb_id)),
            lambda: fanart.fetch_movie_posters(
                tmdb_id, imdb_id, FANART_API_KEY, ctx.fanart_client_key,
            ),
            identity,
        )
        return answer if answer is TRANSIENT else (answer or [])
    if ids.tvdb_transient:
        return TRANSIENT
    tvdb_id = ids.tvdb
    if not tvdb_id:
        return []
    # The whole answer is cached: it carries the show's season posters too.
    answer = _fanart_answer(
        ctx, ("tv", str(tvdb_id)),
        lambda: fanart.fetch_show_posters(tvdb_id, FANART_API_KEY, ctx.fanart_client_key),
        identity,
    )
    if answer is TRANSIENT:
        return TRANSIENT
    return (answer or {}).get("show") or []


def _resolve_thumb(
    primary: _Primary, sources: _TextSources, folder_lang: str,
) -> tuple[FieldOutcome, str | None]:
    """The TMDB still, else TheTVDB's episode image. Neither is a fallback:
    language does not apply to a still."""
    if primary.tmdb is TRANSIENT:
        return _TRANSIENT_OUTCOME, None
    still = (primary.tmdb or {}).get("thumb") or ""
    if still:
        return FieldOutcome(transient=False, value=still, from_fallback=False), "tmdb"
    image = sources.tvdb("image", folder_lang)
    if image is TRANSIENT:
        return _TRANSIENT_OUTCOME, None
    if image:
        return FieldOutcome(transient=False, value=image, from_fallback=False), "tvdb"
    return _ANSWERED_EMPTY, None


def resolve_art(
    ctx: _ScanCtx,
    sources: _TextSources,
    primary: _Primary,
    ids: _Ids,
    identity: str,
    field: str,
    folder_lang: str,
    fallback_lang: str,
) -> tuple[FieldOutcome, str | None]:
    """The field's outcome and the provider of its value.

    A poster needs TMDB's answer, whatever the poster source: TMDB's posters
    are always a candidate tier, so without them the pick could be wrong. No
    candidate is an answer with no value, which keeps the current art.
    """
    if field == "thumb":
        return _resolve_thumb(primary, sources, folder_lang)
    if primary.tmdb is TRANSIENT:
        return _TRANSIENT_OUTCOME, None
    lang = image_language(folder_lang)
    fallback = image_language(fallback_lang) if fallback_lang.strip() else None
    pick = pick_poster(
        (primary.tmdb or {}).get("posters") or [],
        lambda: _fanart_posters(ctx, primary, ids, identity),
        ctx.poster_source, lang, fallback,
        str(sources.item.get("poster") or ""),
    )
    if pick is TRANSIENT:
        return _TRANSIENT_OUTCOME, None
    if pick is None:
        return _ANSWERED_EMPTY, None
    outcome = FieldOutcome(transient=False, value=pick.url, from_fallback=pick.from_fallback)
    return outcome, pick.provider

"""Detection of empty and placeholder text.

TMDB and TVDB return a generic "episode N" title ("Aflevering 3") instead of
an empty name when an episode has no translation in the requested language.
Such a title carries no information, so it counts as no value.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Optional, Tuple

# Word used for "episode" per ISO 639-1 language, written the way TMDB and
# TVDB emit the generic title. Every language of
# providers.languages.TMDB_PRIMARY_TRANSLATIONS needs an entry (enforced by
# tests). Verified against TMDB for nl, de, fr, es and en.
PLACEHOLDER_WORDS: Dict[str, Tuple[str, ...]] = {
    "af": ("Episode",),
    "ar": ("حلقة",),
    "be": ("Эпізод", "Серыя"),
    "bg": ("Епизод",),
    "bn": ("পর্ব", "এপিসোড"),
    "br": ("Rann", "Episod"),
    "ca": ("Episodi", "Capítol"),
    "ch": ("Episode",),
    "cs": ("Epizoda", "Díl"),
    "cy": ("Pennod",),
    "da": ("Afsnit", "Episode"),
    "de": ("Folge", "Episode"),
    "el": ("Επεισόδιο",),
    "en": ("Episode",),
    "eo": ("Epizodo",),
    "es": ("Episodio", "Capítulo"),
    "et": ("Episood", "Osa"),
    "eu": ("Atala", "Pasartea"),
    "fa": ("قسمت",),
    "fi": ("Jakso",),
    "fr": ("Épisode", "Episode"),
    "ga": ("Eipeasóid",),
    "gd": ("Prògram", "Episode"),
    "gl": ("Episodio", "Capítulo"),
    "he": ("פרק",),
    "hi": ("एपिसोड", "भाग"),
    "hr": ("Epizoda",),
    "hu": ("Epizód", "Rész"),
    "hy": ("Դրվագ", "Սերիա"),
    "id": ("Episode",),
    "it": ("Episodio", "Puntata"),
    "ja": ("エピソード", "話"),
    "ka": ("ეპიზოდი", "სერია"),
    "kk": ("Бөлім", "Эпизод"),
    "kn": ("ಸಂಚಿಕೆ", "ಎಪಿಸೋಡ್"),
    "ko": ("에피소드", "화"),
    "ku": ("Beş", "Episode"),
    "ky": ("Бөлүм", "Эпизод"),
    "lt": ("Epizodas", "Serija"),
    "lv": ("Epizode", "Sērija"),
    "ml": ("എപ്പിസോഡ്",),
    "mr": ("भाग", "एपिसोड"),
    "ms": ("Episod", "Episode"),
    "nb": ("Episode",),
    "ne": ("एपिसोड", "भाग"),
    "nl": ("Aflevering", "Episode"),
    "no": ("Episode",),
    "oc": ("Episòdi",),
    "pa": ("ਐਪੀਸੋਡ",),
    "pl": ("Odcinek",),
    "pt": ("Episódio", "Episodio", "Capítulo"),
    "ro": ("Episodul", "Episod"),
    "ru": ("Серия", "Эпизод"),
    "si": ("කථාංගය", "Episode"),
    "sk": ("Epizóda", "Diel"),
    "sl": ("Epizoda", "Del"),
    "so": ("Qaybta", "Episode"),
    "sq": ("Episodi", "Pjesa"),
    "sr": ("Епизода", "Epizoda"),
    "sv": ("Avsnitt", "Episod"),
    "sw": ("Kipindi", "Sehemu"),
    "ta": ("எபிசோடு", "அத்தியாயம்"),
    "te": ("ఎపిసోడ్",),
    "th": ("ตอนที่", "ตอน", "เอพิโซด"),
    "tl": ("Episode", "Yugto"),
    "tr": ("Bölüm",),
    "uk": ("Серія", "Епізод"),
    "ur": ("قسط", "ایپیسوڈ"),
    "uz": ("Qism", "Epizod"),
    "vi": ("Tập", "Phần"),
    "zh": ("剧集", "劇集", "集"),
    "zu": ("Isiqephu",),
}

_WORDS = sorted(
    {_w for _ws in PLACEHOLDER_WORDS.values() for _w in _ws},
    key=len,
    reverse=True,
)
_WORD_ALT = "|".join(re.escape(unicodedata.normalize("NFC", w)) for w in _WORDS)

# "Aflevering 3", "Episode #3", "Folge. 3": a word, then a number.
_WORD_THEN_NUMBER = r"(?:%s)\s*[#.:]?\s*\d+" % _WORD_ALT
# CJK counters written around the number: "第3話", "제3화".
_NUMBER_WRAPPED = r"(?:第|제)\s*\d+\s*(?:話|话|集|回|화|章)"
_PLACEHOLDER_RE = re.compile(
    r"^(?:#\s*\d+|%s|%s)$" % (_WORD_THEN_NUMBER, _NUMBER_WRAPPED),
    re.IGNORECASE,
)


def is_placeholder_title(text: Optional[str]) -> bool:
    """True when text is a generic "episode N" title in any known language."""
    if not text:
        return False
    return bool(_PLACEHOLDER_RE.match(unicodedata.normalize("NFC", text.strip())))


def is_empty_text(text: Optional[str]) -> bool:
    """True for None, blank text, a lone "-", or a placeholder title."""
    if text is None:
        return True
    stripped = text.strip()
    return stripped in ("", "-") or is_placeholder_title(stripped)

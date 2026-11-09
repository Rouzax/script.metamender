"""Kodi language code mapping to TMDB and TVDB."""
import pytest

from resources.lib.providers import languages
from resources.lib.providers.languages import (
    TMDB_PRIMARY_TRANSLATIONS,
    TVDB_UNSUPPORTED,
    tmdb_language,
    tvdb_language,
)


@pytest.mark.parametrize("code", TMDB_PRIMARY_TRANSLATIONS)
def test_tvdb_language_maps_every_tmdb_translation(code):
    assert tvdb_language(code) is not None or code in TVDB_UNSUPPORTED


@pytest.mark.parametrize("code", TMDB_PRIMARY_TRANSLATIONS)
def test_tmdb_language_is_identity_on_listed_codes(code):
    assert tmdb_language(code) == code


@pytest.mark.parametrize("code", ["nl", "NL-nl", "nl-NL", "nl_NL", " nl "])
def test_language_normalization_dutch(code):
    assert tvdb_language(code) == "nld"
    assert tmdb_language(code) == "nl-NL"


def test_portuguese_variants():
    assert tvdb_language("pt-br") == "pt"
    assert tvdb_language("pt-BR") == "pt"
    assert tvdb_language("pt-PT") == "por"
    assert tvdb_language("pt") == "por"
    assert tmdb_language("pt-br") == "pt-BR"
    assert tmdb_language("pt") == "pt-PT"


def test_chinese_variants_are_distinct():
    assert tvdb_language("zh-CN") == "zho"
    assert tvdb_language("zh-TW") == "zhtw"
    assert tvdb_language("zh") == "zho"
    assert tmdb_language("zh") == "zh-CN"
    assert tmdb_language("zh-tw") == "zh-TW"


@pytest.mark.parametrize("code,expected", [
    ("en", "en-US"), ("de", "de-DE"), ("fr", "fr-FR"), ("es", "es-ES"),
    ("ja", "ja-JP"), ("ko", "ko-KR"), ("it", "it-IT"),
])
def test_bare_language_gets_region_default(code, expected):
    assert tmdb_language(code) == expected


def test_region_default_is_a_listed_translation():
    for lang, region_code in languages._DEFAULT_REGION_CODE.items():
        assert region_code in TMDB_PRIMARY_TRANSLATIONS, lang


def test_regional_variant_uses_base_language_for_tvdb():
    assert tvdb_language("en-GB") == "eng"
    assert tvdb_language("de-AT") == "deu"


def test_unknown_language_is_none_for_tvdb():
    assert tvdb_language("xx-YY") is None
    assert tvdb_language("") is None
    assert tvdb_language("tlh") is None


def test_unlisted_region_keeps_normalized_form():
    assert tmdb_language("nl-ZZ") == "nl-ZZ"
    assert tmdb_language("xx") == "xx"
    assert tmdb_language("") == ""

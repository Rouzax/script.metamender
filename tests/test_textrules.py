"""Placeholder title detection."""
import pytest

from resources.lib.engine import textrules
from resources.lib.engine.textrules import is_empty_text, is_placeholder_title
from resources.lib.providers.languages import TMDB_PRIMARY_TRANSLATIONS

_LANGUAGES = sorted({code.split("-")[0] for code in TMDB_PRIMARY_TRANSLATIONS})


@pytest.mark.parametrize("language", _LANGUAGES)
def test_placeholder_per_language(language):
    assert language in textrules.PLACEHOLDER_WORDS, language
    words = textrules.PLACEHOLDER_WORDS[language]
    assert words
    for word in words:
        assert is_placeholder_title("%s 3" % word), word
        assert is_placeholder_title("  %s 12 " % word.upper()), word


@pytest.mark.parametrize("text", [
    "Aflevering 3", "Episode 3", "Folge 3", "Épisode 3", "Episodio 3",
    "Odcinek 3", "Jakso 3", "Серия 3", "第3話", "第 3 集", "제3화", "#7",
    "Episode #4", "episode 10", "Aflevering  5",
])
def test_known_placeholder_forms(text):
    assert is_placeholder_title(text)


@pytest.mark.parametrize("text", [
    "Sterrenkijken", "Episode of the Year", "Folge der Angst", "1984",
    "Aflevering 3: De Terugkeer", "Pilot", "", "Episode",
])
def test_real_titles_are_not_placeholders(text):
    assert not is_placeholder_title(text)


def test_none_is_not_a_placeholder_but_is_empty():
    assert not is_placeholder_title(None)
    assert is_empty_text(None)


@pytest.mark.parametrize("text", ["", "   ", "-", " - ", "Aflevering 3"])
def test_empty_text(text):
    assert is_empty_text(text)


@pytest.mark.parametrize("text", ["Pilot", "1984", "A - B"])
def test_non_empty_text(text):
    assert not is_empty_text(text)


@pytest.mark.parametrize("text", [
    "Del 2", "Osa 1", "Rész 3", "Diel 2", "Díl 2", "Phần 2", "Pjesa 2",
    "Sehemu 2", "भाग 2", "Бөлім 2", "Бөлүм 2", "Beş 2", "Qism 2",
    "Qaybta 2", "Atala 2", "Rann 2",
])
def test_part_numbers_are_real_titles(text):
    """A word meaning "part" numbers the parts of a multi-part episode, so a
    native title made of one is a value, never a placeholder."""
    assert not is_placeholder_title(text)
    assert not is_empty_text(text)


@pytest.mark.parametrize("text", [
    "Avsnitt 2", "Jakso 1", "Episode 3", "Afsnit 4", "Epizód 5", "Bölüm 6",
    "Capítulo 7", "Puntata 8",
])
def test_episode_numbers_are_placeholders(text):
    assert is_placeholder_title(text)

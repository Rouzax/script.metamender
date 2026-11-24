"""Pure artwork rules: classification, poster choice and the art gate."""
from __future__ import annotations

import urllib.parse

import pytest

from resources.lib.engine.art import (
    BROKEN, EMPTY, FRAME_GRAB, GATE_CHANGED, GATE_LOCAL, GATE_OTHER, LOCAL,
    WEB_FANART, WEB_OTHER, WEB_TMDB, Poster, art_gate, classify, pick_poster,
    unwrap, weighted_score,
)
from resources.lib.engine.chain import TRANSIENT
from resources.lib.engine.protect import is_blank


def test_unwrap_round_trip_of_written_url():
    url = "https://image.tmdb.org/t/p/original/abc.jpg"
    wrapped = "image://" + urllib.parse.quote(url, safe="") + "/"
    assert unwrap(wrapped) == url

def test_unwrap_keeps_frame_grab():
    v = "image://video@smb%3a%2f%2fHOST%2fa.mkv/"
    assert unwrap(v) == v and classify(v) == FRAME_GRAB

@pytest.mark.parametrize("url,kind", [
    ("", EMPTY), ("http://thetvdb.com/banners/", BROKEN),
    ("https://image.tmdb.org/t/p/original/x.jpg", WEB_TMDB),
    ("https://assets.fanart.tv/fanart/movies/1/movieposter/x.jpg", WEB_FANART),
    ("https://example.org/x.jpg", WEB_OTHER),
    ("smb://HOST/Data/Movies/A/poster.jpg", LOCAL), ("/storage/a/poster.jpg", LOCAL),
    ("C:\\Media\\poster.jpg", LOCAL)])
def test_classify(url, kind): assert classify(url) == kind

def test_protect_is_blank_counts_frame_grab_and_broken():
    assert is_blank("image://video@x/") and is_blank("http://thetvdb.com/banners/")
    assert not is_blank("https://image.tmdb.org/t/p/original/x.jpg")

def test_pick_poster_language_before_source():
    # source fanart, library nl: a TMDB nl poster beats a fanart en poster
    tm = [Poster("t_nl", "tmdb", "nl", (5.0, 3)), Poster("t_en", "tmdb", "en", (9.0, 9))]
    fa = lambda: [Poster("f_en", "fanart", "en", (7,))]
    assert pick_poster(tm, fa, "fanart", "nl", "en", "").url == "t_nl"

def test_pick_poster_order_chosen_source_first_within_language():
    tm = [Poster("t_nl", "tmdb", "nl", (5.0, 3))]
    fa = lambda: [Poster("f_nl", "fanart", "nl", (1,))]
    assert pick_poster(tm, fa, "fanart", "nl", None, "").url == "f_nl"

def test_pick_poster_fallback_and_textless_set_from_fallback():
    tm = [Poster("t_null", "tmdb", None, (5.0, 3))]
    p = pick_poster(tm, list, "tmdb", "nl", "en", "")
    assert p.url == "t_null" and p.from_fallback is True and p.textless is True
    p = pick_poster([Poster("t_en", "tmdb", "en", (5.0, 3))], list, "tmdb", "nl", "en", "")
    assert p.from_fallback is True and p.textless is False

def test_pick_poster_does_not_call_fanart_when_tmdb_answers_in_language():
    called = []
    pick_poster([Poster("t_nl", "tmdb", "nl", (5.0, 3))], lambda: called.append(1) or [], "tmdb", "nl", None, "")
    assert called == []

def test_pick_poster_transient_fanart_yields_transient():
    assert pick_poster([], lambda: TRANSIENT, "tmdb", "nl", None, "") is TRANSIENT

def test_pick_poster_unrated_never_replaces_rated():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18))]
    fa = lambda: [Poster("f_en", "fanart", "en", (0,))]
    assert pick_poster(tm, fa, "fanart", "en", None, "t_en").url == "t_en"

def test_pick_poster_unrated_fills_empty_and_unknown_current():
    tm = [Poster("t_en", "tmdb", "en", (0.0, 0))]
    assert pick_poster(tm, list, "tmdb", "en", None, "").url == "t_en"
    assert pick_poster(tm, list, "tmdb", "en", None, "gone").url == "t_en"

def test_pick_poster_none_when_nothing():
    assert pick_poster([], list, "tmdb", "nl", None, "") is None

def test_weighted_score_pulls_few_votes_to_the_mean():
    assert weighted_score(10.0, 1, 5.0) == pytest.approx(60 / 11)
    assert weighted_score(7.0, 0, 5.4) == pytest.approx(5.4)

def test_pick_poster_many_votes_beat_one_perfect_vote():
    tm = [Poster("one", "tmdb", "nl", (10.0, 1)), Poster("many", "tmdb", "nl", (6.4, 40)),
          Poster("low", "tmdb", "nl", (4.0, 20))]
    assert pick_poster(tm, list, "tmdb", "nl", None, "").url == "many"

def test_pick_poster_keeps_incumbent_within_margin():
    tm = [Poster("a", "tmdb", "nl", (5.6, 30)), Poster("b", "tmdb", "nl", (5.5, 30))]
    assert pick_poster(tm, list, "tmdb", "nl", None, "b").url == "b"
    tm2 = [Poster("a", "tmdb", "nl", (6.5, 30)), Poster("b", "tmdb", "nl", (5.0, 30))]
    assert pick_poster(tm2, list, "tmdb", "nl", None, "b").url == "a"

@pytest.mark.parametrize("field,current,first,written,expected", [
    ("thumb", "", None, None, None),
    ("thumb", "image://video@x/", None, None, None),
    ("thumb", "https://image.tmdb.org/t/p/original/s.jpg", None, None, GATE_OTHER),
    ("thumb", "https://image.tmdb.org/t/p/original/s.jpg", None, "FP", None),   # ours
    ("poster", "smb://H/p.jpg", None, None, GATE_LOCAL),
    ("poster", "https://image.tmdb.org/t/p/original/p.jpg", None, None, None),  # first sight
    ("poster", "https://image.tmdb.org/t/p/original/p.jpg", "OLD", None, GATE_CHANGED),
    ("poster", "https://image.tmdb.org/t/p/original/p.jpg", "FP", None, None),
    ("poster", "https://image.tmdb.org/t/p/original/p.jpg", "OLD", "FP", None),
    ("poster", "https://example.org/p.jpg", None, None, GATE_OTHER)])
def test_art_gate(field, current, first, written, expected):
    assert art_gate(field, current, "FP", first, written) == expected


def test_classify_full_tvdb_image_is_web_other():
    url = "https://artworks.thetvdb.com/banners/episodes/81189/3859781.jpg"
    assert classify(url) == WEB_OTHER


def test_unwrap_empty_and_plain():
    assert unwrap(None) == "" and unwrap("") == ""
    assert unwrap("  https://example.org/x.jpg ") == "https://example.org/x.jpg"


def test_pick_poster_unrated_pick_keeps_rated_fanart_current():
    tm = [Poster("t_en", "tmdb", "en", (0.0, 0))]
    cur = "https://assets.fanart.tv/fanart/f_en.jpg"
    fa = lambda: [Poster(cur, "fanart", "en", (4,))]
    pick = pick_poster(tm, fa, "tmdb", "en", None, cur)
    assert pick.url == cur and pick.provider == "fanart"


def test_pick_poster_fanart_margin_keeps_incumbent():
    fa = lambda: [Poster("a", "fanart", "nl", (5,)), Poster("b", "fanart", "nl", (4,))]
    assert pick_poster([], fa, "fanart", "nl", None, "b").url == "b"
    assert pick_poster([], fa, "fanart", "nl", None, "").url == "a"


def _transient_fanart(calls):
    def fanart():
        calls.append(1)
        return TRANSIENT
    return fanart


def test_pick_poster_unrated_pick_equal_to_current_ignores_fanart_failure():
    calls = []
    tm = [Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    pick = pick_poster(tm, _transient_fanart(calls), "tmdb", "nl", None, "t_nl")
    assert pick.url == "t_nl" and calls == []


def test_pick_poster_unrated_pick_replaces_gone_tmdb_current_without_fanart():
    calls = []
    tm = [Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    pick = pick_poster(tm, _transient_fanart(calls), "tmdb", "nl", None,
                       "https://image.tmdb.org/t/p/original/gone.jpg")
    assert pick.url == "t_nl" and calls == []


def test_classify_unc_path_is_local():
    assert classify("\\\\server\\share\\poster.jpg") == LOCAL


def test_pick_poster_fanart_called_once_for_tier_and_unrated_check():
    calls = []

    def fanart():
        calls.append(1)
        return [Poster("f_nl", "fanart", "nl", (0,))]
    pick = pick_poster([], fanart, "tmdb", "nl", None,
                       "https://assets.fanart.tv/fanart/x.jpg")
    assert pick.url == "f_nl" and calls == [1]


def test_pick_poster_library_language_beats_rated_fallback_current():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18)), Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    pick = pick_poster(tm, list, "tmdb", "nl", "en", "t_en")
    assert pick.url == "t_nl" and pick.from_fallback is False


def test_pick_poster_library_language_beats_rated_other_language_current():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18)), Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    assert pick_poster(tm, list, "tmdb", "nl", None, "t_en").url == "t_nl"


def test_pick_poster_fallback_language_beats_rated_textless_current():
    tm = [Poster("t_none", "tmdb", None, (7.9, 18)), Poster("t_en", "tmdb", "en", (0.0, 0))]
    assert pick_poster(tm, list, "tmdb", "nl", "en", "t_none").url == "t_en"


def test_pick_poster_unrated_fanart_does_not_replace_rated_tmdb_in_same_fallback_tier():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18))]
    fa = lambda: [Poster("f_en", "fanart", "en", (0,))]
    assert pick_poster(tm, fa, "fanart", "nl", "en", "t_en").url == "t_en"


_TMDB_W500 = "https://image.tmdb.org/t/p/w500/abc.jpg"
_TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original/abc.jpg"


def test_pick_poster_same_tmdb_image_at_another_size_is_the_incumbent():
    tm = [Poster("https://image.tmdb.org/t/p/original/top.jpg", "tmdb", "nl", (5.6, 30)),
          Poster(_TMDB_ORIGINAL, "tmdb", "nl", (5.5, 30))]
    assert pick_poster(tm, list, "tmdb", "nl", None, _TMDB_W500).url == _TMDB_W500


def test_pick_poster_unrated_pick_keeps_rated_current_at_another_size():
    tm = [Poster(_TMDB_ORIGINAL, "tmdb", "nl", (7.9, 18))]
    fa = lambda: [Poster("f_nl", "fanart", "nl", (0,))]
    assert pick_poster(tm, fa, "fanart", "nl", None, _TMDB_W500).url == _TMDB_W500

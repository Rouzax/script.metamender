"""Pure artwork rules: classification, poster choice and the art gate."""
from __future__ import annotations

import urllib.parse

import pytest

from resources.lib.data.libraries import FIELD_TOGGLES
from resources.lib.engine.art import (
    ART_FIELDS, ART_RULES, BROKEN, EMPTY, FANART_ONLY_FIELDS, FRAME_GRAB,
    GATE_CHANGED, GATE_LOCAL, GATE_OTHER, LOCAL, ORDER_TEXT_FIRST,
    ORDER_TEXTLESS_FIRST, ORDER_TEXTLESS_ONLY, OTHER_ART_FIELDS,
    TEXT_ART_FIELDS, TMDB_ART_FIELDS, WEB_FANART, WEB_OTHER, WEB_TMDB, ArtRule,
    Poster, art_gate, art_tiers, classify, drop_show_copies, name_gate,
    pick_art, season_tie_break, unwrap, upload_time, weighted_score,
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
    assert pick_art("poster", tm, fa, "fanart", "nl", "en", "").url == "t_nl"

def test_pick_poster_order_chosen_source_first_within_language():
    tm = [Poster("t_nl", "tmdb", "nl", (5.0, 3))]
    fa = lambda: [Poster("f_nl", "fanart", "nl", (1,))]
    assert pick_art("poster", tm, fa, "fanart", "nl", None, "").url == "f_nl"

def test_pick_poster_fallback_and_textless_set_from_fallback():
    tm = [Poster("t_null", "tmdb", None, (5.0, 3))]
    p = pick_art("poster", tm, list, "tmdb", "nl", "en", "")
    assert p.url == "t_null" and p.from_fallback is True and p.textless is True
    p = pick_art("poster", [Poster("t_en", "tmdb", "en", (5.0, 3))], list, "tmdb", "nl", "en", "")
    assert p.from_fallback is True and p.textless is False

def test_pick_poster_does_not_call_fanart_when_tmdb_answers_in_language():
    called = []
    pick_art("poster", [Poster("t_nl", "tmdb", "nl", (5.0, 3))], lambda: called.append(1) or [], "tmdb", "nl", None, "")
    assert called == []

def test_pick_poster_transient_fanart_yields_transient():
    assert pick_art("poster", [], lambda: TRANSIENT, "tmdb", "nl", None, "") is TRANSIENT

def test_pick_poster_unrated_never_replaces_rated():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18))]
    fa = lambda: [Poster("f_en", "fanart", "en", (0,))]
    assert pick_art("poster", tm, fa, "fanart", "en", None, "t_en").url == "t_en"

def test_pick_poster_unrated_fills_empty_and_unknown_current():
    tm = [Poster("t_en", "tmdb", "en", (0.0, 0))]
    assert pick_art("poster", tm, list, "tmdb", "en", None, "").url == "t_en"
    assert pick_art("poster", tm, list, "tmdb", "en", None, "gone").url == "t_en"

def test_pick_poster_none_when_nothing():
    assert pick_art("poster", [], list, "tmdb", "nl", None, "") is None

def test_weighted_score_pulls_few_votes_to_the_mean():
    assert weighted_score(10.0, 1, 5.0) == pytest.approx(60 / 11)
    assert weighted_score(7.0, 0, 5.4) == pytest.approx(5.4)

def test_pick_poster_many_votes_beat_one_perfect_vote():
    tm = [Poster("one", "tmdb", "nl", (10.0, 1)), Poster("many", "tmdb", "nl", (6.4, 40)),
          Poster("low", "tmdb", "nl", (4.0, 20))]
    assert pick_art("poster", tm, list, "tmdb", "nl", None, "").url == "many"

def test_pick_poster_keeps_incumbent_within_margin():
    tm = [Poster("a", "tmdb", "nl", (5.6, 30)), Poster("b", "tmdb", "nl", (5.5, 30))]
    assert pick_art("poster", tm, list, "tmdb", "nl", None, "b").url == "b"
    tm2 = [Poster("a", "tmdb", "nl", (6.5, 30)), Poster("b", "tmdb", "nl", (5.0, 30))]
    assert pick_art("poster", tm2, list, "tmdb", "nl", None, "b").url == "a"

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
    pick = pick_art("poster", tm, fa, "tmdb", "en", None, cur)
    assert pick.url == cur and pick.provider == "fanart"


def test_pick_poster_fanart_margin_keeps_incumbent():
    fa = lambda: [Poster("a", "fanart", "nl", (5,)), Poster("b", "fanart", "nl", (4,))]
    assert pick_art("poster", [], fa, "fanart", "nl", None, "b").url == "b"
    assert pick_art("poster", [], fa, "fanart", "nl", None, "").url == "a"


def _transient_fanart(calls):
    def fanart():
        calls.append(1)
        return TRANSIENT
    return fanart


def test_pick_poster_unrated_pick_equal_to_current_ignores_fanart_failure():
    calls = []
    tm = [Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    pick = pick_art("poster", tm, _transient_fanart(calls), "tmdb", "nl", None, "t_nl")
    assert pick.url == "t_nl" and calls == []


def test_pick_poster_unrated_pick_replaces_gone_tmdb_current_without_fanart():
    calls = []
    tm = [Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    pick = pick_art("poster", tm, _transient_fanart(calls), "tmdb", "nl", None,
                       "https://image.tmdb.org/t/p/original/gone.jpg")
    assert pick.url == "t_nl" and calls == []


def test_classify_unc_path_is_local():
    assert classify("\\\\server\\share\\poster.jpg") == LOCAL


def test_pick_poster_fanart_called_once_for_tier_and_unrated_check():
    calls = []

    def fanart():
        calls.append(1)
        return [Poster("f_nl", "fanart", "nl", (0,))]
    pick = pick_art("poster", [], fanart, "tmdb", "nl", None,
                       "https://assets.fanart.tv/fanart/x.jpg")
    assert pick.url == "f_nl" and calls == [1]


def test_pick_poster_library_language_beats_rated_fallback_current():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18)), Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    pick = pick_art("poster", tm, list, "tmdb", "nl", "en", "t_en")
    assert pick.url == "t_nl" and pick.from_fallback is False


def test_pick_poster_library_language_beats_rated_other_language_current():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18)), Poster("t_nl", "tmdb", "nl", (0.0, 0))]
    assert pick_art("poster", tm, list, "tmdb", "nl", None, "t_en").url == "t_nl"


def test_pick_poster_fallback_language_beats_rated_textless_current():
    tm = [Poster("t_none", "tmdb", None, (7.9, 18)), Poster("t_en", "tmdb", "en", (0.0, 0))]
    assert pick_art("poster", tm, list, "tmdb", "nl", "en", "t_none").url == "t_en"


def test_pick_poster_unrated_fanart_does_not_replace_rated_tmdb_in_same_fallback_tier():
    tm = [Poster("t_en", "tmdb", "en", (7.9, 18))]
    fa = lambda: [Poster("f_en", "fanart", "en", (0,))]
    assert pick_art("poster", tm, fa, "fanart", "nl", "en", "t_en").url == "t_en"


_TMDB_W500 = "https://image.tmdb.org/t/p/w500/abc.jpg"
_TMDB_ORIGINAL = "https://image.tmdb.org/t/p/original/abc.jpg"


def test_pick_poster_same_tmdb_image_at_another_size_is_the_incumbent():
    tm = [Poster("https://image.tmdb.org/t/p/original/top.jpg", "tmdb", "nl", (5.6, 30)),
          Poster(_TMDB_ORIGINAL, "tmdb", "nl", (5.5, 30))]
    assert pick_art("poster", tm, list, "tmdb", "nl", None, _TMDB_W500).url == _TMDB_W500


def test_pick_poster_unrated_pick_keeps_rated_current_at_another_size():
    tm = [Poster(_TMDB_ORIGINAL, "tmdb", "nl", (7.9, 18))]
    fa = lambda: [Poster("f_nl", "fanart", "nl", (0,))]
    assert pick_art("poster", tm, fa, "fanart", "nl", None, _TMDB_W500).url == _TMDB_W500


def _t(url, lang, avg=7.0, votes=20):
    return Poster(url, "tmdb", lang, (avg, votes))


def _f(url, lang, likes=3, sd=False):
    return Poster(url, "fanart", lang, (likes,), sd)


def test_art_field_sets_match_the_ticks():
    assert FIELD_TOGGLES["other_art"] == OTHER_ART_FIELDS and FIELD_TOGGLES["fanart"] == {"fanart"}
    assert ART_FIELDS == {"poster", "thumb", "fanart"} | OTHER_ART_FIELDS
    assert TMDB_ART_FIELDS | FANART_ONLY_FIELDS == ART_FIELDS - {"thumb"}
    assert TEXT_ART_FIELDS == ART_FIELDS - {"thumb", "keyart"}
    assert set(ART_RULES) == ART_FIELDS - {"thumb"}


def test_art_tiers_per_order():
    both = ArtRule(ORDER_TEXT_FIRST, True)
    assert art_tiers(both, "fanart", "nl", "de", True) == [
        ("nl", "fanart"), ("nl", "tmdb"), ("de", "fanart"), ("de", "tmdb"),
        (None, "fanart"), (None, "tmdb"), ("en", "fanart"), ("en", "tmdb")]
    assert art_tiers(ArtRule(ORDER_TEXTLESS_FIRST, True), "tmdb", "nl", "nl", False) == [
        (None, "tmdb"), (None, "fanart"), ("nl", "tmdb"), ("nl", "fanart")]
    assert art_tiers(ArtRule(ORDER_TEXTLESS_ONLY, True), "tmdb", "nl", "de", True) == [
        (None, "tmdb"), (None, "fanart")]
    # English is already a language step: no extra tier.
    assert art_tiers(ArtRule(ORDER_TEXT_FIRST, False), "tmdb", "nl", "en", True) == [
        ("nl", "fanart"), ("en", "fanart"), (None, "fanart")]
    assert art_tiers(ArtRule(ORDER_TEXT_FIRST, False), "tmdb", "en", None, True) == [
        ("en", "fanart"), (None, "fanart")]


def test_fanart_prefers_textless_over_library_language():
    tm = [_t("nl_bd", "nl", 9.0, 50), _t("none_bd", None, 5.0, 5)]
    p = pick_art("fanart", tm, list, "tmdb", "nl", None, "")
    assert p.url == "none_bd" and p.from_fallback is False and p.textless is True


def test_fanart_with_text_counts_as_fallback():
    p = pick_art("fanart", [_t("nl_bd", "nl")], list, "tmdb", "nl", None, "")
    assert p.url == "nl_bd" and p.from_fallback is True and p.english is False


def test_english_tier_only_when_allowed():
    tm = [_t("en_logo", "en", 7.0, 9)]
    assert pick_art("clearlogo", tm, list, "tmdb", "nl", None, "") is None
    p = pick_art("clearlogo", tm, list, "tmdb", "nl", None, "", english=True)
    assert p.url == "en_logo" and p.english and p.from_fallback


def test_english_as_fallback_language_is_not_the_last_resort():
    p = pick_art("poster", [_t("en", "en")], list, "tmdb", "nl", "en", "", english=True)
    assert p.url == "en" and p.english is False and p.from_fallback is True


def test_english_with_a_fallback_comes_after_textless():
    tm = [_t("en", "en", 9.0, 50), _t("txtless", None, 5.0, 2)]
    assert pick_art("poster", tm, list, "tmdb", "nl", "de", "", english=True).url == "txtless"


def test_hd_before_sd_in_one_tier():
    fa = lambda: [_f("sd", "nl", 30, sd=True), _f("hd", "nl", 1)]
    assert pick_art("clearlogo", [], fa, "fanart", "nl", None, "").url == "hd"


def test_sd_used_when_the_tier_has_no_hd():
    fa = lambda: [_f("sd", "nl", 30, sd=True), _f("hd_de", "de", 1)]
    assert pick_art("clearart", [], fa, "tmdb", "nl", "de", "").url == "sd"


def test_simple_type_uses_fanart_only_and_fallback():
    fa = lambda: [_f("de", "de", 2), _f("none", None, 9)]
    assert pick_art("banner", [_t("tm", "nl")], fa, "tmdb", "nl", "de", "").url == "de"


def test_simple_type_unrated_check_ignores_tmdb():
    # A rated TMDB image equal to the current value is no candidate for a
    # fanart.tv-only type, so it cannot hold the slot.
    fa = lambda: [_f("f_nl", "nl", 0)]
    p = pick_art("landscape", [_t("t_nl", "nl", 8.0, 30)], fa, "tmdb", "nl", None, "t_nl")
    assert p.url == "f_nl"


def test_keyart_is_textless_only():
    tm = [_t("nl", "nl"), _t("none", None, 6.0, 3)]
    p = pick_art("keyart", tm, list, "tmdb", "nl", "en", "")
    assert p.url == "none" and p.from_fallback is False
    assert pick_art("keyart", [_t("nl", "nl")], list, "tmdb", "nl", None, "",
                    english=True) is None


def test_upload_time_from_uniqid():
    assert upload_time("https://assets.fanart.tv/fanart/tv/1/seasonposter/x-5229a9b2a4a1e.jpg") == 0x5229a9b2
    assert upload_time("https://assets.fanart.tv/fanart/tv/1/seasonposter/plain.jpg") is None
    assert upload_time("https://assets.fanart.tv/fanart/tv/1/seasonposter/x-5229a9b2a4a1.jpg") is None
    assert upload_time("https://assets.fanart.tv/fanart/tv/1/seasonposter/x-5229a9b2a4a1z.jpg") is None
    assert upload_time("https://assets.fanart.tv/fanart/tv/1/seasonposter/x-5229a9b2_4a1e.jpg") is None


def test_tie_break_only_breaks_ties():
    batch = [[_f("o-5229a9b2a4a1e", "nl", 1)]]                    # another season, same day
    near, far = _f("a-5229a9c0a4a1e", "nl", 4), _f("b-5329a9b2a4a1e", "nl", 4)
    assert pick_art("poster", [], lambda: [far, near], "fanart", "nl", None, "",
                    tie_break=season_tie_break(batch)).url.startswith("a-")
    more_likes = _f("c-6329a9b2a4a1e", "nl", 5)
    assert pick_art("poster", [], lambda: [near, more_likes], "fanart", "nl", None, "",
                    tie_break=season_tie_break(batch)).url.startswith("c-")


def test_tie_break_counts_seasons_in_the_batch():
    others = [[_f("o1-5229a9b2a4a1e", "nl")], [_f("o2-5229a9b2a4a1e", "nl")],
              [_f("o3-6229a9b2a4a1e", "nl")]]
    tie = season_tie_break(others)
    assert tie(_f("a-5229a9b3a4a1e", "nl")) == 2
    assert tie(_f("b-6229a9b3a4a1e", "nl")) == 1
    assert tie(_f("plain", "nl")) == 0
    two, one = _f("two-5229a9b3a4a1e", "nl", 4), _f("one-6229a9b3a4a1e", "nl", 4)
    assert pick_art("poster", [], lambda: [one, two], "fanart", "nl", None, "",
                    tie_break=tie).url.startswith("two-")


def test_tie_break_keeps_current_within_margin():
    batch = [[_f("o-5229a9b2a4a1e", "nl", 1)]]
    a, b = _f("a-5229a9c0a4a1e", "nl", 4), _f("b-7329a9b2a4a1e", "nl", 4)
    assert pick_art("poster", [], lambda: [a, b], "fanart", "nl", None, b.url,
                    tie_break=season_tie_break(batch)).url == b.url


def test_drop_show_copies_by_tmdb_file():
    s = [_t("https://image.tmdb.org/t/p/original/same.jpg", "nl"), _t(".../own.jpg", "nl")]
    show = [_t("https://image.tmdb.org/t/p/w500/same.jpg", "en")]
    assert [p.url for p in drop_show_copies(s, show)] == [s[1].url]


@pytest.mark.parametrize("cur,first,written,expected", [
    ("A", None, None, None), ("A", "A", None, None), ("B", "A", "B", None), ("C", "A", "B", GATE_CHANGED)])
def test_name_gate(cur, first, written, expected):
    assert name_gate(cur, first, written) == expected


def test_art_gate_missing_local_is_fair_game():
    assert art_gate("fanart", "smb://h/a/fanart.jpg", "fp", "fp", None, missing=True) is None
    assert art_gate("fanart", "smb://h/a/fanart.jpg", "fp", "fp", None) == GATE_LOCAL


@pytest.mark.parametrize("field", sorted(ART_FIELDS - {"thumb"}))
def test_art_gate_web_art_follows_the_poster_rule(field):
    url = "https://assets.fanart.tv/fanart/movies/1/moviebanner/x.jpg"
    assert art_gate(field, url, "FP", None, None) is None
    assert art_gate(field, url, "FP", "OLD", None) == GATE_CHANGED
    assert art_gate(field, "https://example.org/x.jpg", "FP", None, None) == GATE_OTHER

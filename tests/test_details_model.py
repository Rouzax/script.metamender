"""Tests for ui/details_model.py: what the details window says about an item."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from resources.lib.data.media_types import EPISODE, MOVIE
from resources.lib.engine.protect import fingerprint
from resources.lib.ui import details_model
from resources.lib.ui.details_model import build_rows, header_line

_ROOT = Path(__file__).resolve().parent.parent
_PO = (_ROOT / "resources/language/resource.language.en_gb/strings.po").read_text()
_STRINGS = {
    int(k): v.replace('\\"', '"')
    for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', _PO)
}

TODAY = "2026-11-12"
PLOT = "Agenten Mardik en Shannon worden opgeroepen voor een melding op Amsterdam Centraal."


@pytest.fixture(autouse=True)
def strings(monkeypatch):
    monkeypatch.setattr(details_model, "lang", lambda i: _STRINGS.get(i, ""))


def _fs(reason, *, source=None, count=1, next_retry=None, fetched="2026-11-12",
        written=None, fallback=0):
    return {
        "source": source, "fetch_count": count, "next_retry": next_retry,
        "fetched_at": fetched, "from_fallback": fallback, "written_value": written,
        "settings_fp": "fp", "last_reason": reason,
    }


def _episode():
    """The mockup's example: a new TVmaze episode matched by number."""
    item = {
        "title": "Amsterdam Centraal", "showtitle": "Het gouden uur",
        "season": 1, "episode": 1, "firstaired": "2026-11-01",
        "plot": PLOT, "rating": 7.4, "votes": 1234, "runtime": 2700,
        "director": [], "writer": ["Ilse Warringa"],
        "uniqueid": {"tvmaze": "2741234"},
    }
    item_row = {
        "tmdb_id": "4104123", "tvdb_id": "9297341", "imdb_id": "tt23020114",
        "tmdb_season": 1, "number_matched": 1, "orig_title": "Amsterdam Centraal",
        "orig_aired": "2026-11-01", "match_note": "", "ids_checked": TODAY,
        "last_seen": TODAY,
    }
    fs_map = {
        "plot": _fs("updated", source="tmdb", next_retry="2026-11-19",
                    written=fingerprint(PLOT)),
        "rating": _fs("updated", source="imdb", next_retry="2026-11-19",
                      written=json.dumps({"imdb": "x"})),
        "votes": _fs("updated", source="imdb", next_retry="2026-11-19",
                     written=json.dumps({"imdb": "x"})),
        "runtime": _fs("updated", source="tmdb", next_retry="2026-11-19",
                       written=fingerprint(2700)),
        "title": _fs("fill_only", next_retry="2026-11-19"),
        "firstaired": _fs("unchanged", source="tmdb", count=3),
        "director": _fs("nothing", next_retry="2026-11-15"),
    }
    keep = {
        "plot": {"original": "An English plot", "has_original": True,
                 "locked": False, "written_fp": fingerprint(PLOT)},
    }
    return item, item_row, fs_map, keep


def _rows(**kw):
    item, item_row, fs_map, keep = _episode()
    item.update(kw.pop("item", {}))
    item_row.update(kw.pop("item_row", {}))
    fs_map.update(kw.pop("fs_map", {}))
    keep.update(kw.pop("keep", {}))
    return build_rows(EPISODE, item, item_row, fs_map, keep, TODAY, language="Dutch", **kw)


def _by_key(rows):
    return {r.key: r for r in rows}


def test_rows_grouped_like_the_mockup():
    rows = _rows()
    assert rows[0].key == "ids" and rows[0].group == ""
    assert [(r.key, r.group) for r in rows[1:]] == [
        ("plot", "updated"), ("rating", "updated"), ("runtime", "updated"),
        ("firstaired", "left"), ("title", "left"),
        ("director", "waiting"), ("writer", "waiting"),
    ]
    hints = {r.key: r.hint for r in rows}
    assert hints["ids"] == "matched by number"
    assert hints["plot"] == "TMDB"
    assert hints["rating"] == "IMDb"
    assert hints["title"] == "scraper"
    assert hints["firstaired"] == "settled"
    assert hints["director"] == "15 Nov"
    assert hints["writer"] == "not checked"
    labels = {r.key: r.label for r in rows}
    assert labels["firstaired"] == "Aired" and labels["ids"] == "Ids"


def test_plot_story_lines():
    plot = _by_key(_rows())["plot"]
    assert plot.title == "Plot"
    assert plot.lines == [
        ("", PLOT),
        ("From", "TMDB, in Dutch"),
        ("Replaced", "your scraper's value"),
        ("Last checked", "12 Nov 2026"),
        ("Next check", "19 Nov 2026"),
    ]


def test_fallback_and_empty_original_and_unknown_original():
    rows = _by_key(_rows(
        fs_map={"plot": _fs("updated", source="tvdb", next_retry="2026-11-19",
                            written=fingerprint(PLOT), fallback=1)},
        keep={"plot": {"original": "", "has_original": True, "locked": False,
                       "written_fp": fingerprint(PLOT)}},
    ))
    lines = dict(rows["plot"].lines)
    assert lines["From"] == "TheTVDB, in the fallback language"
    assert lines["Replaced"] == "empty"
    runtime = dict(rows["runtime"].lines)
    assert runtime["Replaced"] == "updated before MetaMender kept the old value"
    assert runtime["From"] == "TMDB"
    assert runtime[""] == "45 min"


def test_nfo_field_says_it_is_only_filled_when_empty():
    """may_write fills an empty NFO field and updates MetaMender's own
    earlier value, so the text must not say "never"."""
    rows = _by_key(_rows(fs_map={"title": _fs("nfo", next_retry="2026-11-19")}))
    assert dict(rows["title"].lines)["Why"] == (
        "Your NFO file sets this field, so MetaMender only fills it when it is empty.")


def test_left_and_waiting_fields_say_why():
    rows = _by_key(_rows())
    title = dict(rows["title"].lines)
    # The kept value may be the user's own edit, so it is not called the
    # scraper's.
    assert title["Why"] == ("This folder has no language set, so MetaMender only "
                            "fills empty fields and keeps this value.")
    assert dict(rows["firstaired"].lines)["Next check"] == "settled, not checked again"
    director = dict(rows["director"].lines)
    assert director[""] == "Empty"
    assert "nothing" in director["Why"]
    assert director["Next check"] == "15 Nov 2026"


def test_settled_follows_the_maturity_model():
    """Content past the stability age is final whatever next_retry holds."""
    rows = _by_key(_rows(
        item={"firstaired": "2020-01-01"},
        fs_map={"title": _fs("unchanged", source="tmdb", next_retry="2026-12-01")},
    ))
    assert rows["title"].group == "left"
    assert rows["title"].hint == "settled"
    assert dict(rows["title"].lines)["Next check"] == "settled, not checked again"
    # a young field due now waits for the next scan
    young = _by_key(_rows(fs_map={"director": _fs("nothing", next_retry="2026-11-01")}))
    assert young["director"].hint == "next scan"


def test_locked_field_offers_unlock_and_restorable_offers_restore():
    rows = _by_key(_rows(
        fs_map={"title": _fs("locked", count=0, fetched=None)},
        keep={"title": {"original": "Amsterdam Centraal", "has_original": True,
                        "locked": True, "written_fp": fingerprint("Het gouden uur")}},
    ))
    assert rows["title"].group == "left"
    assert rows["title"].action == "unlock"
    assert rows["title"].hint == "locked"
    assert "locked" in dict(rows["title"].lines)["Why"]
    assert rows["plot"].action == "restore"
    assert rows["runtime"].action == "keep"  # no original kept
    assert rows["ids"].action == ""


def test_ratings_never_offer_restore():
    rows = _rows(
        keep={"rating": {"original": 6.0, "has_original": True, "locked": True,
                         "written_fp": None}},
    )
    rating = _by_key(rows)["rating"]
    assert rating.action == ""
    assert rating.group == "updated"
    assert "votes" not in _by_key(rows)
    assert dict(rating.lines)[""] == "7.4 (1234 votes)"
    assert dict(rating.lines)["From"] == "IMDb"


def test_ids_lines_for_a_number_match():
    ids = _rows()[0]
    assert ids.title == "Ids"
    assert ids.lines == [
        ("TMDB", "4104123, matched by season and episode number"),
        ("TheTVDB", "9297341, matched by season and episode number"),
        ("IMDb", "tt23020114, from TMDB's id list"),
        ("", "Kodi has only a TVmaze id."),
        ("", "Ids MetaMender found stay in its own records."),
    ]


def test_ids_from_kodi_and_found_otherwise():
    item = {"title": "Heat", "premiered": "1995-12-15", "uniqueid": {"tmdb": "949"}}
    row = {"tmdb_id": "949", "imdb_id": "tt0113277", "tvdb_id": None, "number_matched": 0,
           "match_note": "", "last_seen": TODAY}
    ids = build_rows(MOVIE, item, row, {}, {}, TODAY)[0]
    assert ids.hint == "from Kodi"
    assert ids.lines[:2] == [("TMDB", "949, from Kodi"), ("IMDb", "tt0113277, from TMDB's id list")]


def test_rejected_match_lines():
    note = [
        {"site": "tmdb", "site_title": "Traveling Salesmen", "site_aired": "2007-01-11",
         "kodi_title": "Back from Vacation", "kodi_aired": "2007-01-04"},
        {"site": "conflict", "tmdb_id": "1", "tmdb_tvdb_id": "2", "tvdb_id": "3"},
    ]
    ids = _rows(item_row={"tmdb_id": None, "tvdb_id": None, "imdb_id": None,
                          "number_matched": 0, "match_note": json.dumps(note)})[0]
    assert ids.hint == "not matched"
    assert ("TMDB", '"Traveling Salesmen", aired 11 Jan 2007') in ids.lines
    assert ("Yours", '"Back from Vacation", aired 4 Jan 2007') in ids.lines
    why = [text for label, text in ids.lines if label == "Why"]
    assert why == ["The title and date did not agree, so nothing from TMDB was used."]
    assert any("different episodes" in text for _, text in ids.lines)


def test_rejected_match_hidden_once_the_episode_has_its_own_id():
    note = [{"site": "tvdb", "site_title": "A", "site_aired": "2007-01-11",
             "kodi_title": "B", "kodi_aired": "2007-01-04"}]
    ids = _rows(item={"uniqueid": {"tvdb": "555"}},
                item_row={"tmdb_id": None, "tvdb_id": "555", "imdb_id": None,
                          "number_matched": 0, "match_note": json.dumps(note)})[0]
    assert ("TheTVDB", "555, from Kodi") in ids.lines
    assert not any(label in ("Why", "Yours") for label, _ in ids.lines)


def test_never_checked_item():
    item = {"title": "New film", "premiered": "2026-11-01", "plot": "",
            "uniqueid": {"imdb": "tt9999999"}}
    rows = build_rows(MOVIE, item, None, {}, {}, TODAY)
    assert rows[0].key == "ids"
    assert rows[0].lines == [("IMDb", "tt9999999, from Kodi")]
    fields = rows[1:]
    assert fields and all(r.group == "waiting" for r in fields)
    assert all(r.hint == "not checked" for r in fields)
    assert all(
        dict(r.lines)["Why"] == "Not checked by MetaMender yet." for r in fields
    )
    assert all(r.action != "restore" for r in rows)
    assert "votes" not in {r.key for r in rows}
    assert header_line(None, {}, "Dutch") == "Dutch folder · not checked by MetaMender yet"


def test_fields_limited_to_those_the_scan_manages():
    item, item_row, fs_map, keep = _episode()
    rows = build_rows(EPISODE, item, item_row, fs_map, keep, TODAY,
                      fields=["plot", "rating", "votes"])
    assert [r.key for r in rows] == ["ids", "plot", "rating"]


def test_header_line():
    _, item_row, fs_map, _ = _episode()
    assert header_line(item_row, fs_map, "Dutch") == "Dutch folder · last looked at 12 Nov 2026"
    assert header_line({"last_seen": None}, fs_map, "") == (
        "No folder language · last looked at 12 Nov 2026"
    )


def test_unlocked_field_is_due_at_the_next_scan():
    """After an unlock the last reason stays "locked" until the next scan,
    which checks the field again whatever its clock says."""
    unlocked = {"original": "Amsterdam Centraal", "has_original": True,
                "locked": False, "written_fp": None}
    for fs in (_fs("locked", count=2, next_retry="2026-11-19"), _fs("locked", count=2)):
        title = _by_key(_rows(fs_map={"title": fs}, keep={"title": unlocked}))["title"]
        assert title.group == "waiting"
        assert title.hint == "next scan"
        assert title.action != "restore"
        lines = dict(title.lines)
        assert lines["Next check"] == "at the next scan"
        assert lines["Why"] == (
            "You unlocked this field, so MetaMender checks it again at the next scan."
        )


def test_unlocked_wins_over_changed_after_a_restore():
    """Restore put the original back (not MetaMender's value), then Unlock:
    the row tells the unlock, not "changed since MetaMender's update"."""
    entry = {"original": "Old plot", "has_original": True, "locked": False,
             "written_fp": fingerprint(PLOT)}
    plot = _by_key(_rows(
        item={"plot": "Old plot"},
        fs_map={"plot": _fs("locked", source="tmdb", count=3, written=fingerprint(PLOT))},
        keep={"plot": entry},
    ))["plot"]
    assert plot.group == "waiting" and plot.hint == "next scan"
    lines = dict(plot.lines)
    assert lines["Next check"] == "at the next scan"
    assert "unlocked" in lines["Why"] and "Changed since" not in lines["Why"]


def test_partial_number_match_names_the_rejected_site():
    note = [{"site": "tmdb", "site_title": "Traveling Salesmen", "site_aired": "2007-01-11",
             "kodi_title": "Back from Vacation", "kodi_aired": "2007-01-04"}]
    ids = _rows(item_row={"tmdb_id": None, "tvdb_id": "9297341", "imdb_id": None,
                          "number_matched": 1, "match_note": json.dumps(note)})[0]
    assert ("TheTVDB", "9297341, matched by season and episode number") in ids.lines
    why = [text for label, text in ids.lines if label == "Why"]
    assert why == ["The title and date did not agree, so nothing from TMDB was used."]


def test_found_ids_line_does_not_promise_kodi_is_unchanged():
    ids = _rows()[0]
    assert ("", "Ids MetaMender found stay in its own records.") in ids.lines
    assert not any("not changed" in text for _, text in ids.lines)


def test_value_changed_since_metamenders_update_is_left_as_it_is():
    rows = _by_key(_rows(item={"plot": "Edited by hand"}))
    plot = rows["plot"]
    assert plot.group == "left"
    assert plot.hint == "changed"
    lines = dict(plot.lines)
    assert lines["Why"] == "Changed since MetaMender's update (your edit or a re-scrape)."
    assert "From" not in lines and "Replaced" not in lines
    assert plot.action != "restore"


def test_ids_say_how_a_number_match_found_them():
    note = [{"site": "tmdb", "matched": "date"}, {"site": "tvdb", "matched": "tmdb_list"}]
    ids = _rows(item_row={"match_note": json.dumps(note)})[0]
    assert ("TMDB", "4104123, matched by season and episode number (the dates agreed)") \
        in ids.lines
    assert ("TheTVDB", "9297341, from TMDB's id list") in ids.lines
    # A pass is not a rejection.
    assert not any(label in ("Why", "Yours") for label, _ in ids.lines)
    titles = _rows(item_row={"match_note": json.dumps(
        [{"site": "tvdb", "matched": "title"}])})[0]
    assert ("TheTVDB", "9297341, matched by season and episode number (the titles agreed)") \
        in titles.lines


def test_tvmaze_note_only_when_kodi_has_nothing_else():
    ids = _rows(item={"uniqueid": {"tvmaze": "1", "tvdb": "9297341"}})[0]
    assert ("", "Kodi has only a TVmaze id.") not in ids.lines
    ids = _rows(item={"uniqueid": {"tvmaze": "1", "imdb": ""}})[0]
    assert ("", "Kodi has only a TVmaze id.") in ids.lines


def test_settled_nothing_does_not_say_yet():
    director = _by_key(_rows(
        item={"firstaired": "2020-01-01"},
        fs_map={"director": _fs("nothing", next_retry="2026-12-01")},
    ))["director"]
    assert director.hint == "settled"
    assert dict(director.lines)["Why"] == (
        "The sites MetaMender asks had nothing for this field.")


def test_blank_written_by_an_unfilled_refill_is_not_metamenders():
    """A refill that found nothing records the blank as written, so it does
    not trigger again; the blank is still no value of MetaMender's."""
    blank = fingerprint("")
    rows = _by_key(_rows(
        item={"plot": ""},
        fs_map={"plot": _fs("nothing", source="tmdb", next_retry="2026-11-19",
                            written=blank)},
        keep={"plot": {"original": "Old", "has_original": True, "locked": False,
                       "written_fp": blank}},
    ))
    plot = rows["plot"]
    assert plot.group != "updated"
    assert plot.action != "restore"
    assert "From" not in dict(plot.lines)


def test_blanked_final_field_is_filled_again_at_the_next_scan():
    """Kodi lost a settled field's MetaMender value: the scan refills it."""
    plot = _by_key(_rows(
        item={"plot": "", "firstaired": "2020-01-01"},
        fs_map={"plot": _fs("updated", source="tmdb", written=fingerprint(PLOT))},
    ))["plot"]
    assert plot.group == "waiting" and plot.hint == "next scan"
    lines = dict(plot.lines)
    assert lines["Next check"] == "at the next scan"
    assert lines["Why"] == ("Kodi lost MetaMender's value (a refresh or a re-scrape), "
                            "so the next scan fills it again.")
    assert plot.action != "restore"


def test_unmanaged_field_promises_no_next_scan():
    """A field shown only for its lock (its group is switched off) is not
    checked at the next scan."""
    unlocked = {"original": "Amsterdam Centraal", "has_original": True,
                "locked": False, "written_fp": None}
    item, item_row, fs_map, keep = _episode()
    fs_map["title"] = _fs("locked", count=2, next_retry="2026-11-19")
    keep["title"] = unlocked
    rows = _by_key(build_rows(EPISODE, item, item_row, fs_map, keep, TODAY,
                              fields=["plot", "title"], managed=["plot"]))
    title = rows["title"]
    assert title.hint == "switched off"
    lines = dict(title.lines)
    assert lines["Next check"] == "not while this field is switched off in the settings"
    assert lines["Why"] == ("You unlocked this field; MetaMender checks it again once "
                            "the field is switched on in the settings.")
    assert dict(rows["plot"].lines)["Next check"] == "19 Nov 2026"


def test_dates_and_separator_come_from_the_strings():
    assert details_model.long_date("2026-11-12") == "12 Nov 2026"
    assert details_model.long_date("2007-01-04") == "4 Jan 2007"
    assert details_model.long_date("soon") == "soon"
    assert details_model._short_date("2026-12-05") == "5 Dec"
    for code in range(32610, 32625):
        assert _STRINGS.get(code), code


def test_refilled_placeholder_is_not_metamenders():
    """The scan refills a placeholder like an empty value; a refill that
    found nothing records the placeholder as written. It is no value of
    MetaMender's: not updated, no Restore."""
    held = fingerprint("Episode 5")
    title = _by_key(_rows(
        item={"title": "Episode 5"},
        fs_map={"title": _fs("nothing", source="tmdb", next_retry="2026-11-19",
                             written=held)},
        keep={"title": {"original": "Episode 5", "has_original": True,
                        "locked": False, "written_fp": held}},
    ))["title"]
    assert title.group != "updated"
    assert title.action != "restore"
    # The placeholder is the scraper's, not TMDB's.
    assert dict(title.lines)["From"] == "your scraper"
    assert title.hint != "changed"


def test_unmanaged_certification_without_a_country_says_so():
    item = {"title": "Heat", "premiered": "1995-12-15", "mpaa": "NL:12",
            "uniqueid": {"tmdb": "949"}}
    fs_map = {"mpaa": _fs("locked", count=2, next_retry="2026-11-19")}
    keep = {"mpaa": {"original": "", "has_original": True, "locked": False,
                     "written_fp": None}}
    rows = _by_key(build_rows(MOVIE, item, None, fs_map, keep, TODAY,
                              fields=["mpaa", "plot"], managed=["plot"],
                              cert_country_missing=True))
    mpaa = rows["mpaa"]
    lines = dict(mpaa.lines)
    assert lines["Next check"] == "not while no age rating country is set up"
    assert mpaa.hint == "no country"
    assert lines["Why"] == ("You unlocked this field; MetaMender checks it again once "
                            "an age rating country is set up.")


def test_a_lockable_field_offers_keep_this_value():
    """Any managed field that is not locked and has no Restore can be kept
    as it is; a restorable one offers Restore (which locks too), a locked
    one the unlock, a rating nothing."""
    rows = _by_key(_rows())
    assert rows["title"].action == "keep"
    assert rows["director"].action == "keep"
    assert rows["plot"].action == "restore"
    assert rows["rating"].action == ""
    locked = _by_key(_rows(keep={"title": {"locked": True}}))
    assert locked["title"].action == "unlock"


def test_a_field_the_scan_does_not_manage_offers_no_keep():
    rows = _by_key(_rows(managed=["plot", "rating", "votes", "runtime", "firstaired",
                                  "title", "writer"]))
    assert rows["director"].action == ""


def test_only_a_locked_field_row_is_marked_locked():
    rows = _by_key(_rows(keep={"title": {"locked": True}}))
    assert rows["title"].locked is True
    assert rows["plot"].locked is False and rows["ids"].locked is False


def _cert_from(source, fallback):
    item = {"title": "Heat", "premiered": "1995-12-15", "mpaa": "AU:MA 15+",
            "uniqueid": {"tmdb": "949"}}
    fs_map = {"mpaa": _fs("updated", source=source, written=fingerprint("AU:MA 15+"),
                          fallback=fallback, next_retry="2026-12-12")}
    rows = _by_key(build_rows(MOVIE, item, None, fs_map, {}, TODAY, fields=["mpaa"]))
    return dict(rows["mpaa"].lines)["From"]


def test_a_converted_age_rating_says_so():
    assert _cert_from("tmdb-gb", 1) == "TMDB (GB), converted"
    assert _cert_from("fsk", 1) == "FSK, converted"
    assert _cert_from("tmdb", 0) == "TMDB"
    assert _cert_from("bbfc", 0) == "BBFC"

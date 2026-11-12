"""Tests for engine/epmatch.py: matching an episode by number."""

from resources.lib.engine.epmatch import (
    MatchVerdict,
    SiteEpisode,
    check_number_match,
    episode_title_key,
)


def _ep(number, aired, *titles, season=1, ep_id=None):
    return SiteEpisode(
        ep_id=ep_id or "{}-{}".format(season, number), season=season,
        number=number, aired=aired, titles=tuple(titles),
    )


class TestTitle:
    def test_title_agrees_despite_different_dates(self):
        site = [_ep(1, "2024-03-10", "De burgemeesters"),
                _ep(2, "2024-03-17", "De gele vlek")]
        verdict = check_number_match("De gele vlek", "2024-03-10", 1, 2, site)
        assert verdict.reason == "title"
        assert verdict.episode == site[1]

    def test_any_site_title_counts(self):
        site = [_ep(1, "2017-12-01", "Secrets", "Geheimnisse")]
        assert check_number_match("Geheimnisse", "", 1, 1, site).reason == "title"

    def test_part_markers_agree(self):
        key = episode_title_key("Home, Part 1")
        assert key
        assert key == episode_title_key("Home (1)") == episode_title_key("Home Part 1")
        assert key == episode_title_key("Home Pt. 1")
        assert key != episode_title_key("Home (2)")

    def test_title_variants_agree(self):
        for a, b in (("The New Guys", "New Guys"), ("Pride and Joy", "Pride & Joy"),
                     ("Show and Tell", "Show & Tell")):
            assert episode_title_key(a) == episode_title_key(b), (a, b)

    def test_placeholder_never_agrees(self):
        site = [_ep(3, "2019-11-19", "Aflevering 3")]
        assert check_number_match("Aflevering 3", "2019-11-12", 1, 3, site).reason == "rejected"
        assert episode_title_key("Capítulo 1") == ""
        assert episode_title_key("12") == ""
        assert episode_title_key("") == ""


class TestDate:
    def test_date_agrees_despite_different_titles(self):
        site = [_ep(1, "2022-10-23", "Het gouden uur"), _ep(2, "2022-10-30", "Schokkend")]
        verdict = check_number_match("Amsterdam Centraal", "2022-10-23", 1, 1, site)
        assert verdict == MatchVerdict(site[0], "date")

    def test_one_day_apart_passes(self):
        site = [_ep(1, "2023-01-02", "Other")]
        assert check_number_match("Mine", "2023-01-01", 1, 1, site).reason == "date"

    def test_shift_rejected(self):
        site = [_ep(11, "2007-01-04", "Back from Vacation"),
                _ep(12, "2007-01-11", "Traveling Salesmen")]
        verdict = check_number_match("Back from Vacation", "2007-01-04", 1, 12, site)
        assert verdict == MatchVerdict(None, "rejected")

    def test_shared_season_date_does_not_count(self):
        site = [_ep(n, "2017-12-01", "Title {}".format(n)) for n in range(2, 11)]
        site.insert(0, _ep(1, "2017-12-01", "Secrets"))
        assert check_number_match("Geheimnisse", "2017-12-01", 1, 1, site).reason == "rejected"
        site[0] = _ep(1, "2017-12-01", "Secrets", "Geheimnisse")
        assert check_number_match("Geheimnisse", "2017-12-01", 1, 1, site).reason == "title"

    def test_neighbour_within_a_day_blocks_date(self):
        site = [_ep(3, "2020-01-06", "Three"), _ep(4, "2020-01-07", "Four")]
        assert check_number_match("Something", "2020-01-07", 1, 3, site).reason == "rejected"

    def test_other_seasons_do_not_block_date(self):
        site = [_ep(1, "2020-01-06", "One"), _ep(1, "2020-01-06", "Other", season=2)]
        assert check_number_match("Mine", "2020-01-06", 1, 1, site).reason == "date"

    def test_bad_or_empty_dates_never_pass(self):
        site = [_ep(1, "2019-11-12", "Real")]
        for kodi_date in ("", "2019", "not a date", None):
            assert check_number_match("Mine", kodi_date, 1, 1, site).reason == "rejected"
        site = [_ep(1, "", "Real")]
        assert check_number_match("Mine", "2019-11-12", 1, 1, site).reason == "rejected"


class TestNotFound:
    def test_missing_number_is_not_found(self):
        site = [_ep(1, "1999-10-20", "I'm Luffy")]
        assert check_number_match("x", "1999-10-20", 0, 1, site) == MatchVerdict(None, "not_found")
        assert check_number_match("x", "1999-10-20", 1999, 1, site).reason == "not_found"
        assert check_number_match("x", "1999-10-20", 1, 1, []).reason == "not_found"


class TestGenericTitles:
    def test_part_and_short_generic_titles_never_agree(self):
        for title in ("Part 3", "Deel 3", "Teil 3", "Afl. 3", "Ep. 3", "S01E03",
                      "Chapter 1", "Episode 3", "3"):
            assert episode_title_key(title) == "", title
        site = [_ep(3, "2020-01-14", "Deel 3")]
        assert check_number_match("Deel 3", "2020-01-07", 1, 3, site).reason == "rejected"

    def test_real_title_with_part_still_counts(self):
        assert episode_title_key("Home, Part 1") == episode_title_key("Home (1)") != ""

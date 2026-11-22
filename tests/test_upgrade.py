import json
from unittest.mock import patch

from resources.lib.constants import COUNTRY_CODES
from resources.lib.data import upgrade
from resources.lib.data.upgrade import MOVED_IDS, convert, ensure_copy, read_user_settings

DISCOVERED = [{"path": "/m/movies/", "label": "Movies"},
              {"path": "/m/shows/", "label": "Shows"}]


def _legacy(**kw):
    base = {"enable_ratings": "false", "enable_plots": "true", "rating_source": "1",
            "country": "3", "replace_incorrect": "false"}
    base.update({k: str(v) for k, v in kw.items()})
    return base


def test_convert_copies_global_settings_to_each_library():
    config = convert(_legacy(), {"sources": []}, DISCOVERED, "nl-NL")
    assert len(config.libraries) == 2
    for lib in config.libraries:
        assert lib.included
        assert "plots" in lib.toggles and "ratings" not in lib.toggles
        assert lib.rating_source == 1
        assert lib.cert_country == 3
        assert lib.cert_replace_foreign is False
        assert lib.title_mode == "language"


def test_convert_defaults_for_absent_ids():
    lib = convert({}, {}, DISCOVERED[:1], "nl-NL").libraries[0]
    assert lib.toggles == {"ratings", "plots", "certifications"}
    assert (lib.rating_source, lib.text_source_order, lib.fallback_order) == (4, 2, 0)
    assert lib.cert_replace_foreign is True
    assert lib.cert_prefix_override is None and lib.cert_not_found is None


def test_convert_1_1_with_new_toggles_does_not_raise():
    lib = convert(_legacy(), {}, DISCOVERED[:1], "nl-NL").libraries[0]
    assert "posters" not in lib.toggles and "episode_thumbs" not in lib.toggles


def test_convert_automatic_country_stores_resolved_index():
    lib = convert(_legacy(country=0), {}, DISCOVERED[:1], "nl-NL").libraries[0]
    assert lib.cert_country == COUNTRY_CODES.index("NL") + 1


def test_convert_library_language_from_old_entry():
    old = {"default_language": "en-GB",
           "sources": [{"path": "/m/shows", "language": "de-DE", "fallback_language": "en-US"}]}
    shows = convert(_legacy(), old, DISCOVERED, "nl-NL").libraries[1]
    assert (shows.language, shows.fallback_language) == ("de-DE", "en-US")


def test_convert_library_without_entry_gets_old_default_language():
    old = {"default_language": "en-GB", "fallback_language": "fr-FR", "sources": []}
    lib = convert(_legacy(), old, DISCOVERED, "nl-NL").libraries[0]
    assert (lib.language, lib.fallback_language) == ("en-GB", "fr-FR")
    lib = convert(_legacy(), {"sources": []}, DISCOVERED, "nl-NL").libraries[0]
    assert (lib.language, lib.fallback_language) == ("nl-NL", "")


def test_convert_disabled_source_not_included_keeps_settings():
    old = {"sources": [{"path": "/m/movies/", "enabled": False}]}
    movies, shows = convert(_legacy(), old, DISCOVERED, "nl-NL").libraries
    assert not movies.included and shows.included
    assert movies.rating_source == 1 and "plots" in movies.toggles


def test_convert_subfolder_entries_become_subfolders():
    old = {"fallback_language": "en-US", "sources": [
        {"path": "/m/movies/anime/", "enabled": False},
        {"path": "/m/movies/kids", "language": "de-DE"},
        {"path": "/elsewhere/", "language": "fr-FR"},
    ]}
    config = convert(_legacy(), old, DISCOVERED, "nl-NL")
    movies = config.libraries[0]
    assert movies.subfolders == (
        {"path": "/m/movies/anime/", "enabled": False},
        {"path": "/m/movies/kids", "language": "de-DE", "fallback_language": "en-US"},
    )
    assert config.libraries[1].subfolders == ()
    assert config.folder_settings("/m/movies/anime/x.mkv", movies)[0] is False


def test_convert_multipath_folder_matches_entry():
    disc = [{"path": "multipath://%2fa%2fone%2f/%2fb%2ftwo%2f/", "label": "Both"}]
    old = {"sources": [{"path": "/b/two/", "language": "de-DE"}]}
    config = convert(_legacy(), old, disc, "nl-NL")
    assert config.folder_settings("/b/two/x.mkv", config.libraries[0])[1] == "de-DE"


MULTI = [{"path": "multipath://%2fa%2fone%2f/%2fb%2ftwo%2f/", "label": "Both"}]


def test_convert_multipath_keeps_each_folder_entry():
    old = {"sources": [{"path": "/a/one/", "language": "en-US"},
                       {"path": "/b/two/", "language": "de-DE"}]}
    config = convert(_legacy(), old, MULTI, "nl-NL")
    lib = config.libraries[0]
    assert config.folder_settings("/a/one/x.mkv", lib)[:2] == (True, "en-US")
    assert config.folder_settings("/b/two/x.mkv", lib)[:2] == (True, "de-DE")


def test_convert_multipath_one_folder_disabled():
    old = {"sources": [{"path": "/a/one/", "enabled": False},
                       {"path": "/b/two/", "language": "de-DE"}]}
    config = convert(_legacy(), old, MULTI, "nl-NL")
    lib = config.libraries[0]
    assert lib.included
    assert config.folder_settings("/a/one/x.mkv", lib)[0] is False
    assert config.folder_settings("/b/two/x.mkv", lib)[0] is True


def test_convert_multipath_all_folders_disabled_not_included():
    old = {"sources": [{"path": "/a/one/", "enabled": False},
                       {"path": "/b/two/", "enabled": False}]}
    assert not convert(_legacy(), old, MULTI, "nl-NL").libraries[0].included


def test_convert_uses_prefix_and_not_found():
    lib = convert(_legacy(override_prefix="true", rating_prefix="NL:",
                          enable_fallback="true", fallback_rating="AL"),
                  {}, DISCOVERED[:1], "nl-NL").libraries[0]
    assert (lib.cert_prefix_override, lib.cert_not_found) == ("NL:", "AL")
    lib = convert(_legacy(override_prefix="false", enable_fallback="false"),
                  {}, DISCOVERED[:1], "nl-NL").libraries[0]
    assert (lib.cert_prefix_override, lib.cert_not_found) == (None, None)


def test_convert_unparsable_integer_takes_default():
    lib = convert({"rating_source": "x"}, {}, DISCOVERED[:1], "nl-NL").libraries[0]
    assert lib.rating_source == 4


def _write_settings(path, **ids):
    body = "".join('<setting id="%s">%s</setting>' % kv for kv in ids.items())
    path.write_text('<settings version="2">%s</settings>' % body)


def test_read_user_settings_missing_or_broken(tmp_path):
    assert read_user_settings(str(tmp_path / "none.xml")) == {}
    bad = tmp_path / "bad.xml"
    bad.write_text("<settings")
    assert read_user_settings(str(bad)) == {}


def test_ensure_copy_writes_once_and_reuses(tmp_path):
    user, copy = tmp_path / "settings.xml", tmp_path / "addon" / "legacy-settings.json"
    _write_settings(user, rating_source="2", tmdb_api_key="k", enable_plots="true")
    first = ensure_copy(str(user), str(copy))
    assert first == {"rating_source": "2", "enable_plots": "true"}
    assert json.loads(copy.read_text()) == first
    _write_settings(user, tmdb_api_key="k")
    assert ensure_copy(str(user), str(copy)) == first


def test_ensure_copy_fresh_install_is_none(tmp_path):
    user = tmp_path / "settings.xml"
    _write_settings(user, tmdb_api_key="k")
    assert ensure_copy(str(user), str(tmp_path / "c.json")) is None
    assert not (tmp_path / "c.json").exists()
    assert ensure_copy(str(tmp_path / "missing.xml"), str(tmp_path / "c.json")) is None


def test_convert_resumes_from_copy_after_partial_run(tmp_path):
    user, copy = tmp_path / "settings.xml", tmp_path / "c.json"
    _write_settings(user, rating_source="1", country="3", enable_plots="true")
    first = convert(ensure_copy(str(user), str(copy)), {}, DISCOVERED, "nl-NL")
    _write_settings(user)
    again = convert(ensure_copy(str(user), str(copy)), {}, DISCOVERED, "nl-NL")
    assert first == again


def test_convert_is_deterministic():
    old = {"sources": [{"path": "/m/movies/kids", "language": "de-DE"}]}
    assert convert(_legacy(), old, DISCOVERED, "nl-NL") == convert(_legacy(), old, DISCOVERED, "nl-NL")


def test_ensure_copy_write_failure_is_none(tmp_path):
    user = tmp_path / "settings.xml"
    _write_settings(user, rating_source="2")
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert ensure_copy(str(user), str(blocker / "c.json")) is None


def test_moved_ids_have_defaults():
    covered = set(upgrade._DEFAULT_TEXT) | set(upgrade._DEFAULT_INT) | set(upgrade._DEFAULT_BOOL)
    # The art toggles are new in 1.3: they have defaults but were never 1.1 ids.
    assert covered - {"enable_posters", "enable_episode_thumbs"} == set(MOVED_IDS)


def test_convert_without_old_settings_switches_nothing_on():
    """Kodi dropped the moved ids and no copy was made: the 1.1 values are
    unknown, so nothing is switched on, but the folders 1.1 scanned stay
    included with their 1.1 languages."""
    old = {"default_language": "en-GB", "fallback_language": "fr-FR", "sources": [
        {"path": "/m/movies/", "language": "de-DE"},
        {"path": "/m/shows/", "enabled": False},
        {"path": "/m/movies/kids/", "language": "nl-NL"}]}
    config = convert(None, old, DISCOVERED, "nl-NL")
    movies, shows = config.libraries
    assert movies.included and not shows.included
    for lib in (movies, shows):
        assert lib.toggles == frozenset() and lib.cert_country == 0
    assert (movies.language, movies.fallback_language) == ("de-DE", "fr-FR")
    assert (shows.language, shows.fallback_language) == ("en-GB", "fr-FR")
    assert config.folder_settings("/m/movies/kids/a.mkv", movies)[1] == "nl-NL"


def test_convert_parent_entry_gives_its_language():
    # 1.1 matched a file to the longest configured prefix, so an entry for
    # a folder above a library set that library's language.
    old = {"sources": [{"path": "/m/", "language": "de-DE", "fallback_language": "en-US"}]}
    with patch.object(upgrade, "log") as log:
        movies, shows = convert(_legacy(), old, DISCOVERED, "nl-NL").libraries
    for lib in (movies, shows):
        assert lib.included
        assert (lib.language, lib.fallback_language) == ("de-DE", "en-US")
    log.warning.assert_not_called()


def test_convert_parent_entry_disabled_leaves_library_out():
    old = {"sources": [{"path": "/m", "enabled": False}]}
    movies, shows = convert(_legacy(), old, DISCOVERED, "nl-NL").libraries
    assert not movies.included and not shows.included
    assert "plots" in movies.toggles


def test_convert_exact_entry_wins_over_parent_and_longest_parent_wins():
    old = {"sources": [{"path": "/", "language": "fr-FR"},
                       {"path": "/m/", "language": "de-DE"},
                       {"path": "/m/shows/", "language": "en-GB"}]}
    movies, shows = convert(_legacy(), old, DISCOVERED, "nl-NL").libraries
    assert movies.language == "de-DE"
    assert shows.language == "en-GB"


def test_convert_parent_entry_reaches_each_multipath_folder():
    old = {"sources": [{"path": "/a/", "enabled": False},
                       {"path": "/b/two/", "language": "de-DE"}]}
    config = convert(_legacy(), old, MULTI, "nl-NL")
    lib = config.libraries[0]
    assert lib.included
    assert config.folder_settings("/a/one/x.mkv", lib)[0] is False
    assert config.folder_settings("/b/two/x.mkv", lib)[:2] == (True, "de-DE")


def test_ensure_copy_is_written_atomically(tmp_path):
    user, copy = tmp_path / "settings.xml", tmp_path / "c.json"
    _write_settings(user, rating_source="2")
    with patch.object(upgrade.os, "replace", side_effect=OSError("disk full")):
        assert ensure_copy(str(user), str(copy)) is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["settings.xml"]
    assert ensure_copy(str(user), str(copy)) == {"rating_source": "2"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.json", "settings.xml"]


def test_unreadable_copy_is_logged_and_treated_as_absent(tmp_path):
    user, copy = tmp_path / "settings.xml", tmp_path / "c.json"
    copy.write_text("{broken")
    _write_settings(user, rating_source="2")
    with patch.object(upgrade, "log") as log:
        assert ensure_copy(str(user), str(copy)) == {"rating_source": "2"}
    assert [c.kwargs["event"] for c in log.warning.call_args_list] == [
        "upgrade.copy_unreadable"]
    assert json.loads(copy.read_text()) == {"rating_source": "2"}

    copy.write_text("[1, 2]")
    _write_settings(user)
    with patch.object(upgrade, "log") as log:
        assert ensure_copy(str(user), str(copy)) is None
    assert [c.kwargs["event"] for c in log.warning.call_args_list] == [
        "upgrade.copy_unreadable"]

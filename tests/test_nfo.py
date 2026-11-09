"""NFO detection following Kodi's CVideoTagLoaderNFO::FindNFO."""
import os
from urllib.parse import quote

import pytest

from resources.lib.data import nfo
from resources.lib.data.nfo import NFO, NO_NFO, UNREACHABLE, nfo_state


@pytest.fixture
def vfs(kodi_stubs, monkeypatch, tmp_path):
    monkeypatch.setattr(nfo, "xbmcvfs", kodi_stubs["xbmcvfs"])

    def make(*rel):
        path = tmp_path.joinpath(*rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return str(path)

    return make


def test_same_name_nfo(vfs):
    media = vfs("Movies", "Film (2020).mkv")
    assert nfo_state("movie", media) == NO_NFO
    vfs("Movies", "Film (2020).nfo")
    assert nfo_state("movie", media) == NFO


def test_other_name_nfo_among_many_is_not_used(vfs):
    media = vfs("Movies", "A.mkv")
    vfs("Movies", "B.nfo")
    vfs("Movies", "C.nfo")
    assert nfo_state("movie", media) == NO_NFO


def test_movie_nfo_in_folder(vfs):
    media = vfs("Film", "file.mkv")
    vfs("Film", "movie.nfo")
    assert nfo_state("movie", media) == NFO


def test_movie_nfo_ignored_for_episodes(vfs):
    media = vfs("Show", "S01E01.mkv")
    vfs("Show", "movie.nfo")
    assert nfo_state("episode", media) == NO_NFO


def test_unique_nfo_in_movie_folder(vfs):
    media = vfs("Film", "file.mkv")
    vfs("Film", "whatever.nfo")
    assert nfo_state("movie", media) == NFO


def test_two_nfos_in_movie_folder_are_ambiguous(vfs):
    media = vfs("Film", "file.mkv")
    vfs("Film", "one.nfo")
    vfs("Film", "two.NFO")
    assert nfo_state("movie", media) == NO_NFO


def test_unique_nfo_not_used_for_episodes(vfs):
    media = vfs("Show", "S01E01.mkv")
    vfs("Show", "other.nfo")
    assert nfo_state("episode", media) == NO_NFO


def test_episode_same_name_nfo(vfs):
    media = vfs("Show", "S01E01.mkv")
    vfs("Show", "S01E01.nfo")
    assert nfo_state("episode", media) == NFO


def test_stack_first_file_nfo(vfs):
    a = vfs("Film", "Film-cd1.avi")
    b = vfs("Film", "Film-cd2.avi")
    vfs("Film", "Film-cd1.nfo")
    vfs("Film", "extra.nfo")  # makes the folder ambiguous
    assert nfo_state("movie", "stack://%s , %s" % (a, b)) == NFO


def test_stack_titled_nfo(vfs):
    a = vfs("Film", "Film-cd1.avi")
    b = vfs("Film", "Film-cd2.avi")
    vfs("Film", "Film.nfo")
    vfs("Film", "extra.nfo")
    assert nfo_state("movie", "stack://%s , %s" % (a, b)) == NFO


def test_stack_without_nfo(vfs):
    a = vfs("Film", "Film-cd1.avi")
    b = vfs("Film", "Film-cd2.avi")
    vfs("Film", "x.nfo")
    vfs("Film", "y.nfo")
    assert nfo_state("movie", "stack://%s , %s" % (a, b)) == NO_NFO


def test_cd1_folder_uses_parent_nfo(vfs):
    media = vfs("Film", "cd1", "Film.avi")
    vfs("Film", "Film.nfo")
    assert nfo_state("movie", media) == NFO


def test_cd1_rule_does_not_apply_to_other_folder_names(vfs):
    media = vfs("Film", "disc2", "Film.avi")
    vfs("Film", "Film.nfo")
    vfs("Film", "b.nfo")
    assert nfo_state("movie", media) == NO_NFO


def test_video_ts_uses_parent_folder_nfo(vfs):
    media = vfs("Film", "VIDEO_TS", "VIDEO_TS.IFO")
    vfs("Film", "Film.nfo")
    assert nfo_state("movie", media) == NFO


def test_bdmv_uses_parent_folder_nfo(vfs):
    media = vfs("Film", "BDMV", "index.bdmv")
    vfs("Film", "disc.nfo")
    assert nfo_state("movie", media) == NFO


def test_optical_without_nfo(vfs):
    media = vfs("Film", "BDMV", "index.bdmv")
    assert nfo_state("movie", media) == NO_NFO


def test_rar_checks_outside_the_archive(vfs):
    archive = vfs("Film", "film.rar")
    vfs("Film", "film.nfo")
    vfs("Film", "z.nfo")
    path = "rar://%s/film.mkv" % quote(archive, safe="")
    assert nfo_state("movie", path) == NFO


def test_zip_checks_outside_the_archive(vfs):
    archive = vfs("Film", "film.zip")
    vfs("Film", "film.nfo")
    vfs("Film", "z.nfo")
    path = "zip://%s/film.mkv" % quote(archive, safe="")
    assert nfo_state("movie", path) == NFO


def test_show_folder_tvshow_nfo(vfs, tmp_path):
    vfs("Show", "S01E01.mkv")
    show = str(tmp_path / "Show")
    assert nfo_state("tvshow", show) == NO_NFO
    vfs("Show", "tvshow.nfo")
    assert nfo_state("tvshow", show) == NFO


def test_show_other_nfo_is_not_tvshow_nfo(vfs, tmp_path):
    vfs("Show", "only.nfo")
    assert nfo_state("tvshow", str(tmp_path / "Show")) == NO_NFO


def test_trailing_slash_on_show_folder(vfs, tmp_path):
    vfs("Show", "tvshow.nfo")
    assert nfo_state("tvshow", str(tmp_path / "Show") + os.sep) == NFO


def test_missing_media_is_unreachable(vfs, tmp_path):
    vfs("Film", "Film.nfo")
    assert nfo_state("movie", str(tmp_path / "Film" / "gone.mkv")) == UNREACHABLE


def test_missing_show_folder_is_unreachable(vfs, tmp_path):
    assert nfo_state("tvshow", str(tmp_path / "nope")) == UNREACHABLE


def test_missing_stack_first_file_is_unreachable(vfs, tmp_path):
    b = vfs("Film", "Film-cd2.avi")
    gone = str(tmp_path / "Film" / "Film-cd1.avi")
    assert nfo_state("movie", "stack://%s , %s" % (gone, b)) == UNREACHABLE


def test_empty_path_is_unreachable(vfs):
    assert nfo_state("movie", "") == UNREACHABLE


def test_listing_failure_means_no_nfo(vfs, kodi_stubs):
    media = vfs("Film", "file.mkv")
    kodi_stubs["xbmcvfs"].listdir.side_effect = OSError("share went away")
    assert nfo_state("movie", media) == NO_NFO

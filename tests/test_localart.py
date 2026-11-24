"""Whether a local art file still exists, without mistaking an offline share
for a deleted file."""
from __future__ import annotations

import pytest

from resources.lib.data import localart
from resources.lib.data.localart import EXISTS, MISSING, UNREACHABLE, local_art_state


@pytest.fixture
def vfs(kodi_stubs, monkeypatch, tmp_path):
    monkeypatch.setattr(localart, "xbmcvfs", kodi_stubs["xbmcvfs"])

    def make(*rel):
        path = tmp_path.joinpath(*rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return str(path)

    return make


def test_local_art_states(vfs, tmp_path):
    media = vfs("Movies", "Film", "film.mkv")
    poster = vfs("Movies", "Film", "poster.jpg")
    assert local_art_state(poster, media) == EXISTS

    # Folder there, file gone.
    gone = str(tmp_path / "Movies" / "Film" / "fanart.jpg")
    assert local_art_state(gone, media) == MISSING

    # The art's own folder is gone, but the media answers and the art lay
    # under the media's folder.
    sub = str(tmp_path / "Movies" / "Film" / "extrafanart" / "fanart1.jpg")
    assert local_art_state(sub, media) == MISSING

    # Neither the art's folder nor the media answers: an offline share.
    offline_media = str(tmp_path / "Offline" / "Film" / "film.mkv")
    offline_art = str(tmp_path / "Offline" / "Film" / "art" / "poster.jpg")
    assert local_art_state(offline_art, offline_media) == UNREACHABLE

    # The art lives on another share whose folder does not answer: the
    # media being reachable says nothing about it.
    other_share = str(tmp_path / "ArtShare" / "Film" / "poster.jpg")
    assert local_art_state(other_share, media) == UNREACHABLE


def test_show_folder_media_path(vfs, tmp_path):
    """A show's media path is its folder, with a trailing separator."""
    vfs("TV", "Show", "tvshow.nfo")
    show = str(tmp_path / "TV" / "Show") + "/"
    sub = str(tmp_path / "TV" / "Show" / "extrafanart" / "fanart1.jpg")
    assert local_art_state(sub, show) == MISSING
    assert local_art_state(sub, str(tmp_path / "TV" / "Gone") + "/") == UNREACHABLE


def test_disc_image_art_beside_the_disc_folder(vfs, tmp_path):
    media = vfs("Movies", "Film", "VIDEO_TS", "VIDEO_TS.IFO")
    art = str(tmp_path / "Movies" / "Film" / "art" / "poster.jpg")
    assert local_art_state(art, media) == MISSING


def test_stacked_media_uses_the_first_part(vfs, tmp_path):
    first = vfs("Movies", "Film", "film-cd1.mkv")
    media = "stack://{} , {}".format(first, str(tmp_path / "Movies" / "Film" / "film-cd2.mkv"))
    art = str(tmp_path / "Movies" / "Film" / "art" / "poster.jpg")
    assert local_art_state(art, media) == MISSING


def test_unmounted_mount_point_is_unreachable(vfs, tmp_path):
    """A share mounted at a local path leaves an empty folder when it is
    down: with the media gone too, nothing proves the art was deleted."""
    root = tmp_path / "storage" / "movies"
    root.mkdir(parents=True)
    art = str(root / "Film (2020)-poster.jpg")
    assert local_art_state(art, str(root / "Film (2020).mkv")) == UNREACHABLE


def test_empty_show_folder_mount_point_is_unreachable(vfs, tmp_path):
    """A source whose root is one show's folder, mounted at a local path:
    when the share is down the folder exists but is empty, which says
    nothing about the show's art."""
    show = tmp_path / "media" / "nas-show"
    show.mkdir(parents=True)
    fanart = str(show / "fanart.jpg")
    assert local_art_state(fanart, str(show) + "/") == UNREACHABLE
    vfs("media", "nas-show", "Season 1", "s01e01.mkv")
    assert local_art_state(fanart, str(show) + "/") == MISSING


def test_empty_folder_without_media_is_unreachable(vfs, tmp_path):
    (tmp_path / "Sets").mkdir()
    assert local_art_state(str(tmp_path / "Sets" / "poster.jpg"), "") == UNREACHABLE


def test_non_empty_folder_with_the_file_absent_is_missing(vfs, tmp_path):
    vfs("Sets", "fanart.jpg")
    assert local_art_state(str(tmp_path / "Sets" / "poster.jpg"), "") == MISSING


def test_media_exists_and_art_absent_is_missing(vfs, tmp_path):
    media = vfs("storage", "movies", "Film (2020).mkv")
    art = str(tmp_path / "storage" / "movies" / "Film (2020)-poster.jpg")
    assert local_art_state(art, media) == MISSING


def test_art_folder_elsewhere_needs_media_and_contents(vfs, tmp_path):
    """Art outside the video's folder: its folder must hold something, and
    the media must still answer."""
    media = vfs("Movies", "Film", "film.mkv")
    (tmp_path / "Art" / "Film").mkdir(parents=True)
    art = str(tmp_path / "Art" / "Film" / "poster.jpg")
    assert local_art_state(art, media) == UNREACHABLE
    vfs("Art", "Film", "fanart.jpg")
    assert local_art_state(art, media) == MISSING
    gone = str(tmp_path / "Movies" / "Gone" / "film.mkv")
    assert local_art_state(art, gone) == UNREACHABLE


def test_listdir_error_is_unreachable(vfs, kodi_stubs, tmp_path):
    vfs("Sets", "fanart.jpg")
    kodi_stubs["xbmcvfs"].listdir.side_effect = OSError("share went away")
    assert local_art_state(str(tmp_path / "Sets" / "poster.jpg"), "") == UNREACHABLE


def test_vfs_error_is_unreachable(vfs, kodi_stubs, tmp_path):
    media = vfs("Movies", "Film", "film.mkv")
    gone = str(tmp_path / "Movies" / "Film" / "poster.jpg")
    kodi_stubs["xbmcvfs"].exists.side_effect = OSError("share went away")
    assert local_art_state(gone, media) == UNREACHABLE


def test_no_media_path_is_judged_by_the_folder_alone(vfs, tmp_path):
    vfs("Sets", "keep.txt")
    assert local_art_state(str(tmp_path / "Sets" / "poster.jpg"), "") == MISSING
    assert local_art_state(str(tmp_path / "NoSets" / "poster.jpg"), "") == UNREACHABLE

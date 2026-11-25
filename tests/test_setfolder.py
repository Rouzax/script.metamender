from __future__ import annotations

from unittest.mock import MagicMock

from resources.lib.data import setfolder
from resources.lib.data.setfolder import folder_for, legal_name


def test_legal_name_matches_kodi():
    assert legal_name('Star Wars: Saga') == 'Star Wars_ Saga'
    assert legal_name('What? "Now" <x>|y*') == 'What_ _Now_ _x__y_'
    assert legal_name('Ocean...  ') == 'Ocean'


def _exists_only(kodi_stubs, *present, monkeypatch):
    monkeypatch.setattr(setfolder, "xbmcvfs", kodi_stubs["xbmcvfs"])
    kodi_stubs["xbmcvfs"].exists = MagicMock(side_effect=lambda p: p in present)


def test_folder_for_first_existing_name(kodi_stubs, monkeypatch):
    _exists_only(kodi_stubs, "smb://nas/sets/Alien Collection/", monkeypatch=monkeypatch)
    names = ["Alien - Filmreihe", "Alien Collection"]
    assert folder_for("smb://nas/sets", names) == "Alien Collection"
    assert folder_for("smb://nas/sets/", names) == "Alien Collection"


def test_folder_for_uses_legal_name(kodi_stubs, monkeypatch):
    _exists_only(kodi_stubs, "smb://nas/sets/Star Wars_ Saga/", monkeypatch=monkeypatch)
    assert folder_for("smb://nas/sets/", ["Star Wars: Saga"]) == "Star Wars: Saga"


def test_folder_for_none(kodi_stubs, monkeypatch):
    _exists_only(kodi_stubs, monkeypatch=monkeypatch)
    assert folder_for("", ["Alien Collection"]) is None
    assert folder_for("smb://nas/sets", []) is None
    assert folder_for("smb://nas/sets", ["Alien Collection"]) is None
    kodi_stubs["xbmcvfs"].exists = MagicMock(side_effect=OSError("down"))
    assert folder_for("smb://nas/sets", ["Alien Collection"]) is None

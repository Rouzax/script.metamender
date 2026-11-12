"""addon.xml and changelog.txt agree with each other, with Kodi's schema and
with the published docs."""
from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from resources.lib.constants import DEFAULT_ADDON_ID

ROOT = Path(__file__).resolve().parents[1]
ADDON_XML = ROOT / "addon.xml"
CHANGELOG = ROOT / "changelog.txt"
# v1.0.0, v1.0.0~beta1; the date is filled in when the version is released.
HEADER = re.compile(
    r"^v(\d+\.\d+\.\d+(?:~(?:alpha|beta|rc)\d+)?) \((\d{4}-\d{2}-\d{2}|unreleased)\)$")


def _root():
    return ElementTree.parse(ADDON_XML).getroot()


def _changelog_versions() -> list[str]:
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    return [m.group(1) for line in lines if (m := HEADER.match(line))]


def test_addon_id_matches_constants():
    assert _root().get("id") == DEFAULT_ADDON_ID


def test_every_changelog_entry_header_has_the_release_format():
    headers = [line for line in CHANGELOG.read_text(encoding="utf-8").splitlines()
               if line.startswith("v")]
    assert headers
    for line in headers:
        assert HEADER.match(line), line


def test_the_addon_version_is_the_newest_changelog_entry():
    """Kodi reads the tilde form; tags and file names use a hyphen."""
    assert _root().get("version") == _changelog_versions()[0]


def test_news_fits_kodis_metadata_schema():
    """The schema caps <news> at 1500 characters; the checker fails past it."""
    news = _root().find("extension/news")
    assert news is not None and 0 < len(news.text or "") <= 1500


def test_every_declared_asset_ships_with_the_addon():
    """Kodi shows a blank tile for a declared asset that is missing."""
    assets = _root().find("extension/assets")
    assert assets is not None
    for asset in assets:
        assert (ROOT / (asset.text or "")).is_file(), asset.tag
    assert assets.findtext("icon") == "icon.png"
    assert assets.findtext("fanart") == "fanart.jpg"


def test_fanart_has_a_size_the_official_repository_accepts():
    """The add-on rules allow 1280x720, 1920x1080 or 3840x2160; read from the
    JPEG's frame header."""
    data = (ROOT / "fanart.jpg").read_bytes()
    i = 2
    while data[i] == 0xFF and data[i + 1] not in (0xC0, 0xC1, 0xC2):
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    height = int.from_bytes(data[i + 5:i + 7], "big")
    width = int.from_bytes(data[i + 7:i + 9], "big")
    assert (width, height) in {(1280, 720), (1920, 1080), (3840, 2160)}


def test_the_links_are_the_published_site_and_repository():
    """Kodi's add-on info shows these; they are the URLs the site and repo use."""
    site_url = re.search(r'^site_url = "([^"]+)"', (ROOT / "zensical.toml").read_text(), re.MULTILINE)
    assert site_url is not None
    assert _root().findtext(".//website") == site_url.group(1).removesuffix("docs/")
    repo = re.search(r'^repo_url = "([^"]+)"', (ROOT / "zensical.toml").read_text(), re.MULTILINE)
    assert repo is not None and _root().findtext(".//source") == repo.group(1)


def test_the_licence_matches_the_licence_file():
    assert _root().findtext(".//license") == "GPL-3.0-only"
    assert "GNU GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE.txt").read_text()[:200]
    assert "Version 3" in (ROOT / "LICENSE.txt").read_text()[:200]

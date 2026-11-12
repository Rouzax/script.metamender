"""The generated art: the add-on icon and the setup window's textures."""
from __future__ import annotations

import os
import re

from PIL import Image

MEDIA = "resources/skins/Default/media/metamender/"
XML = "resources/skins/Default/1080i/script-metamender-setup.xml"
TEXTURES = ["panel", "frame", "button", "row", "check_off", "check_on", "radio_off",
            "radio_on", "badge", "pulse", "dot", "divider"]


def test_icon_is_512_square():
    assert Image.open("icon.png").size == (512, 512)


def test_textures_exist_and_are_white_on_transparent():
    # White so the XML can tint each one with colordiffuse.
    for name in TEXTURES:
        data = Image.open(MEDIA + name + ".png").convert("RGBA").tobytes()
        pixels = [data[i:i + 4] for i in range(0, len(data), 4)]
        opaque = [p for p in pixels if p[3] > 200]
        assert opaque, name
        assert all(p[:3] == b"\xff\xff\xff" for p in opaque), name


def test_xml_textures_exist():
    with open(XML) as handle:
        text = handle.read()
    for name in set(re.findall(r"metamender/([a-z_]+\.png)", text)):
        assert os.path.exists(MEDIA + name), name


def test_xml_reads_only_properties_the_window_sets():
    from resources.lib.ui.theme import THEMES
    GOLDEN_HOUR = THEMES[0]
    with open(XML) as handle:
        text = handle.read()
    used = set(re.findall(r"Window\.Property\((MM\.[A-Za-z]+)\)", text))
    produced = {"MM.Heading", "MM.Text", "MM.Next", "MM.Back", "MM.Cancel",
                "MM.StepIndex", "MM.StepTotal", "MM.Marker", *GOLDEN_HOUR}
    assert used <= produced, used - produced
    assert {"MM.Accent", "MM.StepTotal", "MM.Marker"} <= used


def test_addon_xml_declares_the_icon():
    # Kodi 19 and later read the icon only from <assets>; a root icon.png
    # without it is never shown, in the add-on browser or on toasts.
    import xml.etree.ElementTree as ET
    root = ET.parse("addon.xml").getroot()
    meta = root.find("extension[@point='xbmc.addon.metadata']")
    assert meta is not None
    icon = meta.findtext("assets/icon")
    assert icon == "icon.png" and os.path.exists(icon)


def test_every_colour_the_window_sets_is_used():
    from resources.lib.ui.theme import THEMES
    GOLDEN_HOUR = THEMES[0]
    with open(XML) as handle:
        text = handle.read()
    used = set(re.findall(r"Window\.Property\((MM\.[A-Za-z]+)\)", text))
    assert set(GOLDEN_HOUR) <= used, set(GOLDEN_HOUR) - used


PROGRESS_XML = "resources/skins/Default/1080i/script-metamender-progress.xml"


def test_progress_xml_textures_exist():
    with open(PROGRESS_XML) as handle:
        text = handle.read()
    for name in set(re.findall(r"metamender/([a-z_]+\.png)", text)):
        assert os.path.exists(MEDIA + name), name


def test_progress_xml_reads_only_properties_the_window_sets():
    from resources.lib.service.refresh import RefreshProgress
    from resources.lib.ui.progress_window import progress_properties
    with open(PROGRESS_XML) as handle:
        text = handle.read()
    used = set(re.findall(r"Window\.Property\((MM\.[A-Za-z]+)\)", text))
    produced = set(progress_properties(RefreshProgress(), "x", 0.0))
    assert used <= produced, used - produced
    assert {"MM.Heading", "MM.Current", "MM.Stop", "MM.Background", "MM.Accent"} <= used


def test_progress_buttons_and_bar_match_the_window_code():
    from resources.lib.ui import progress_window
    with open(PROGRESS_XML) as handle:
        text = handle.read()
    assert 'id="%d"' % progress_window.STOP in text
    assert 'id="%d"' % progress_window.BACKGROUND in text
    assert '<defaultcontrol always="true">%d</defaultcontrol>' % progress_window.BACKGROUND in text
    assert '<control type="progress" id="40">' in text


def test_context_menu_shows_on_seasons():
    with open("addon.xml") as handle:
        assert "String.IsEqual(ListItem.DBType,season)" in handle.read()


DOCS_ASSETS = "docs/assets/"
SITE_IMAGES = "site/images/"


def _has_transparency(path):
    alpha = Image.open(path).convert("RGBA").getchannel("A")
    return alpha.getextrema()[0] == 0


def test_wordmarks_are_wide_and_transparent():
    # One for dark backgrounds (light lettering), one for light ones.
    for name in ("wordmark-on-dark.png", "wordmark-on-light.png"):
        width, height = Image.open(DOCS_ASSETS + name).size
        assert width > 3 * height, name
        assert _has_transparency(DOCS_ASSETS + name), name


def test_banner_is_1600x400():
    assert Image.open(DOCS_ASSETS + "banner.png").size == (1600, 400)


def test_social_preview_is_1280x640():
    # GitHub's recommended social preview size.
    assert Image.open(DOCS_ASSETS + "social-preview.png").size == (1280, 640)


def test_site_images_match_docs_assets():
    for name in ("wordmark-on-dark.png", "social-preview.png"):
        with open(DOCS_ASSETS + name, "rb") as a, open(SITE_IMAGES + name, "rb") as b:
            assert a.read() == b.read(), name
    with open("icon.png", "rb") as a, open(SITE_IMAGES + "icon.png", "rb") as b:
        assert a.read() == b.read()

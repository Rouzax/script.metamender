"""The generated art: the add-on icon and the setup window's textures."""
from __future__ import annotations

import os
import re

from PIL import Image

MEDIA = "resources/skins/Default/media/metamender/"
XML = "resources/skins/Default/1080i/script-metamender-setup.xml"
TEXTURES = ["panel", "frame", "glow", "button", "row", "check_off", "check_on", "radio_off",
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
    from resources.lib.ui.setup_window import GOLDEN_HOUR
    with open(XML) as handle:
        text = handle.read()
    used = set(re.findall(r"Window\.Property\((MM\.[A-Za-z]+)\)", text))
    produced = {"MM.Heading", "MM.Text", "MM.Next", "MM.Back", "MM.Cancel",
                "MM.StepIndex", "MM.StepTotal", "MM.Marker", *GOLDEN_HOUR}
    assert used <= produced, used - produced
    assert {"MM.Accent", "MM.StepTotal", "MM.Marker"} <= used

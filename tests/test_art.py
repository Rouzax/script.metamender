"""The generated art: the add-on icon and the setup window's textures."""
from __future__ import annotations

from PIL import Image

MEDIA = "resources/skins/Default/media/metamender/"
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

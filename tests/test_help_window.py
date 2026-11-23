"""Tests for the "Manual and help" entry: the settings action, its dispatch,
the help window and the QR code image it shows."""
from __future__ import annotations

import importlib.util
import re
import struct
import sys
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from unittest.mock import MagicMock

from resources.lib.service import entry
from resources.lib.ui import help_window
from resources.lib.ui.theme import THEMES

_ROOT = Path(__file__).resolve().parent.parent
_SKIN = _ROOT / "resources/skins/Default/1080i/script-metamender-help.xml"
_QR = _ROOT / "resources/skins/Default/media/metamender/manual-qr.png"
_URL = "https://rouzax.github.io/script.metamender/docs/"


def _build_art():
    spec = importlib.util.spec_from_file_location("build_art", _ROOT / "docs/art/build_art.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_help_is_the_first_setting_and_an_action():
    root = ET.parse(_ROOT / "resources/settings.xml").getroot()
    first = root.find("section/category/group/setting")
    assert first is not None and first.get("id") == "help"
    assert first.get("type") == "action"
    assert (first.findtext("data") or "").strip() == "RunScript(script.metamender,help)"
    assert first.find("control[@type='button']/close") is not None


def test_help_argument_opens_the_help_window(monkeypatch):
    show = MagicMock()
    run = MagicMock()
    monkeypatch.setattr(entry, "show_help", show)
    monkeypatch.setattr(entry, "run", run)
    entry.main(["help"])
    show.assert_called_once_with()
    run.assert_not_called()


def test_help_properties_carry_the_address_and_the_image(monkeypatch):
    monkeypatch.setattr(help_window, "lang", lambda i: "#%d" % i)
    monkeypatch.setattr(help_window, "kodi_lang", lambda i: "Close")
    props = help_window.help_properties()
    assert props["MM.Url"] == "rouzax.github.io/script.metamender/docs/"
    assert props["MM.Qr"] == "metamender/manual-qr.png"
    assert props["MM.Primary"] == "Close"
    assert props["MM.Heading"].startswith("#") and props["MM.Message"].startswith("#")
    assert not set(THEMES[0]) & set(props)  # theme colours live on the Home window


def test_the_shown_address_is_the_qr_target_without_its_scheme():
    assert help_window.MANUAL_URL == _URL
    assert _URL == "https://" + help_window.MANUAL_ADDRESS


def test_show_help_sets_every_property_and_waits_for_the_window(monkeypatch):
    window = MagicMock()
    monkeypatch.setattr(help_window, "HelpWindow", MagicMock(return_value=window))
    monkeypatch.setattr(help_window, "help_properties", lambda: {"MM.Url": "u", "MM.Qr": "q"})
    help_window.show_help()
    window.setProperty.assert_any_call("MM.Url", "u")
    window.setProperty.assert_any_call("MM.Qr", "q")
    window.doModal.assert_called_once_with()


def test_skin_follows_the_message_window_style():
    xml = _SKIN.read_text(encoding="utf-8")
    assert "metamender/panel.png" in xml and "metamender/frame.png" in xml
    assert "Window.Property(MM.Url)" in xml and "Window.Property(MM.Qr)" in xml
    assert 'id="%d"' % help_window.CLOSE in xml
    assert "<include" not in xml
    assert not re.search(r"<font>(?!font1[023]<)", xml)


def _png_pixels(path: Path) -> tuple[int, int, set[tuple[int, ...]]]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, header, palette = 8, b"", b"", b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = body
        elif kind == b"PLTE":
            palette = body
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, depth, colour = struct.unpack(">IIBB", header[:10])
    assert depth <= 8 and colour in (0, 2, 3), "unexpected PNG layout"
    channels = {0: 1, 2: 3, 3: 1}[colour]
    raw = zlib.decompress(idat)
    row = (width * depth + 7) // 8 * channels
    assert len(raw) == height * (row + 1)
    colours: set[tuple[int, ...]] = set()
    line = bytes(row)
    for y in range(height):
        filter_type = raw[y * (row + 1)]
        assert filter_type in (0, 2), "a PNG row filter this reader lacks"
        data = raw[y * (row + 1) + 1:(y + 1) * (row + 1)]
        line = data if filter_type == 0 else bytes((a + b) & 255 for a, b in zip(data, line))
        if colour == 3:
            colours |= {tuple(palette[i * 3:i * 3 + 3]) for i in _indices(line, width, depth)}
        elif colour == 0:
            top = (1 << depth) - 1
            colours |= {(i * 255 // top,) for i in _indices(line, width, depth)}
        else:
            colours |= {tuple(line[i:i + channels]) for i in range(0, len(line), channels)}
    return width, height, colours


def _indices(line: bytes, width: int, depth: int) -> list[int]:
    if depth == 8:
        return list(line[:width])
    per = 8 // depth
    out = [(byte >> (8 - depth * (k + 1))) & ((1 << depth) - 1)
           for byte in line for k in range(per)]
    return out[:width]


def test_the_qr_image_is_a_square_of_black_and_white_only():
    width, height, colours = _png_pixels(_QR)
    assert width == height >= 300
    assert colours <= {(0,), (255,), (0, 0, 0), (255, 255, 255)}
    assert len(colours) == 2


def test_the_qr_image_has_a_four_module_white_quiet_zone():
    width, _, _ = _png_pixels(_QR)
    # 33 modules (version 4) plus 4 on each side; the skin draws it at its own size.
    assert width % 41 == 0


def test_build_art_encodes_the_exact_url_at_level_m(monkeypatch, tmp_path):
    art = _build_art()
    assert art.MANUAL_URL == _URL == help_window.MANUAL_URL
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "segno", fake)
    art.manual_qr(str(tmp_path / "qr.png"))
    fake.make.assert_called_once()
    assert fake.make.call_args.args == (_URL,)
    assert fake.make.call_args.kwargs["error"] == "m"
    save = fake.make.return_value.save
    assert save.call_args.kwargs["border"] == 4

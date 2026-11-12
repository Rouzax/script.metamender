#!/usr/bin/env python3
"""Render metamender's art: the add-on icon and the setup window textures.

Run from the repository root:

    python3 docs/art/build_art.py

Writes icon.png (512x512, full colour) and the textures under
resources/skins/Default/media/metamender/, then the docs art: the wordmark
(icon, a light "Meta" and a heavy amber "Mender") for dark and for light
backgrounds, the docs banner and the GitHub social preview, under
docs/assets/, with the landing page's copies under site/images/. The textures are white on
transparent so the window XML tints each one with colordiffuse; the nine-slice
ones (panel, frame, button, row) are 64x64 with their rounding inside a 20px
border. Also writes docs/art/preview-small.png, the icon at Kodi toast sizes,
for checking that it still reads when small (not committed).

Needs rsvg-convert (librsvg) and Pillow. This folder is under docs/, which the
release zip check refuses, so none of it ships with the add-on.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MEDIA = os.path.join(ROOT, "resources", "skins", "Default", "media", "metamender")
DOCS_ASSETS = os.path.join(ROOT, "docs", "assets")
SITE_IMAGES = os.path.join(ROOT, "site", "images")
ART = os.path.dirname(os.path.abspath(__file__))

AMBER = "#F5A623"
AMBER_LIGHT = "#F5C564"
AMBER_DARK = "#D4912A"
NAVY = "#1A1D23"
INK = "#060607"
LIGHT_TEXT = "#F1F3F5"

TAGLINE = "No gaps. No stale scores. Age ratings you can trust."
# Noto Sans Display carries both weights the wordmark contrasts; rsvg finds
# it through fontconfig.
WORDMARK_FONT = "Noto Sans Display"

# The heartbeat as drawn on the icon's badge (centre 372,356, radius 92).
PULSE = "300,360 334,360 352,322 374,396 394,340 408,360 444,360"


def _svg(width: int, height: int, body: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{1}" '
            'viewBox="0 0 {0} {1}">{2}</svg>').format(width, height, body)


def _render(svg: str, out: str, width: int) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as handle:
        handle.write(svg)
        path = handle.name
    try:
        subprocess.run(["rsvg-convert", "-w", str(width), "-o", out, path], check=True)
    finally:
        os.remove(path)


def _pattern() -> str:
    """Faint rating numbers, stars and heartbeat strokes behind the card, the
    way eucert's tile carries faint rating badges."""
    marks = []
    texts = ["7.8", "★", "8.4", "6.9", "★", "9.1"]
    for row in range(8):
        for col in range(6):
            x = 40 + col * 80 + (40 if row % 2 else 0)
            y = 70 + row * 58
            if (row + col) % 3 == 2:
                marks.append('<polyline points="{0},{1} {2},{1} {3},{4} {5},{6} {7},{1} {8},{1}" '
                             'fill="none" stroke="#fff" stroke-width="4" stroke-linejoin="round" '
                             'stroke-linecap="round"/>'.format(
                                 x - 24, y, x - 10, x - 4, y - 14, x + 6, y + 14, x + 12, x + 24))
            else:
                marks.append('<text x="{0}" y="{1}" text-anchor="middle" font-family="DejaVu Sans" '
                             'font-weight="700" font-size="22" fill="#fff">{2}</text>'.format(
                                 x, y + 8, texts[(row * 6 + col) % len(texts)]))
    return '<g opacity="0.055">{}</g>'.format("".join(marks))


def icon_svg(backdrop: bool = True) -> str:
    """The add-on icon. Without backdrop the square of ink behind the rounded
    tile is left out, for the wordmark on any background."""
    defs = """
<defs>
  <radialGradient id="tile" cx="50%" cy="42%" r="70%">
    <stop offset="0" stop-color="#262a33"/><stop offset="1" stop-color="{navy}"/>
  </radialGradient>
  <linearGradient id="card" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#e6e9ee"/>
  </linearGradient>
  <linearGradient id="poster" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#3a404b"/><stop offset="1" stop-color="#22262d"/>
  </linearGradient>
  <radialGradient id="badge" cx="38%" cy="32%" r="75%">
    <stop offset="0" stop-color="{light}"/><stop offset="0.55" stop-color="{amber}"/>
    <stop offset="1" stop-color="{dark}"/>
  </radialGradient>
  <filter id="glow" x="-10%" y="-10%" width="120%" height="120%">
    <feGaussianBlur stdDeviation="9" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="12"/>
    <feOffset dx="0" dy="14"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.55"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="tileclip"><rect x="24" y="24" width="464" height="464" rx="44"/></clipPath>
</defs>""".format(navy=NAVY, light=AMBER_LIGHT, amber=AMBER, dark=AMBER_DARK)
    tile = """
{backdrop}<rect x="24" y="24" width="464" height="464" rx="44" fill="url(#tile)"/>
<g clip-path="url(#tileclip)">{pattern}</g>
<rect x="24" y="24" width="464" height="464" rx="44" fill="none" stroke="{amber}"
      stroke-width="6" filter="url(#glow)"/>""".format(backdrop='<rect width="512" height="512" fill="{}"/>'.format(INK) if backdrop else "",
           pattern=_pattern(), amber=AMBER)
    card = """
<g transform="rotate(-6 236 238)" filter="url(#shadow)">
  <rect x="70" y="120" width="330" height="236" rx="22" fill="url(#card)"/>
  <rect x="92" y="142" width="96" height="140" rx="10" fill="url(#poster)"/>
  <rect x="206" y="148" width="168" height="20" rx="10" fill="#2b3038"/>
  <rect x="206" y="186" width="140" height="13" rx="6.5" fill="#9aa0a8"/>
  <rect x="206" y="210" width="158" height="13" rx="6.5" fill="#9aa0a8"/>
  <rect x="206" y="234" width="112" height="13" rx="6.5" fill="#9aa0a8"/>
  <g fill="#2b3038"><circle cx="104" cy="318" r="11"/><circle cx="134" cy="318" r="11"/>
    <circle cx="164" cy="318" r="11"/><circle cx="194" cy="318" r="11"/>
    <circle cx="224" cy="318" r="11" fill="#c9cdd2"/></g>
</g>"""
    badge = """
<g filter="url(#shadow)">
  <circle cx="372" cy="356" r="92" fill="url(#badge)" stroke="{ink}" stroke-width="14"/>
</g>
<polyline points="{pulse}" fill="none" stroke="{ink}" stroke-width="16"
          stroke-linejoin="round" stroke-linecap="round"/>""".format(ink=INK, pulse=PULSE)
    return _svg(512, 512, defs + tile + card + badge)


def _rounded(size: int, radius: int, inset: float = 0, stroke: float = 0) -> str:
    edge = size - 2 * inset
    if stroke:
        return ('<rect x="{0}" y="{0}" width="{1}" height="{1}" rx="{2}" fill="none" '
                'stroke="#fff" stroke-width="{3}"/>').format(inset, edge, radius, stroke)
    return '<rect x="{0}" y="{0}" width="{1}" height="{1}" rx="{2}" fill="#fff"/>'.format(
        inset, edge, radius)


TICK = "M18 33 L28 43 L47 22"


def textures() -> dict[str, tuple[str, int]]:
    """Texture name to (SVG, pixel width)."""
    check_on = ('<defs><mask id="m"><rect width="64" height="64" fill="#fff"/>'
                '<path d="{0}" fill="none" stroke="#000" stroke-width="8" '
                'stroke-linecap="round" stroke-linejoin="round"/></mask></defs>'
                '<rect x="4" y="4" width="56" height="56" rx="12" fill="#fff" mask="url(#m)"/>'
                ).format(TICK)
    pulse = ('<polyline points="8,33 20,33 26,20 34,46 41,26 46,33 56,33" fill="none" '
             'stroke="#fff" stroke-width="6" stroke-linejoin="round" stroke-linecap="round"/>')
    divider = ('<defs><linearGradient id="d" x1="0" y1="0" x2="1" y2="0">'
               '<stop offset="0" stop-color="#fff" stop-opacity="0"/>'
               '<stop offset="0.5" stop-color="#fff" stop-opacity="1"/>'
               '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>'
               '<rect width="512" height="4" fill="url(#d)"/>')
    return {
        "panel": (_svg(64, 64, _rounded(64, 18)), 64),
        "frame": (_svg(64, 64, _rounded(64, 17, inset=1.5, stroke=3)), 64),
        "button": (_svg(64, 64, _rounded(64, 14)), 64),
        "row": (_svg(64, 64, _rounded(64, 10)), 64),
        "check_off": (_svg(64, 64, _rounded(64, 12, inset=5, stroke=6)), 64),
        "check_on": (_svg(64, 64, check_on), 64),
        "radio_off": (_svg(64, 64, '<circle cx="32" cy="32" r="26" fill="none" '
                                   'stroke="#fff" stroke-width="6"/>'), 64),
        "radio_on": (_svg(64, 64, '<circle cx="32" cy="32" r="26" fill="none" stroke="#fff" '
                                  'stroke-width="6"/><circle cx="32" cy="32" r="14" '
                                  'fill="#fff"/>'), 64),
        "badge": (_svg(64, 64, '<circle cx="32" cy="32" r="31" fill="#fff"/>'), 64),
        "pulse": (_svg(64, 64, pulse), 64),
        "dot": (_svg(32, 32, '<circle cx="16" cy="16" r="15" fill="#fff"/>'), 32),
        "divider": (_svg(512, 4, divider), 512),
    }


def preview_small(icon_path: str, out: str) -> None:
    """The icon at 96, 64 and 40px on a toast-coloured strip."""
    sizes = (96, 64, 40)
    strip = Image.new("RGBA", (sum(sizes) + 20 * (len(sizes) + 1), 116), "#1c1f24")
    icon = Image.open(icon_path).convert("RGBA")
    x = 20
    for size in sizes:
        strip.alpha_composite(icon.resize((size, size), Image.Resampling.LANCZOS), (x, 10))
        x += size + 20
    strip.save(out)


def _icon_at(x: int, y: int, size: int) -> str:
    """The icon as a nested SVG placed at (x, y), size pixels square."""
    return re.sub(r'width="512" height="512"', 'x="{}" y="{}" width="{}" height="{}"'.format(
        x, y, size, size), icon_svg(backdrop=False), count=1)


def wordmark_svg(dark_text: bool) -> str:
    """The icon, a light "Meta" and a heavy amber "Mender". Navy lettering
    (dark_text) for light backgrounds, near-white for dark ones. The canvas is
    roomier than the mark; _render_trimmed crops it."""
    meta = NAVY if dark_text else LIGHT_TEXT
    text = ('<text x="250" y="160" font-family="{font}" font-size="120">'
            '<tspan fill="{meta}" font-weight="300">Meta</tspan>'
            '<tspan fill="{amber}" font-weight="800">Mender</tspan></text>').format(
                font=WORDMARK_FONT, meta=meta, amber=AMBER)
    return _svg(1300, 240, _icon_at(40, 30, 180) + text)


def _render_trimmed(svg: str, out: str, width: int, pad: int = 8) -> None:
    """Render, then crop to the drawn pixels plus pad on every side."""
    _render(svg, out, width)
    image = Image.open(out).convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box:
        left, top, right, bottom = box
        image = image.crop((max(left - pad, 0), max(top - pad, 0),
                            min(right + pad, image.width), min(bottom + pad, image.height)))
    image.save(out)


def _backdrop_svg(width: int, height: int, tagline_y: int, tagline_size: int) -> str:
    """Navy with the icon's faint pattern and an amber rule, the tagline
    centred under where the wordmark goes."""
    defs = ('<defs><radialGradient id="bg" cx="50%" cy="40%" r="80%">'
            '<stop offset="0" stop-color="#262a33"/><stop offset="1" stop-color="{navy}"/>'
            '</radialGradient></defs>').format(navy=NAVY)
    # Fainter than on the icon: here it sits behind the lettering.
    tiles = '<g opacity="0.5">{}</g>'.format("".join(
        '<g transform="translate({} {})">{}</g>'.format(x, y, _pattern())
        for x in range(0, width, 512) for y in range(0, height, 512)))
    rule = '<rect x="0" y="{}" width="{}" height="6" fill="{}"/>'.format(height - 6, width, AMBER)
    tagline = ('<text x="{x}" y="{y}" text-anchor="middle" font-family="{font}" '
               'font-size="{size}" fill="{fill}">{text}</text>').format(
                   x=width // 2, y=tagline_y, font=WORDMARK_FONT, size=tagline_size,
                   fill="#A8AEB8", text=TAGLINE)
    return _svg(width, height, defs + '<rect width="{}" height="{}" fill="url(#bg)"/>'.format(
        width, height) + tiles + rule + tagline)


def _poster(out: str, size: tuple[int, int], mark_width: int, mark_top: int,
            tagline_y: int, tagline_size: int) -> None:
    """A backdrop with the wordmark centred on it."""
    width, height = size
    _render(_backdrop_svg(width, height, tagline_y, tagline_size), out, width)
    with tempfile.TemporaryDirectory() as tmp:
        mark_path = os.path.join(tmp, "mark.png")
        _render_trimmed(wordmark_svg(dark_text=False), mark_path, 2600, pad=0)
        mark = Image.open(mark_path).convert("RGBA")
    mark = mark.resize((mark_width, round(mark.height * mark_width / mark.width)),
                       Image.Resampling.LANCZOS)
    poster = Image.open(out).convert("RGBA")
    poster.alpha_composite(mark, ((width - mark_width) // 2, mark_top))
    poster.convert("RGB").save(out)


def docs_art() -> None:
    os.makedirs(DOCS_ASSETS, exist_ok=True)
    os.makedirs(SITE_IMAGES, exist_ok=True)
    _render_trimmed(wordmark_svg(dark_text=False),
                    os.path.join(DOCS_ASSETS, "wordmark-on-dark.png"), 1300)
    _render_trimmed(wordmark_svg(dark_text=True),
                    os.path.join(DOCS_ASSETS, "wordmark-on-light.png"), 1300)
    _poster(os.path.join(DOCS_ASSETS, "banner.png"), (1600, 400), 1000, 80, 330, 40)
    _poster(os.path.join(DOCS_ASSETS, "social-preview.png"), (1280, 640), 980, 190, 470, 40)
    for name in ("wordmark-on-dark.png", "social-preview.png"):
        shutil.copyfile(os.path.join(DOCS_ASSETS, name), os.path.join(SITE_IMAGES, name))
    shutil.copyfile(os.path.join(ROOT, "icon.png"), os.path.join(SITE_IMAGES, "icon.png"))


def main() -> None:
    icon_path = os.path.join(ROOT, "icon.png")
    _render(icon_svg(), icon_path, 512)
    for name, (svg, width) in textures().items():
        _render(svg, os.path.join(MEDIA, name + ".png"), width)
    preview_small(icon_path, os.path.join(ART, "preview-small.png"))
    docs_art()


if __name__ == "__main__":
    main()

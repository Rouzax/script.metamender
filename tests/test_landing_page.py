"""The hand-built landing page (site/): its colours are the add-on's own, its
headline is the agreed one, and every local link resolves."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
HEADLINE = "No gaps. No stale scores. Age ratings you can trust."
THEME_NAMES = {0: "golden-hour", 1: "ultraviolet", 2: "ember", 3: "nightfall"}


def _html() -> str:
    return (SITE / "index.html").read_text(encoding="utf-8")


def _css() -> str:
    return (SITE / "style.css").read_text(encoding="utf-8")


def test_theme_tokens_match_the_addon():
    from resources.lib.ui.theme import THEMES
    css = _css().lower()
    for index, name in THEME_NAMES.items():
        accent = "#" + THEMES[index]["MM.Accent"][2:].lower()
        focus = "#" + THEMES[index]["MM.ButtonFocus"][2:].lower()
        assert f"--accent-{name}: {accent};" in css, name
        assert f"--focus-{name}: {focus};" in css, name


def test_every_theme_has_a_swatch():
    html = _html()
    for name in THEME_NAMES.values():
        assert f'data-theme="{name}"' in html, name


def test_headline_is_the_agreed_one():
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", _html(), re.DOTALL)
    assert h1 and " ".join(h1.group(1).split()) == HEADLINE


def test_local_links_resolve():
    for target in re.findall(r'(?:src|href)="([^"#]+)"', _html()):
        if re.match(r"[a-z]+:", target) or target == "./":
            continue
        if target.startswith("docs/"):
            # The docs build lands under /docs/ next to the landing page.
            source = ROOT / target
            assert source.exists() or (source / "index.md").exists() \
                or source.with_suffix(".md").exists() or target == "docs/", target
            continue
        assert (SITE / target).exists(), target


def test_support_link_sits_in_the_nav():
    nav = re.search(r"<nav.*?</nav>", _html(), re.DOTALL)
    assert nav and 'class="nav-support"' in nav.group(0)
    assert "https://ko-fi.com/O0W221GBUG" in nav.group(0)


def test_no_external_scripts_or_styles():
    """Everything the page runs or styles itself with ships in site/."""
    html = _html()
    for tag in re.findall(r"<script[^>]*src=\"([^\"]+)\"|<link[^>]*stylesheet[^>]*href=\"([^\"]+)\"",
                          html):
        url = tag[0] or tag[1]
        assert not re.match(r"https?:", url), url

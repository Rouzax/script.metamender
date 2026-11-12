"""The docs site and the landing page: every page reachable, every link
resolving, nothing private or em-dashed in published text.

The site itself builds with Zensical (`zensical build --clean --strict`, see
CLAUDE.md); these checks run without it.
"""
from __future__ import annotations

import glob
import os
import re

DOCS = "docs/"
CONFIG = "zensical.toml"


def _config() -> str:
    with open(CONFIG, encoding="utf-8") as handle:
        return handle.read()


def _nav_pages() -> list[str]:
    # Python 3.10 has no tomllib; the nav block is a plain list of strings.
    block = re.search(r"^nav = \[(.*?)^\]", _config(), re.DOTALL | re.MULTILINE)
    assert block, "zensical.toml has no nav"
    return re.findall(r'"([\w-]+\.md)"', block.group(1))


def _pages() -> list[str]:
    return sorted(glob.glob(DOCS + "*.md"))


def _published_text() -> list[str]:
    return _pages() + ["README.md"] + glob.glob("site/*.html") + glob.glob("site/*.css") \
        + glob.glob("site/*.js")


def test_every_nav_page_exists():
    for page in _nav_pages():
        assert os.path.isfile(DOCS + page), page


def test_every_page_is_in_the_nav():
    nav = set(_nav_pages())
    for path in _pages():
        assert os.path.basename(path) in nav, path


def test_art_and_working_files_are_excluded():
    config = _config()
    exclude = re.search(r"\[project\.plugins\.exclude\]\s*glob = \[(.*?)\]", config, re.DOTALL)
    assert exclude, "no exclude block"
    assert '"art/**"' in exclude.group(1)
    assert '"superpowers/**"' in exclude.group(1)


def test_published_text_has_no_em_dash():
    for path in _published_text():
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                assert "\u2014" not in handle.read(), path


def test_published_text_leaks_nothing_private():
    private = re.compile(r"/home/|builder|\b[0-9a-f]{32,}\b", re.IGNORECASE)
    for path in _published_text():
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                found = private.search(handle.read())
            assert not found, f"{path}: {found.group(0) if found else ''}"


def test_relative_links_resolve():
    link = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)|src=\"([^\"#:]+)\"")
    for path in _pages():
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        for match in link.finditer(text):
            target = match.group(1) or match.group(2)
            if re.match(r"[a-z]+:", target):
                continue
            assert os.path.exists(os.path.join(DOCS, target)), f"{path}: {target}"

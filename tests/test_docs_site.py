"""The docs site and the landing page: every page reachable, every link
resolving, nothing private or em-dashed in published text.

The site itself builds with Zensical (`zensical build --clean --strict`, see
CLAUDE.md); these checks run without it.
"""
from __future__ import annotations

import getpass
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
    # The builder's own user name and home folder, read here rather than
    # spelled out, so this test names no one either.
    user = re.escape(getpass.getuser())
    private = re.compile(r"/home/|" + user + r"|\b[0-9a-f]{32,}\b", re.IGNORECASE)
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


def _settings_labels() -> dict[str, str]:
    """Setting id -> its English label, for every setting a user can see."""
    with open("resources/settings.xml", encoding="utf-8") as handle:
        xml = handle.read()
    with open("resources/language/resource.language.en_gb/strings.po", encoding="utf-8") as handle:
        po = handle.read()
    strings = {int(k): v.replace('\\"', '"')
               for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', po)}
    labels = {}
    for match in re.finditer(r'<setting id="(\w+)" type="\w+" label="(\d+)"(.*?)</setting>',
                             xml, re.DOTALL):
        body = match.group(3)
        # Level 4 is internal: Kodi never shows it.
        if "<level>4</level>" in body:
            continue
        labels[match.group(1)] = strings[int(match.group(2))]
    return labels


def test_settings_page_covers_every_setting():
    with open(DOCS + "settings.md", encoding="utf-8") as handle:
        page = handle.read()
    for setting, label in _settings_labels().items():
        assert f"**{label}**" in page, f"{setting}: **{label}** is not on settings.md"


def test_settings_page_names_no_setting_that_does_not_exist():
    with open(DOCS + "settings.md", encoding="utf-8") as handle:
        page = handle.read()
    labels = set(_settings_labels().values())
    # Every setting is introduced as a definition term: "**Label**" at the
    # start of a line.
    for term in re.findall(r"^\*\*(.+?)\*\*", page, re.MULTILINE):
        assert term in labels, f"settings.md describes {term!r}, which is not a setting"

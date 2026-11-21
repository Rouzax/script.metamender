"""resources/settings.xml keeps only the settings that apply to the whole add-on."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from resources.lib.data.upgrade import MOVED_IDS

GENERAL_SITE_TOGGLES = {"enable_fsk", "enable_bbfc", "enable_medieraadet"}


def _settings() -> dict[str, ET.Element]:
    root = ET.parse("resources/settings.xml").getroot()
    return {node.get("id", ""): node for node in root.iter("setting")}


def test_settings_xml_has_no_moved_ids():
    present = set(_settings()) & set(MOVED_IDS)
    assert not present, f"per-library choices still in Kodi's settings: {sorted(present)}"


def test_libraries_action_opens_the_library_list():
    node = _settings()["configure_sources"]
    assert node.get("type") == "action"
    assert (node.findtext("data") or "").strip() == "RunScript(script.metamender,libraries)"


def test_site_toggles_stay_general_and_stand_alone():
    settings = _settings()
    assert GENERAL_SITE_TOGGLES <= set(settings)
    for site in GENERAL_SITE_TOGGLES:
        # They once depended on a per-library toggle that no longer exists.
        assert settings[site].find("dependencies") is None


def test_no_category_or_group_is_empty():
    root = ET.parse("resources/settings.xml").getroot()
    for group in root.iter("group"):
        assert group.find("setting") is not None, group.get("id")
    for category in root.iter("category"):
        assert category.find("group/setting") is not None, category.get("id")

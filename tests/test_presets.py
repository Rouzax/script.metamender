"""Tests for the bundled certification presets and the country setting."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # the repo's own settings.xml
from pathlib import Path
from unittest.mock import patch

from resources.lib.constants import COUNTRY_CODES
from resources.lib.data import presets
from resources.lib.data.presets import country_for_setting, load_preset


def test_every_country_has_a_valid_preset():
    for code in COUNTRY_CODES:
        p = load_preset(code)
        assert p is not None and p.country == code and p.valid
        for table in p.mappings.values():
            assert set(table.values()) <= p.valid


def test_nl_preset_values():
    p = load_preset("NL")
    assert p is not None
    assert p.prefix == "NL:" and p.inference[:3] == ("BE", "DE", "AT")
    assert p.mappings["GB"]["15"] == "16"
    assert "NL" not in p.mappings


def test_us_prefix_keeps_its_space():
    p = load_preset("US")
    assert p is not None and p.prefix == "Rated "


def _broken(tmp_path, monkeypatch, body):
    (tmp_path / "NL.json").write_text(body, encoding="utf-8")
    monkeypatch.setattr(presets, "_PRESET_DIR", str(tmp_path))


def test_unparseable_preset_fails_closed(tmp_path, monkeypatch):
    _broken(tmp_path, monkeypatch, "{not json")
    with patch.object(presets, "log") as log:
        assert load_preset("NL") is None
    assert log.exception.call_args.kwargs["event"] == "presets.invalid"


def test_mapping_outside_the_list_fails_closed(tmp_path, monkeypatch):
    good = json.loads(Path("resources/presets/NL.json").read_text(encoding="utf-8"))
    good["mappings"]["GB"]["15"] = "15"
    _broken(tmp_path, monkeypatch, json.dumps(good))
    assert load_preset("NL") is None


def test_missing_preset_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(presets, "_PRESET_DIR", str(tmp_path))
    assert load_preset("NL") is None


def test_unknown_country_is_none():
    assert load_preset("ES") is None
    assert load_preset("../NL") is None


def test_country_setting_order_matches_codes():
    root = ET.parse("resources/settings.xml").getroot()
    country = next(s for s in root.iter("setting") if s.get("id") == "country")
    po = Path("resources/language/resource.language.en_gb/strings.po").read_text(encoding="utf-8")
    labels = dict(re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', po))
    options = {int(o.text): labels[o.get("label")] for o in country.iter("option")}
    assert options[0].startswith("Automatic")
    for value, code in enumerate(COUNTRY_CODES, start=1):
        assert options[value].startswith(code + " ")
    assert country.findtext("default") == "0"


def test_country_for_setting():
    assert country_for_setting(0, "nl-NL") == "NL"
    assert country_for_setting(0, "en-gb") == "GB"
    assert country_for_setting(0, "es-ES") is None
    assert country_for_setting(0, "nl") is None
    assert country_for_setting(3, "en-US") == "DE"
    assert country_for_setting(10, "en-US") is None

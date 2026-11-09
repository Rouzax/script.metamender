"""Provenance rule for writing onto NFO-managed items."""
import hashlib

from resources.lib.engine.protect import fingerprint, may_write


def test_fingerprint_stable_and_sensitive():
    assert fingerprint("a") == fingerprint("a")
    assert fingerprint("a") != fingerprint("b")
    assert fingerprint(["x", "y"]) != fingerprint(["y", "x"])
    assert len(fingerprint("a")) == 40


def test_fingerprint_is_sha1_of_compact_utf8_json():
    expected = hashlib.sha1('["caf\u00e9"]'.encode("utf-8")).hexdigest()
    assert fingerprint(["café"]) == expected


def test_fingerprint_dict_key_order_irrelevant():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})


def test_fingerprint_float_round_trip():
    assert fingerprint(7.28) == fingerprint(7.28000020980835)
    assert fingerprint(7.28) != fingerprint(7.3)


def test_unmanaged_always_writable():
    assert may_write(False, "NFO text", None)
    assert may_write(False, "NFO text", fingerprint("other"))


def test_nfo_empty_writable():
    assert may_write(True, "", None)
    assert may_write(True, None, None)
    assert may_write(True, [], None)
    assert may_write(True, "  ", None)


def test_nfo_placeholder_writable():
    assert may_write(True, "-", None)
    assert may_write(True, "Episode 3", None)


def test_nfo_value_we_wrote_writable():
    assert may_write(True, "our plot", fingerprint("our plot"))
    assert may_write(True, ["Drama"], fingerprint(["Drama"]))


def test_nfo_value_changed_by_nfo_or_user_protected():
    assert not may_write(True, "their plot", None)
    assert not may_write(True, "their plot", fingerprint("our plot"))
    assert not may_write(True, ["Drama"], fingerprint(["Comedy"]))

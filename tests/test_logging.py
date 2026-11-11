"""Where the debug log goes: the per-scan file, or the activity log between scans."""
from __future__ import annotations

import os

import pytest

from resources.lib import utils
from resources.lib.utils import StructuredLogger


@pytest.fixture
def vfs(kodi_stubs, monkeypatch):
    # utils binds xbmcvfs at import, so the tmp_path-backed stub is set there.
    monkeypatch.setattr(utils, "xbmcvfs", kodi_stubs["xbmcvfs"])


@pytest.fixture
def log_dir(vfs, tmp_path):
    StructuredLogger.reconfigure(debug_enabled=True)
    yield os.path.join(str(tmp_path), "script.metamender", "logs")
    StructuredLogger.shutdown()


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_debug_outside_a_scan_goes_to_the_activity_log(log_dir):
    StructuredLogger("setup").debug("Wizard opened", step="key")
    text = _read(os.path.join(log_dir, "metamender.log"))
    assert "Wizard opened" in text and "step=key" in text and "[DEBUG]" in text


def test_info_outside_a_scan_also_reaches_the_activity_log(log_dir):
    StructuredLogger("setup").info("Setup saved", event="setup.finish")
    assert "Setup saved" in _read(os.path.join(log_dir, "metamender.log"))


def test_a_scan_log_takes_the_lines_while_it_is_open(log_dir):
    path = StructuredLogger.start_scan_log()
    StructuredLogger("scan").debug("Inside the scan")
    StructuredLogger.stop_scan_log()
    assert path and "Inside the scan" in _read(path)
    assert not os.path.exists(os.path.join(log_dir, "metamender.log"))


def test_nothing_is_written_with_debug_logging_off(vfs, tmp_path):
    StructuredLogger.reconfigure(debug_enabled=False)
    StructuredLogger("setup").debug("Hidden")
    StructuredLogger.shutdown()
    assert not os.path.exists(os.path.join(str(tmp_path), "script.metamender", "logs"))


def test_activity_log_rotates_at_its_size_limit(log_dir):
    active = os.path.join(log_dir, "metamender.log")
    with open(active, "w", encoding="utf-8") as handle:
        handle.write("x" * (600 * 1024))
    StructuredLogger("setup").debug("After rotation")
    assert "After rotation" in _read(active)
    assert os.path.getsize(os.path.join(log_dir, "metamender.1.log")) == 600 * 1024


def test_old_rotations_beyond_three_are_dropped(log_dir):
    for n in (1, 2, 3):
        with open(os.path.join(log_dir, "metamender.{}.log".format(n)), "w") as handle:
            handle.write(str(n))
    with open(os.path.join(log_dir, "metamender.log"), "w") as handle:
        handle.write("x" * (600 * 1024))
    StructuredLogger("setup").debug("Newest")
    names = sorted(os.listdir(log_dir))
    assert names == ["metamender.1.log", "metamender.2.log", "metamender.3.log",
                     "metamender.log"]
    assert _read(os.path.join(log_dir, "metamender.3.log")) == "2"


def test_scan_log_cleanup_keeps_the_activity_log(log_dir):
    for day in range(1, 13):
        name = "metamender_2026-10-{:02d}_120000.log".format(day)
        with open(os.path.join(log_dir, name), "w") as handle:
            handle.write("old")
    for name in ("metamender.log", "metamender.1.log"):
        with open(os.path.join(log_dir, name), "w") as handle:
            handle.write("activity")
    StructuredLogger.start_scan_log()
    StructuredLogger.stop_scan_log()
    names = os.listdir(log_dir)
    assert "metamender.log" in names and "metamender.1.log" in names
    assert len([n for n in names if n.startswith("metamender_")]) == 10

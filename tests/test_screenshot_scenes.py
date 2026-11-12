"""Every window has a screenshot scene, every scene has a committed screenshot,
and the demo data builds the rows the scenes focus.

The capture itself needs a running Kodi (docker/scripts/screenshots.py);
these checks keep a new window or a new scene from landing without one.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "resources" / "skins" / "Default" / "1080i"
HELPER = ROOT / "docker" / "scripts" / "screenshot_helper"
RUNNER = ROOT / "docker" / "scripts" / "screenshots.py"
SCENES = json.loads((HELPER / "scenes.json").read_text(encoding="utf-8"))
SHOTS = ROOT / "docs" / "screenshots"

GROUPS = {"setup", "update-now", "attention", "details", "library"}
KINDS = {"setup", "details", "attention", "progress", "corner-bar", "context"}


def test_every_window_has_a_scene():
    windows = {path.name for path in WINDOWS.glob("*.xml")}
    assert windows
    assert windows <= {scene["window"] for scene in SCENES.values()}


def test_every_scene_names_a_shipped_window_or_a_native_one():
    for name, scene in SCENES.items():
        if scene["window"] == "native":
            # Kodi's own dialog: the runner needs its window name and a crop.
            assert scene.get("active") and scene.get("crop"), name
        else:
            assert (WINDOWS / scene["window"]).is_file(), name


def test_every_scene_has_a_known_group_and_kind():
    for name, scene in SCENES.items():
        assert scene.get("group") in GROUPS, name
        assert scene.get("kind") in KINDS, name


def test_scene_names_are_file_and_argument_safe():
    """A name becomes a file name and a RunScript argument, which Kodi splits on commas."""
    for name in SCENES:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name), name


def test_every_scene_has_a_committed_screenshot():
    for name, scene in SCENES.items():
        path = SHOTS / scene["group"] / f"{name}.png"
        assert path.is_file(), f"run docker/scripts/screenshots.py --only {name}"


def test_no_screenshot_is_left_outside_a_group_folder():
    assert sorted(p.name for p in SHOTS.glob("*.png")) == []


def test_setup_scenes_name_real_steps():
    from resources.lib.ui.setup_model import SetupFlow, SetupState
    state = SetupState(features={"enable_certifications": True})
    steps = set(SetupFlow("wizard", "en-US").steps(state))
    for name, scene in SCENES.items():
        if scene["kind"] == "setup":
            assert scene["step"] in steps, name


@pytest.fixture
def demo(monkeypatch):
    from resources.lib.ui import details_model
    po = (ROOT / "resources/language/resource.language.en_gb/strings.po").read_text()
    strings = {int(k): v for k, v in re.findall(r'msgctxt "#(\d+)"\nmsgid "(.*)"', po)}
    monkeypatch.setattr(details_model, "lang", lambda i: strings.get(i, ""))
    monkeypatch.syspath_prepend(str(HELPER))
    sys.modules.pop("demo", None)
    import demo as module
    return module


def test_details_scenes_focus_rows_their_demo_item_has(demo):
    from resources.lib.ui.details_model import build_rows
    for name, scene in SCENES.items():
        if scene["kind"] != "details":
            continue
        media_type, item, item_row, fs_map, keep, language = demo.PRESETS[scene["preset"]]()
        rows = build_rows(media_type, item, item_row, fs_map, keep, demo.TODAY,
                          language=language)
        focused = [r for r in rows if r.key == scene["focus"]]
        assert focused, name
        if scene.get("button"):
            assert focused[0].action, f"{name}: the focused row has no button"


def test_demo_rows_cover_every_group(demo):
    from resources.lib.ui.details_model import build_rows
    groups = set()
    for preset in demo.PRESETS.values():
        media_type, item, item_row, fs_map, keep, language = preset()
        groups |= {r.group for r in build_rows(media_type, item, item_row, fs_map, keep,
                                               demo.TODAY, language=language)}
    assert {"updated", "left", "waiting"} <= groups


def test_runner_guards_the_library_before_the_first_scene():
    """The service never scans while setup is incomplete; the runner refuses
    to run when it is complete, before it installs anything."""
    source = RUNNER.read_text(encoding="utf-8")
    main = source[source.index("def main("):]
    guard = main.index("refuse_if_setup_complete(")
    version = main.index("refuse_if_profile_not_omega(")
    assert version < main.index("install(")
    loop = main.index("for name, scene in chosen.items()")
    assert guard < loop
    assert "finally:" in main[loop:]


@pytest.fixture
def runner(monkeypatch, tmp_path):
    """screenshots.py with its Kodi profile in tmp_path and no real Kodi."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("screenshots_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "data"
    (data / "addons").mkdir(parents=True)
    monkeypatch.setattr(module, "KODI_DATA", data)
    monkeypatch.setattr(module, "ADDON_DATA", data / "userdata" / "addon_data" / module.ADDON_ID)
    calls = []

    def rpc(url, method, params=None):
        calls.append((method, params))
        raise OSError("no Kodi")

    monkeypatch.setattr(module, "rpc", rpc)
    monkeypatch.setattr(module, "alive", lambda url: False)
    monkeypatch.setattr(module, "_install_imdbratings", lambda addons: True)
    module.calls = calls
    return module


def test_a_failed_install_is_cleaned_up(runner, monkeypatch):
    """Kodi never coming back must not leave this checkout in the profile."""
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(runner, "must", lambda condition, what, timeout=60: sys.exit(what))
    added: dict = {}
    with pytest.raises(SystemExit):
        runner.install("url", ":20", added)
    link = runner.KODI_DATA / "addons" / runner.ADDON_ID
    assert link.is_symlink() and added["addon"]
    runner.uninstall("url", added)
    assert not link.is_symlink()
    assert not (runner.KODI_DATA / "addons" / runner.HELPER_ID).exists()


def test_cleanup_leaves_an_existing_install_enabled(runner):
    added = {"addon": False, "imdbratings": False, "addon_data": False}
    runner.uninstall("url", added)
    disabled = [p["addonid"] for m, p in runner.calls if m == "Addons.SetAddonEnabled"]
    assert runner.ADDON_ID not in disabled
    assert runner.HELPER_ID in disabled

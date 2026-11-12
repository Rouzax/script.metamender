#!/usr/bin/env python3
"""Retake the docs screenshots from the Flatpak Kodi on an X display.

Installs this checkout and a development-only helper add-on
(screenshot_helper/) into the Flatpak Kodi's Omega profile, restarts Kodi,
then opens each scene in screenshot_helper/scenes.json with demo data and
captures it, cropped to the window (the panel group in the skin XML, plus a
margin) or to the scene's own box for Kodi's dialogs.

    screenshots.py                 every scene, staged under build/screenshots/
    screenshots.py --only a,b      only these scenes
    screenshots.py --apply         also copy the staged images to docs/screenshots/
    screenshots.py --no-restart    Kodi already runs this checkout and the helper
    screenshots.py --show TITLE    the library show for the context menu scene

Safe for the shared library: MetaMender is installed with setup not
finished, and the service neither scans nor writes until setup is done. The
script refuses to run when the profile says setup is done. Afterwards it
removes what it installed (unless --keep) and puts Kodi's debug overlay back.

Needs ImageMagick (import, convert) and Estuary as the active skin, so the
images share one look.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "docker" / "scripts" / "screenshot_helper"
HELPER_ID = "script.metamender.screenshots"
ADDON_ID = "script.metamender"
IMDB_ID = "script.module.imdbratings"
IMDB_ZIP = ("https://mirrors.kodi.tv/addons/omega/script.module.imdbratings/"
            "script.module.imdbratings-1.0.0.zip")
WINDOWS = ROOT / "resources" / "skins" / "Default" / "1080i"
STAGE = ROOT / "build" / "screenshots"
OUT = ROOT / "docs" / "screenshots"
KODI_DATA = Path.home() / ".var" / "app" / "tv.kodi.Kodi" / "data"
ADDON_DATA = KODI_DATA / "userdata" / "addon_data" / ADDON_ID
CLOSE_FLAG = KODI_DATA / "temp" / "metamender-screenshots.close"
# Space kept around a window's panel, so its rounded corners and frame show.
MARGIN = 24
SHOW_LOG_INFO = "debug.showloginfo"


def rpc(url: str, method: str, params: dict | None = None) -> object:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        reply = json.load(response)
    if "error" in reply:
        raise RuntimeError(f"{method}: {reply['error']}")
    return reply.get("result")


def alive(url: str) -> bool:
    try:
        return rpc(url, "JSONRPC.Ping") == "pong"
    except OSError:
        return False


def wait_for(condition, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.4)
    return False


def must(condition, what: str, timeout: float = 60) -> None:
    if not wait_for(condition, timeout):
        sys.exit(f"timed out waiting for {what}")


def boolean(url: str, condition: str) -> bool:
    result = rpc(url, "XBMC.GetInfoBooleans", {"booleans": [condition]})
    return isinstance(result, dict) and bool(result.get(condition))


def label(url: str, info: str) -> str:
    result = rpc(url, "XBMC.GetInfoLabels", {"labels": [info]})
    return str(result.get(info) or "") if isinstance(result, dict) else ""


def refuse_if_setup_complete() -> None:
    """With setup done the service would scan the shared library on start."""
    settings = ADDON_DATA / "settings.xml"
    if settings.is_file():
        root = ElementTree.parse(settings).getroot()
        for node in root.iter("setting"):
            if node.get("id") == "setup_complete" and (node.text or "").strip() == "true":
                sys.exit(f"{settings} says setup is complete: the service would scan the "
                         "library. Move that folder aside first.")


# Kodi 21 Omega writes guisettings.xml as version 2; Kodi 22 upgrades it, and
# Omega cannot read the upgraded file: it falls back to default settings,
# which turns the web server off and loses every other GUI setting.
OMEGA_SETTINGS_VERSION = "2"


def refuse_if_profile_not_omega() -> None:
    """The Omega profile must only ever be opened by Omega (docker/README.md)."""
    settings = KODI_DATA / "userdata" / "guisettings.xml"
    version = ElementTree.parse(settings).getroot().get("version")
    if version != OMEGA_SETTINGS_VERSION:
        sys.exit(f"{settings} is version {version}, not Omega's {OMEGA_SETTINGS_VERSION}: "
                 "another Kodi version has opened this profile. Starting Omega on it "
                 "would reset Kodi's settings; restore the profile first.")


def _install_imdbratings(addons: Path) -> bool:
    """Unpack the IMDb ratings module from the official Kodi mirror; True when
    this run installed it."""
    if (addons / IMDB_ID).exists():
        return False
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "imdbratings.zip"
        urllib.request.urlretrieve(IMDB_ZIP, archive)
        unpacked = Path(tmp) / "unpacked"
        with zipfile.ZipFile(archive) as zf:
            if any(not n.startswith(IMDB_ID + "/") for n in zf.namelist()):
                sys.exit(f"{IMDB_ZIP} holds files outside {IMDB_ID}/")
            zf.extractall(unpacked)
        shutil.move(str(unpacked / IMDB_ID), str(addons / IMDB_ID))
    return True


def install(url: str, display: str, added: dict[str, bool]) -> None:
    """Put this checkout, the IMDb module and the helper into the profile and
    restart Kodi. Records in ``added`` what this run added as it goes, so
    uninstall() can remove it even when a later step fails."""
    addons = KODI_DATA / "addons"
    added["addon_data"] = not ADDON_DATA.exists()
    link = addons / ADDON_ID
    if link.is_symlink() and link.resolve() != ROOT:
        sys.exit(f"{link} points somewhere else")
    if link.exists() and not link.is_symlink():
        sys.exit(f"{link} is an installed copy, not this checkout; remove it first")
    added["addon"] = not link.is_symlink()
    if added["addon"]:
        link.symlink_to(ROOT)
    added["imdbratings"] = _install_imdbratings(addons)
    target = addons / HELPER_ID
    shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(HELPER, target, ignore=shutil.ignore_patterns("__pycache__"))
    if alive(url):
        rpc(url, "Application.Quit")
        must(lambda: not alive(url), "Kodi to quit")
        time.sleep(3)
    subprocess.Popen(
        ["flatpak", "run", "--system", "--branch=stable", "tv.kodi.Kodi"],
        env={**os.environ, "DISPLAY": display},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    must(lambda: alive(url), "Kodi to start", timeout=120)
    time.sleep(8)  # the home screen is still building when the web server answers
    for addon in (IMDB_ID, ADDON_ID, HELPER_ID):
        rpc(url, "Addons.SetAddonEnabled", {"addonid": addon, "enabled": True})
    # The service asks for setup once per profile; dismiss that window.
    if wait_for(lambda: boolean(url, "Window.IsActive(script-metamender-attention.xml)"),
                timeout=20):
        rpc(url, "Input.Back")


def uninstall(url: str, added: dict[str, bool]) -> None:
    """Remove what this run added, also when Kodi is gone. An install of
    MetaMender that was there before the run stays enabled."""
    for addon in (HELPER_ID, ADDON_ID) if added.get("addon") else (HELPER_ID,):
        try:
            rpc(url, "Addons.SetAddonEnabled", {"addonid": addon, "enabled": False})
        except (OSError, RuntimeError):
            pass
    addons = KODI_DATA / "addons"
    shutil.rmtree(addons / HELPER_ID, ignore_errors=True)
    if added.get("addon"):
        (addons / ADDON_ID).unlink(missing_ok=True)
    if added.get("imdbratings"):
        shutil.rmtree(addons / IMDB_ID, ignore_errors=True)
    if added.get("addon_data"):
        shutil.rmtree(ADDON_DATA, ignore_errors=True)


def crop_box(scene: dict) -> tuple[int, int, int, int] | None:
    """(left, top, width, height) on the 1920x1080 screen; None for all of it."""
    if scene["window"] == "native":
        box = scene["crop"]
        return None if box == "full" else tuple(box)  # type: ignore[return-value]
    group = ElementTree.parse(WINDOWS / scene["window"]).getroot().find(
        "controls/control[@type='group']")
    if group is None:
        sys.exit(f"{scene['window']}: no panel group to crop to")
    left, top, width, height = (int(group.findtext(k) or 0)
                                for k in ("left", "top", "width", "height"))
    return left - MARGIN, top - MARGIN, width + 2 * MARGIN, height + 2 * MARGIN


def active_name(scene: dict) -> str:
    return scene.get("active") or scene["window"]


def open_context_menu(url: str, show_title: str | None) -> None:
    """The MetaMender submenu on the first episode of a library show."""
    result = rpc(url, "VideoLibrary.GetTVShows", {"properties": ["title", "episode"]})
    shows = [s for s in (result.get("tvshows") or []) if s.get("episode")] \
        if isinstance(result, dict) else []
    if show_title:
        shows = [s for s in shows if s["title"] == show_title]
    if not shows:
        sys.exit("no library show with episodes" + (f" titled {show_title!r}" if show_title else ""))
    show = random.choice(shows)
    rpc(url, "GUI.ActivateWindow", {"window": "videos", "parameters": [
        f"videodb://tvshows/titles/{show['tvshowid']}/-1/"]})
    must(lambda: boolean(url, "Window.IsActive(videos)")
         and label(url, "Container.NumItems") not in ("", "0"), "the episode list", 20)
    time.sleep(1)
    if label(url, "ListItem.Label") == "..":
        rpc(url, "Input.Down")
        time.sleep(0.4)
    rpc(url, "Input.ContextMenu")
    must(lambda: boolean(url, "Window.IsActive(contextmenu)"), "the context menu", 10)
    for _ in range(30):
        if label(url, "System.CurrentControl").strip() == "MetaMender":
            break
        rpc(url, "Input.Down")
        time.sleep(0.3)
    else:
        sys.exit("the context menu has no MetaMender entry")
    rpc(url, "Input.Select")
    time.sleep(1)
    print(f"context menu on: {show['title']}")


def close_context_menu(url: str) -> None:
    for _ in range(3):
        if not boolean(url, "Window.IsActive(contextmenu)"):
            break
        rpc(url, "Input.Back")
        time.sleep(0.5)
    rpc(url, "GUI.ActivateWindow", {"window": "home"})


def capture(url: str, display: str, name: str, scene: dict, show: str | None) -> Path:
    if scene["kind"] == "context":
        open_context_menu(url, show)
    else:
        CLOSE_FLAG.unlink(missing_ok=True)
        rpc(url, "Addons.ExecuteAddon", {"addonid": HELPER_ID, "params": [name]})
        must(lambda: boolean(url, f"Window.IsActive({active_name(scene)})"),
             f"{name} to open", 20)
    time.sleep(1)  # the open animation
    must(lambda: not boolean(url, "Window.IsVisible(notification)"),
         "notifications to clear", 30)
    time.sleep(1.5)  # focus animations, so a re-take gives an identical file
    folder = STAGE / scene["group"]
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / f".{name}.raw.png"
    out = folder / f"{name}.png"
    subprocess.run(["import", "-display", display, "-window", "root", str(raw)], check=True)
    box = crop_box(scene)
    crop = ["-crop", "{2}x{3}+{0}+{1}".format(*box), "+repage"] if box else []
    # -strip drops ImageMagick's timestamps, so an unchanged window gives an
    # unchanged file and git shows only real differences.
    subprocess.run(["convert", str(raw), *crop, "-strip", str(out)], check=True)
    raw.unlink()
    if scene["kind"] == "context":
        close_context_menu(url)
    else:
        CLOSE_FLAG.touch()
        must(lambda: not boolean(url, f"Window.IsActive({active_name(scene)})"),
             f"{name} to close", 15)
    time.sleep(0.5)
    return out


def apply(names: list[str], scenes: dict) -> None:
    for name in names:
        group = scenes[name]["group"]
        (OUT / group).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STAGE / group / f"{name}.png", OUT / group / f"{name}.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="comma-separated scene names (default: all)")
    parser.add_argument("--apply", action="store_true",
                        help="copy the staged images over docs/screenshots/")
    parser.add_argument("--display", default=":20")
    parser.add_argument("--rpc", default="http://127.0.0.1:8100/jsonrpc")
    parser.add_argument("--no-restart", action="store_true", help="skip install and restart")
    parser.add_argument("--keep", action="store_true",
                        help="leave MetaMender and the helper installed afterwards")
    parser.add_argument("--show", help="library show title for the context menu scene")
    args = parser.parse_args()

    scenes = json.loads((HELPER / "scenes.json").read_text(encoding="utf-8"))
    names = args.only.split(",") if args.only else list(scenes)
    unknown = set(names) - set(scenes)
    if unknown:
        sys.exit(f"unknown scenes: {', '.join(sorted(unknown))}")
    chosen = {name: scenes[name] for name in names}

    refuse_if_setup_complete()
    if not args.no_restart:
        refuse_if_profile_not_omega()
    added: dict[str, bool] = {}
    overlay_was_on = False
    try:
        if not args.no_restart:
            install(args.rpc, args.display, added)
        skin = rpc(args.rpc, "Settings.GetSettingValue", {"setting": "lookandfeel.skin"})
        if not isinstance(skin, dict) or skin.get("value") != "skin.estuary":
            sys.exit("switch Kodi to Estuary first: the screenshots are all taken in it")
        overlay = rpc(args.rpc, "Settings.GetSettingValue", {"setting": SHOW_LOG_INFO})
        overlay_was_on = isinstance(overlay, dict) and overlay.get("value") is True
        rpc(args.rpc, "Settings.SetSettingValue", {"setting": SHOW_LOG_INFO, "value": False})
        for name, scene in chosen.items():
            print(capture(args.rpc, args.display, name, scene, args.show).relative_to(ROOT))
        if args.apply:
            apply(names, scenes)
            print(f"copied {len(names)} images to {OUT.relative_to(ROOT)}")
    finally:
        if overlay_was_on and alive(args.rpc):
            rpc(args.rpc, "Settings.SetSettingValue",
                {"setting": SHOW_LOG_INFO, "value": True})
        # With --no-restart nothing was installed by this run. The files go
        # whether or not Kodi still answers.
        if added and not args.keep:
            uninstall(args.rpc, added)


if __name__ == "__main__":
    main()

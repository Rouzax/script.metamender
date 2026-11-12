"""Open one MetaMender window with demo data, for docker/scripts/screenshots.py.

Development only, never shipped. It runs inside Kodi as its own add-on and
imports the installed MetaMender's window classes, so a screenshot shows the
real window with the real strings. Nothing is written: no setting, no
tracker row, no library field.

    RunScript(script.metamender.screenshots,<scene name>)

The window stays open until the capture script drops the close flag in
special://temp (or a minute passes), then closes.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = xbmcaddon.Addon("script.metamender")
sys.path.insert(0, TARGET.getAddonInfo("path"))
sys.path.insert(0, HERE)

from resources.lib import utils

# Inside this helper xbmcaddon.Addon() is the helper itself, which has none
# of MetaMender's strings; every lang() and setting read goes to MetaMender.
utils._addon = TARGET

import demo
from resources.lib.service import attention
from resources.lib.service.details import DetailsView, _heading
from resources.lib.service.refresh import RefreshProgress
from resources.lib.ui import theme
from resources.lib.ui import details_window as dw
from resources.lib.ui import setup_window as sw
from resources.lib.ui.attention_window import show_attention_window
from resources.lib.ui.details_model import build_rows, header_line
from resources.lib.ui.progress_window import show_progress_window
from resources.lib.providers.tmdb import KEY_OK
from resources.lib.ui.setup_model import FEATURES, initial_state
from resources.lib.utils import lang

CLOSE_FLAG = "special://temp/metamender-screenshots.close"
GIVE_UP_SECONDS = 60

DEMO_FOLDERS = [
    {"path": "smb://nas/media/Movies/", "label": "Movies"},
    {"path": "smb://nas/media/TV Shows/", "label": "TV Shows"},
    {"path": "smb://nas/media/Kids/TV/", "label": "Kids TV"},
]
DEMO_CONFIG = {
    "default_language": "en-US",
    "sources": [{"path": "smb://nas/media/Kids/TV/", "language": "nl-NL",
                 "fallback_language": "en-US"}],
}
# Shaped like a TMDB key, but not one.
DEMO_KEY = "demo0key0for0screenshots0only00"


def _close_when_flagged(close) -> None:
    """Wait for the capture script's flag, then close the window."""
    deadline = time.monotonic() + GIVE_UP_SECONDS
    while time.monotonic() < deadline:
        if xbmcvfs.exists(CLOSE_FLAG):
            xbmcvfs.delete(CLOSE_FLAG)
            break
        time.sleep(0.3)
    close()


def _watch(close) -> None:
    threading.Thread(target=_close_when_flagged, args=(close,), daemon=True).start()


def _close_dialogs() -> None:
    xbmc.executebuiltin("Dialog.Close(all,true)")


def setup(scene: dict) -> None:
    settings: dict[str, Any] = {f: f in ("enable_ratings", "enable_plots", "enable_episode_titles",
                         "enable_certifications") for f in FEATURES}
    settings["tmdb_api_key"] = DEMO_KEY
    settings["country"] = 0
    state = initial_state(settings, DEMO_CONFIG, DEMO_FOLDERS, "en-US")
    controller = sw.SetupController(
        "wizard", state, DEMO_CONFIG, DEMO_FOLDERS, "en-US", "", first_run=True,
        save_config=lambda path, config: True, settings_writer=lambda values: None,
        key_checker=lambda key: KEY_OK,
    )
    controller.flow.current = scene["step"]
    window = sw.SetupWindow(sw._XML, TARGET.getAddonInfo("path"), "Default", "1080i")
    window.controller = controller
    _watch(window.close)
    window.doModal()
    del window


class _DemoSession:
    """The window's buttons do nothing: a screenshot changes no item."""

    def __init__(self, view: DetailsView) -> None:
        self.view = view

    def reload(self) -> DetailsView:
        return self.view

    def update_now(self) -> None:
        pass

    def run_action(self, row) -> None:
        pass


class _FocusedDetails(dw.DetailsWindow):
    """Opens on the scene's row, with the focus on its button if asked."""

    focus_key: str | None = None
    focus_button: bool = False

    def onInit(self) -> None:
        self._fill(self.focus_key)
        self.setFocusId(dw.ROW_ACTION if self.focus_button else dw.ROW_LIST)


def details(scene: dict) -> None:
    media_type, item, item_row, fs_map, keep, language = demo.PRESETS[scene["preset"]]()
    rows = build_rows(media_type, item, item_row, fs_map, keep, demo.TODAY,
                      language=language)
    view = DetailsView(_heading(media_type, item),
                       header_line(item_row, fs_map, language), rows)
    window = _FocusedDetails(dw._XML, TARGET.getAddonInfo("path"), "Default", "1080i")
    window.session, window.view = _DemoSession(view), view
    window.focus_key = scene.get("focus")
    window.focus_button = bool(scene.get("button"))
    props = dw.header_properties(view.heading, view.sub)
    focused = next((r for r in rows if r.key == window.focus_key), None)
    entries = dw.list_entries(rows)
    first = focused or entries[dw.first_field_position(entries)].row
    if first is not None:
        props.update(dw.details_properties([first], 0))
    for key, value in props.items():
        window.setProperty(key, value)
    _watch(window.close)
    window.doModal()
    del window


def attention_window(scene: dict) -> None:
    main = attention.Problem(scene["main"], scene.get("main_detail", ""))
    others = [attention.Problem(kind, detail) for kind, detail in scene.get("others", [])]
    _watch(_close_dialogs)
    show_attention_window(main, others)


def _demo_progress() -> RefreshProgress:
    progress = RefreshProgress()
    progress.begin(24, time.monotonic() - 95)
    progress.done, progress.updated, progress.unchanged, progress.unchecked = 9, 6, 2, 1
    progress.item_started("Season 1, episode 10")
    return progress


def progress(scene: dict) -> None:
    done = threading.Event()
    _watch(_close_dialogs)
    show_progress_window(_demo_progress(), lang(32485).format(scene["title"]), done)


def corner_bar(scene: dict) -> None:
    state = _demo_progress()
    heading = lang(32485).format(scene["title"])
    bar = xbmcgui.DialogProgressBG()
    bar.create(heading, state.bar_text())
    bar.update(state.percent(), heading, state.bar_text())
    closed = threading.Event()
    _watch(closed.set)
    closed.wait(GIVE_UP_SECONDS + 5)
    bar.close()


KINDS = {"setup": setup, "details": details, "attention": attention_window,
         "progress": progress, "corner-bar": corner_bar}


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(os.path.join(HERE, "scenes.json"), encoding="utf-8") as handle:
        scenes = json.load(handle)
    scene = scenes[name]
    # The theme setting is read through this module; the scene picks one
    # without touching the user's setting.
    chosen = int(scene.get("theme", 0))
    theme.get_int_setting = lambda key, default=0: chosen if key == "theme" else default
    KINDS[scene["kind"]](scene)


main()

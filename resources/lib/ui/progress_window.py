"""The refresh progress window: what a show or season refresh shows while it
runs, with Stop and Background. Same family style as the setup window.

Logging:
    Logger: 'progress'
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, cast

import xbmcgui

from resources.lib.ui.setup_window import GOLDEN_HOUR
from resources.lib.utils import get_addon, lang

if TYPE_CHECKING:
    # Type only: refresh.py imports this module to show the window.
    from resources.lib.service.refresh import RefreshProgress

_XML = "script-metamender-progress.xml"
_BAR = 40
STOP = 31
BACKGROUND = 32
_ACTION_PREVIOUS_MENU = 10
_ACTION_NAV_BACK = 92
# How often the open window copies the progress in.
_REFRESH_SECONDS = 0.5


def _time_left(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return lang(32476)
    return lang(32475).format((seconds + 59) // 60)


def progress_properties(progress: RefreshProgress, heading: str, now: float) -> dict[str, str]:
    """Every window property the progress XML reads."""
    props = {
        "MM.Heading": heading,
        "MM.Current": progress.current,
        "MM.Count": progress.bar_text(),
        "MM.TimeLeft": _time_left(progress.seconds_left(now)),
        "MM.Percent": str(progress.percent()),
        "MM.Updated": lang(32477).format(progress.updated),
        "MM.Unchanged": lang(32478).format(progress.unchanged),
        "MM.Unchecked": lang(32479).format(progress.unchecked),
        "MM.Stop": lang(32480),
        "MM.Background": lang(32481),
    }
    props.update(GOLDEN_HOUR)
    return props


class ProgressWindow(xbmcgui.WindowXMLDialog):
    """Shows a running refresh. ``result`` is "stop", "background", or
    "done" when the refresh finished while the window was open. The
    attributes are set before doModal()."""

    progress: RefreshProgress
    heading: str
    done: threading.Event
    result: str = "background"
    _open: bool = False

    def onInit(self) -> None:
        self._open = True
        self._render()
        self.setFocusId(BACKGROUND)
        threading.Thread(target=self._follow, daemon=True).start()

    def _render(self) -> None:
        props = progress_properties(self.progress, self.heading, time.monotonic())
        for key, value in props.items():
            self.setProperty(key, value)
        try:
            cast(xbmcgui.ControlProgress, self.getControl(_BAR)).setPercent(
                float(props["MM.Percent"]))
        except RuntimeError:
            pass

    def _follow(self) -> None:
        # Kodi lets a script thread update an open window; this one keeps the
        # numbers current and closes the window when the refresh ends.
        while self._open:
            if self.done.is_set():
                self._close("done")
                return
            self._render()
            time.sleep(_REFRESH_SECONDS)

    def _close(self, result: str) -> None:
        if self._open:
            self._open = False
            self.result = result
            self.close()

    def onClick(self, controlId: int) -> None:
        if controlId == STOP:
            self._close("stop")
        elif controlId == BACKGROUND:
            self._close("background")

    def onAction(self, action: Any) -> None:
        if action.getId() in (_ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK):
            self._close("background")


def show_progress_window(progress: RefreshProgress, heading: str, done: threading.Event) -> str:
    """Show the window until Stop, Background or the end of the refresh."""
    window = ProgressWindow(_XML, get_addon().getAddonInfo("path"), "Default", "1080i")
    window.progress, window.heading, window.done = progress, heading, done
    window.doModal()
    result = window.result
    del window
    return result

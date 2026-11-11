"""The setup wizard window and the controller behind it.

SetupController turns the setup model into what the window shows (heading,
explanation, rows, buttons) and carries out what the user picks, through
small dialog and storage seams so it is tested without Kodi. SetupWindow is
the WindowXMLDialog that renders it; run_setup wires both to Kodi.

Nothing is written before Finish (or Save in the folder dialog): Cancel at
any step leaves settings and sources.json as they were. sources.json is
written first, so a failed write leaves the settings untouched too.

Logging:
    Logger: 'setup'
    Key events:
        - setup.start (INFO): The wizard or folder dialog opened
        - setup.finish (INFO): Everything was saved
        - setup.cancel (INFO): Closed without saving
        - setup.save_fail (WARNING): sources.json could not be written
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, cast

import xbmc
import xbmcgui

from resources.lib.constants import COUNTRY_CODES
from resources.lib.data.presets import country_for_setting
from resources.lib.data.sources import (
    discover_sources,
    kodi_ui_language,
    load_source_config,
    save_source_config,
)
from resources.lib.providers.languages import TMDB_PRIMARY_TRANSLATIONS
from resources.lib.providers.tmdb import (
    KEY_OK,
    KEY_REFUSED,
    KEY_UNCHECKED,
    check_api_key,
)
from resources.lib.ui.setup_model import (
    FEATURES,
    FOLDERS,
    WIZARD,
    SetupFlow,
    SetupState,
    config_to_write,
    initial_state,
    mask_key,
    settings_to_write,
)
from resources.lib.utils import (
    get_addon,
    get_bool_setting,
    get_int_setting,
    get_logger,
    get_setting,
    lang,
)

log = get_logger('setup')

# Window XML and control ids (resources/skins/Default/1080i).
_XML = "script-metamender-setup.xml"
_LIST = 100
_NEXT = 21
_BACK = 22
_CANCEL = 23
_ACTION_PREVIOUS_MENU = 10
_ACTION_NAV_BACK = 92

# (name, one-line explanation) string ids per feature, in FEATURES order.
_FEATURE_TEXT = {
    "enable_ratings": (32432, 32433),
    "enable_plots": (32434, 32435),
    "enable_episode_titles": (32436, 32437),
    "enable_main_titles": (32438, 32439),
    "enable_certifications": (32440, 32441),
    "enable_credits": (32442, 32443),
    "enable_trailers": (32444, 32445),
    "enable_taglines": (32446, 32447),
    "enable_details": (32448, 32449),
}
# The `country` setting's options after Automatic: the labels of its
# options 1.. in settings.xml.
_COUNTRY_LABEL_BASE = 32259
_COUNTRY_OPTIONS = 9

_STEP_TEXT = {
    "key": (32410, 32411),
    "language": (32420, 32421),
    FOLDERS: (32425, 32426),
    "features": (32430, 32431),
    "country": (32450, 32451),
}


@dataclass(frozen=True)
class Row:
    label: str
    detail: str = ""
    # True shows the tick; False shows none.
    chosen: bool = False


class Dialogs:
    """The stock Kodi dialogs the steps open on top of the window."""

    def input(self, heading: str, default: str) -> str:
        return xbmcgui.Dialog().input(heading, default)

    def select(self, heading: str, options: list[str], preselect: int) -> int:
        items: list[Any] = list(options)
        return xbmcgui.Dialog().select(heading, items, preselect=preselect)

    def ok(self, heading: str, message: str) -> None:
        xbmcgui.Dialog().ok(heading, message)


def language_name(code: str) -> str:
    """"Dutch (NL)" for "nl-NL", from Kodi's own language names; the code
    itself when Kodi has no name for it."""
    base, _, region = code.partition("-")
    name = xbmc.convertLanguage(base, xbmc.ENGLISH_NAME)
    if not isinstance(name, str) or not name:
        return code
    return "{} ({})".format(name, region) if region else name


def write_settings(values: dict[str, Any]) -> None:
    """Write the wizard's settings in order (setup_complete last, so an
    interrupted write leaves setup incomplete)."""
    addon = get_addon()
    for key, value in values.items():
        if isinstance(value, bool):
            addon.setSettingBool(key, value)
        elif isinstance(value, int):
            addon.setSettingInt(key, value)
        else:
            addon.setSettingString(key, str(value))


class SetupController:
    def __init__(
        self,
        mode: str,
        state: SetupState,
        config: dict[str, Any],
        discovered: list[dict[str, str]],
        ui_language: str,
        config_path: str,
        first_run: bool,
        dialogs: Any = None,
        key_checker: Callable[[str], str] = check_api_key,
        save_config: Callable[[str, dict[str, Any]], bool] = save_source_config,
        settings_writer: Callable[[dict[str, Any]], None] = write_settings,
        name: Callable[[str], str] = language_name,
    ) -> None:
        self.mode = mode
        self.state = state
        self.flow = SetupFlow(mode, ui_language)
        self.finished = False
        self._config = config
        self._discovered = discovered
        self._ui_language = ui_language
        self._config_path = config_path
        self._first_run = first_run
        self._dialogs = dialogs or Dialogs()
        self._check = key_checker
        self._save = save_config
        self._write = settings_writer
        self._name = name
        self._languages = self._language_options()

    # -- what the window shows ---------------------------------------------

    @property
    def step(self) -> str:
        return self.flow.current

    def heading(self) -> str:
        if self.mode == FOLDERS:
            return lang(32429)
        if self.step == "summary":
            return lang(32455)
        return lang(_STEP_TEXT[self.step][0])

    def explanation(self) -> str:
        if self.step == "summary":
            return lang(32456 if self._first_run else 32457)
        if self.step == FOLDERS and not self._discovered:
            return lang(32427)
        return lang(_STEP_TEXT[self.step][1])

    def step_label(self) -> str:
        if self.mode == FOLDERS:
            return ""
        return lang(32403).format(self.flow.index(self.state), self.flow.total(self.state))

    def next_label(self) -> str:
        if self.mode == FOLDERS:
            return lang(32407)
        return lang(32406 if self.step == "summary" else 32405)

    def has_back(self) -> bool:
        return self.mode == WIZARD and self.flow.index(self.state) > 1

    def rows(self) -> list[Row]:
        return getattr(self, "_rows_" + self.step)()

    def _rows_key(self) -> list[Row]:
        key = self.state.api_key
        return [Row(lang(32412), mask_key(key) if key else lang(32413))]

    def _rows_language(self) -> list[Row]:
        fallback = self.state.fallback_language
        return [
            Row(lang(32422), self._name(self.state.default_language)),
            Row(lang(32423), self._name(fallback) if fallback else lang(32424)),
        ]

    def _rows_folders(self) -> list[Row]:
        rows = []
        for source in self._discovered:
            langs = self.state.exceptions.get(source.get("path", ""))
            if langs is None:
                rows.append(Row(source.get("label", ""), lang(32428)))
            else:
                rows.append(Row(source.get("label", ""), self._pair(*langs), True))
        return rows

    def _rows_features(self) -> list[Row]:
        return [
            Row(lang(_FEATURE_TEXT[f][0]), lang(_FEATURE_TEXT[f][1]),
                bool(self.state.features.get(f)))
            for f in FEATURES
        ]

    def _rows_country(self) -> list[Row]:
        return [
            Row(self._country_label(i), "", self.state.country == i)
            for i in range(_COUNTRY_OPTIONS + 1)
        ]

    def _rows_summary(self) -> list[Row]:
        s = self.state
        status = lang(32459 if s.key_status == KEY_OK else 32460)
        rows = [
            Row(lang(32458), "{} ({})".format(mask_key(s.api_key), status)),
            Row(lang(32422), self._name(s.default_language)),
            Row(lang(32423), self._name(s.fallback_language) if s.fallback_language
                else lang(32424)),
        ]
        for source in self._discovered:
            langs = s.exceptions.get(source.get("path", ""))
            if langs is not None:
                rows.append(Row(lang(32461).format(source.get("label", "")), self._pair(*langs)))
        ticked = [lang(_FEATURE_TEXT[f][0]) for f in FEATURES if s.features.get(f)]
        rows.append(Row(lang(32462), ", ".join(ticked) or lang(32463)))
        if s.features.get("enable_certifications"):
            rows.append(Row(lang(32450), self._country_label(s.country)))
        return rows

    # -- what the user does ------------------------------------------------

    def activate(self, index: int) -> None:
        """The user selected row ``index`` of the current step."""
        handler = getattr(self, "_activate_" + self.step, None)
        if handler is not None:
            handler(index)

    def _activate_key(self, _index: int) -> None:
        # Never pre-filled: a stored key is not shown in full again, and an
        # empty entry keeps it.
        entered = self._dialogs.input(lang(32412), "").strip()
        if entered and entered != self.state.api_key:
            self.state.api_key = entered
            self.state.key_status = ""

    def _activate_language(self, index: int) -> None:
        if index == 0:
            picked = self._pick_language(lang(32422), self.state.default_language, False)
            if picked is not None:
                self.state.default_language = picked
        else:
            picked = self._pick_language(lang(32423), self.state.fallback_language, True)
            if picked is not None:
                self.state.fallback_language = picked

    def _activate_folders(self, index: int) -> None:
        if not 0 <= index < len(self._discovered):
            return
        path = self._discovered[index].get("path", "")
        if path in self.state.exceptions:
            del self.state.exceptions[path]
            return
        language = self._pick_language(lang(32422), self.state.default_language, False)
        if language is None:
            return
        fallback = self._pick_language(lang(32423), "", True)
        self.state.exceptions[path] = (language, fallback or "")

    def _activate_features(self, index: int) -> None:
        if 0 <= index < len(FEATURES):
            feature = FEATURES[index]
            self.state.features[feature] = not self.state.features.get(feature)

    def _activate_country(self, index: int) -> None:
        if 0 <= index <= _COUNTRY_OPTIONS:
            self.state.country = index

    def on_next(self) -> bool:
        """Next, Finish or Save. True once everything is saved and the
        window should close."""
        if self.step == "key" and not self._key_passes():
            return False
        if not self.flow.can_advance(self.state):
            if self.step == "country":
                self._dialogs.ok(lang(32450), lang(32453))
            return False
        if self.mode == FOLDERS or self.step == "summary":
            return self._finish()
        self.flow.next(self.state)
        return False

    def on_back(self) -> bool:
        """Back; False on the first step (the caller then cancels)."""
        return self.mode == WIZARD and self.flow.back(self.state)

    def _key_passes(self) -> bool:
        if not self.state.api_key:
            self._activate_key(0)
            if not self.state.api_key:
                return False
        if self.state.key_status in (KEY_OK, KEY_UNCHECKED):
            return True
        status = self._check(self.state.api_key)
        if status == KEY_REFUSED:
            self._dialogs.ok(lang(32410), lang(32414))
            return False
        if status == KEY_UNCHECKED:
            self._dialogs.ok(lang(32410), lang(32415))
        self.state.key_status = status
        return True

    def _finish(self) -> bool:
        config = config_to_write(self.state, self._config, self._discovered)
        if not self._save(self._config_path, config):
            log.warning("Setup could not save the folder languages", event="setup.save_fail")
            self._dialogs.ok(self.heading(), lang(32465))
            return False
        if self.mode == WIZARD:
            self._write(settings_to_write(self.state))
        self.finished = True
        log.info("Setup saved", event="setup.finish", mode=self.mode,
                 first_run=self._first_run, exceptions=len(self.state.exceptions))
        return True

    # -- helpers -----------------------------------------------------------

    def _language_options(self) -> list[str]:
        codes = list(TMDB_PRIMARY_TRANSLATIONS)
        for extra in (self._ui_language, self.state.default_language):
            if extra and extra not in codes:
                codes.append(extra)
        return sorted(codes, key=lambda c: self._name(c).lower())

    def _pick_language(self, heading: str, current: str, allow_none: bool) -> str | None:
        """A language code, "" for None (when allowed), or None when the
        user cancelled."""
        options = ([lang(32424)] if allow_none else []) + [
            self._name(c) for c in self._languages
        ]
        offset = 1 if allow_none else 0
        if current in self._languages:
            preselect = self._languages.index(current) + offset
        else:
            preselect = 0
        picked = self._dialogs.select(heading, options, preselect)
        if picked < 0:
            return None
        if allow_none and picked == 0:
            return ""
        return self._languages[picked - offset]

    def _pair(self, language: str, fallback: str) -> str:
        if fallback:
            return "{}, {}: {}".format(self._name(language), lang(32423), self._name(fallback))
        return self._name(language)

    def _country_label(self, index: int) -> str:
        if index == 0:
            code = country_for_setting(0, self._ui_language)
            if code is None:
                return lang(32453)
            return lang(32452).format(self._country_label(1 + COUNTRY_CODES.index(code)))
        return lang(_COUNTRY_LABEL_BASE + index)


class SetupWindow(xbmcgui.WindowXMLDialog):
    """Renders a SetupController. The controller is attached before
    doModal(); every click re-renders the whole step."""

    controller: SetupController

    def onInit(self) -> None:
        self._render()

    def onClick(self, controlId: int) -> None:
        c = self.controller
        if controlId == _LIST:
            c.activate(self._list().getSelectedPosition())
        elif controlId == _NEXT:
            if c.on_next():
                self.close()
                return
        elif controlId == _BACK:
            c.on_back()
        elif controlId == _CANCEL:
            self.close()
            return
        self._render()

    def onAction(self, action: Any) -> None:
        if action.getId() in (_ACTION_PREVIOUS_MENU, _ACTION_NAV_BACK):
            if self.controller.on_back():
                self._render()
            else:
                self.close()

    def _list(self) -> xbmcgui.ControlList:
        return cast(xbmcgui.ControlList, self.getControl(_LIST))

    def _render(self) -> None:
        c = self.controller
        self.setProperty("MM.Heading", c.heading())
        self.setProperty("MM.Text", c.explanation())
        self.setProperty("MM.Step", c.step_label())
        self.setProperty("MM.Next", c.next_label())
        self.setProperty("MM.Back", lang(32404) if c.has_back() else "")
        self.setProperty("MM.Cancel", lang(32408))
        control = self._list()
        position = max(control.getSelectedPosition(), 0)
        control.reset()
        items = []
        for row in c.rows():
            item = xbmcgui.ListItem(row.label, offscreen=True)
            item.setProperty("detail", row.detail)
            item.setProperty("chosen", "true" if row.chosen else "")
            items.append(item)
        control.addItems(items)
        if items:
            control.selectItem(min(position, len(items) - 1))
            self.setFocusId(_LIST)
        else:
            self.setFocusId(_NEXT)


def run_setup(mode: str, config_path: str) -> bool:
    """Open the wizard (mode "wizard") or the folder dialog ("folders").
    True when the user finished and everything was saved."""
    ui_language = kodi_ui_language()
    config = load_source_config(config_path)
    discovered = discover_sources()
    settings: dict[str, Any] = {f: get_bool_setting(f) for f in FEATURES}
    settings["tmdb_api_key"] = get_setting("tmdb_api_key")
    settings["country"] = get_int_setting("country", 0)
    first_run = not get_bool_setting("setup_complete")
    state = initial_state(settings, config, discovered, ui_language)
    controller = SetupController(
        mode, state, config, discovered, ui_language, config_path, first_run,
    )
    log.info("Setup opened", event="setup.start", mode=mode, first_run=first_run,
             sources=len(discovered))
    addon = get_addon()
    window = SetupWindow(_XML, addon.getAddonInfo("path"), "Default", "1080i")
    window.controller = controller
    window.doModal()
    del window
    if not controller.finished:
        log.info("Setup closed without saving", event="setup.cancel", mode=mode)
    return controller.finished

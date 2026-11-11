"""The setup wizard as plain logic: which steps exist, what each needs
before Next, and what Finish writes. The window in setup_window is a thin
view over this module; nothing here talks to Kodi's GUI, so the whole flow
is unit tested.

The wizard asks only what metamender cannot guess (the TMDB key and the
library's languages) plus the feature toggles; every other setting keeps
its default and stays in Kodi's add-on settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from resources.lib.data.presets import country_for_setting
from resources.lib.data.sources import apply_folder_languages, folder_exceptions
from resources.lib.providers.tmdb import KEY_OK, KEY_UNCHECKED

# The field toggles, in settings.xml order.
FEATURES: tuple[str, ...] = (
    "enable_ratings", "enable_plots", "enable_episode_titles",
    "enable_main_titles", "enable_certifications", "enable_credits",
    "enable_trailers", "enable_taglines", "enable_details",
)

WIZARD = "wizard"
FOLDERS = "folders"


@dataclass
class SetupState:
    api_key: str = ""
    # KEY_OK or KEY_UNCHECKED once the key step passed; "" before that. A
    # refused key never gets here: the key step keeps it out.
    key_status: str = ""
    default_language: str = ""
    # "" for no fallback, here and in each exception.
    fallback_language: str = ""
    # Discovered source path (possibly multipath) -> (language, fallback).
    exceptions: dict[str, tuple[str, str]] = field(default_factory=dict)
    features: dict[str, bool] = field(default_factory=dict)
    # The `country` setting's index: 0 is Automatic.
    country: int = 0


def initial_state(
    settings: dict[str, Any],
    config: dict[str, Any],
    discovered: list[dict[str, str]],
    ui_language: str,
) -> SetupState:
    """The state every step opens with: the current values, so a re-run
    starts where the last setup left off. ``settings`` holds tmdb_api_key,
    the FEATURES and country as Kodi reports them (defaults when never set).
    A stored key was accepted when it was saved."""
    key = str(settings.get("tmdb_api_key") or "").strip()
    return SetupState(
        api_key=key,
        key_status=KEY_OK if key else "",
        default_language=str(config.get("default_language") or ui_language),
        fallback_language=str(config.get("fallback_language") or ""),
        exceptions=folder_exceptions(config, discovered),
        features={f: bool(settings.get(f)) for f in FEATURES},
        country=int(settings.get("country") or 0),
    )


class SetupFlow:
    """Step order and position. The country step exists only while
    certifications are ticked, so the step list is recomputed from the
    state on every move and the position is kept by step name."""

    def __init__(self, mode: str, ui_language: str) -> None:
        self.mode = mode
        self._ui_language = ui_language
        self.current = FOLDERS if mode == FOLDERS else "key"

    def steps(self, state: SetupState) -> list[str]:
        if self.mode == FOLDERS:
            return [FOLDERS]
        steps = ["key", "language", FOLDERS, "features"]
        if state.features.get("enable_certifications"):
            steps.append("country")
        steps.append("summary")
        return steps

    def index(self, state: SetupState) -> int:
        """1-based position, for "Step N of M"."""
        return self._position(state) + 1

    def total(self, state: SetupState) -> int:
        return len(self.steps(state))

    def next(self, state: SetupState) -> bool:
        steps = self.steps(state)
        pos = self._position(state)
        if pos + 1 >= len(steps):
            return False
        self.current = steps[pos + 1]
        return True

    def back(self, state: SetupState) -> bool:
        pos = self._position(state)
        if pos == 0:
            return False
        self.current = self.steps(state)[pos - 1]
        return True

    def can_advance(self, state: SetupState) -> bool:
        if self.current == "key":
            return bool(state.api_key.strip()) and state.key_status in (KEY_OK, KEY_UNCHECKED)
        if self.current == "country":
            return country_for_setting(state.country, self._ui_language) is not None
        return True

    def _position(self, state: SetupState) -> int:
        steps = self.steps(state)
        if self.current in steps:
            return steps.index(self.current)
        # The current step left the list (country after unticking
        # certifications): it stood just before the summary.
        self.current = steps[-1]
        return len(steps) - 1


def settings_to_write(state: SetupState) -> dict[str, Any]:
    """The add-on settings Finish writes, setup_complete last."""
    out: dict[str, Any] = {"tmdb_api_key": state.api_key.strip()}
    out.update({f: bool(state.features.get(f)) for f in FEATURES})
    out["country"] = state.country
    out["setup_complete"] = True
    return out


def config_to_write(
    state: SetupState, config: dict[str, Any], discovered: list[dict[str, str]],
) -> dict[str, Any]:
    """The sources.json Finish (or the folder dialog's Save) writes."""
    return apply_folder_languages(
        config, discovered, state.default_language, state.fallback_language,
        state.exceptions,
    )


def mask_key(key: str) -> str:
    """Four stars and the key's last four characters, so the mask tells
    keys apart without showing how long the key is; a key that short is
    hidden entirely."""
    key = key.strip()
    if len(key) <= 4:
        return "****"
    return "****" + key[-4:]


def summary_lines(
    state: SetupState, discovered: list[dict[str, str]],
) -> list[tuple[str, str]]:
    """The summary as (what, raw value) pairs, for the window to label and
    translate: "key", "key_status", "language", "fallback", one
    "folder:<label>" per exception ("language,fallback"), "features" (the
    ticked toggles, comma-separated) and, with certifications on,
    "country" (the setting index)."""
    lines = [
        ("key", mask_key(state.api_key)),
        ("key_status", state.key_status),
        ("language", state.default_language),
        ("fallback", state.fallback_language),
    ]
    for source in discovered:
        langs = state.exceptions.get(source.get("path", ""))
        if langs is not None:
            lines.append(("folder:" + source.get("label", ""), ",".join(langs)))
    lines.append(("features", ",".join(f for f in FEATURES if state.features.get(f))))
    if state.features.get("enable_certifications"):
        lines.append(("country", str(state.country)))
    return lines

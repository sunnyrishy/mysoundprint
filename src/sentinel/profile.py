"""Per-person profiles: which sounds matter to this wearer, in which situation, and how sensitive
Sentinel should be.

A profile maps every sound to a level (interrupt, quiet, later, off) and holds a sensitivity.
There are several named profiles (Focus, Home, Sleep and Commute by default) and exactly one is
active. Changes apply live, without a restart, and are saved to a small JSON file. The store holds
settings only, never audio.

Backward compatibility: the original API (`get`, `update` with `enabled` and `sensitivity`,
`is_enabled`, `sensitivity`) still works and now acts on the active profile. An `enabled: false`
sets the level off. An old single-profile JSON file is read as overrides to the active profile.
"""

from __future__ import annotations

import contextlib
import copy
import json
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.config import (
    MAX_LABEL_CHARS,
    SENSITIVITY_MAX,
    SENSITIVITY_MIN,
    ClassRule,
    ProfilesConfig,
    SentinelConfig,
    resolve_path,
)
from sentinel.policy import effective_level
from sentinel.types import ALERT_WORDING, AlertClass, Level

__all__ = ["SENSITIVITY_MAX", "SENSITIVITY_MIN", "Profile", "ProfileError", "ProfileStore"]

LEVEL_NAMES = [lv.value for lv in Level]
_LEGACY_KEYS = {"enabled", "sensitivity"}
_UPDATE_KEYS = _LEGACY_KEYS | {"levels", "active"}


class ProfileError(ValueError):
    """An invalid profile update, with a message meant for a human."""


@dataclass
class Profile:
    id: str
    label: str
    sensitivity: float = 1.0
    levels: dict[str, Level] = field(default_factory=dict)  # one entry for every sound


class ProfileStore:
    """Thread-safe holder read by the pipeline and written by the phone page."""

    def __init__(
        self,
        path: str | Path | None = None,
        profiles: ProfilesConfig | None = None,
        catalog: Mapping[AlertClass, ClassRule] | None = None,
        protected: Iterable[AlertClass] = (),
    ) -> None:
        self._lock = threading.Lock()
        self._path = resolve_path(path) if path else None
        self._catalog = dict(catalog or {})
        self._protected = frozenset(protected)
        cfg = profiles or ProfilesConfig()
        self._profiles = {pid: self._build(pid, d) for pid, d in cfg.definitions.items()}
        self._active = cfg.active
        self._version = 0
        if self._path and self._path.exists():
            # a damaged or invalid file must never stop Sentinel starting
            with contextlib.suppress(ValueError, OSError):
                self._profiles, self._active = self._load(json.loads(self._path.read_text()))

    @classmethod
    def from_config(cls, cfg: SentinelConfig, path: str | Path | None = None) -> ProfileStore:
        """The store for a config: its profiles, sound catalog and protected sounds."""
        return cls(
            path,
            cfg.profiles,
            cfg.classifier.catalog(),
            [AlertClass(c) for c in cfg.policy.protected],
        )

    # ---- building and loading --------------------------------------------------------------
    def default_level(self, alert_class: AlertClass) -> Level:
        rule = self._catalog.get(alert_class)
        return rule.default_level if rule else Level.INTERRUPT

    def _build(self, pid: str, definition: Any) -> Profile:
        levels = {
            c.value: Level(definition.levels.get(c.value, self.default_level(c)))
            for c in AlertClass
        }
        return Profile(pid, definition.label or pid.title(), definition.sensitivity, levels)

    def _load(self, data: Any) -> tuple[dict[str, Profile], str]:
        """Apply a saved file to copies of the current state. Raises if anything is invalid."""
        if not isinstance(data, dict):
            raise ProfileError("profile file must be a JSON object")
        profiles = copy.deepcopy(self._profiles)
        active = self._active
        if "profiles" in data:
            for pid, saved in (data.get("profiles") or {}).items():
                if pid not in profiles:
                    raise ProfileError(f"unknown profile '{pid}'")
                self._apply(profiles[pid], saved)
            active = data.get("active", active)
            if active not in profiles:
                raise ProfileError(f"unknown active profile '{active}'")
        else:  # the original single-profile format
            self._apply(profiles[active], data)
        return profiles, active

    # ---- validation of one update ----------------------------------------------------------
    def _apply(self, prof: Profile, data: Any) -> None:
        """Merge enabled, sensitivity and levels into one profile. Raises ProfileError."""
        if not isinstance(data, dict):
            raise ProfileError("profile must be a JSON object")
        unknown = set(data) - (_UPDATE_KEYS | {"version"})
        if unknown:
            raise ProfileError(f"unknown profile keys {sorted(unknown)}")
        for name, on in (data.get("enabled") or {}).items():
            cls = self._sound(name)
            if not isinstance(on, bool):
                raise ProfileError(f"enabled.{name} must be true or false")
            if not on:
                prof.levels[cls.value] = Level.OFF
            elif prof.levels[cls.value] == Level.OFF:
                fallback = self.default_level(cls)
                prof.levels[cls.value] = Level.INTERRUPT if fallback == Level.OFF else fallback
        for name, level in (data.get("levels") or {}).items():
            cls = self._sound(name)
            if level not in LEVEL_NAMES and not isinstance(level, Level):
                raise ProfileError(f"levels.{name} must be one of {LEVEL_NAMES}")
            prof.levels[cls.value] = Level(level)
        if "sensitivity" in data:
            s = data["sensitivity"]
            if isinstance(s, bool) or not isinstance(s, int | float):
                raise ProfileError("sensitivity must be a number")
            if not SENSITIVITY_MIN <= s <= SENSITIVITY_MAX:
                raise ProfileError(
                    f"sensitivity must be between {SENSITIVITY_MIN} and {SENSITIVITY_MAX}"
                )
            prof.sensitivity = float(s)

    @staticmethod
    def _sound(name: str) -> AlertClass:
        try:
            return AlertClass(name)
        except ValueError:
            valid = sorted(c.value for c in AlertClass)
            raise ProfileError(f"unknown alert class '{name}'. Valid: {valid}") from None

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        saved = {
            "version": 2,
            "active": self._active,
            "profiles": {
                pid: {
                    "sensitivity": p.sensitivity,
                    "levels": {k: lv.value for k, lv in p.levels.items()},
                }
                for pid, p in self._profiles.items()
            },
        }
        self._path.write_text(json.dumps(saved, indent=2) + "\n")

    # ---- reading ----------------------------------------------------------------------------
    @property
    def active(self) -> str:
        with self._lock:
            return self._active

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def sensitivity(self) -> float:
        with self._lock:
            return self._profiles[self._active].sensitivity

    @property
    def protected(self) -> frozenset[AlertClass]:
        return self._protected

    def names(self) -> list[str]:
        with self._lock:
            return list(self._profiles)

    def level_for(self, alert_class: AlertClass) -> Level:
        """The level the active profile asks for. The protected rule is applied by the policy."""
        with self._lock:
            return self._profiles[self._active].levels[alert_class.value]

    def is_enabled(self, alert_class: AlertClass) -> bool:
        """The original on/off view: true unless the active profile says off."""
        return self.level_for(alert_class) != Level.OFF

    def get(self) -> dict[str, Any]:
        """The original shape: per sound on/off and the sensitivity of the active profile."""
        with self._lock:
            prof = self._profiles[self._active]
            return {
                "enabled": {c.value: prof.levels[c.value] != Level.OFF for c in AlertClass},
                "sensitivity": prof.sensitivity,
            }

    def view(self) -> dict[str, Any]:
        """The original shape plus the active profile name and its levels. Sent over WebSocket."""
        out = self.get()
        with self._lock:
            out["active"] = self._active
            out["levels"] = {k: lv.value for k, lv in self._profiles[self._active].levels.items()}
        return out

    def _sound_info(self, cls: AlertClass) -> dict[str, str]:
        rule = self._catalog.get(cls)
        legacy = ALERT_WORDING.get(cls)
        if rule is not None:
            line1, line2 = rule.labels(cls)
        else:
            line1 = legacy[1] if legacy else cls.value.replace("_", " ").upper()[:MAX_LABEL_CHARS]
            line2 = legacy[2] if legacy else "NEARBY"
        return {
            "id": cls.value,
            "label1": line1,
            "label2": line2,
            "default_level": self.default_level(cls).value,
        }

    def snapshot(self) -> dict[str, Any]:
        """Everything the phone page needs: profiles, sounds, levels and what is protected."""
        with self._lock:
            active = self._profiles[self._active]
            effective = {
                c.value: effective_level(active.levels[c.value], c, self._protected).value
                for c in AlertClass
            }
            return {
                "active": self._active,
                "profiles": [
                    {
                        "id": p.id,
                        "label": p.label,
                        "sensitivity": p.sensitivity,
                        "levels": {k: lv.value for k, lv in p.levels.items()},
                    }
                    for p in self._profiles.values()
                ],
                "sounds": [self._sound_info(c) for c in AlertClass],
                "levels": LEVEL_NAMES,
                "protected": sorted(c.value for c in self._protected),
                "effective": effective,
            }

    # ---- writing ----------------------------------------------------------------------------
    def set_active(self, name: str) -> dict[str, Any]:
        """Switch the active profile. Nothing else changes."""
        with self._lock:
            if name not in self._profiles:
                raise ProfileError(f"unknown profile '{name}'. Choose from {list(self._profiles)}")
            self._active = name
            self._version += 1
            self._save()
        return self.view()

    def update(self, data: dict[str, Any]) -> dict[str, Any]:
        """Merge a partial update into the active profile. Original keys: enabled, sensitivity.
        New keys: levels (sound to level) and active (switch profile first).
        Raises ProfileError and changes nothing if any part is invalid."""
        if not isinstance(data, dict):
            raise ProfileError("profile must be a JSON object")
        with self._lock:
            active = data.get("active", self._active)
            if active not in self._profiles:
                raise ProfileError(
                    f"unknown profile '{active}'. Choose from {list(self._profiles)}"
                )
            candidate = copy.deepcopy(self._profiles[active])
            self._apply(candidate, {k: v for k, v in data.items() if k != "active"})
            self._profiles[active] = candidate
            self._active = active
            self._version += 1
            self._save()
        return self.get()

    def update_profile(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Edit a named profile (levels and sensitivity) without switching to it."""
        if not isinstance(data, dict) or set(data) - {"levels", "sensitivity"}:
            raise ProfileError("send levels and or sensitivity")
        with self._lock:
            if name not in self._profiles:
                raise ProfileError(f"unknown profile '{name}'. Choose from {list(self._profiles)}")
            candidate = copy.deepcopy(self._profiles[name])
            self._apply(candidate, data)
            self._profiles[name] = candidate
            self._version += 1
            self._save()
        return self.view()

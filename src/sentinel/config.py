"""Pydantic config models, YAML loading and config hash. Bad keys and class names fail loudly."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from sentinel.types import ALERT_WORDING, AlertClass, DisplayCaps, Level

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"
VALID_CLASSES = [c.value for c in AlertClass]
SENSITIVITY_MIN = 0.5
SENSITIVITY_MAX = 1.5
MAX_LABEL_CHARS = 12  # the assumed display width. The display adapter still truncates.


def _level_from_yaml(value: Any) -> Any:
    """YAML 1.1 reads an unquoted `off` as the boolean False. Accept it as the level off."""
    return Level.OFF if value is False else value


LevelValue = Annotated[Level, BeforeValidator(_level_from_yaml)]


class ConfigError(ValueError):
    """Raised for any invalid configuration, with a message meant for a human."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AudioConfig(_Strict):
    sample_rate: Literal[16000] = 16000
    device: str | int | None = None
    frame_ms: int = Field(32, gt=0, le=1000)


class BufferConfig(_Strict):
    seconds: float = Field(10, gt=1)


class GateConfig(_Strict):
    enabled: bool = True
    n_fft: int = Field(1024, gt=0)
    hop: int = Field(256, gt=0)
    band_hz: tuple[float, float] = (800.0, 3000.0)
    peak_to_median_db: float = Field(15.0, gt=0)
    freq_tolerance_hz: float = Field(50.0, gt=0)
    min_stable_frames: int = Field(5, ge=1)
    burst_min_s: float = Field(0.25, gt=0)
    burst_max_s: float = Field(1.0, gt=0)
    pulse_period_s: tuple[float, float] = (0.6, 1.5)
    confirm_timeout_s: float = Field(1.6, gt=0)

    @model_validator(mode="after")
    def _check_ranges(self) -> GateConfig:
        if not 0 < self.band_hz[0] < self.band_hz[1] <= 8000:
            raise ValueError("gate.band_hz must satisfy 0 < low < high <= 8000")
        if self.burst_min_s >= self.burst_max_s:
            raise ValueError("gate.burst_min_s must be smaller than gate.burst_max_s")
        if self.pulse_period_s[0] >= self.pulse_period_s[1]:
            raise ValueError("gate.pulse_period_s must be [min, max] with min < max")
        if self.hop > self.n_fft:
            raise ValueError("gate.hop must not exceed gate.n_fft")
        return self


class Smoothing(_Strict):
    hits: int = Field(2, ge=1)
    windows: int = Field(3, ge=1)

    @model_validator(mode="after")
    def _check(self) -> Smoothing:
        if self.hits > self.windows:
            raise ValueError("classifier.smoothing.hits must not exceed windows")
        return self


class ClassRule(_Strict):
    """One entry of the sound catalog: what to listen for and how to show it."""

    names: list[str] = Field(min_length=1)
    threshold: float = Field(gt=0, le=1)
    label1: str | None = Field(None, min_length=1, max_length=MAX_LABEL_CHARS)
    label2: str | None = Field(None, min_length=1, max_length=MAX_LABEL_CHARS)
    default_level: LevelValue = Level.INTERRUPT

    def labels(self, alert_class: AlertClass) -> tuple[str, str]:
        """Display text. Config first, then the original spec wording, then the class name."""
        legacy = ALERT_WORDING.get(alert_class)
        line1 = self.label1 or (
            legacy[1] if legacy else alert_class.value.replace("_", " ").upper()
        )
        line2 = self.label2 or (legacy[2] if legacy else "NEARBY")
        return line1[:MAX_LABEL_CHARS], line2[:MAX_LABEL_CHARS]


class ClassifierConfig(_Strict):
    enabled: bool = True
    model_path: str = "models/yamnet_classification.tflite"
    class_map_path: str = "models/yamnet_class_map.csv"
    hop_s: float = Field(0.25, gt=0)
    smoothing: Smoothing = Smoothing()
    classes: dict[str, ClassRule]

    @field_validator("classes")
    @classmethod
    def _check_class_names(cls, v: dict[str, ClassRule]) -> dict[str, ClassRule]:
        for key in v:
            if key not in VALID_CLASSES:
                raise ValueError(f"unknown alert class '{key}'. Valid classes: {VALID_CLASSES}")
        return v

    def catalog(self) -> dict[AlertClass, ClassRule]:
        """The sound catalog keyed by AlertClass."""
        return {AlertClass(k): rule for k, rule in self.classes.items()}


class PolicyConfig(_Strict):
    cooldown_s: float = Field(5.0, ge=0)
    ttl_s: float = Field(3.0, gt=0)
    blink: bool = True
    priority: list[str] = Field(default_factory=lambda: [c.value for c in AlertClass])
    # Sounds that always interrupt, whatever the active profile says (explicit safety rule).
    protected: list[str] = Field(default_factory=lambda: [AlertClass.FIRE_ALARM.value])

    @field_validator("priority", "protected")
    @classmethod
    def _check_class_list(cls, v: list[str], info: Any) -> list[str]:
        for name in v:
            if name not in VALID_CLASSES:
                raise ValueError(f"unknown alert class '{name}' in policy.{info.field_name}")
        if len(set(v)) != len(v):
            raise ValueError(f"policy.{info.field_name} has duplicates")
        return v


class CapsConfig(_Strict):
    lines: int = Field(2, ge=1)
    max_chars_per_line: int = Field(12, ge=1)
    color: Literal["mono_green", "full"] = "mono_green"
    supports_blink: bool = False

    def to_caps(self) -> DisplayCaps:
        return DisplayCaps(
            lines=self.lines,
            max_chars_per_line=self.max_chars_per_line,
            color=self.color,
            supports_blink=self.supports_blink,
        )


def _default_profile_definitions() -> dict[str, ProfileDef]:
    return {name: ProfileDef(label=name.title()) for name in ("focus", "home", "sleep", "commute")}


class ProfileDef(_Strict):
    """One named profile: a level per sound (missing sounds use their catalog default) and a
    sensitivity. Sensitivity above 1 catches more, below 1 gives fewer false alerts."""

    label: str | None = Field(None, min_length=1, max_length=24)
    sensitivity: float = Field(1.0, ge=SENSITIVITY_MIN, le=SENSITIVITY_MAX)
    levels: dict[str, LevelValue] = Field(default_factory=dict)

    @field_validator("levels")
    @classmethod
    def _check_levels(cls, v: dict[str, LevelValue]) -> dict[str, LevelValue]:
        for name in v:
            if name not in VALID_CLASSES:
                raise ValueError(f"unknown alert class '{name}'. Valid classes: {VALID_CLASSES}")
        return v


class ProfilesConfig(_Strict):
    active: str = "home"
    definitions: dict[str, ProfileDef] = Field(default_factory=_default_profile_definitions)

    @model_validator(mode="after")
    def _check(self) -> ProfilesConfig:
        if not self.definitions:
            raise ValueError("profiles.definitions needs at least one profile")
        for name in self.definitions:
            if not name.replace("_", "").isalnum() or name != name.lower():
                raise ValueError(f"profile id '{name}' must be lowercase letters, digits or _")
        if self.active not in self.definitions:
            raise ValueError(
                f"profiles.active '{self.active}' is not defined. Choose from "
                f"{sorted(self.definitions)}"
            )
        return self


class DisplayConfig(_Strict):
    adapter: Literal["console", "sim", "bridge", "microlumin"] = "console"
    caps: CapsConfig = CapsConfig()


class DashboardConfig(_Strict):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)


class EventLogConfig(_Strict):
    path: str = "data/events.sqlite"


class RecordingConfig(_Strict):
    enabled: bool = False
    dir: str = "data/recordings"


class SentinelConfig(_Strict):
    audio: AudioConfig = AudioConfig()
    buffer: BufferConfig = BufferConfig()
    gate: GateConfig = GateConfig()
    classifier: ClassifierConfig
    policy: PolicyConfig = PolicyConfig()
    profiles: ProfilesConfig = Field(default_factory=ProfilesConfig)
    display: DisplayConfig = DisplayConfig()
    dashboard: DashboardConfig = DashboardConfig()
    eventlog: EventLogConfig = EventLogConfig()
    recording: RecordingConfig = RecordingConfig()

    def config_hash(self) -> str:
        """Stable short hash of the active config, stored in every event."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


def resolve_path(path: str | Path) -> Path:
    """Relative paths in the config are relative to the project root, not the cwd."""
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _format_errors(err: ValidationError, source: str) -> str:
    lines = [f"Invalid config ({source}):"]
    for item in err.errors():
        loc = ".".join(str(x) for x in item["loc"]) or "(root)"
        lines.append(f"  {loc}: {item['msg']}")
    return "\n".join(lines)


def config_from_dict(data: dict[str, Any], source: str = "dict") -> SentinelConfig:
    try:
        return SentinelConfig.model_validate(data)
    except ValidationError as err:
        raise ConfigError(_format_errors(err, source)) from err


def load_config(path: str | Path | None = None) -> SentinelConfig:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(cfg_path.read_text())
    except FileNotFoundError as err:
        raise ConfigError(f"Config file not found: {cfg_path}") from err
    except yaml.YAMLError as err:
        raise ConfigError(f"Config file is not valid YAML ({cfg_path}): {err}") from err
    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must contain a mapping at the top level: {cfg_path}")
    return config_from_dict(raw, source=str(cfg_path))

"""Fusion and alert state machine. Pure logic: time is passed in, actions come out, no I/O.

State per class: IDLE, PROVISIONAL, CONFIRMED, COOLDOWN (spec, Fusion and alert state machine).
The pipeline applies the returned actions to the display and the event log.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sentinel.config import ClassRule, PolicyConfig
from sentinel.types import Alert, AlertClass, Detection, Level


class ClassState(str, Enum):
    IDLE = "idle"
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"
    COOLDOWN = "cooldown"


def effective_level(
    level: Level, alert_class: AlertClass, protected: Collection[AlertClass]
) -> Level:
    """The protected rule: a protected sound (fire and smoke alarms by default) always interrupts,
    whatever the active profile asks for. The profile itself is never changed by this."""
    return Level.INTERRUPT if alert_class in protected else level


@dataclass(frozen=True)
class CreateEvent:
    event_id: str
    alert_class: AlertClass
    stage: str  # gate | classifier | both
    score: float
    confirmed: bool
    cancel_reason: str | None
    t_onset: float
    t_detect: float
    level: str | None = None  # the effective level that decided what happened
    profile: str | None = None  # the active profile at the time of detection


@dataclass(frozen=True)
class UpdateEvent:
    event_id: str
    changes: dict[str, Any]  # any of: confirmed, stage, score, cancel_reason


@dataclass(frozen=True)
class ShowAlert:
    alert: Alert


@dataclass(frozen=True)
class ClearAlert:
    event_id: str
    reason: str  # 'timeout' (unconfirmed) or 'ttl'


Action = CreateEvent | UpdateEvent | ShowAlert | ClearAlert


@dataclass
class _Track:
    state: ClassState = ClassState.IDLE
    event_id: str | None = None
    stages: set[str] = field(default_factory=set)
    score: float = 0.0
    confirm_deadline: float = 0.0
    clear_at: float = 0.0
    cool_until: float = 0.0
    level: Level = Level.INTERRUPT


def stage_label(stages: set[str]) -> str:
    return "both" if {"gate", "classifier"} <= stages else next(iter(stages))


class Policy:
    def __init__(
        self,
        cfg: PolicyConfig,
        confirm_timeout_s: float,
        id_gen: Callable[[], str],
        catalog: Mapping[AlertClass, ClassRule] | None = None,
    ) -> None:
        self.cfg = cfg
        self.confirm_timeout_s = confirm_timeout_s
        self._id_gen = id_gen
        self._catalog = dict(catalog or {})
        self._protected = frozenset(AlertClass(c) for c in cfg.protected)
        # Lower number is higher priority. Classes missing from the list rank last.
        order = [AlertClass(c) for c in cfg.priority]
        self._rank = {c: order.index(c) if c in order else len(order) for c in AlertClass}
        self._tracks = {c: _Track() for c in AlertClass}
        self._on_display: AlertClass | None = None

    # Introspection for tests and the dashboard ----------------------------------------------------
    def state(self, alert_class: AlertClass) -> ClassState:
        return self._tracks[alert_class].state

    @property
    def displayed_class(self) -> AlertClass | None:
        return self._on_display

    def default_level(self, alert_class: AlertClass) -> Level:
        rule = self._catalog.get(alert_class)
        return rule.default_level if rule else Level.INTERRUPT

    def effective_level(self, alert_class: AlertClass, requested: Level | None = None) -> Level:
        """Requested level (or the catalog default) after the protected rule."""
        return effective_level(requested or self.default_level(alert_class), alert_class,
                               self._protected)  # fmt: skip

    # Inputs -----------------------------------------------------------------------------------
    def on_detection(
        self,
        det: Detection,
        now: float,
        level: Level | None = None,
        profile: str | None = None,
    ) -> list[Action]:
        """Handle one detection. level is what the active profile asks for (None means the
        catalog default). The protected rule is applied here, then the level decides the outcome."""
        actions = self.tick(now)
        lv = self.effective_level(det.alert_class, level)
        if lv == Level.OFF:
            return actions  # dropped: no alert, no event
        tr = self._tracks[det.alert_class]
        if tr.state == ClassState.COOLDOWN:
            return actions
        if tr.state in (ClassState.PROVISIONAL, ClassState.CONFIRMED):
            return actions + self._reinforce(det, tr, now)
        if lv == Level.LATER:
            return actions + self._save_for_later(det, tr, now, profile)
        return actions + self._start(det, tr, now, lv, profile)

    def tick(self, now: float) -> list[Action]:
        """Resolve timers: unconfirmed timeout, alert time to live, cooldown end."""
        actions: list[Action] = []
        for cls in sorted(AlertClass, key=lambda c: self._rank[c]):
            tr = self._tracks[cls]
            if tr.state == ClassState.PROVISIONAL:
                deadline = min(tr.confirm_deadline, tr.clear_at)
                if now >= deadline and tr.event_id is not None:
                    actions.append(ClearAlert(tr.event_id, "timeout"))
                    actions.append(UpdateEvent(tr.event_id, {"cancel_reason": "timeout"}))
                    self._release(cls, tr, deadline)
            elif tr.state == ClassState.CONFIRMED and now >= tr.clear_at and tr.event_id:
                actions.append(ClearAlert(tr.event_id, "ttl"))
                self._release(cls, tr, tr.clear_at)
            if tr.state == ClassState.COOLDOWN and now >= tr.cool_until:
                tr.state = ClassState.IDLE  # checked after the branches so one long tick resolves
        return actions

    # Internals --------------------------------------------------------------------------------
    def _reinforce(self, det: Detection, tr: _Track, now: float) -> list[Action]:
        """A detection of a class whose alert is already active: extend, maybe confirm."""
        assert tr.event_id is not None
        changes: dict[str, Any] = {}
        tr.clear_at = max(tr.clear_at, now + self.cfg.ttl_s)
        before = stage_label(tr.stages)
        tr.stages.add(det.stage)
        if stage_label(tr.stages) != before:
            changes["stage"] = stage_label(tr.stages)
        if tr.state == ClassState.PROVISIONAL and not det.provisional:
            tr.state = ClassState.CONFIRMED
            changes["confirmed"] = True
        if det.score > tr.score:
            tr.score = det.score
            changes["score"] = det.score
        return [UpdateEvent(tr.event_id, changes)] if changes else []

    def _prio(self, cls: AlertClass, level: Level) -> tuple[int, int]:
        """Display priority, lower is stronger: interrupt beats quiet, then the class order."""
        return (0 if level == Level.INTERRUPT else 1, self._rank[cls])

    def _save_for_later(
        self, det: Detection, tr: _Track, now: float, profile: str | None
    ) -> list[Action]:
        """Level later: log a confirmed event for the phone history and never show it."""
        if det.provisional:
            return []  # wait for the gate's confirmation, so unconfirmed hits are not saved
        tr.state = ClassState.COOLDOWN  # one saved event per cooldown, not one per detection
        tr.cool_until = now + self.cfg.cooldown_s
        return [
            CreateEvent(
                self._id_gen(), det.alert_class, det.stage, det.score, True, None,
                det.t_onset, det.t_detect, Level.LATER.value, profile,
            )
        ]  # fmt: skip

    def _start(
        self, det: Detection, tr: _Track, now: float, level: Level, profile: str | None
    ) -> list[Action]:
        cls = det.alert_class
        event_id = self._id_gen()
        confirmed = not det.provisional
        shown = self._on_display
        if shown is not None and self._prio(shown, self._tracks[shown].level) < self._prio(
            cls, level
        ):
            # A higher priority alert is on screen. Log the event, never show it.
            tr.state = ClassState.COOLDOWN
            tr.cool_until = now + self.cfg.cooldown_s
            return [
                CreateEvent(
                    event_id, cls, det.stage, det.score, confirmed, "superseded",
                    det.t_onset, det.t_detect, level.value, profile,
                )
            ]  # fmt: skip
        actions: list[Action] = []
        if shown is not None:
            old = self._tracks[shown]
            if old.event_id is not None:
                actions.append(UpdateEvent(old.event_id, {"cancel_reason": "superseded"}))
            self._release(shown, old, now)
        actions.append(
            CreateEvent(
                event_id,
                cls,
                det.stage,
                det.score,
                confirmed,
                None,
                det.t_onset,
                det.t_detect,
                level.value,
                profile,
            )
        )
        actions.append(ShowAlert(self._alert(cls, event_id, level)))
        tr.level = level
        tr.event_id = event_id
        tr.stages = {det.stage}
        tr.score = det.score
        tr.clear_at = now + self.cfg.ttl_s
        tr.confirm_deadline = now + self.confirm_timeout_s
        tr.state = ClassState.CONFIRMED if confirmed else ClassState.PROVISIONAL
        self._on_display = cls
        return actions

    def _release(self, cls: AlertClass, tr: _Track, t_clear: float) -> None:
        """The alert for cls is no longer on screen: start its cooldown."""
        tr.state = ClassState.COOLDOWN
        tr.cool_until = t_clear + self.cfg.cooldown_s
        tr.event_id = None
        tr.stages = set()
        if self._on_display == cls:
            self._on_display = None

    def _alert(self, cls: AlertClass, event_id: str, level: Level) -> Alert:
        """Interrupt is tier 1 and asks for blink. Quiet is tier 2 and never blinks. Whether the
        display can blink is the display adapter's business, not the policy's."""
        rule = self._catalog.get(cls)
        line1, line2 = (
            rule.labels(cls) if rule else ClassRule(names=["x"], threshold=0.3).labels(cls)
        )
        tier = 1 if level == Level.INTERRUPT else 2
        return Alert(
            event_id=event_id,
            alert_class=cls,
            tier=tier,
            line1=line1,
            line2=line2,
            blink=self.cfg.blink and tier == 1,
            ttl_s=self.cfg.ttl_s,
        )

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

from _metrics.readability import ReadStatus

RootCauseKind = Literal["sender", "receiver", "bus", "normal"]


def _default_now_ms() -> float:
    return time.monotonic() * 1000.0


def classify_pair_root_cause_kind(
    source_read_status: ReadStatus,
    target_read_status: ReadStatus,
    bus_not_ok: bool,
) -> RootCauseKind:
    if source_read_status != ReadStatus.OK:
        return "sender"
    if target_read_status != ReadStatus.OK:
        return "receiver"
    if bus_not_ok:
        return "bus"
    return "normal"


@dataclass(frozen=True)
class PipelineAttribution:
    cause: Literal[
        "bus_down", "receiver_down", "receiver_stale_frozen",
        "platform_unreadable_frozen", "queued_unknown_frozen", "normal",
    ]
    blocked_by: Optional[str] = None


def resolve_bus_incident(
    bus_health_status: ReadStatus,
    bus_endpoint_status: ReadStatus,
    bus_health_object_id: str,
    bus_endpoint_object_id: str,
    target_db_status: ReadStatus,
    target_db_object_id: str,
    target_heartbeat_class: str,
    target_read_status: ReadStatus,
    source_read_status: ReadStatus,
    queued_data_available: bool,
    last_known: Optional[PipelineAttribution],
) -> PipelineAttribution:
    if bus_health_status != ReadStatus.OK:
        return PipelineAttribution(cause="bus_down", blocked_by=bus_health_object_id)
    if bus_endpoint_status != ReadStatus.OK:
        return PipelineAttribution(cause="bus_down", blocked_by=bus_endpoint_object_id)

    if target_db_status != ReadStatus.OK:
        return PipelineAttribution(cause="receiver_down", blocked_by=target_db_object_id)

    if target_heartbeat_class != "ok":
        if last_known is not None:
            return last_known
        return PipelineAttribution(cause="receiver_stale_frozen", blocked_by=None)

    if target_read_status == ReadStatus.UNREAD or source_read_status == ReadStatus.UNREAD:
        if last_known is not None:
            return last_known
        return PipelineAttribution(cause="platform_unreadable_frozen", blocked_by=None)

    if not queued_data_available:
        if last_known is not None:
            return last_known
        return PipelineAttribution(cause="queued_unknown_frozen", blocked_by=None)

    return PipelineAttribution(cause="normal", blocked_by=None)


class BusRawStreakTracker:

    def __init__(self) -> None:
        self._streaks: dict[str, int] = {}

    def observe(self, key: str, raw_status: ReadStatus) -> int:
        if raw_status == ReadStatus.OK:
            self._streaks[key] = 0
        else:
            self._streaks[key] = self._streaks.get(key, 0) + 1
        return self._streaks[key]

    def streak(self, key: str) -> int:
        return self._streaks.get(key, 0)


@dataclass
class _LostAlertState:
    active: bool = False
    zero_streak: int = 0
    zero_since_ms: Optional[float] = None


@dataclass(frozen=True)
class LostAlertTick:
    newly_raised: bool
    active: bool
    newly_resolved: bool
    severity: Optional[int]


class LostAlertTracker:

    def __init__(self, bus_incident_stale_after_ms: int = 120000, now_ms: Callable[[], float] = _default_now_ms):
        self.bus_incident_stale_after_ms = bus_incident_stale_after_ms
        self._now_ms = now_ms
        self._states: dict[str, _LostAlertState] = {}

    @classmethod
    def from_settings(cls, settings, now_ms: Callable[[], float] = _default_now_ms) -> "LostAlertTracker":
        return cls(bus_incident_stale_after_ms=settings.bus_incident_stale_after_ms, now_ms=now_ms)

    def observe(self, pipeline_key: str, lost_count: int, trigger_severity: int) -> LostAlertTick:
        state = self._states.setdefault(pipeline_key, _LostAlertState())
        now = self._now_ms()

        if lost_count > 0:
            newly_raised = not state.active
            state.active = True
            state.zero_streak = 0
            state.zero_since_ms = None
            return LostAlertTick(
                newly_raised=newly_raised, active=True, newly_resolved=False,
                severity=min(2, trigger_severity),
            )

        if not state.active:
            return LostAlertTick(newly_raised=False, active=False, newly_resolved=False, severity=None)

        state.zero_streak += 1
        if state.zero_since_ms is None:
            state.zero_since_ms = now
        resolved = state.zero_streak >= 2 and (now - state.zero_since_ms) >= self.bus_incident_stale_after_ms
        if resolved:
            state.active = False
            state.zero_streak = 0
            state.zero_since_ms = None
        return LostAlertTick(newly_raised=False, active=not resolved, newly_resolved=resolved, severity=None)


def apply_sound_gate(
    wants_alarm: bool,
    oldest_pending_packet_age_seconds: Optional[float],
    sound_min_oldest_packet_age_seconds: int,
) -> bool:
    if not wants_alarm:
        return False
    if oldest_pending_packet_age_seconds is None:
        return False
    return oldest_pending_packet_age_seconds >= sound_min_oldest_packet_age_seconds

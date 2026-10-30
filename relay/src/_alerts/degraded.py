from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


def is_draining(*, matched_recently: bool, queued_countable: bool) -> bool:
    return bool(matched_recently and queued_countable)


def _actual_wait_seconds(
    oldest_pending_seconds: Optional[float],
    avg_lag_seconds_fallback: Optional[float],
    unblocked_for_seconds: Optional[float],
) -> Optional[float]:
    wait = oldest_pending_seconds if oldest_pending_seconds is not None else avg_lag_seconds_fallback
    if wait is None:
        return None
    if unblocked_for_seconds is not None:
        wait = min(wait, unblocked_for_seconds)
    return wait


@dataclass(frozen=True)
class DegradedResult:
    degraded: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    send_wait_seconds: Optional[float] = None
    apply_wait_seconds: Optional[float] = None
    draining: bool = False


def evaluate_degraded(
    *,
    queued_count: int,
    backlog_max: int,
    red_on_the_map: bool,
    lost_count: int,
    matched_recently: bool,
    queued_countable: bool,
    send_oldest_pending_seconds: Optional[float],
    send_avg_lag_seconds: Optional[float],
    apply_oldest_pending_seconds: Optional[float],
    apply_avg_lag_seconds: Optional[float],
    lag_max: int,
    unblocked_for_seconds: Optional[float] = None,
) -> DegradedResult:
    draining = is_draining(matched_recently=matched_recently, queued_countable=queued_countable)
    reasons: list[str] = []

    if queued_count > backlog_max and not draining:
        reasons.append("backlog")

    if red_on_the_map:
        reasons.append("red_on_the_map")

    if lost_count > 0 and not draining:
        reasons.append("lost")

    send_wait = _actual_wait_seconds(send_oldest_pending_seconds, send_avg_lag_seconds, unblocked_for_seconds)
    apply_wait = _actual_wait_seconds(apply_oldest_pending_seconds, apply_avg_lag_seconds, unblocked_for_seconds)
    if not draining:
        if send_wait is not None and send_wait > lag_max:
            reasons.append("send_lag")
        if apply_wait is not None and apply_wait > lag_max:
            reasons.append("apply_lag")

    return DegradedResult(
        degraded=bool(reasons),
        reasons=tuple(reasons),
        send_wait_seconds=send_wait,
        apply_wait_seconds=apply_wait,
        draining=draining,
    )

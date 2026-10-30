from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def down_pending_level(count: int, yellow_threshold: int, red_threshold: int) -> str:
    if count >= red_threshold:
        return "red"
    if count >= yellow_threshold:
        return "yellow"
    return "green"


@dataclass(frozen=True)
class SummaryAggregates:
    worst_backlog: Optional[dict]
    lost_summary: dict
    messages_held_by: dict[int, int]
    biggest_pile_outside_buses: Optional[dict]


def pipeline_text(pid) -> str:
    server, sep, number = str(pid).rpartition(":")
    return f"{number} on {server}" if sep else str(pid)


def compute_summary_aggregates(
    *,
    pipeline_queue_counts: dict[int, int],
    pipeline_bus_id: dict[int, Optional[int]],
    lost_pipeline_ids: set[int],
    yellow_threshold: int,
    red_threshold: int,
    material_backlog_factor: int,
) -> SummaryAggregates:
    material_floor = yellow_threshold * material_backlog_factor

    worst_backlog: Optional[dict] = None
    biggest_outside: Optional[dict] = None
    held_by: dict[int, int] = {}

    for pid, count in pipeline_queue_counts.items():
        bus_id = pipeline_bus_id.get(pid)
        if bus_id is not None:
            held_by[bus_id] = held_by.get(bus_id, 0) + count

        if count < material_floor:
            continue

        level = down_pending_level(count, yellow_threshold, red_threshold)
        if worst_backlog is None or count > worst_backlog["count"]:
            worst_backlog = {"pipeline_id": pid, "count": count, "level": level}

        if bus_id is None and (biggest_outside is None or count > biggest_outside["count"]):
            biggest_outside = {"pipeline_id": pid, "count": count}

    lost_summary = {"count": len(lost_pipeline_ids), "pipeline_ids": sorted(lost_pipeline_ids)}

    return SummaryAggregates(
        worst_backlog=worst_backlog,
        lost_summary=lost_summary,
        messages_held_by=held_by,
        biggest_pile_outside_buses=biggest_outside,
    )


BRANCH_ORDER: tuple[str, ...] = (
    "all_relays_stopped",
    "service_bus_down",
    "red_backlog",
    "yellow_backlog",
    "down_db_objects",
    "stopped_relays",
    "unreadable_relays",
    "lost_messages",
    "generic_pipeline_problem",
    "good",
)


@dataclass(frozen=True)
class SummaryResult:
    level: str
    branch: str
    note: str
    aggregates: SummaryAggregates


def compute_summary(
    *,
    all_relays_stopped: bool,
    any_service_bus_down: bool,
    any_db_object_down: bool,
    any_relay_stopped: bool,
    any_relay_unreadable: bool,
    any_pipeline_degraded: bool,
    aggregates: SummaryAggregates,
) -> SummaryResult:
    worst = aggregates.worst_backlog
    worst_level = worst["level"] if worst else "green"

    if all_relays_stopped:
        return SummaryResult("grey", "all_relays_stopped", "All relays are stopped.", aggregates)

    if any_service_bus_down and worst is not None:
        note = f"Service Bus is down; backlog {worst['count']} on pipeline {pipeline_text(worst['pipeline_id'])}."
        return SummaryResult("red", "service_bus_down", note, aggregates)

    if worst_level == "red":
        return SummaryResult(
            "red", "red_backlog",
            f"Backlog on pipeline {pipeline_text(worst['pipeline_id'])} ({worst['count']}).", aggregates,
        )

    if worst_level == "yellow":
        return SummaryResult(
            "yellow", "yellow_backlog",
            f"Backlog building on pipeline {pipeline_text(worst['pipeline_id'])} ({worst['count']}).", aggregates,
        )

    if any_db_object_down:
        return SummaryResult("red", "down_db_objects", "One or more database objects are down.", aggregates)

    if any_service_bus_down:
        return SummaryResult("grey", "service_bus_down", "Service Bus is down.", aggregates)

    if any_relay_stopped:
        return SummaryResult("yellow", "stopped_relays", "One or more relays have stopped.", aggregates)

    if any_relay_unreadable:
        return SummaryResult("yellow", "unreadable_relays", "One or more relays cannot currently be read.", aggregates)

    if aggregates.lost_summary["count"] > 0:
        return SummaryResult(
            "yellow", "lost_messages",
            f"{aggregates.lost_summary['count']} pipeline(s) have lost messages.", aggregates,
        )

    if any_pipeline_degraded:
        return SummaryResult("yellow", "generic_pipeline_problem", "One or more pipelines are degraded.", aggregates)

    return SummaryResult("green", "good", "All monitored systems are healthy.", aggregates)

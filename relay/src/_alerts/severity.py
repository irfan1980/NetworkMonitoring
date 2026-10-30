from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import ObjectSeverities

DEFAULT_SEVERITY_FALLBACK = 3


def row_severity(explicit_severity: Optional[int], fallback: int = DEFAULT_SEVERITY_FALLBACK) -> int:
    return explicit_severity if explicit_severity is not None else fallback


def pipeline_severity(row_severities: list[int], fallback: int = DEFAULT_SEVERITY_FALLBACK) -> int:
    if not row_severities:
        return fallback
    return min(row_severities)


def object_severity(kind: str, object_severities: "ObjectSeverities", fallback: int = DEFAULT_SEVERITY_FALLBACK) -> int:
    mapping = {
        "bus": object_severities.bus,
        "db": object_severities.db,
        "machine": object_severities.machine,
        "schedule": object_severities.schedule,
        "scheduler_endpoint": object_severities.scheduler_endpoint,
    }
    return mapping.get(kind, fallback)

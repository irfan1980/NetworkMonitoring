from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from _metrics.readability import EMITTED_STATUSES, ReadStatus

WireStatus = Literal["ok", "down", "blocked"]


def to_wire_status(status: ReadStatus) -> WireStatus:
    if status not in EMITTED_STATUSES:
        raise ValueError(
            f"{status!r} is not an emitted wire status "
            f"(EMITTED_STATUSES = {sorted(s.value for s in EMITTED_STATUSES)}); "
            "this value must never be serialized onto any WS message."
        )
    return status.value  # type: ignore[return-value]


class WireObject(BaseModel):

    id: str
    kind: str
    status: WireStatus
    blocked_by: Optional[str] = None


def build_wire_object(object_id: str, kind: str, read_status: ReadStatus, blocked_by: Optional[str] = None) -> dict:
    wire = WireObject(id=object_id, kind=kind, status=to_wire_status(read_status), blocked_by=blocked_by)
    return wire.model_dump(exclude_none=True)


def build_wire_objects(emitted: dict[str, ReadStatus], kinds: dict[str, str], blocked_by: dict[str, str]) -> list[dict]:
    return [
        build_wire_object(object_id, kinds.get(object_id, "unknown"), status, blocked_by.get(object_id))
        for object_id, status in emitted.items()
    ]

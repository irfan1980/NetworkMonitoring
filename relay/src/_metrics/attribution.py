from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from _metrics.down_confirm import DownConfirm
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import RelayConfig


def _default_now_ms() -> float:
    return time.monotonic() * 1000.0


@dataclass(frozen=True)
class ObjectSpec:

    object_id: str
    kind: str
    depends_on: Optional[str] = None


def resolve_server_host_machine(config: "RelayConfig") -> dict[int, int]:
    host_of: dict[int, int] = {}
    for machine in config.machines:
        for node in machine.nodes:
            if node.node_type in ("Database", "POS"):
                host_of[node.server_id] = machine.machine_id
    return host_of


def build_object_inventory(
    config: "RelayConfig", offline_machine_ids: frozenset[int] = frozenset()
) -> list[ObjectSpec]:
    host_of = resolve_server_host_machine(config)
    machine_objects_added: set[int] = set()

    objects: list[ObjectSpec] = []
    for bus in config.service_busses:
        health_id = f"bus:{bus.id}:health"
        endpoint_id = f"bus:{bus.id}:endpoint"
        objects.append(ObjectSpec(object_id=health_id, kind="bus_health"))
        objects.append(ObjectSpec(object_id=endpoint_id, kind="bus_endpoint", depends_on=health_id))
    for server in config.servers:
        query_id = f"db:{server.platform}:{server.id}:query"
        machine_id = host_of.get(server.id)
        depends_on = None
        if machine_id is not None and machine_id in offline_machine_ids:
            machine_object_id = f"machine:{machine_id}"
            if machine_id not in machine_objects_added:
                objects.append(ObjectSpec(object_id=machine_object_id, kind="machine"))
                machine_objects_added.add(machine_id)
            depends_on = machine_object_id
        objects.append(ObjectSpec(object_id=query_id, kind="db_query", depends_on=depends_on))
    return objects


def obj_status_for_reads(down_confirm: DownConfirm, object_id: str) -> ReadStatus:
    if down_confirm.is_currently_unread(object_id):
        return ReadStatus.UNREAD
    return down_confirm.confirmed_status(object_id)


@dataclass
class AttributionResult:
    emitted: dict[str, ReadStatus] = field(default_factory=dict)
    blocked_by: dict[str, str] = field(default_factory=dict)
    read_status: dict[str, ReadStatus] = field(default_factory=dict)


def compute_attribution(inventory: list[ObjectSpec], down_confirm: DownConfirm) -> AttributionResult:
    emitted: dict[str, ReadStatus] = {}
    blocked_by: dict[str, str] = {}
    read_status: dict[str, ReadStatus] = {}

    remaining = list(inventory)
    while remaining:
        progressed = False
        next_remaining: list[ObjectSpec] = []
        for obj in remaining:
            if obj.depends_on is not None and obj.depends_on not in emitted:
                next_remaining.append(obj)
                continue

            read_status[obj.object_id] = obj_status_for_reads(down_confirm, obj.object_id)

            if obj.depends_on is not None and emitted[obj.depends_on] != ReadStatus.OK:
                emitted[obj.object_id] = ReadStatus.BLOCKED
                blocked_by[obj.object_id] = obj.depends_on
            else:
                emitted[obj.object_id] = down_confirm.confirmed_status(obj.object_id)
            progressed = True

        if not progressed and next_remaining:
            cyclic = [o.object_id for o in next_remaining]
            raise ValueError(f"attribution dependency cycle among {cyclic!r}")
        remaining = next_remaining

    return AttributionResult(emitted=emitted, blocked_by=blocked_by, read_status=read_status)


def root_cause_of(object_id: str, result: AttributionResult) -> Optional[str]:
    current = object_id
    seen: set[str] = set()
    while True:
        if current in seen:
            raise ValueError(f"attribution blockedBy cycle at {current!r}")
        seen.add(current)

        emitted = result.emitted.get(current)
        if emitted == ReadStatus.BLOCKED:
            current = result.blocked_by[current]
            continue

        read = result.read_status.get(current, ReadStatus.OK)
        if emitted == ReadStatus.DOWN or read == ReadStatus.UNREAD:
            return current
        return None


def affects_of(object_id: str, result: AttributionResult) -> list[str]:
    affected: list[str] = []
    for other_id, upstream in result.blocked_by.items():
        current: Optional[str] = upstream
        seen: set[str] = set()
        while current is not None:
            if current == object_id:
                affected.append(other_id)
                break
            if current in seen:
                break
            seen.add(current)
            current = result.blocked_by.get(current)
    return affected


HeartbeatClass = str


def classify_heartbeat_staleness(
    heartbeat_age_seconds: Optional[float],
    ping_seconds: int,
    down_multiplier: int = 3,
    blocked_multiplier: int = 2,
) -> HeartbeatClass:
    if heartbeat_age_seconds is None:
        return "ok"
    if heartbeat_age_seconds >= ping_seconds * down_multiplier:
        return "down"
    if heartbeat_age_seconds >= ping_seconds * blocked_multiplier:
        return "blocked_by_relay"
    return "ok"


class HeartbeatAgeSmoother:

    def __init__(self) -> None:
        self._was_down_or_blocked: dict[str, bool] = {}

    def smoothed_age(
        self, object_id: str, raw_age_seconds: Optional[float], currently_down_or_blocked: bool
    ) -> Optional[float]:
        was_bad = self._was_down_or_blocked.get(object_id, False)
        self._was_down_or_blocked[object_id] = currently_down_or_blocked
        if was_bad and not currently_down_or_blocked:
            return 0.0
        return raw_age_seconds


def prune_stale_metadata(meta: dict, known_object_ids) -> dict:
    known = set(known_object_ids)
    return {k: v for k, v in meta.items() if k in known}


@dataclass
class _RoutineNamesCacheEntry:
    names: frozenset
    fetched_at_ms: float


class RoutineNamesCache:

    def __init__(self, triggers_cache_ms: int = 60000, now_ms: Callable[[], float] = _default_now_ms):
        self.triggers_cache_ms = triggers_cache_ms
        self._now_ms = now_ms
        self._by_server: dict[str, _RoutineNamesCacheEntry] = {}

    async def get_or_fetch(self, server_id: str, fetch_fn: Callable[[], Awaitable[frozenset]]) -> frozenset:
        entry = self._by_server.get(server_id)
        now = self._now_ms()
        if entry is not None and (now - entry.fetched_at_ms) < self.triggers_cache_ms:
            return entry.names
        names = frozenset(await fetch_fn())
        self._by_server[server_id] = _RoutineNamesCacheEntry(names=names, fetched_at_ms=now)
        return names

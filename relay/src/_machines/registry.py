from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

PushCallback = Callable[[dict], Awaitable[None]]


@dataclass
class MachineRuntimeState:
    machine_id: int
    status: str = "pending"
    last_probe_ts: Optional[str] = None
    last_active_ts: Optional[str] = None
    ping_ms: Optional[float] = None
    active_services: list[dict] = field(default_factory=list)
    last_known_services: list[dict] = field(default_factory=list)
    active_alerts: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=lambda: {"level": "unknown"})
    connections: dict[int, PushCallback] = field(default_factory=dict, repr=False)
    _next_conn_id: int = field(default=0, repr=False)

    def register_connection(self, push: PushCallback) -> int:
        conn_id = self._next_conn_id
        self._next_conn_id += 1
        self.connections[conn_id] = push
        return conn_id

    def unregister_connection(self, conn_id: int) -> None:
        self.connections.pop(conn_id, None)


class MachineRegistry:
    def __init__(self) -> None:
        self._states: dict[int, MachineRuntimeState] = {}

    def state_for(self, machine_id: int) -> MachineRuntimeState:
        return self._states.setdefault(machine_id, MachineRuntimeState(machine_id))

    def get(self, machine_id: int) -> Optional[MachineRuntimeState]:
        return self._states.get(machine_id)

    def known_ids(self) -> set[int]:
        return set(self._states.keys())

    def drop(self, machine_id: int) -> None:
        self._states.pop(machine_id, None)

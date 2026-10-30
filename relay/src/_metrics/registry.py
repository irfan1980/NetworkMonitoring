from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from _db.registry import RegistryDriver, RegistryRow


@dataclass(frozen=True)
class PipelinePair:

    pipeline_id: int
    source_system: str
    target_system: str
    name: str = ""
    platform: str = ""


def derive_pipeline_pairs(rows: list[RegistryRow], platform: str = "") -> list[PipelinePair]:
    return [
        PipelinePair(
            pipeline_id=row.pipeline_id,
            source_system=row.source_system,
            target_system=row.target_system,
            name=row.name,
            platform=platform,
        )
        for row in rows
    ]


@dataclass
class RegistryRefreshResult:
    changed: bool
    signature: str
    pairs: list[PipelinePair]


@dataclass
class _ServerRegistryState:
    signature: Optional[str] = None
    pairs: list[PipelinePair] = field(default_factory=list)
    force_next: bool = False


class RegistryCache:

    def __init__(self) -> None:
        self._by_server: dict[str, _ServerRegistryState] = {}

    async def refresh(self, server_id: str, driver: RegistryDriver, platform: str = "") -> RegistryRefreshResult:
        state = self._by_server.setdefault(server_id, _ServerRegistryState())
        signature = await driver.fetch_registry_signature()

        force = state.force_next
        state.force_next = False
        if not force and state.signature is not None and signature == state.signature:
            return RegistryRefreshResult(changed=False, signature=signature, pairs=state.pairs)

        rows = await driver.fetch_registry_rows()
        pairs = derive_pipeline_pairs(rows, platform=platform)
        state.signature = signature
        state.pairs = pairs
        return RegistryRefreshResult(changed=True, signature=signature, pairs=pairs)

    def force_refresh_next(self, server_id: str) -> None:
        state = self._by_server.setdefault(server_id, _ServerRegistryState())
        state.force_next = True

    def pairs_for(self, server_id: str) -> list[PipelinePair]:
        state = self._by_server.get(server_id)
        return list(state.pairs) if state else []

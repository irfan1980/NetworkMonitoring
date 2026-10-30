from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RegistryRow:

    pipeline_id: int
    source_system: str
    target_system: str
    name: str = ""


class RegistryDriver(Protocol):

    async def fetch_registry_signature(self) -> str: ...

    async def fetch_registry_rows(self) -> list[RegistryRow]: ...

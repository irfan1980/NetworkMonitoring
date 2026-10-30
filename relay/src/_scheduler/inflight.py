from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

from _lib.atomic_write import atomic_write_json

logger = logging.getLogger("relay.scheduler.inflight")

INFLIGHT_FILE = ("scheduler", "inflight.json")


@dataclass
class ActiveRun:
    run_id: str
    schedule_id: int
    trigger: str
    machine_id: int
    script: str
    args: list[str]
    timeout_s: float
    retries: int
    retry_delay_s: float
    started_at: str
    attempt: int = 1
    phase: str = "running"  # running | retry_wait
    retry_at: Optional[str] = None
    task: object = field(default=None, repr=False, compare=False)
    waiting: bool = field(default=False, repr=False, compare=False)
    cancelling: bool = field(default=False, repr=False, compare=False)
    abandoned: Optional[str] = field(default=None, repr=False, compare=False)

    def endpoint_run_id(self) -> str:
        return f"{self.run_id}-{self.attempt}"

    def to_json(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("task", "waiting", "cancelling", "abandoned")}


class InflightStore:

    def __init__(self, state_dir: Path | str):
        self._state_dir = Path(state_dir)

    def _path(self) -> Path:
        return self._state_dir.joinpath(*INFLIGHT_FILE)

    def save(self, runs: list[ActiveRun], last_tick: Optional[str]) -> None:
        atomic_write_json(
            self._state_dir, *INFLIGHT_FILE,
            data={"last_tick": last_tick, "runs": [r.to_json() for r in runs]},
        )

    def load(self) -> tuple[list[ActiveRun], Optional[str]]:
        path = self._path()
        if not path.exists():
            return [], None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            runs = [ActiveRun(**item) for item in raw.get("runs", [])]
            return runs, raw.get("last_tick")
        except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            logger.error("inflight file unreadable (%s); ignoring it", exc)
            return [], None

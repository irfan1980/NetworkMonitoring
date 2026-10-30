from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from _ws.estate_payload import build_estate_sections

if TYPE_CHECKING:
    from _lib.config import ConfigStore

logger = logging.getLogger("relay.ws.estate_loop")


class EstateSnapshotLoop:
    def __init__(self, config_store: "ConfigStore", app_state):
        self._config_store = config_store
        self._app_state = app_state
        self.latest: dict | None = None
        self.last_tick_at: datetime | None = None
        self._sessions: list = []
        self._running = False

    def register(self, session) -> None:
        self._sessions.append(session)

    def unregister(self, session) -> None:
        self._sessions = [s for s in self._sessions if s is not session]

    async def tick(self) -> dict:
        _t0 = time.monotonic()
        sections = await build_estate_sections(self._app_state)
        self.latest = sections
        self.last_tick_at = datetime.now(timezone.utc)
        if os.environ.get("RELAY_PACKETS_TRACE") == "1":
            logger.info("PKTTRACE tick build_seconds=%.2f sessions=%d", time.monotonic() - _t0, len(self._sessions))
        await self.push_snapshots()
        return sections

    async def push_snapshots(self) -> None:
        for session in list(self._sessions):
            if not session.hello_received:
                continue
            try:
                await session.send_snapshot()
            except (ConnectionError, OSError, RuntimeError) as exc:
                logger.info("estate_loop: /ws session gone while pushing a snapshot: %s", exc)
            except Exception:
                logger.exception("estate_loop: failed to push snapshot to a /ws session")

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            started = time.monotonic()
            try:
                await self.tick()
            except Exception:
                logger.exception("estate snapshot loop cycle failed")
            settings = self._config_store.get().settings
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(1.0, float(settings.poll_seconds) - elapsed))

    def stop(self) -> None:
        self._running = False

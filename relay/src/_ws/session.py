from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from _ws.messages import POST_HELLO_ALLOWED_TYPES, validate_inbound, validate_inbound_hello, validate_outbound
from _ws.rate_limit import AckRateLimiter

logger = logging.getLogger(__name__)

STARTING_UP_SOURCE = "STARTING_UP_SOURCE"
STARTING_UP_ENGINE = "STARTING_UP_ENGINE"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def starting_up_snapshot(seq: int) -> dict:
    return {
        "type": "snapshot",
        "seq": seq,
        "ts": _now_iso(),
        "source": STARTING_UP_SOURCE,
        "engine": STARTING_UP_ENGINE,
    }


@dataclass
class WsSession:
    send_json: Callable[[dict], Awaitable[None]]
    ack_limiter: AckRateLimiter
    build_snapshot: Callable[[int], dict]
    """Called with the next `seq` value; returns a full, schema-valid snapshot dict
    (`type`/`seq`/`ts` plus endpoint-specific sections). Not called until `hello`
    succeeds."""
    is_ready: Callable[[], bool] = field(default=lambda: True)
    """P104: `False` means "no real payload yet" -> `starting_up_snapshot()` is sent
    instead of calling `build_snapshot`."""
    on_ack: Optional[Callable[["object"], Awaitable[None]]] = None
    """Called with a validated `AckMessage` when an `ack` is accepted (passed rate
    limiting and schema validation) — the caller's own job to actually acknowledge
    the alert and reply with `ackResult` (`send_ack_result` below)."""

    hello_received: bool = field(default=False, init=False)
    client_id: Optional[str] = field(default=None, init=False)
    seq: int = field(default=0, init=False)

    async def _send_validated(self, payload: dict) -> bool:
        if not validate_outbound(payload):
            logger.warning("suppressed invalid outbound WS message: %r", payload.get("type"))
            return False
        await self.send_json(payload)
        return True

    async def handle_inbound(self, raw: dict) -> None:
        if not self.hello_received:
            hello = validate_inbound_hello(raw)
            if hello is None:
                return
            self.hello_received = True
            self.client_id = hello.clientId
            self.seq += 1
            if self.is_ready():
                await self._send_validated(self.build_snapshot(self.seq))
            else:
                await self._send_validated(starting_up_snapshot(self.seq))
            return

        msg = validate_inbound(raw)
        if msg is None:
            return

        if msg.type == "resync":
            await self.send_resync()
        elif msg.type == "ack":
            if not self.ack_limiter.allow(msg.clientId or msg.ackedBy or self.client_id or "unknown"):
                await self.send_ack_result(msg.alertId, False, "rate_limited")
                return
            if self.on_ack is not None:
                await self.on_ack(msg)

    async def send_resync(self) -> None:
        if self.is_ready():
            await self._send_validated(self.build_snapshot(self.seq))
        else:
            await self._send_validated(starting_up_snapshot(self.seq))

    async def send_snapshot(self) -> None:
        self.seq += 1
        if self.is_ready():
            await self._send_validated(self.build_snapshot(self.seq))
        else:
            await self._send_validated(starting_up_snapshot(self.seq))

    async def send_delta(self, build_delta: Callable[[int], dict]) -> None:
        self.seq += 1
        await self._send_validated(build_delta(self.seq))

    async def send_alert(self, payload: dict) -> None:
        await self._send_validated(payload)

    async def send_ack_result(self, alert_id: str, ok: bool, reason: Optional[str] = None) -> None:
        await self._send_validated({"type": "ackResult", "alertId": alert_id, "ok": ok, "reason": reason})

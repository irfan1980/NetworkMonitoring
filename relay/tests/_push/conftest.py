from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from _lib.config import ConfigStore
from _push.dispatcher import PushDispatcher
from _push.fcm import FcmClient


class FakeClock:
    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeFcm:
    def __init__(self):
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": body})
        token = body["message"]["token"]
        if token.startswith("dead"):
            return httpx.Response(404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}})
        if token.startswith("invalid"):
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "details": [
                {"@type": "type.googleapis.com/google.rpc.BadRequest", "fieldViolations": [{"field": "message.token"}]}]}})
        if token.startswith("bad"):
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}})
        if token.startswith("err"):
            return httpx.Response(500, json={"error": {"status": "INTERNAL"}})
        return httpx.Response(200, json={"name": "projects/p/messages/1"})

    def tokens(self) -> list[str]:
        return [r["body"]["message"]["token"] for r in self.requests]

    def clear(self) -> None:
        self.requests.clear()


def device(token: str, platform: str = "android") -> dict:
    return {"token": token, "platform": platform, "device_name": token, "registered_at": "2026-10-08T00:00:00Z",
            "last_seen_at": "2026-10-08T00:00:00Z"}


def alert(alert_id: str = "machine:4", severity: int = 2, reason: str = "machine 4 is offline", kind: str = "machine") -> dict:
    return {"alertId": alert_id, "kind": kind, "severity": severity, "reason": reason}

from tests._accounts_helpers import get_accounts, set_accounts  # noqa: E402


class PushHarness:
    def __init__(self, tmp_path: Path, config_dict: dict):
        self.tmp = tmp_path
        self.clock = FakeClock()
        self.fcm = FakeFcm()
        self.key_path = tmp_path / "firebase-service-account.json"
        self.key_path.write_text(json.dumps({"project_id": "demo-project"}))
        self.write_accounts([
            {"account_secret": "1111", "name": "Ops", "min_severity": 2, "device_codes": [device("ops-1"), device("ops-2", "web")]},
            {"account_secret": "2222", "name": "Boss", "min_severity": 5, "device_codes": [device("boss-1", "ios")]},
        ])
        config_dict["settings"]["push"] = {
            "enabled": True, "service_account_file": str(self.key_path),
            "grace_seconds": 15, "min_interval_seconds": 120, "dashboard_url": "http://localhost:8081",
        }
        config_path = tmp_path / "relay_config.json"
        config_path.write_text(json.dumps(config_dict))
        self.config_store = ConfigStore(config_path)
        self.config_store.load_startup()
        self.alerts: list[dict] = []
        self.dispatcher = self.new_dispatcher()

    def write_accounts(self, accounts: list[dict]) -> None:
        set_accounts(self.tmp / "state", accounts)

    def read_accounts(self) -> list[dict]:
        return get_accounts(self.tmp / "state")

    def fcm_factory(self, path: Path) -> FcmClient:
        return FcmClient(path, http_client=httpx.Client(transport=httpx.MockTransport(self.fcm.handler)), token_provider=lambda: "access-token")

    def new_dispatcher(self) -> PushDispatcher:
        return PushDispatcher(
            self.config_store, self.tmp / "state", lambda: list(self.alerts),
            fcm_factory=self.fcm_factory, clock=self.clock,
        )

    async def run(self, seconds: float, step: float = 1.0) -> None:
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.clock.advance(step)
            await self.dispatcher.tick()


@pytest.fixture
def push(tmp_path, config_dict) -> PushHarness:
    return PushHarness(tmp_path, config_dict)

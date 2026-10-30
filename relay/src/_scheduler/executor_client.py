from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from _scheduler.exec_key import exec_key


class EndpointError(Exception):
    pass


class EndpointUnavailable(EndpointError):
    pass


class EndpointAuthRejected(EndpointError):
    def __init__(self, message: str, forbidden_ip: bool = False):
        super().__init__(message)
        self.forbidden_ip = forbidden_ip


@dataclass
class StartResult:
    kind: str  # running | finished | script_not_found | locked
    body: dict = field(default_factory=dict)

    @property
    def locked_by(self) -> Optional[str]:
        return self.body.get("locked_by")


class ExecutorClient:

    def __init__(
        self,
        settings_getter: Callable[[], object],
        transport: Optional[httpx.AsyncBaseTransport] = None,
        clock: Optional[Callable[[], float]] = None,
        timeout_seconds: float = 5.0,
    ):
        self._settings = settings_getter
        self._clock = clock
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, machine, method: str, path: str, body: Optional[dict] = None) -> httpx.Response:
        port = self._settings().executor_port
        url = f"http://{machine.machine_ip}:{port}{path}"
        headers = {"X-Exec-Key": exec_key(machine.machine_id, machine.machine_ip, self._clock() if self._clock else None)}
        try:
            response = await self._client.request(method, url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise EndpointUnavailable(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__) from exc
        if response.status_code == 401:
            raise EndpointAuthRejected("endpoint rejected the key")
        if response.status_code == 403:
            raise EndpointAuthRejected("endpoint refused the relay IP", forbidden_ip=True)
        if response.status_code >= 500:
            raise EndpointUnavailable(f"endpoint answered HTTP {response.status_code}")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise EndpointUnavailable("endpoint answered with invalid JSON") from exc
        return data if isinstance(data, dict) else {}

    async def health(self, machine) -> dict:
        response = await self._request(machine, "GET", "/health")
        data = self._json(response)
        if response.status_code != 200 or not data.get("ok"):
            raise EndpointUnavailable("endpoint reported not ok")
        return data

    async def check_script(self, machine, script: str) -> dict:
        response = await self._request(machine, "POST", "/scripts/check", {"script": script})
        if response.status_code != 200:
            raise EndpointUnavailable(f"script check answered HTTP {response.status_code}")
        data = self._json(response)
        out = {"exists": bool(data.get("exists")), "is_file": bool(data.get("is_file"))}
        for key in ("resolved", "error"):
            if data.get(key) is not None:
                out[key] = data[key]
        return out

    async def start_run(
        self, machine, run_id: str, script: str, args: list[str], timeout_s: float, output_tail_kb: int
    ) -> StartResult:
        response = await self._request(
            machine, "POST", "/runs",
            {"run_id": run_id, "script": script, "args": args, "timeout_s": timeout_s, "output_tail_kb": output_tail_kb},
        )
        body = self._json(response)
        if response.status_code == 202:
            return StartResult("running", body)
        if response.status_code == 404:
            return StartResult("script_not_found", body)
        if response.status_code == 409:
            return StartResult("locked", body)
        if response.status_code == 200:
            return StartResult("running" if body.get("status") == "running" else "finished", body)
        raise EndpointUnavailable(f"start run answered HTTP {response.status_code}")

    async def get_run(self, machine, run_id: str) -> Optional[dict]:
        response = await self._request(machine, "GET", f"/runs/{run_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise EndpointUnavailable(f"run status answered HTTP {response.status_code}")
        return self._json(response)

    async def kill(self, machine, run_id: str) -> None:
        await self._request(machine, "POST", f"/runs/{run_id}/kill")

from __future__ import annotations

import json
import time
from typing import Callable

import httpx

from _scheduler.exec_key import exec_key


class FakeEndpoint:

    def __init__(self, machine_id: int = 1, machine_ip: str = "127.0.0.1", clock: Callable[[], float] = time.time):
        self.machine_id = machine_id
        self.machine_ip = machine_ip
        self.clock = clock
        self.scripts: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.down = False
        self.forbid = False
        self.reject_keys = False
        self.rejections: dict[str, dict] = {}
        self.check_extra: dict[str, dict] = {}

    def add_script(self, script: str, exit_code: int = 0, polls: int = 0, stdout: str = "", stderr: str = "", final: str = "finished", sequence: list | None = None,
                   script_path: str | None = None, interpreter: str | None = None):
        self.scripts[script.lower()] = {
            "exit_code": exit_code, "polls": polls, "stdout": stdout, "stderr": stderr, "final": final,
            "sequence": list(sequence or []), "script_path": script_path, "interpreter": interpreter,
        }

    def finish(self, run_id: str, exit_code: int = 0, status: str = "finished") -> None:
        run = self.runs[run_id]
        run.update(status=status, exit_code=exit_code, ended_at="2026-03-01T12:00:05Z", duration_ms=5000)

    def _valid_keys(self) -> set[str]:
        now = self.clock()
        return {exec_key(self.machine_id, self.machine_ip, now), exec_key(self.machine_id, self.machine_ip, now - 900)}

    @staticmethod
    def _json(status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, json=body)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("connection refused")
        if self.forbid:
            return self._json(403, {"error": "forbidden_ip"})
        if self.reject_keys or request.headers.get("x-exec-key") not in self._valid_keys():
            return self._json(401, {"error": "auth_rejected"})

        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if request.method == "GET" and path == "/health":
            running = [r for r, v in self.runs.items() if v["status"] == "running"]
            return self._json(200, {"ok": True, "machine_id": self.machine_id, "running": running, "time_utc": "2026-03-01T12:00:00Z"})
        if request.method == "POST" and path == "/scripts/check":
            key = body["script"].lower()
            known = key in self.scripts
            out = {"exists": known, "is_file": known}
            if known and self.scripts[key]["script_path"]:
                out["resolved"] = self.scripts[key]["script_path"]
            if key in self.rejections:
                out.update(self.rejections[key])
            out.update(self.check_extra.get(key, {}))
            out["unexpected_extra"] = {"future": True}
            return self._json(200, out)
        if request.method == "POST" and path == "/runs":
            return self._start(body)
        if request.method == "GET" and path.startswith("/runs/"):
            return self._status(path.split("/")[2])
        if request.method == "POST" and path.endswith("/kill"):
            run_id = path.split("/")[2]
            if run_id in self.runs:
                self.finish(run_id, -1, "killed")
            return self._json(200, {"status": "killed"})
        return self._json(404, {"error": "not_found"})

    def _view(self, run_id: str) -> dict:
        run = self.runs[run_id]
        return {"run_id": run_id, **{k: v for k, v in run.items() if k not in ("script", "polls_seen", "plan")}}

    def _start(self, body: dict) -> httpx.Response:
        run_id, script = body["run_id"], body["script"]
        if run_id in self.runs:
            return self._json(200, self._view(run_id))
        config = self.scripts.get(script.lower())
        if script.lower() in self.rejections or config is None:
            return self._json(404, {"status": "script_not_found", **self.rejections.get(script.lower(), {})})
        for other_id, other in self.runs.items():
            if other["status"] == "running" and other["script"] == script.lower():
                return self._json(409, {"status": "locked", "locked_by": other_id})
        plan = config["sequence"].pop(0) if config["sequence"] else config["exit_code"]
        self.runs[run_id] = {
            "script": script.lower(), "status": "running", "exit_code": None, "started_at": "2026-03-01T12:00:00Z",
            "ended_at": None, "duration_ms": None, "stdout_tail": config["stdout"], "stderr_tail": config["stderr"],
            "polls_seen": 0, "plan": plan, "script_path": config["script_path"], "interpreter": config["interpreter"], "future_field": 1, "args": body.get("args"), "timeout_s": body.get("timeout_s"),
        }
        return self._json(202, {"status": "running"})

    def _status(self, run_id: str) -> httpx.Response:
        run = self.runs.get(run_id)
        if run is None:
            return self._json(404, {"status": "unknown"})
        config = self.scripts[run["script"]]
        if run["status"] == "running" and config["final"] != "hang":
            run["polls_seen"] += 1
            if run["polls_seen"] > config["polls"]:
                self.finish(run_id, run["plan"], config["final"])
        return self._json(200, self._view(run_id))

from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from typing import Callable, Optional, TextIO

SEPARATOR = " · "
ELLIPSIS = "…"


def safe_text(text: str, encoding: Optional[str]) -> str:
    try:
        text.encode(encoding or "ascii")
        return text
    except (UnicodeEncodeError, LookupError):
        return text.replace(SEPARATOR, " | ").replace(ELLIPSIS, "...")


class StatusLine:
    def __init__(self, stream: TextIO, width: Callable[[], int] = lambda: shutil.get_terminal_size((120, 24)).columns):
        self._stream = stream
        self._width = width
        self._last = ""
        self._printed_len = 0

    def _fit(self, text: str) -> str:
        text = safe_text(text, getattr(self._stream, "encoding", None))
        limit = max(10, self._width() - 1)
        return text if len(text) <= limit else text[: limit - 1] + "~"

    def update(self, text: str) -> bool:
        text = self._fit(text)
        if text == self._last:
            return False
        self._write(text)
        return True

    def _write(self, text: str) -> None:
        padding = " " * max(0, self._printed_len - len(text))
        self._stream.write("\r" + text + padding)
        self._stream.flush()
        self._last = text
        self._printed_len = len(text)

    def clear(self) -> None:
        if self._printed_len:
            self._stream.write("\r" + " " * self._printed_len + "\r")
            self._stream.flush()
        self._last = ""
        self._printed_len = 0

    def finish(self, text: str) -> None:
        text = self._fit(text)
        padding = " " * max(0, self._printed_len - len(text))
        self._stream.write("\r" + text + padding + "\n")
        self._stream.flush()
        self._last = text
        self._printed_len = 0


def _online_count(state) -> tuple[int, int]:
    config = state.config_store.get()
    online = sum(
        1 for m in config.machines
        if (runtime := state.machine_registry.get(m.machine_id)) is not None and runtime.status == "online"
    )
    return online, len(config.machines)


def _alert_count(state) -> int:
    return sum(
        len(getattr(state, name))
        for name in ("machine_alert_incidents", "pipeline_alert_incidents", "schedule_incidents", "scheduler_endpoint_incidents")
        if hasattr(state, name)
    )


def build_status(state, stopping: bool = False) -> str:
    config = state.config_store.get()
    latest = state.estate_loop.latest
    online, total = _online_count(state)
    schedules = state.schedule_store.list()
    running = sum(1 for s in schedules if state.scheduler_loop.is_running(s.id))
    last_tick = getattr(state.estate_loop, "last_tick_at", None)

    if stopping:
        word = f"stopping{ELLIPSIS}"
    elif latest is None:
        word = f"starting{ELLIPSIS}"
    else:
        level = (latest.get("summary") or {}).get("level")
        reasons = []
        if level not in (None, "good"):
            reasons.append(f"summary {level}")
        if state.scheduler_loop.lock_attempted and not state.scheduler_loop.enabled:
            reasons.append("scheduler off: another instance holds the lock")
        word = f"DEGRADED ({'; '.join(reasons)})" if reasons else "healthy and running"

    cycle = last_tick.strftime("%H:%M:%SZ") if last_tick else "-"
    return SEPARATOR.join([
        f"Relay {word}",
        f":{config.settings.port}",
        f"machines {online}/{total} online",
        f"alerts {_alert_count(state)}",
        f"schedules {len(schedules)} ({running} running)",
        f"last cycle {cycle}",
    ])


def stopped_text(now: Optional[datetime] = None) -> str:
    return f"Relay stopped ({(now or datetime.now(timezone.utc)).strftime('%H:%M:%SZ')})"


class StatusReporter:
    def __init__(self, state, line: StatusLine, should_stop: Callable[[], bool], interval: float = 1.0):
        self._state = state
        self._line = line
        self._should_stop = should_stop
        self._interval = interval
        self._halt = threading.Event()
        self._thread = threading.Thread(target=self._run, name="relay-status", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        self._thread.join(timeout=2)

    def tick(self) -> None:
        try:
            self._line.update(build_status(self._state, stopping=self._should_stop()))
        except Exception:
            pass

    def _run(self) -> None:
        while not self._halt.is_set():
            self.tick()
            self._halt.wait(self._interval)

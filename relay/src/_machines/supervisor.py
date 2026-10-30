from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import queue as queue_module
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from _machines import child_main
from _machines.registry import MachineRegistry

if TYPE_CHECKING:
    from _contract.schema import Machine

logger = logging.getLogger("relay.machines.supervisor")

_RESTART_BACKOFF_SECONDS = (1, 2, 5, 10, 30)


@dataclass
class _ChildHandle:
    machine_id: int
    process: "mp.process.BaseProcess"
    to_child: "mp.Queue"
    from_child: "mp.Queue"
    crash_count: int = 0
    last_spawn_monotonic: float = 0.0


class Supervisor:
    def __init__(
        self,
        config_path: str,
        registry: MachineRegistry,
        on_machine_offline: Optional[Callable[[int], None]] = None,
        mp_context: Optional["mp.context.BaseContext"] = None,
        now: Callable[[], float] = time.monotonic,
        log_queue=None,
    ):
        self._config_path = str(config_path)
        self._registry = registry
        self._on_machine_offline = on_machine_offline
        self._mp = mp_context or mp.get_context("spawn")
        self._now = now
        self._log_queue = log_queue
        self._children: dict[int, _ChildHandle] = {}

    def is_running(self, machine_id: int) -> bool:
        handle = self._children.get(machine_id)
        return handle is not None and handle.process.is_alive()

    def running_machine_ids(self) -> set[int]:
        return {mid for mid, h in self._children.items() if h.process.is_alive()}


    def spawn(self, machine: "Machine") -> None:
        if self.is_running(machine.machine_id):
            return
        to_child: "mp.Queue" = self._mp.Queue()
        from_child: "mp.Queue" = self._mp.Queue()
        process = self._mp.Process(
            target=child_main.run,
            args=(machine.machine_id, self._config_path, to_child, from_child, self._log_queue),
            name=f"relay-machine-{machine.machine_id}",
            daemon=True,
        )
        process.start()
        self._children[machine.machine_id] = _ChildHandle(
            machine_id=machine.machine_id, process=process, to_child=to_child, from_child=from_child,
            last_spawn_monotonic=self._now(),
        )
        logger.info("spawned child process for machine %s (pid=%s)", machine.machine_id, process.pid)

    def kill(self, machine_id: int) -> None:
        handle = self._children.pop(machine_id, None)
        if handle is not None:
            self._terminate(handle)
            logger.info("killed child process for machine %s", machine_id)

        if self._on_machine_offline is not None:
            self._on_machine_offline(machine_id)

        state = self._registry.get(machine_id)
        if state is not None:
            state.active_services = []
            self._close_connections(state)

    def _close_connections(self, state) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        for close_cb in list(state.connections.values()):
            if loop is not None:
                loop.create_task(close_cb())
            else:
                try:
                    asyncio.run(close_cb())
                except (ConnectionError, OSError, RuntimeError) as exc:
                    logger.info("closing a ws connection for machine %s: already gone (%s)", state.machine_id, exc)
                except Exception:
                    logger.exception("error closing a ws connection for machine %s", state.machine_id)

    def _terminate(self, handle: _ChildHandle) -> None:
        try:
            handle.process.terminate()
            handle.process.join(timeout=2.0)
            if handle.process.is_alive():
                handle.process.kill()
                handle.process.join(timeout=2.0)
        except Exception:
            logger.exception("error terminating child process for machine %s", handle.machine_id)
        finally:
            for q in (handle.to_child, handle.from_child):
                try:
                    q.close()
                except Exception:
                    pass

    def shutdown_all(self, grace_seconds: float = 1.0) -> None:
        handles = list(self._children.values())
        self._children.clear()
        for handle in handles:
            try:
                handle.to_child.put({"type": "stop"})
            except Exception:
                pass
        deadline = time.monotonic() + grace_seconds
        for handle in handles:
            handle.process.join(timeout=max(0.0, deadline - time.monotonic()))
        stragglers = [h for h in handles if h.process.is_alive()]
        for handle in stragglers:
            try:
                handle.process.terminate()
            except Exception:
                logger.exception("error terminating child process for machine %s", handle.machine_id)
        for handle in stragglers:
            handle.process.join(timeout=1.0)
            if handle.process.is_alive():
                handle.process.kill()
                handle.process.join(timeout=1.0)
        for handle in handles:
            for q in (handle.to_child, handle.from_child):
                try:
                    q.cancel_join_thread()
                    q.close()
                except Exception:
                    pass


    def check_crashes(self, machines_by_id: dict[int, "Machine"]) -> None:
        for machine_id, handle in list(self._children.items()):
            if handle.process.is_alive():
                continue
            machine = machines_by_id.get(machine_id)
            del self._children[machine_id]
            for q in (handle.to_child, handle.from_child):
                try:
                    q.close()
                except Exception:
                    pass
            if machine is None:
                continue
            backoff_index = min(handle.crash_count, len(_RESTART_BACKOFF_SECONDS) - 1)
            backoff = _RESTART_BACKOFF_SECONDS[backoff_index]
            if self._now() - handle.last_spawn_monotonic < backoff:
                continue
            logger.warning("child process for machine %s crashed, restarting (backoff=%ss)", machine_id, backoff)
            self.spawn(machine)
            restarted = self._children.get(machine_id)
            if restarted is not None:
                restarted.crash_count = handle.crash_count + 1


    def drain(self) -> None:
        for handle in self._children.values():
            self._drain_one(handle)

    def _drain_one(self, handle: _ChildHandle) -> None:
        state = self._registry.state_for(handle.machine_id)
        while True:
            try:
                msg = handle.from_child.get_nowait()
            except queue_module.Empty:
                return
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "active_services":
                services = msg.get("active_services", [])
                state.active_services = services
                if services:
                    state.last_known_services = services

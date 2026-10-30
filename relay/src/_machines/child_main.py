from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
import os
import queue
from datetime import datetime, timezone

from _lib.config import ConfigStore
from _metrics.down_confirm import DownConfirm
from _metrics.readability import ReadStatus

logger = logging.getLogger("relay.machines.child")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def probe_all_nodes(machine, settings, down_confirm: DownConfirm) -> list[dict]:
    from _machines.node_probe import probe_node

    async def _one(node):
        raw = await probe_node(machine, node, settings)
        confirmed = down_confirm.observe(f"node:{machine.machine_id}:{node.node_id}", raw)
        return {
            "node_id": node.node_id,
            "node_name": node.node_name,
            "node_type": node.node_type,
            "node_platform": node.node_platform,
            "status": confirmed.value,
            "last_probe_ts": _now_iso(),
        }

    results = await asyncio.gather(*(_one(n) for n in machine.nodes), return_exceptions=True)
    out: list[dict] = []
    for node, result in zip(machine.nodes, results):
        if isinstance(result, BaseException):
            out.append({
                "node_id": node.node_id, "node_name": node.node_name, "node_type": node.node_type,
                "node_platform": node.node_platform, "status": ReadStatus.UNREAD.value, "last_probe_ts": _now_iso(),
            })
        else:
            out.append(result)
    return out


async def _run_async(machine_id: int, config_path: str, to_child: "queue.Queue", from_child: "queue.Queue") -> None:
    store = ConfigStore(config_path)
    try:
        store.load_startup()
    except Exception as exc:  # pragma: no cover
        from_child.put({"type": "child_error", "machine_id": machine_id, "error": str(exc)})
        return

    down_confirm = DownConfirm.from_settings(store.get().settings)

    parent = mp.parent_process()

    while True:
        if parent is not None and not parent.is_alive():
            logger.warning(
                "machine %s child: parent process %s is gone -- exiting (parent-death watchdog)",
                machine_id, parent.pid,
            )
            return

        try:
            msg = to_child.get_nowait()
            if isinstance(msg, dict) and msg.get("type") == "stop":
                return
        except queue.Empty:
            pass

        config = store.get()
        machine = next((m for m in config.machines if m.machine_id == machine_id), None)
        if machine is None:
            from_child.put({"type": "removed", "machine_id": machine_id})
            return

        active_services = await probe_all_nodes(machine, config.settings, down_confirm)
        from_child.put({
            "type": "active_services", "machine_id": machine_id, "active_services": active_services, "ts": _now_iso(),
        })

        await asyncio.sleep(max(0.1, config.settings.node_poll_seconds))


def run(machine_id: int, config_path: str, to_child: "queue.Queue", from_child: "queue.Queue", log_queue=None) -> None:
    if log_queue is not None:
        from _lib.file_logging import configure_child_logging

        configure_child_logging(log_queue)
        logger.info("machine %s child started (pid=%s)", machine_id, os.getpid())
    try:
        asyncio.run(_run_async(machine_id, config_path, to_child, from_child))
    except KeyboardInterrupt:
        return
    except Exception:
        if log_queue is None:
            raise
        logger.exception("machine %s child crashed", machine_id)

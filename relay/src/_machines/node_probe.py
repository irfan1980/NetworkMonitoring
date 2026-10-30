from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Optional

from _db.factory import build_driver
from _lib.expected_errors import describe_driver_error
from _lib.transition_log import TransitionLogger
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import Machine, MachineNode, Settings

logger = logging.getLogger("relay.machines.node_probe")
_probe_transition_log = TransitionLogger()


def _synth_database_server_entry(machine: "Machine", node: "MachineNode"):
    from _contract.schema import ServerEntry

    cfg = node.node_config
    host = cfg.host or machine.machine_ip
    return ServerEntry(
        id=_synthetic_id(node.node_id),
        name=node.node_name or node.node_id,
        platform=node.node_platform,  # type: ignore[arg-type]
        host=host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        schema=cfg.schema_name,
        timeout_ms=cfg.timeout_ms,
        driver=cfg.driver,
    )


def _synthetic_id(node_id: str) -> int:
    return abs(hash(node_id)) % 1_000_000_000


_CONN_STRING_KV = re.compile(r"([^=;]+)=([^;]*)")


def _parse_pos_connection_string(connection_string: str) -> Optional[dict]:
    fields = {k.strip().lower(): v.strip() for k, v in _CONN_STRING_KV.findall(connection_string)}
    server = fields.get("server") or fields.get("data source")
    database = fields.get("database") or fields.get("initial catalog")
    user = fields.get("user id") or fields.get("uid")
    password = fields.get("password") or fields.get("pwd")
    if not server or not database or not user or password is None:
        return None
    if "," in server:
        host, port_s = server.split(",", 1)
        try:
            port = int(port_s.strip())
        except ValueError:
            port = 1433
    else:
        host, port = server.strip(), 1433
    return {"host": host, "port": port, "user": user, "password": password, "database": database}


def _synth_pos_server_entry(node: "MachineNode"):
    from _contract.schema import ServerEntry

    cfg = node.node_config
    parsed = _parse_pos_connection_string(cfg.connection_string)
    if parsed is None:
        return None
    return ServerEntry(id=_synthetic_id(node.node_id), name=node.node_name or node.node_id, platform="mssql", **parsed)


async def probe_node(machine: "Machine", node: "MachineNode", settings: "Settings") -> ReadStatus:
    label = f"machine {machine.machine_id} node {node.node_id}"
    platform = "mssql" if node.node_type == "POS" else (node.node_platform if node.node_type == "Database" else None)
    try:
        if node.node_type == "Database":
            driver = build_driver(_synth_database_server_entry(machine, node), settings)
            status = await driver.check_chain()

        elif node.node_type == "POS":
            server = _synth_pos_server_entry(node)
            if server is None:
                return ReadStatus.UNREAD
            driver = build_driver(server, settings)
            status = await driver.check_chain()
        else:
            return ReadStatus.UNREAD
    except Exception as exc:
        is_expected, message = describe_driver_error(exc, platform, label) if platform else (False, str(exc))
        if is_expected:
            _probe_transition_log.down(logger, label, f"{label}: {message}")
        else:
            logger.error("node_probe: %s raised unexpectedly", label, exc_info=True)
        return ReadStatus.UNREAD

    _probe_transition_log.ok(logger, label, label)
    return status

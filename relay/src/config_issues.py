from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from _contract.schema import RelayConfig

IssueSeverity = Literal["info", "warning", "error"]


class ConfigIssue(BaseModel):
    code: str
    severity: IssueSeverity
    message: str
    where: Optional[dict] = None


def compute_config_issues(config: RelayConfig, schedules=None, state_dir=None) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []

    bus_ids = {b.id for b in config.service_busses}
    used_bus_ids: set[int] = set()

    for server in config.servers:
        used_bus_ids.update(server.used_buses)
        if not server.used_buses:
            issues.append(
                ConfigIssue(
                    code="server_unused_by_any_bus",
                    severity="info",
                    message=f"server {server.id} ({server.name!r}) has an empty "
                    "used_buses — it is configured but not attached to any bus.",
                    where={"serverId": server.id},
                )
            )

    seen_databases: dict[tuple, int] = {}
    for server in config.servers:
        key = (server.platform, server.host.lower(), server.port, server.database.lower(), (server.schema_name or "").lower())
        first = seen_databases.setdefault(key, server.id)
        if first != server.id:
            issues.append(
                ConfigIssue(
                    code="servers_share_one_database",
                    severity="warning",
                    message=f"servers {first} and {server.id} point at the same database "
                    f"({server.host}:{server.port}/{server.database}); their data is counted twice.",
                    where={"serverIds": [first, server.id]},
                )
            )

    for bus in config.service_busses:
        if bus.id not in used_bus_ids:
            issues.append(
                ConfigIssue(
                    code="service_bus_unused_by_any_server",
                    severity="info",
                    message=f"service_bus {bus.id} ({bus.name!r}) is not referenced "
                    "by any server's used_buses.",
                    where={"busId": bus.id},
                )
            )

    from _push.machine_alert import parse_machine_alert

    for machine in config.machines:
        for problem in parse_machine_alert(machine.machine_alert)[1]:
            issues.append(
                ConfigIssue(
                    code="machine_alert_invalid",
                    severity="warning",
                    message=f"machine {machine.machine_label_id} ({machine.machine_name!r}): machine_alert {problem}; "
                    "the default push timing is used for it.",
                    where={"machineId": machine.machine_label_id, "entity": f"machine:{machine.machine_label_id}"},
                )
            )

    push = config.settings.push
    if push.enabled:
        from _push.paths import resolve_push_path

        if not resolve_push_path(push.service_account_file).is_file():
            issues.append(
                ConfigIssue(
                    code="push_service_account_missing",
                    severity="warning",
                    message=f"push is enabled but the Firebase service-account key {push.service_account_file!r} "
                    "does not exist; no notifications will be sent.",
                    where={"entity": "settings.push.service_account_file"},
                )
            )
        if state_dir is not None:
            from _push.accounts import AccountStore
            from _state.database import Database

            if AccountStore(Database.for_dir(state_dir)).count() == 0:
                issues.append(
                    ConfigIssue(
                        code="push_no_accounts",
                        severity="info",
                        message="push is enabled but the user_accounts table is empty; nobody can log in or register a device "
                        "until an account is added (see relay/README.md).",
                        where={"entity": "user_accounts"},
                    )
                )

    for name, value in (
        ("bus", config.alerts.object_severities.bus),
        ("db", config.alerts.object_severities.db),
        ("machine", config.alerts.object_severities.machine),
        ("schedule", config.alerts.object_severities.schedule),
        ("scheduler_endpoint", config.alerts.object_severities.scheduler_endpoint),
    ):
        if not (1 <= value <= 5):
            issues.append(
                ConfigIssue(
                    code="object_severity_out_of_range",
                    severity="warning",
                    message=f"alerts.object_severities.{name} = {value} is outside "
                    "the documented 1-5 severity range.",
                    where={"entity": f"object_severities.{name}"},
                )
            )

    machines_by_id = {m.machine_id: m for m in config.machines}
    for schedule in schedules or []:
        machine = machines_by_id.get(schedule.machine_id)
        where = {"scheduleId": schedule.id, "machineId": schedule.machine_id}
        if machine is None:
            issues.append(
                ConfigIssue(
                    code="schedule_machine_missing",
                    severity="warning",
                    message=f"schedule {schedule.id} ({schedule.name!r}) targets machine "
                    f"{schedule.machine_id}, which is not in the config.",
                    where=where,
                )
            )
        elif not machine.machine_scheduler:
            issues.append(
                ConfigIssue(
                    code="schedule_machine_scheduler_off",
                    severity="warning",
                    message=f"schedule {schedule.id} ({schedule.name!r}) targets machine "
                    f"{schedule.machine_id}, which has machine_scheduler false; it will not run.",
                    where=where,
                )
            )

    return issues


def _no(row: dict):
    return row.get("pipelineNo", row["pipelineId"])


def _enabled(row: dict) -> bool:
    return row.get("_isEnabled") not in (0, False, "0")


def orphan_pipeline_ids(config: RelayConfig, pipelines: list[dict]) -> set:
    known = {s.id for s in config.servers}
    return {p["pipelineId"] for p in pipelines if _enabled(p) and _missing_server_ids(p, known)}


def _topic_problem(config: RelayConfig, row: dict) -> Optional[str]:
    topic = row.get("_queueOrTopic")
    host = row.get("_hostServerId")
    if not topic or host is None:
        return None
    server = next((s for s in config.servers if s.id == host), None)
    if server is None:
        return None
    topics = {b.id: b.topic for b in config.service_busses}
    used = [b for b in server.used_buses if b in topics]
    if topic in {topics[b] for b in used}:
        return None
    elsewhere = sorted(b for b, t in topics.items() if t == topic)
    if not elsewhere:
        return f"pipeline {_no(row)} uses topic '{topic}', which no configured service bus provides. Fix queue_or_topic or configure the bus."
    bus_word = f"bus {elsewhere[0]}" if len(elsewhere) == 1 else f"buses {', '.join(str(b) for b in elsewhere)}"
    if used:
        own = ", ".join(str(b) for b in used)
        own_topics = ", ".join(f"'{topics[b]}'" for b in used)
        uses = f"only uses {'bus' if len(used) == 1 else 'buses'} {own} (topic{'' if len(used) == 1 else 's'} {own_topics})"
    else:
        uses = "uses no bus"
    add = f"bus {elsewhere[0]}" if len(elsewhere) == 1 else f"one of buses {', '.join(str(b) for b in elsewhere)}"
    return (
        f"pipeline {_no(row)} uses topic '{topic}' ({bus_word}), but this service {uses}. "
        f"Add {add} to its used_buses or change queue_or_topic."
    )


def excluded_row_keys(config: RelayConfig, pipelines: list[dict]) -> set:
    known = {s.id for s in config.servers}
    out = set()
    for row in pipelines:
        if not _enabled(row) or _row_ends(row) is None:
            continue
        if _missing_server_ids(row, known) or is_foreign_row(row) or _topic_problem(config, row):
            out.add((row.get("_hostServerId"), row["pipelineId"]))
    return out


def _missing_server_ids(row: dict, known: set[int]) -> list[int]:
    missing = []
    for key in ("sourceSystem", "targetSystem"):
        try:
            sid = int(row.get(key))
        except (TypeError, ValueError):
            continue
        if sid not in known and sid not in missing:
            missing.append(sid)
    return missing


_ROLE_ORDER = {"outgoing": 0, "incoming": 1, "unowned": 2}


def _pipeline_names(config: RelayConfig, host) -> tuple[str, str]:
    server = next((x for x in config.servers if x.id == host), None)
    if server is not None and server.platform == "mssql":
        return "SyncPipeline", "SourceSystem/TargetSystem"
    return "sync_pipeline", "source_system/target_system"


def _host_label(config: RelayConfig, host) -> str:
    for machine in config.machines:
        for node in machine.nodes:
            if node.server_id == host:
                return f"{machine.machine_name} ({machine.machine_id}) → {node.node_name or node.node_id} ({host})"
    return f"server {host}"


def _row_ends(row: dict) -> Optional[tuple[int, int]]:
    try:
        return int(row.get("sourceSystem")), int(row.get("targetSystem"))
    except (TypeError, ValueError):
        return None


def is_foreign_row(row: dict) -> bool:
    ends = _row_ends(row)
    host = row.get("_hostServerId")
    return _enabled(row) and ends is not None and host is not None and host not in ends


def orphan_pipeline_issues(config: RelayConfig, pipelines: list[dict]) -> list[ConfigIssue]:
    known = {s.id for s in config.servers}
    missing_groups: dict[tuple, set] = {}
    foreign: list[tuple] = []
    topic_rows: list[tuple] = []
    for row in pipelines:
        if not _enabled(row):
            continue
        host = row.get("_hostServerId")
        ends = _row_ends(row)
        if ends is None:
            continue
        if is_foreign_row(row):
            foreign.append((str(host), _no(row), host, ends))
            continue
        src, tgt = ends
        missing_ids = _missing_server_ids(row, known)
        for missing in missing_ids:
            role = "outgoing" if host == src else "incoming" if host == tgt else "unowned"
            missing_groups.setdefault((host, missing, role), set()).add(_no(row))
        if not missing_ids:
            problem = _topic_problem(config, row)
            if problem:
                topic_rows.append((str(host), str(_no(row)), host, row, problem))

    issues = []
    for (host, missing, role), pids in sorted(missing_groups.items(), key=lambda kv: (str(kv[0][0]), kv[0][1], _ROLE_ORDER[kv[0][2]])):
        ids = ", ".join(str(i) for i in sorted(pids))
        plural = len(pids) > 1
        if role == "outgoing":
            what = f"outgoing pipeline{'s' if plural else ''} {ids} target{'' if plural else 's'}"
        elif role == "incoming":
            what = f"incoming pipeline{'s' if plural else ''} {ids} come{'' if plural else 's'} from"
        else:
            what = f"pipeline{'s' if plural else ''} {ids} reference{'' if plural else 's'}"
        issues.append(
            ConfigIssue(
                code="pipeline_references_unknown_server",
                severity="error",
                message=f"{_host_label(config, host)}: {what} server {missing}, which is not configured. "
                f"Remove {'them' if plural else 'it'} from {_pipeline_names(config, host)[0]} or add server {missing} to the config.",
                where={"entity": f"server:{host}", "serverId": str(host), "missingServerId": missing, "pipelineIds": sorted(pids)},
            )
        )
    for _, pid, host, (src, tgt) in sorted(foreign, key=lambda f: (f[0], str(f[1]))):
        issues.append(
            ConfigIssue(
                code="pipeline_names_other_server",
                severity="error",
                message=f"{_host_label(config, host)}: pipeline {pid} has source {src} and target {tgt} — "
                f"neither is this server ({host}). Fix {_pipeline_names(config, host)[1]} in {_pipeline_names(config, host)[0]}.",
                where={
                    "entity": f"server:{host}", "serverId": str(host), "pipelineIds": [pid],
                    "sourceServerId": src, "targetServerId": tgt,
                },
            )
        )
    for _, _pid, host, row, problem in sorted(topic_rows, key=lambda t: (t[0], t[1])):
        issues.append(
            ConfigIssue(
                code="pipeline_topic_not_on_used_bus",
                severity="error",
                message=f"{_host_label(config, host)}: {problem}",
                where={
                    "entity": f"server:{host}", "serverId": str(host), "pipelineIds": [_no(row)],
                    "topic": row.get("_queueOrTopic"),
                },
            )
        )
    return issues

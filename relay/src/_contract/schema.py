from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


class AuthSettings(BaseModel):
    web_client_id: Optional[str] = Field(None, description="Legacy label only; no longer grants access.")
    web_client_secret: Optional[str] = Field(None, description="Legacy; no longer grants access.")
    app_client_id: Optional[str] = Field(None, description="Legacy label only; no longer grants access.")
    app_client_secret: Optional[str] = Field(None, description="Legacy; no longer grants access.")
    session_days: float = Field(30, gt=0, description="Ignored. Sessions no longer expire; they end on logout, a dead push token or account removal.")


class TwilioDispatchSettings(BaseModel):
    account_sid: str = ""
    auth_token: str = ""
    sms_from: str = ""
    whatsapp_from: str = ""


class SmtpDispatchSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    host: str = ""
    port: int = 587
    user: str = ""
    password: str = ""
    from_address: str = Field("", alias="from")


class FcmDispatchSettings(BaseModel):
    service_account_file: str = ""
    tokens: list[str] = Field(default_factory=list)
    topic: Optional[str] = None


class DispatchRecipients(BaseModel):

    sms: list[str] = Field(default_factory=list)
    wa_message: list[str] = Field(default_factory=list)
    email: list[str] = Field(default_factory=list)
    push_notification: list[str] = Field(default_factory=list)


class DispatchSettings(BaseModel):
    twilio: TwilioDispatchSettings = Field(default_factory=TwilioDispatchSettings)
    smtp: SmtpDispatchSettings = Field(default_factory=SmtpDispatchSettings)
    fcm: FcmDispatchSettings = Field(default_factory=FcmDispatchSettings)
    recipients: DispatchRecipients = Field(default_factory=DispatchRecipients)


class PushSettings(BaseModel):
    enabled: bool = Field(False, description="Send FCM push notifications for active alerts.")
    accounts_file: str = Field("", description="Deprecated. Accounts live in the state database (table user_accounts). This path is read once, only to import an existing accounts file into an empty accounts table; empty means <state dir>/auth/push_accounts.json, relative paths resolve against relay/.")
    service_account_file: str = Field(
        "config/firebase-service-account.json", description="Firebase service-account key; relative paths resolve against relay/."
    )
    grace_seconds: float = Field(15, ge=0, description="Seconds an alert must stay active before its first push.")
    min_interval_seconds: float = Field(120, ge=0, description="Minimum seconds between two pushes of the same alert id.")
    repeat_minutes_unacknowledged: float = Field(15, gt=0, description="Minutes between repeat pushes of an active, unacknowledged alert.")
    repeat_minutes_acknowledged: float = Field(30, gt=0, description="Minutes between repeat pushes of an active, acknowledged alert.")
    dashboard_url: str = Field("", description="Dashboard base URL used for web-push links.")


class StateBackupSettings(BaseModel):
    enabled: bool = Field(True, description="Copy the state database once a day (UTC) and delete copies past retention_days.")
    folder: str = Field(
        "", description="Backup folder. Empty means <state dir>/backups; relative paths resolve against relay/."
    )
    retention_days: int = Field(
        30, ge=1, le=3650, description="Copies dated more than this many days ago are deleted after a successful copy. The newest copy is always kept."
    )
    retry_minutes: float = Field(30, gt=0, description="Minutes before a failed or missed copy is tried again, and between date checks.")


class Settings(BaseModel):
    bind_host: str = "0.0.0.0"
    port: int = 8085
    auth: AuthSettings

    poll_seconds: int = Field(5, description="Seconds between relay polls.")
    poll_hard_timeout_ms: int = Field(
        15000,
        description="Hard timeout for one poll cycle in milliseconds. Should not be set below max(poll_seconds * 3000, 15000).",
    )

    ping_seconds: int = Field(10, description="Seconds between pings.")
    ping_down_multiplier: int = Field(
        3, description="Multiples of ping_seconds without a response before a target is confirmed down."
    )
    ping_blocked_multiplier: int = Field(
        2, description="Multiples of ping_seconds without a response before a target is confirmed blocked by the relay."
    )
    machine_poll_seconds: int = Field(
        5, description="Machine TCP-liveness loop interval in seconds."
    )
    machine_poll_timeout_seconds: int = Field(
        2, description="Machine TCP-liveness probe timeout in seconds."
    )
    node_poll_seconds: int = Field(
        5, description="Per-node probe loop interval in seconds."
    )
    node_poll_timeout_seconds: int = Field(
        3, description="Per-node probe timeout in seconds."
    )
    config_force_reload_seconds: int = Field(
        900,
        description="Interval in seconds at which the config file is force-reloaded even if its modification time is unchanged.",
    )

    backlog_max: int = Field(100, description="Maximum tolerated backlog size.")
    lag_max: int = Field(60, description="Maximum tolerated lag in seconds.")

    per_server_timeout_ms: int = Field(
        4000,
        description="Timeout in milliseconds for a single query or stage within one server's poll.",
    )
    server_budget_ms: int = Field(
        15000,
        description="Total time budget in milliseconds for one server's whole poll.",
    )

    e2e_window_seconds: int = Field(150, description="End-to-end delivery window in seconds.")
    queued_max_age_seconds: int = Field(3600, description="Maximum age in seconds of a queued packet.")
    lost_quiet_seconds: int = Field(
        120, description="Seconds of silence after which a packet counts as lost."
    )

    pair_full_rescan_seconds: int = Field(60, description="Seconds between full pair rescans.")
    pair_inbox_ask_max: int = Field(2000, description="Maximum inbox rows requested per pair scan.")

    pending_yellow_threshold: int = Field(50, description="Pending count at which a pipeline turns yellow.")
    pending_red_threshold: int = Field(500, description="Pending count at which a pipeline turns red.")

    sound_min_oldest_packet_age_seconds: int = Field(
        60,
        description="Minimum age in seconds of the oldest packet before an alert sound plays.",
    )

    alert_repeat_minutes_sev1_2: int = Field(15, description="Minutes between repeated alerts for severity 1-2.")
    alert_repeat_hours_sev3_5: int = Field(4, description="Hours between repeated alerts for severity 3-5.")
    alert_escalate_after_repeats: int = Field(3, description="Number of repeats after which an alert escalates.")
    device_silence_minutes: int = Field(15, description="Minutes of silence before a device is considered silent.")
    bus_incident_cooldown_minutes: int = Field(5, description="Cooldown in minutes between incidents on the same bus.")

    bus_incident_stale_after_ms: int = Field(
        120000,
        description="Age in milliseconds after which a bus incident is considered stale.",
    )
    default_significant_pile: int = Field(
        500,
        description="Pending count treated as a significant pile-up. Independent of pending_red_threshold.",
    )

    overlap_seconds: int = Field(
        60,
        description="Clock-skew allowance in seconds added to e2e_window_seconds when deciding a pair is bus-attributed.",
    )

    max_full_rescan_backoff: int = Field(10, description="Maximum backoff multiplier between full rescans.")
    pin_rate_limit_attempts: int = Field(5, description="PIN attempts allowed per rate-limit window.")
    pin_rate_limit_window_seconds: int = Field(60, description="PIN rate-limit window in seconds.")
    pin_rate_limit_lockout_seconds: int = Field(60, description="PIN lockout duration in seconds after the attempt limit is reached.")

    unread_min_ms: int = Field(
        15000,
        description="Minimum time in milliseconds before the unread tracker reports. Independent of down_confirm_soft_min_ms.",
    )
    down_confirm_soft_min_ms: int = Field(
        15000,
        description="Minimum time in milliseconds before a soft down is confirmed. Independent of unread_min_ms.",
    )

    poll_stats_window_samples: int = Field(
        720, description="Number of poll-cycle duration samples kept in the rolling window."
    )
    triggers_cache_ms: int = Field(60000, description="Triggers cache lifetime in milliseconds.")
    amqp_timeout_ms: int = Field(2500, description="AMQP timeout in milliseconds.")
    emulator_timeout_ms: int = Field(3000, description="Emulator timeout in milliseconds.")
    query_timeout_ms_default: int = Field(
        4000, description="Query timeout in milliseconds for mysql, postgresql and the tedious mssql driver."
    )
    query_timeout_ms_odbc: int = Field(
        8000, description="Query timeout in milliseconds for the odbc mssql driver."
    )
    tcp_timeout_ms: int = Field(2500, description="TCP connect timeout in milliseconds.")
    probe_timeout_ms_odbc: int = Field(
        6000, description="Probe timeout in milliseconds for the odbc mssql driver."
    )
    auth_probe_interval_ms: int = Field(30000, description="Interval in milliseconds between authentication probes.")
    essentials_reserve_ms: int = Field(3000, description="Time in milliseconds reserved for essential queries within a poll budget.")

    inline_query_max_chars: int = Field(
        4000, description="Maximum inline query length for the odbc driver before a temp file is used."
    )
    mssql_in_chunk_size: int = Field(
        1000, description="Number of values per IN() chunk in mssql queries."
    )
    sqlcmd_max_buffer_bytes: int = Field(
        16 * 1024 * 1024, description="Maximum output buffer in bytes for the sqlcmd subprocess."
    )

    min_secret_length: int = Field(
        4, description="Minimum length of a secret for it to be redacted from logs."
    )
    material_backlog_factor: int = Field(
        2, description="Factor applied to the backlog threshold to decide a backlog is material."
    )
    atomic_write_retry_attempts: int = Field(
        5, description="Retry attempts for atomic JSON writes."
    )
    atomic_write_retry_backoff_ms_per_attempt: int = Field(
        10, description="Backoff in milliseconds per attempt, multiplied by the attempt number, for atomic JSON writes."
    )

    ack_pin: str = Field(..., min_length=1, description="PIN required to acknowledge alerts. No default because it is a credential.")
    dispatch: DispatchSettings = Field(default_factory=DispatchSettings)
    push: PushSettings = Field(default_factory=PushSettings)
    state_backup: StateBackupSettings = Field(default_factory=StateBackupSettings)
    log_retention_days: int = Field(
        30, ge=1, le=3650,
        description="Daily relay log files (<date>_info_log.log, <date>_error_log.log) dated more than this many days ago are deleted at startup and each rollover. Other files in the log folder are never touched.",
    )

    ws_allowed_origins: list[str] = Field(
        default_factory=list,
        description="Exact Origin header values allowed on top of the automatic LAN and loopback allow-list. Any other origin is refused at WebSocket upgrade (close 4403) and on every REST route (403 origin_forbidden). Requests with no Origin header are not affected. The name is kept for backward compatibility; it governs REST and CORS too.",
    )


DatabasePlatform = Literal["mysql", "postgresql", "mssql"]


class ServerEntry(BaseModel):

    id: int
    name: str
    platform: DatabasePlatform
    host: str
    port: int
    user: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    database: str
    schema_name: Optional[str] = Field(
        None,
        alias="schema",
        description="PostgreSQL schema name; defaults to 'sync' when unset. Ignored for other platforms.",
    )
    used_buses: list[int] = Field(default_factory=list, description="IDs of the service_busses entries this database publishes to.")
    timeout_ms: Optional[int] = Field(
        None, description="Per-server override of query_timeout_ms_default or query_timeout_ms_odbc."
    )
    driver: Literal["tedious", "odbc"] = Field(
        "tedious",
        description="MSSQL driver: 'tedious' uses the DB-API driver, 'odbc' uses the sqlcmd subprocess driver for legacy instances. Ignored for mysql and postgresql.",
    )

    model_config = ConfigDict(populate_by_name=True)


class ServiceBusEntry(BaseModel):

    id: int
    name: str
    health_url: str = Field(..., min_length=1)
    amqp_port: int
    topic: str = Field(..., min_length=1)
    host: Optional[str] = Field(
        None,
        description="Address the database services connect to. Used only by config_maker; the relay ignores it.",
    )
    sas_key_name: Optional[str] = Field(
        None, description="AMQP shared-access key name. Used only by config_maker; the relay ignores it."
    )
    sas_key: Optional[str] = Field(
        None, repr=False, description="AMQP shared-access key (secret). Used only by config_maker; the relay ignores it."
    )
    mssql_sa_password: Optional[str] = Field(
        None, repr=False, description="SA password of the emulator's SQL Server (secret). Used only by config_maker; the relay ignores it."
    )
    emulator_config_path: Optional[str] = Field(
        None, description="CONFIG_PATH written to the emulator env file. Used only by config_maker; the relay ignores it."
    )


class ObjectSeverities(BaseModel):

    bus: int = 1
    db: int = 2
    machine: int = 2
    schedule: int = 3
    scheduler_endpoint: int = 2


DispatchChannel = Literal["sms", "wa_message", "email", "push_notification"]


class AlertsSettings(BaseModel):
    severity_channels: dict[str, list[DispatchChannel]] = Field(
        default_factory=dict,
        description="Severity (1-5, string keys) to dispatch channel list. Restricted to the supported dispatch channels.",
    )
    escalated_channels: dict[str, list[DispatchChannel]] = Field(
        default_factory=dict,
        description="Severity (1-5, string keys) to dispatch channel list used after escalation. Same channel set as severity_channels.",
    )
    pending_thresholds: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description="Per-pipeline overrides keyed by pipeline id, shaped {yellow: int, red: int}, replacing pending_yellow_threshold and pending_red_threshold for that pipeline.",
    )
    object_severities: ObjectSeverities = Field(default_factory=ObjectSeverities)


class DatabaseNodeConfig(BaseModel):

    model_config = ConfigDict(populate_by_name=True)

    host: Optional[str] = Field(
        None,
        min_length=1,
        description="Database host. Defaults to the owning machine's machine_ip; required for a standalone services entry.",
    )
    port: int
    user: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    database: str
    schema_name: Optional[str] = Field(None, alias="schema", description="PostgreSQL schema name; defaults to 'sync' when unset. Ignored for other platforms.")
    used_buses: list[int] = Field(
        default_factory=list,
        description="IDs of the service_busses entries this database publishes to.",
    )
    timeout_ms: Optional[int] = Field(
        None, description="Per-server override of query_timeout_ms_default or query_timeout_ms_odbc."
    )
    driver: Literal["tedious", "odbc"] = Field(
        "tedious",
        description="MSSQL driver: 'tedious' uses the DB-API driver, 'odbc' uses the sqlcmd subprocess driver for legacy instances. Ignored for mysql and postgresql.",
    )
    service_name: Optional[str] = Field(
        None, description="Service name suffix (lowercase letters, digits, '-'). Used only by config_maker; the relay ignores it."
    )
    service_db_host: Optional[str] = Field(
        None, description="Database host the service connects to, when it differs from host. Used only by config_maker; the relay ignores it."
    )
    service_database_extra: Optional[dict] = Field(
        None, description="Extra keys merged into the service's database block. Used only by config_maker; the relay ignores it."
    )
    service_log_file: Optional[str] = Field(
        None, description="Log file path for the service. Used only by config_maker; the relay ignores it."
    )
    service_tuning: Optional[dict] = Field(
        None, description="Per-service tuning overrides. Used only by config_maker; the relay ignores it."
    )


class PosNodeConfig(BaseModel):

    db_type: Literal["mssql"] = "mssql"
    connection_string: str = Field(..., min_length=1)
    auth_type: Literal["sql_server_auth"] = "sql_server_auth"
    used_buses: list[int] = Field(default_factory=list)


NodeType = Literal["Database", "POS"]
_NODE_CONFIG_BY_TYPE: dict[str, type[BaseModel]] = {
    "Database": DatabaseNodeConfig,
    "POS": PosNodeConfig,
}


class MachineNode(BaseModel):

    node_id: str
    node_name: Optional[str] = None
    node_type: NodeType
    node_platform: str = Field(
        ...,
        description="Database or POS platform, e.g. 'mysql', 'postgresql', 'mssql', 'lightspeed'.",
    )
    node_config: Union[DatabaseNodeConfig, PosNodeConfig]
    server_id: int = Field(
        ...,
        description="Numeric server id, unique across machines[].nodes[] and services[] combined.",
    )

    @model_validator(mode="after")
    def _node_config_matches_node_type(self) -> "MachineNode":
        expected = _NODE_CONFIG_BY_TYPE[self.node_type]
        if not isinstance(self.node_config, expected):
            raise ValueError(
                f"node_type={self.node_type!r} requires node_config of type "
                f"{expected.__name__}, got {type(self.node_config).__name__}"
            )
        return self


def _check_ipv4(values, label: str):
    if values is None:
        return values
    import ipaddress

    for value in values:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"{label}: {value!r} is not an IPv4 address") from exc
    return values


class MachineSchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    fail_threshold: Optional[int] = Field(None, ge=1)
    timeout_minutes: Optional[float] = Field(None, ge=0, description="0 means no timeout.")
    retries: Optional[int] = Field(None, ge=0)
    retry_delay_seconds: Optional[float] = Field(None, ge=0)
    run_if_missed: Optional[bool] = None
    allowed_ips: Optional[list[str]] = Field(None, description="Machine-side setting (config_maker); the relay only validates it.")
    interpreters: Optional[dict[str, str]] = Field(None, description="Machine-side setting (config_maker); the relay only validates it.")

    @field_validator("allowed_ips")
    @classmethod
    def _ipv4_list(cls, v):
        return _check_ipv4(v, "machine_scheduler.allowed_ips")

    def overrides(self) -> Optional["MachineSchedulerConfig"]:
        stripped = self.model_copy(update={"allowed_ips": None, "interpreters": None})
        values = stripped.model_dump(exclude={"enabled"})
        return stripped if any(v is not None for v in values.values()) else None


class Machine(BaseModel):

    machine_id: int
    machine_label_id: Optional[str] = Field(
        None,
        description="machine_id as written in the config (e.g. '0001'), used for display. Defaults to str(machine_id).",
    )
    machine_ip: str = Field(..., min_length=1)
    machine_port: Optional[int] = Field(
        None,
        description="Optional OS-level TCP port (SSH/SMB/RDP) used as a fallback when the ICMP liveness probe does not confirm reachability.",
    )
    machine_name: str
    machine_type: str = Field(
        ...,
        description="Machine type, e.g. 'LOCAL'.",
    )
    machine_os: str
    machine_scheduler: bool = Field(
        False,
        description="True when this machine hosts the scheduler endpoint and may be a target of schedules. "
        "The config accepts a bool or an object {enabled, fail_threshold?, timeout_minutes?, retries?, retry_delay_seconds?, run_if_missed?}.",
    )
    scheduler_defaults: Optional[MachineSchedulerConfig] = Field(
        None, description="Machine-level schedule defaults from the object form of machine_scheduler."
    )
    machine_alert: Any = Field(
        None,
        description="Per-machine push settings {enabled, alert_report_interval, alert_repeat_interval, "
        "alert_acknoledge_repeat_interval} in seconds. Kept raw: bad values become a config issue and fall back to defaults.",
    )
    nodes: list[MachineNode] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _keep_label_id(cls, data):
        if isinstance(data, dict) and isinstance(data.get("machine_scheduler"), dict):
            cfg = MachineSchedulerConfig.model_validate(data["machine_scheduler"])
            data = {**data, "machine_scheduler": cfg.enabled, "scheduler_defaults": cfg.overrides()}
        if isinstance(data, dict) and data.get("machine_label_id") is None and data.get("machine_id") is not None:
            data = {**data, "machine_label_id": str(data["machine_id"])}
        return data

    @field_validator("nodes")
    @classmethod
    def _no_bus_nodes(cls, v: list[MachineNode]) -> list[MachineNode]:
        for node in v:
            if getattr(node, "node_type", None) == "ServiceBus":  # pragma: no cover
                raise ValueError(
                    f"machine node {node.node_id!r}: node_type='ServiceBus' is not "
                    "allowed under machines[].nodes[] -- a Service Bus is independent "
                    "infrastructure, never owned by a machine; add it to the top-level "
                    "service_busses[] instead (referenced by id via used_buses)"
                )
        return v


class SchedulerSettings(BaseModel):
    executor_port: int = Field(8095, description="Port of the scheduler endpoint on every machine_scheduler machine.")
    relay_ip: str = Field("10.101.8.85", description="Relay source IP the machine endpoints allow. Used only by config_maker.")
    tick_seconds: float = Field(1, gt=0, description="Seconds between scheduler ticks.")
    health_probe_seconds: float = Field(5, gt=0, description="Seconds between endpoint health probes.")
    health_down_after_misses: int = Field(2, ge=1, description="Consecutive missed probes before an endpoint is down.")
    default_timeout_minutes: float = Field(0, ge=0, description="Run timeout in minutes when neither schedule nor machine sets one. 0 means no timeout.")
    default_retries: int = Field(0, ge=0, description="Retries after a failed attempt when a schedule sets none.")
    default_retry_delay_seconds: float = Field(60, ge=0, description="Wait between attempts when a schedule sets none.")
    default_fail_threshold: int = Field(3, ge=1, description="Consecutive failed runs that raise the schedule alert.")
    default_run_if_missed: bool = Field(False, description="Run once after a relay restart when a fire time was missed.")
    success_exit_codes: list[int] = Field(default_factory=lambda: [0])
    log_keep_days: int = Field(90, ge=0, description="Run log entries newer than this are kept.")
    log_keep_runs: int = Field(1000, ge=0, description="Newest run log entries per schedule that are kept. The larger retention of days or runs wins.")
    log_output_tail_kb: int = Field(64, ge=1, description="Cap on stored stdout and stderr per run.")
    log_max_age_days: int = Field(
        30, ge=1, le=3650,
        description="Finished runs whose end time is more than this many days ago are deleted from the run history at startup and "
        "once a day, whatever log_keep_runs says. Unfinished or undated rows and the newest row of each schedule are kept.",
    )
    extra_allowed_ips: list[str] = Field(
        default_factory=list, description="Machine-side setting (config_maker); the relay only validates it."
    )

    @field_validator("extra_allowed_ips")
    @classmethod
    def _ipv4_list(cls, v):
        return _check_ipv4(v, "scheduler.extra_allowed_ips")


class RelayConfig(BaseModel):

    settings: Settings
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    alerts: AlertsSettings = Field(default_factory=AlertsSettings)
    service_busses: list[ServiceBusEntry] = Field(default_factory=list)
    services: list[MachineNode] = Field(default_factory=list)
    machines: list[Machine] = Field(default_factory=list)

    _servers_cache: list[ServerEntry] = PrivateAttr(default_factory=list)

    @property
    def servers(self) -> list[ServerEntry]:
        return self._servers_cache

    @field_validator("machines")
    @classmethod
    def _unique_machine_ids(cls, v: list[Machine]) -> list[Machine]:
        ids = [m.machine_id for m in v]
        if len(ids) != len(set(ids)):
            raise ValueError("machines[].machine_id must be unique")
        return v

    @field_validator("service_busses")
    @classmethod
    def _unique_bus_ids(cls, v: list[ServiceBusEntry]) -> list[ServiceBusEntry]:
        ids = [b.id for b in v]
        if len(ids) != len(set(ids)):
            raise ValueError("service_busses[].id must be unique")
        return v

    @model_validator(mode="after")
    def _derive_servers(self) -> "RelayConfig":
        servers: list[ServerEntry] = []
        seen_server_ids: set[int] = set()
        bus_ids = {b.id for b in self.service_busses}

        def _add_node(
            node: "MachineNode", *, owner_label: str, machine: Optional["Machine"] = None
        ) -> None:
            if node.server_id in seen_server_ids:
                raise ValueError(
                    f"duplicate server_id {node.server_id} ({owner_label} node "
                    f"{node.node_id!r}) — server_id must be unique among all "
                    "Database/POS nodes (machines[].nodes[] and services[] combined)"
                )
            seen_server_ids.add(node.server_id)
            if node.node_type == "Database":
                cfg = node.node_config
                host = cfg.host or (machine.machine_ip if machine is not None else None)
                if not host:
                    raise ValueError(
                        f"node {node.node_id!r} ({owner_label}): node_config.host is "
                        "required -- it has no owning machine to inherit machine_ip from"
                    )
                servers.append(
                    ServerEntry(
                        id=node.server_id,
                        name=node.node_name or node.node_id,
                        platform=node.node_platform,  # type: ignore[arg-type]
                        host=host,
                        port=cfg.port,
                        user=cfg.user,
                        password=cfg.password,
                        database=cfg.database,
                        schema=cfg.schema_name,
                        used_buses=cfg.used_buses,
                        timeout_ms=cfg.timeout_ms,
                        driver=cfg.driver,
                    )
                )

        for machine in self.machines:
            node_ids = [n.node_id for n in machine.nodes]
            if len(node_ids) != len(set(node_ids)):
                raise ValueError(
                    f"machine {machine.machine_id} ({machine.machine_name!r}) has "
                    "duplicate node_id values among its nodes"
                )
            for node in machine.nodes:
                _add_node(node, owner_label=f"machine {machine.machine_id}", machine=machine)

        service_ids = [n.node_id for n in self.services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("services[] has duplicate node_id values")
        for node in self.services:
            _add_node(node, owner_label="standalone service")

        all_database_pos_nodes = [n for m in self.machines for n in m.nodes] + list(self.services)
        for node in all_database_pos_nodes:
            for bus_id in node.node_config.used_buses:
                if bus_id not in bus_ids:
                    raise ValueError(
                        f"node {node.node_id!r} used_buses references unknown "
                        f"service_busses[].id {bus_id}"
                    )

        self._servers_cache = servers
        return self

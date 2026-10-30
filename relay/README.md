# Relay

Single Python process (FastAPI + uvicorn) that polls the databases and Service Bus emulators named in one config
file, computes health, pairs and alerts, and serves the dashboard's data over one port (default 8085): a WebSocket
estate feed, per-machine feeds, and a small HTTP API. Read-only against the databases and buses. It also runs the
central scheduler: it keeps the clock for schedules defined in the dashboard and triggers scripts on machines with
`machine_scheduler` on, through each machine's scheduler endpoint (`docs/scheduler/_index.md`). It does not serve the
dashboard's web build (`dashboard/dist`); host that separately.

Reference docs: `docs/relay/architecture/_index.md` (modules, one tick), `docs/scheduler/` (scheduler), `docs/relay/config-reference.md` (config keys),
`docs/dashboard-data-contract.md` (what goes on the wire), `docs/relay-service.md` (how the relay relates to the Service Bus services).

## Install and run

```
cd relay
pip install -r requirements.txt
python run.py
```

`run.py` takes one optional flag, `--no-log` (below); `-h` shows help and any other argument exits 2. It reads one
file, `relay/config/relay_config.json`, or the path in the `RELAY_CONFIG` environment variable (for a test relay);
bind address and port come from `settings.bind_host` / `settings.port` in that file. A missing, unparseable or
schema-invalid file at startup prints the reason to stderr and exits 1.

Before starting, confirm the port is free: `netstat -ano | findstr ":8085"`.

```
curl http://127.0.0.1:8085/health
```

`/health` is the only unauthenticated route and returns `{"status":"ok","pollStats":{...}}`.

### Quiet mode: `python run.py --no-log`

```
python run.py            # logs to the console and to the files
python run.py --no-log   # logs only to files, one status line in the terminal
python run.py -h         # help; any other argument exits 2
```

Logs always go to files; `--no-log` only silences the console (and replaces it with the status line). Every log line, including the machine child processes' and uvicorn's, goes to files in
`relay/logs/` (or `RELAY_LOG_DIR`), one pair per local date:

- `<YYYY-MM-DD>_info_log.log`: INFO and above
- `<YYYY-MM-DD>_error_log.log`: WARNING and above

A new pair starts at local midnight. Anything a library prints to stdout/stderr is captured too (as INFO / WARNING).

The terminal shows one status line, rewritten in place (checked every second, redrawn only when it changes):

```
Relay healthy and running · :8085 · machines 3/5 online · alerts 2 · schedules 4 (1 running) · last cycle 09:41:07Z
```

The first word is `starting…` (before the first estate tick), `healthy and running`, `DEGRADED (<reasons>)` (estate
summary not `good`, e.g. `summary problem`, and/or `scheduler off: another instance holds the lock`) or `stopping…`.
"alerts" counts open machine, pipeline, schedule and scheduler-endpoint incidents; "last cycle" is the last estate
tick (UTC). On a console that cannot show `·` or `…` they become ` | ` and `...`. On exit the line becomes
`Relay stopped (HH:MM:SSZ)`. Fatal startup errors still print to the terminal (`refusing to start: ...`, or
`relay failed to start on <host>:<port> (details in <error log>)`).

## Config

- Template: `relay/config/relay_config.example.json` (placeholders such as `REPLACE_ME_DB_PASSWORD`). Copy it to
  `relay/config/relay_config.json` and fill in real values. The real file is git-ignored.
- `config_maker/relay_config.example.json` is the template for the generator input: a plain relay config whose
  `service_busses[]` entries and Database `node_config` carry extra native fields that only the generator reads (the
  relay ignores them; a leftover `config_maker` section is rejected by the generator).
  `config_maker/make_config.py` turns it into the Service Bus emulator files and one deploy package per machine;
  `make_config.py --check` also checks that both files agree on the scheduler machines. See `config_maker/README.md` and `docs/config_maker/_index.md`.
- Schema: `relay/src/_contract/schema.py` (`RelayConfig`). Top-level keys: `settings`, `scheduler` (optional; every
  key has a default, `docs/scheduler/config.md`), `alerts`, `service_busses`, `services`, `machines`
  (`machines[].machine_scheduler`: bool or object with per-machine schedule defaults). Databases are `machines[].nodes[]` entries with `node_type: "Database"` (or `"POS"`);
  `server_id` is unique across all nodes and `services[]`. Reference: `docs/relay/config-reference.md`.
- There is no `.env`: dashboard credentials, DB credentials, alert recipients and provider credentials all live in
  this file.

### Reload behaviour

`ConfigStore.get()` re-reads the file when its mtime changes (every poll/probe tick and every WS upgrade).
`ConfigRefreshLoop` also force-reloads every `settings.config_force_reload_seconds` (900) for filesystems that
do not update mtime. `POST /config/reload` forces an immediate reload and returns `{"reloaded": bool, "error": str|null}`.

A bad file at startup refuses to start; a bad file on a later re-read logs one warning and the relay keeps the
last-known-good config.

| Change | Live without restart |
|---|---|
| Thresholds, intervals, alert recipients, adding/removing servers or machines, the `scheduler` section | yes |
| `settings.auth.*` | yes, for the next connection (open sockets are not dropped) |
| `settings.bind_host`, `settings.port` | no, restart required |

Ctrl+C stops the relay cleanly in about 1.5 s, even with dashboards connected, schedules in flight and machine child
processes running: uvicorn gets `timeout_graceful_shutdown` 5 s, each shutdown step is bounded to 3 s, child processes
are stopped in parallel, and database drivers have connect/read timeouts so no thread blocks exit. In-flight runs are
saved and resumed on the next start.

**Do not run test suites in the console that hosts the live relay.** On Windows, `os.kill(pid, 0)` (a common "is this
pid alive" check) sends CTRL_C_EVENT to every process in the console, which stopped the relay mid-run before
`tests/_db_lock.py` was fixed. Use a separate console.

## Auth

`settings.auth` holds two pairs, `web_client_id`/`web_client_secret` and `app_client_id`/`app_client_secret`; either
pair authenticates. Credentials are accepted as `x-client-id` / `x-client-secret` headers or `?clientId=&clientSecret=`
query parameters, compared in constant time (`_lib/auth.py`). Every route except `/health` requires them
(WS upgrades check on connect; HTTP routes use `require_http_auth`). Uvicorn access logs redact the WS secret
(`_lib/log_redact.py`). WS Origin must be loopback, a private LAN address, or listed in `settings.ws_allowed_origins`.

A rejected HTTP request gets `401 {"detail":"invalid_client"}` and one WARNING on the `relay.auth` logger naming the
cause (`missing credentials`, `unknown client id`, `secret mismatch`), where the credentials came from (`header`,
`query`, `none`), the client id, path and client IP; never the secret. A wrong PIN on a PIN-gated route is a different
401, `{"detail":"invalid_pin"}`; only wrong PINs count toward the `pin_rate_limit_*` lockout (`429 rate_limited`).

WS close codes (`_ws/close_codes.py`): `4401` auth failed, `4403` origin forbidden, `4404` unknown `machine_id`,
`4410` machine offline, `1012` relay restarting.

## Endpoints

| Route | Purpose |
|---|---|
| `WS /ws` | Thin estate feed; keys `type, seq, ts, source, machines, services, buses, activeAlerts, schedules, configIssues` |
| `WS /ws/machine/{machine_id}` | Per-machine feed |
| `GET /health` | Liveness and poll-cycle statistics, no auth |
| `GET /api/config/check` | Configuration issues (`configIssues[]`) |
| `GET /api/buses` | Service Bus status |
| `GET /api/pairs/{from_id}/{to_id}/entities`, `GET /api/servers/{id}/entities`, `GET /api/platforms/{id}/entities` | Entity drill-down |
| `GET /api/pipelines/{ref}/errors` | Pipeline error rows; `ref` = `<serverId>:<pipelineNo>` or an unambiguous bare number (409 `ambiguous_pipeline` lists candidates; 503 when the owning server is unreachable) |
| `POST /ops/silence`, `GET /ops/silences`, `DELETE /ops/silence/{id}` | Silences; create and delete need `ack_pin`, rate-limited by `pin_rate_limit_*` |
| `POST /config/reload` | Force config reload |
| `GET /api/schedules`, `GET /api/schedules/{id}`, `GET /api/schedules/{id}/runs`, `POST /api/schedules/preview` | Schedules, run history, next-5 preview |
| `POST/PUT/DELETE /api/schedules[/{id}]`, `.../enable`, `.../disable`, `.../run`, `.../stop`, `POST /api/schedules/check-script` | Schedule writes, Test run, Stop run; all need `ack_pin` |
| `GET/POST /api/downtime`, `PUT/DELETE /api/downtime/{id}` | Recurring push-downtime windows per machine or service (all accounts); 400 on bad input, 404 unknown id |
| `GET/PUT /api/push/window` | The session account's own push hours (`{"window": null}` clears) |
| `POST /api/push/register`, `/unregister`, `/mute`, `/unmute`, `GET /api/push/status`, `/mutes` | Push device registration and per-alert stop |

Acknowledgement is shared: a WS `ack` (PIN checked) on an active alert is stored in `state/push/acks.json`, sets `acknowledged`, `acknowledged_by`, `acknowledged_at` on the alert for every client, is broadcast as a snapshot, and moves push to the 30 min cadence. All `/api` routes need the session bearer token. Push behaviour (repeat cadence, downtime, account windows, tones) is in
`docs/relay/push-notifications.md`. `settings.push.repeat_minutes_unacknowledged` (15) and `repeat_minutes_acknowledged`
(30) replace the `alert_repeat_*` / `alert_escalate_after_repeats` settings for push. `tzdata` is a requirement (Windows has no IANA database).

Payload shapes are in `docs/dashboard-data-contract.md`.

## Machine layer

`MachineLivenessLoop` probes each machine every `machine_poll_seconds`: ICMP ping first, and only when ICMP does not
confirm reachability it falls back to a TCP connect to `machine_port` (if set). Each machine gets a child process
(`_machines/supervisor.py`) that probes its nodes (`node_probe.py`). A confirmed-offline machine closes its
`/ws/machine/{id}` sockets with `4410` and raises a machine alert. The relay process must be restarted to pick up
code changes; only the config file is re-read live.

## Logging

Expected failures (connection refused, timeout, DNS failure, auth rejected, MySQL host blocked and similar) are
classified by `_lib/expected_errors.py` and logged once when they start, with one reminder line every 5 minutes and a
single recovery line (`_lib/transition_log.py`). Anything unclassified keeps its full traceback. Provider send
failures are logged at ERROR with secrets redacted (`_lib/redact.py`). `httpx`/`httpcore` request logs are held at
WARNING so the scheduler's polling does not flood the console. Set `RELAY_PACKETS_TRACE=1` for packet-tracing
output. With `--no-log` all of this goes to `relay/logs/<YYYY-MM-DD>_info_log.log` / `_error_log.log` instead of the
console (see "Quiet mode" above).

## Tests

```
cd relay
pytest
```

`pytest.ini` sets `pythonpath = src`, `testpaths = tests`, `asyncio_mode = auto`. `tests/` mirrors `src/`. Most tests
build a throwaway config under `tmp_path` (`make_config_dict`, `make_database_node` in `tests/conftest.py`) and need no
infrastructure.

- Live-lab tests (the `*_scenario` fixtures in `tests/conftest.py`, backed by `src/_fixtures/`) seed and delete rows in
  real databases and buses. They are disabled unless `RELAY_LIVE_LAB=1`: with it unset they report as skipped (tests marked `live_lab`, and any test whose fixture raises `LiveLabDisabledError`), never failed. They read connection details from
  `config_maker/configs/*_<platform>-local.json`; `make_config.py` no longer generates that folder (machines now get
  `deploy/<id>_<name>/machine.json`), so those files must already exist.
- Only one pytest process may run at a time: an autouse session fixture takes `relay/.pytest-db.lock` (PID + timestamp);
  a live holder fails the new run immediately, a dead PID or a lock older than one hour is taken over. Delete the file
  by hand only if you are sure nothing is running.
- Tests marked `elevated` stop a real Windows service and skip unless pytest runs as Administrator.
- `fixtures_cli.py` drives the same scenarios from the command line (`src/_fixtures/cli.py`).
- Generator tests live in `config_maker/tests/`.

## Layout

```
run.py              entry point
config/             relay_config.example.json (template), relay_config.json (real, git-ignored)
src/_contract/      pydantic schema
src/_lib/           config store, auth, origin check, redaction, log classification
src/_db/            MySQL / PostgreSQL / MSSQL (tedious or sqlcmd-ODBC) drivers, Service Bus checks, registry
src/_metrics/       poller, down-confirm, pair scans, attribution, bus incidents
src/_alerts/        engine, severity, dedup, dispatch channels
src/_machines/      liveness loop, supervisor, node probes
src/_ws/            router, estate payload, diffs, machine scope
src/_http/          REST routers
src/_ops/           silences, shared PIN check
src/_scheduler/     schedules store, UTC triggers, SchedulerLoop, run log, endpoint client, health probe
src/topology/       nodes and edges
src/_fixtures/      live-lab scenario toolkit
state/              runtime state (git-ignored; RELAY_STATE_DIR overrides the location): scheduler/ (schedules.json,
                    the only copy of the schedules: back it up; runs/, inflight.json, scheduler.lock), plus the
                    alert-state and dispatch-sent files (docs/relay/alert-dispatch-channels.md), push/ (state.json, mutes.json,
                    downtime.json, acks.json) and auth/ (push_accounts.json incl. per-account push_window, sessions.json)
tests/              pytest suite
```

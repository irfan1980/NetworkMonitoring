# ServiceBus: DB-to-DB sync over Azure Service Bus, with a live monitor

Databases on several machines (MySQL, PostgreSQL, SQL Server) keep business tables in sync through Azure Service Bus
(run locally as emulators). Each database has a registry-driven outbox/inbox: triggers write changes to `sync_outbox`,
a per-database service publishes them to a bus topic, and the receiving database's service applies them from
`sync_inbox`. No database ever queries another directly.

A monitor sits beside it, read-only against the databases and buses: a Python **relay** polls every database and bus
and publishes health, pipelines, pairs and alerts over WebSocket/HTTP, and an Expo **dashboard** draws the estate as a
live topology graph. The relay also runs a central **scheduler**: schedules defined in the dashboard trigger scripts
deployed on the machines.

## Layout

```
README.md           this file
TaskList.md         upcoming tasks, in execution order
config_maker/       generator for the bus emulator files and one deploy package per machine, plus the platform
                    services (mysql/, postgresql/, mssql/), machine_host.py, the scheduler endpoint (common/),
                    all database SQL (sql/<platform>/) and monitor.env tooling
relay/              Python monitoring relay (FastAPI + uvicorn, port 8085), pytest suite under relay/tests/
dashboard/          Expo / React Native Web monitoring UI (web, Android, iOS)
gateway/            App Service front end (Python 3.14): serves the dashboard build, proxies /api and /ws to the relay; supervisor.py is the startup command
docs/               Obsidian vault: architecture, config reference, runbook, operations. Start at docs/index.md
```

Each of `config_maker/`, `relay/` and `dashboard/` has its own `README.md`.

## How it fits together

- **Machines**: one package folder, one process, one OS service (`SB-<id>-<name>`) per machine
  (`config_maker/deploy/<id>_<name>/`). It runs every database node of the machine and, when `machine_scheduler` is
  on, the scheduler endpoint (port 8095).
- **Services** (`config_maker/<platform>/<platform>_service.py`): one node per database (`server_id`). A sender
  thread publishes Pending outbox rows to the service's buses, receiver threads read the service's own subscription
  (`to-<id>-<name>`, filter `targetSystem = '<server_id>'`), and a heartbeat thread stamps
  `sync_pipeline.last_ping_at`.
- **Buses**: Service Bus emulators in Docker, one per bus id (bus 1: topic `sync`, AMQP 5672, health 5300; bus 2:
  topic `sync-2`, AMQP 5673, health 5301 in the lab config).
- **config_maker**: one input file (a plain relay config) generates every emulator file and, with `--package`, every
  machine package, so subscription filters and service subscription names cannot drift apart. `--check` validates it
  against the relay config.
- **Relay**: reads `relay/config/relay_config.json`, probes machines (ICMP first, TCP fallback), polls each database
  through the read-only `sb_monitor` login, checks each bus, and serves `/ws`, `/ws/machine/{id}` and a small HTTP API.
  It never writes to a database or a bus, and it does not serve the dashboard's web build. Its scheduler keeps the
  clock (UTC) and calls each machine's scheduler endpoint (time-bracket key + relay IP allowlist); every schedule
  change and test run needs the PIN.
- **Dashboard**: tabs Overview, Pipelines, Alerts, Scheduler, Downtime, Notifications, with the issues sidebar on every screen. One socket for
  the machine inventory, one per online machine. Machines are labelled
  `Name (machine_label_id)`, with the id exactly as configured (e.g. `Tabish-pc (0004)`). Each pipeline is one
  logical lane made of halves: the sender half comes from the outgoing `sync_pipeline` row, the receiver half from
  the incoming row, and the middle stays blank until a bus carries that route. Lane colour comes from the heartbeat
  (`last_ping_at`); a pipeline that has never pinged is not shown as online.

Details: [docs/system-overview.md](docs/system-overview.md).

## Quick start

Windows, Python 3.9+, Node (for the dashboard), Docker (for the bus emulators).

1. **Config**. Copy `config_maker/relay_config.example.json` to `config_maker/relay_config.json` (generator input, local only) and write `relay/config/relay_config.json` (relay config; structure in `relay/README.md`). `relay/config/relay_config.example.json` is only the relay's blank fallback config (local machine, push off): the relay runs on it while `relay_config.json` is missing and switches by itself when that file is dropped in.

   The two JSON templates are mirrored and pass `make_config.py --check`: same buses, full `scheduler` section and five
   example machines (two-node Windows with a full `machine_scheduler` object, MSSQL, Linux, scheduler-only,
   monitoring-only). Replace them with your machines, keeping both files in step.
   - `dashboard/.env.example` to `dashboard/.env` (client ids/secrets must match the relay's `settings.auth`)
2. **Check, then generate bus files and machine packages**:
   ```
   python config_maker/make_config.py --check
   python config_maker/make_config.py --package
   ```
   `--check` validates the input and that both config files agree on the machines. `--package` writes
   `config_maker/buses/` and one `config_maker/deploy/<id>_<name>/` per machine. It first uninstalls services
   registered from those folders and wipes them (including `services/` scripts and `logs/`), so run it elevated when a
   package service is installed and back up scripts first. Exit 2 = problems listed, nothing written.
3. **Set up each database** from `config_maker/sql/<platform>/` (`mysql`, `postgresql`, `mssql`): `setup.sql`,
   `shared.sql`, `entities/customers.sql`, `triggers/customer.sql`, `monitor_user.sql` (password from
   `python config_maker/gen_monitor_env.py`), then seed the registry with `seed/sender.example.sql` (sender) or `seed/receiver.example.sql` (receiver).
   MySQL needs `SET GLOBAL event_scheduler = ON`. Full steps: `config_maker/README.md` ("Set up a new machine").
4. **Start the buses** (on the bus host): `config_maker\buses\emulator_1.start.cmd` and `emulator_2.start.cmd`. Poll
   `http://localhost:5300/health` and `:5301/health` until healthy; a cold start can take minutes.
5. **Deploy each machine**: copy its one `deploy\<id>_<name>\` folder (it holds the DB password; transfer it
   securely), open TCP 8095 to the relay only, and run `install_service.cmd` as Administrator (Linux:
   `sudo ./install_service.sh`). `start.cmd` runs it in a console instead. Step by step, including prerequisites,
   updating and uninstalling: [docs/runbook.md](docs/runbook.md#deploy-a-machine).
6. **Start the relay**:
   ```
   cd relay
   pip install -r requirements.txt
   python run.py
   curl http://127.0.0.1:8085/health
   ```
   `python run.py --no-log` logs only to `relay/logs/<date>_info_log.log` / `_error_log.log` and shows one status
   line. `RELAY_CONFIG=<path>` overrides the config file; bind address and port come from
   `settings.bind_host` / `settings.port`. Ctrl+C stops it in about 1.5 s. Do not run test suites in the console that
   hosts the live relay (on Windows a pid check there can send Ctrl+C to the relay).
   `requirements.txt` now includes `croniter` (scheduler). The relay is read-only against the databases and buses
   and can start at any point; schedules fire only while it runs.
7. **Start the dashboard**:
   ```
   cd dashboard
   yarn install
   yarn web             # or: yarn android / yarn ios
   ```
   Set `EXPO_PUBLIC_RELAY_WS_URL` to `ws://<relay-host>:8085/ws`. For a wall display, export with
   `npx expo export --platform web --output-dir dist --clear` and serve `dist/` from a separate static host; the
   relay must allow that page origin (`settings.ws_allowed_origins`, unless it is loopback or a private LAN address).

Stop order, readiness checks, cold-start recovery and what needs a restart: [docs/runbook.md](docs/runbook.md).

## Secrets & config (not in git)

These files hold credentials and are git-ignored (see `.gitignore`), so a fresh clone must create them locally. Only
the `*.example.json`, `monitor.env.example` and `.env.example` templates are meant to be committed.

| File | How to get it |
| --- | --- |
| `relay/config/relay_config.json` | Write it from the structure in `relay/README.md` (or copy `config_maker/relay_config.example.json` plus `settings`): `ack_pin`, DB users/passwords, alert recipients. Without it the relay runs on its blank fallback (`relay/config/relay_config.example.json`) and switches when the file appears. Every other `*.json` in `relay/config/` is ignored too. |
| `config_maker/relay_config.json` | Copy `config_maker/relay_config.example.json` and fill in the relay values plus the native bus fields (`host`, `sas_key_name`, `sas_key`, `mssql_sa_password`) and optional `node_config.service_*` overrides. |
| `config_maker/buses/`, `config_maker/deploy/` | Generated by `python config_maker/make_config.py [--package]`. Contain DB, SAS and SA credentials. `buses/emulator_<id>.env` carries the emulator variables; each `deploy/<id>_<name>/machine.json` one machine's DB and bus credentials. |
| `config_maker/monitor.env` | Generated by `python config_maker/gen_monitor_env.py` (`--regenerate` rotates it); format in `config_maker/monitor.env.example`. |
| `dashboard/.env`, `dashboard/.env.local` | Copy `dashboard/.env.example` and fill in the relay URL, client id/secret pairs and access codes. |
| `GoogleService-Info.plist`, `google-services*.json` | Firebase client configs, from the Firebase console. |
| `config_maker/sql/` | The whole folder is currently ignored by `.gitignore` (including the `seed/*.example.sql` files), so it must be copied from an existing checkout. |

`relay/state/relay_state.db` (SQLite) holds the only copy of the schedules, plus sessions and push state: back it up with `python relay/state_db.py backup <file>`, never by copying the live file. Existing `relay/state/**/*.json` files are imported once on first start and left in place.

Also ignored: any `.env` / `*.env`, logs (`**/logs/*`), `relay/state/*`, `relay/ws_*capture*.json`, venvs,
`node_modules/`, `dashboard/dist/`, caches and editor history (`.history/`). Never paste real values into docs.

## Configuration

- Relay: one file, `relay/config/relay_config.json` (schema `relay/src/_contract/schema.py`). Top-level keys
  `settings`, `scheduler` (optional, all defaults), `alerts`, `service_busses`, `services`, `machines`
  (`machines[].machine_scheduler` turns the scheduler on per machine). Re-read on change; `bind_host`/`port` need a
  restart.
  Reference: [docs/relay/config-reference.md](docs/relay/config-reference.md).
- Services and buses: generated only, never hand-edited. Change the generator input and re-run
  `make_config.py --package`, then reinstall the machine; a subscription/filter change also needs
  `emulator_<id>.recreate.cmd` (drops in-flight messages).
- Scheduler: [docs/scheduler/config.md](docs/scheduler/config.md) (every key, per-machine overrides, interpreters).
- `server_id` is unique across all database nodes (lab convention `<machine number>001`); the config is the source
  of truth.
- Every port, file and env var in one place: [docs/config-reference.md](docs/config-reference.md).

## Testing

```
cd relay && pytest                    # relay suite; no infrastructure needed for most tests
python -m pytest config_maker/tests   # generator, package and scheduler endpoint tests
cd dashboard && yarn typecheck     # dashboard type check
```

Live-lab tests (real databases and buses), from `cmd.exe`:

```
set RELAY_LIVE_LAB=1
cd relay
pytest
```

The value must be exactly `1` (PowerShell: `$env:RELAY_LIVE_LAB = "1"`).

- Live-lab tests seed and delete rows in real databases and buses. They are disabled unless `RELAY_LIVE_LAB=1`, and read
  connection details from `config_maker/configs/*_<platform>-local.json`, which `make_config.py` no longer generates.
- Only one relay pytest run at a time: a session fixture holds `relay/.pytest-db.lock`.
- Tests marked `elevated` stop a real Windows service and skip unless run as Administrator.
- The dashboard can run with no relay: `EXPO_PUBLIC_FIXTURE=1` renders bundled fixture data.
- Guided full-system test with fault injection: [docs/manual-test.md](docs/manual-test.md).

## Running tests locally

The GitHub Actions workflows no longer run tests; run them on your machine. Needs Python 3.12 or newer (3.14 used for the check) and Node >=24.12 with yarn. In PowerShell, from the repo root (venvs outside the repo are fine; any folder works):

```
# relay (about 4 minutes; one run at a time)
py -3.14 -m venv $env:TEMP\relay-venv
& $env:TEMP\relay-venv\Scripts\python -m pip install -r relay\requirements.txt
cd relay; & $env:TEMP\relay-venv\Scripts\python -m pytest -q -p no:cacheprovider; cd ..

# config_maker (same venv as relay)
& $env:TEMP\relay-venv\Scripts\python -m pytest config_maker\tests -q -p no:cacheprovider

# gateway
py -3.14 -m venv $env:TEMP\gateway-venv
& $env:TEMP\gateway-venv\Scripts\python -m pip install -r gateway\requirements-dev.txt
cd gateway; & $env:TEMP\gateway-venv\Scripts\python -m pytest -q -p no:cacheprovider; cd ..

# dashboard (no unit tests: type check and Firebase config check)
cd dashboard; yarn install --frozen-lockfile; yarn typecheck; yarn check:firebase; cd ..
```

Run the relay tests from `relay\` (its `pytest.ini` sets the paths). Use `py -V:<tag>` (see `py -0`) if `py -3.14` is not found. Last run: relay 1355 passed, 55 skipped; config_maker 224 passed, 10 skipped; gateway 35 passed; dashboard typecheck clean.

## Docs map

| Doc | What it covers |
| --- | --- |
| [docs/index.md](docs/index.md) | Vault home: links every current doc |
| [docs/system-overview.md](docs/system-overview.md) | How services, buses, relay and dashboard fit together |
| [docs/runbook.md](docs/runbook.md) | Setup, start/stop order, readiness checks, restarts |
| [docs/operations.md](docs/operations.md) | Colour rules, maintenance mode, bus outages, alerts, troubleshooting |
| [docs/config-reference.md](docs/config-reference.md) | Every config file, port and setting |
| [docs/dashboard-data-contract.md](docs/dashboard-data-contract.md) | WebSocket and HTTP payloads the dashboard reads |
| [docs/relay/_index.md](docs/relay/_index.md) | Relay architecture, config reference, alert dispatch, pair scans |
| [docs/dashboard/_index.md](docs/dashboard/_index.md) | Dashboard topology and per-file architecture notes |
| [docs/sync/_index.md](docs/sync/_index.md) | Sync design, SQL layout, triggers registry, buses, concepts |
| [docs/config_maker/_index.md](docs/config_maker/_index.md) | Generator: usage, deploy packages and per-module architecture notes |
| [docs/scheduler/_index.md](docs/scheduler/_index.md) | Scheduler: security, triggers, runs and outcomes, config, API, state files |
| [docs/relay-service.md](docs/relay-service.md) | What the relay depends on from the services |
| [docs/manual-test.md](docs/manual-test.md) | Owner's guided end-to-end test |
| [docs/scripts/mars-abafar-products.md](docs/scripts/mars-abafar-products.md) | MARS to Abafar product copy script: steps and how to run |
| [docs/demo/README.md](docs/demo/README.md) | Demo material: presenter guide and technical reference PDFs |

## Tasks completed
- 2026-11-04 (task 56): Removed the `server_unused_by_any_bus` config issue ("server N has an empty used_buses"): a server with an empty `used_buses` and no bus configured is valid, and a bus never references a server, so no real inconsistency existed. `service_bus_unused_by_any_server` and the pipeline topic checks are unchanged. See `docs/relay/architecture/contract-schema.md`.
- 2026-11-04 (task 55): Dashboard users on Azure: create `/home/relay/state/relay_state.db` by hand with a refuse-if-exists `sqlite3` CLI script (schema 2, triggers, meta markers; verified identical to `Database.for_dir`), then insert accounts one row at a time. See `docs/relay/state-database.md`.
- 2026-11-04 (task 53): Investigated creating dashboard users on the Azure App Service. Login is a single plain-text `account_secret` in `user_accounts` (no hashing, roles or disabled flag); no code changed. A stdlib helper (`docs/relay/relay_users.py`) lists, adds, re-secrets and removes accounts through the relay venv's Python with a hidden prompt; steps in `docs/relay/state-database.md`.
- 2026-11-04 (task 52): The two Azure workflows are replaced by one, `.github/workflows/development_azure.yml`: it builds the dashboard export and deploys dashboard, gateway and relay together as one zip (`dashboard/`, `gateway/`, `relay/`) to `/home/site/wwwroot` (`type: zip`, `clean: true`, `restart: true`, same publish-profile secret, same concurrency group). The old files are in `.github/workflows-backup/`. A push touching any of the three folders now deploys all three (a dashboard-only push restarts the relay). Runtime data in `/home/relay` is outside wwwroot and not touched. The App Service setting `SCM_DO_BUILD_DURING_DEPLOYMENT=false` and `ENABLE_ORYX_BUILD=false` are both required (Oryx was observed replacing the folders with `output.tar.zst`; see `docs/github-actions.md`).
- 2026-11-04 (task 51): Dashboard deploy failed on Azure with Oryx "Could not detect any platform" because a remote build ran on the static export. The dashboard zip now includes a `.deployment` file with `SCM_DO_BUILD_DURING_DEPLOYMENT=false`; the App Service settings `SCM_DO_BUILD_DURING_DEPLOYMENT=false` and `ENABLE_ORYX_BUILD=false` must be set in the portal (see `docs/github-actions.md`).
- 2026-11-04 (task 50): New `scripts/mars_abafar_products/seed_mars_product.sql` creates `[dbo].[SEED_MARS_PRODUCT]`, which inserts one random test product (ID given as `@ProductID`) into the MARS tables the script reads, in one transaction. Not run against MARS yet. See `docs/scripts/mars-abafar-products.md`.
- 2026-11-03 (task 49): The Azure workflows no longer run tests: `development_relay.yml` lost its Python setup and the relay and gateway pytest steps (the zips are unchanged); `development_dashboard.yml` had no test step (its `yarn typecheck` stays). Tests are run locally, see "Running tests locally". See `docs/github-actions.md`.
- 2026-11-03 (task 48): `scripts/mars_abafar_products/run.py`: the metadata insert filters on `last_id` like the other migrate steps (it was fixed at ID > 20000); new `TESTING = True` at the top keeps the one-product limit (`last_id < ID < last_id + 2`) on the four migrate fetchers, `TESTING = False` fetches everything above `last_id`; a failed insert now rolls back, closes the connection and stops the script instead of being printed and skipped. See `docs/scripts/mars-abafar-products.md`.
- 2026-11-03 (task 47): GitHub Actions moved off Node.js 20: `actions/checkout` v4 to v5, `actions/setup-node` v4 to v5, `actions/setup-python` v5 to v6, `actions/upload-artifact` v4 to v6, `actions/download-artifact` v4 to v7 (each verified as `using: node24` in its action.yml); `azure/webapps-deploy@v3` was already node24. The dashboard build already pins `node-version: '24.x'`. See `docs/github-actions.md`.
- 2026-11-03 (task 47): `scripts/mars_abafar_products/run.py`: `fetchLastIDFromAbafar` no longer returns a hardcoded `21320`; it returns the int `max(ID)` of Abafar `PRODUCTS` (previously the unreachable query read `PRODUCT_PRICES`), or `None` on an empty table. The `migrate...` insert steps return `0` when MARS returns no rows instead of raising `IndexError`. See `docs/scripts/mars-abafar-products.md`.
- 2026-11-03 (task 46): `scripts/mars_abafar_products/run.py` no longer uses the text `'NULL'` for missing values: `SALE_END` (both the new-price and the price-update queries) now returns a real SQL NULL and `HAS_SUBSTITUTE` is the plain `CAST(... AS VARCHAR(20))`, which is NULL when there is no substitute; `queries.sql` matches. Python `None` reaches `executemany` unchanged. Rows already written with the text 'NULL' are not corrected by this change. See `docs/scripts/mars-abafar-products.md`.
- 2026-11-03 (task 45): The dashboard GitHub Actions workflow (`.github/workflows/development_dashboard.yml`) now runs on a push to `Development` only when `dashboard/**` or the workflow file changed; manual `workflow_dispatch` still works. The relay workflow is unchanged. See `docs/github-actions.md` (Path filters).
- 2026-11-03 (task 44): `scripts/mars_abafar_products/run.py` update and UPC sync steps reworked: each fetcher now runs a plain `SELECT` on MARS and returns JSON rows; the update functions write them to Abafar with parameterized `UPDATE`s (`executemany`), and the UPC sync deletes then inserts in one transaction. `queries.sql` updated. See `docs/scripts/mars-abafar-products.md`.
- 2026-11-02 (task 43): `scripts/mars_abafar_products/run.py` finished: fetch and update/sync functions for products, prices, UPCs, metadata and sizes built from the queries in `queries.sql`. Only `syncProductUPCsFromMarsToAbafar` is active in the bottom call block (the rest are commented out). The UPC sync was tested against a fake `pyodbc`. See `docs/scripts/mars-abafar-products.md`.
- 2026-11-02 (task 42): The run history table `schedule_runs` now cleans itself: `scheduler.log_max_age_days` (default 30) deletes finished runs older than that at relay start and once a day, in small batches, keeping the newest run of each schedule, unfinished runs and undated rows (`relay/src/_scheduler/runlog.py`). Test runs no longer write the real `relay/logs` (`test_shutdown.py` passed no `RELAY_LOG_DIR`; `conftest.py` now sets it for every test). See `relay/README.md`.
- 2026-11-02 (task 39): Push/login accounts moved from `push_accounts.json` into the state database: `user_accounts (account_secret, name, min_severity)` is edited with plain SQL (`sqlite3 /home/relay/state/relay_state.db "INSERT INTO user_accounts VALUES ('secret', 'Name', 5);"`) and applies on the next login with no restart; the relay writes tokens and push windows to `account_devices` and `account_push_windows`. An existing `push_accounts.json` is imported once (also into databases made by the previous release) and then ignored; `settings.push.accounts_file` is deprecated (import source only). `state_db.py check` prints account counts. The database file is `0600` and now holds the secrets, as do daily copies. See `relay/README.md` "Accounts" and `docs/relay/state-database.md`.
- 2026-11-02 (task 38): `relay/config/relay_config.example.json` is now a blank, valid fallback config (local machine only, `127.0.0.1:8085`, push off, no secrets). The relay starts on it when the main config file is missing (throwaway state folder: no real state DB, import or daily copy; no logins), reports `configMode: fallback` on `/health`, and switches to the main config inside the same process within seconds of a valid file appearing. `gateway/supervisor.py` no longer blocks the relay on a missing config. See `relay/README.md` and `docs/relay/fallback-config.md`.
- 2026-11-02 (task 34, relay logs and tests): relay daily logs are now deleted after `settings.log_retention_days` (30) days, matching only the exact daily file names (`relay/src/_lib/file_logging.py`); `config_maker` does not read `settings`, so it needs no change. The shutdown test helper and `_scheduler/lock.py` now treat a process that has already exited (psutil `NoSuchProcess` or raw `ProcessLookupError`) as gone.
- 2026-11-02 (task 34, relay push): after a long stop the relay no longer sends one overdue repeat push per alert at once (repeat timers overdue by two or more intervals are re-baselined on the first evaluation after start; `docs/relay/push-notifications.md`). `relay/pytest.ini` sets `asyncio_default_fixture_loop_scope`.
- 2026-11-02 (task 33): The relay now copies its state database once per UTC day to `<state dir>/backups/relay_state_YYYY-MM-DD.db` and deletes copies dated more than 30 days ago after each successful copy (`settings.state_backup`: `enabled`, `folder`, `retention_days`, `retry_minutes`; defaults need no config change). Copies are written to a temp name, integrity-checked and published without replacing; the newest copy is always kept and only files with that exact name are deleted. `python relay/state_db.py check` shows the last copy and error. Also fixed `test_missing_object_ids_are_treated_as_unread_across_cycles` (it depended on the clock tick size and failed only on Python 3.14; the product code was correct). See `docs/relay/state-database.md`.
- 2026-11-02 (task 31): Moved the relay's runtime state (schedules, run history, in-flight runs, sessions as hashes, push state, mutes, downtime, acks) from JSON files in `relay/state` into one SQLite file, `relay/state/relay_state.db` (standard library only, rollback journal, no WAL, busy timeout, versioned migrations; `relay/src/_state/`). The first start imports the old files once in a transaction and leaves them in place; an unreadable old file stops the start with an error. `push_accounts.json`, the scheduler lock file and the logs stay files. New `python relay/state_db.py backup|check|export`. See `docs/relay/state-database.md`, including the risks on network storage.
- 2026-11-02 (task 30): The relay now applies the WebSocket Origin rule to every REST route too: a disallowed `Origin` gets `403 origin_forbidden` (login, preflight and `/health` included), allowed origins get CORS headers, requests with no Origin are unchanged (`relay/src/_http/origin_guard.py`, `relay/tests/_http/test_origin_guard.py`). A public dashboard domain must be listed in `settings.ws_allowed_origins`; no config migration needed.

- 2026-11-02 (task 37): `mssql_deployment.txt` (it carries logins and passwords) is now in `.gitignore`. The `deploy` jobs of `development_relay.yml` and `development_dashboard.yml` share the concurrency group `azure-deploy-devmonitoringApp` (`cancel-in-progress: false`), so the two Azure deploys take turns; GitHub keeps one running and one pending per group, a newer pending run replaces an older pending one. See `docs/github-actions.md`.
- 2026-11-02 (task 36): New `.github/workflows/local_ubuntu.yml`: builds the dashboard (same-origin) on GitHub and deploys it with `relay/` and `gateway/` to a self-hosted runner labelled `localUbuntu`, where a systemd user service runs `gateway/supervisor.py`: `http://localhost:8085` serves the dashboard and forwards `/api` and `/ws` to the relay on 127.0.0.1:8086. Triggers: push to branch `local-ubuntu` or manual (not `Development`, to avoid the Azure workflows). See `docs/github-actions.md` for machine prerequisites.
- 2026-11-02 (task 32): One App Service (`devmonitoringApp`), two ends. New `gateway/` (Python 3.14, aiohttp): `server.py` is the public listener (dashboard static files with SPA fallback, allowlisted proxy of `/api/*` and `/ws`, `/ws/*` to the relay on 127.0.0.1:8085) and `supervisor.py` is the App Service startup command (`python3 /home/site/wwwroot/gateway/supervisor.py`): it builds one venv per folder under `/home/relay`, keeps the gateway in the foreground and the relay restarted with backoff, and stops the relay gracefully on SIGTERM. `development_relay.yml` (now also `gateway/**`) deploys `relay.zip` and `gateway.zip` to `/home/site/wwwroot/relay|gateway` and restarts the app; `development_dashboard.yml` deploys `dashboard.zip` to `/home/site/wwwroot/dashboard` without a restart; `EXPO_PUBLIC_RELAY_WS_URL` is no longer used in CI (same-origin build). Relay config behind it: `bind_host` 127.0.0.1, `port` 8085, `ws_allowed_origins` `["https://mydomain.com"]` (placeholder). Gateway tests: `cd gateway && pip install -r requirements-dev.txt && pytest`. See `docs/github-actions.md`.
- 2026-11-02 (task 28): Separate GitHub Actions for the two components: `.github/workflows/development_relay.yml` (relay/**, Python 3.14, pytest, artifact `relay/`) and `.github/workflows/development_dashboard.yml` (dashboard/**, Node 24 + yarn, typecheck, `expo export --platform web`, artifact `dashboard/dist`). Both trigger on push to `Development` for their own path (and manually) and reuse the existing deploy step of `development_devmonitoringapp.yml` (azure/webapps-deploy, app `devmonitoringApp`, same publish-profile secret). The old `development_devmonitoringapp.yml` was retired on 2026-11-02 (task 29) and moved unchanged to `.github/workflows-backup/` (inactive; move it back to re-enable); see `docs/github-actions.md` for open questions. Dashboard builds need the repo variable `EXPO_PUBLIC_RELAY_WS_URL`.
- 2026-10-23 (task 27): Topology lanes get two config tunables, `EXPO_PUBLIC_LANE_NODE_MIN` (70) and `EXPO_PUBLIC_LANE_BUS_MIN` (150), for the min node-side and service-bus segment lengths (optional relay override `layout.lane_node_min/lane_bus_min` on the root snapshot). Service-to-service lanes are now orthogonal on the right of the cards, enter the target with an arrowhead, and carry an `A → B` label beside them. See `docs/dashboard/topology.md`.
- 2026-10-22 (tasks 24-26): Topology map: machines without nodes get their own left section under EXTERNAL SERVICES (stacked when it fits, else a second column); both sections have blue borders, red when every member is down/offline, larger readable text, and sit outside Shuffle. Pipelines to and from external services are drawn from the service cards (pairs/pipelines for machine-less servers need optional `pairs`/`pipelines` on the root `/ws` snapshot, see `docs/dashboard/topology.md`). The map now fills the viewport width when it has few items. Fixture data in `dashboard/src/fixtures/services.ts`.
- 2026-10-22 (task 23): `make_config --package` now builds one extra package, `deploy/0_external-services/` (OS service `SB-0-external-services`), holding every top-level `services[]` entry (independent services, e.g. MSSQL 9001) for the main server; before they got a bus subscription but no package, so nothing consumed it. `--check` reports it, machine_id 0 is reserved, and an mssql `node_config.port` is now honoured. Neither live relay_config.json gained a `services[]` entry. See `docs/config_maker/deploy-package.md`.
- 2026-10-23 (task 26): The root `/ws` snapshot now carries `pairs` and `pipelines` for pairs/pipelines with a standalone `services[]` end (`[]` when none), so service-to-service pipelines reach the dashboard; the pair entities endpoint already accepted service server ids. Restart the relay to apply.
- 2026-10-22 (task 22): Rewrote `mssql_deployment.txt` for the Azure SQL `Abafar` database in two self-contained sections (SENDER, RECEIVER) with a contained monitor user, object-level grants, idempotent seeds and a non-e-commerce test entity (`WeatherStation`). Every table, column, constraint, index and routine is PascalCase, including the service-bus tables (`SyncPipeline`, `SyncTriggers`, `SyncOutbox`, `SyncInbox`); the old-to-new naming map for the relay and service update is in the docs page. `config_maker/sql/mssql/` uses the same sync names. Flow: Abafar (9001) to Mars (9002). Not run against any database. See `docs/sync/mssql-deployment.md`. Relay and `config_maker/mssql/mssql_service.py` MSSQL code now use the PascalCase names (SQL aliases keep the shared snake_case row keys; the service reads PascalCase keys); restart the relay to apply.
- 2026-10-22 (task 20): Topology map: independent services (relay `services[]`) now sit in a dedicated vertical "EXTERNAL SERVICES" strip at the left-most edge; machines are laid out to its right and Shuffle/Reset/Relocate never move or reorder the strip. No strip and no reserved space when there are none. See `docs/dashboard/topology.md`.
- 2026-10-21 (task 18): Live demo material in `docs/demo/`: a presenter guide PDF and a technical reference and FAQ PDF (architecture, components, status model, scheduler, deployment to third-party systems, security, notifications, dependencies, limitations, 49 questions, glossary) to send to attendees; HTML sources beside the PDFs. See `docs/demo/README.md`.
- 2026-10-14 (task 17): Dashboard polish from the final e2e: pan works from anywhere on the topology map and a drag that starts on a node no longer opens its modal; the map fits to the drawn content on first layout; the Overview headline shows "CONNECTING..." before the first snapshot and never "ALL GOOD" while issues exist; "1 kind of data flowing" pluralises. See `docs/dashboard/topology.md`.
- 2026-10-14 (tasks 7, 15): Stabilised flaky tests. The relay shutdown test now waits up to 4 s for an in-flight liveness `ping` child to end instead of a fixed 0.5 s look. On Windows under a venv, the scheduler endpoint recorded the venv launcher's pid instead of the runner's: `runner_python()` in `config_maker/common/scheduler_endpoint.py` now starts the runner with the base interpreter and passes `SCHEDULER_SERVICE_PYTHON` so the runner's python fallback is still the service's own interpreter. Machines running the scheduler endpoint get this only after `python config_maker/make_config.py --package` (it rebuilds `deploy/<id>_<name>/`, so back up `services/` and `logs/` first, run it elevated, and reinstall the service with `install_service.cmd|sh`); see `docs/config_maker/deploy-package.md`.
- 2026-10-14 (task 16): Every alert channel (`sev{n}_{first|repeat}` and `alerts`) vibrates 5 groups of 3 strong pulses (400 ms on, 120 ms off, 600 ms between groups; `VIBRATION_PATTERN` in `dashboard/src/push/channels.native.ts`). Android fixes a channel's vibration when it is created, so existing installs need their app data cleared (`adb shell pm clear com.servicebusmonitoring.app`) or a reinstall. iOS has no custom vibration. See `docs/dashboard/push-notifications.md`.
- 2026-10-14 (task 14): Per-machine `machine_alert` block in the relay config (seconds): the report interval is also the first-push delay, with a repeat and an acknowledged repeat interval; `enabled: false` means no push for that machine's alerts; across machines the shorter interval wins, and a mix of enabled and disabled machines still pushes; a bad value falls back to the default and raises the config issue `machine_alert_invalid` (shown in the dashboard sidebar with the machine name). See `docs/relay/push-notifications.md`.
- 2026-10-22 (task 21): Database object alerts (`object:db:<platform>:<id>:query`) now carry `server_id`, the last read error as `detail` and in `reason` (e.g. "Login failed for user ... (18456)"), and `machine_id: null` for standalone `services[]` servers; before, the reason was the raw object id with no error. Takes effect after a relay restart. Dashboard side: the issues sidebar now lists independent services as `<name> · no machine` (from the machine-less alert matched on `server_id`, or from a `down` node status when no alert exists); before, a broken service card on the map never produced a sidebar line. See `docs/dashboard/_index.md`.
- 2026-10-22 (task 19): Registered a standalone MSSQL service (`mssql-service`, server_id 9001) in `relay/config/relay_config.json` `services[]`; the relay already supports `mssql` (tedious/odbc drivers), so no code change. Fill in `node_config.password` (and host/port/user/database if different) before the relay is restarted.
- 2026-10-14 (task 13): Pipeline and server diagnostics return partial data when one side or the bus is down: `GET /api/pairs/{from}/{to}/entities` carries `sides` and `bus` availability (503 only when both databases are unreadable), and the entity row shape matches the dashboard contract. The modal shows a note per unavailable piece, "unavailable" instead of 0, machine-qualified labels ("Tabish-pc · MySQL (4001)"), the buses a server actually uses, and a normaliser keeps odd rows from crashing it; fixtures use the relay's real response.
- 2026-10-13 (task 12, dashboard): Login sends a stable per-install `device_id` (secure storage on native, localStorage on web, in-memory fallback), so the relay replaces a session only for the same account and device (sessions never expire on their own; a push token is pruned only on FCM `UNREGISTERED`; relay file logs are always written); default device names are now descriptive (`android - <model>`, `web - <browser>`). The foreground `push_enabled` confirmation is shown on the default-sound `alerts` channel, not a severity tone; sign-in errors no longer show `[object Object]`. The client never ends a session on its own (a 401 or WebSocket 4401 is re-checked against `/api/auth/me` and only `session_invalid` counts) and re-registers its push token on every start. Runbook, manual-test and system-overview commands switched to yarn.
- 2026-10-13 (task 8): Live bring-up. Relay running; the EAS development build failed on a manifest merge (the `expo-notifications` plugin `color` clashed with RNFirebase's `default_notification_color`) and was fixed by removing `color`; the new build installed on the Pixel35 emulator with all 11 notification channels (10 severity plus `alerts`) verified; web and Android end-to-end checks passed.
- 2026-10-13 (task 11): The dashboard uses yarn classic (`yarn.lock`, no `package-lock.json`); the npm-only `allowScripts` field is gone and install/audit commands in the docs are now yarn. The Android `alerts` channel no longer passes `sound: 'default'` (expo-notifications treats a string as a custom file; omitting it gives the system default).
- 2026-10-13 (task 10): Dashboard now requires Node >=24.12 (`engines`; install with `nvm install 24.21.0` and `nvm use 24.21.0` from an elevated terminal). Expo SDK 57.0.28 (react 19.2.3, react-native 0.86.3, expo-router 57.0.26, @react-native-firebase 26.4.0, firebase 13.0.0); `expo-doctor` is clean. Fixed the missing notification icon (`app.json` now uses `assets/android-icon-monochrome.png`), which would have broken an Android build. `npm audit` keeps 34 findings from five Expo/Firebase transitive advisories with no safe fix in SDK 57; reasons are in `dashboard/README.md` "Dependency audit".
- 2026-10-13 (task 9): Tidied `dashboard/assets/sounds/`: only the five `sevarity-N` folders remain, each with its source mp3 and its `sev{n}_first.wav` / `sev{n}_repeat.wav`; the 11 unused mp3s were removed (Recycle Bin) and the tone docs moved into `docs/dashboard/push-notifications.md`.
- 2026-10-12 (task 6): Added push tones, repeats, shared acknowledgement, downtime and per-account push hours. Each severity has a first and a softer, shorter repeat tone (`dashboard/assets/sounds/sevarity-1..5/`, sources and swap steps in `docs/dashboard/push-notifications.md`); Android gets channels `sev{n}_first|repeat`, iOS and web play the matching WAV. Push repeats every 15 min while unacknowledged and every 30 min once acknowledged, and an acknowledgement is stored in the relay and shared by all accounts. Recurring downtime windows per machine or service (push only, alerts still show) are edited on the Downtime tab; each account sets its own push hours and IANA timezone on Notifications. New relay endpoints `/api/downtime` and `/api/push/window`; new relay dependency `tzdata` (`pip install -r relay/requirements.txt`). Setup: restart the relay, and make a new Android/iOS dev build (sounds and channels are native). See `docs/relay/push-notifications.md` and `docs/dashboard/push-notifications.md`.
- 2026-10-07: Made pipeline ids server-qualified on the wire (`pipelineId: "<serverId>:<pipelineNo>"` plus `pipelineNo`, alert ids `pipeline:<server>:<no>` etc.); `GET /api/pipelines/{ref}/errors` takes a composite or unambiguous bare id; dashboard route `/pipeline/<encoded id>` (`relay/src/_ws/estate_payload.py`, `relay/src/_http/pipeline_errors.py`).
- 2026-10-07: Rewrote `config_maker/relay_config.example.json` and mirrored it in `relay/config/relay_config.example.json` (five example machines, full `scheduler` section, placeholders); both pass `--check`.
- 2026-10-07: Added `python run.py --no-log`: daily `relay/logs/<date>_info_log.log` and `_error_log.log` (child processes included) and a one-line in-place status in the terminal (`relay/src/_lib/file_logging.py`, `relay/src/_lib/status_line.py`).
- 2026-10-07: Added misconfiguration errors for `sync_pipeline` rows (`pipeline_names_other_server`, `pipeline_references_unknown_server`, `pipeline_topic_not_on_used_bus`) to `configIssues` on the root `/ws`, `/api/config/check` and the sidebar; removed `machine_without_nodes` (`relay/src/config_issues.py`).
- 2026-10-07: Made the relay stop cleanly on Ctrl+C in about 1.5 s (`relay/run.py` `timeout_graceful_shutdown`, bounded shutdown steps in `relay/src/main.py`, parallel child stop, DB driver timeouts); fixed the self-shutdown caused by `os.kill(pid, 0)` in `relay/tests/_db_lock.py`; added `RELAY_CONFIG`, quieter `httpx` logs and the `servers_share_one_database` config issue.
- 2026-10-07 (partial, task 11): Added `machine_scheduler.allowed_ips` and `scheduler.extra_allowed_ips` (`config_maker/make_config.py`), let the relay accept `allowed_ips`/`interpreters`, let `--check` accept `timeout_minutes: 0`, set the example `default_timeout_minutes` to 0, logged refused source IPs on the endpoint, added "Press any key to exit..." to the Windows scripts (`SB_NO_PAUSE=1` skips), removed `scheduler/scheduler_service.py` and stopped using `service_log_file`.
- 2026-10-06: Updated the docs for the scheduler and machine packages: new `docs/scheduler/` notes, `docs/config_maker/deploy-package.md`, runbook "Deploy a machine", data contract, and the read-only statements.
- 2026-10-06: Made one machine one package, one process and one OS service: `config_maker/machine_host.py` runs every node plus the scheduler endpoint from `deploy/<id>_<name>/machine.json`, named `SB-<id>-<name>`; `--package` uninstalls services registered from the deploy folders, then wipes and rebuilds them.
- 2026-10-06: Added a `services/` scripts folder per package: relative script paths resolve there, the interpreter is chosen by extension (`machine_scheduler.interpreters` overrides), and new scripts need no restart.
- 2026-10-06: Added Windows (pywin32 service, LocalSystem, delayed auto start, restart on failure) and Linux (systemd unit, `KillMode=process`) packages, chosen by `machines[].machine_os`.
- 2026-10-06: Made scheduled scripts run detached (`config_maker/common/scheduler_runner.py`), so they survive service restarts; removed the default timeout (`default_timeout_minutes: 0`); added Stop run.
- 2026-10-05: Ran end-to-end verification of the scheduler on web and Android (ClaudeTester).
- 2026-10-05: Added the dashboard Scheduler page (`dashboard/app/scheduler/`), schedule editor with trigger presets and next-5 preview, run history, machine-modal Schedules with Test run, and schedule/endpoint lines in the sidebar.
- 2026-10-05: Added the relay scheduler (`relay/src/_scheduler/`, `relay/src/_http/schedules.py`): UTC triggers, single-instance lock, run log with retention, endpoint health probe, schedule and endpoint alerts, `/ws` `schedules[]`, PIN-gated REST, `scheduler` config section and `machines[].machine_scheduler`.
- 2026-10-05: Added the machine scheduler endpoint (`config_maker/common/scheduler_endpoint.py`) with the SHA512 time-bracket key, relay IP allowlist and per-script lock, plus `make_config.py --check` and `--package`.
- 2026-10-05: Made the relay-down overlay cover only the page content (nav stays usable) and mounted `IssuesSidebar` on every screen (`dashboard/src/components/AppContent.tsx`).
- 2026-10-08: Added dashboard push notifications (FCM native, web push, Notifications tab, per-alert Stop push, EAS development build config); see `docs/dashboard/push-notifications.md`.
- 2026-10-08: Replaced the dashboard access code and client credentials with relay session login (bearer token, logout, session-expired lock screen) and auto push registration; web push config now comes from `google-services-web.json`.
- 2026-10-14: Pair diagnostics (`/api/pairs/{from}/{to}/entities`) now returns partial data with `sides` and `bus` availability instead of a 503 when a DB or the bus is down; added per-machine push timing via `machines[].machine_alert` (see `docs/relay/push-notifications.md`).

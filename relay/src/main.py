from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from _alerts.dedup import OpenIncidentIndex
from _http.buses import router as buses_router
from _http.config_check import router as config_check_router
from _http.config_reload import router as config_reload_router
from _http.entities import router as entities_router
from _http.health import router as health_router
from _http.ops_silence import router as ops_silence_router
from _http.pipeline_errors import router as pipeline_errors_router
from _http.downtime import router as downtime_router
from _http.origin_guard import OriginGuardMiddleware
from _http.push import router as push_router
from _http.schedules import PinGateError, pin_error_handler, router as schedules_router
from _http.auth import protected as auth_protected_router
from _http.auth import router as auth_login_router
from _lib.auth import require_session
from _lib.config import ConfigStore, StartupConfigError
from _machines.liveness import MachineLivenessLoop
from _machines.registry import MachineRegistry
from _machines.supervisor import Supervisor
from _metrics.attribution import build_object_inventory
from _metrics.bus_incident import LostAlertTracker
from _metrics.down_confirm import DownConfirm
from _metrics.pair_scan_runner import PairScanRunner
from _metrics.parity_loop import ParityLoop
from _metrics.poller import Poller
from _metrics.poll_stats import PollStats
from _ops.silence import SilenceStore
from _alerts.shared_ack import AckStore
from _push.accounts import AccountStore
from _push.dispatcher import PushDispatcher
from _push.paths import resolve_accounts_path
from _session.store import SessionStore
from _state.backup import StateBackupLoop
from _state.database import Database, StateDbError
from _state.importer import StateImportError
from _push.sources import active_alerts, alert_scopes
from _scheduler.executor_client import ExecutorClient
from _scheduler.health_probe import HealthProbeLoop
from _scheduler.loop import SchedulerLoop
from _scheduler.runlog import RunLog
from _scheduler.store import ScheduleStore
from _ws.estate_loop import EstateSnapshotLoop
from _ws.rate_limit import AckRateLimiter
from _ws.router import router as ws_router

logger = logging.getLogger("relay")


def create_app(
    config_path: Path | str, state_dir: Path | str | None = None, log_queue=None, fallback_config: bool = False,
) -> FastAPI:
    config_store = ConfigStore(config_path)
    try:
        config = config_store.load_startup()
    except StartupConfigError:
        logger.exception("refusing to start: invalid startup config at %s", config_path)
        raise

    app = FastAPI(title="Service Bus Relay", lifespan=_lifespan)
    app.state.config_store = config_store
    app.state.fallback_config = fallback_config
    app.add_exception_handler(PinGateError, pin_error_handler)
    app.add_middleware(OriginGuardMiddleware)

    app.state.poll_stats = PollStats(window_samples=config.settings.poll_stats_window_samples)

    app.state.down_confirm = DownConfirm.from_settings(config.settings)
    app.state.poller = Poller.from_settings(config.settings, app.state.down_confirm, app.state.poll_stats)
    build_object_inventory(config)

    app.state.machine_registry = MachineRegistry()
    app.state.machine_alert_incidents = OpenIncidentIndex()

    app.state.pair_scan_runner = PairScanRunner()
    app.state.pipeline_alert_incidents = OpenIncidentIndex()
    app.state.alert_previous_emitted = {}
    app.state.alert_previous_degraded = {}
    app.state.alert_previous_bus_attribution = {}
    app.state.lost_alert_tracker = LostAlertTracker.from_settings(config.settings)

    app.state.parity_loop = ParityLoop(
        config_store, app.state.down_confirm, app.state.poller, app.state.poll_stats,
        machine_registry=app.state.machine_registry,
    )

    app.state.silence_store = SilenceStore(AckRateLimiter.from_settings(config.settings))

    def _on_machine_transition(machine_id: int, is_online: bool) -> None:
        from _alerts.dedup import AlertKey
        from _alerts.severity import object_severity

        incidents = app.state.machine_alert_incidents
        key = AlertKey("machine", str(machine_id))
        if is_online:
            app.state.scheduler_loop.on_machine_online(machine_id)
            if incidents.is_open(key):
                incidents.release(key)
        else:
            if not incidents.is_open(key):
                severity = object_severity("machine", config_store.get().alerts.object_severities)
                incidents.claim(key, {"machine_id": machine_id, "severity": severity})

    app.state.machine_supervisor = Supervisor(
        config_path=config_store.path, registry=app.state.machine_registry, log_queue=log_queue,
    )
    app.state.machine_liveness = MachineLivenessLoop(
        config_store=config_store,
        registry=app.state.machine_registry,
        supervisor=app.state.machine_supervisor,
        on_transition=_on_machine_transition,
    )

    app.state.estate_loop = EstateSnapshotLoop(config_store, app.state)

    default_state_dir = Path(__file__).resolve().parents[1] / "state"
    resolved_state_dir = Path(state_dir or os.environ.get("RELAY_STATE_DIR") or default_state_dir)
    scheduler_settings = lambda: config_store.get().scheduler  # noqa: E731
    app.state.state_dir = resolved_state_dir
    try:
        app.state.db = Database.for_dir(
            resolved_state_dir, accounts_file=resolve_accounts_path(config.settings.push.accounts_file, resolved_state_dir)
        )
    except (StateImportError, StateDbError, sqlite3.Error) as exc:
        logger.error("refusing to start: state database %s: %s", resolved_state_dir, exc)
        raise StartupConfigError(f"state database in {resolved_state_dir}: {exc}") from exc
    # the fallback runs on a throwaway state dir, so it must never copy or prune anything
    app.state.state_backup_loop = None if fallback_config else StateBackupLoop(config_store, app.state.db, resolved_state_dir)
    app.state.schedule_incidents = OpenIncidentIndex()
    app.state.scheduler_endpoint_incidents = OpenIncidentIndex()
    app.state.schedule_store = ScheduleStore(resolved_state_dir)
    app.state.run_log = RunLog(resolved_state_dir, scheduler_settings)
    app.state.executor_client = ExecutorClient(scheduler_settings)
    app.state.push_tasks = set()

    def _push_snapshots() -> None:
        try:
            task = asyncio.get_running_loop().create_task(app.state.estate_loop.push_snapshots())
        except RuntimeError:
            return
        app.state.push_tasks.add(task)
        task.add_done_callback(app.state.push_tasks.discard)

    app.state.health_probe = HealthProbeLoop(
        config_store, app.state.machine_registry, app.state.executor_client,
        app.state.scheduler_endpoint_incidents, on_change=_push_snapshots,
    )
    app.state.scheduler_loop = SchedulerLoop(
        config_store, app.state.machine_registry, app.state.schedule_store, app.state.run_log,
        app.state.executor_client, app.state.health_probe, app.state.schedule_incidents,
        resolved_state_dir, on_change=_push_snapshots,
    )
    app.state.schedule_store.on_change(_push_snapshots)

    def _push_alert_source():
        if app.state.estate_loop.latest is None:
            return None
        return active_alerts(app.state, config_store.get())

    app.state.accounts = AccountStore(app.state.db)
    app.state.alert_acks = AckStore(resolved_state_dir)

    def _scope_resolver(alert, cfg):
        lookup = {str(k): v for k, v in getattr(app.state, "last_pipeline_lookup", {}).items()}

        def schedule_machine(schedule_id):
            schedule = app.state.schedule_store.get(schedule_id) if isinstance(schedule_id, int) else None
            return None if schedule is None else schedule.machine_id

        return alert_scopes(alert, cfg, lookup, schedule_machine)

    app.state.push = PushDispatcher(
        config_store, resolved_state_dir, _push_alert_source, accounts=app.state.accounts, acks=app.state.alert_acks,
        scope_resolver=_scope_resolver,
    )
    app.state.login_rate_limiter = AckRateLimiter.from_settings(config.settings)

    app.state.sessions = SessionStore(
        resolved_state_dir, lambda: app.state.accounts,
    )
    app.state.push.on_tokens_removed = app.state.sessions.revoke_for_tokens

    from _lib.config import ConfigRefreshLoop

    app.state.config_refresh_loop = ConfigRefreshLoop(config_store)

    app.include_router(health_router)
    app.include_router(ws_router)
    app.include_router(auth_login_router)

    auth_dependency = [Depends(require_session)]
    for router in (
        config_check_router,
        buses_router,
        entities_router,
        pipeline_errors_router,
        config_reload_router,
        ops_silence_router,
        schedules_router,
        push_router,
        downtime_router,
        auth_protected_router,
    ):
        app.include_router(router, dependencies=auth_dependency)

    return app


SHUTDOWN_STEP_SECONDS = 3.0


class _AbandonOnShutdownExecutor(ThreadPoolExecutor):
    # a thread stuck in a blocking driver call must not keep the process alive
    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        super().shutdown(wait=False, cancel_futures=True)


async def _bounded(awaitable, what: str) -> None:
    try:
        await asyncio.wait_for(awaitable, timeout=SHUTDOWN_STEP_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("%s did not finish within %ss; continuing", what, SHUTDOWN_STEP_SECONDS)
    except Exception:
        logger.exception("%s failed", what)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    asyncio.get_running_loop().set_default_executor(_AbandonOnShutdownExecutor(thread_name_prefix="relay"))
    tasks = [
        asyncio.create_task(app.state.parity_loop.run_forever()),
        asyncio.create_task(app.state.machine_liveness.run_forever()),
        asyncio.create_task(app.state.estate_loop.run_forever()),
        asyncio.create_task(app.state.config_refresh_loop.run_forever()),
        asyncio.create_task(app.state.health_probe.run_forever()),
        asyncio.create_task(app.state.scheduler_loop.run_forever()),
        asyncio.create_task(app.state.push.run_forever()),
    ]
    if app.state.state_backup_loop is not None:
        tasks.append(asyncio.create_task(app.state.state_backup_loop.run_forever()))
    try:
        yield
    finally:
        app.state.parity_loop.stop()
        app.state.machine_liveness.stop()
        app.state.estate_loop.stop()
        app.state.config_refresh_loop.stop()
        app.state.health_probe.stop()
        app.state.scheduler_loop.stop()
        app.state.push.stop()
        if app.state.state_backup_loop is not None:
            app.state.state_backup_loop.stop()
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_STEP_SECONDS)
        if pending:
            logger.warning("%d background task(s) did not stop within %ss", len(pending), SHUTDOWN_STEP_SECONDS)
        await _bounded(app.state.scheduler_loop.shutdown(), "scheduler shutdown")
        await _bounded(app.state.executor_client.aclose(), "executor client close")
        await asyncio.get_running_loop().run_in_executor(None, app.state.machine_supervisor.shutdown_all)
        await _bounded(asyncio.get_running_loop().run_in_executor(None, app.state.db.flush), "state database flush")

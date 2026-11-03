from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _alerts.severity import object_severity
from _scheduler.effective import effective_controls
from _scheduler.executor_client import EndpointAuthRejected, EndpointError, EndpointUnavailable, ExecutorClient
from _scheduler.health_probe import AUTH_REJECTED_TEXT, FORBIDDEN_IP_TEXT, HealthProbeLoop
from _scheduler.inflight import ActiveRun, InflightStore
from _scheduler.lock import InstanceLock
from _scheduler.model import Schedule
from _scheduler.runlog import FAIL_OUTCOMES, SUCCESS, UNCOUNTED_OUTCOMES, RunLog
from _scheduler.store import ScheduleStore
from _scheduler.triggers import is_reboot, next_fire
from _scheduler.utc import iso, now_utc, parse_iso

logger = logging.getLogger("relay.scheduler")

RELAY_START_GRACE_SECONDS = 60
TIMEOUT_GUARD_SECONDS = 600
STOPPED_TEXT = "stopped by user"
ABANDONED_TEXT = (
    "abandoned while endpoint unreachable (script may still be running on the machine; "
    "its per-script lock stays until it ends, so a later run may be skipped_locked)"
)
CHECK_TIMEOUT_SECONDS = 2.0
NOT_STARTED_OUTCOMES = frozenset({"missed", "skipped_locked"})
MAX_POLL_BACKOFF_SECONDS = 30
LAST_TICK_SAVE_SECONDS = 15
PURGE_INTERVAL_SECONDS = 24 * 3600


@dataclass
class Attempt:
    outcome: str
    reason: Optional[str] = None
    exit_code: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    script_path: Optional[str] = None
    interpreter: Optional[str] = None


@dataclass
class Dispatched:
    run_id: str
    status: str  # started | skipped_locked | missed
    reason: Optional[str] = None


class SchedulerLoop:

    def __init__(
        self,
        config_store,
        registry,
        store: ScheduleStore,
        runlog: RunLog,
        client: ExecutorClient,
        health: HealthProbeLoop,
        schedule_incidents: OpenIncidentIndex,
        state_dir: Path | str,
        on_change: Optional[Callable[[], None]] = None,
        clock: Callable[[], datetime] = now_utc,
    ):
        self._config_store = config_store
        self._registry = registry
        self._store = store
        self._runlog = runlog
        self._client = client
        self._health = health
        self._incidents = schedule_incidents
        self._on_change = on_change
        self._clock = clock
        self._inflight = InflightStore(state_dir)
        self._lock = InstanceLock(Path(state_dir) / "scheduler" / "scheduler.lock")
        self.enabled = False
        self.lock_attempted = False
        self.active: dict[str, ActiveRun] = {}
        self._cursor: dict[int, datetime] = {}
        self._cursor_sig: dict[int, str] = {}
        self._pending_start: set[int] = set()
        self._started_at: Optional[datetime] = None
        self._last_tick_saved = 0.0
        self._running = False
        self._next_purge = 0.0
        self._purge_tasks: set = set()

    @property
    def _settings(self):
        return self._config_store.get().scheduler

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def is_waiting(self, schedule_id: int) -> bool:
        return any(a.schedule_id == schedule_id and a.waiting for a in self.active.values())

    async def stop_runs(self, schedule_id: int) -> Optional[dict]:
        runs = [a for a in self.active.values() if a.schedule_id == schedule_id]
        if not runs:
            return None
        statuses = [await self._stop_run(a) for a in runs]
        status = "stopping" if "stopping" in statuses else "cancelled"
        return {"run_id": runs[0].run_id, "status": status}

    async def _stop_run(self, active: ActiveRun) -> str:
        active.cancelling = True
        machine = next((m for m in self._config_store.get().machines if m.machine_id == active.machine_id), None)
        if active.phase == "running" and machine is not None:
            try:
                await self._client.kill(machine, active.endpoint_run_id())
                return "stopping"
            except EndpointError as exc:
                logger.warning("stop of run %s: endpoint unreachable (%s); abandoning it", active.run_id, exc)
        active.abandoned = STOPPED_TEXT if active.phase == "retry_wait" else ABANDONED_TEXT
        task = active.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if active.run_id in self.active:
            now = iso(self._clock())
            self._finish(active, None, Attempt("cancelled", active.abandoned, started_at=active.started_at, ended_at=now))
        return "cancelled"

    def is_running(self, schedule_id: int) -> bool:
        return any(a.schedule_id == schedule_id for a in self.active.values())

    def start(self) -> bool:
        self.lock_attempted = True
        self.enabled = self._lock.acquire()
        if not self.enabled:
            logger.warning("scheduler disabled (another instance holds the lock)")
            return False
        self._activate()
        return True

    def _activate(self) -> None:
        self._started_at = self._clock()
        runs, last_tick = self._inflight.load()
        if last_tick:
            cursor = parse_iso(last_tick)
            for schedule in self._store.list():
                self._cursor[schedule.id] = cursor
                self._cursor_sig[schedule.id] = _signature(schedule)
        for schedule in self._store.list():
            trigger = schedule.trigger
            if schedule.enabled and ((trigger.kind == "event" and trigger.event == "relay_start") or is_reboot(trigger)):
                self._pending_start.add(schedule.id)
        for active in runs:
            if self._store.get(active.schedule_id) is None:
                continue
            self.active[active.run_id] = active
            active.task = asyncio.create_task(self._execute(active, resume=True))
            logger.info("resuming in-flight run %s of schedule %s", active.run_id, active.schedule_id)

    def _try_takeover(self) -> bool:
        if not self._lock.acquire():
            return False
        logger.info("scheduler enabled (lock acquired)")
        self.enabled = True
        self._store.reload()
        self._runlog.reset_cache()
        self._activate()
        return True

    def _purge_history(self) -> None:
        try:
            removed = self._runlog.purge_expired(self._settings.log_max_age_days, protected_run_ids=list(self.active))
            if removed:
                logger.info("run history: deleted %d run(s) older than %d days", removed, self._settings.log_max_age_days)
        except Exception as exc:
            logger.warning("run history cleanup failed (will retry tomorrow): %s", exc)

    def _maybe_purge(self) -> None:
        if not self.enabled or time.monotonic() < self._next_purge:
            return
        self._next_purge = time.monotonic() + PURGE_INTERVAL_SECONDS
        task = asyncio.get_running_loop().run_in_executor(None, self._purge_history)
        self._purge_tasks.add(task)
        task.add_done_callback(self._purge_tasks.discard)

    async def run_forever(self) -> None:
        self._running = True
        self.start()
        while self._running:
            try:
                self._maybe_purge()
                await self.tick()
            except Exception:
                logger.exception("scheduler tick failed")
            await asyncio.sleep(max(0.01, self._settings.tick_seconds))

    def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        self._running = False
        tasks = [a.task for a in self.active.values() if a.task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self.enabled:
            self._save_inflight()
        self._lock.release()
        self.enabled = False

    async def tick(self) -> None:
        now = self._clock()
        config = self._config_store.get()
        if not self.enabled and not self._try_takeover():
            self.sync_alerts(config)
            return
        self.sync_alerts(config)

        self._save_inflight_periodic(now)
        self._fire_pending_start(now)

        for schedule in self._store.list():
            sig = _signature(schedule)
            if self._cursor_sig.get(schedule.id) != sig:
                self._cursor[schedule.id] = now
                self._cursor_sig[schedule.id] = sig
                continue
            trigger = schedule.trigger
            if not schedule.enabled or trigger.kind == "event" or is_reboot(trigger):
                self._cursor[schedule.id] = now
                continue
            due = next_fire(trigger, self._cursor[schedule.id])
            if due is None or due > now:
                continue
            self._cursor[schedule.id] = now
            self._on_due(schedule, due, now)

    def _on_due(self, schedule: Schedule, due: datetime, now: datetime) -> None:
        window = schedule.controls
        if (window.active_from and now < window.active_from) or (window.active_until and now > window.active_until):
            return
        late = self._started_at is not None and due < self._started_at
        if late:
            if effective_controls(schedule, self._settings, self._machine_of(schedule))["run_if_missed"]:
                self.dispatch(schedule, "missed_catchup")
            else:
                self._record_instant(
                    schedule, "scheduled", "missed", f"relay was not running at the scheduled time ({iso(due)})"
                )
            return
        self.dispatch(schedule, "scheduled")

    def _fire_pending_start(self, now: datetime) -> None:
        for schedule_id in list(self._pending_start):
            schedule = self._store.get(schedule_id)
            if schedule is None or not schedule.enabled:
                self._pending_start.discard(schedule_id)
                continue
            runtime = self._registry.get(schedule.machine_id)
            online = runtime is not None and runtime.status == "online"
            waited = (now - self._started_at).total_seconds() if self._started_at else 0
            if online or waited >= RELAY_START_GRACE_SECONDS:
                self._pending_start.discard(schedule_id)
                self.dispatch(schedule, "event")

    def on_machine_online(self, machine_id: int) -> None:
        if not self.enabled:
            return
        for schedule in self._store.list():
            trigger = schedule.trigger
            if schedule.enabled and schedule.machine_id == machine_id and trigger.kind == "event" and trigger.event == "machine_online":
                self.dispatch(schedule, "event")

    def _after_finished(self, schedule_id: int, outcome: str) -> None:
        if outcome == SUCCESS:
            matches = ("success", "any")
        elif outcome in FAIL_OUTCOMES:
            matches = ("failure", "any")
        else:
            return
        for schedule in self._store.list():
            trigger = schedule.trigger
            if (
                schedule.enabled and trigger.kind == "event" and trigger.event == "after_schedule"
                and trigger.after_schedule_id == schedule_id and trigger.on in matches
            ):
                self.dispatch(schedule, "event")

    def _machine_of(self, schedule: Schedule):
        return next((m for m in self._config_store.get().machines if m.machine_id == schedule.machine_id), None)

    def _unavailable_reason(self, schedule: Schedule) -> Optional[str]:
        config = self._config_store.get()
        machine = next((m for m in config.machines if m.machine_id == schedule.machine_id), None)
        if machine is None:
            return f"machine {schedule.machine_id} is not in the config"
        if not machine.machine_scheduler:
            return "machine scheduler is not enabled for this machine"
        runtime = self._registry.get(machine.machine_id)
        if runtime is None or runtime.status != "online":
            return "machine offline"
        entry = self._health.state_for(machine.machine_id)
        if entry.state == "auth_rejected":
            return AUTH_REJECTED_TEXT
        if entry.state == "down":
            return "scheduler endpoint down"
        return None

    def dispatch(self, schedule: Schedule, trigger: str) -> Dispatched:
        reason = self._unavailable_reason(schedule)
        if reason is not None:
            run_id = self._record_instant(schedule, trigger, "missed", reason)
            return Dispatched(run_id, "missed", reason)

        if schedule.controls.no_overlap and self.is_running(schedule.id):
            reason = "previous run of this schedule is still running"
            run_id = self._record_instant(schedule, trigger, "skipped_locked", reason)
            return Dispatched(run_id, "skipped_locked", reason)

        eff = effective_controls(schedule, self._settings, self._machine_of(schedule))
        run_id = _new_run_id(schedule.id)
        active = ActiveRun(
            run_id=run_id, schedule_id=schedule.id, trigger=trigger, machine_id=schedule.machine_id,
            script=schedule.script, args=list(schedule.args), timeout_s=eff["timeout_s"],
            retries=eff["retries"], retry_delay_s=eff["retry_delay_s"], started_at=iso(self._clock()),
        )
        self.active[run_id] = active
        self._save_inflight()
        active.task = asyncio.create_task(self._execute(active, resume=False))
        self._notify()
        return Dispatched(run_id, "started")

    def _record_instant(self, schedule: Schedule, trigger: str, outcome: str, reason: str) -> str:
        run_id = _new_run_id(schedule.id)
        stamp = iso(self._clock())
        self._runlog.append(schedule.id, {
            "run_id": run_id, "trigger": trigger, "started_at": stamp, "ended_at": stamp,
            "attempts": 0, "exit_code": None, "outcome": outcome, "reason": reason,
            "stdout_tail": "", "stderr_tail": "", "script_path": None, "interpreter": None,
        })
        self.sync_alerts(self._config_store.get())
        if self.enabled:
            self._save_inflight()
        self._notify()
        return run_id

    async def _execute(self, active: ActiveRun, resume: bool) -> None:
        first: Optional[Attempt] = None
        last: Optional[Attempt] = None
        prev_failed: Optional[Attempt] = None
        try:
            skip_start = resume and active.phase == "running"
            while True:
                if active.phase == "retry_wait":
                    wait = (parse_iso(active.retry_at) - self._clock()).total_seconds() if active.retry_at else 0
                    await asyncio.sleep(max(0.0, wait))
                    active.attempt += 1
                    active.phase = "running"
                    active.retry_at = None
                    self._save_inflight()
                last = await self._attempt(active, skip_start)
                skip_start = False
                if prev_failed is not None and last.outcome in UNCOUNTED_OUTCOMES and last.outcome != "cancelled":
                    last = Attempt(
                        prev_failed.outcome, f"{prev_failed.reason}; retry not started: {last.reason}",
                        prev_failed.exit_code, prev_failed.stdout_tail, prev_failed.stderr_tail,
                        prev_failed.started_at, prev_failed.ended_at, prev_failed.script_path, prev_failed.interpreter,
                    )
                    break
                if first is None:
                    first = last
                if last.outcome == SUCCESS or last.outcome not in FAIL_OUTCOMES or last.outcome == "script_not_found":
                    break
                if active.attempt > active.retries:
                    break
                prev_failed = last
                active.phase = "retry_wait"
                active.retry_at = iso(self._clock() + timedelta(seconds=active.retry_delay_s))
                self._save_inflight()
        except asyncio.CancelledError:
            if not active.abandoned:
                raise
            last = Attempt(
                "cancelled", active.abandoned,
                started_at=first.started_at if first else active.started_at, ended_at=iso(self._clock()),
            )
            first = first or last
        except Exception as exc:
            logger.exception("run %s crashed inside the scheduler", active.run_id)
            last = Attempt("lost", f"scheduler error: {exc}")
            first = first or last
        self._finish(active, first, last)

    async def _attempt(self, active: ActiveRun, skip_start: bool) -> Attempt:
        config = self._config_store.get()
        machine = next((m for m in config.machines if m.machine_id == active.machine_id), None)
        if machine is None:
            return Attempt("missed", f"machine {active.machine_id} is not in the config")
        settings = config.scheduler
        endpoint_id = active.endpoint_run_id()
        started = iso(self._clock())

        if not skip_start:
            try:
                result = await self._client.start_run(
                    machine, endpoint_id, active.script, active.args, active.timeout_s, settings.log_output_tail_kb
                )
            except EndpointAuthRejected as exc:
                reason = FORBIDDEN_IP_TEXT if exc.forbidden_ip else AUTH_REJECTED_TEXT
                return Attempt("missed", reason, started_at=started, ended_at=iso(self._clock()))
            except EndpointError as exc:
                return Attempt("missed", f"scheduler endpoint unreachable: {exc}", started_at=started, ended_at=iso(self._clock()))
            if result.kind == "script_not_found":
                body = await self._with_resolved(machine, active.script, dict(result.body))
                return Attempt(
                    "script_not_found", not_found_reason(active.script, body), started_at=started, ended_at=iso(self._clock()),
                    script_path=body.get("script_path") or body.get("resolved"),
                )
            if result.kind == "locked":
                by = f" (run {result.locked_by})" if result.locked_by else ""
                return Attempt("skipped_locked", f"script already running on the machine{by}", started_at=started, ended_at=iso(self._clock()))

        poll = max(0.01, settings.tick_seconds)
        guard = active.timeout_s + TIMEOUT_GUARD_SECONDS if active.timeout_s > 0 else None
        deadline = time.monotonic() + guard if guard else None
        last_error: Optional[EndpointError] = None
        errors = 0
        while True:
            try:
                status = await self._client.get_run(machine, endpoint_id)
            except EndpointError as exc:
                last_error = exc
                errors += 1
                if not active.waiting:
                    active.waiting = True
                    self._notify()
            else:
                last_error = None
                errors = 0
                if active.waiting:
                    active.waiting = False
                    self._notify()
                if guard:
                    deadline = time.monotonic() + guard
                if status is None:
                    return Attempt("lost", "scheduler endpoint has no record of this run", started_at=started, ended_at=iso(self._clock()))
                if status.get("status") != "running":
                    return self._from_status(status, active, settings, started)
            if deadline is not None and time.monotonic() > deadline:
                if isinstance(last_error, EndpointAuthRejected):
                    return Attempt("auth_rejected", AUTH_REJECTED_TEXT, started_at=started, ended_at=iso(self._clock()))
                return Attempt("lost", f"lost contact with the scheduler endpoint: {last_error}", started_at=started, ended_at=iso(self._clock()))
            await asyncio.sleep(min(poll * 2 ** min(errors, 6), max(poll, MAX_POLL_BACKOFF_SECONDS)))

    async def _with_resolved(self, machine, script: str, body: dict) -> dict:
        if body.get("script_path") or body.get("resolved"):
            return body
        try:
            check = await asyncio.wait_for(self._client.check_script(machine, script), CHECK_TIMEOUT_SECONDS)
        except (EndpointError, asyncio.TimeoutError):
            return body
        for key in ("resolved", "error"):
            if check.get(key) and not body.get(key):
                body[key] = check[key]
        return body

    def _from_status(self, status: dict, active: ActiveRun, settings, started: str) -> Attempt:
        state = status.get("status")
        base = dict(
            exit_code=status.get("exit_code"),
            stdout_tail=status.get("stdout_tail") or "",
            stderr_tail=status.get("stderr_tail") or "",
            started_at=status.get("started_at") or started,
            ended_at=status.get("ended_at") or iso(self._clock()),
            script_path=status.get("script_path"),
            interpreter=status.get("interpreter"),
        )
        if state == "finished":
            if status.get("exit_code") in settings.success_exit_codes:
                return Attempt(SUCCESS, None, **base)
            return Attempt("failed", f"exit code {status.get('exit_code')}", **base)
        if state == "timeout":
            return Attempt("timeout", f"timed out after {int(active.timeout_s)}s" if active.timeout_s else "timed out", **base)
        if state == "killed":
            if active.cancelling:
                return Attempt("cancelled", STOPPED_TEXT, **base)
            return Attempt("failed", "killed", **base)
        if state == "lost":
            return Attempt("lost", "scheduler endpoint lost the run (service restarted?)", **base)
        return Attempt("lost", f"unexpected run status {state!r}", **base)

    def _finish(self, active: ActiveRun, first: Optional[Attempt], last: Attempt) -> None:
        attempts = 0 if last.outcome in NOT_STARTED_OUTCOMES and active.attempt == 1 else active.attempt
        self._runlog.append(active.schedule_id, {
            "run_id": active.run_id, "trigger": active.trigger,
            "started_at": (first.started_at if first else None) or active.started_at,
            "ended_at": last.ended_at or iso(self._clock()),
            "attempts": attempts, "exit_code": last.exit_code, "outcome": last.outcome,
            "reason": last.reason, "stdout_tail": last.stdout_tail, "stderr_tail": last.stderr_tail,
            "script_path": last.script_path, "interpreter": last.interpreter,
        })
        self.active.pop(active.run_id, None)
        self._save_inflight()
        self.sync_alerts(self._config_store.get())
        self._after_finished(active.schedule_id, last.outcome)
        self._notify()

    def sync_alerts(self, config) -> None:
        live = set()
        for schedule in self._store.list():
            key = AlertKey("schedule", str(schedule.id))
            live.add(key.alert_id())
            state = self._runlog.state(schedule.id)
            machine = next((m for m in config.machines if m.machine_id == schedule.machine_id), None)
            threshold = effective_controls(schedule, config.scheduler, machine)["fail_threshold"]
            if state.consecutive_failures >= threshold:
                record = {
                    "schedule_id": schedule.id,
                    "name": schedule.name,
                    "severity": object_severity("schedule", config.alerts.object_severities),
                    "consecutive_failures": state.consecutive_failures,
                    "threshold": threshold,
                    "last_reason": state.last_reason,
                }
                if self._incidents.is_open(key):
                    self._incidents.get(key).update(record)
                else:
                    self._incidents.claim(key, record)
                    logger.warning("schedule %s failed %d times in a row", schedule.id, state.consecutive_failures)
            elif self._incidents.is_open(key):
                self._incidents.release(key)
        for alert_id in self._incidents.open_ids() - live:
            self._incidents.release(AlertKey("schedule", alert_id.split(":", 1)[1]))

    def forget(self, schedule_id: int) -> None:
        self._cursor.pop(schedule_id, None)
        self._cursor_sig.pop(schedule_id, None)
        self._pending_start.discard(schedule_id)

    def _save_inflight(self) -> None:
        last_tick = iso(self._clock())
        self._inflight.save(list(self.active.values()), last_tick)
        self._last_tick_saved = time.monotonic()

    def _save_inflight_periodic(self, now: datetime) -> None:
        if time.monotonic() - self._last_tick_saved >= LAST_TICK_SAVE_SECONDS:
            self._save_inflight()


def not_found_reason(script: str, body: dict) -> str:
    path = body.get("script_path") or body.get("resolved") or script
    error = body.get("error")
    if error == "outside_scripts_dir":
        return f"script not found: {path} (outside services folder)"
    if error == "unsupported_on_this_os":
        return f"unsupported on this OS: {script}"
    if error:
        return f"script not found: {path} ({error})"
    return f"script not found: {path}"


def _signature(schedule: Schedule) -> str:
    return f"{schedule.enabled}|{schedule.trigger.model_dump_json()}"


def _new_run_id(schedule_id: int) -> str:
    return f"s{schedule_id}_{uuid.uuid4().hex[:12]}"

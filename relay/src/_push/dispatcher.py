from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from _state.database import Database
from _state.importer import DOC_PUSH_STATE
from _alerts.shared_ack import AckStore
from _push.downtime import DowntimeStore
from _push.schedule_window import is_active
from _push.accounts import AccountStore, AccountsFileError, account_key
from _push.fcm import FcmClient, build_message
from _push.mutes import MuteStore
from _push.paths import resolve_push_path
from _push.machine_alert import Cadence, resolve_cadence
from _push.sources import alert_scopes

logger = logging.getLogger("relay.push")

TITLE_LIMIT = 100


@dataclass
class AlertPushState:
    first_seen: float
    sent: dict[str, dict] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"first_seen": self.first_seen, "sent": self.sent}


class PushDispatcher:

    def __init__(
        self,
        config_store,
        state_dir: Path | str,
        alert_source: Callable[[], Optional[list[dict]]],
        *,
        accounts: Optional[AccountStore] = None,
        mutes: Optional[MuteStore] = None,
        downtime: Optional[DowntimeStore] = None,
        acks: Optional[AckStore] = None,
        scope_resolver: Optional[Callable[[dict, object], list]] = None,
        fcm_factory: Callable[[Path], FcmClient] = FcmClient,
        clock: Callable[[], float] = time.time,
    ):
        self._config_store = config_store
        self._state_dir = Path(state_dir)
        self._db = Database.for_dir(state_dir)
        self._alert_source = alert_source
        self._fcm_factory = fcm_factory
        self._clock = clock
        push = config_store.get().settings.push
        self.accounts = accounts or AccountStore(self._db)
        self.mutes = mutes or MuteStore(self._state_dir)
        self.downtime = downtime or DowntimeStore(self._state_dir)
        self.acks = acks or AckStore(self._state_dir, clock)
        self._scope_resolver = scope_resolver or alert_scopes
        self._states: dict[str, AlertPushState] = {}
        self._last_pushed: dict[str, float] = {}
        self._decisions: dict[tuple[str, str], str] = {}
        self.on_tokens_removed: Callable[[set[str]], object] = lambda tokens: None
        self._fcm: Optional[FcmClient] = None
        self._fcm_file: Optional[Path] = None
        self._availability: Optional[str] = None
        self.unavailable_reason: Optional[str] = None
        self._running = False
        self._baseline_open = True
        self._load()

    def _load(self) -> None:
        try:
            raw = self._db.get_doc(DOC_PUSH_STATE)
            if raw is None:
                return
            self._states = {k: AlertPushState(first_seen=float(v["first_seen"]), sent=dict(v.get("sent", {}))) for k, v in raw.get("alerts", {}).items()}
            self._last_pushed = {k: float(v) for k, v in raw.get("last_pushed", {}).items()}
        except (ValueError, TypeError, KeyError) as exc:
            logger.warning("push state unreadable (%s); starting fresh", exc)

    def _save(self) -> None:
        horizon = self._clock() - 86400
        self._last_pushed = {k: v for k, v in self._last_pushed.items() if v >= horizon}
        self._db.put_doc(
            DOC_PUSH_STATE,
            {"alerts": {k: v.to_json() for k, v in self._states.items()}, "last_pushed": self._last_pushed},
            durable=False,
        )

    def current_alerts(self) -> Optional[list[dict]]:
        return self._alert_source()

    @property
    def available(self) -> bool:
        return self._fcm is not None

    def _ensure_fcm(self, push) -> bool:
        key_file = resolve_push_path(push.service_account_file)
        if not key_file.is_file():
            self._fcm, self._fcm_file = None, None
            self.unavailable_reason = "push disabled: service account key missing"
            self._note_availability(f"missing:{key_file}", f"push disabled: Firebase service-account key not found at {key_file}")
            return False
        if self._fcm is None or self._fcm_file != key_file:
            try:
                self._fcm, self._fcm_file = self._fcm_factory(key_file), key_file
                _ = self._fcm.project_id
            except Exception as exc:
                self._fcm, self._fcm_file = None, None
                self.unavailable_reason = "push disabled: service account key unusable"
                self._note_availability(f"bad:{key_file}", f"push disabled: Firebase service-account key unusable ({type(exc).__name__}: {exc})")
                return False
            self._note_availability(f"ok:{key_file}", None)
        self.unavailable_reason = None
        return True

    def _note_availability(self, marker: str, warning: Optional[str]) -> None:
        if marker == self._availability:
            return
        self._availability = marker
        if warning:
            logger.warning(warning)
        else:
            logger.info("push enabled (project %s)", self._fcm.project_id if self._fcm else "?")

    def _repeat_interval(self, alert: dict, cadence: Cadence) -> float:
        acked = alert.get("acknowledged") or self.acks.is_acked(alert["alertId"])
        return cadence.acked_repeat if acked else cadence.repeat

    def _account_due(self, alert: dict, state: AlertPushState, key: str, push, now: float, cadence: Cadence) -> Optional[str]:
        record = state.sent.get(key)
        if record is None:
            if now - state.first_seen < cadence.grace:
                return None
            last = self._last_pushed.get(f"{alert['alertId']}|{key}")
            if last is not None and now - last < push.min_interval_seconds:
                return None
            return "first"
        if now - record["last"] < self._repeat_interval(alert, cadence):
            return None
        return "repeat"

    def _rebaseline(self, alert: dict, state: AlertPushState, now: float, cadence: Cadence) -> bool:
        # First evaluation after a start: a repeat overdue by more than one whole interval was missed because the relay
        # was down, so the next one comes a normal interval from now instead of every alert repeating at once.
        interval = self._repeat_interval(alert, cadence)
        moved = False
        for record in state.sent.values():
            if now - record["last"] >= 2 * interval:
                record["last"] = now
                moved = True
        if moved:
            logger.info("push: repeat timers for alert=%s re-baselined after downtime", alert["alertId"])
        return moved

    def _note(self, alert: dict, account: dict, key: str, reason: str) -> None:
        if self._decisions.get((alert["alertId"], key)) != reason:
            self._decisions[(alert["alertId"], key)] = reason
            logger.info("push suppressed alert=%s account=%s reason=%s", alert["alertId"], account["name"], reason)

    def _track(self, alerts: list[dict]) -> None:
        now = self._clock()
        current = {a["alertId"] for a in alerts}
        changed = False
        for alert_id in current - set(self._states):
            self._states[alert_id] = AlertPushState(first_seen=now)
            changed = True
        for alert_id in set(self._states) - current:
            del self._states[alert_id]
            changed = True
        self.mutes.retain_only(current)
        self._decisions = {k: v for k, v in self._decisions.items() if k[0] in current}
        self.acks.retain(current, self._config_store.get().settings.push.grace_seconds)
        if changed:
            self._save()

    def _title(self, alert: dict) -> str:
        title = f"SEV{alert['severity']} · {alert['reason']}"
        return title if len(title) <= TITLE_LIMIT else title[: TITLE_LIMIT - 1] + "…"

    def _link(self, push, alert: dict) -> str:
        base = push.dashboard_url.rstrip("/")
        return f"{base}/alerts" if base else ""

    def _send_blocking(self, jobs: list[tuple[str, dict]]) -> set[str]:
        dead: set[str] = set()
        for token, payload in jobs:
            result = self._fcm.send(payload)
            if result.dead_token:
                dead.add(token)
            elif not result.ok:
                logger.warning("push send failed for a device: %s", result.error)
        return dead

    def send_confirmation(self, account_name: str, min_severity: int, token: str, device_name: str) -> tuple[bool, Optional[str]]:
        push = self._config_store.get().settings.push
        if not push.enabled:
            return False, "push disabled in config"
        if not self._ensure_fcm(push):
            return False, self.unavailable_reason
        body = (
            f"{account_name} on {device_name or 'this device'} — you'll receive alerts of severity 1 to {min_severity} "
            "(1 = most critical)."
        )
        link = self._link(push, {})
        result = self._fcm.send(build_message(token, title="Notifications enabled", body=body, data={"type": "push_enabled"}, link=link))
        if result.ok:
            return True, None
        if result.dead_token:
            try:
                self.accounts.remove_tokens({token})
                self.on_tokens_removed({token})
            except AccountsFileError as exc:
                logger.warning("push: could not remove a rejected device token: %s", exc)
            return False, "device token rejected by FCM and removed"
        logger.warning("push confirmation failed: %s", result.error)
        return False, f"FCM error: {result.error}"

    async def tick(self) -> int:
        config = self._config_store.get()
        push = config.settings.push
        alerts = self._alert_source()
        if alerts is None:
            return 0
        self._track(alerts)
        if not push.enabled:
            return 0
        if not self._ensure_fcm(push):
            return 0
        now = self._clock()
        accounts = self.accounts.accounts()
        keys = {account_key(a["account_secret"]) for a in accounts}
        jobs: list[tuple[str, dict]] = []
        changed = False
        for alert in alerts:
            state = self._states[alert["alertId"]]
            scopes = self._scope_resolver(alert, config)
            cadence = resolve_cadence(scopes, config, push)
            downtime_ids = sorted({wid for m, svc in scopes for wid in self.downtime.active_ids(m, svc, now)})
            title, link = self._title(alert), self._link(push, alert)
            if self._baseline_open:
                changed |= self._rebaseline(alert, state, now, cadence)
            for account in accounts:
                key = account_key(account["account_secret"])
                if not account["device_codes"]:
                    continue
                if alert["severity"] > account["min_severity"]:
                    self._note(alert, account, key, "below min_severity")
                    continue
                if self.mutes.is_muted(alert["alertId"], key):
                    self._note(alert, account, key, "muted")
                    continue
                if not cadence.enabled:
                    self._note(alert, account, key, "machine alerts disabled")
                    continue
                if downtime_ids:
                    self._note(alert, account, key, f"downtime window {','.join(downtime_ids)}")
                    continue
                window = account.get("push_window")
                if window is not None and not is_active(window, now):
                    self._note(alert, account, key, "outside account push window")
                    continue
                kind = self._account_due(alert, state, key, push, now, cadence)
                if kind is None:
                    continue
                changed = True
                self._decisions[(alert["alertId"], key)] = "sent"
                record = state.sent.setdefault(key, {"count": 0})
                record["last"], record["count"] = now, record["count"] + 1
                self._last_pushed[f"{alert['alertId']}|{key}"] = now
                sound_id = f"sev{alert['severity']}_{kind}"
                logger.info("push sent alert=%s account=%s kind=%s sound=%s", alert["alertId"], account["name"], kind, sound_id)
                data = {
                    "alertId": alert["alertId"], "severity": alert["severity"], "kind": kind,
                    "alertKind": alert.get("kind", ""), "sound_id": sound_id, "url": link or "/alerts",
                }
                for device in account["device_codes"]:
                    jobs.append((device["token"], build_message(
                        device["token"], title=title, body=alert["reason"], data=data, link=link, sound_id=sound_id,
                    )))
        for state in self._states.values():
            for stale in set(state.sent) - keys:
                del state.sent[stale]
                changed = True
        self._baseline_open = False
        if changed:
            self._save()
        if not jobs:
            return 0
        dead = await asyncio.get_running_loop().run_in_executor(None, self._send_blocking, jobs)
        if dead:
            try:
                removed = self.accounts.remove_tokens(dead)
                self.on_tokens_removed(dead)
                logger.info("push: removed %d dead device token(s)", removed)
            except AccountsFileError as exc:
                logger.warning("push: could not remove dead tokens: %s", exc)
        return len(jobs)

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                await self.tick()
            except Exception:
                logger.exception("push dispatcher tick failed")
            await asyncio.sleep(1.0)

    def stop(self) -> None:
        self._running = False

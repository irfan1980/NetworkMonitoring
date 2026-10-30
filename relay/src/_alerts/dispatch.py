from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from _alerts import channel_email, channel_push_notification, channel_sms, channel_wa_message
from _alerts.dispatch_sent_guard import DEDUPED_CHANNELS, DispatchSentGuard
from _lib.redact import redact

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import DispatchSettings
    from _lib.config import ConfigStore

logger = logging.getLogger("relay.alerts.dispatch")

_CHANNEL_MODULES = {
    channel_sms.CHANNEL: channel_sms,
    channel_wa_message.CHANNEL: channel_wa_message,
    channel_email.CHANNEL: channel_email,
    channel_push_notification.CHANNEL: channel_push_notification,
}


@dataclass(frozen=True)
class DispatchOutcome:
    channel: str
    status: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class DispatchResult:
    alert_text: str
    severity: int
    alert_id: str
    channels_attempted: tuple[str, ...]
    outcomes: tuple[DispatchOutcome, ...]

    def outcome_for(self, channel: str) -> Optional[DispatchOutcome]:
        for outcome in self.outcomes:
            if outcome.channel == channel:
                return outcome
        return None


class WarnOnceRegistry:

    def __init__(self) -> None:
        self._warned: set[str] = set()

    def warn_once(self, reason: str) -> None:
        if reason in self._warned:
            return
        self._warned.add(reason)
        logger.warning("dispatch channel not configured: %s", reason)

    def has_warned(self, reason: str) -> bool:
        return reason in self._warned


def _secrets_of(settings: "DispatchSettings") -> list[str]:
    return [
        settings.twilio.account_sid,
        settings.twilio.auth_token,
        settings.smtp.password,
        settings.smtp.user,
        settings.fcm.service_account_file,
    ]


def channels_for_severity(alerts_settings, severity: int, escalated: bool) -> list[str]:
    base = list(alerts_settings.severity_channels.get(str(severity), []))
    if escalated:
        for channel in alerts_settings.escalated_channels.get(str(severity), []):
            if channel not in base:
                base.append(channel)
    return base


def _dispatch_one_channel(
    channel: str,
    alert_text: str,
    alert_id: str,
    dispatch_settings: "DispatchSettings",
    sent_guard: DispatchSentGuard,
    warn_registry: WarnOnceRegistry,
) -> DispatchOutcome:
    module = _CHANNEL_MODULES.get(channel)
    if module is None:
        return DispatchOutcome(channel=channel, status="failed", reason=f"unknown channel: {channel}")

    if channel in DEDUPED_CHANNELS and sent_guard.has_been_sent(channel, alert_id):
        return DispatchOutcome(channel=channel, status="skipped_already_sent")

    not_configured = module.is_configured(dispatch_settings)
    if not_configured is not None:
        warn_registry.warn_once(not_configured.reason)
        return DispatchOutcome(channel=channel, status="skipped_not_configured", reason=not_configured.reason)

    recipients = module.recipients_of(dispatch_settings)
    if not recipients:
        reason = f"{channel}: no recipients configured"
        warn_registry.warn_once(reason)
        return DispatchOutcome(channel=channel, status="skipped_no_recipients", reason=reason)

    if channel in DEDUPED_CHANNELS:
        sent_guard.mark_sent(channel, alert_id)

    try:
        module.send(alert_text, recipients, dispatch_settings)
    except Exception as exc:  # noqa: BLE001
        safe_reason = redact(f"{type(exc).__name__}: {exc}", _secrets_of(dispatch_settings))
        logger.error("dispatch channel %s failed for alert_id=%s: %s", channel, alert_id, safe_reason)
        return DispatchOutcome(channel=channel, status="failed", reason=safe_reason)

    return DispatchOutcome(channel=channel, status="sent")


def dispatch(
    alert_text: str,
    severity: int,
    *,
    alert_id: str,
    config_store: "ConfigStore",
    sent_guard: DispatchSentGuard,
    escalated: bool = False,
    warn_registry: Optional[WarnOnceRegistry] = None,
) -> DispatchResult:
    if warn_registry is None:
        warn_registry = WarnOnceRegistry()

    config = config_store.get()
    dispatch_settings = config.settings.dispatch
    channels = channels_for_severity(config.alerts, severity, escalated)

    outcomes = tuple(
        _dispatch_one_channel(channel, alert_text, alert_id, dispatch_settings, sent_guard, warn_registry)
        for channel in channels
    )
    return DispatchResult(
        alert_text=alert_text, severity=severity, alert_id=alert_id,
        channels_attempted=tuple(channels), outcomes=outcomes,
    )

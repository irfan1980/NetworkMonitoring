from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from _alerts._twilio_transport import send_via_twilio
from _alerts.channel_interface import NotConfigured

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import DispatchSettings

CHANNEL = "sms"

_transport_send = send_via_twilio


def is_configured(settings: "DispatchSettings") -> Optional[NotConfigured]:
    twilio = settings.twilio
    if not (twilio.account_sid and twilio.auth_token and twilio.sms_from):
        return NotConfigured(reason="sms: twilio account_sid/auth_token/sms_from not configured")
    return None


def recipients_of(settings: "DispatchSettings") -> list[str]:
    return list(settings.recipients.sms)


def send(alert_text: str, recipients: list[str], settings: "DispatchSettings") -> None:
    twilio = settings.twilio
    _transport_send(
        account_sid=twilio.account_sid,
        auth_token=twilio.auth_token,
        from_number=twilio.sms_from,
        to_numbers=recipients,
        body=alert_text,
    )

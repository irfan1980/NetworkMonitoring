from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import TYPE_CHECKING, Optional

from _alerts.channel_interface import NotConfigured

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import DispatchSettings

CHANNEL = "email"

ALERT_SUBJECT = "Service Bus Alert"


def is_configured(settings: "DispatchSettings") -> Optional[NotConfigured]:
    smtp = settings.smtp
    if not (smtp.host and smtp.from_address):
        return NotConfigured(reason="email: smtp host/from not configured")
    return None


def recipients_of(settings: "DispatchSettings") -> list[str]:
    return list(settings.recipients.email)


def _transport_send(
    *, host: str, port: int, user: str, password: str, from_address: str,
    to_addresses: list[str], subject: str, body: str,
) -> None:
    message = EmailMessage()
    message["From"] = from_address
    message["To"] = ", ".join(to_addresses)
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=10) as client:
        client.starttls()
        if user and password:
            client.login(user, password)
        client.send_message(message)


def send(alert_text: str, recipients: list[str], settings: "DispatchSettings") -> None:
    smtp = settings.smtp
    _transport_send(
        host=smtp.host, port=smtp.port, user=smtp.user, password=smtp.password,
        from_address=smtp.from_address, to_addresses=recipients,
        subject=ALERT_SUBJECT, body=alert_text,
    )

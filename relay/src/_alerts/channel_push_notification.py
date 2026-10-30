from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

import httpx

from _alerts.channel_interface import NotConfigured

if TYPE_CHECKING:  # pragma: no cover
    from _contract.schema import DispatchSettings

CHANNEL = "push_notification"

FCM_SEND_URL_TEMPLATE = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TOPIC_PREFIX = "topic:"


def is_configured(settings: "DispatchSettings") -> Optional[NotConfigured]:
    fcm = settings.fcm
    if not fcm.service_account_file:
        return NotConfigured(reason="push_notification: fcm.service_account_file not configured")
    if not fcm.tokens and not fcm.topic:
        return NotConfigured(reason="push_notification: no fcm tokens or topic configured")
    return None


def recipients_of(settings: "DispatchSettings") -> list[str]:
    fcm = settings.fcm
    if fcm.tokens:
        return list(fcm.tokens)
    if fcm.topic:
        return [f"{TOPIC_PREFIX}{fcm.topic}"]
    return []


def _access_token(service_account_file: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise RuntimeError("push_notification: google-auth package not installed") from exc
    credentials = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=[FCM_SCOPE],
    )
    credentials.refresh(Request())
    return credentials.token


def _project_id(service_account_file: str) -> str:
    with open(service_account_file, "r", encoding="utf-8") as f:
        return json.load(f)["project_id"]


def _real_transport_send(*, service_account_file: str, targets: list[str], alert_text: str) -> None:
    token = _access_token(service_account_file)
    project_id = _project_id(service_account_file)
    url = FCM_SEND_URL_TEMPLATE.format(project_id=project_id)
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=10.0) as client:
        for target in targets:
            message: dict = {"notification": {"title": "Service Bus Alert", "body": alert_text}}
            if target.startswith(TOPIC_PREFIX):
                message["topic"] = target[len(TOPIC_PREFIX):]
            else:
                message["token"] = target
            response = client.post(url, headers=headers, json={"message": message})
            response.raise_for_status()


_transport_send = _real_transport_send


def send(alert_text: str, recipients: list[str], settings: "DispatchSettings") -> None:
    _transport_send(
        service_account_file=settings.fcm.service_account_file,
        targets=recipients, alert_text=alert_text,
    )

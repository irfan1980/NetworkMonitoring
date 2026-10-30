from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import httpx

from _alerts.channel_push_notification import FCM_SCOPE, FCM_SEND_URL_TEMPLATE

logger = logging.getLogger("relay.push.fcm")

DEAD_TOKEN_CODES = {"UNREGISTERED"}
SEND_TIMEOUT_SECONDS = 10.0


@dataclass
class SendResult:
    ok: bool
    dead_token: bool = False
    error: Optional[str] = None


TTL_SECONDS = 3600


def build_message(token: str, *, title: str, body: str, data: dict, link: str, sound_id: Optional[str] = None) -> dict:
    webpush: dict = {"headers": {"Urgency": "high", "TTL": str(TTL_SECONDS)}}
    if link:
        webpush["fcm_options"] = {"link": link}
    return {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": {k: str(v) for k, v in data.items()},
            "android": {
                "priority": "HIGH",
                "ttl": f"{TTL_SECONDS}s",
                "notification": {
                    "channel_id": sound_id or "alerts",
                    "default_sound": True,
                    "default_vibrate_timings": True,
                    "notification_priority": "PRIORITY_MAX",
                    "visibility": "PUBLIC",
                },
            },
            "apns": {
                "headers": {"apns-priority": "10", "apns-push-type": "alert"},
                "payload": {"aps": {"alert": {"title": title, "body": body}, "sound": f"{sound_id}.wav" if sound_id else "default", "interruption-level": "time-sensitive"}},
            },
            "webpush": webpush,
        }
    }


def _token_rejected(response: httpx.Response) -> bool:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return False
    if str(error.get("status")) != "INVALID_ARGUMENT":
        return False
    for detail in error.get("details", []) or []:
        for violation in detail.get("fieldViolations", []) or []:
            if str(violation.get("field", "")).endswith("token"):
                return True
    return False


def _error_code(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return ""
    for detail in error.get("details", []) or []:
        code = detail.get("errorCode")
        if code:
            return str(code)
    return str(error.get("status") or "")


class FcmClient:

    def __init__(
        self,
        service_account_file: Path | str,
        *,
        http_client: Optional[httpx.Client] = None,
        token_provider: Optional[Callable[[], str]] = None,
    ):
        self._file = Path(service_account_file)
        self._http = http_client or httpx.Client(timeout=SEND_TIMEOUT_SECONDS)
        self._token_provider = token_provider
        self._credentials = None
        self._lock = threading.Lock()
        self._project_id: Optional[str] = None

    @property
    def project_id(self) -> str:
        if self._project_id is None:
            self._project_id = json.loads(self._file.read_text(encoding="utf-8"))["project_id"]
        return self._project_id

    def _access_token(self, force: bool = False) -> str:
        if self._token_provider is not None:
            return self._token_provider()
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account

        with self._lock:
            if self._credentials is None:
                self._credentials = service_account.Credentials.from_service_account_file(str(self._file), scopes=[FCM_SCOPE])
            if force or not self._credentials.valid:
                self._credentials.refresh(Request())
            return self._credentials.token

    def send(self, payload: dict) -> SendResult:
        url = FCM_SEND_URL_TEMPLATE.format(project_id=self.project_id)
        try:
            for attempt in (0, 1):
                response = self._http.post(
                    url, json=payload, headers={"Authorization": f"Bearer {self._access_token(force=attempt == 1)}"},
                    timeout=SEND_TIMEOUT_SECONDS,
                )
                if response.status_code == 401 and attempt == 0:
                    continue
                break
        except Exception as exc:
            return SendResult(False, error=f"{type(exc).__name__}: {exc}")
        if response.status_code < 300:
            return SendResult(True)
        code = _error_code(response)
        body = response.text.replace(str(payload.get("message", {}).get("token", "\0")), "<token>")[:600]
        return SendResult(False, dead_token=code in DEAD_TOKEN_CODES or _token_rejected(response), error=f"HTTP {response.status_code} {code} {body}".strip())

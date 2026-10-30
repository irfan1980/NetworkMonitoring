from __future__ import annotations

import json

from _alerts import channel_push_notification
from _contract.schema import DispatchSettings, FcmDispatchSettings


def _settings(**fcm_overrides) -> DispatchSettings:
    fcm = FcmDispatchSettings(service_account_file="/tmp/fake-service-account.json", tokens=["device-token-1"])
    for key, value in fcm_overrides.items():
        setattr(fcm, key, value)
    return DispatchSettings(fcm=fcm)


def test_channel_constant_is_push_notification_not_push():
    assert channel_push_notification.CHANNEL == "push_notification"


def test_is_configured_false_reason_when_service_account_file_missing():
    result = channel_push_notification.is_configured(_settings(service_account_file=""))
    assert result is not None
    assert "push_notification" in result.reason


def test_is_configured_false_reason_when_neither_tokens_nor_topic():
    result = channel_push_notification.is_configured(_settings(tokens=[]))
    assert result is not None
    assert "tokens or topic" in result.reason


def test_is_configured_true_with_tokens():
    assert channel_push_notification.is_configured(_settings()) is None


def test_is_configured_true_with_topic_only():
    settings = _settings(tokens=[], topic="ops-alerts")
    assert channel_push_notification.is_configured(settings) is None


def test_recipients_of_prefers_tokens_over_topic():
    settings = _settings(topic="ops-alerts")
    assert channel_push_notification.recipients_of(settings) == ["device-token-1"]


def test_recipients_of_falls_back_to_topic_prefixed_entry():
    settings = _settings(tokens=[], topic="ops-alerts")
    assert channel_push_notification.recipients_of(settings) == ["topic:ops-alerts"]


def test_send_calls_transport_with_service_account_file_and_targets(monkeypatch):
    calls = []
    monkeypatch.setattr(channel_push_notification, "_transport_send", lambda **kwargs: calls.append(kwargs))
    settings = _settings()
    channel_push_notification.send("alert text", ["device-token-1"], settings)

    assert len(calls) == 1
    assert calls[0]["service_account_file"] == "/tmp/fake-service-account.json"
    assert calls[0]["targets"] == ["device-token-1"]
    assert calls[0]["alert_text"] == "alert text"


def _real_fake_service_account_file(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    path = tmp_path / "service-account.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "test-project",
                "private_key_id": "key-1",
                "private_key": pem,
                "client_email": "relay-test@test-project.iam.gserviceaccount.com",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_real_transport_send_exercises_real_google_auth_signing_and_posts_to_fcm(tmp_path, monkeypatch):
    from google.auth.transport.requests import Request

    service_account_file = _real_fake_service_account_file(tmp_path)

    class _FakeTokenResponse:
        status = 200
        headers = {"content-type": "application/json"}
        data = json.dumps({"access_token": "real-signed-fake-token", "expires_in": 3600, "token_type": "Bearer"}).encode()

    token_calls = []

    def fake_request_call(self, url, method="POST", body=None, headers=None, timeout=120, **kwargs):
        token_calls.append((url, body))
        return _FakeTokenResponse()

    monkeypatch.setattr(Request, "__call__", fake_request_call)

    fcm_calls = []

    class _FakeHttpxResponse:
        def raise_for_status(self):
            return None

    class _FakeHttpxClient:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            fcm_calls.append({"url": url, "headers": headers, "json": json})
            return _FakeHttpxResponse()

    monkeypatch.setattr(channel_push_notification.httpx, "Client", lambda timeout=10.0: _FakeHttpxClient())

    channel_push_notification._real_transport_send(
        service_account_file=str(service_account_file),
        targets=["device-token-1"], alert_text="alert text",
    )

    assert len(token_calls) == 1
    assert len(fcm_calls) == 1
    assert fcm_calls[0]["headers"]["Authorization"] == "Bearer real-signed-fake-token"
    assert fcm_calls[0]["json"]["message"]["token"] == "device-token-1"
    assert "test-project" in fcm_calls[0]["url"]

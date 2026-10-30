from __future__ import annotations

from _alerts import channel_email
from _contract.schema import DispatchRecipients, DispatchSettings, SmtpDispatchSettings


def _settings(**smtp_overrides) -> DispatchSettings:
    smtp = SmtpDispatchSettings(
        host="smtp.example.com", port=587, user="relay@example.com",
        password="smtp_secret", from_address="relay@example.com",
    )
    for key, value in smtp_overrides.items():
        setattr(smtp, key, value)
    return DispatchSettings(smtp=smtp, recipients=DispatchRecipients(email=["ops@example.com"]))


def test_channel_constant_is_email():
    assert channel_email.CHANNEL == "email"


def test_is_configured_true_when_host_and_from_present():
    assert channel_email.is_configured(_settings()) is None


def test_is_configured_false_reason_when_host_missing():
    result = channel_email.is_configured(_settings(host=""))
    assert result is not None
    assert "email" in result.reason


def test_recipients_of_reads_email_recipients():
    assert channel_email.recipients_of(_settings()) == ["ops@example.com"]


def test_send_calls_transport_with_smtp_fields(monkeypatch):
    calls = []
    monkeypatch.setattr(channel_email, "_transport_send", lambda **kwargs: calls.append(kwargs))
    channel_email.send("alert text", ["ops@example.com"], _settings())

    assert len(calls) == 1
    assert calls[0]["host"] == "smtp.example.com"
    assert calls[0]["to_addresses"] == ["ops@example.com"]
    assert calls[0]["body"] == "alert text"
    assert calls[0]["password"] == "smtp_secret"

from __future__ import annotations

from _alerts import channel_sms
from _contract.schema import DispatchRecipients, DispatchSettings, TwilioDispatchSettings


def _settings(**twilio_overrides) -> DispatchSettings:
    twilio = TwilioDispatchSettings(
        account_sid="AC_test", auth_token="tok_test", sms_from="+15550001111",
        whatsapp_from="+15550002222",
    )
    for key, value in twilio_overrides.items():
        setattr(twilio, key, value)
    return DispatchSettings(twilio=twilio, recipients=DispatchRecipients(sms=["+15559990000"]))


def test_channel_constant_is_sms():
    assert channel_sms.CHANNEL == "sms"


def test_is_configured_true_when_twilio_credentials_present():
    assert channel_sms.is_configured(_settings()) is None


def test_is_configured_false_reason_when_account_sid_missing():
    result = channel_sms.is_configured(_settings(account_sid=""))
    assert result is not None
    assert "sms" in result.reason
    assert "not configured" in result.reason


def test_recipients_of_reads_sms_recipients():
    settings = _settings()
    assert channel_sms.recipients_of(settings) == ["+15559990000"]


def test_send_calls_transport_with_sms_from_not_whatsapp_from(monkeypatch):
    calls = []

    def fake_transport(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(channel_sms, "_transport_send", fake_transport)
    settings = _settings()
    channel_sms.send("alert text", ["+15559990000"], settings)

    assert len(calls) == 1
    assert calls[0]["from_number"] == "+15550001111"
    assert calls[0]["to_numbers"] == ["+15559990000"]
    assert calls[0]["body"] == "alert text"
    assert calls[0]["account_sid"] == "AC_test"
    assert calls[0]["auth_token"] == "tok_test"

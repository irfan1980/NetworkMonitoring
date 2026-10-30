from __future__ import annotations

from _alerts import channel_sms, channel_wa_message
from _contract.schema import DispatchRecipients, DispatchSettings, TwilioDispatchSettings


def _settings(**twilio_overrides) -> DispatchSettings:
    twilio = TwilioDispatchSettings(
        account_sid="AC_test", auth_token="tok_test", sms_from="+15550001111",
        whatsapp_from="+15550002222",
    )
    for key, value in twilio_overrides.items():
        setattr(twilio, key, value)
    return DispatchSettings(twilio=twilio, recipients=DispatchRecipients(wa_message=["+15559990000"]))


def test_channel_constant_is_wa_message_not_whatsapp():
    assert channel_wa_message.CHANNEL == "wa_message"


def test_is_configured_false_reason_when_whatsapp_from_missing():
    result = channel_wa_message.is_configured(_settings(whatsapp_from=""))
    assert result is not None
    assert "wa_message" in result.reason


def test_recipients_of_reads_wa_message_recipients_not_sms():
    settings = _settings()
    assert channel_wa_message.recipients_of(settings) == ["+15559990000"]


def test_send_calls_transport_with_whatsapp_from_not_sms_from(monkeypatch):
    calls = []
    monkeypatch.setattr(channel_wa_message, "_transport_send", lambda **kwargs: calls.append(kwargs))
    channel_wa_message.send("alert text", ["+15559990000"], _settings())
    assert calls[0]["from_number"] == "+15550002222"


def test_patching_wa_message_transport_does_not_affect_sms_transport(monkeypatch):
    wa_calls, sms_calls = [], []
    monkeypatch.setattr(channel_wa_message, "_transport_send", lambda **kwargs: wa_calls.append(kwargs))
    monkeypatch.setattr(channel_sms, "_transport_send", lambda **kwargs: sms_calls.append(kwargs))

    settings = _settings()
    settings.recipients.sms = ["+15551230000"]
    channel_wa_message.send("wa text", ["+15559990000"], settings)

    assert len(wa_calls) == 1
    assert len(sms_calls) == 0

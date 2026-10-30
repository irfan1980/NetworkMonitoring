from __future__ import annotations

import pytest

from _alerts import channel_email, channel_push_notification, channel_sms, channel_wa_message


def _real_send_attempted(*args, **kwargs):
    raise AssertionError(
        "a channel module's real _transport_send was invoked during a test run -- "
        "every test that calls send()/dispatch() must monkeypatch the relevant "
        "module's _transport_send with its own fake first."
    )


@pytest.fixture(autouse=True)
def _no_real_sends(monkeypatch):
    monkeypatch.setattr(channel_sms, "_transport_send", _real_send_attempted)
    monkeypatch.setattr(channel_wa_message, "_transport_send", _real_send_attempted)
    monkeypatch.setattr(channel_email, "_transport_send", _real_send_attempted)
    monkeypatch.setattr(channel_push_notification, "_transport_send", _real_send_attempted)

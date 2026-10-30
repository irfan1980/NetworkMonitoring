from __future__ import annotations

import pytest
from pydantic import ValidationError

from _contract.schema import DispatchRecipients, RelayConfig


def test_a_stray_non_dispatch_channel_in_severity_channels_fails_validation(config_dict):
    config_dict["alerts"] = {"severity_channels": {"1": ["alarm"]}}
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(config_dict)


def test_whatsapp_the_old_pseudocode_name_is_rejected_wa_message_is_the_real_name(config_dict):
    config_dict["alerts"] = {"severity_channels": {"1": ["whatsapp"]}}
    with pytest.raises(ValidationError):
        RelayConfig.model_validate(config_dict)


def test_the_four_real_channels_are_all_accepted(config_dict):
    config_dict["alerts"] = {
        "severity_channels": {"1": ["sms", "wa_message", "email", "push_notification"]}
    }
    RelayConfig.model_validate(config_dict)


def test_dispatch_recipients_uses_wa_message_and_push_notification_not_whatsapp_or_push():
    recipients = DispatchRecipients()
    assert hasattr(recipients, "wa_message")
    assert hasattr(recipients, "push_notification")
    assert not hasattr(recipients, "whatsapp")
    assert not hasattr(recipients, "push")

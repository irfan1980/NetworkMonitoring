from __future__ import annotations

import inspect
import logging
import time

import pytest

from _alerts import channel_email, channel_push_notification, channel_sms, channel_wa_message
from _alerts import dispatch as dispatch_module
from _alerts.dispatch import DispatchOutcome, WarnOnceRegistry, channels_for_severity, dispatch
from _alerts.dispatch_sent_guard import DispatchSentGuard
from _lib.config import ConfigStore
from _ws.rate_limit import AckRateLimiter


def _dispatch_config_dict(config_dict: dict) -> dict:
    config_dict = dict(config_dict)
    config_dict["settings"] = dict(config_dict["settings"])
    config_dict["settings"]["dispatch"] = {
        "twilio": {
            "account_sid": "AC_live_sid", "auth_token": "live_auth_token_123",
            "sms_from": "+15550001111", "whatsapp_from": "+15550002222",
        },
        "smtp": {
            "host": "smtp.example.com", "port": 587, "user": "relay@example.com",
            "password": "smtp_live_secret", "from": "relay@example.com",
        },
        "fcm": {"service_account_file": "", "tokens": [], "topic": None},
        "recipients": {
            "sms": ["+15559990000"], "wa_message": ["+15559990000"],
            "email": ["ops@example.com"], "push_notification": [],
        },
    }
    config_dict["alerts"] = {
        "severity_channels": {"1": ["sms", "wa_message", "email", "push_notification"], "3": ["email"]},
        "escalated_channels": {"1": ["push_notification"]},
    }
    return config_dict


@pytest.fixture
def dispatch_store(write_config, config_dict):
    path = write_config(_dispatch_config_dict(config_dict))
    store = ConfigStore(path)
    store.load_startup()
    return store, path


@pytest.fixture
def fake_ok_transports(monkeypatch):
    calls: dict[str, list] = {"sms": [], "wa_message": [], "email": []}
    monkeypatch.setattr(channel_sms, "_transport_send", lambda **kw: calls["sms"].append(kw))
    monkeypatch.setattr(channel_wa_message, "_transport_send", lambda **kw: calls["wa_message"].append(kw))
    monkeypatch.setattr(channel_email, "_transport_send", lambda **kw: calls["email"].append(kw))
    return calls


def test_p65_four_channel_modules_exist_with_the_shared_interface():
    for module in (channel_sms, channel_wa_message, channel_email, channel_push_notification):
        assert hasattr(module, "CHANNEL")
        assert hasattr(module, "is_configured")
        assert hasattr(module, "recipients_of")
        assert hasattr(module, "send")


def test_p65_severity_3_email_only_routes_to_the_email_module(dispatch_store, fake_ok_transports):
    store, _ = dispatch_store
    result = dispatch(
        "pipeline 1 degraded", 3, alert_id="pipeline:1",
        config_store=store, sent_guard=DispatchSentGuard(store.path.parent),
    )
    assert result.channels_attempted == ("email",)
    assert result.outcome_for("email").status == "sent"
    assert len(fake_ok_transports["email"]) == 1
    assert fake_ok_transports["sms"] == []


def test_p59_severity_channels_resolves_the_full_list_for_a_configured_severity(dispatch_store):
    store, _ = dispatch_store
    channels = channels_for_severity(store.get().alerts, 1, escalated=False)
    assert channels == ["sms", "wa_message", "email", "push_notification"]


def test_p59_unconfigured_severity_resolves_to_an_empty_list(dispatch_store):
    store, _ = dispatch_store
    assert channels_for_severity(store.get().alerts, 5, escalated=False) == []


def test_p59_escalated_unions_in_escalated_channels_without_duplicating(dispatch_store):
    store, _ = dispatch_store
    base = channels_for_severity(store.get().alerts, 1, escalated=False)
    escalated = channels_for_severity(store.get().alerts, 1, escalated=True)
    assert escalated == base

    escalated_only_severity = channels_for_severity(store.get().alerts, 3, escalated=True)
    assert escalated_only_severity == ["email"]


def test_p67_visual_states_are_not_among_the_routed_channel_modules():
    from _alerts.dispatch import _CHANNEL_MODULES

    for visual_state in ("alarm", "red_block", "full_red_screen"):
        assert visual_state not in _CHANNEL_MODULES


def test_p67_visual_state_in_config_is_a_hard_schema_refusal_not_a_relay_no_op():
    from pydantic import ValidationError

    from _contract.schema import AlertsSettings

    with pytest.raises(ValidationError):
        AlertsSettings(severity_channels={"1": ["alarm"]})


def test_p68_sms_dedupe_across_two_dispatch_calls_for_the_same_alert(dispatch_store, fake_ok_transports):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)

    dispatch("down", 3, alert_id="object:db:1", config_store=store, sent_guard=guard)
    dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)
    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    assert result.outcome_for("sms").status == "skipped_already_sent"
    assert len(fake_ok_transports["sms"]) == 1


def test_p68_sms_claim_does_not_suppress_wa_message_for_the_same_alert_id_end_to_end(dispatch_store, fake_ok_transports):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)
    guard.mark_sent("sms", "object:db:1")

    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    assert result.outcome_for("sms").status == "skipped_already_sent"
    assert result.outcome_for("wa_message").status == "sent"
    assert len(fake_ok_transports["wa_message"]) == 1


def test_p68_wa_message_claim_does_not_suppress_sms_for_the_same_alert_id_end_to_end(dispatch_store, fake_ok_transports):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)
    guard.mark_sent("wa_message", "object:db:1")

    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    assert result.outcome_for("wa_message").status == "skipped_already_sent"
    assert result.outcome_for("sms").status == "sent"
    assert len(fake_ok_transports["sms"]) == 1


def test_o9_p116_guard_is_claimed_even_when_the_send_itself_fails(dispatch_store, monkeypatch):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)
    monkeypatch.setattr(channel_sms, "_transport_send", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)
    assert result.outcome_for("sms").status == "failed"
    assert guard.has_been_sent("sms", "object:db:1") is True


def test_p69_dedupe_guard_persists_across_a_fresh_guard_instance(dispatch_store, fake_ok_transports):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)
    dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    restarted_guard = DispatchSentGuard(store.path.parent)
    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=restarted_guard)
    assert result.outcome_for("sms").status == "skipped_already_sent"


def test_p70_credentials_are_read_from_the_json_config_file(dispatch_store):
    store, _ = dispatch_store
    config = store.get()
    assert config.settings.dispatch.twilio.account_sid == "AC_live_sid"
    assert config.settings.dispatch.smtp.password == "smtp_live_secret"


def test_decision_19_recipients_are_re_read_live_on_every_dispatch_call_no_restart(
    dispatch_store, fake_ok_transports, write_config,
):
    store, path = dispatch_store
    guard = DispatchSentGuard(store.path.parent)

    result_before = dispatch("down", 3, alert_id="pipeline:1", config_store=store, sent_guard=guard)
    assert result_before.outcome_for("email").reason is None

    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["settings"]["dispatch"]["recipients"]["email"] = []
    time.sleep(0.02)
    path.write_text(json.dumps(raw), encoding="utf-8")

    result_after = dispatch("down", 3, alert_id="pipeline:2", config_store=store, sent_guard=guard)
    assert result_after.outcome_for("email").status == "skipped_no_recipients"


def test_p66_p118_two_distinct_reasons_on_two_channels_produce_two_warnings(dispatch_store, caplog, fake_ok_transports):
    store, path = dispatch_store
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["settings"]["dispatch"]["twilio"]["account_sid"] = ""
    raw["settings"]["dispatch"]["recipients"]["email"] = []
    path.write_text(json.dumps(raw), encoding="utf-8")
    store.force_reload()

    guard = DispatchSentGuard(store.path.parent)
    with caplog.at_level(logging.WARNING, logger="relay.alerts.dispatch"):
        dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("sms" in m and "not configured" in m for m in warning_messages)
    assert any("wa_message" in m and "not configured" in m for m in warning_messages)
    assert any("email" in m and "no recipients" in m for m in warning_messages)
    assert len(warning_messages) == 4


def test_p66_p118_the_same_reason_repeated_across_calls_warns_exactly_once(dispatch_store, caplog, fake_ok_transports):
    store, path = dispatch_store
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["settings"]["dispatch"]["twilio"]["account_sid"] = ""
    path.write_text(json.dumps(raw), encoding="utf-8")
    store.force_reload()

    guard = DispatchSentGuard(store.path.parent)
    warn_registry = WarnOnceRegistry()
    with caplog.at_level(logging.WARNING, logger="relay.alerts.dispatch"):
        dispatch("down", 1, alert_id="a", config_store=store, sent_guard=guard, warn_registry=warn_registry)
        dispatch("down", 1, alert_id="b", config_store=store, sent_guard=guard, warn_registry=warn_registry)
        dispatch("down", 1, alert_id="c", config_store=store, sent_guard=guard, warn_registry=warn_registry)

    sms_reason_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "sms" in r.message and "not configured" in r.message
    ]
    assert len(sms_reason_warnings) == 1


def test_p117_first_dispatch_attempts_every_channel_in_the_resolved_list(dispatch_store, fake_ok_transports):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)
    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    assert set(result.channels_attempted) == {"sms", "wa_message", "email", "push_notification"}
    assert result.outcome_for("sms").status == "sent"
    assert result.outcome_for("wa_message").status == "sent"
    assert result.outcome_for("email").status == "sent"
    assert result.outcome_for("push_notification").status == "skipped_not_configured"


def test_a_failing_channel_does_not_block_the_others(dispatch_store, fake_ok_transports, monkeypatch):
    store, _ = dispatch_store
    monkeypatch.setattr(channel_sms, "_transport_send", lambda **kw: (_ for _ in ()).throw(RuntimeError("provider down")))
    guard = DispatchSentGuard(store.path.parent)

    result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    assert result.outcome_for("sms").status == "failed"
    assert result.outcome_for("wa_message").status == "sent"
    assert result.outcome_for("email").status == "sent"


def test_no_automatic_retry_send_is_attempted_exactly_once_per_dispatch_call(dispatch_store, monkeypatch):
    store, _ = dispatch_store
    call_count = {"n": 0}

    def failing(**kw):
        call_count["n"] += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(channel_sms, "_transport_send", failing)
    monkeypatch.setattr(channel_wa_message, "_transport_send", lambda **kw: None)
    monkeypatch.setattr(channel_email, "_transport_send", lambda **kw: None)
    guard = DispatchSentGuard(store.path.parent)

    dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)
    assert call_count["n"] == 1


def test_credential_never_appears_in_any_log_record_or_returned_outcome_reason(dispatch_store, caplog, monkeypatch):
    store, _ = dispatch_store
    secret = "live_auth_token_123"

    def leaky_transport(**kw):
        raise RuntimeError(f"Twilio auth failed for token {secret}")

    monkeypatch.setattr(channel_sms, "_transport_send", leaky_transport)
    guard = DispatchSentGuard(store.path.parent)

    with caplog.at_level(logging.ERROR, logger="relay.alerts.dispatch"):
        result = dispatch("down", 1, alert_id="object:db:1", config_store=store, sent_guard=guard)

    outcome = result.outcome_for("sms")
    assert outcome.status == "failed"
    assert secret not in outcome.reason

    for record in caplog.records:
        assert secret not in record.message
        assert secret not in record.getMessage()


def test_smtp_password_never_appears_in_any_log_record_or_returned_outcome_reason(dispatch_store, caplog, monkeypatch):
    store, _ = dispatch_store
    secret = "smtp_live_secret"

    def leaky_transport(**kw):
        raise RuntimeError(f"SMTP auth failed, password={secret}")

    monkeypatch.setattr(channel_email, "_transport_send", leaky_transport)
    guard = DispatchSentGuard(store.path.parent)

    with caplog.at_level(logging.ERROR, logger="relay.alerts.dispatch"):
        result = dispatch("down", 3, alert_id="pipeline:1", config_store=store, sent_guard=guard)

    outcome = result.outcome_for("email")
    assert secret not in outcome.reason
    for record in caplog.records:
        assert secret not in record.message


def test_p62_dispatch_module_never_imports_or_references_alert_state_acknowledgement():
    source = inspect.getsource(dispatch_module)
    import_lines = [line.strip() for line in source.splitlines() if line.strip().startswith(("import ", "from "))]
    assert not any("_alerts.state" in line or "_alerts import state" in line for line in import_lines)
    assert "acknowledg" not in source.lower()


def test_p71_ack_pin_rate_limiter_is_a_structurally_separate_mechanism_from_the_dedupe_guard(dispatch_store):
    store, _ = dispatch_store
    guard = DispatchSentGuard(store.path.parent)
    ack_pin_limiter = AckRateLimiter.from_settings(store.get().settings)

    for _ in range(store.get().settings.pin_rate_limit_attempts + 1):
        ack_pin_limiter.allow("client-1")
    assert ack_pin_limiter.allow("client-1") is False

    assert guard.has_been_sent("sms", "object:db:1") is False
    guard.mark_sent("sms", "object:db:1")
    assert guard.has_been_sent("sms", "object:db:1") is True

    assert ack_pin_limiter.allow("client-2") is True

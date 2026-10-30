from __future__ import annotations

from _alerts.dispatch_sent_guard import DispatchSentGuard


def test_not_sent_initially(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    assert guard.has_been_sent("sms", "object:db:1") is False


def test_mark_sent_then_has_been_sent_true(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    guard.mark_sent("sms", "object:db:1")
    assert guard.has_been_sent("sms", "object:db:1") is True


def test_p68_sms_claim_does_not_suppress_wa_message_for_the_same_alert_id(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    guard.mark_sent("sms", "object:db:1")

    assert guard.has_been_sent("sms", "object:db:1") is True
    assert guard.has_been_sent("wa_message", "object:db:1") is False


def test_wa_message_claim_does_not_suppress_sms_for_the_same_alert_id(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    guard.mark_sent("wa_message", "object:db:1")

    assert guard.has_been_sent("wa_message", "object:db:1") is True
    assert guard.has_been_sent("sms", "object:db:1") is False


def test_different_alert_ids_do_not_cross_contaminate(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    guard.mark_sent("sms", "object:db:1")
    assert guard.has_been_sent("sms", "object:db:2") is False


def test_email_and_push_notification_are_never_deduped_by_this_guard(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    guard.mark_sent("email", "object:db:1")
    guard.mark_sent("push_notification", "object:db:1")

    assert guard.has_been_sent("email", "object:db:1") is False
    assert guard.has_been_sent("push_notification", "object:db:1") is False


def test_p69_survives_a_restart(tmp_path):
    guard = DispatchSentGuard(tmp_path)
    guard.mark_sent("sms", "object:db:1")

    restarted = DispatchSentGuard(tmp_path)
    assert restarted.has_been_sent("sms", "object:db:1") is True


def test_missing_state_file_starts_empty_never_crashes(tmp_path):
    guard = DispatchSentGuard(tmp_path / "does-not-exist-yet")
    assert guard.has_been_sent("sms", "object:db:1") is False


def test_corrupt_state_file_starts_empty_never_crashes(tmp_path):
    state_dir = tmp_path
    (state_dir / "dispatch_sent.json").write_text("{not valid json", encoding="utf-8")
    guard = DispatchSentGuard(state_dir)
    assert guard.has_been_sent("sms", "object:db:1") is False

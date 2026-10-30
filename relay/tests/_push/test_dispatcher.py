from __future__ import annotations

import json
import logging

import pytest

from _push.accounts import account_key

from .conftest import alert, device


async def test_nothing_is_sent_before_the_grace_period_and_the_payload_is_complete(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(10)
    assert push.fcm.requests == []
    await push.run(6)
    assert sorted(push.fcm.tokens()) == ["boss-1", "ops-1", "ops-2"]
    message = push.fcm.requests[0]["body"]["message"]
    assert message["notification"] == {"title": "SEV2 · machine 4 is offline", "body": "machine 4 is offline"}
    assert message["data"] == {
        "alertId": "machine:4", "severity": "2", "kind": "first", "alertKind": "machine",
        "sound_id": "sev2_first", "url": "http://localhost:8081/alerts",
    }
    assert message["android"] == {
        "priority": "HIGH", "ttl": "3600s",
        "notification": {
            "channel_id": "sev2_first", "default_sound": True, "default_vibrate_timings": True,
            "notification_priority": "PRIORITY_MAX", "visibility": "PUBLIC",
        },
    }
    assert message["apns"] == {
        "headers": {"apns-priority": "10", "apns-push-type": "alert"},
        "payload": {"aps": {
            "alert": {"title": "SEV2 · machine 4 is offline", "body": "machine 4 is offline"},
            "sound": "sev2_first.wav", "interruption-level": "time-sensitive",
        }},
    }
    assert "mutable-content" not in message["apns"]["payload"]["aps"] and "content-available" not in message["apns"]["payload"]["aps"]
    assert message["webpush"] == {
        "headers": {"Urgency": "high", "TTL": "3600"}, "fcm_options": {"link": "http://localhost:8081/alerts"},
    }
    assert push.fcm.requests[0]["url"] == "https://fcm.googleapis.com/v1/projects/demo-project/messages:send"
    assert push.fcm.requests[0]["auth"] == "Bearer access-token"


async def test_an_alert_that_clears_inside_the_grace_period_never_pushes(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(10)
    push.alerts = []
    await push.run(60)
    assert push.fcm.requests == []


async def test_a_reappearing_alert_gets_a_fresh_grace_period(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(10)
    push.alerts = []
    await push.run(1)
    push.alerts = [alert()]
    await push.run(10)
    assert push.fcm.requests == []
    await push.run(6)
    assert push.fcm.requests


async def test_flapping_alert_is_limited_to_one_push_per_min_interval(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    sent_first = len(push.fcm.requests)
    assert sent_first == 3
    push.alerts = []
    await push.run(5)
    push.alerts = [alert()]
    await push.run(60)
    assert len(push.fcm.requests) == sent_first
    await push.run(80)
    assert len(push.fcm.requests) == sent_first * 2


async def test_no_recovery_push(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(20)
    push.fcm.clear()
    push.alerts = []
    await push.run(300)
    assert push.fcm.requests == []


async def test_account_min_severity_filters_recipients(push):
    push.alerts = [alert("a:3", severity=3, reason="sev three"), alert("a:1", severity=1, reason="sev one")]
    await push.dispatcher.tick()
    await push.run(16)
    by_alert = {}
    for request in push.fcm.requests:
        by_alert.setdefault(request["body"]["message"]["data"]["alertId"], set()).add(request["body"]["message"]["token"])
    assert by_alert["a:3"] == {"boss-1"}
    assert by_alert["a:1"] == {"boss-1", "ops-1", "ops-2"}


async def test_muted_alert_skips_that_account_only_and_the_mute_dies_with_the_alert(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    push.dispatcher.mutes.mute("machine:4", account_key("1111"))
    await push.run(16)
    assert push.fcm.tokens() == ["boss-1"]
    push.fcm.clear()
    push.dispatcher.mutes.unmute("machine:4", account_key("1111"))
    await push.run(2)
    assert sorted(push.fcm.tokens()) == ["ops-1", "ops-2"]
    push.dispatcher.mutes.mute("machine:4", account_key("1111"))
    push.alerts = []
    await push.run(2)
    assert not push.dispatcher.mutes.is_muted("machine:4", account_key("1111"))


async def test_mutes_persist_across_restarts(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    push.dispatcher.mutes.mute("machine:4", account_key("2222"))
    again = push.new_dispatcher()
    assert again.mutes.is_muted("machine:4", account_key("2222"))


async def test_dead_tokens_are_removed_and_transient_errors_are_kept(push):
    push.write_accounts([{
        "account_secret": "1111", "name": "Ops", "min_severity": 5,
        "device_codes": [device("ok-1"), device("dead-1"), device("bad-1"), device("err-1"), device("invalid-1")],
    }])
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    remaining = [d["token"] for d in push.read_accounts()[0]["device_codes"]]
    assert remaining == ["ok-1", "bad-1", "err-1"]


async def test_missing_service_account_key_disables_push_with_one_warning_then_recovers(push, caplog):
    push.key_path.unlink()
    push.alerts = [alert()]
    with caplog.at_level(logging.WARNING, logger="relay.push"):
        await push.dispatcher.tick()
        await push.run(40)
    warnings = [r for r in caplog.records if "push disabled" in r.getMessage()]
    assert len(warnings) == 1 and "service-account key not found" in warnings[0].getMessage()
    assert push.fcm.requests == [] and not push.dispatcher.available
    push.key_path.write_text(json.dumps({"project_id": "demo-project"}))
    await push.run(2)
    assert push.dispatcher.available and push.fcm.requests


async def test_unusable_key_file_is_reported_once_and_the_relay_keeps_running(push, caplog):
    push.key_path.write_text("{not json")
    push.alerts = [alert()]
    with caplog.at_level(logging.WARNING, logger="relay.push"):
        await push.dispatcher.tick()
        await push.run(40)
    assert len([r for r in caplog.records if "unusable" in r.getMessage()]) == 1
    assert push.fcm.requests == []


async def test_disabled_push_sends_nothing(push):
    raw = json.loads(push.config_store.path.read_text())
    raw["settings"]["push"]["enabled"] = False
    push.config_store.path.write_text(json.dumps(raw))
    push.config_store.force_reload()
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(60)
    assert push.fcm.requests == []


async def test_restart_does_not_resend_an_alert_that_was_already_pushed(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    await push.run(16)
    sent = len(push.fcm.requests)
    again = push.new_dispatcher()
    push.dispatcher = again
    await push.run(120)
    assert len(push.fcm.requests) == sent


async def test_owner_edits_to_the_accounts_file_are_picked_up_while_running(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    push.write_accounts([{"account_secret": "9999", "name": "New", "min_severity": 5, "device_codes": [device("new-1")]}])
    await push.run(16)
    assert push.fcm.tokens() == ["new-1"]


async def test_alert_source_not_ready_changes_nothing(push):
    push.dispatcher._alert_source = lambda: None
    assert await push.dispatcher.tick() == 0


async def test_long_titles_are_cut(push):
    push.alerts = [alert(reason="x" * 300)]
    await push.dispatcher.tick()
    await push.run(16)
    title = push.fcm.requests[0]["body"]["message"]["notification"]["title"]
    assert len(title) <= 100 and title.endswith("…")


async def test_state_file_never_contains_secrets(push):
    push.alerts = [alert()]
    await push.dispatcher.tick()
    push.dispatcher.mutes.mute("machine:4", account_key("1111"))
    await push.run(16)
    text = "".join(p.read_text() for p in (push.tmp / "state" / "push").glob("*.json"))
    assert "1111" not in text and "2222" not in text and "ops-1" not in text


def test_webpush_without_a_dashboard_url_keeps_the_urgency_headers_and_drops_the_link():
    from _push.fcm import build_message

    message = build_message("t", title="a", body="b", data={"x": 1}, link="")["message"]
    assert message["webpush"] == {"headers": {"Urgency": "high", "TTL": "3600"}}
    assert message["data"] == {"x": "1"}


@pytest.mark.parametrize("status,body,dead", [
    (404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}, True),
    (400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad channel"}}, False),
    (400, {"error": {"status": "INVALID_ARGUMENT", "details": [{"fieldViolations": [{"field": "message.data"}]}]}}, False),
    (400, {"error": {"status": "INVALID_ARGUMENT", "details": [{"fieldViolations": [{"field": "message.token"}]}]}}, True),
    (500, {"error": {"status": "INTERNAL"}}, False),
    (503, {"error": {"status": "UNAVAILABLE"}}, False),
    (429, {"error": {"status": "QUOTA_EXCEEDED"}}, False),
])
def test_only_permanent_token_errors_mark_a_token_dead(tmp_path, status, body, dead):
    import httpx

    from _push.fcm import FcmClient, build_message

    key = tmp_path / "key.json"
    key.write_text(json.dumps({"project_id": "p"}))
    client = FcmClient(key, http_client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))), token_provider=lambda: "t")
    result = client.send(build_message("secret-token-1", title="a", body="b", data={}, link=""))
    assert result.dead_token is dead and not result.ok
    assert "secret-token-1" not in result.error and str(status) in result.error


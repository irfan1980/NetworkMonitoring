import pytest

from _ws.rate_limit import AckRateLimiter
from _ws.session import STARTING_UP_ENGINE, STARTING_UP_SOURCE, WsSession, starting_up_snapshot


class _Sink:
    def __init__(self):
        self.sent: list[dict] = []

    async def __call__(self, payload: dict) -> None:
        self.sent.append(payload)


def _limiter(attempts=5, window=60, lockout=60, now=None):
    return AckRateLimiter(attempts, window, lockout, now=now or (lambda: 0.0))


def _session(build_snapshot, **kwargs):
    sink = _Sink()
    session = WsSession(send_json=sink, ack_limiter=_limiter(), build_snapshot=build_snapshot, **kwargs)
    return session, sink


@pytest.mark.asyncio
async def test_p43_p44_hello_first_then_snapshot_once():
    calls = []

    def build(seq):
        calls.append(seq)
        return {"type": "snapshot", "seq": seq, "ts": "2026-01-01T00:00:00Z"}

    session, sink = _session(build)
    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})

    assert session.hello_received is True
    assert len(sink.sent) == 1
    assert sink.sent[0]["type"] == "snapshot"
    assert calls == [1]


@pytest.mark.asyncio
async def test_p105_invalid_hello_is_silently_dropped_no_reply():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"})
    await session.handle_inbound({"type": "hello"})
    assert session.hello_received is False
    assert sink.sent == []


@pytest.mark.asyncio
async def test_p106_messages_before_hello_other_than_hello_are_ignored():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"})
    await session.handle_inbound({"type": "resync"})
    assert session.hello_received is False
    assert sink.sent == []


@pytest.mark.asyncio
async def test_p106_post_hello_only_resync_ack_silence_are_recognised():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"})
    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})
    sink.sent.clear()

    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})
    await session.handle_inbound({"type": "unknown-type"})
    assert sink.sent == []


@pytest.mark.asyncio
async def test_p45_resync_sends_a_fresh_snapshot_at_the_current_seq_not_a_new_one():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"})
    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})
    assert session.seq == 1
    sink.sent.clear()

    await session.handle_inbound({"type": "resync"})
    assert session.seq == 1
    assert sink.sent[0]["seq"] == 1


@pytest.mark.asyncio
async def test_p45_delta_after_a_state_change_increments_seq():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"})
    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})
    await session.send_delta(lambda seq: {"type": "delta", "seq": seq, "ts": "t"})
    assert session.seq == 2
    assert sink.sent[-1] == {"type": "delta", "seq": 2, "ts": "t"}


@pytest.mark.asyncio
async def test_p46_alert_and_ackresult_message_types():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"})
    await session.send_alert({"type": "alert", "alertId": "a1", "rootCause": "db:1", "severity": 2})
    await session.send_ack_result("a1", True)
    assert sink.sent[0]["type"] == "alert"
    assert sink.sent[1] == {"type": "ackResult", "alertId": "a1", "ok": True, "reason": None}


@pytest.mark.asyncio
async def test_p104_starting_up_stub_sent_when_not_ready_instead_of_a_blackout():
    session, sink = _session(lambda seq: {"type": "snapshot", "seq": seq, "ts": "t", "real": True}, is_ready=lambda: False)
    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})
    sent = sink.sent[0]
    assert sent["source"] == STARTING_UP_SOURCE
    assert sent["engine"] == STARTING_UP_ENGINE
    assert "real" not in sent


def test_p104_starting_up_snapshot_is_schema_valid_on_its_own():
    from _ws.messages import validate_outbound

    assert validate_outbound(starting_up_snapshot(1))


@pytest.mark.asyncio
async def test_p108_ack_rate_limiting_shares_the_pin_limiter_shape_and_replies_rate_limited():
    received = []

    async def on_ack(msg):
        received.append(msg.alertId)

    now = [0.0]
    limiter = AckRateLimiter(attempts=2, window_seconds=60, lockout_seconds=60, now=lambda: now[0])
    sink = _Sink()
    session = WsSession(send_json=sink, ack_limiter=limiter, build_snapshot=lambda seq: {"type": "snapshot", "seq": seq, "ts": "t"}, on_ack=on_ack)
    await session.handle_inbound({"type": "hello", "device": "d1", "clientId": "c1"})

    for _ in range(2):
        await session.handle_inbound({"type": "ack", "alertId": "a1", "clientId": "c1"})
    await session.handle_inbound({"type": "ack", "alertId": "a1", "clientId": "c1"})

    assert received == ["a1", "a1"]
    assert sink.sent[-1] == {"type": "ackResult", "alertId": "a1", "ok": False, "reason": "rate_limited"}

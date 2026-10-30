import socket

import pytest

from _db.common import tcp_check
from _metrics.readability import ReadStatus


@pytest.fixture
def unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_refused_port_is_down_not_unread(unused_loopback_port):
    status = await tcp_check("127.0.0.1", unused_loopback_port, timeout_ms=3000)
    assert status == ReadStatus.DOWN


async def test_blackhole_ip_times_out_to_down():
    status = await tcp_check("192.0.2.1", 80, timeout_ms=300)
    assert status == ReadStatus.DOWN


async def test_unreachable_host_confirms_down_not_stuck_ok(monkeypatch):
    from _metrics.down_confirm import DownConfirm

    down_confirm = DownConfirm()
    raw = await tcp_check("192.0.2.1", 1433, timeout_ms=300)
    confirmed = down_confirm.observe("db:mssql:1003:query", raw)

    assert raw == ReadStatus.DOWN
    assert confirmed == ReadStatus.DOWN


async def test_a_real_open_port_is_ok():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        status = await tcp_check("127.0.0.1", port, timeout_ms=3000)
    assert status == ReadStatus.OK

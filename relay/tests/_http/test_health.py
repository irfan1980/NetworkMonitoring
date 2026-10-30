from fastapi.testclient import TestClient

from main import create_app


def test_health_ok_no_auth(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "pollStats" in body


def test_health_poll_stats_empty_state_before_any_cycle(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    body = client.get("/health").json()
    stats = body["pollStats"]
    assert stats["samples"] == 0
    assert stats["avg_ms"] is None


def test_health_poll_stats_reflects_recorded_cycles(write_config, config_dict):
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    app.state.poll_stats.record(120.0)
    app.state.poll_stats.record(80.0)

    stats = client.get("/health").json()["pollStats"]
    assert stats["samples"] == 2
    assert stats["avg_ms"] == 100.0
    assert stats["min_ms"] == 80.0
    assert stats["max_ms"] == 120.0
    assert stats["last_cycle_ts"] is not None


def test_health_poll_stats_window_samples_c23_non_default_actually_truncates(write_config, config_dict):
    config_dict["settings"]["poll_stats_window_samples"] = 2
    path = write_config(config_dict)
    app = create_app(path)
    client = TestClient(app)

    app.state.poll_stats.record(10.0)
    app.state.poll_stats.record(20.0)
    app.state.poll_stats.record(30.0)

    stats = client.get("/health").json()["pollStats"]
    assert stats["window_samples"] == 2
    assert stats["samples"] == 2
    assert stats["min_ms"] == 20.0
    assert stats["max_ms"] == 30.0

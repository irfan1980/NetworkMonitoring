import errno
import json
from pathlib import Path

import pytest

from _lib.atomic_write import atomic_write_json


def test_writes_json_under_state_dir(tmp_path: Path):
    result = atomic_write_json(tmp_path, "alert_state.json", data={"a": 1})
    assert result == tmp_path / "alert_state.json"
    assert json.loads(result.read_text(encoding="utf-8")) == {"a": 1}


def test_creates_nested_parent_directories(tmp_path: Path):
    result = atomic_write_json(tmp_path, "machine_2", "log.json", data={"ok": True})
    assert result.parent.is_dir()
    assert json.loads(result.read_text(encoding="utf-8")) == {"ok": True}


def test_overwrites_existing_file_atomically(tmp_path: Path):
    atomic_write_json(tmp_path, "state.json", data={"v": 1})
    atomic_write_json(tmp_path, "state.json", data={"v": 2})
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")) == {"v": 2}


def test_refuses_escape_before_opening_anything_o10(tmp_path: Path):
    before = set(tmp_path.iterdir())
    with pytest.raises(ValueError):
        atomic_write_json(tmp_path, "..", "escaped.json", data={})
    after = set(tmp_path.iterdir())
    assert before == after


def test_retries_transient_windows_sharing_violation_then_succeeds(tmp_path: Path, monkeypatch):
    calls = {"n": 0}
    real_replace = __import__("os").replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(errno.EACCES, "sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky_replace)
    result = atomic_write_json(tmp_path, "state.json", data={"v": 1}, retry_attempts=5, backoff_ms_per_attempt=1)
    assert calls["n"] == 3
    assert json.loads(result.read_text(encoding="utf-8")) == {"v": 1}


def test_gives_up_after_retry_attempts_exhausted(tmp_path: Path, monkeypatch):
    def always_flaky(src, dst):
        raise OSError(errno.EBUSY, "always busy")

    monkeypatch.setattr("os.replace", always_flaky)
    with pytest.raises(OSError):
        atomic_write_json(tmp_path, "state.json", data={"v": 1}, retry_attempts=3, backoff_ms_per_attempt=1)


def test_does_not_retry_a_non_retryable_error(tmp_path: Path, monkeypatch):
    calls = {"n": 0}

    def not_found(src, dst):
        calls["n"] += 1
        raise OSError(errno.ENOENT, "gone")

    monkeypatch.setattr("os.replace", not_found)
    with pytest.raises(OSError):
        atomic_write_json(tmp_path, "state.json", data={"v": 1}, retry_attempts=5, backoff_ms_per_attempt=1)
    assert calls["n"] == 1

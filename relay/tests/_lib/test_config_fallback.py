from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from _contract.schema import RelayConfig
from _lib.config_fallback import FALLBACK_CONFIG_PATH, MainConfigWatcher, uses_fallback, validate_config_file

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "relay_config.example.json"


def good_config() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_the_fallback_is_the_example_next_to_the_relay_code():
    assert FALLBACK_CONFIG_PATH == EXAMPLE or FALLBACK_CONFIG_PATH.name  # RELAY_FALLBACK_CONFIG may override it
    assert validate_config_file(EXAMPLE) is None


def test_only_a_missing_main_file_means_fallback(tmp_path):
    assert uses_fallback(tmp_path / "nope.json") is True
    assert uses_fallback(tmp_path) is True
    broken = tmp_path / "relay_config.json"
    broken.write_text("{nope")
    assert uses_fallback(broken) is False  # an existing but invalid file must refuse to start, never fall back
    broken.write_text("")
    assert uses_fallback(broken) is False


@pytest.mark.parametrize("text,needle", [("", "not valid JSON"), ("{trunc", "not valid JSON"), ("[]", "schema"), ('{"settings": {}}', "schema")])
def test_invalid_files_give_a_reason(tmp_path, text, needle):
    path = tmp_path / "c.json"
    path.write_text(text)
    assert needle in validate_config_file(path)


def test_a_good_file_validates(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(good_config()))
    assert validate_config_file(path) is None
    assert validate_config_file(tmp_path / "missing.json").startswith("could not be read")


def wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def watcher(path, ready):
    w = MainConfigWatcher(path, lambda: ready.set(), interval=0.05)
    w.start()
    return w


def test_watcher_switches_once_a_valid_file_appears_and_is_stable(tmp_path):
    path = tmp_path / "relay_config.json"
    ready = threading.Event()
    w = watcher(path, ready)
    time.sleep(0.3)
    assert not ready.is_set()
    path.write_text(json.dumps(good_config()))
    assert wait(ready.is_set)
    w.join(2)
    assert not w.is_alive()


def test_invalid_file_is_logged_once_per_change_and_retried_when_it_changes(tmp_path, caplog):
    path = tmp_path / "relay_config.json"
    ready = threading.Event()
    caplog.set_level(logging.ERROR, logger="relay.config")
    path.write_text("{half")
    w = watcher(path, ready)
    time.sleep(0.6)
    assert not ready.is_set()
    assert caplog.text.count("staying on the fallback config") == 1
    path.write_text('{"settings": {}}')
    assert wait(lambda: caplog.text.count("staying on the fallback config") == 2)
    time.sleep(0.4)
    assert caplog.text.count("staying on the fallback config") == 2
    path.write_text(json.dumps(good_config()))
    assert wait(ready.is_set)
    w.join(2)


def test_a_file_still_being_uploaded_is_not_validated_until_it_stops_changing(tmp_path):
    path = tmp_path / "relay_config.json"
    ready = threading.Event()
    calls = []
    w = MainConfigWatcher(path, ready.set, interval=0.1, validate=lambda p: calls.append(1) or None)
    w.start()
    for n in range(8):
        path.write_text("x" * (n + 1))
        time.sleep(0.04)
    assert calls == [] and not ready.is_set()
    assert wait(ready.is_set)
    assert calls == [1]
    w.join(2)


def test_watcher_survives_a_validator_crash_and_can_be_stopped(tmp_path):
    path = tmp_path / "relay_config.json"
    path.write_text("x")
    w = MainConfigWatcher(path, lambda: None, interval=0.05, validate=lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    w.start()
    time.sleep(0.4)
    assert w.is_alive()
    w.stop()
    w.join(2)
    assert not w.is_alive()

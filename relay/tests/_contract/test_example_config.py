from __future__ import annotations

import json
import re
from pathlib import Path

from _contract.schema import RelayConfig

RELAY_EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "relay_config.example.json"
# The example is the relay's blank fallback config (used while relay_config.json is missing); it no longer mirrors
# config_maker/relay_config.example.json, and config_maker/ is not in the repository.


def good_config() -> dict:
    return json.loads(RELAY_EXAMPLE.read_text(encoding="utf-8"))


def test_the_example_is_valid_minimal_and_holds_nothing_real():
    raw = good_config()
    config = RelayConfig.model_validate(raw)
    assert raw["settings"]["bind_host"] == "127.0.0.1" and raw["settings"]["port"] == 8085
    assert raw["settings"]["push"] == {"enabled": False}
    assert raw["settings"]["auth"] == {}
    assert raw["settings"]["ack_pin"].startswith("REPLACE_ME")
    assert raw["service_busses"] == [] and raw["alerts"] == {} and "scheduler" not in raw
    assert [m.machine_ip for m in config.machines] == ["127.0.0.1"] and len(config.machines) == 1
    machine = config.machines[0]
    assert machine.nodes == [] and machine.machine_scheduler is False and config.servers == []
    text = RELAY_EXAMPLE.read_text(encoding="utf-8")
    import re

    ips = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))
    assert ips <= {"127.0.0.1"}, ips
    for word in ("password", "sas_key", "secret", "token", "private_key", "account", "firebase", "twilio", "smtp"):
        assert word not in text.lower(), word

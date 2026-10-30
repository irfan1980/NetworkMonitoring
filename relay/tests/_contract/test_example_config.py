from __future__ import annotations

import json
from pathlib import Path

from _contract.schema import RelayConfig

RELAY_EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "relay_config.example.json"
CANONICAL = Path(__file__).resolve().parents[3] / "config_maker" / "relay_config.example.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_relay_example_loads_through_the_schema():
    config = RelayConfig.model_validate(_load(RELAY_EXAMPLE))
    assert [m.machine_id for m in config.machines] == [1, 2, 3, 4, 5]
    assert [s.id for s in config.servers] == [1001, 1002, 2001, 3001]
    assert config.machines[3].nodes == [] and config.machines[3].machine_scheduler is True
    assert config.machines[4].machine_scheduler is False


def test_relay_example_mirrors_the_config_maker_template():
    relay, canonical = _load(RELAY_EXAMPLE), _load(CANONICAL)
    for key in ("machines", "service_busses", "scheduler"):
        assert relay[key] == canonical[key], key


def test_relay_example_keeps_only_placeholder_secrets():
    relay = _load(RELAY_EXAMPLE)
    assert relay["settings"]["ack_pin"].startswith("REPLACE_ME")
    assert all(v.startswith("REPLACE_ME") for k, v in relay["settings"]["auth"].items() if k.endswith("secret"))

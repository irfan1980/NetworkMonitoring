from __future__ import annotations

from types import SimpleNamespace

from _contract.schema import RelayConfig, Settings
from _http.live_attribution import refresh_attribution
from _metrics.down_confirm import DownConfirm
from _metrics.poller import Poller


def _settings() -> Settings:
    return Settings(
        auth={"web_client_id": "w", "web_client_secret": "w", "app_client_id": "a", "app_client_secret": "a"},
        ack_pin="1234",
    )


def _config(settings: Settings) -> RelayConfig:
    return RelayConfig.model_validate({
        "settings": settings.model_dump(by_alias=True), "alerts": {}, "machines": [],
    })


class _FakeConfigStore:
    def __init__(self, config: RelayConfig):
        self._config = config

    def get(self) -> RelayConfig:
        return self._config


class _FakeParityLoop:
    def __init__(self, latest=None):
        self.latest = latest


async def test_returns_the_cached_loop_result_without_running_a_new_cycle():
    settings = _settings()
    config = _config(settings)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)

    class _PollerThatMustNotRun(Poller):
        async def run_cycle(self, specs):
            raise AssertionError("refresh_attribution() must not run a fresh cycle when a cached result exists")

    sentinel = object()
    app_state = SimpleNamespace(
        config_store=_FakeConfigStore(config), poller=_PollerThatMustNotRun.from_settings(settings, down_confirm),
        down_confirm=down_confirm, parity_loop=_FakeParityLoop(latest=sentinel),
    )

    result = await refresh_attribution(app_state)
    assert result is sentinel


async def test_falls_back_to_an_on_demand_cycle_when_the_loop_has_not_ticked_yet():
    settings = _settings()
    config = _config(settings)
    down_confirm = DownConfirm.from_settings(settings)
    poller = Poller.from_settings(settings, down_confirm)
    parity_loop = _FakeParityLoop(latest=None)
    app_state = SimpleNamespace(
        config_store=_FakeConfigStore(config), poller=poller, down_confirm=down_confirm, parity_loop=parity_loop,
    )

    result = await refresh_attribution(app_state)
    assert result is not None
    assert parity_loop.latest is result

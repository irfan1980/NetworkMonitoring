from __future__ import annotations

from typing import TYPE_CHECKING

from _metrics.attribution import AttributionResult, build_object_inventory, compute_attribution
from _metrics.poll_specs import build_all_poll_specs

if TYPE_CHECKING:
    from fastapi import FastAPI


async def refresh_attribution(app_state: "FastAPI.state") -> AttributionResult:
    parity_loop = getattr(app_state, "parity_loop", None)
    if parity_loop is not None and parity_loop.latest is not None:
        return parity_loop.latest

    config_store = app_state.config_store
    config = config_store.get()
    specs = build_all_poll_specs(config, config.settings)
    await app_state.poller.run_cycle(specs)
    inventory = build_object_inventory(config)
    result = compute_attribution(inventory, app_state.down_confirm)
    if parity_loop is not None:
        parity_loop.latest = result
    return result

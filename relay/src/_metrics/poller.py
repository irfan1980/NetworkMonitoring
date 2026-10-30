from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from _lib.expected_errors import describe_driver_error
from _lib.transition_log import TransitionLogger
from _metrics.down_confirm import DownConfirm
from _metrics.poll_stats import PollStats
from _metrics.readability import ReadStatus

if TYPE_CHECKING:
    from _contract.schema import Settings

logger = logging.getLogger("relay.metrics.poller")
_stage_transition_log = TransitionLogger()

StageFn = Callable[[], Awaitable[ReadStatus]]


@dataclass
class PollSpec:

    object_id_prefix: str
    stages: list[tuple[str, StageFn]]


@dataclass
class PollCycleResult:
    generation: int
    published: bool
    duration_ms: float
    metrics: dict[str, ReadStatus]
    confirmed: dict[str, ReadStatus]


async def _run_stage_chain(
    object_id_prefix: str,
    stages: list[tuple[str, StageFn]],
    per_object_timeout_ms: int,
    object_budget_ms: int,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, ReadStatus]:
    results: dict[str, ReadStatus] = {}
    remaining_budget_ms = float(object_budget_ms)

    for stage_name, stage_fn in stages:
        object_id = f"{object_id_prefix}:{stage_name}"
        if remaining_budget_ms <= 0:
            results[object_id] = ReadStatus.UNREAD
            continue

        stage_timeout_ms = min(per_object_timeout_ms, remaining_budget_ms)
        started = clock()
        try:
            status = await asyncio.wait_for(stage_fn(), timeout=stage_timeout_ms / 1000)
        except asyncio.TimeoutError:
            status = ReadStatus.UNREAD
            _stage_transition_log.down(logger, object_id, f"{object_id} timed out")
        except Exception as exc:
            status = ReadStatus.UNREAD
            platform = object_id.split(":")[1] if object_id.startswith("db:") else None
            is_expected, message = describe_driver_error(exc, platform, object_id) if platform else (False, str(exc))
            if is_expected:
                _stage_transition_log.down(logger, object_id, f"{object_id}: {message}")
            else:
                logger.error("poller: stage %s raised unexpectedly", object_id, exc_info=True)
        else:
            _stage_transition_log.ok(logger, object_id, object_id)
        elapsed_ms = (clock() - started) * 1000
        remaining_budget_ms -= elapsed_ms
        results[object_id] = status

    return results


class Poller:

    def __init__(
        self,
        down_confirm: DownConfirm,
        poll_stats: Optional[PollStats] = None,
        per_object_timeout_ms: int = 4000,
        object_budget_ms: int = 15000,
        poll_hard_timeout_ms: int = 15000,
        stage_chain_clock: Callable[[], float] = time.monotonic,
    ):
        self._down_confirm = down_confirm
        self._poll_stats = poll_stats
        self.per_object_timeout_ms = per_object_timeout_ms
        self.object_budget_ms = object_budget_ms
        self.poll_hard_timeout_ms = poll_hard_timeout_ms
        self._stage_chain_clock = stage_chain_clock

        self._poll_generation = 0
        self._published_generation = 0
        self._publish_lock = asyncio.Lock()
        self._ever_known_object_ids: set[str] = set()

    @classmethod
    def from_settings(cls, settings: "Settings", down_confirm: DownConfirm, poll_stats: Optional[PollStats] = None) -> "Poller":
        return cls(
            down_confirm=down_confirm,
            poll_stats=poll_stats,
            per_object_timeout_ms=settings.per_server_timeout_ms,
            object_budget_ms=settings.server_budget_ms,
            poll_hard_timeout_ms=settings.poll_hard_timeout_ms,
        )

    def known_object_ids(self, specs: list[PollSpec]) -> set[str]:
        return {f"{spec.object_id_prefix}:{stage_name}" for spec in specs for stage_name, _ in spec.stages}

    async def run_cycle(self, specs: list[PollSpec]) -> PollCycleResult:
        self._poll_generation += 1
        generation = self._poll_generation
        started = time.monotonic()

        gather_coro = asyncio.gather(
            *(
                _run_stage_chain(
                    spec.object_id_prefix, spec.stages, self.per_object_timeout_ms, self.object_budget_ms,
                    clock=self._stage_chain_clock,
                )
                for spec in specs
            ),
            return_exceptions=True,
        )
        try:
            chain_results = await asyncio.wait_for(gather_coro, timeout=self.poll_hard_timeout_ms / 1000)
        except asyncio.TimeoutError:
            chain_results = [BaseException() for _ in specs]

        metrics: dict[str, ReadStatus] = {}
        for spec, chain_result in zip(specs, chain_results):
            if isinstance(chain_result, BaseException):
                for stage_name, _ in spec.stages:
                    metrics[f"{spec.object_id_prefix}:{stage_name}"] = ReadStatus.UNREAD
            else:
                metrics.update(chain_result)

        duration_ms = (time.monotonic() - started) * 1000

        async with self._publish_lock:
            if generation < self._published_generation:
                return PollCycleResult(
                    generation=generation, published=False, duration_ms=duration_ms, metrics=metrics, confirmed={}
                )

            self._ever_known_object_ids |= self.known_object_ids(specs)
            confirmed: dict[str, ReadStatus] = {}
            for object_id in self._ever_known_object_ids:
                raw_status = metrics.get(object_id, ReadStatus.UNREAD)
                confirmed[object_id] = self._down_confirm.observe(object_id, raw_status)

            self._published_generation = generation
            if self._poll_stats is not None:
                self._poll_stats.record(duration_ms)

            return PollCycleResult(
                generation=generation, published=True, duration_ms=duration_ms, metrics=metrics, confirmed=confirmed
            )

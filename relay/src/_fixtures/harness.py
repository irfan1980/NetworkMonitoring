from __future__ import annotations

from typing import Any, Callable, Protocol


class _ScenarioLike(Protocol):
    code: str
    seed: Callable[[], dict[str, Any]]
    teardown: Callable[[dict[str, Any]], None]


def seed_with_recovery(scn: _ScenarioLike, code: str, seeded: list[tuple]) -> dict[str, Any]:
    try:
        facts = scn.seed()
    except Exception:
        try:
            scn.teardown({"tag": code})
        except Exception:
            pass
        raise
    seeded.append((scn, facts))
    return facts


def teardown_all(seeded: list[tuple]) -> None:
    teardown_errors: list[BaseException] = []
    for scn, facts in reversed(seeded):
        try:
            scn.teardown(facts)
        except BaseException as exc:  # noqa: BLE001
            teardown_errors.append(exc)
    if teardown_errors:
        raise RuntimeError(
            f"{len(teardown_errors)} of {len(seeded)} fixture teardown(s) failed: "
            f"{[repr(e) for e in teardown_errors]}"
        ) from teardown_errors[0]

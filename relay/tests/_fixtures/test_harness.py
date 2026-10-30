from __future__ import annotations

import pytest

from _fixtures.harness import seed_with_recovery, teardown_all


class _FakeScenario:
    def __init__(self, code: str, seed_fn, teardown_fn):
        self.code = code
        self.seed = seed_fn
        self.teardown = teardown_fn


def test_seed_with_recovery_returns_facts_and_records_it_on_success():
    seeded: list[tuple] = []
    scn = _FakeScenario("FIXOK", lambda: {"tag": "FIXOK", "extra": 1}, lambda facts: None)
    facts = seed_with_recovery(scn, "FIXOK", seeded)
    assert facts == {"tag": "FIXOK", "extra": 1}
    assert seeded == [(scn, facts)]


def test_seed_with_recovery_attempts_best_effort_teardown_on_a_mid_seed_failure():
    teardown_calls: list[dict] = []

    def failing_seed():
        raise RuntimeError("platform B insert failed")

    scn = _FakeScenario("FIXFAIL", failing_seed, lambda facts: teardown_calls.append(facts))
    seeded: list[tuple] = []

    with pytest.raises(RuntimeError, match="platform B insert failed"):
        seed_with_recovery(scn, "FIXFAIL", seeded)

    assert teardown_calls == [{"tag": "FIXFAIL"}]
    assert seeded == []


def test_seed_with_recovery_never_masks_the_original_failure_even_if_best_effort_teardown_also_fails():
    def failing_seed():
        raise RuntimeError("the real, original failure")

    def teardown_needs_more_keys(facts):
        return facts["bus"]

    scn = _FakeScenario("FIXFAIL2", failing_seed, teardown_needs_more_keys)
    seeded: list[tuple] = []

    with pytest.raises(RuntimeError, match="the real, original failure"):
        seed_with_recovery(scn, "FIXFAIL2", seeded)


def test_teardown_all_runs_every_teardown_even_if_an_earlier_one_raises():
    calls: list[str] = []

    def teardown_a(facts):
        calls.append("a")
        raise RuntimeError("a's teardown failed")

    def teardown_b(facts):
        calls.append("b")

    def teardown_c(facts):
        calls.append("c")

    scn_a = _FakeScenario("A", lambda: {"tag": "A"}, teardown_a)
    scn_b = _FakeScenario("B", lambda: {"tag": "B"}, teardown_b)
    scn_c = _FakeScenario("C", lambda: {"tag": "C"}, teardown_c)

    seeded = [(scn_a, {"tag": "A"}), (scn_b, {"tag": "B"}), (scn_c, {"tag": "C"})]

    with pytest.raises(RuntimeError, match=r"1 of 3 fixture teardown\(s\) failed"):
        teardown_all(seeded)

    assert calls == ["c", "b", "a"]


def test_teardown_all_raises_nothing_when_every_teardown_succeeds():
    calls: list[str] = []
    scn = _FakeScenario("A", lambda: {"tag": "A"}, lambda facts: calls.append("a"))
    seeded = [(scn, {"tag": "A"})]
    teardown_all(seeded)
    assert calls == ["a"]


def test_teardown_all_collects_multiple_failures_not_just_the_first():
    def failing(facts):
        raise ValueError(f"failed for {facts['tag']}")

    scn_a = _FakeScenario("A", lambda: {"tag": "A"}, failing)
    scn_b = _FakeScenario("B", lambda: {"tag": "B"}, failing)
    seeded = [(scn_a, {"tag": "A"}), (scn_b, {"tag": "B"})]

    with pytest.raises(RuntimeError, match=r"2 of 2 fixture teardown\(s\) failed"):
        teardown_all(seeded)

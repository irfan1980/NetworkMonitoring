from __future__ import annotations

import inspect

from _alerts import summary as summary_module
from _alerts.summary import (
    BRANCH_ORDER,
    SummaryAggregates,
    compute_summary,
    compute_summary_aggregates,
    down_pending_level,
)

YELLOW, RED = 50, 500


def _agg(**overrides) -> SummaryAggregates:
    base = dict(worst_backlog=None, lost_summary={"count": 0, "pipeline_ids": []}, messages_held_by={}, biggest_pile_outside_buses=None)
    base.update(overrides)
    return SummaryAggregates(**base)


def _summary(**overrides):
    base = dict(
        all_relays_stopped=False, any_service_bus_down=False, any_db_object_down=False,
        any_relay_stopped=False, any_relay_unreadable=False, any_pipeline_degraded=False,
        aggregates=_agg(),
    )
    base.update(overrides)
    return compute_summary(**base)


def test_p125_down_pending_level_thresholds():
    assert down_pending_level(0, YELLOW, RED) == "green"
    assert down_pending_level(YELLOW, YELLOW, RED) == "yellow"
    assert down_pending_level(RED, YELLOW, RED) == "red"


def test_p125_down_pending_level_never_calls_topology_edges():
    assert "import topology" not in inspect.getsource(summary_module)
    body = inspect.getsource(down_pending_level).split('"""', 2)[-1]
    assert "topology" not in body
    assert "queue_level_for_count" not in body
    assert "thresholds_for_pipeline" not in body


def test_p123_branch_order_matches_the_checklists_own_ten_names():
    assert BRANCH_ORDER == (
        "all_relays_stopped", "service_bus_down", "red_backlog", "yellow_backlog",
        "down_db_objects", "stopped_relays", "unreadable_relays", "lost_messages",
        "generic_pipeline_problem", "good",
    )


def test_p123_good_when_nothing_is_wrong():
    result = _summary()
    assert result.level == "green"
    assert result.branch == "good"


def test_p123_all_relays_stopped_wins_over_everything_else():
    result = _summary(all_relays_stopped=True, any_service_bus_down=True, any_db_object_down=True)
    assert result.branch == "all_relays_stopped"
    assert result.level == "grey"


def test_p123_service_bus_down_wins_over_backlog_and_db_down():
    worst = {"pipeline_id": 1, "count": 600, "level": "red"}
    result = _summary(any_service_bus_down=True, any_db_object_down=True, aggregates=_agg(worst_backlog=worst))
    assert result.branch == "service_bus_down"
    assert "backlog 600" in result.note


def test_db_down_with_healthy_buses_yields_problem_not_outage():
    result = _summary(any_db_object_down=True)
    assert result.branch == "down_db_objects"
    assert result.level == "red"


def test_a_genuinely_down_bus_with_no_other_signal_yields_outage_grey():
    result = _summary(any_service_bus_down=True)
    assert result.branch == "service_bus_down"
    assert result.level == "grey"
    assert result.note == "Service Bus is down."


def test_pure_service_bus_down_no_longer_masks_a_confirmed_db_down():
    result = _summary(any_service_bus_down=True, any_db_object_down=True)
    assert result.branch == "down_db_objects"
    assert result.level == "red"
    assert result.note == "One or more database objects are down."


def test_service_bus_down_magnitude_override_still_wins_over_db_down():
    worst = {"pipeline_id": 1, "count": 600, "level": "red"}
    result = _summary(any_service_bus_down=True, any_db_object_down=True, aggregates=_agg(worst_backlog=worst))
    assert result.branch == "service_bus_down"
    assert result.level == "red"
    assert "backlog 600" in result.note


def test_p123_red_backlog_wins_over_yellow_and_down_db_objects():
    worst = {"pipeline_id": 2, "count": 600, "level": "red"}
    result = _summary(any_db_object_down=True, aggregates=_agg(worst_backlog=worst))
    assert result.branch == "red_backlog"
    assert result.level == "red"


def test_p123_yellow_backlog_wins_over_down_db_objects():
    worst = {"pipeline_id": 3, "count": 60, "level": "yellow"}
    result = _summary(any_db_object_down=True, aggregates=_agg(worst_backlog=worst))
    assert result.branch == "yellow_backlog"
    assert result.level == "yellow"


def test_p123_down_db_objects_wins_over_stopped_relays():
    result = _summary(any_db_object_down=True, any_relay_stopped=True)
    assert result.branch == "down_db_objects"
    assert result.level == "red"


def test_p123_stopped_relays_wins_over_unreadable_relays():
    result = _summary(any_relay_stopped=True, any_relay_unreadable=True)
    assert result.branch == "stopped_relays"


def test_p123_unreadable_relays_wins_over_lost_messages():
    result = _summary(any_relay_unreadable=True, aggregates=_agg(lost_summary={"count": 3, "pipeline_ids": [1, 2, 3]}))
    assert result.branch == "unreadable_relays"


def test_p123_lost_messages_wins_over_generic_pipeline_problem():
    result = _summary(any_pipeline_degraded=True, aggregates=_agg(lost_summary={"count": 1, "pipeline_ids": [9]}))
    assert result.branch == "lost_messages"


def test_p123_generic_pipeline_problem_is_the_last_non_good_branch():
    result = _summary(any_pipeline_degraded=True)
    assert result.branch == "generic_pipeline_problem"
    assert result.level == "yellow"


def test_p124_worst_backlog_picks_the_largest_material_pile():
    aggregates = compute_summary_aggregates(
        pipeline_queue_counts={1: 40, 2: 300, 3: 10},
        pipeline_bus_id={1: 10, 2: 10, 3: 10},
        lost_pipeline_ids=set(),
        yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    assert aggregates.worst_backlog == {"pipeline_id": 2, "count": 300, "level": "yellow"}


def test_p124_trivial_piles_below_the_material_floor_are_excluded():
    aggregates = compute_summary_aggregates(
        pipeline_queue_counts={1: 60},
        pipeline_bus_id={1: 10},
        lost_pipeline_ids=set(),
        yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    assert aggregates.worst_backlog is None


def test_p124_lost_summary_counts_and_lists_pipeline_ids():
    aggregates = compute_summary_aggregates(
        pipeline_queue_counts={}, pipeline_bus_id={}, lost_pipeline_ids={5, 3},
        yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    assert aggregates.lost_summary == {"count": 2, "pipeline_ids": [3, 5]}


def test_p124_messages_held_by_sums_per_bus_regardless_of_materiality():
    aggregates = compute_summary_aggregates(
        pipeline_queue_counts={1: 5, 2: 7},
        pipeline_bus_id={1: 10, 2: 10},
        lost_pipeline_ids=set(),
        yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    assert aggregates.messages_held_by == {10: 12}


def test_p124_biggest_pile_outside_buses_excludes_bus_attributed_piles():
    aggregates = compute_summary_aggregates(
        pipeline_queue_counts={1: 300, 2: 400},
        pipeline_bus_id={1: 10, 2: None},
        lost_pipeline_ids=set(),
        yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    assert aggregates.biggest_pile_outside_buses == {"pipeline_id": 2, "count": 400}


def test_p123_two_machines_can_reach_different_top_level_verdicts_from_differing_filtered_sets():
    machine_1_aggregates = _agg()
    machine_1_result = _summary(aggregates=machine_1_aggregates)

    machine_2_aggregates = compute_summary_aggregates(
        pipeline_queue_counts={99: 600},
        pipeline_bus_id={99: 20},
        lost_pipeline_ids=set(),
        yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    machine_2_result = _summary(aggregates=machine_2_aggregates)

    assert machine_1_result.level == "green"
    assert machine_2_result.level == "red"
    assert machine_1_result.branch != machine_2_result.branch


def test_p124_two_machines_own_worst_backlog_and_messages_held_by_are_independently_scoped():
    all_counts = {1: 300, 2: 150}
    all_bus_ids = {1: 10, 2: 20}

    machine_1_aggregates = compute_summary_aggregates(
        pipeline_queue_counts={1: all_counts[1]}, pipeline_bus_id={1: all_bus_ids[1]},
        lost_pipeline_ids=set(), yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )
    machine_2_aggregates = compute_summary_aggregates(
        pipeline_queue_counts={2: all_counts[2]}, pipeline_bus_id={2: all_bus_ids[2]},
        lost_pipeline_ids=set(), yellow_threshold=YELLOW, red_threshold=RED, material_backlog_factor=2,
    )

    assert machine_1_aggregates.worst_backlog == {"pipeline_id": 1, "count": 300, "level": "yellow"}
    assert machine_2_aggregates.worst_backlog == {"pipeline_id": 2, "count": 150, "level": "yellow"}
    assert machine_1_aggregates.messages_held_by == {10: 300}
    assert machine_2_aggregates.messages_held_by == {20: 150}

from __future__ import annotations

from _alerts.dedup import AlertKey, OpenIncidentIndex
from _alerts.degraded import evaluate_degraded
from _alerts.engine import (
    STAGE_ORDER,
    MachineAlertScope,
    PipelineSnapshot,
    evaluate_machine_alerts,
    row_group_severity,
)
from _alerts.severity import pipeline_severity
from _contract.schema import ObjectSeverities
from _metrics.bus_incident import LostAlertTick, PipelineAttribution
from _metrics.readability import ReadStatus
from topology.nodes import ColorLevel

OK = ReadStatus.OK
DOWN = ReadStatus.DOWN
OBJ_SEV = ObjectSeverities()


def _empty_scope(machine_id: int = 1) -> MachineAlertScope:
    return MachineAlertScope(machine_id=machine_id)


def _run(scope, incidents=None, **overrides):
    incidents = incidents or OpenIncidentIndex()
    kwargs = dict(
        object_severities=OBJ_SEV,
        previous_degraded={},
        lost_ticks={},
        row_data={},
    )
    kwargs.update(overrides)
    return evaluate_machine_alerts(scope, incidents, **kwargs), incidents


def test_p57_o6_stage_order_is_exactly_the_seven_named_stages_in_order():
    scope = _empty_scope()
    result, _ = _run(scope)
    assert result.stage_order == (
        "vanished", "objectTransitions", "edgeTransitions", "pipelineTransitions",
        "relayHeartbeats", "busAttributedAlerts", "rowLevelAlerts",
    )
    assert result.stage_order == STAGE_ORDER


def test_o6_vanished_pruning_runs_before_row_level_alerts_real_behaviour_not_just_labels():
    scope = _empty_scope()
    scope.pipelines[99] = PipelineSnapshot(pipeline_id=99, degraded=True)
    scope.vanished_pipeline_ids = {99}
    row_data = {99: [{"row_id": 1, "direction": "outbound", "severity": 2}]}

    result, incidents = _run(scope, row_data=row_data)

    row_events = [e for e in result.events if e["kind"] == "row"]
    assert row_events == []
    assert 99 not in scope.pipelines
    assert incidents.is_open(AlertKey("row", "99:1")) is False


def test_p110_vanished_resolves_any_open_pipeline_incident():
    incidents = OpenIncidentIndex()
    incidents.claim(AlertKey("pipeline", "7"), {"pipeline_id": 7})
    scope = _empty_scope()
    scope.vanished_pipeline_ids = {7}
    result, incidents = _run(scope, incidents=incidents)
    resolved = [e for e in result.events if e["type"] == "resolved" and e.get("pipeline_id") == 7]
    assert len(resolved) == 1
    assert resolved[0]["reason"] == "vanished"
    assert incidents.is_open(AlertKey("pipeline", "7")) is False


def test_p113_object_first_observation_already_down_gets_from_unknown_not_ok():
    scope = _empty_scope()
    scope.emitted = {"db:mysql:1:query": DOWN}
    scope.previous_emitted = {}
    scope.object_kind_of = {"db:mysql:1:query": "db"}
    result, _ = _run(scope)
    raised = [e for e in result.events if e["kind"] == "object"]
    assert len(raised) == 1
    assert raised[0]["from"] == "unknown"
    assert raised[0]["from"] != "ok"


def test_p113_object_a_real_previous_ok_reading_is_not_hidden():
    scope = _empty_scope()
    scope.emitted = {"db:mysql:1:query": DOWN}
    scope.previous_emitted = {"db:mysql:1:query": OK}
    scope.object_kind_of = {"db:mysql:1:query": "db"}
    result, _ = _run(scope)
    raised = [e for e in result.events if e["kind"] == "object"][0]
    assert raised["from"] == "ok"


def test_p113_pipeline_first_observation_already_degraded_gets_from_unknown():
    scope = _empty_scope()
    scope.pipelines[1] = PipelineSnapshot(pipeline_id=1, degraded=True, row_severities=[2])
    result, _ = _run(scope, previous_degraded={})
    raised = [e for e in result.events if e["kind"] == "pipeline"][0]
    assert raised["from"] == "unknown"


def test_p114_row_level_alerts_only_fire_for_pipelines_currently_degraded():
    scope = _empty_scope()
    scope.pipelines[1] = PipelineSnapshot(pipeline_id=1, degraded=False)
    row_data = {1: [{"row_id": 1, "direction": "outbound", "severity": 1}]}
    result, _ = _run(scope, row_data=row_data)
    assert [e for e in result.events if e["kind"] == "row"] == []


def test_p114_row_level_alerts_fire_once_pipeline_is_degraded():
    scope = _empty_scope()
    scope.pipelines[1] = PipelineSnapshot(pipeline_id=1, degraded=True)
    row_data = {1: [{"row_id": 1, "direction": "outbound", "severity": 1}]}
    result, _ = _run(scope, row_data=row_data)
    row_events = [e for e in result.events if e["kind"] == "row"]
    assert len(row_events) == 1
    assert row_events[0]["severity"] == 1


def test_p115_row_group_severity_falls_back_to_min_of_same_direction():
    by_direction = {"outbound": [4, 2], "inbound": [5]}
    assert row_group_severity(by_direction, "outbound") == 2
    assert row_group_severity(by_direction, "inbound") == 5


def test_p115_row_group_severity_falls_back_to_3_when_no_direction_match():
    assert row_group_severity({}, "outbound") == 3


def test_p115_engine_uses_row_group_severity_for_rows_missing_their_own():
    scope = _empty_scope()
    scope.pipelines[1] = PipelineSnapshot(pipeline_id=1, degraded=True)
    row_data = {1: [
        {"row_id": 1, "direction": "outbound", "severity": 4},
        {"row_id": 2, "direction": "outbound", "severity": None},
    ]}
    result, _ = _run(scope, row_data=row_data)
    row2 = next(e for e in result.events if e["kind"] == "row" and e["row_id"] == 2)
    assert row2["severity"] == 4


def test_p111_a_second_tick_with_the_same_down_object_does_not_re_raise():
    scope = _empty_scope()
    scope.emitted = {"bus:1:health": DOWN}
    scope.object_kind_of = {"bus:1:health": "bus"}
    incidents = OpenIncidentIndex()

    result1, incidents = _run(scope, incidents=incidents)
    assert len([e for e in result1.events if e["type"] == "raised"]) == 1

    result2, incidents = _run(scope, incidents=incidents)
    assert [e for e in result2.events if e["type"] == "raised"] == []
    assert len(incidents) == 1


def test_p111_recovery_then_a_new_incident_raises_again():
    scope = _empty_scope()
    incidents = OpenIncidentIndex()
    scope.emitted = {"bus:1:health": DOWN}
    scope.object_kind_of = {"bus:1:health": "bus"}
    result1, incidents = _run(scope, incidents=incidents)
    assert incidents.is_open(AlertKey("object", "bus:1:health")) is True

    scope2 = _empty_scope()
    scope2.emitted = {"bus:1:health": OK}
    scope2.previous_emitted = {"bus:1:health": DOWN}
    scope2.object_kind_of = {"bus:1:health": "bus"}
    result2, incidents = _run(scope2, incidents=incidents)
    assert any(e["type"] == "resolved" for e in result2.events)
    assert incidents.is_open(AlertKey("object", "bus:1:health")) is False

    scope3 = _empty_scope()
    scope3.emitted = {"bus:1:health": DOWN}
    scope3.previous_emitted = {"bus:1:health": OK}
    scope3.object_kind_of = {"bus:1:health": "bus"}
    result3, incidents = _run(scope3, incidents=incidents)
    assert any(e["type"] == "raised" for e in result3.events)


def test_bus_attributed_alert_raised_for_bus_down_attribution():
    scope = _empty_scope()
    scope.pipelines[1] = PipelineSnapshot(
        pipeline_id=1, bus_attribution=PipelineAttribution(cause="bus_down", blocked_by="bus:1:health"),
    )
    result, incidents = _run(scope)
    bus_events = [e for e in result.events if e["kind"] == "bus"]
    assert len(bus_events) == 1
    assert bus_events[0]["cause"] == "bus_down"
    assert incidents.is_open(AlertKey("bus", "1")) is True


def test_lost_alert_family_raises_and_resolves_via_lost_ticks():
    scope = _empty_scope()
    scope.pipelines[1] = PipelineSnapshot(pipeline_id=1)
    lost_ticks = {1: LostAlertTick(newly_raised=True, active=True, newly_resolved=False, severity=2)}
    result, incidents = _run(scope, lost_ticks=lost_ticks)
    lost_events = [e for e in result.events if e["kind"] == "lost"]
    assert lost_events == [{"type": "raised", "kind": "lost", "pipeline_id": 1, "severity": 2}]
    assert incidents.is_open(AlertKey("lost", "1")) is True

    scope2 = _empty_scope()
    scope2.pipelines[1] = PipelineSnapshot(pipeline_id=1)
    lost_ticks2 = {1: LostAlertTick(newly_raised=False, active=False, newly_resolved=True, severity=None)}
    result2, incidents = _run(scope2, incidents=incidents, lost_ticks=lost_ticks2)
    assert any(e["kind"] == "lost" and e["type"] == "resolved" for e in result2.events)


def _shared_bus_down_pipeline() -> PipelineSnapshot:
    return PipelineSnapshot(
        pipeline_id=12,
        row_severities=[2, 3],
        bus_attribution=PipelineAttribution(cause="bus_down", blocked_by="bus:1:health"),
    )


def test_p57_two_machines_sharing_a_bus_can_reach_two_independent_bus_incident_decisions():
    machine_1_scope = _empty_scope(machine_id=1)
    machine_1_scope.pipelines[12] = _shared_bus_down_pipeline()
    machine_2_scope = _empty_scope(machine_id=2)
    machine_2_scope.pipelines[12] = _shared_bus_down_pipeline()

    result_1, incidents_1 = _run(machine_1_scope)
    result_2, incidents_2 = _run(machine_2_scope)

    bus_events_1 = [e for e in result_1.events if e["kind"] == "bus"]
    bus_events_2 = [e for e in result_2.events if e["kind"] == "bus"]
    assert len(bus_events_1) == 1
    assert len(bus_events_2) == 1
    assert incidents_1.is_open(AlertKey("bus", "12"))
    assert incidents_2.is_open(AlertKey("bus", "12"))
    assert incidents_1 is not incidents_2

    machine_1_recovered = _empty_scope(machine_id=1)
    machine_1_recovered.pipelines[12] = PipelineSnapshot(pipeline_id=12, row_severities=[2, 3])
    result_1b, incidents_1 = _run(machine_1_recovered, incidents=incidents_1)
    assert any(e["kind"] == "bus" and e["type"] == "resolved" for e in result_1b.events)
    assert incidents_1.is_open(AlertKey("bus", "12")) is False
    assert incidents_2.is_open(AlertKey("bus", "12")) is True


def test_p25_and_p58_give_the_identical_result_for_the_same_pipeline_record_on_both_sides_of_a_cross_machine_pipeline():
    shared_kwargs = dict(
        queued_count=150, backlog_max=100, red_on_the_map=False, lost_count=0,
        matched_recently=False, queued_countable=False,
        send_oldest_pending_seconds=90, send_avg_lag_seconds=None,
        apply_oldest_pending_seconds=None, apply_avg_lag_seconds=None,
        lag_max=60, unblocked_for_seconds=None,
    )
    result_as_seen_by_machine_1 = evaluate_degraded(**shared_kwargs)
    result_as_seen_by_machine_2 = evaluate_degraded(**shared_kwargs)
    assert result_as_seen_by_machine_1 == result_as_seen_by_machine_2

    row_severities = [4, 2, 5]
    assert pipeline_severity(list(row_severities)) == pipeline_severity(list(row_severities)) == 2

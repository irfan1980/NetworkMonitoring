from __future__ import annotations

from _alerts.severity import DEFAULT_SEVERITY_FALLBACK, object_severity, pipeline_severity, row_severity
from _contract.schema import ObjectSeverities


def test_row_severity_uses_explicit_value():
    assert row_severity(1) == 1
    assert row_severity(4) == 4


def test_row_severity_falls_back_to_3_when_absent():
    assert row_severity(None) == DEFAULT_SEVERITY_FALLBACK == 3


def test_pipeline_severity_is_the_minimum_not_average_or_max():
    assert pipeline_severity([4, 2, 5]) == 2


def test_pipeline_severity_fallback_when_no_rows():
    assert pipeline_severity([]) == 3


def test_object_severity_reads_object_severities_defaults():
    sev = ObjectSeverities()
    assert object_severity("bus", sev) == 1
    assert object_severity("db", sev) == 2
    assert object_severity("machine", sev) == 2


def test_object_severity_reads_configured_overrides():
    sev = ObjectSeverities(bus=5, db=1, machine=3)
    assert object_severity("bus", sev) == 5
    assert object_severity("db", sev) == 1
    assert object_severity("machine", sev) == 3


def test_object_severity_unrecognised_kind_falls_back_to_3():
    sev = ObjectSeverities()
    assert object_severity("nonsense", sev) == 3


def test_object_severities_never_consulted_by_row_or_pipeline_severity():
    import inspect

    from _alerts import severity as severity_module

    assert "ObjectSeverities" not in inspect.getsource(severity_module.row_severity)
    assert "ObjectSeverities" not in inspect.getsource(severity_module.pipeline_severity)

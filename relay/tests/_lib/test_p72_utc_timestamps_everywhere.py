from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def _datetime_now_calls(py_file: Path) -> list[tuple[int, bool]]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "now"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "datetime"
        ):
            is_utc = any(
                (isinstance(arg, ast.Attribute) and arg.attr == "utc")
                for arg in node.args
            )
            hits.append((node.lineno, is_utc))
    return hits


def test_p72_every_datetime_now_call_in_src_is_utc_qualified():
    offenders = []
    for py_file in SRC.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        for lineno, is_utc in _datetime_now_calls(py_file):
            if not is_utc:
                offenders.append(f"{py_file.relative_to(SRC)}:{lineno}")
    assert offenders == [], f"non-UTC-qualified datetime.now() call(s): {offenders}"


def test_p72_every_utc_now_emitting_helper_uses_the_z_suffixed_iso_shape():
    import re

    from _metrics.poll_stats import _utc_now_iso
    from _ops.silence import _iso, _now_utc
    from _ws.payload import _now_iso as ws_now_iso
    from _ws.session import _now_iso as session_now_iso

    z_iso = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    assert z_iso.match(_utc_now_iso())
    assert z_iso.match(_iso(_now_utc()))
    assert z_iso.match(ws_now_iso())
    assert z_iso.match(session_now_iso())

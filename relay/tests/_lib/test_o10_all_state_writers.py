from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

_DIRECT_WRITE_APIS = {"write_text", "write_bytes", "dump"}


def _direct_write_call_lines(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _DIRECT_WRITE_APIS:
                hits.append(f"{py_file.name}:{node.lineno}:{node.func.attr}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            for arg in node.args[1:] + [kw.value for kw in node.keywords if kw.arg == "mode"]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "w" in arg.value:
                    hits.append(f"{py_file.name}:{node.lineno}:open(...,'w')")
    return hits


def test_o10_no_state_writer_under_alerts_or_ops_bypasses_atomic_write_json():
    offenders = []
    for subpkg in ("_alerts", "_ops"):
        pkg_dir = SRC / subpkg
        for py_file in pkg_dir.glob("*.py"):
            if py_file.name == "__init__.py":
                continue
            hits = _direct_write_call_lines(py_file)
            if subpkg == "_lib" and py_file.name == "atomic_write.py":
                continue
            offenders.extend(hits)
    assert offenders == [], f"direct file-write call(s) bypassing atomic_write_json(): {offenders}"


def test_o10_every_real_state_writer_imports_atomic_write_json():
    writers = []
    for subpkg in ("_alerts", "_ops"):
        pkg_dir = SRC / subpkg
        for py_file in pkg_dir.glob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            if "atomic_write_json" in text:
                writers.append(py_file.name)
    assert set(writers) >= {"dispatch_sent_guard.py", "silence.py"}

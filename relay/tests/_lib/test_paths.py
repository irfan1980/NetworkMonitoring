from pathlib import Path

import pytest

from _lib.paths import under_state_dir


def test_rebases_relative_path_under_state_dir(tmp_path: Path):
    result = under_state_dir(tmp_path, "alert_state.json")
    assert result == tmp_path / "alert_state.json"


def test_rebases_nested_relative_path(tmp_path: Path):
    result = under_state_dir(tmp_path, "machine_2", "log.txt")
    assert result == tmp_path / "machine_2" / "log.txt"
    assert result.parent.is_dir()


def test_creates_state_dir_if_missing(tmp_path: Path):
    state_dir = tmp_path / "does" / "not" / "exist"
    assert not state_dir.exists()
    under_state_dir(state_dir, "x.json")
    assert state_dir.is_dir()


def test_rejects_parent_traversal(tmp_path: Path):
    with pytest.raises(ValueError):
        under_state_dir(tmp_path, "..", "escaped.json")


def test_rejects_absolute_part_escaping_state_dir(tmp_path: Path):
    other = tmp_path.parent / "elsewhere.json"
    with pytest.raises(ValueError):
        under_state_dir(tmp_path, str(other))

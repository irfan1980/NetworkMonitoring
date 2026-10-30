from __future__ import annotations

from typing import Any, Optional

from _metrics.pair_scan import diff_pairs

_PAIRS_KEY = "pairs"


def diff_top_level_keys(previous: Optional[dict], current: dict) -> dict:
    if _PAIRS_KEY in current:
        raise ValueError(
            "diff_top_level_keys() must never be used for 'pairs' — call "
            "diff_pairs_list() instead, which never omits {id, packets}."
        )
    prev = previous or {}
    changed: dict[str, Any] = {}
    for key, value in current.items():
        if key not in prev or prev[key] != value:
            changed[key] = value
    return changed


def diff_pairs_list(previous: Optional[list[dict]], current: list[dict]) -> dict:
    prev_by_id = {p["id"]: p for p in (previous or [])}
    curr_ids = {p["id"] for p in current}
    changed = [diff_pairs(prev_by_id.get(p["id"]), p) for p in current]
    removed = [pid for pid in prev_by_id if pid not in curr_ids]
    return {"changed": changed, "removed": removed}

from __future__ import annotations

from typing import Optional


def resolve_sender_receiver(row: dict, host_id: int) -> Optional[tuple[int, int]]:
    try:
        src, tgt = int(row.get("sourceSystem")), int(row.get("targetSystem"))
    except (TypeError, ValueError):
        return None
    direction = str(row.get("direction") or "").strip().lower()
    if host_id in (src, tgt) and src != tgt:
        other = tgt if host_id == src else src
        if direction == "outgoing":
            return host_id, other
        if direction == "incoming":
            return other, host_id
        return src, tgt
    return src, tgt


def normalize_pipeline_row(row: dict, host_id: int) -> dict:
    resolved = resolve_sender_receiver(row, host_id)
    if resolved is None:
        return row
    out = dict(row)
    as_str = isinstance(row.get("sourceSystem"), str)
    out["sourceSystem"] = str(resolved[0]) if as_str else resolved[0]
    out["targetSystem"] = str(resolved[1]) if as_str else resolved[1]
    return out

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ParsedMessageId:
    origin: str
    entity: str
    key: str
    row_version: int
    pipeline_id: int
    uuid: str
    raw: str


def parse_message_id(message_id: str) -> Optional[ParsedMessageId]:
    parts = message_id.split(":")
    if len(parts) != 6:
        return None
    origin, entity, key, row_version_s, pipeline_tok, uuid = parts
    if not pipeline_tok.startswith("p"):
        return None
    try:
        row_version = int(row_version_s)
        pipeline_id = int(pipeline_tok[1:])
    except ValueError:
        return None
    return ParsedMessageId(
        origin=origin,
        entity=entity,
        key=key,
        row_version=row_version,
        pipeline_id=pipeline_id,
        uuid=uuid,
        raw=message_id,
    )

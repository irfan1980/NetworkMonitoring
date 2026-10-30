from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from _metrics.readability import ReadStatus


class ColorLevel(str, Enum):

    GREY = "grey"
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"

    def __str__(self) -> str:
        return self.value


LEVEL_PRECEDENCE: tuple[ColorLevel, ...] = (
    ColorLevel.GREY,
    ColorLevel.RED,
    ColorLevel.YELLOW,
    ColorLevel.GREEN,
)


def worst_of(*levels: ColorLevel) -> ColorLevel:
    if not levels:
        raise ValueError("worst_of() requires at least one level")
    for candidate in LEVEL_PRECEDENCE:
        if candidate in levels:
            return candidate
    raise ValueError(f"unrecognised colour level(s): {levels!r}")  # pragma: no cover


NodeKind = str


@dataclass(frozen=True)
class TopologyNode:

    node_id: str
    kind: NodeKind


def compute_node_status(
    kind: NodeKind,
    emitted: ReadStatus,
    queue_level: ColorLevel = ColorLevel.GREEN,
) -> ColorLevel:
    if emitted != ReadStatus.OK:
        return ColorLevel.RED if kind == "db" else ColorLevel.GREY
    return queue_level

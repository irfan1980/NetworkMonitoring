from __future__ import annotations

import math
from typing import Optional


class InvalidWindowParam(ValueError):
    pass


def validate_window_param(raw: Optional[str]) -> float:
    if raw is None or raw == "":
        raise InvalidWindowParam("window is required")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise InvalidWindowParam(f"window={raw!r} is not a number")
    if not math.isfinite(value):
        raise InvalidWindowParam(f"window={raw!r} must be finite")
    if value <= 0:
        raise InvalidWindowParam(f"window={raw!r} must be positive")
    return value

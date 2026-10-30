from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from _lib.paths import under_state_dir

_RETRYABLE_ERRNOS = frozenset({getattr(__import__("errno"), name) for name in ("EACCES", "EBUSY", "EPERM")})


def atomic_write_json(
    state_dir: Path | str,
    *parts: str,
    data: dict,
    retry_attempts: int = 5,
    backoff_ms_per_attempt: int = 10,
) -> Path:
    target = under_state_dir(state_dir, *parts)

    payload = json.dumps(data, indent=2)
    last_error: Optional[OSError] = None
    for attempt in range(1, retry_attempts + 1):
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_path, target)
            return target
        except OSError as exc:
            last_error = exc
            tmp_path.unlink(missing_ok=True)
            if getattr(exc, "errno", None) not in _RETRYABLE_ERRNOS or attempt == retry_attempts:
                raise
            time.sleep((attempt * backoff_ms_per_attempt) / 1000)

    assert last_error is not None
    raise last_error

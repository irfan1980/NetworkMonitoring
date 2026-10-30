from __future__ import annotations

from pathlib import Path


def under_state_dir(state_dir: Path | str, *parts: str) -> Path:
    base = Path(state_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)

    candidate = (base / Path(*parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            f"refusing to rebase state path outside state dir: {candidate} is not "
            f"under {base} (parts={parts!r})"
        ) from exc

    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Callable, Optional

from pydantic import ValidationError

from _contract.schema import RelayConfig

logger = logging.getLogger("relay.config")

# RELAY_FALLBACK_CONFIG exists so tests and trial runs can use another port; deployments leave it unset
FALLBACK_CONFIG_PATH = Path(os.environ.get("RELAY_FALLBACK_CONFIG") or Path(__file__).resolve().parents[2] / "config" / "relay_config.example.json")
WATCH_INTERVAL_SECONDS = 2.0


def uses_fallback(main_path: Path | str) -> bool:
    """True when the main config file is absent. A file that exists but is unreadable or invalid is NOT a fallback case:
    the relay must refuse to start on it, never silently run the blank example."""
    return not Path(main_path).is_file()


def validate_config_file(path: Path | str) -> Optional[str]:
    """None when the file parses and passes the schema, else a short reason."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return f"could not be read: {exc}"
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return f"is not valid JSON ({exc}); it may still be uploading"
    try:
        RelayConfig.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return f"failed schema validation ({exc.error_count()} error(s), first: {where}: {first['msg']})"
    return None


def _signature(path: Path) -> Optional[tuple[int, int]]:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class MainConfigWatcher(threading.Thread):
    """While the relay runs on the fallback, polls for the main config. A new file must hold the same size and mtime
    for one whole poll (so a file still being uploaded is skipped) and then validate; an invalid file is reported once per
    change and retried when it changes. on_ready runs once, when a valid main config is in place."""

    def __init__(
        self, path: Path | str, on_ready: Callable[[], None], interval: float = WATCH_INTERVAL_SECONDS,
        validate: Callable[[Path], Optional[str]] = validate_config_file,
    ):
        super().__init__(name="relay-config-watcher", daemon=True)
        self._path = Path(path)
        self._on_ready = on_ready
        self._interval = interval
        self._validate = validate
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        seen: Optional[tuple[int, int]] = None
        evaluated: Optional[tuple[int, int]] = None
        while not self._halt.wait(self._interval):
            try:
                sig = _signature(self._path)
                if sig is None:
                    seen = evaluated = None
                    continue
                if sig != seen:
                    seen = sig
                    continue
                if sig == evaluated:
                    continue
                evaluated = sig
                problem = self._validate(self._path)
                if problem is None:
                    logger.warning("main config %s is in place and valid; leaving the fallback config", self._path)
                    self._on_ready()
                    return
                logger.error("main config %s %s; staying on the fallback config until the file changes", self._path, problem)
            except Exception:
                logger.exception("config watcher pass failed; will try again")

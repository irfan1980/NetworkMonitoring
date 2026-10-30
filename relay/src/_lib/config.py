from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from _contract.schema import RelayConfig

logger = logging.getLogger("relay.config")


class StartupConfigError(Exception):
    pass


class ConfigStore:

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._config: Optional[RelayConfig] = None
        self._mtime: Optional[float] = None
        self._last_logged_error: Optional[str] = None


    def load_startup(self) -> RelayConfig:
        raw = self._read_raw_or_raise_startup()
        try:
            config = RelayConfig.model_validate(raw)
        except ValidationError as exc:
            raise StartupConfigError(
                f"config file failed schema validation: {self.path}:\n{exc}"
            ) from exc

        with self._lock:
            self._config = config
            self._mtime = self._safe_mtime()
            self._last_logged_error = None
        return config

    def _read_raw_or_raise_startup(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise StartupConfigError(f"config file not found: {self.path}") from exc
        except OSError as exc:
            raise StartupConfigError(f"config file could not be read: {self.path}: {exc}") from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise StartupConfigError(f"config file is not valid JSON: {self.path}: {exc}") from exc


    def get(self) -> RelayConfig:
        with self._lock:
            if self._config is None:
                raise RuntimeError(
                    "ConfigStore.get() called before load_startup() succeeded — "
                    "there is no config, good or bad, to serve yet."
                )

            mtime = self._safe_mtime()
            if mtime is None:
                self._warn_once(f"config file missing on refresh, keeping last-known-good: {self.path}")
                return self._config
            if self._mtime is not None and mtime == self._mtime:
                return self._config

            try:
                text = self.path.read_text(encoding="utf-8")
                raw = json.loads(text)
                new_config = RelayConfig.model_validate(raw)
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                self._warn_once(f"config file reload failed, keeping last-known-good: {self.path}: {exc}")
                return self._config

            self._config = new_config
            self._mtime = mtime
            self._last_logged_error = None
            return self._config

    def force_reload(self) -> tuple[bool, Optional[str]]:
        with self._lock:
            if self._config is None:
                raise RuntimeError(
                    "ConfigStore.force_reload() called before load_startup() succeeded — "
                    "there is no config, good or bad, to serve yet."
                )
            try:
                text = self.path.read_text(encoding="utf-8")
                raw = json.loads(text)
                new_config = RelayConfig.model_validate(raw)
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                message = f"config file reload failed, keeping last-known-good: {self.path}: {exc}"
                self._warn_once(message)
                return False, str(exc)

            self._config = new_config
            self._mtime = self._safe_mtime()
            self._last_logged_error = None
            return True, None

    def _safe_mtime(self) -> Optional[float]:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def _warn_once(self, message: str) -> None:
        if message != self._last_logged_error:
            logger.warning(message)
            self._last_logged_error = message


class ConfigRefreshLoop:

    def __init__(self, config_store: ConfigStore):
        self._config_store = config_store
        self._running = False

    async def run_forever(self) -> None:
        import asyncio

        self._running = True
        while self._running:
            settings = self._config_store.get().settings
            interval = max(1, settings.config_force_reload_seconds)
            await asyncio.sleep(interval)
            if not self._running:
                return
            self._config_store.force_reload()

    def stop(self) -> None:
        self._running = False

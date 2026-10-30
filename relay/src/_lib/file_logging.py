from __future__ import annotations

import logging
import logging.handlers
import multiprocessing
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from _lib.log_redact import RedactWsSecretFilter

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
INFO_KIND = "info"
ERROR_KIND = "error"


def dated_log_path(directory: Path | str, kind: str, day: date) -> Path:
    return Path(directory) / f"{day:%Y-%m-%d}_{kind}_log.log"


class DatedFileHandler(logging.Handler):
    def __init__(self, directory: Path | str, kind: str, level: int, clock: Callable[[], datetime] = datetime.now):
        super().__init__(level)
        self._directory = Path(directory)
        self._kind = kind
        self._clock = clock
        self._day: Optional[date] = None
        self._stream = None
        self.setFormatter(logging.Formatter(LOG_FORMAT))

    def current_path(self) -> Path:
        return dated_log_path(self._directory, self._kind, self._clock().date())

    def _open(self, day: date) -> None:
        if self._stream is not None:
            self._stream.close()
        self._directory.mkdir(parents=True, exist_ok=True)
        self._stream = open(dated_log_path(self._directory, self._kind, day), "a", encoding="utf-8")
        self._day = day

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = self._clock().date()
            if day != self._day or self._stream is None:
                self._open(day)
            self._stream.write(self.format(record) + "\n")
            self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self.lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
        super().close()


class FileLogging:
    def __init__(self, log_dir: Path | str, clock: Callable[[], datetime] = datetime.now):
        self.log_dir = Path(log_dir)
        self.queue = multiprocessing.get_context("spawn").Queue(-1)
        redact = RedactWsSecretFilter()
        self.info = DatedFileHandler(self.log_dir, INFO_KIND, logging.INFO, clock)
        self.error = DatedFileHandler(self.log_dir, ERROR_KIND, logging.WARNING, clock)
        for handler in (self.info, self.error):
            handler.addFilter(redact)
        self._listener = logging.handlers.QueueListener(self.queue, self.info, self.error, respect_handler_level=True)
        self._previous_root: list[logging.Handler] = []

    def install(self) -> None:
        root = logging.getLogger()
        self._previous_root = list(root.handlers)
        for handler in self._previous_root:
            root.removeHandler(handler)
        root.addHandler(logging.handlers.QueueHandler(self.queue))
        root.setLevel(logging.INFO)
        for noisy in ("httpx", "httpcore"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.captureWarnings(True)
        self._listener.start()

    def stop(self) -> None:
        try:
            self._listener.stop()
        except Exception:
            pass
        self.info.close()
        self.error.close()


def add_console_handler() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RedactWsSecretFilter())
    logging.getLogger().addHandler(handler)


class LogStream:
    def __init__(self, logger_name: str, level: int = logging.WARNING):
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, "%s", line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, "%s", self._buffer.rstrip())
        self._buffer = ""

    def isatty(self) -> bool:
        return False


def configure_child_logging(log_queue) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(logging.handlers.QueueHandler(log_queue))
    root.setLevel(logging.INFO)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.captureWarnings(True)
    sys.stdout = LogStream("child.stdout")
    sys.stderr = LogStream("child.stderr")


def default_log_dir() -> Path:
    return Path(os.environ.get("RELAY_LOG_DIR") or Path(__file__).resolve().parents[2] / "logs")

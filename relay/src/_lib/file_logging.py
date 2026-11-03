from __future__ import annotations

import logging
import logging.handlers
import multiprocessing
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

from _lib.log_redact import RedactWsSecretFilter

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
INFO_KIND = "info"
ERROR_KIND = "error"


DAILY_LOG_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(?:info|error)_log\.log$")
DEFAULT_RETENTION_DAYS = 30
retention_logger = logging.getLogger("relay.logs")


def prune_old_logs(directory: Path | str, today: date, retention_days: int) -> list[Path]:
    """Delete the relay's own daily logs dated more than retention_days ago. Only exact `<date>_info|error_log.log`
    regular files qualify, so supervisor.log, gateway.log, relay-console.log and their rotated copies are never touched.
    Today's files, future dates and unparseable dates are skipped; a failed delete only logs a warning."""
    removed: list[Path] = []
    try:
        entries = list(Path(directory).iterdir())
    except OSError:
        return removed
    for path in entries:
        match = DAILY_LOG_RE.match(path.name)
        if match is None:
            continue
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if day >= today or (today - day).days <= retention_days:
            continue
        try:
            if path.is_file() and not path.is_symlink():
                path.unlink()
                removed.append(path)
        except OSError as exc:
            retention_logger.warning("could not delete old log %s: %s", path.name, exc)
    return removed


def dated_log_path(directory: Path | str, kind: str, day: date) -> Path:
    return Path(directory) / f"{day:%Y-%m-%d}_{kind}_log.log"


class DatedFileHandler(logging.Handler):
    def __init__(
        self, directory: Path | str, kind: str, level: int, clock: Callable[[], datetime] = datetime.now,
        on_new_day: Optional[Callable[[date], None]] = None,
    ):
        super().__init__(level)
        self._on_new_day = on_new_day
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
        first = self._day is None
        self._day = day
        if self._on_new_day is not None and not first:
            try:
                self._on_new_day(day)
            except Exception:
                pass

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
        self._clock = clock
        self._retention_days: Callable[[], int] = lambda: DEFAULT_RETENTION_DAYS
        self.info = DatedFileHandler(self.log_dir, INFO_KIND, logging.INFO, clock, self._prune)
        self.error = DatedFileHandler(self.log_dir, ERROR_KIND, logging.WARNING, clock)
        for handler in (self.info, self.error):
            handler.addFilter(redact)
        self._listener = logging.handlers.QueueListener(self.queue, self.info, self.error, respect_handler_level=True)
        self._previous_root: list[logging.Handler] = []

    def set_retention_source(self, getter: Callable[[], int]) -> None:
        self._retention_days = getter

    def _prune(self, today: Optional[date] = None) -> None:
        try:
            days = int(self._retention_days())
        except Exception:
            days = DEFAULT_RETENTION_DAYS
        today = today if isinstance(today, date) else self._clock().date()
        removed = prune_old_logs(self.log_dir, today, days)
        if removed:
            retention_logger.info("deleted %d relay log file(s) older than %d days", len(removed), days)

    def prune(self) -> None:
        """Startup pass; also runs at every day rollover of the info log."""
        self._prune()

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

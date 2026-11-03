#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

GRACEFUL_SHUTDOWN_SECONDS = 5

CONFIG_PATH = Path(os.environ.get("RELAY_CONFIG") or Path(__file__).resolve().parent / "config" / "relay_config.json")

logger = logging.getLogger("relay")


def _make_server_class():
    import contextlib
    import signal

    import uvicorn
    from uvicorn.server import HANDLED_SIGNALS

    class Server(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self):
            previous = {sig: signal.signal(sig, self.handle_exit) for sig in HANDLED_SIGNALS}
            try:
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)

    return Server


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description=f"Start the relay. It reads exactly one config file, {CONFIG_PATH} (override with RELAY_CONFIG); "
        "edit that file rather than passing a path. While that file is missing the relay runs on the blank "
        "config/relay_config.example.json and switches to it as soon as it appears.",
    )
    parser.add_argument(
        "--no-log",
        action="store_true",
        help="keep the terminal quiet and show one status line; logs always go to logs/<date>_info_log.log "
        "and logs/<date>_error_log.log, with or without this flag",
    )
    return parser.parse_args(argv or [])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    real_stdout, real_stderr = sys.stdout, sys.stderr

    from _lib.file_logging import FileLogging, LogStream, add_console_handler, default_log_dir

    file_logging = FileLogging(default_log_dir())
    file_logging.install()
    if args.no_log:
        sys.stdout = LogStream("stdout", logging.INFO)
        sys.stderr = LogStream("stderr", logging.WARNING)
    else:
        add_console_handler()

    try:
        return _serve(args, file_logging, real_stdout, real_stderr)
    finally:
        sys.stdout, sys.stderr = real_stdout, real_stderr
        file_logging.stop()


def _serve(args, file_logging, real_stdout, real_stderr) -> int:
    status = None
    if args.no_log:
        from _lib.status_line import StatusLine

        status = StatusLine(real_stdout)
        status.update("Relay starting…")

    def fatal(message: str) -> int:
        logger.error(message)
        if status is not None:
            status.clear()
        print(message, file=real_stderr)
        return 1

    try:
        from main import create_app
        from _lib.config import StartupConfigError
    except Exception as exc:
        logger.exception("relay import failed")
        if status is not None:
            status.clear()
        print(f"import error: {exc!r}", file=real_stderr)
        return 1

    from _lib.config_fallback import FALLBACK_CONFIG_PATH, MainConfigWatcher, uses_fallback

    while True:
        code, switch = _run_once(
            args, file_logging, status, fatal, create_app, StartupConfigError,
            FALLBACK_CONFIG_PATH, MainConfigWatcher, uses_fallback,
        )
        if not switch:
            return code
        logger.warning("restarting the relay on %s", CONFIG_PATH)


def _run_once(args, file_logging, status, fatal, create_app, StartupConfigError, fallback_path, watcher_cls, uses_fallback):
    import shutil
    import tempfile
    import threading

    fallback = uses_fallback(CONFIG_PATH)
    config_path = fallback_path if fallback else CONFIG_PATH
    state_dir = None
    if fallback:
        # throwaway state: the real state dir and its one-time file import must stay untouched until the main config exists
        state_dir = tempfile.mkdtemp(prefix="relay-fallback-state-")
        logger.warning(
            "RUNNING ON THE FALLBACK CONFIG %s because %s does not exist: no machines or databases are monitored, nobody can "
            "log in, nothing is saved. The relay switches to %s by itself once that file is valid.",
            fallback_path, CONFIG_PATH, CONFIG_PATH,
        )
    try:
        app = create_app(config_path, state_dir=state_dir, log_queue=file_logging.queue, fallback_config=fallback)
        file_logging.set_retention_source(lambda: app.state.config_store.get().settings.log_retention_days)
        file_logging.prune()
    except StartupConfigError as exc:
        if state_dir:
            shutil.rmtree(state_dir, ignore_errors=True)
        return fatal(f"refusing to start: {exc}"), False

    import uvicorn
    import uvicorn.config

    config = app.state.config_store.get()
    log_config = None

    server = _make_server_class()(uvicorn.Config(
        app,
        host=config.settings.bind_host,
        port=config.settings.port,
        log_config=log_config,
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    ))

    switch = threading.Event()
    watcher = None
    if fallback:
        def on_ready() -> None:
            switch.set()
            server.should_exit = True

        watcher = watcher_cls(CONFIG_PATH, on_ready)
        watcher.start()

    reporter = None
    if status is not None:
        from _lib.status_line import StatusReporter

        reporter = StatusReporter(app.state, status, lambda: server.should_exit)
        reporter.start()

    code = 0
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    finally:
        if watcher is not None:
            watcher.stop()
        if reporter is not None:
            reporter.stop()
        if state_dir:
            del app
            shutil.rmtree(state_dir, ignore_errors=True)

    if switch.is_set():
        return 0, True

    if status is not None:
        if server.started:
            from _lib.status_line import stopped_text

            status.finish(stopped_text())
        else:
            where = f" (details in {file_logging.error.current_path()})"
            code = fatal(f"relay failed to start on {config.settings.bind_host}:{config.settings.port}{where}")
    return code, False


if __name__ == "__main__":
    try:
        exit_code = main(sys.argv[1:])
    except KeyboardInterrupt:
        exit_code = 0
    for child in multiprocessing.active_children():
        child.terminate()
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    # driver threads stuck in blocking I/O would otherwise be joined at interpreter exit
    os._exit(exit_code)

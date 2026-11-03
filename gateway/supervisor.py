#!/usr/bin/env python3
"""App Service startup entry point (standard library only).

Keeps the public gateway in the foreground and the relay running in the background as a restarted child.
Each has its own virtualenv under SB_HOME, rebuilt only when its requirements.txt (or the Python minor version) changes.

    python3 /home/site/wwwroot/gateway/supervisor.py

Environment (all optional):
  SB_SITE_ROOT     deployed code root                 default /home/site/wwwroot
  SB_HOME          persistent data root               default /home/relay
  RELAY_CONFIG     relay config file                  default $SB_HOME/config/relay_config.json
                   (when missing the relay starts on its blank fallback config and switches by itself once the file is valid)
  RELAY_UPSTREAM   host:port the relay listens on     default 127.0.0.1:8085 (must match settings.port)
  SB_RELAY_STOP_SECONDS  grace for the relay on stop  default 8

Logs under $SB_HOME/logs are size-bounded: supervisor.log, gateway.log and relay-console.log each rotate at 5 MB
and keep 3 older copies. (The relay's own daily <date>_*_log.log files are written by the relay, not rotated here.)
"""
from __future__ import annotations

import hashlib
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

log = logging.getLogger("supervisor")
IS_NT = os.name == "nt"
LOG_MAX_BYTES = 5 * 1024 * 1024  # per file; with LOG_BACKUPS rotated copies each log is bounded to ~20 MB
LOG_BACKUPS = 3


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if IS_NT else "bin/python")


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.site_root = Path(env.get("SB_SITE_ROOT", "/home/site/wwwroot"))
        self.home = Path(env.get("SB_HOME", "/home/relay"))
        self.relay_dir = self.site_root / "relay"
        self.gateway_dir = self.site_root / "gateway"
        self.static_dir = self.site_root / "dashboard"
        self.relay_config = Path(env.get("RELAY_CONFIG") or self.home / "config" / "relay_config.json")
        self.state_dir = self.home / "state"
        self.log_dir = self.home / "logs"
        self.relay_venv = self.home / "venv-relay"
        self.gateway_venv = self.home / "venv-gateway"
        self.upstream = env.get("RELAY_UPSTREAM", "127.0.0.1:8085")
        self.relay_stop_seconds = float(env.get("SB_RELAY_STOP_SECONDS", "8"))
        self.gateway_stop_seconds = 5.0
        self.min_backoff = 2.0
        self.max_backoff = 60.0
        self.healthy_after = 120.0


def ensure_venv(venv: Path, requirements: Path) -> bool:
    """Create or rebuild the venv when the stamp (requirements hash + Python minor) differs. True when usable."""
    if not requirements.is_file():
        log.error("requirements file missing: %s", requirements)
        return False
    stamp_value = hashlib.sha256(requirements.read_bytes() + f"{sys.version_info[0]}.{sys.version_info[1]}".encode()).hexdigest()
    stamp = venv / ".requirements.stamp"
    if stamp.is_file() and stamp.read_text().strip() == stamp_value and venv_python(venv).is_file():
        return True
    log.info("building virtualenv %s from %s", venv, requirements)
    if venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
    venv.parent.mkdir(parents=True, exist_ok=True)
    for cmd in ([sys.executable, "-m", "venv", str(venv)],
                [str(venv_python(venv)), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir",
                 "-r", str(requirements)]):
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode != 0:
            log.error("command failed (%s): %s", result.returncode, result.stdout[-2000:])
            return False
    stamp.write_text(stamp_value)
    return True


def child_logger(path: Path, max_bytes: int, backups: int) -> logging.Logger:
    """One size-bounded rotating file per child process (path, path.1 ... path.<backups>)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(f"child.{path.name}")
    lg.propagate = False
    lg.setLevel(logging.INFO)
    for h in list(lg.handlers):
        lg.removeHandler(h)
        h.close()
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    lg.addHandler(handler)
    return lg


def _pump(stream, lg: logging.Logger) -> None:
    try:
        for line in stream:
            lg.info(line.rstrip("\r\n"))
    except (OSError, ValueError):
        pass


def spawn(cmd: Sequence[str], cwd: Path, env: dict, logfile: Path,
          max_bytes: int = LOG_MAX_BYTES, backups: int = LOG_BACKUPS) -> subprocess.Popen:
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if IS_NT else {"start_new_session": True}
    proc = subprocess.Popen(list(cmd), cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", **kwargs)
    proc.pump = threading.Thread(target=_pump, args=(proc.stdout, child_logger(logfile, max_bytes, backups)), daemon=True)
    proc.pump.start()
    return proc


def ask_to_stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if IS_NT else signal.SIGTERM)
    except (OSError, ValueError):
        proc.terminate()


def stop_child(proc: Optional[subprocess.Popen], grace: float, name: str) -> None:
    if proc is None or proc.poll() is not None:
        return
    ask_to_stop(proc)
    try:
        proc.wait(timeout=grace)
        log.info("%s stopped (exit %s)", name, proc.returncode)
    except subprocess.TimeoutExpired:
        log.warning("%s did not stop within %.0fs, killing", name, grace)
        proc.kill()
        proc.wait()
    getattr(proc, "pump", threading.Thread()).join(2)


class Supervisor:
    def __init__(self, cfg: Config, *, gateway_cmd: Optional[Sequence[str]] = None, relay_cmd: Optional[Sequence[str]] = None,
                 prepare_gateway: Optional[Callable[[], bool]] = None, prepare_relay: Optional[Callable[[], bool]] = None):
        self.cfg = cfg
        self.stop = threading.Event()
        self.relay_proc: Optional[subprocess.Popen] = None
        self.gateway_proc: Optional[subprocess.Popen] = None
        self.relay_starts = 0
        self._gateway_cmd = gateway_cmd
        self._relay_cmd = relay_cmd
        self._prepare_gateway = prepare_gateway or (lambda: ensure_venv(cfg.gateway_venv, cfg.gateway_dir / "requirements.txt"))
        self._prepare_relay = prepare_relay or (lambda: ensure_venv(cfg.relay_venv, cfg.relay_dir / "requirements.txt"))

    def gateway_cmd(self):
        return self._gateway_cmd or [str(venv_python(self.cfg.gateway_venv)), str(self.cfg.gateway_dir / "server.py")]

    def relay_cmd(self):
        return self._relay_cmd or [str(venv_python(self.cfg.relay_venv)), "run.py", "--no-log"]

    def relay_env(self) -> dict:
        env = dict(os.environ)
        env.update(RELAY_CONFIG=str(self.cfg.relay_config), RELAY_STATE_DIR=str(self.cfg.state_dir),
                   RELAY_LOG_DIR=str(self.cfg.log_dir), PYTHONUNBUFFERED="1", PYTHONUTF8="1")
        return env

    def gateway_env(self) -> dict:
        env = dict(os.environ)
        env.update(GATEWAY_STATIC_DIR=str(self.cfg.static_dir), RELAY_UPSTREAM=self.cfg.upstream, PYTHONUNBUFFERED="1")
        return env

    def relay_loop(self) -> None:
        cfg = self.cfg
        backoff = cfg.min_backoff
        while not self.stop.is_set():
            if not self._prepare_relay():
                log.error("relay dependencies are not ready, retrying in %.0fs", backoff)
                self.stop.wait(backoff)
                backoff = min(backoff * 2, cfg.max_backoff)
                continue
            if not cfg.relay_config.is_file():
                # the relay itself runs on its blank fallback config and switches to this file when it appears
                log.warning("relay config %s not found; starting the relay on its fallback config", cfg.relay_config)
            started = time.monotonic()
            proc = spawn(self.relay_cmd(), cfg.relay_dir, self.relay_env(), cfg.log_dir / "relay-console.log")
            self.relay_proc = proc
            self.relay_starts += 1
            log.info("relay started (pid %s)", proc.pid)
            while proc.poll() is None and not self.stop.is_set():
                self.stop.wait(0.5)
            if self.stop.is_set():
                stop_child(proc, cfg.relay_stop_seconds, "relay")
                return
            proc.pump.join(2)
            uptime = time.monotonic() - started
            backoff = cfg.min_backoff if uptime >= cfg.healthy_after else min(backoff * 2, cfg.max_backoff)
            log.error("relay exited with code %s after %.0fs; restarting in %.0fs", proc.returncode, uptime, backoff)
            self.stop.wait(backoff)

    def request_stop(self, *_):
        if not self.stop.is_set():
            log.info("stop requested")
        self.stop.set()

    def run(self) -> int:
        cfg = self.cfg
        for sig in (signal.SIGTERM, signal.SIGINT, *([signal.SIGBREAK] if IS_NT else [])):
            try:
                signal.signal(sig, self.request_stop)
            except ValueError:
                pass  # not in the main thread (tests)
        if not self._prepare_gateway():
            log.error("gateway dependencies could not be installed; exiting")
            return 1
        self.gateway_proc = spawn(self.gateway_cmd(), cfg.gateway_dir, self.gateway_env(), cfg.log_dir / "gateway.log")
        log.info("gateway started (pid %s)", self.gateway_proc.pid)
        relay_thread = threading.Thread(target=self.relay_loop, name="relay", daemon=True)
        relay_thread.start()
        while not self.stop.is_set() and self.gateway_proc.poll() is None:
            self.stop.wait(0.5)
        gateway_died = self.gateway_proc.poll() is not None and not self.stop.is_set()
        if gateway_died:
            log.error("gateway exited with code %s", self.gateway_proc.returncode)
        self.stop.set()
        ask_to_stop(self.gateway_proc)  # signal both first so they stop in parallel
        relay_thread.join(timeout=cfg.relay_stop_seconds + 5)
        stop_child(self.gateway_proc, cfg.gateway_stop_seconds, "gateway")
        log.info("supervisor exiting")
        return 1 if gateway_died else 0


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers = [logging.StreamHandler(sys.stdout),
                logging.handlers.RotatingFileHandler(log_dir / "supervisor.log", maxBytes=5 * 1024 * 1024, backupCount=3)]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)


def main() -> int:
    cfg = Config()
    setup_logging(cfg.log_dir)
    log.info("starting: site %s, home %s, python %s", cfg.site_root, cfg.home, sys.version.split()[0])
    return Supervisor(cfg).run()


if __name__ == "__main__":
    raise SystemExit(main())

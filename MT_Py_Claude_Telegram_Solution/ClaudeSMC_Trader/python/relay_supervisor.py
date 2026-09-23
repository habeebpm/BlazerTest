"""
Runs the Telegram relay bridge (../../python/telegram_relay_bridge.py) as a
supervised child process of main.py, so one command runs everything:

    python main.py --live --relay ...

Why a child process rather than importing the bridge: the bridge's folder
has modules with the same names as this one (config.py, ...), so importing
it here could silently load the wrong module; and Telethon's asyncio loop
stays out of the trading loop's process. A bridge crash can never stop
trading.

Behavior:
  - started with --no-login: it never waits for a phone code in the
    background (log in once with `python main.py --relay-login`);
  - restarted after a crash or disconnect, waiting 30s, doubling up to
    10 minutes (reset after it has run 10 minutes cleanly);
  - NOT restarted after exit code 2 (not logged in) or 3 (configuration:
    credentials, channels, telethon) - retrying cannot fix those; on_fatal
    is called once with the reason (main.py sends a Telegram alert);
  - stopped with main.py (stop(), also registered with atexit).

Runs with the bridge folder as its working directory, so the saved login
(tg_relay_bridge.session) is the same file the standalone bridge uses.
"""
from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import threading
import time

log = logging.getLogger("relay")

BRIDGE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                           "..", "..", "python"))
BRIDGE_SCRIPT = "telegram_relay_bridge.py"
EXIT_NOT_LOGGED_IN = 2   # must match telegram_relay_bridge.EXIT_NOT_LOGGED_IN
EXIT_CONFIG = 3          # must match telegram_relay_bridge.EXIT_CONFIG

_FATAL_TEXT = {
    EXIT_NOT_LOGGED_IN: "not logged in to Telegram - run: python main.py --relay-login",
    EXIT_CONFIG: "configuration problem (TELEGRAM_API_ID/HASH, TELEGRAM_SOURCE_CHANNELS, "
                 "TELEGRAM_RELAY_GROUP or telethon) - see the lines above",
}


def bridge_path(bridge_dir: str = BRIDGE_DIR) -> str:
    return os.path.join(bridge_dir, BRIDGE_SCRIPT)


def login(bridge_dir: str = BRIDGE_DIR) -> int:
    """Interactive one-time login + chat id check (foreground, prompts for
    the phone number and code). Returns the bridge's exit code."""
    return subprocess.call([sys.executable, BRIDGE_SCRIPT, "--check"], cwd=bridge_dir)


class ChildSupervisor:
    """Keeps one companion script running as a child process: restart after
    a crash/exit (first_delay doubling to max_delay, reset once it ran
    healthy_after seconds), stop for good on an exit code in `fatal`
    ({code: reason}; on_fatal(reason) is called once), stop() on exit."""

    def __init__(self, name: str, command, cwd: str, fatal=None, on_fatal=None,
                 first_delay: float = 30.0, max_delay: float = 600.0, healthy_after: float = 600.0):
        self.name = name
        self.command = list(command)
        self.cwd = cwd
        self.fatal = dict(fatal or {})
        self.on_fatal = on_fatal
        self.first_delay, self.max_delay, self.healthy_after = first_delay, max_delay, healthy_after
        self._stop = threading.Event()
        self._proc = None
        self._lock = threading.Lock()
        self._thread = None
        self.starts = 0
        self.fatal_reason = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name=f"{self.name}-supervisor", daemon=True)
        self._thread.start()
        atexit.register(self.stop)

    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _run(self) -> None:
        delay = self.first_delay
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                with self._lock:
                    if self._stop.is_set():
                        return
                    self._proc = subprocess.Popen(self.command, cwd=self.cwd, stdin=subprocess.DEVNULL)
                    self.starts += 1
                log.info("%s started (pid %s).", self.name, self._proc.pid)
                rc = self._proc.wait()
            except OSError as exc:
                rc = None
                log.error("Could not start %s (%s).", self.name, exc)
            if self._stop.is_set():
                return
            if rc in self.fatal:
                self.fatal_reason = self.fatal[rc]
                log.error("%s stopped for good: %s. Trading continues.", self.name, self.fatal_reason)
                if self.on_fatal is not None:
                    try:
                        self.on_fatal(self.fatal_reason)
                    except Exception:
                        log.debug("on_fatal callback failed", exc_info=True)
                return
            if time.monotonic() - started >= self.healthy_after:
                delay = self.first_delay
            log.warning("%s exited (code %s) - restarting in %.0fs.", self.name, rc, delay)
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self.max_delay)


class RelaySupervisor(ChildSupervisor):
    """The Telegram relay bridge (see the module docstring)."""

    def __init__(self, bridge_dir: str = BRIDGE_DIR, extra_args=(), on_fatal=None,
                 first_delay: float = 30.0, max_delay: float = 600.0, healthy_after: float = 600.0,
                 command=None):
        super().__init__("Telegram relay bridge",
                         command or [sys.executable, BRIDGE_SCRIPT, "--no-login", *extra_args],
                         bridge_dir, fatal=_FATAL_TEXT, on_fatal=on_fatal, first_delay=first_delay,
                         max_delay=max_delay, healthy_after=healthy_after)

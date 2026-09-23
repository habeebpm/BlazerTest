"""
The solution's other Python programs, run by main.py - each optional,
switched on in settings.ini (package root), so one command runs
everything:

    [relay_bridge]        relay/telegram_relay_bridge.py          continuous
    [xtr_export]          drive_export/xtr_export.py              continuous
    [ml_retrain]          train_ml_model.py                        every N days
    [calibration_report]  calibration_report.py                    every N days
    [scorecard]           scorecard.py (result sent to Telegram)   every N days

Continuous ones run as supervised child processes (relay_supervisor.
ChildSupervisor): restarted after a crash, stopped for good on a
configuration error (one Telegram alert), stopped with main.py. Scheduled
ones run as a child process when due (last run remembered in
logs/services_state.json, so a restart of main.py does not re-run them),
output saved to logs/<job>.log. Child processes rather than imports: the
folders share module names (config.py, ...), and nothing here can stop or
slow down trading.

No other trading program belongs here: UnifiedTrader_EA copies the
Telegram signals; a second copier would double every trade.
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

import paths
import relay_supervisor

log = logging.getLogger("services")

HERE = paths.APP_DIR
DEFAULT_PRESET = paths.SETTINGS_INI
XTR_EXPORT_DIR = paths.DRIVE_EXPORT_DIR

XTR_EXPORT_FATAL = {2: "bad options in settings.ini [xtr_export] args (see the lines above)"}


@dataclass
class ServiceSettings:
    enabled: bool = False
    args: list = field(default_factory=list)
    every_days: float = 7.0


@dataclass
class Preset:
    relay_bridge: ServiceSettings = field(default_factory=ServiceSettings)
    xtr_export: ServiceSettings = field(default_factory=ServiceSettings)
    ml_retrain: ServiceSettings = field(default_factory=ServiceSettings)
    calibration_report: ServiceSettings = field(default_factory=ServiceSettings)
    scorecard: ServiceSettings = field(default_factory=ServiceSettings)
    path: str = ""
    errors: list = field(default_factory=list)

    def enabled_names(self) -> list:
        return [n for n in SECTIONS if getattr(self, n).enabled]


SECTIONS = ("relay_bridge", "xtr_export", "ml_retrain", "calibration_report", "scorecard")


def split_args(text: str) -> list:
    """Command-line style split that keeps Windows backslashes (C:\\XTR_Data)
    and accepts "quoted paths with spaces"."""
    out = []
    for tok in shlex.split(text or "", posix=False):
        if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        out.append(tok)
    return out


def load_preset(path: str = DEFAULT_PRESET) -> Preset:
    """Missing file = everything off. A bad value switches that one
    service off and is reported in Preset.errors - never an exception."""
    preset = Preset(path=path)
    if not path or not os.path.exists(path):
        return preset
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
    try:
        with open(path, encoding="utf-8-sig") as f:
            parser.read_file(f)
    except (OSError, configparser.Error) as exc:
        preset.errors.append(f"could not read {path}: {exc}")
        return preset
    for name in SECTIONS:
        if not parser.has_section(name):
            continue
        sec = parser[name]
        s = getattr(preset, name)
        try:
            s.enabled = sec.getboolean("enabled", fallback=False)
            s.args = split_args(sec.get("args", fallback=""))
            s.every_days = sec.getfloat("every_days", fallback=7.0)
            if s.every_days <= 0:
                raise ValueError("every_days must be > 0")
        except ValueError as exc:
            preset.errors.append(f"[{name}] {exc} - that service is off")
            setattr(preset, name, ServiceSettings())
    return preset


class PeriodicJob:
    """Runs `command` when due (every `every_days`), first check
    `first_delay` seconds after start, then every `check_seconds`. The last
    run time lives in `state_path`; output is appended to `log_path`."""

    def __init__(self, name: str, command, cwd: str, every_days: float, state_path: str,
                 log_path: str, first_delay: float = 300.0, check_seconds: float = 600.0,
                 timeout: float = 1800.0, clock=time.time):
        self.name, self.command, self.cwd = name, list(command), cwd
        self.every_seconds = every_days * 86400.0
        self.state_path, self.log_path = state_path, log_path
        self.first_delay, self.check_seconds, self.timeout = first_delay, check_seconds, timeout
        self.clock = clock
        self._stop = threading.Event()
        self._thread = None
        self.runs = 0
        self.last_rc = None

    def _state(self) -> dict:
        try:
            with open(self.state_path) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_last_run(self, when: float) -> None:
        data = self._state()
        data[self.name] = when
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.warning("Could not save %s (%s).", self.state_path, exc)

    def due(self) -> bool:
        last = self._state().get(self.name)
        return not isinstance(last, (int, float)) or self.clock() - last >= self.every_seconds

    def run_now(self) -> int | None:
        started = self.clock()
        try:
            os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as out:
                out.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {self.name} =====\n")
                out.flush()
                rc = subprocess.run(self.command, cwd=self.cwd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, timeout=self.timeout,
                                    env=relay_supervisor.child_env()).returncode
        except subprocess.TimeoutExpired:
            rc = None
            log.warning("%s took longer than %.0f min - stopped; retried next time.",
                        self.name, self.timeout / 60)
        except OSError as exc:
            rc = None
            log.warning("Could not run %s (%s).", self.name, exc)
        self.runs += 1
        self.last_rc = rc
        # Recorded even on failure (e.g. too few trades to train yet) - it is
        # simply tried again next period, never every few minutes.
        self._save_last_run(started)
        log.info("%s finished (exit %s) - output in %s", self.name, rc, self.log_path)
        return rc

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name=f"{self.name}-job", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(self.first_delay):
            return
        while not self._stop.is_set():
            if self.due():
                self.run_now()
            if self._stop.wait(self.check_seconds):
                return


def start_services(cfg, preset: Preset, alert=None, force_relay: bool = False,
                   supervisor_cls=relay_supervisor.ChildSupervisor, job_cls=PeriodicJob) -> list:
    """Starts every enabled service; returns the started objects.
    alert(text) is called when a continuous service stops for good."""
    for err in preset.errors:
        log.error("settings.ini: %s", err)
    started = []
    py = sys.executable

    def fatal_alert(name):
        def _cb(reason):
            if alert is not None:
                alert(f"{name} stopped: {reason}. Trading continues.")
        return _cb

    relay = preset.relay_bridge
    if relay.enabled or force_relay:
        if os.path.exists(relay_supervisor.bridge_path()):
            sup = supervisor_cls("Telegram relay bridge", relay_supervisor.relay_command(relay.args),
                                 relay_supervisor.BRIDGE_DIR, fatal=relay_supervisor.RELAY_FATAL,
                                 on_fatal=fatal_alert("Telegram relay bridge"))
            sup.start()
            started.append(sup)
        else:
            log.error("relay_bridge: %s not found.", relay_supervisor.bridge_path())

    xtr = preset.xtr_export
    if xtr.enabled:
        script = os.path.join(XTR_EXPORT_DIR, "xtr_export.py")
        if os.path.exists(script):
            sup = supervisor_cls("XTR price export", [py, "xtr_export.py", *xtr.args], XTR_EXPORT_DIR,
                                 fatal=XTR_EXPORT_FATAL, on_fatal=fatal_alert("XTR price export"))
            sup.start()
            started.append(sup)
        else:
            log.error("xtr_export: %s not found.", script)

    logs = os.path.abspath(cfg.log_dir)          # the jobs run in HERE; main.py maybe elsewhere
    state = os.path.join(logs, "services_state.json")
    for name, script, extra in (
            ("ml_retrain", "train_ml_model.py", ["--magic", str(cfg.magic), "--log-dir", logs]),
            ("calibration_report", "calibration_report.py",
             ["--magic", str(cfg.magic), "--decisions", os.path.join(logs, "decisions.csv"),
              "--trades", os.path.join(logs, "trades.csv")]),
            ("scorecard", "scorecard.py",
             ["--magic", str(cfg.magic)]
             + (["--telegram-magic", str(cfg.shared_cap_magic_numbers[0])]
                if cfg.shared_cap_magic_numbers else []))):
        s = getattr(preset, name)
        if not s.enabled:
            continue
        job = job_cls(name, [py, script, "--symbol", cfg.symbol, *extra, *s.args], HERE, s.every_days,
                      state, os.path.join(logs, f"{name}.log"))
        job.start()
        started.append(job)

    names = preset.enabled_names()
    if force_relay and "relay_bridge" not in names:
        names.append("relay_bridge (--relay)")
    log.info("Companion programs: %s", ", ".join(names) if names else "none (see settings.ini)")
    return started

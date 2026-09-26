#!/usr/bin/env python3
r"""
GoldTrader launcher - everything runs from this folder.

    python goldtrader.py setup          install every Python package + run every self-test
    python goldtrader.py install-mt5    copy the EAs/includes/presets into MT5 and compile them
    python goldtrader.py settings       enter / change every setting in one go
                                        (also asked automatically on the first start)
    python goldtrader.py check          MT5 connection + saved-settings report
    python goldtrader.py test-alert     send a sample Telegram alert
    python goldtrader.py test-feeds     check the free news feeds
    python goldtrader.py test-news buy  one breaking-news check
    python goldtrader.py relay-login    one-time Telegram login for the relay bridge
    python goldtrader.py once           one evaluation cycle (dry-run)
    python goldtrader.py start [options]      the trading program (+ settings.ini companions)
    python goldtrader.py backtest [options]
    python goldtrader.py replay-signals [--months 3]   your signal provider's past messages, replayed
    python goldtrader.py scorecard      real demo/live results of both sources + verdict
    python goldtrader.py xtr-export [options] e.g. --check (VPS Drive upload test)
    python goldtrader.py dashboard-password   set the web dashboard's password (dashboard/)
    python goldtrader.py update         download + install the latest version from GitHub (update.bat)
    python goldtrader.py autostart on   start.bat at every sign-in + restarted if its window closes
                                        (autostart.bat; also off | pause | resume | status)
    python goldtrader.py drive-copy     copy the solution to Google Drive\MyTraderbyClaude\GoldTrader
                                        (also after every successful setup / update)
    python goldtrader.py test           every self-test

Double-click versions: setup.bat, settings.bat, check.bat, start.bat, relay_login.bat, update.bat,
autostart.bat, stop.bat.
"""
from __future__ import annotations

import argparse
import filecmp
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT, "app")
RELAY_DIR = os.path.join(ROOT, "relay")
DRIVE_DIR = os.path.join(ROOT, "drive_export")

REQUIREMENTS = [os.path.join(ROOT, "requirements.txt")]

SELFTESTS = [(APP_DIR, "selftest.py"), (RELAY_DIR, "relay_selftest.py"),
             (DRIVE_DIR, "selftest.py"), (ROOT, "goldtrader_selftest.py")]

# (source in this package, destination under <data folder>\MQL5)
MT5_FILES = [
    ("mt5/Experts/UnifiedTrader_EA.mq5", "Experts/UnifiedTrader_EA.mq5"),
    ("mt5/Experts/TelegramSMC_TradeLogger.mq5", "Experts/TelegramSMC_TradeLogger.mq5"),
    ("mt5/Experts/BTCTrader_EA.mq5", "Experts/BTCTrader_EA.mq5"),
    ("mt5/Include/TelegramSMC_Common.mqh", "Include/TelegramSMC_Common.mqh"),
    ("mt5/Include/EconCalendar.mqh", "Include/EconCalendar.mqh"),
    ("mt5/Include/XtrBarExport.mqh", "Include/XtrBarExport.mqh"),
    ("mt5/Presets/UnifiedTrader_EA_Default.set", "Presets/UnifiedTrader_EA_Default.set"),
    ("mt5/Presets/TelegramSMC_TradeLogger_Unified.set", "Presets/TelegramSMC_TradeLogger_Unified.set"),
    ("mt5/Presets/BTCTrader_EA_Default.set", "Presets/BTCTrader_EA_Default.set"),
]
MT5_COMPILE = ["Experts/UnifiedTrader_EA.mq5", "Experts/TelegramSMC_TradeLogger.mq5", "Experts/BTCTrader_EA.mq5"]

def run(cmd, cwd) -> int:
    print(f"\n> ({os.path.relpath(cwd, ROOT)}) {' '.join(cmd)}", flush=True)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")   # emoji-safe logs on Windows
    try:
        return subprocess.call(cmd, cwd=cwd, env=env)
    except KeyboardInterrupt:
        return 130


def py(cwd, script, *args) -> int:
    return run([sys.executable, script, *args], cwd)


# --------------------------------------------------------------- setup/test

def cmd_setup(_args) -> int:
    for req in REQUIREMENTS:
        rc = run([sys.executable, "-m", "pip", "install", "-r", req], ROOT)
        if rc != 0:
            print(f"\npip failed for {req} (exit {rc}).")
            return rc
    py(APP_DIR, "keys.py")          # keys.txt, ready to fill in (never overwritten)
    rc = cmd_test(_args)
    if rc == 0:                     # only a version that passed its tests replaces the Drive copy
        cmd_drive_copy(None)
    else:
        print("Google Drive copy not updated (self-tests failed) - it keeps the last good version.")
    return rc


def cmd_test(_args) -> int:
    failed = []
    for cwd, script in SELFTESTS:
        if py(cwd, script) != 0:
            failed.append(os.path.relpath(os.path.join(cwd, script), ROOT))
    print("\n" + ("ALL SELF-TESTS PASSED" if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


# --------------------------------------------------------------- Drive copy

DRIVE_COPY_FOLDER = os.path.join("MyTraderbyClaude", "GoldTrader")
# Only the solution itself - code, EAs, presets, launchers, docs, settings.ini.
# Never keys.txt, the relay's Telegram login (*.session), the dashboard
# password, logs or caches: anything not listed here stays on this PC.
COPY_EXTENSIONS = {".py", ".mq5", ".mqh", ".set", ".bat", ".md", ".ini", ".aspx", ".config"}
COPY_NAMES = {"requirements.txt", "status.sample.json"}
SKIP_DIRS = {"logs", "__pycache__", "venv", ".venv", "env", "node_modules"}


def is_solution_file(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in COPY_EXTENSIONS or name in COPY_NAMES


def solution_files(root: str = ROOT) -> list:
    """Relative paths of every solution file under `root`."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        out += [os.path.relpath(os.path.join(dirpath, f), root) for f in sorted(filenames) if is_solution_file(f)]
    return out


def find_drive_root(isdir=os.path.isdir) -> str | None:
    """Google Drive for Desktop's "My Drive" (G: first, then any letter)."""
    for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
        for name in ("My Drive", "MyDrive"):
            path = f"{letter}:\\{name}"
            if isdir(path):
                return path
    return None


def copy_to_drive(dest_root: str, root: str = ROOT) -> tuple[int, int, int]:
    """Mirrors the solution into <dest_root>\\MyTraderbyClaude\\GoldTrader:
    changed files overwritten (temp file + rename, so Drive never uploads a
    half-written one), unchanged ones left alone (no re-upload), solution
    files that no longer exist here removed. Returns (copied, unchanged, removed)."""
    target = os.path.join(dest_root, DRIVE_COPY_FOLDER)
    files = solution_files(root)
    copied = unchanged = removed = 0
    for rel in files:
        src, dst = os.path.join(root, rel), os.path.join(target, rel)
        if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
            unchanged += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        copied += 1
    keep = {os.path.normcase(rel) for rel in files}
    for rel in solution_files(target):
        if os.path.normcase(rel) not in keep:
            os.remove(os.path.join(target, rel))
            removed += 1
    return copied, unchanged, removed


def cmd_drive_copy(args) -> int:
    dest = getattr(args, "to", None) or find_drive_root()
    if not dest:
        print("\nGoogle Drive copy skipped: Google Drive for Desktop (G:\\My Drive) was not found. "
              "Start Google Drive, or: python goldtrader.py drive-copy --to <your My Drive folder>")
        return 1 if args is not None else 0
    try:
        copied, unchanged, removed = copy_to_drive(dest)
    except OSError as exc:
        print(f"\nGoogle Drive copy failed ({exc}) - trading is not affected; try again later.")
        return 1 if args is not None else 0
    print(f"\nGoogle Drive copy: {os.path.join(dest, DRIVE_COPY_FOLDER)} - {copied} updated, "
          f"{unchanged} unchanged, {removed} removed (keys.txt, logins, passwords and logs stay on this PC).")
    return 0


# --------------------------------------------------------------- update

UPDATE_REPO = "habeebpm/BlazerTest"
UPDATE_BRANCH = "claude/telegram-copier-verifier-j794ck"
UPDATE_STATE = os.path.join(ROOT, "logs", "update_state.json")
UPDATE_BACKUPS = os.path.join(ROOT, "logs", "update_backup")
# Yours to edit: an update never overwrites your changed copy - the new one
# is saved next to it as <name>.new instead.
USER_FILES = {"start.bat", "start_btc.bat", "settings.ini"}   # start_btc.bat: from before start.bat ran both


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _http(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "GoldTrader-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def latest_commit(repo: str = UPDATE_REPO, branch: str = UPDATE_BRANCH, fetch=_http, timeout: float = 15.0):
    """(sha, first line of its message) of the branch's newest commit."""
    data = json.loads(fetch(f"https://api.github.com/repos/{repo}/commits/{branch}", timeout))
    return data["sha"], (data.get("commit", {}).get("message") or "").splitlines()[0]


def changes_since(old_sha: str, new_sha: str, repo: str = UPDATE_REPO, fetch=_http) -> list:
    """First lines of the commit messages between two versions (best effort)."""
    try:
        data = json.loads(fetch(f"https://api.github.com/repos/{repo}/compare/{old_sha}...{new_sha}", 15.0))
        return [(c.get("commit", {}).get("message") or "").splitlines()[0] for c in data.get("commits", [])]
    except Exception:
        return []


def load_update_state(path: str = UPDATE_STATE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_update_state(state: dict, path: str = UPDATE_STATE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def extract_package(zip_bytes: bytes, dest: str) -> tuple[str, str]:
    """Unpacks GitHub's branch zip into `dest`; returns (its GoldTrader
    folder, the commit sha GitHub records as the zip comment)."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        sha = z.comment.decode("ascii", "ignore").strip()
        marker = [n for n in z.namelist() if n.endswith("GoldTrader/goldtrader.py")]
        if not marker:
            raise ValueError("the download has no GoldTrader/goldtrader.py")
        z.extractall(dest)
    return os.path.join(dest, *marker[0].split("/")[:-1]), sha


def instance_running(root: str = ROOT, now: float | None = None) -> str:
    """The instance whose status.json was written in the last 3 minutes
    (start.bat still open), else ""."""
    now = time.time() if now is None else now
    for name, rel in (("start.bat (gold)", ("logs", "status.json")),
                      ("start.bat (Bitcoin)", ("logs", "btc", "status.json"))):
        try:
            if now - os.path.getmtime(os.path.join(root, *rel)) < 180:
                return name
        except OSError:
            pass
    return ""


def apply_update(new_root: str, root: str = ROOT, state: dict | None = None,
                 backup_dir: str | None = None) -> dict:
    """Copies the new version's solution files over `root`. Your files are
    never touched: keys.txt, *.session, the dashboard password, logs\\ (not
    solution files), and a start.bat / settings.ini you
    changed (the new one goes to <name>.new). Files the previous version
    shipped but this one does not are removed. Everything replaced or
    removed is kept in backup_dir for rollback_update()."""
    state = state or {}
    shipped = state.get("shipped", {})
    backup_dir = backup_dir or os.path.join(UPDATE_BACKUPS, time.strftime("%Y%m%d-%H%M%S"))
    report = {"changed": [], "added": [], "kept": [], "removed": [], "backup": backup_dir, "shipped": {}}

    def backup(rel):
        dst = os.path.join(backup_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(root, rel), dst)

    new_files = solution_files(new_root)
    for rel in new_files:
        src, dst = os.path.join(new_root, rel), os.path.join(root, rel)
        report["shipped"][rel] = _sha256(src)
        exists = os.path.exists(dst)
        if exists and filecmp.cmp(src, dst, shallow=False):
            continue
        if exists and rel in USER_FILES and shipped.get(rel) != _sha256(dst):
            shutil.copyfile(src, dst + ".new")          # you changed it (or it predates the updater)
            report["kept"].append(rel)
            continue
        if exists:
            backup(rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = dst + ".tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        report["changed" if exists else "added"].append(rel)
    new_set = {os.path.normcase(r) for r in new_files}
    for rel in shipped:
        path = os.path.join(root, rel)
        if os.path.normcase(rel) not in new_set and os.path.exists(path) and rel not in USER_FILES:
            backup(rel)
            os.remove(path)
            report["removed"].append(rel)
    return report


def rollback_update(report: dict, root: str = ROOT) -> None:
    """Puts back exactly what apply_update() changed."""
    for rel in report["added"]:
        try:
            os.remove(os.path.join(root, rel))
        except OSError:
            pass
    for rel in report["changed"] + report["removed"]:
        src = os.path.join(report["backup"], rel)
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)


def cmd_update(args, fetch=_http, setup=None, root: str = ROOT) -> int:
    branch = getattr(args, "branch", None) or UPDATE_BRANCH
    state_path = os.path.join(root, "logs", "update_state.json")
    pause_path = os.path.join(root, "logs", "autostart_paused")
    running = instance_running(root) or ("start.bat" if goldtrader_running(
        os.path.join(root, "logs", "start_all.lock")) else "")
    if running and not getattr(args, "force", False):
        set_paused(True, pause_path)                     # so the autostart does not reopen it meanwhile
        print(f"\n{running} is still running - close its window now (open trades stay managed by the "
              "EA; the autostart will not reopen it), then run update.bat again.")
        return 1
    set_paused(True, pause_path)                         # no autostart restart in the middle of an update
    state = load_update_state(state_path)
    try:
        sha, headline = latest_commit(branch=branch, fetch=fetch)
    except Exception as exc:
        print(f"\nCould not reach GitHub ({exc}) - check the internet connection and try again.")
        return 1
    if sha == state.get("sha") and not getattr(args, "force", False):
        print(f"\nGoldTrader is up to date ({sha[:7]}: {headline}).")
        return 0
    print(f"\nDownloading GoldTrader {sha[:7]} ({headline}) from github.com/{UPDATE_REPO} ...")
    if state.get("sha"):
        for line in changes_since(state["sha"], sha, fetch=fetch):
            print(f"  - {line}")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            data = fetch(f"https://codeload.github.com/{UPDATE_REPO}/zip/refs/heads/{branch}", 120.0)
            new_root, zip_sha = extract_package(data, tmp)
        except Exception as exc:
            print(f"\nDownload failed ({exc}) - nothing was changed. Try again later.")
            return 1
        report = apply_update(new_root, root, state, os.path.join(
            root, "logs", "update_backup", time.strftime("%Y%m%d-%H%M%S")))
    sha = zip_sha or sha
    n = len(report["changed"]) + len(report["added"]) + len(report["removed"])
    print(f"\n{n} file(s) updated: {len(report['changed'])} changed, {len(report['added'])} new, "
          f"{len(report['removed'])} removed. keys.txt, logins, passwords and logs untouched.")
    for rel in report["kept"]:
        print(f"  KEPT your {rel} - the new version is saved as {rel}.new (compare and copy what you need).")
    if n:
        rc = (setup or cmd_setup)(None)                  # packages, every self-test, Drive copy
        if rc != 0:
            rollback_update(report, root)
            print("\nThe new version FAILED its self-tests - your previous version was put back. "
                  "Nothing else changed; tell Claude what the test output says.")
            return rc
    save_update_state({"sha": sha, "branch": branch,
                       "installed_utc": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
                       "shipped": report["shipped"]}, state_path)
    print(f"\nGoldTrader updated to {sha[:7]}. Next: the EAs are recompiled (update.bat does it), then start "
          "start.bat. New EA inputs, if any, are listed in docs\\DEPLOYMENT.md section 11.")
    return 0


def update_notice(fetch=_http) -> str:
    """One line for start.bat when GitHub has a newer version, else ""."""
    state = load_update_state()
    try:
        sha, headline = latest_commit(branch=state.get("branch") or UPDATE_BRANCH, fetch=fetch, timeout=5.0)
    except Exception:
        return ""
    if not state.get("sha") or sha == state["sha"]:
        return ""
    return f"An update is available ({sha[:7]}: {headline}) - press Ctrl+C here, then double-click update.bat."


# --------------------------------------------------------------- logs -> Drive

RUN_LOG_DIR = os.path.join(ROOT, "logs", "run")
DRIVE_LOGS_FOLDER = os.path.join("MyTraderbyClaude", "Logs")
LOG_KEEP_DAYS = 7
JOB_LOGS = ("ml_retrain", "calibration_report", "scorecard")


class RunLog:
    """Every line of the start.bat window also goes to
    logs\\run\\start_<date>.log (one file a day, the last 7 kept)."""
    def __init__(self, folder: str = RUN_LOG_DIR, keep_days: int = LOG_KEEP_DAYS, today=None):
        self.folder, self.keep_days = folder, keep_days
        self.today = today or (lambda: time.strftime("%Y-%m-%d"))
        self.day = None

    def path(self) -> str:
        return os.path.join(self.folder, f"start_{self.today()}.log")

    def write(self, text: str) -> None:
        try:
            day = self.today()
            new_day = day != self.day
            if new_day:
                self.day = day
                os.makedirs(self.folder, exist_ok=True)
            with open(self.path(), "a", encoding="utf-8") as f:
                f.write(text.rstrip("\n") + "\n")
            if new_day:                                   # today's file exists now: keep the newest 7
                for old in sorted(glob.glob(os.path.join(self.folder, "start_*.log")))[:-self.keep_days]:
                    os.remove(old)
        except OSError:
            pass                                          # a full disk must never stop trading


def _log_text(raw: bytes) -> str:
    """MT5's logs are UTF-16 (sometimes without a byte-order mark)."""
    if raw[:2] not in (b"\xff\xfe", b"\xfe\xff") and len(raw) > 3 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return decode_text(raw)


def log_sources(root: str = ROOT, appdata: str | None = None) -> dict:
    """{name in Drive: local file} - this window (last 7 days), the
    autostart log, gold's and BTC's job logs, and today's MT5 Experts log
    (every EA message) of each MT5 installation."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "logs", "run", "start_*.log")))[-LOG_KEEP_DAYS:]:
        out[os.path.basename(path)] = path
    for name, rel in [("autostart.log", ("logs", "autostart.log"))] + [
            (f"{prefix}{job}.log", ("logs", *sub, f"{job}.log"))
            for prefix, sub in (("gold_", ()), ("btc_", ("btc",))) for job in JOB_LOGS]:
        path = os.path.join(root, *rel)
        if os.path.exists(path):
            out[name] = path
    for i, (data_folder, _install) in enumerate(find_data_folders(appdata)):
        logs = sorted(glob.glob(os.path.join(data_folder, "MQL5", "Logs", "*.log")))
        if logs:
            tag = "" if i == 0 else f"{i + 1}_"
            out[f"MT5_Experts_{tag}{os.path.basename(logs[-1])}"] = logs[-1]
    return out


def mirror_logs(dest_root: str, root: str = ROOT, sources: dict | None = None) -> int:
    """Copies the logs into <My Drive>\\MyTraderbyClaude\\Logs (UTF-8, only
    when changed, temp file + rename); files it put there before that are no
    longer current are removed. Returns how many files were updated."""
    target = os.path.join(dest_root, DRIVE_LOGS_FOLDER)
    sources = log_sources(root) if sources is None else sources
    os.makedirs(target, exist_ok=True)
    updated = 0
    for name, src in sources.items():
        try:
            with open(src, "rb") as f:
                data = _log_text(f.read()).encode("utf-8")
        except OSError:
            continue
        dst = os.path.join(target, name)
        try:
            with open(dst, "rb") as f:
                if f.read() == data:
                    continue
        except OSError:
            pass
        tmp = dst + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dst)
        updated += 1
    for path in glob.glob(os.path.join(target, "*.log")):
        name = os.path.basename(path)
        if name not in sources and (name.startswith("start_") or name.startswith("MT5_Experts_")):
            os.remove(path)
    return updated


def log_mirror_loop(stop, every: float = 300.0, find=None, mirror=None, say=None) -> None:
    """start.bat's background copy of the logs to Google Drive, every 5
    minutes and once more on the way out. Never raises."""
    find, mirror = find or find_drive_root, mirror or mirror_logs
    warned = False
    while True:
        try:
            dest = find()
            if dest:
                mirror(dest)
            elif not warned and say:
                say("Logs are not copied to Google Drive: G:\\My Drive not found (Google Drive for Desktop).")
                warned = True
        except Exception:
            pass
        if stop.wait(every):
            try:
                dest = find()
                if dest:
                    mirror(dest)
            except Exception:
                pass
            return


# --------------------------------------------------------------- autostart

AUTOSTART_TASK = "GoldTrader"
RUN_LOCK = os.path.join(ROOT, "logs", "start_all.lock")
PAUSE_FLAG = os.path.join(ROOT, "logs", "autostart_paused")
AUTOSTART_LOG = os.path.join(ROOT, "logs", "autostart.log")


def hold_lock(path: str):
    """An OS lock on `path`, held while the returned handle stays open (freed
    by the OS if the process dies); None if another process holds it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release_lock(handle) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
    except (OSError, ValueError):
        pass


def goldtrader_running(lock_path: str = RUN_LOCK) -> bool:
    """True while a start.bat window (start-all) holds its lock."""
    handle = hold_lock(lock_path)
    if handle is None:
        return True
    release_lock(handle)
    return False


def set_paused(paused: bool, path: str = PAUSE_FLAG) -> None:
    try:
        if paused:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def launch_start_bat(root: str = ROOT, popen=subprocess.Popen):
    """start.bat in a NEW visible console window, detached from the scheduled
    task that launched it (so it keeps running after the watchdog exits)."""
    cmd = ["cmd.exe", "/c", os.path.join(root, "start.bat"), "auto"]
    new_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    try:
        return popen(cmd, cwd=root, creationflags=new_console | breakaway)
    except OSError:                                      # the task's job does not allow breakaway
        return popen(cmd, cwd=root, creationflags=new_console)


def cmd_watchdog(_args=None, lock_path: str | None = None, pause_path: str | None = None,
                 launch=None, log_path: str | None = None) -> int:
    """Run by the scheduled task at sign-in and every 5 minutes, without a
    window: starts start.bat when it is not running and not paused."""
    if os.path.exists(pause_path or PAUSE_FLAG):
        return 0
    if goldtrader_running(lock_path or RUN_LOCK):
        return 0
    (launch or launch_start_bat)()
    try:
        path = log_path or AUTOSTART_LOG
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  start.bat was not running - started it\n")
    except OSError:
        pass
    return 0


def task_xml(user: str, pythonw: str, root: str = ROOT) -> str:
    """The scheduled task: at sign-in (after 1 minute, so MT5, Drive and the
    network are up) and every 5 minutes, run the watchdog - one at a time."""
    from xml.sax.saxutils import escape as x
    user_el = f"<UserId>{x(user)}</UserId>" if user else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>GoldTrader: starts start.bat at sign-in and restarts it within 5 minutes if its window closes unexpectedly (goldtrader.py autostart).</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      {user_el}
      <Delay>PT1M</Delay>
    </LogonTrigger>
    <TimeTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      {user_el}
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{x(pythonw)}</Command>
      <Arguments>"{x(os.path.join(root, "goldtrader.py"))}" watchdog</Arguments>
      <WorkingDirectory>{x(root)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def pythonw_path() -> str:
    """pythonw.exe next to this Python (runs the watchdog without a window)."""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return candidate if os.path.exists(candidate) else sys.executable


def autostart_installed(run=subprocess.run) -> bool:
    try:
        return run(["schtasks", "/Query", "/TN", AUTOSTART_TASK], capture_output=True).returncode == 0
    except OSError:
        return False


def cmd_autostart(args, run=subprocess.run) -> int:
    action = getattr(args, "action", "status")
    if action == "pause":
        set_paused(True)
        print("Autostart paused: GoldTrader will not be restarted until you double-click start.bat.\n"
              "Close the GoldTrader window now if it is open (open trades stay managed by the EAs).")
        return 0
    if action == "resume":
        set_paused(False)
        print("GoldTrader starts again by itself within 5 minutes (autostart), or double-click start.bat now."
              if autostart_installed(run) else "Double-click start.bat to start GoldTrader again.")
        return 0
    if action == "status":
        print(f"Autostart: {'ON' if autostart_installed(run) else 'off'}; "
              f"{'PAUSED (start.bat restarts it)' if os.path.exists(PAUSE_FLAG) else 'not paused'}; "
              f"GoldTrader {'running' if goldtrader_running() else 'not running'}.")
        return 0
    if os.name != "nt":
        print("Autostart uses the Windows Task Scheduler - only on Windows.")
        return 1
    if action == "off":
        rc = run(["schtasks", "/Delete", "/TN", AUTOSTART_TASK, "/F"], capture_output=True, text=True).returncode
        print("Autostart OFF - start GoldTrader with start.bat yourself." if rc == 0 else
              "Autostart was not on.")
        return 0
    user = "\\".join(p for p in (os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")) if p)
    xml_path = os.path.join(tempfile.gettempdir(), "goldtrader_autostart.xml")
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(task_xml(user, pythonw_path()))
    try:
        res = run(["schtasks", "/Create", "/TN", AUTOSTART_TASK, "/XML", xml_path, "/F"],
                  capture_output=True, text=True)
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass
    if res.returncode != 0:
        print(f"Could not create the scheduled task: {(res.stderr or res.stdout).strip()}")
        return 1
    set_paused(False)
    print("Autostart ON:\n"
          "  - at every Windows sign-in (after 1 minute) start.bat opens by itself; MT5 is started too\n"
          "  - if the GoldTrader window closes unexpectedly it is reopened within 5 minutes\n"
          "  - Ctrl+C in the window, or stop.bat, stops it for good (until you double-click start.bat)\n"
          "Windows must sign in by itself after a restart for this to work unattended (see docs).")
    return 0


# --------------------------------------------------------------- MT5 install

def decode_text(raw: bytes) -> str:
    """MT5 writes UTF-16 with a byte-order mark; anything else is UTF-8/ANSI.
    Decided by the BOM, never by trial decoding (UTF-16 'succeeds' on
    almost any even-length bytes and returns garbage)."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def read_origin(path: str) -> str:
    """origin.txt holds the terminal's install folder."""
    with open(path, "rb") as f:
        return decode_text(f.read()).strip().strip("\x00").strip()


def find_data_folders(appdata: str | None = None) -> list:
    """Every MT5 data folder: [(data_folder, install_folder)], newest first."""
    appdata = appdata if appdata is not None else os.environ.get("APPDATA", "")
    found = []
    for d in glob.glob(os.path.join(appdata, "MetaQuotes", "Terminal", "*")):
        if os.path.isdir(os.path.join(d, "MQL5")):
            origin = os.path.join(d, "origin.txt")
            found.append((d, read_origin(origin) if os.path.exists(origin) else ""))
    found.sort(key=lambda t: os.path.getmtime(t[0]), reverse=True)
    return found


def copy_mt5_files(data_folder: str, root: str = ROOT) -> list:
    """Copies MT5_FILES into <data_folder>/MQL5; a different existing file
    is kept as <name>.bak first. Returns [(dest, action)]."""
    done = []
    for src_rel, dst_rel in MT5_FILES:
        src = os.path.join(root, *src_rel.split("/"))
        dst = os.path.join(data_folder, "MQL5", *dst_rel.split("/"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            if filecmp.cmp(src, dst, shallow=False):
                done.append((dst, "unchanged"))
                continue
            shutil.copy2(dst, dst + ".bak")
            shutil.copy2(src, dst)
            done.append((dst, "updated (old copy: .bak)"))
        else:
            shutil.copy2(src, dst)
            done.append((dst, "added"))
    return done


def compile_result(log_text: str) -> tuple[int, int] | None:
    """(errors, warnings) from a MetaEditor compile log, None if unreadable."""
    m = re.search(r"(\d+)\s+errors?,\s*(\d+)\s+warnings?", log_text)
    return (int(m.group(1)), int(m.group(2))) if m else None


def read_log(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return decode_text(f.read())
    except OSError:
        return ""


def compile_eas(data_folder: str, install_folder: str) -> bool:
    editor = os.path.join(install_folder, "metaeditor64.exe") if install_folder else ""
    if not editor or not os.path.exists(editor):
        print("\nMetaEditor not found - open each EA in MetaEditor and press F7 instead.")
        return False
    ok = True
    for rel in MT5_COMPILE:
        src = os.path.join(data_folder, "MQL5", *rel.split("/"))
        log_path = os.path.splitext(src)[0] + ".compile.log"
        if os.path.exists(log_path):
            os.remove(log_path)                  # never read an old run's result
        subprocess.call([editor, f"/compile:{src}", f"/log:{log_path}"])
        res = compile_result(read_log(log_path))
        if res is None:
            print(f"  {os.path.basename(src)}: no compile result - open it in MetaEditor, press F7.")
            ok = False
        else:
            errors, warnings = res
            print(f"  {os.path.basename(src)}: {errors} error(s), {warnings} warning(s)"
                  + ("" if errors == 0 else f" - see {log_path}"))
            ok &= errors == 0
    return ok


def cmd_install_mt5(args) -> int:
    if args.data_folder:
        folders = [(args.data_folder, args.install_folder or "")]
        if not os.path.isdir(os.path.join(args.data_folder, "MQL5")):
            print(f"{args.data_folder} has no MQL5 folder - use MT5: File -> Open Data Folder.")
            return 1
    else:
        folders = find_data_folders()
        if not folders:
            print("No MT5 data folder found. Start MT5 once, or pass --data-folder "
                  "(MT5: File -> Open Data Folder).")
            return 1
        if len(folders) > 1 and args.all is False:
            print("Several MT5 installations found:")
            for i, (d, inst) in enumerate(folders, 1):
                print(f"  {i}. {inst or '?'}\n     data: {d}")
            if args.choice is None:
                print("Run again with --choice N (or --all).")
                return 1
            if not 1 <= args.choice <= len(folders):
                print("--choice out of range.")
                return 1
            folders = [folders[args.choice - 1]]
    rc = 0
    for data, install in folders:
        print(f"\nMT5: {install or '?'}\n  data folder: {data}")
        for dst, action in copy_mt5_files(data):
            print(f"  {action:<26} {os.path.relpath(dst, data)}")
        if not args.no_compile and not compile_eas(data, install):
            rc = 1
    print("\nNext in MT5: Tools -> Options -> Expert Advisors -> Allow WebRequest for "
          "https://api.telegram.org; drag UnifiedTrader_EA onto an XAUUSD M15 chart -> Inputs -> "
          "Load UnifiedTrader_EA_Default.set.")
    return rc


# --------------------------------------------------------------- main.py wrappers

RESTART_DELAY = 60   # seconds between automatic restarts of `start`
SETTINGS_ERROR = 3   # main.py: a mistyped option - restarting cannot fix that
ALREADY_RUNNING = 4  # main.py: the same instance already runs in another window


def disable_quick_edit() -> None:
    """Windows console QuickEdit freezes a program the moment someone clicks
    inside its window (until a key is pressed) - never acceptable for the
    trading loop. Clears it for this console; no-op elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)                  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, (mode.value & ~0x0040) | 0x0080)   # -QUICK_EDIT, +EXTENDED_FLAGS
    except Exception:
        pass


def run_forever(cwd: str, script: str, args, sleep=None, max_runs=None, runner=None, say=None,
                stop=None) -> int:
    """`start`: keep the trading program running. A normal stop (Ctrl+C,
    exit 0) ends it; any other exit (MT5 not open yet after a reboot, a
    crash) restarts it after RESTART_DELAY seconds - except a settings error
    (SETTINGS_ERROR), which a restart cannot fix, and ALREADY_RUNNING (the
    same instance is open in another window). start-all passes its own
    runner / say (labelled output) and a stop event."""
    import time
    sleep = sleep or time.sleep
    say = say or (lambda text: print(text, flush=True))
    runner = runner or (lambda: py(cwd, script, *args))
    runs = 0
    while True:
        if stop is not None and stop.is_set():
            return 130
        rc = runner()
        runs += 1
        if rc in (0, 130) or (max_runs is not None and runs >= max_runs) or (stop is not None and stop.is_set()):
            return rc
        if rc == SETTINGS_ERROR:
            say("\nThe program stopped because of a setting it did not understand (see the "
                "message above). Fix it in start.bat, then start again.")
            return rc
        if rc == ALREADY_RUNNING:
            say("\nNot started: this instance is already running in another window (close that one "
                "first - an old start_btc.bat window, for example).")
            return rc
        say(f"\nThe program stopped (exit {rc}) - restarting in {RESTART_DELAY}s. "
            "Close this window or press Ctrl+C to stop.")
        try:
            sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            return 130


def labelled_runner(label: str, cwd: str, script: str, args, emit):
    """Runs the program once with every output line prefixed "GOLD | " /
    "BTC  | ". No keyboard input: settings questions are asked before
    start-all launches anything."""
    def run_once() -> int:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        try:
            proc = subprocess.Popen([sys.executable, script, *args], cwd=cwd, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            emit(f"{label} | could not start: {exc}")
            return 1
        for line in proc.stdout:
            emit(f"{label} | {line.rstrip()}")
        return proc.wait()
    return run_once


def split_start_all(argv) -> tuple[list, list | None]:
    """start-all <gold options> --btc <BTC options>: (gold args, BTC args or
    None). BTC is off without --btc, with nothing after it, or with "off"."""
    argv = list(argv)
    if "--btc" not in argv:
        return argv, None
    i = argv.index("--btc")
    gold, btc = argv[:i], argv[i + 1:]
    if not btc or [a.lower() for a in btc] == ["off"]:
        return gold, None
    if "--profile" not in btc:
        btc = ["--profile", "btc", *btc]
    return gold, btc


def first_run_pending() -> bool:
    """True when the settings questions still have to be asked (first start)."""
    sys.path.insert(0, APP_DIR)
    try:
        import first_run
        import keys
        keys.load()
        return first_run.should_run()
    except Exception:
        return False
    finally:
        sys.path.remove(APP_DIR)


def start_all(argv, runner_factory=labelled_runner, sleep=None, lock_path: str | None = None,
              pause_path: str | None = None, run_log=None, mirror_kw: dict | None = None) -> int:
    """start.bat: gold and (unless switched off) Bitcoin in ONE window - two
    separate programs, each restarted on its own, every line labelled.
    Ctrl+C or closing the window stops both. Holds logs\\start_all.lock
    while it runs (the autostart watchdog's "is it running?"); Ctrl+C or a
    settings error pauses the autostart, so an intentional stop stays
    stopped - only an unexpected close is restarted."""
    import threading
    lock_path = lock_path or RUN_LOCK
    pause_path = pause_path or PAUSE_FLAG
    run_lock = hold_lock(lock_path)
    if run_lock is None:
        print("GoldTrader is already running in another window - not starting a second one.", flush=True)
        return ALREADY_RUNNING
    set_paused(False, pause_path)                        # running = the watchdog may restart it again
    gold, btc = split_start_all(argv)
    if first_run_pending():
        rc = py(APP_DIR, "main.py", "--setup")           # questions first, in the open
        if rc != 0:
            return rc
    lock = threading.Lock()
    run_log = RunLog() if run_log is None else run_log

    def emit(text: str) -> None:
        with lock:
            print(text, flush=True)
            run_log.write(text)
    stop = threading.Event()
    mirror_thread = threading.Thread(target=log_mirror_loop, args=(stop,),
                                     kwargs={"say": emit, **(mirror_kw or {})}, daemon=True)
    mirror_thread.start()
    jobs = [("GOLD", gold)] + ([("BTC ", btc)] if btc is not None else [])
    emit("Starting " + (" + ".join(name.strip() for name, _ in jobs)) + " in this window - leave it open. "
         "Ctrl+C stops everything.")
    results = {}
    threads = []
    for name, args in jobs:
        run = runner_factory(name, APP_DIR, "main.py", args, emit)
        say = (lambda n: (lambda text: emit(f"{n} | {text.strip()}")))(name)
        t = threading.Thread(target=lambda n=name, r=run, s=say: results.__setitem__(
            n, run_forever(APP_DIR, "main.py", [], sleep=sleep or stop.wait, runner=r, say=s, stop=stop)),
            daemon=True)
        t.start()
        threads.append(t)
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(0.5)
    except KeyboardInterrupt:
        stop.set()                                       # the programs got Ctrl+C themselves
        for t in threads:
            t.join(20)
        set_paused(True, pause_path)
        emit("Stopped (Ctrl+C) - the autostart will not restart GoldTrader until you double-click start.bat.")
        mirror_thread.join(30)                           # last copy of the logs to Drive
        release_lock(run_lock)
        return 130
    rc = max((rc for rc in results.values()), default=0)
    if rc == SETTINGS_ERROR:
        set_paused(True, pause_path)                     # restarting cannot fix a mistyped setting
        emit("Autostart paused until the setting in start.bat is fixed and start.bat is started again.")
    stop.set()
    mirror_thread.join(30)                               # last copy of the logs to Drive
    release_lock(run_lock)
    return rc


PASSTHROUGH = {"start": (APP_DIR, "main.py"), "backtest": (APP_DIR, "backtest.py"),
               "replay-signals": (APP_DIR, "signal_replay.py"),
               "scorecard": (APP_DIR, "scorecard.py"), "xtr-export": (DRIVE_DIR, "xtr_export.py")}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] == "start-all":           # start.bat: gold + BTC in one window
        disable_quick_edit()
        notice = update_notice()
        if notice:
            print(f"\n*** {notice} ***\n", flush=True)
        return start_all(argv[1:])
    if argv and argv[0] in PASSTHROUGH:          # every option goes to the program itself (even -h)
        cwd, script = PASSTHROUGH[argv[0]]
        if argv[0] == "start" and not any(a in ("-h", "--help", "--once", "--check", "--setup")
                                          for a in argv[1:]):
            disable_quick_edit()
            notice = update_notice()
            if notice:
                print(f"\n*** {notice} ***\n", flush=True)
            return run_forever(cwd, script, argv[1:])
        return py(cwd, script, *argv[1:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("test")
    p = sub.add_parser("install-mt5")
    p.add_argument("--data-folder", dest="data_folder", help="MT5 data folder (File -> Open Data Folder)")
    p.add_argument("--install-folder", dest="install_folder", help="MT5 program folder (for compiling)")
    p.add_argument("--choice", type=int, help="which MT5 installation when several are found")
    p.add_argument("--all", action="store_true", help="install into every MT5 installation found")
    p.add_argument("--no-compile", action="store_true", dest="no_compile")
    sub.add_parser("settings")
    sub.add_parser("dashboard-password")
    sub.add_parser("watchdog")
    p = sub.add_parser("autostart")
    p.add_argument("action", nargs="?", default="status", choices=["on", "off", "pause", "resume", "status"])
    p = sub.add_parser("update")
    p.add_argument("--branch", help="GitHub branch to install (default: the solution's own)")
    p.add_argument("--force", action="store_true", help="reinstall even if up to date / an instance runs")
    p = sub.add_parser("drive-copy")
    p.add_argument("--to", help="your Google Drive 'My Drive' folder (default: found automatically, G: first)")
    for name in ("check", "test-alert", "test-feeds", "relay-login", "once"):
        sub.add_parser(name)
    p = sub.add_parser("test-news")
    p.add_argument("direction", choices=["buy", "sell"])
    for name in ("start", "backtest", "replay-signals", "scorecard", "xtr-export"):
        p = sub.add_parser(name)
        p.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "test":
        return cmd_test(args)
    if args.cmd == "install-mt5":
        return cmd_install_mt5(args)
    if args.cmd == "settings":
        return py(APP_DIR, "main.py", "--setup")
    if args.cmd == "update":
        return cmd_update(args)
    if args.cmd == "watchdog":
        return cmd_watchdog(args)
    if args.cmd == "autostart":
        return cmd_autostart(args)
    if args.cmd == "drive-copy":
        return cmd_drive_copy(args)
    if args.cmd == "dashboard-password":
        return py(APP_DIR, "dashboard_password.py")
    if args.cmd == "test-news":
        return py(APP_DIR, "main.py", "--test-news-check", args.direction)
    if args.cmd == "start":
        return py(APP_DIR, "main.py", *args.rest)
    if args.cmd == "backtest":
        return py(APP_DIR, "backtest.py", *args.rest)
    if args.cmd == "scorecard":
        return py(APP_DIR, "scorecard.py", *args.rest)
    if args.cmd == "xtr-export":
        return py(DRIVE_DIR, "xtr_export.py", *args.rest)
    flag = {"check": "--check", "test-alert": "--test-alert", "test-feeds": "--test-feeds",
            "relay-login": "--relay-login", "once": "--once"}[args.cmd]
    extra = ["-v"] if args.cmd == "once" else []
    return py(APP_DIR, "main.py", flag, *extra)


if __name__ == "__main__":
    raise SystemExit(main())

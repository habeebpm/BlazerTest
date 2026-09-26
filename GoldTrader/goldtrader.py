#!/usr/bin/env python3
"""
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
    python goldtrader.py test           every self-test

Double-click versions: setup.bat, settings.bat, check.bat, start.bat, relay_login.bat.
"""
from __future__ import annotations

import argparse
import filecmp
import glob
import os
import re
import shutil
import subprocess
import sys

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
    return cmd_test(_args)


def cmd_test(_args) -> int:
    failed = []
    for cwd, script in SELFTESTS:
        if py(cwd, script) != 0:
            failed.append(os.path.relpath(os.path.join(cwd, script), ROOT))
    print("\n" + ("ALL SELF-TESTS PASSED" if not failed else "FAILED: " + ", ".join(failed)))
    return 1 if failed else 0


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


def run_forever(cwd: str, script: str, args, sleep=None, max_runs=None) -> int:
    """`start`: keep the trading program running. A normal stop (Ctrl+C,
    exit 0) ends it; any other exit (MT5 not open yet after a reboot, a
    crash) restarts it after RESTART_DELAY seconds - except a settings error
    (SETTINGS_ERROR), which a restart cannot fix."""
    import time
    sleep = sleep or time.sleep
    runs = 0
    while True:
        rc = py(cwd, script, *args)
        runs += 1
        if rc in (0, 130) or (max_runs is not None and runs >= max_runs):
            return rc
        if rc == SETTINGS_ERROR:
            print("\nThe program stopped because of a setting it did not understand (see the "
                  "message above). Fix it in start.bat, then start again.", flush=True)
            return rc
        print(f"\nThe program stopped (exit {rc}) - restarting in {RESTART_DELAY}s. "
              "Close this window or press Ctrl+C to stop.", flush=True)
        try:
            sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            return 130


PASSTHROUGH = {"start": (APP_DIR, "main.py"), "backtest": (APP_DIR, "backtest.py"),
               "replay-signals": (APP_DIR, "signal_replay.py"),
               "scorecard": (APP_DIR, "scorecard.py"), "xtr-export": (DRIVE_DIR, "xtr_export.py")}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in PASSTHROUGH:          # every option goes to the program itself (even -h)
        cwd, script = PASSTHROUGH[argv[0]]
        if argv[0] == "start" and not any(a in ("-h", "--help", "--once", "--check", "--setup")
                                          for a in argv[1:]):
            disable_quick_edit()
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

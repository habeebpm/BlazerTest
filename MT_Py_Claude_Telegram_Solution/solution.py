#!/usr/bin/env python3
"""
One launcher for the whole solution - run everything from THIS folder, no
cd-ing into sub-folders, no hand-copying files into MT5:

    python solution.py setup          install every Python package + run every self-test
    python solution.py install-mt5    copy the EAs/includes/presets into MT5 and compile them
    python solution.py keys           enter API key / bot token / ... once (saved permanently)
    python solution.py check          MT5 connection + saved-settings report
    python solution.py test-alert     send a sample Telegram alert
    python solution.py test-feeds     check the free news feeds
    python solution.py test-news buy  one breaking-news check
    python solution.py relay-login    one-time Telegram login for the relay bridge
    python solution.py once           one evaluation cycle (dry-run)
    python solution.py start [main.py options]   the Claude program (+ main_preset.ini companions)
    python solution.py backtest [backtest.py options]
    python solution.py xtr-export [xtr_export.py options]   e.g. --check (VPS Drive upload test)
    python solution.py test           every self-test in the solution

Double-click versions: setup.bat, keys.bat, check.bat, start.bat, relay_login.bat.
Each command runs its program in the program's own folder, so logs,
presets and saved logins always land in the same place.
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
CLAUDE_DIR = os.path.join(ROOT, "ClaudeSMC_Trader", "python")
COPIER_DIR = os.path.join(ROOT, "python")
XTR_DIR = os.path.join(ROOT, "XTR_Export", "python")

REQUIREMENTS = [os.path.join(CLAUDE_DIR, "requirements.txt"),
                os.path.join(COPIER_DIR, "requirements.txt"),
                os.path.join(XTR_DIR, "requirements.txt")]

SELFTESTS = [(CLAUDE_DIR, "selftest.py"), (COPIER_DIR, "copier_selftest.py"),
             (COPIER_DIR, "selftest.py"), (COPIER_DIR, "test_integration.py"),
             (XTR_DIR, "selftest.py"), (ROOT, "solution_selftest.py")]

# (source in this solution, destination under <data folder>\MQL5)
MT5_FILES = [
    ("UnifiedTrader/MQL5/Experts/UnifiedTrader_EA.mq5", "Experts/UnifiedTrader_EA.mq5"),
    ("MQL5/Experts/TelegramSMC_TradeLogger.mq5", "Experts/TelegramSMC_TradeLogger.mq5"),
    ("MQL5/Include/TelegramSMC_Common.mqh", "Include/TelegramSMC_Common.mqh"),
    ("MQL5/Include/EconCalendar.mqh", "Include/EconCalendar.mqh"),
    ("MQL5/Include/XtrBarExport.mqh", "Include/XtrBarExport.mqh"),
    ("UnifiedTrader/MQL5/Presets/UnifiedTrader_EA_Default.set", "Presets/UnifiedTrader_EA_Default.set"),
    ("MQL5/Presets/TelegramSMC_TradeLogger_Unified.set", "Presets/TelegramSMC_TradeLogger_Unified.set"),
    ("MQL5/Presets/TelegramSMC_TradeLogger_Default.set", "Presets/TelegramSMC_TradeLogger_Default.set"),
]
MT5_COMPILE = ["Experts/UnifiedTrader_EA.mq5", "Experts/TelegramSMC_TradeLogger.mq5"]

# name, secret, prompt
KEYS = [
    ("ANTHROPIC_API_KEY", True, "Anthropic API key (sk-ant-...)"),
    ("TELEGRAM_ALERT_BOT_TOKEN", True, "Telegram bot token (from @BotFather)"),
    ("TELEGRAM_ALERT_CHAT_ID", False, "Your Telegram chat id"),
    ("TELEGRAM_API_ID", False, "Relay bridge only - api_id from my.telegram.org"),
    ("TELEGRAM_API_HASH", True, "Relay bridge only - api_hash from my.telegram.org"),
    ("TELEGRAM_SOURCE_CHANNELS", False, "Relay bridge only - @channel1,@channel2,@channel3"),
    ("TELEGRAM_RELAY_GROUP", False, "Relay bridge only - relay group id"),
]


def run(cmd, cwd) -> int:
    print(f"\n> ({os.path.relpath(cwd, ROOT)}) {' '.join(cmd)}", flush=True)
    try:
        return subprocess.call(cmd, cwd=cwd)
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


# --------------------------------------------------------------- keys

def save_user_env(name: str, value: str) -> bool:
    """Permanent (Windows user account, survives a restart) - same as setx."""
    if os.name != "nt":
        print(f"  {name}: not Windows - add  export {name}=...  to your shell profile instead.")
        return False
    rc = subprocess.call(["setx", name, value], stdout=subprocess.DEVNULL)
    return rc == 0


def cmd_keys(_args) -> int:
    import getpass
    print("Enter each value (Enter = keep the current one, '-' = skip). Saved permanently.\n")
    changed = 0
    for name, secret, prompt in KEYS:
        current = os.environ.get(name, "")
        shown = ("set, ends ..." + current[-4:]) if (current and secret) else (current or "not set")
        ask = f"{prompt}\n  {name} [{shown}]: "
        value = (getpass.getpass(ask) if secret else input(ask)).strip()
        if not value or value == "-":
            continue
        if save_user_env(name, value):
            changed += 1
            print(f"  saved {name}")
    print(f"\n{changed} value(s) saved. Close this window and open a new one before starting.")
    return 0


# --------------------------------------------------------------- main.py wrappers

PASSTHROUGH = {"start": (CLAUDE_DIR, "main.py"), "backtest": (CLAUDE_DIR, "backtest.py"),
               "xtr-export": (XTR_DIR, "xtr_export.py")}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in PASSTHROUGH:          # every option goes to the program itself (even -h)
        cwd, script = PASSTHROUGH[argv[0]]
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
    sub.add_parser("keys")
    for name in ("check", "test-alert", "test-feeds", "relay-login", "once"):
        sub.add_parser(name)
    p = sub.add_parser("test-news")
    p.add_argument("direction", choices=["buy", "sell"])
    for name in ("start", "backtest", "xtr-export"):
        p = sub.add_parser(name)
        p.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "test":
        return cmd_test(args)
    if args.cmd == "install-mt5":
        return cmd_install_mt5(args)
    if args.cmd == "keys":
        return cmd_keys(args)
    if args.cmd == "test-news":
        return py(CLAUDE_DIR, "main.py", "--test-news-check", args.direction)
    if args.cmd == "start":
        return py(CLAUDE_DIR, "main.py", *args.rest)
    if args.cmd == "backtest":
        return py(CLAUDE_DIR, "backtest.py", *args.rest)
    if args.cmd == "xtr-export":
        return py(XTR_DIR, "xtr_export.py", *args.rest)
    flag = {"check": "--check", "test-alert": "--test-alert", "test-feeds": "--test-feeds",
            "relay-login": "--relay-login", "once": "--once"}[args.cmd]
    extra = ["-v"] if args.cmd == "once" else []
    return py(CLAUDE_DIR, "main.py", flag, *extra)


if __name__ == "__main__":
    raise SystemExit(main())

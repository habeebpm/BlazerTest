#!/usr/bin/env python3
"""Offline tests for goldtrader.py (no MT5, no Windows, no network)."""
from __future__ import annotations

import os
import sys
import tempfile

import goldtrader as solution

results = []
failed_lines = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    line = f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" - {detail}")
    print(line)
    if not cond:
        failed_lines.append(line[:2000])


def save_test_report(name: str, failed: list, root: str) -> None:
    """logs\\selftest_<name>.log, and a copy in <My Drive>\\MyTraderbyClaude\\Logs
    when that folder exists - so a failed update's reason can be read from
    Drive. Never raises."""
    import time as _t
    text = (f"{_t.strftime('%Y-%m-%d %H:%M:%S')} {name} self-test: "
            + (f"{len(failed)} FAILED check(s)\n" + "\n".join(failed) if failed else "ALL PASS") + "\n")
    targets = [os.path.join(root, "logs")]
    if os.name == "nt":
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            base = next((f"{letter}:\\{d}\\MyTraderbyClaude" for d in ("My Drive", "MyDrive")
                         if os.path.isdir(f"{letter}:\\{d}\\MyTraderbyClaude")), None)
            if base:
                targets.append(os.path.join(base, "Logs"))
                break
    for folder in targets:
        try:
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"selftest_{name}.log"), "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass


def main() -> int:
    print("goldtrader.py launcher self-test")
    for rel, _ in solution.MT5_FILES:
        check(f"source exists: {rel}", os.path.exists(os.path.join(solution.ROOT, *rel.split("/"))))
    for cwd, script in solution.SELFTESTS:
        check(f"self-test exists: {os.path.relpath(os.path.join(cwd, script), solution.ROOT)}",
              os.path.exists(os.path.join(cwd, script)))
    for req in solution.REQUIREMENTS:
        check(f"requirements exist: {os.path.relpath(req, solution.ROOT)}", os.path.exists(req))

    with tempfile.TemporaryDirectory() as tmp:
        term = os.path.join(tmp, "MetaQuotes", "Terminal")
        a = os.path.join(term, "D0E8209F77C8CF37AD8BF550E51FF075")
        os.makedirs(os.path.join(a, "MQL5", "Experts"))
        with open(os.path.join(a, "origin.txt"), "wb") as f:
            f.write("C:\\Program Files\\MetaTrader 5".encode("utf-16"))
        os.makedirs(os.path.join(term, "Community"))           # not a terminal - ignored
        found = solution.find_data_folders(tmp)
        check("finds the MT5 data folder and reads origin.txt (UTF-16)",
              found == [(a, "C:\\Program Files\\MetaTrader 5")], found)

        old = os.path.join(a, "MQL5", "Experts", "UnifiedTrader_EA.mq5")
        with open(old, "w") as f:
            f.write("// my old version")
        done = dict((os.path.relpath(d, a).replace("\\", "/"), act) for d, act in solution.copy_mt5_files(a))
        check("copies every EA/include/preset into MQL5", len(done) == len(solution.MT5_FILES)
              and all(os.path.exists(os.path.join(a, "MQL5", *dst.split("/"))) for _, dst in solution.MT5_FILES),
              done)
        check("an existing different file is kept as .bak first",
              done["MQL5/Experts/UnifiedTrader_EA.mq5"].startswith("updated")
              and open(old + ".bak").read() == "// my old version", done)
        again = solution.copy_mt5_files(a)
        check("running it again changes nothing", all(act == "unchanged" for _, act in again), again)

    check("compile log parsed", solution.compile_result("Result: 0 errors, 2 warnings, 812 msec") == (0, 2)
          and solution.compile_result("result: 3 errors, 0 warnings") == (3, 0)
          and solution.compile_result("nothing") is None)
    with tempfile.NamedTemporaryFile("wb", suffix=".log", delete=False) as f:
        f.write("Result: 1 error, 0 warnings".encode("utf-16"))
    check("UTF-16 compile log read", solution.compile_result(solution.read_log(f.name)) == (1, 0))
    os.unlink(f.name)

    check("plain UTF-8 text is not mis-decoded as UTF-16",
          solution.decode_text(b"Result: 0 errors, 0 warnings") == "Result: 0 errors, 0 warnings")
    calls = []
    real_py = solution.py
    solution.py = lambda cwd, script, *args: calls.append((cwd, script, args)) or 0
    try:
        solution.main(["start", "--live", "--help"])
        solution.main(["check"])
        solution.main(["backtest", "--mechanical"])
    finally:
        solution.py = real_py
    check("start/backtest pass every option (even --help) to their program, in its own folder",
          calls == [(solution.APP_DIR, "main.py", ("--live", "--help")),
                    (solution.APP_DIR, "main.py", ("--check",)),
                    (solution.APP_DIR, "backtest.py", ("--mechanical",))], calls)
    seq = iter([2, 1, 0])
    naps = []
    real_py = solution.py
    solution.py = lambda cwd, script, *args: next(seq)
    try:
        rc_run = solution.run_forever(solution.APP_DIR, "main.py", ["--live"], sleep=naps.append)
    finally:
        solution.py = real_py
    check("start restarts after a failure (e.g. MT5 not open yet) and stops on a normal exit",
          rc_run == 0 and naps == [solution.RESTART_DELAY, solution.RESTART_DELAY], (rc_run, naps))
    seq2 = iter([130])
    solution.py = lambda cwd, script, *args: next(seq2)
    try:
        rc_int = solution.run_forever(solution.APP_DIR, "main.py", [], sleep=naps.append)
    finally:
        solution.py = real_py
    check("Ctrl+C stops it for good (no restart)", rc_int == 130 and len(naps) == 2)
    seq3 = iter([solution.SETTINGS_ERROR])
    solution.py = lambda cwd, script, *args: next(seq3)
    try:
        rc_cfg = solution.run_forever(solution.APP_DIR, "main.py", ["--trade-hours", "8-17"],
                                      sleep=naps.append)
    finally:
        solution.py = real_py
    check("a mistyped setting stops start (no endless restarts)",
          rc_cfg == solution.SETTINGS_ERROR and len(naps) == 2)
    rc = solution.main(["install-mt5", "--data-folder", os.path.join(solution.ROOT, "nope")])
    check("install-mt5 refuses a folder without MQL5", rc == 1)

    # Google Drive copy (drive-copy / after every successful setup)
    with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as drive:
        def put(rel, text="x"):
            path = os.path.join(src, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        for rel in ("goldtrader.py", "start.bat", "settings.ini", "requirements.txt", "app/main.py",
                    "mt5/Experts/A.mq5", "mt5/Presets/A.set", "docs/BTC.md", "dashboard/Default.aspx",
                    "dashboard/Web.config", "dashboard/App_Data/status.sample.json"):
            put(rel)
        secrets = ("keys.txt", "relay/relay.session", "relay/relay.session-journal",
                   "dashboard/App_Data/password.txt", "logs/trades.csv", "logs/btc/status.json",
                   "app/__pycache__/main.cpython-312.pyc", ".git/config", "drive.json")
        for rel in secrets:
            put(rel, "SECRET")
        first = solution.copy_to_drive(drive, root=src)
        target = os.path.join(drive, "MyTraderbyClaude", "GoldTrader")
        copied = set(solution.solution_files(target))
        leaked = [rel for rel in secrets if os.path.exists(os.path.join(target, *rel.split("/")))]
        check("drive-copy: code, EAs, presets, launchers, docs into MyTraderbyClaude\\GoldTrader",
              first == (11, 0, 0) and len(copied) == 11, (first, sorted(copied)))
        check("drive-copy never copies keys.txt, the Telegram login, passwords, logs or caches",
              not leaked and not os.path.exists(os.path.join(target, "logs")), leaked)
        check("running it again uploads nothing", solution.copy_to_drive(drive, root=src) == (0, 11, 0))
        put("app/main.py", "new version")
        os.remove(os.path.join(src, "docs", "BTC.md"))
        again = solution.copy_to_drive(drive, root=src)
        with open(os.path.join(target, "app", "main.py"), encoding="utf-8") as f:
            now = f.read()
        check("an update overwrites the changed file and removes one the update deleted",
              again == (1, 9, 1) and now == "new version"
              and not os.path.exists(os.path.join(target, "docs", "BTC.md")), again)
        check("no temp files left behind",
              not any(n.endswith(".tmp") for _, _, fs in os.walk(target) for n in fs))
    # One-click update from GitHub (update.bat)
    import hashlib
    import io
    import json
    import zipfile

    def write(base, rel, text):
        path = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(base, rel):
        with open(os.path.join(base, *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def _mk_pkg(base, files):
        pkg = tempfile.mkdtemp(dir=base)
        for rel, text in files.items():
            write(pkg, rel, text)
        return pkg

    def make_zip(files, sha):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for rel, text in files.items():
                z.writestr(f"BlazerTest-branch/GoldTrader/{rel}", text)
            z.writestr("BlazerTest-branch/README.md", "repo root")
            z.comment = sha.encode()
        return buf.getvalue()

    old_ver = {"goldtrader.py": "v1", "app/main.py": "main v1", "app/old.py": "old", "start.bat": "start v1",
               "start_btc.bat": "btc v1", "settings.ini": "ini v1"}
    new_ver = {"goldtrader.py": "v2", "app/main.py": "main v2", "app/new.py": "new", "start.bat": "start v1",
               "start_btc.bat": "btc v2", "settings.ini": "ini v2", "update.bat": "upd"}
    with tempfile.TemporaryDirectory() as home:
        for rel, text in old_ver.items():
            write(home, rel, text)
        write(home, "start_btc.bat", "btc v1 --symbol BTCUSDm")          # you edited it
        mine = {"keys.txt": "KEY", "relay/tg_relay_bridge.session": "LOGIN", "logs/trades.csv": "HISTORY",
                "dashboard/App_Data/password.txt": "HASH", "docs/my_notes.md": "notes"}
        for rel, text in mine.items():
            write(home, rel, text)
        shipped = {rel: solution._sha256(os.path.join(home, *rel.split("/"))) for rel in old_ver}
        shipped["start_btc.bat"] = hashlib.sha256(b"btc v1").hexdigest()   # what v1 shipped (you edited it since)
        solution.save_update_state({"sha": "a" * 40, "shipped": shipped},
                                   os.path.join(home, "logs", "update_state.json"))
        zip_bytes = make_zip(new_ver, "b" * 40)
        calls = []

        def fetch(url, timeout=0):
            calls.append(url)
            if "/commits/" in url:
                return json.dumps({"sha": "b" * 40, "commit": {"message": "New feature\n\nbody"}}).encode()
            if "/compare/" in url:
                return json.dumps({"commits": [{"commit": {"message": "New feature"}}]}).encode()
            return zip_bytes
        setups = []
        rc = solution.cmd_update(None, fetch=fetch, setup=lambda _a: setups.append(1) or 0, root=home)
        state = solution.load_update_state(os.path.join(home, "logs", "update_state.json"))
        check("update: downloads the branch zip, installs the new version, runs setup, remembers the version",
              rc == 0 and setups == [1] and state["sha"] == "b" * 40 and read(home, "goldtrader.py") == "v2"
              and read(home, "app/main.py") == "main v2" and read(home, "app/new.py") == "new"
              and read(home, "update.bat") == "upd" and any("codeload.github.com" in u for u in calls), calls)
        check("update never touches keys.txt, the Telegram login, logs, the dashboard password or your own files",
              all(read(home, rel) == text for rel, text in mine.items()))
        check("your edited start_btc.bat is KEPT (new one saved as .new); unedited settings.ini is updated",
              read(home, "start_btc.bat") == "btc v1 --symbol BTCUSDm" and read(home, "start_btc.bat.new") == "btc v2"
              and read(home, "settings.ini") == "ini v2")
        check("a file the new version dropped is removed (kept in the backup)",
              not os.path.exists(os.path.join(home, "app", "old.py"))
              and any("old.py" in fs for _, _, fs in os.walk(os.path.join(home, "logs", "update_backup"))))
        calls.clear()
        rc2 = solution.cmd_update(None, fetch=fetch, setup=lambda _a: 1 / 0, root=home)
        check("already up to date -> no download, no setup", rc2 == 0 and not any("codeload" in u for u in calls))
        write(home, "logs/status.json", "{}")                 # written a moment ago, then Ctrl+C
        check("just after Ctrl+C (status.json still fresh, nothing running) the update is NOT refused",
              solution.cmd_update(None, fetch=fetch, root=home) == 0 and solution.instance_running(home) == "")
        os.remove(os.path.join(home, "logs", "status.json"))
        for rel, label in (("logs/instance.lock", "start.bat (gold)"), ("logs/btc/instance.lock", "start.bat (Bitcoin)")):
            os.makedirs(os.path.dirname(os.path.join(home, *rel.split("/"))), exist_ok=True)
            live = solution.hold_lock(os.path.join(home, *rel.split("/")))       # a program that is running
            refused = solution.cmd_update(None, fetch=fetch, root=home)
            seen = solution.instance_running(home)
            solution.release_lock(live)
            check(f"refuses while {label} is really running (its lock is held); free again once it ends",
                  refused == 1 and seen == label and solution.instance_running(home) == "", (refused, seen))
        solution.set_paused(False, os.path.join(home, "logs", "autostart_paused"))

        # A new version that fails its self-tests is rolled back completely.
        solution.save_update_state({"sha": "b" * 40, "shipped": state["shipped"]},
                                   os.path.join(home, "logs", "update_state.json"))
        before = {rel: read(home, rel) for rel in ("goldtrader.py", "app/main.py", "app/new.py", "settings.ini")}
        zip_bytes = make_zip({"goldtrader.py": "v3 broken", "app/main.py": "main v3", "app/extra.py": "x",
                              "settings.ini": "ini v2"}, "c" * 40)

        def fetch3(url, timeout=0):
            if "/commits/" in url:
                return json.dumps({"sha": "c" * 40, "commit": {"message": "Broken"}}).encode()
            if "/compare/" in url:
                return b"{}"
            return zip_bytes
        rc3 = solution.cmd_update(None, fetch=fetch3, setup=lambda _a: 1, root=home)
        after = {rel: read(home, rel) for rel in before}
        check("failed self-tests -> the previous version is put back exactly, version not recorded",
              rc3 == 1 and after == before and not os.path.exists(os.path.join(home, "app", "extra.py"))
              and solution.load_update_state(os.path.join(home, "logs", "update_state.json"))["sha"] == "b" * 40,
              (after, before))

        # The update's own output (packages + every self-test) goes to logs\\update.log.
        solution.save_update_state({"sha": "b" * 40, "shipped": state["shipped"]},
                                   os.path.join(home, "logs", "update_state.json"))
        zip_bytes = make_zip({"goldtrader.py": "v4", "app/main.py": "main v4", "settings.ini": "ini v2"}, "d" * 40)

        def fetch4(url, timeout=0):
            if "/commits/" in url:
                return json.dumps({"sha": "d" * 40, "commit": {"message": "Four"}}).encode()
            if "/compare/" in url:
                return b"{}"
            return zip_bytes
        rc4 = solution.cmd_update(None, fetch=fetch4, root=home, setup=lambda _a: solution.run(
            [sys.executable, "-c", "print('TEST OUTPUT LINE')"], home))
        with open(os.path.join(home, "logs", "update.log"), encoding="utf-8") as f:
            ulog = f.read()
        check("update: the whole setup/test output is also saved to logs\\update.log (for Claude, via Drive)",
              rc4 == 0 and "TEST OUTPUT LINE" in ulog and "update to ddddddd" in ulog, ulog[-300:])

        # .new files: refreshed when your copy is kept, removed when stale
        write(home, "start.bat.new", "an old template")
        write(home, "start.bat", "start v1")                               # = what was shipped -> unedited
        st = {"shipped": {"start.bat": hashlib.sha256(b"start v1").hexdigest()}}   # what the last update shipped
        base = os.path.dirname(home)
        rep = solution.apply_update(_mk_pkg(base, {"start.bat": "start v5"}), home, st,
                                    os.path.join(base, "bk5"))
        check("a start.bat you never edited is updated and a leftover start.bat.new removed",
              read(home, "start.bat") == "start v5" and not os.path.exists(os.path.join(home, "start.bat.new"))
              and rep["kept"] == [], rep)
        write(home, "start.bat", "start v5 --symbol BTCUSDm")                 # you edit it
        st2 = {"shipped": {"start.bat": rep["shipped"]["start.bat"]}}
        rep2 = solution.apply_update(_mk_pkg(base, {"start.bat": "start v6"}), home, st2, os.path.join(base, "bk6"))
        check("an edited start.bat is kept, the package's current one saved as start.bat.new (what the tests read)",
              rep2["kept"] == ["start.bat"] and read(home, "start.bat.new") == "start v6"
              and solution.shipped_copy("start.bat", home).endswith("start.bat.new"), rep2)

        def offline(url, timeout=0):
            raise OSError("no internet")
        before_offline = sorted(solution.solution_files(home))
        check("no internet -> clear message, nothing changed", solution.cmd_update(None, fetch=offline, root=home) == 1
              and sorted(solution.solution_files(home)) == before_offline and read(home, "start.bat") == "start v5 --symbol BTCUSDm")
    check("start.bat notice only when GitHub has a newer version than the installed one",
          solution.update_notice(fetch=lambda u, t=0: b"not json") == "")
    # start.bat: gold + Bitcoin in one window (tests: no real log file, no Drive)
    import types
    real_runlog, real_find_drive = solution.RunLog, solution.find_drive_root
    window_lines = []
    solution.RunLog = lambda: types.SimpleNamespace(write=window_lines.append)
    solution.find_drive_root = lambda isdir=None: None
    split = solution.split_start_all
    check("start-all options: gold before --btc, BTC after (+ --profile btc); empty or 'off' = gold only",
          split(["--live", "--shared-cap-magic", "20260922", "--btc", "--live"])
          == (["--live", "--shared-cap-magic", "20260922"], ["--profile", "btc", "--live"])
          and split(["--live", "--btc"]) == (["--live"], None) and split(["--live", "--btc", "OFF"]) == (["--live"], None)
          and split(["--live"]) == (["--live"], None)
          and split(["--btc", "--live", "--symbol", "BTCUSDm"])[1] == ["--profile", "btc", "--live", "--symbol", "BTCUSDm"])
    ran, lines = [], []

    def fake_factory(label, cwd, script, args, emit):
        def run():
            ran.append((label.strip(), list(args)))
            emit(f"{label} | hello")
            return 0
        return run
    real_pending, real_print = solution.first_run_pending, print
    solution.first_run_pending = lambda: False
    try:
        import builtins
        builtins.print = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
        tmp_locks = tempfile.mkdtemp()
        lk, pz = os.path.join(tmp_locks, "start_all.lock"), os.path.join(tmp_locks, "autostart_paused")
        rc_all = solution.start_all(["--live", "--btc", "--live"], runner_factory=fake_factory,
                                    lock_path=lk, pause_path=pz)
        both = sorted(ran)
        ran.clear()
        rc_gold = solution.start_all(["--live", "--btc", "off"], runner_factory=fake_factory,
                                     lock_path=lk, pause_path=pz)
        gold_only = list(ran)
    finally:
        builtins.print = real_print
        solution.first_run_pending = real_pending
    check("start-all runs gold and BTC as two programs in one window, every line labelled",
          rc_all == 0 and both == [("BTC", ["--profile", "btc", "--live"]), ("GOLD", ["--live"])]
          and "GOLD | hello" in lines and "BTC  | hello" in lines, (both, lines))
    check("BTC_ARGS=off -> gold only", rc_gold == 0 and gold_only == [("GOLD", ["--live"])], gold_only)
    naps = []
    rc_dup = solution.run_forever(solution.APP_DIR, "main.py", [], runner=lambda: solution.ALREADY_RUNNING,
                                  sleep=naps.append, say=lambda t: None)
    check("an instance already running elsewhere is not restarted", rc_dup == solution.ALREADY_RUNNING and naps == [])
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "child.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import sys\nprint('line one')\nprint('keyboard:', sys.stdin.isatty())\nsys.exit(7)\n")
        got = []
        rc_child = solution.labelled_runner("BTC ", tmp, "child.py", [], got.append)()
    check("each program's output is labelled; it gets no keyboard (never asks questions mid-run)",
          rc_child == 7 and got == ["BTC  | line one", "BTC  | keyboard: False"], got)

    import warnings
    bad = []
    for rel in solution.solution_files(solution.ROOT):
        # the package's own folders only - never extra files of yours
        if rel.endswith(".py") and os.path.dirname(rel) in ("", "app", "relay", "drive_export"):
            with open(os.path.join(solution.ROOT, rel), encoding="utf-8") as f:
                source = f.read()
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                try:
                    compile(source, rel, "exec")
                except (SyntaxError, Warning) as exc:
                    bad.append(f"{rel}: {exc}")
    check("every .py compiles without warnings (a bad backslash once printed a SyntaxWarning in start.bat)",
          not bad, bad)

    # Autostart: the scheduled task, the watchdog, pause-on-purpose
    import xml.etree.ElementTree as ET
    xml = solution.task_xml("PC\\trader", "C:\\Py\\pythonw.exe", "C:\\A & B\\GoldTrader")
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    root_el = ET.fromstring(xml.split("?>", 1)[1])
    check("autostart task: at sign-in (+1 min) and every 5 min, one at a time, windowless watchdog, "
          "paths escaped",
          root_el.find("t:Triggers/t:LogonTrigger/t:Delay", ns).text == "PT1M"
          and root_el.find("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval", ns).text == "PT5M"
          and root_el.find("t:Settings/t:MultipleInstancesPolicy", ns).text == "IgnoreNew"
          and root_el.find("t:Settings/t:DisallowStartIfOnBatteries", ns).text == "false"
          and root_el.find("t:Actions/t:Exec/t:Command", ns).text == "C:\\Py\\pythonw.exe"
          and root_el.find("t:Actions/t:Exec/t:Arguments", ns).text.endswith('goldtrader.py" watchdog')
          and root_el.find("t:Actions/t:Exec/t:WorkingDirectory", ns).text == "C:\\A & B\\GoldTrader"
          and root_el.find("t:Principals/t:Principal/t:LogonType", ns).text == "InteractiveToken")
    with tempfile.TemporaryDirectory() as tmp:
        lk, pz, lg = (os.path.join(tmp, n) for n in ("start_all.lock", "autostart_paused", "autostart.log"))
        launched = []
        wd = lambda: solution.cmd_watchdog(None, lock_path=lk, pause_path=pz,  # noqa: E731
                                           launch=lambda: launched.append(1), log_path=lg)
        wd()
        check("watchdog: start.bat not running -> started (and logged)",
              launched == [1] and "started it" in open(lg, encoding="utf-8").read())
        held = solution.hold_lock(lk)
        wd()
        check("watchdog: already running -> nothing (never a second window)", launched == [1])
        check("a second start.bat is refused while the first runs",
              solution.start_all(["--live"], runner_factory=fake_factory, lock_path=lk, pause_path=pz)
              == solution.ALREADY_RUNNING)
        solution.release_lock(held)
        solution.set_paused(True, pz)
        wd()
        check("watchdog: paused (Ctrl+C / stop.bat / update) -> not restarted", launched == [1])

        def bad_settings(label, cwd, script, args, emit):
            return lambda: solution.SETTINGS_ERROR
        solution.first_run_pending = lambda: False
        try:
            rc_bad = solution.start_all(["--oops"], runner_factory=bad_settings, lock_path=lk, pause_path=pz)
            paused_after_bad = os.path.exists(pz)
            solution.start_all(["--live"], runner_factory=fake_factory, lock_path=lk, pause_path=pz)
            paused_after_start = os.path.exists(pz)
        finally:
            solution.first_run_pending = real_pending
        check("start.bat clears the pause; a settings error pauses the autostart (no restart loop)",
              rc_bad == solution.SETTINGS_ERROR and paused_after_bad and not paused_after_start)
        calls = []

        def popen(cmd, cwd=None, creationflags=0):
            calls.append((cmd, creationflags))
            if len(calls) == 1 and creationflags & getattr(solution.subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0):
                raise OSError("breakaway not allowed")
            return object()
        solution.launch_start_bat(root=tmp, popen=popen)
        check("the watchdog opens start.bat auto in its own window (breakaway, else plain)",
              calls[-1][0][:2] == ["cmd.exe", "/c"] and calls[-1][0][2].endswith("start.bat")
              and calls[-1][0][3] == "auto", calls)
        home = os.path.join(tmp, "home")
        os.makedirs(os.path.join(home, "logs"))
        held = solution.hold_lock(os.path.join(home, "logs", "start_all.lock"))
        rc_upd = solution.cmd_update(None, fetch=lambda u, t=0: 1 / 0, root=home)
        solution.release_lock(held)
        check("update while start.bat runs: refused AND the autostart paused so it is not reopened meanwhile",
              rc_upd == 1 and os.path.exists(os.path.join(home, "logs", "autostart_paused")))
    with open(solution.shipped_copy("start.bat"), newline="") as f:     # the package's, not your edits
        sb = f.read()
    with open(os.path.join(solution.ROOT, "autostart.bat"), newline="") as f:
        ab = f.read()
    with open(os.path.join(solution.ROOT, "stop.bat"), newline="") as f:
        stb = f.read()
    check("start.bat closes its window when opened by the autostart; autostart.bat / stop.bat (CRLF)",
          'if /i "%~1"=="auto" exit /b' in sb and "python goldtrader.py autostart on" in ab
          and "python goldtrader.py autostart pause" in stb and "\r\n" in ab and "\r\n" in stb)

    check("the start.bat window is also written to the run log", "GOLD | hello" in window_lines, window_lines[:3])
    solution.RunLog, solution.find_drive_root = real_runlog, real_find_drive

    # Logs -> Google Drive (MyTraderbyClaude\\Logs)
    import threading
    with tempfile.TemporaryDirectory() as tmp:
        days = iter(["2026-09-2%d" % d for d in range(1, 10)])
        today = ["2026-09-20"]
        rl = solution.RunLog(os.path.join(tmp, "logs", "run"), keep_days=7, today=lambda: today[0])
        for _ in range(9):
            today[0] = next(days)
            rl.write("GOLD | line")
        kept = sorted(os.listdir(os.path.join(tmp, "logs", "run")))
        check("run log: one file a day, the last 7 kept",
              len(kept) == 7 and kept[-1] == "start_2026-09-29.log" and kept[0] == "start_2026-09-23.log", kept)
        home = os.path.join(tmp, "home")
        appdata = os.path.join(tmp, "appdata")
        mql_logs = os.path.join(appdata, "MetaQuotes", "Terminal", "ABC123", "MQL5", "Logs")
        os.makedirs(mql_logs)
        with open(os.path.join(mql_logs, "20260925.log"), "wb") as f:
            f.write("old".encode("utf-16"))
        with open(os.path.join(mql_logs, "20260926.log"), "wb") as f:
            f.write("BTCTrader_EA: managing BTCUSD".encode("utf-16-le"))       # no byte-order mark
        for rel, text in (("logs/run/start_2026-09-26.log", "GOLD | hi"), ("logs/autostart.log", "started"),
                          ("logs/ml_retrain.log", "gold ml"), ("logs/btc/scorecard.log", "btc card"),
                          ("keys.txt", "SECRET"), ("logs/trades.csv", "x")):
            path = os.path.join(home, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        src = solution.log_sources(home, appdata=appdata)
        check("logs sent: this window, autostart, gold/BTC job logs, today's MT5 Experts log - nothing else",
              set(src) == {"start_2026-09-26.log", "autostart.log", "gold_ml_retrain.log", "btc_scorecard.log",
                           "MT5_Experts_20260926.log"}, sorted(src))
        drive = os.path.join(tmp, "drive")
        os.makedirs(os.path.join(drive, "MyTraderbyClaude", "Logs"))
        stale = os.path.join(drive, "MyTraderbyClaude", "Logs", "start_2026-09-01.log")
        mine = os.path.join(drive, "MyTraderbyClaude", "Logs", "my_notes.txt")
        for path in (stale, mine):
            with open(path, "w") as f:
                f.write("x")
        first = solution.mirror_logs(drive, home, sources=src)
        out = os.path.join(drive, "MyTraderbyClaude", "Logs")
        with open(os.path.join(out, "MT5_Experts_20260926.log"), encoding="utf-8") as f:
            mt5_text = f.read()
        check("logs copied to Drive\\MyTraderbyClaude\\Logs (MT5's UTF-16 as readable text); an old day's "
              "file removed, your own files kept; unchanged logs not re-uploaded",
              first == 5 and mt5_text == "BTCTrader_EA: managing BTCUSD" and not os.path.exists(stale)
              and os.path.exists(mine) and solution.mirror_logs(drive, home, sources=src) == 0, (first, mt5_text))
        calls, said = [], []
        stop_now = threading.Event()
        stop_now.set()
        solution.log_mirror_loop(stop_now, every=0, find=lambda: drive, mirror=calls.append, say=said.append)
        none_calls = []
        solution.log_mirror_loop(stop_now, every=0, find=lambda: None, mirror=none_calls.append, say=said.append)
        check("log copy: every 5 min + once on the way out; without Drive one warning, no copy",
              calls == [drive, drive] and none_calls == [] and len(said) == 1, (calls, said))

    with open(os.path.join(solution.ROOT, "update.bat"), newline="") as f:
        bat = f.read()
    check("update.bat: update, then recompile the EAs (CRLF)",
          "python goldtrader.py update && python goldtrader.py install-mt5" in bat and "\r\n" in bat
          and bat.index("install-mt5") < bat.index("python goldtrader.py autostart resume"))
    check("finds Google Drive for Desktop's My Drive (G: first)",
          solution.find_drive_root(isdir=lambda p: p in ("G:\\My Drive", "H:\\My Drive")) == "G:\\My Drive"
          and solution.find_drive_root(isdir=lambda p: p == "E:\\MyDrive") == "E:\\MyDrive"
          and solution.find_drive_root(isdir=lambda p: False) is None)
    ok = all(results)
    save_test_report("launcher", failed_lines, solution.ROOT)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Offline tests for goldtrader.py (no MT5, no Windows, no network)."""
from __future__ import annotations

import os
import sys
import tempfile

import goldtrader as solution

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" - {detail}"))


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
        write(home, "logs/status.json", "{}")
        check("refuses while start.bat is running (status.json fresh)",
              solution.cmd_update(None, fetch=fetch, root=home) == 1)
        os.remove(os.path.join(home, "logs", "status.json"))

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

        def offline(url, timeout=0):
            raise OSError("no internet")
        check("no internet -> clear message, nothing changed", solution.cmd_update(None, fetch=offline, root=home) == 1
              and read(home, "goldtrader.py") == "v2")
    check("start.bat notice only when GitHub has a newer version than the installed one",
          solution.update_notice(fetch=lambda u, t=0: b"not json") == "")
    # start.bat: gold + Bitcoin in one window
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
        rc_all = solution.start_all(["--live", "--btc", "--live"], runner_factory=fake_factory)
        both = sorted(ran)
        ran.clear()
        rc_gold = solution.start_all(["--live", "--btc", "off"], runner_factory=fake_factory)
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
        if rel.endswith(".py"):
            with open(os.path.join(solution.ROOT, rel), encoding="utf-8") as f:
                source = f.read()
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                try:
                    compile(source, rel, "exec")
                except (SyntaxError, SyntaxWarning) as exc:
                    bad.append(f"{rel}: {exc}")
    check("every .py compiles without warnings (a bad backslash once printed a SyntaxWarning in start.bat)",
          not bad, bad)

    with open(os.path.join(solution.ROOT, "update.bat"), newline="") as f:
        bat = f.read()
    check("update.bat: update, then recompile the EAs (CRLF)",
          "python goldtrader.py update && python goldtrader.py install-mt5" in bat and "\r\n" in bat)
    check("finds Google Drive for Desktop's My Drive (G: first)",
          solution.find_drive_root(isdir=lambda p: p in ("G:\\My Drive", "H:\\My Drive")) == "G:\\My Drive"
          and solution.find_drive_root(isdir=lambda p: p == "E:\\MyDrive") == "E:\\MyDrive"
          and solution.find_drive_root(isdir=lambda p: False) is None)
    ok = all(results)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

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
    rc = solution.main(["install-mt5", "--data-folder", os.path.join(solution.ROOT, "nope")])
    check("install-mt5 refuses a folder without MQL5", rc == 1)
    ok = all(results)
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

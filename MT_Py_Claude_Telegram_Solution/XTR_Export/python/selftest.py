"""
Offline self-test - runs anywhere, no MT5 terminal, no MetaTrader5 package,
no Google credentials and no network needed. Mirrors the testing
philosophy of the rest of this repo: every piece of logic is pure or
dependency-injected, so it's exercised here with synthetic data and fakes.

Covers:
  * detect_broker_utc_offset_seconds - recovers the broker's true UTC
    offset from a fake gateway's tick time, rounds out jitter
  * format_bars - drops the still-forming last row, converts broker wall
    clock to genuine UTC, rounds to the symbol's quote precision, keeps
    only the latest N closed bars, sorts/dedupes
  * export_once - the full pipeline against a fake gateway: three CSVs +
    a manifest, all with the right shape/content
  * xtr_export.last_closed_bar_time - the new-bar-closed detection the
    poll loop relies on (M1 by default)
  * drive_uploader - creates a Drive file once, updates the SAME file
    (same id/link) on every call after, via a fake Drive service and a
    fake media factory (so this needs no google-api-python-client install)
"""
from __future__ import annotations

import json
import os
import tempfile

import pandas as pd

import drive_uploader
import exporter
import mt5_bars
import xtr_export


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


# --------------------------------------------------------------------------- #
# broker UTC offset detection
# --------------------------------------------------------------------------- #
class FakeOffsetGateway:
    def __init__(self, broker_now: int):
        self.broker_now = broker_now

    def broker_server_epoch_now(self, symbol):
        return self.broker_now


def test_offset_detection() -> bool:
    print("\n=== 1. detect_broker_utc_offset_seconds ===")
    ok = True

    true_utc_epoch = int(pd.Timestamp("2026-09-22 12:00:00", tz="UTC").timestamp())
    broker_epoch = true_utc_epoch + 3 * 3600  # broker running UTC+3 (EEST)

    offset = exporter.detect_broker_utc_offset_seconds(
        FakeOffsetGateway(broker_epoch), "XAUUSD", local_now_fn=lambda: true_utc_epoch)
    ok &= check("a broker running UTC+3 is detected as a +3h offset", offset == 3 * 3600, offset)

    jittered = exporter.detect_broker_utc_offset_seconds(
        FakeOffsetGateway(broker_epoch + 40), "XAUUSD", local_now_fn=lambda: true_utc_epoch)
    ok &= check("a few seconds of round-trip jitter still rounds to the same offset",
                jittered == 3 * 3600, jittered)

    zero = exporter.detect_broker_utc_offset_seconds(
        FakeOffsetGateway(true_utc_epoch), "XAUUSD", local_now_fn=lambda: true_utc_epoch)
    ok &= check("a broker actually running true UTC is detected as a 0 offset", zero == 0, zero)

    # A tick that's genuinely stale (market closed, last trade hours ago)
    # implies an offset nowhere near a clean 15-minute boundary - must raise
    # rather than silently export under a garbage offset.
    stale_gw = FakeOffsetGateway(broker_epoch - 70_620)  # ~19h37m "off" from a clean boundary
    raised = None
    try:
        exporter.detect_broker_utc_offset_seconds(stale_gw, "XAUUSD", local_now_fn=lambda: true_utc_epoch)
    except RuntimeError as exc:
        raised = exc
    ok &= check("a stale tick (implausible, non-15-minute offset) raises instead of exporting garbage",
                raised is not None and "stale" in str(raised), raised)

    return ok


# --------------------------------------------------------------------------- #
# format_bars
# --------------------------------------------------------------------------- #
def test_format_bars() -> bool:
    print("\n=== 2. format_bars ===")
    ok = True

    base = int(pd.Timestamp("2026-09-22 15:00:00", tz="UTC").timestamp())  # broker wall clock
    offset = 3 * 3600  # broker is UTC+3, so true UTC for `base` is 12:00:00
    raw = pd.DataFrame({
        "time": [base, base + 300, base + 600, base + 900],  # last row still forming
        "open":  [2350.123, 2350.456, 2350.789, 2351.111],
        "high":  [2351.0, 2351.3, 2351.6, 2351.9],
        "low":   [2349.5, 2349.8, 2350.1, 2350.4],
        "close": [2350.456, 2350.789, 2351.0, 2351.222],
        "volume": [100, 110, 120, 130],
    })
    out = exporter.format_bars(raw, offset, digits=2, bars=200)
    ok &= check("the still-forming last row is dropped", len(out) == 3, len(out))
    ok &= check("OHLC is rounded to the symbol's quote precision",
                list(out["open"]) == [2350.12, 2350.46, 2350.79], list(out["open"]))
    expected_first = pd.to_datetime(base - offset, unit="s", utc=True).strftime(exporter.DATETIME_FORMAT)
    ok &= check("broker wall-clock time is converted to genuine UTC",
                out["datetime"].iloc[0] == expected_first,
                (out["datetime"].iloc[0], expected_first))
    ok &= check("columns are exactly XTR's requested shape",
                list(out.columns) == exporter.CSV_COLUMNS, list(out.columns))

    # Only the still-forming bar exists yet (no closed bars) - must not crash,
    # must return the right (empty) shape.
    single = pd.DataFrame({"time": [1000], "open": [1.0], "high": [1.0], "low": [1.0],
                           "close": [1.0], "volume": [1]})
    out_single = exporter.format_bars(single, 0, 2, 200)
    ok &= check("with only the forming bar, format_bars returns an empty frame with the right columns",
                len(out_single) == 0 and list(out_single.columns) == exporter.CSV_COLUMNS,
                list(out_single.columns))

    # More closed bars available than requested - keep the LATEST N, not the
    # earliest, so a fixed-window file always shows the most recent history.
    base2 = 1_000_000
    six_bars = pd.DataFrame({
        "time": [base2 + i * 300 for i in range(6)],  # 5 closed + 1 forming
        "open": [1.0] * 6, "high": [1.0] * 6, "low": [1.0] * 6, "close": [1.0] * 6, "volume": [1] * 6,
    })
    truncated = exporter.format_bars(six_bars, 0, 2, bars=3)
    expected_times = [pd.to_datetime(base2 + s, unit="s", utc=True).strftime(exporter.DATETIME_FORMAT)
                      for s in (600, 900, 1200)]
    ok &= check("asking for fewer bars than available keeps the LATEST N closed bars",
                list(truncated["datetime"]) == expected_times,
                (list(truncated["datetime"]), expected_times))

    return ok


# --------------------------------------------------------------------------- #
# export_once end-to-end (fake gateway, no MT5)
# --------------------------------------------------------------------------- #
class FakeExportGateway:
    def __init__(self, digits: int = 2, broker_now: int = 2_000_000):
        self.digits = digits
        self.broker_now = broker_now

    def symbol_spec(self, symbol):
        return mt5_bars.SymbolSpec(name=symbol, digits=self.digits)

    def broker_server_epoch_now(self, symbol):
        return self.broker_now

    def get_bars(self, symbol, timeframe_name, count):
        base = 1_000_000
        return pd.DataFrame({
            "time": [base + i * 300 for i in range(count)],
            "open": [2350.0 + i * 0.1 for i in range(count)],
            "high": [2351.0 + i * 0.1 for i in range(count)],
            "low": [2349.0 + i * 0.1 for i in range(count)],
            "close": [2350.5 + i * 0.1 for i in range(count)],
            "volume": [100 + i for i in range(count)],
        })


def test_export_once() -> bool:
    print("\n=== 3. export_once end-to-end (fake gateway) ===")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        gw = FakeExportGateway()
        paths = exporter.export_once(gw, "XAUUSD", ["M5", "M15", "H1"], bars=5, out_dir=tmp,
                                     local_now_fn=lambda: 1_000_000)
        for tf in ("M5", "M15", "H1"):
            df = pd.read_csv(paths[tf])
            ok &= check(f"{tf} CSV exists with exactly 5 rows and XTR's columns",
                        os.path.exists(paths[tf]) and len(df) == 5
                        and list(df.columns) == exporter.CSV_COLUMNS, (paths[tf], len(df)))

        manifest = json.load(open(paths["manifest"]))
        ok &= check("manifest states the symbol, UTC timezone, precision and per-file bar counts",
                    manifest["symbol"] == "XAUUSD" and manifest["datetime_timezone"] == "UTC"
                    and manifest["quote_digits"] == 2
                    and manifest["bars_per_file"] == {"M5": 5, "M15": 5, "H1": 5},
                    manifest)
    return ok


# --------------------------------------------------------------------------- #
# xtr_export.last_closed_bar_time
# --------------------------------------------------------------------------- #
class FakeBarTimesGateway:
    def __init__(self, times: list):
        self.times = times
        self.requested_tf = None

    def get_bars(self, symbol, tf, count):
        self.requested_tf = tf
        return pd.DataFrame({"time": self.times[-count:]})


def test_last_closed_bar_time() -> bool:
    print("\n=== 4. xtr_export.last_closed_bar_time ===")
    ok = True
    original_gw = xtr_export.gw
    try:
        xtr_export.gw = FakeBarTimesGateway([100, 200, 300, 400])  # 400 is still forming
        t = xtr_export.last_closed_bar_time("XAUUSD")
        ok &= check("returns the last CLOSED bar's time, not the still-forming one", t == 300, t)
        ok &= check("the export trigger defaults to M1 closes (a fresh export every minute)",
                    xtr_export.gw.requested_tf == "M1", xtr_export.gw.requested_tf)

        xtr_export.gw = FakeBarTimesGateway([100, 200, 300])
        xtr_export.last_closed_bar_time("XAUUSD", "M5")
        ok &= check("--trigger-timeframe M5 still watches M5 (the old pace) when asked",
                    xtr_export.gw.requested_tf == "M5", xtr_export.gw.requested_tf)

        xtr_export.gw = FakeBarTimesGateway([500])  # only the forming bar exists
        t2 = xtr_export.last_closed_bar_time("XAUUSD")
        ok &= check("with no closed bar yet, returns None rather than the forming bar's time",
                    t2 is None, t2)

        # The poll loop's own new-bar check must use a sentinel, not None, as
        # its "nothing exported yet" starting value - otherwise this
        # legitimate None (not enough history) would compare equal to that
        # starting value and silently skip the very first export once real
        # history exists. See main()'s _UNSET sentinel.
        unset = object()
        ok &= check("a real None return still differs from a non-None 'nothing exported yet' sentinel "
                    "(main()'s poll loop relies on this to not skip its first real export)",
                    t2 != unset, (t2, unset))
    finally:
        xtr_export.gw = original_gw
    return ok


# --------------------------------------------------------------------------- #
# drive_uploader (fake Drive service - no google-api-python-client needed)
# --------------------------------------------------------------------------- #
class FakeDriveRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeDriveFiles:
    def __init__(self):
        self.created = []
        self.updated = []
        self._next_id = 1

    def create(self, body, media_body, fields):
        file_id = f"file{self._next_id}"
        self._next_id += 1
        self.created.append((body["name"], body["parents"]))
        return FakeDriveRequest({"id": file_id})

    def update(self, fileId, media_body):
        self.updated.append(fileId)
        return FakeDriveRequest({"id": fileId})


class FakeDriveService:
    def __init__(self):
        self._files = FakeDriveFiles()

    def files(self):
        return self._files


def test_drive_uploader() -> bool:
    print("\n=== 5. drive_uploader (fake Drive service, no network) ===")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        local_path = os.path.join(tmp, "XAUUSD_M5.csv")
        with open(local_path, "w") as f:
            f.write("datetime,open\n")
        svc = FakeDriveService()
        cache_path = os.path.join(tmp, ".drive_ids.json")
        fake_media = lambda path: object()

        ids1 = drive_uploader.sync_paths(svc, "folder123", {"M5": local_path}, cache_path,
                                         media_factory=fake_media)
        ok &= check("the first sync CREATEs the file under the target folder",
                    svc._files.created == [("XAUUSD_M5.csv", ["folder123"])]
                    and svc._files.updated == [] and ids1["M5"] == "file1", svc._files.created)
        cache_on_disk = json.load(open(cache_path))
        ok &= check("the returned file id is persisted to the cache file, keyed by folder+filename",
                    cache_on_disk.get("folder123::XAUUSD_M5.csv") == "file1", cache_on_disk)

        ids_same = drive_uploader.sync_paths(svc, "folder123", {"M5": local_path}, cache_path,
                                             media_factory=fake_media)
        ok &= check("a later sync with UNCHANGED content sends nothing (no create, no update)",
                    svc._files.created == [("XAUUSD_M5.csv", ["folder123"])]
                    and svc._files.updated == [] and ids_same["M5"] == "file1",
                    (svc._files.created, svc._files.updated))

        with open(local_path, "a") as f:
            f.write("2026-09-22 12:00:00,2650.10\n")
        ids2 = drive_uploader.sync_paths(svc, "folder123", {"M5": local_path}, cache_path,
                                         media_factory=fake_media)
        ok &= check("a later sync with CHANGED content UPDATEs the same file id instead of creating a new one",
                    svc._files.created == [("XAUUSD_M5.csv", ["folder123"])]  # unchanged
                    and svc._files.updated == ["file1"] and ids2["M5"] == "file1",
                    (svc._files.created, svc._files.updated))
        drive_uploader.sync_paths(svc, "folder123", {"M5": local_path}, cache_path,
                                  media_factory=fake_media)
        ok &= check("...and the new content's hash is remembered, so the next unchanged sync is skipped again",
                    svc._files.updated == ["file1"], svc._files.updated)

        # Switching --drive-folder-id with the same --out-dir/cache must
        # start a fresh file in the new folder, not keep silently updating
        # the old folder's file by its now-stale cached id.
        ids3 = drive_uploader.sync_paths(svc, "folder456", {"M5": local_path}, cache_path,
                                         media_factory=fake_media)
        ok &= check("switching to a different folder id CREATEs a new file there, not an update "
                    "of the old folder's file",
                    svc._files.created == [("XAUUSD_M5.csv", ["folder123"]), ("XAUUSD_M5.csv", ["folder456"])]
                    and ids3["M5"] == "file2", (svc._files.created, ids3))
        cache_after_switch = json.load(open(cache_path))
        ids_only = {k: v for k, v in cache_after_switch.items() if not k.startswith("sha256::")}
        ok &= check("both folders' file ids are retained in the cache, under distinct keys",
                    ids_only == {"folder123::XAUUSD_M5.csv": "file1",
                                 "folder456::XAUUSD_M5.csv": "file2"},
                    cache_after_switch)

        # The cache must be written via a temp file + atomic rename, not a
        # direct in-place write - no ".tmp" file should be left behind once
        # a sync completes normally.
        leftover_tmp = os.path.exists(cache_path + ".tmp")
        ok &= check("no leftover .tmp file after a normal sync (atomic rename cleaned it up)",
                    not leftover_tmp)

    return ok


def main() -> int:
    print("XTR_Export self-test\n")
    results = [
        test_offset_detection(),
        test_format_bars(),
        test_export_once(),
        test_last_closed_bar_time(),
        test_drive_uploader(),
    ]
    print()
    if all(results):
        print(f"ALL PASS ({len(results)}/{len(results)} suites)")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} suites failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

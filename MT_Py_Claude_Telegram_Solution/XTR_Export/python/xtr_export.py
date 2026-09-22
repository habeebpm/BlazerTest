#!/usr/bin/env python3
"""
Exports XAUUSD M5/M15/H1 bars from MT5 into the CSV shape XTR asked for
(datetime,open,high,low,close,volume - true UTC, one file per timeframe),
on every new M5 candle close, optionally pushing each file straight to a
Google Drive folder. See XTR_Export/README.md for the full spec this
satisfies and the two ways to get the files onto Drive.

Usage:
    python xtr_export.py --check                        # connect, print spec, one export, exit
    python xtr_export.py --once                          # one export cycle, exit
    python xtr_export.py                                 # loop: export on every new M5 close
    python xtr_export.py --upload-drive \\
        --drive-folder-id <id> --drive-credentials sa.json
"""
from __future__ import annotations

import argparse
import logging
import time

import exporter
import mt5_bars as gw

log = logging.getLogger("xtr_export")

DEFAULT_TIMEFRAMES = ["M5", "M15", "H1"]


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


def last_closed_m5_time(symbol: str):
    """The most recently CLOSED M5 bar's own time (broker epoch seconds) -
    used only to detect "a new bar closed since last export", never
    exported itself (export_once() re-reads and re-converts everything to
    true UTC on every cycle).
    """
    bars = gw.get_bars(symbol, "M5", 2)
    if len(bars) < 2:
        return None
    return int(bars["time"].iloc[-2])


def run_export(symbol: str, timeframes: list, bars: int, out_dir: str,
                drive_service, drive_folder_id: str | None, drive_cache_path: str) -> dict:
    paths = exporter.export_once(gw, symbol, timeframes, bars, out_dir, gw.local_utc_epoch_now)
    log.info("Exported %s: %s", symbol, {k: v for k, v in paths.items()})
    if drive_service is not None:
        import drive_uploader
        ids = drive_uploader.sync_paths(drive_service, drive_folder_id, paths, drive_cache_path)
        log.info("Synced to Drive folder %s: %s", drive_folder_id, ids)
    return paths


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframes", default=",".join(DEFAULT_TIMEFRAMES),
                        help=f"comma-separated (default {','.join(DEFAULT_TIMEFRAMES)})")
    parser.add_argument("--bars", type=int, default=200,
                        help="closed bars per file (default 200 - within XTR's requested 150-200)")
    parser.add_argument("--out-dir", default="xtr_data", dest="out_dir")
    parser.add_argument("--poll-seconds", type=int, default=15, dest="poll_seconds",
                        help="how often to check for a new closed M5 bar (default 15)")
    parser.add_argument("--once", action="store_true", help="export once and exit")
    parser.add_argument("--check", action="store_true",
                        help="connect, print symbol spec, export once, exit")
    parser.add_argument("--upload-drive", action="store_true", dest="upload_drive",
                        help="also push every export to Google Drive (needs --drive-folder-id "
                             "and --drive-credentials) - see README.md if your MT5 machine has "
                             "a desktop instead, Google Drive for Desktop needs none of this")
    parser.add_argument("--drive-folder-id", dest="drive_folder_id",
                        help="target Drive folder id (from the folder's URL)")
    parser.add_argument("--drive-credentials", dest="drive_credentials",
                        help="path to the service account JSON key")
    parser.add_argument("--login", type=int, help="MT5 account login (optional)")
    parser.add_argument("--password", help="MT5 account password")
    parser.add_argument("--server", help="MT5 broker server name")
    parser.add_argument("--terminal-path", dest="terminal_path", help="path to terminal64.exe")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    timeframes = [t.strip().upper() for t in args.timeframes.split(",") if t.strip()]

    if args.upload_drive and not (args.drive_folder_id and args.drive_credentials):
        parser.error("--upload-drive needs both --drive-folder-id and --drive-credentials")

    gw.connect(login=args.login, password=args.password, server=args.server,
               terminal_path=args.terminal_path)
    spec = gw.symbol_spec(args.symbol)
    log.info("Connected. %s quote precision: %d digits.", args.symbol, spec.digits)

    drive_service = None
    drive_cache_path = f"{args.out_dir}/.drive_ids.json"
    if args.upload_drive:
        import drive_uploader
        drive_service = drive_uploader.build_service(args.drive_credentials)
        log.info("Google Drive upload enabled -> folder %s", args.drive_folder_id)

    if args.check:
        run_export(args.symbol, timeframes, args.bars, args.out_dir,
                   drive_service, args.drive_folder_id, drive_cache_path)
        return 0

    if args.once:
        run_export(args.symbol, timeframes, args.bars, args.out_dir,
                   drive_service, args.drive_folder_id, drive_cache_path)
        return 0

    log.info("Watching %s M5 for a new closed candle every %ds - Ctrl+C to stop.",
              args.symbol, args.poll_seconds)
    # A distinct sentinel, not None - last_closed_m5_time() itself legitimately
    # returns None when fewer than 2 M5 bars exist yet (e.g. right after
    # connecting to a freshly added symbol). Starting last_bar_time at None
    # would make that "not enough history" None compare equal to a real
    # first-poll None and skip the very first export once history exists,
    # rather than exporting as soon as a real bar_time - or even a second
    # None-returning poll after a transient hiccup - is seen for the first time.
    _UNSET = object()
    last_bar_time = _UNSET
    while True:
        try:
            bar_time = last_closed_m5_time(args.symbol)
            if bar_time != last_bar_time:
                run_export(args.symbol, timeframes, args.bars, args.out_dir,
                           drive_service, args.drive_folder_id, drive_cache_path)
                last_bar_time = bar_time
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0
        except Exception:
            log.exception("Error during export cycle - will retry next poll")
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

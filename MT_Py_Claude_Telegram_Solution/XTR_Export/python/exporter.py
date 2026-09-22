"""
Pure formatting/export logic for XTR_Export - deliberately separated from
mt5_bars.py (the live MT5 connection) and xtr_export.py (the CLI/poll loop)
so every rule below is testable with a fake gateway and no real terminal
(see selftest.py).

Builds exactly what XTR asked for (see the conversation this was speced
in, and XTR_Export/README.md):
  - one CSV per timeframe: datetime,open,high,low,close,volume
  - only CLOSED bars (the still-forming bar is always dropped - same
    convention get_bars() itself documents, and the one
    ClaudeSMC_Trader/python/market_intel.py already relies on elsewhere in
    this repo)
  - `datetime` is GENUINE UTC, not broker-server wall clock. MT5's own
    `copy_rates_*` returns bar times as the BROKER SERVER's wall clock,
    encoded as if it were a UTC epoch - which is almost never actually
    true (EET/EEST brokers commonly run 2-3 hours ahead of UTC, and shift
    with their own DST calendar, not any particular exchange's). Silently
    handing that raw value to an external consumer as "UTC" would be
    exactly the kind of mislabeled-timezone bug that quietly wrecks an
    EMA/MACD read. See detect_broker_utc_offset_seconds() below - this
    module converts to true UTC itself, so XTR (or anyone else reading
    these files) never has to know or guess the broker's own offset.
  - fixed-window overwrite (always the latest N closed bars, not an
    ever-growing file) - simplest of the two behaviors XTR said either was
    fine with, and keeps every read a fixed, bounded cost.
  - OHLC rounded to the symbol's own quote precision (SymbolSpec.digits)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
CSV_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]

# Broker UTC offsets are always whole-15-minute increments (the most
# unusual real-world zones, e.g. Nepal's +5:45, still land on a 15-minute
# boundary) - rounding to the nearest 900 seconds absorbs the few seconds
# of round-trip/processing jitter between reading the broker's tick time
# and this machine's own clock without ever rounding across a real
# boundary.
_OFFSET_ROUNDING_SECONDS = 900

# gw.broker_server_epoch_now() reads the LAST TRADED TICK's time, not a true
# "current broker clock" - which is only actually close to now while the
# market is open and liquid. With the market closed (or right after a
# reboot before any tick has arrived) that tick can be hours or days old, so
# raw_offset stops being a small amount of network/processing jitter around
# a real UTC-offset boundary and becomes garbage - e.g. +20h instead of the
# broker's real +3h. Anything further than this from the nearest 15-minute
# boundary is treated as "the tick is stale, not a jitter", and raises
# rather than silently rounding a multi-hour error into a plausible-looking
# but wrong offset that would then mislabel every exported timestamp.
_MAX_OFFSET_JITTER_SECONDS = 120


def detect_broker_utc_offset_seconds(gw, symbol: str, local_now_fn) -> int:
    """How far ahead of true UTC the broker's own clock reads, in seconds.

    MT5 bar/tick times are the broker server's wall clock, numerically
    encoded as if it were a UTC epoch (see the module docstring) - so a
    broker running EEST (UTC+3) hands back a `time` value 3 hours LARGER
    than the true UTC epoch for the same instant. Comparing the broker's
    own current tick time against this machine's real UTC clock
    (`local_now_fn`, assumed NTP-correct) recovers that offset directly,
    with no manual "look up your broker's GMT offset" step required.

    Raises RuntimeError if the tick this is based on looks stale (see
    _MAX_OFFSET_JITTER_SECONDS) rather than risk exporting bars under a
    wrong offset - this is checked fresh on every export cycle specifically
    so a DST transition mid-session is picked up promptly; it can NOT,
    however, retroactively fix bars already exported under the old offset
    before the transition happened - see README.md's "Honest limitations".
    """
    broker_now = gw.broker_server_epoch_now(symbol)
    local_now = local_now_fn()
    raw_offset = broker_now - local_now
    rounded = round(raw_offset / _OFFSET_ROUNDING_SECONDS) * _OFFSET_ROUNDING_SECONDS
    jitter = abs(raw_offset - rounded)
    if jitter > _MAX_OFFSET_JITTER_SECONDS:
        raise RuntimeError(
            f"Broker tick time for {symbol} looks stale (implies a UTC offset of "
            f"{raw_offset / 3600:.2f}h before rounding, {jitter}s from the nearest clean "
            "15-minute boundary) - the market may be closed, or no tick has arrived since "
            "connecting yet. Refusing to export under an untrustworthy offset; retry once a "
            "live tick is available.")
    return rounded


def format_bars(df: pd.DataFrame, broker_offset_seconds: int, digits: int, bars: int) -> pd.DataFrame:
    """Raw MT5 bars (oldest first, `time` = broker epoch seconds, last row
    still forming) -> exactly `bars` closed rows in XTR's column shape,
    true-UTC `datetime` strings, OHLC rounded to `digits`, ascending,
    deduped, with the still-forming last row dropped.
    """
    if len(df) == 0:
        return pd.DataFrame(columns=CSV_COLUMNS)
    closed = df.iloc[:-1] if len(df) > 1 else df.iloc[0:0]
    closed = closed.tail(bars)
    true_utc_epoch = closed["time"].astype("int64") - broker_offset_seconds
    datetimes = pd.to_datetime(true_utc_epoch, unit="s", utc=True)
    out = pd.DataFrame({
        "datetime": datetimes.dt.strftime(DATETIME_FORMAT),
        "open": closed["open"].round(digits),
        "high": closed["high"].round(digits),
        "low": closed["low"].round(digits),
        "close": closed["close"].round(digits),
        "volume": closed["volume"].astype("int64"),
    })
    out = out.drop_duplicates(subset="datetime", keep="last")
    out = out.sort_values("datetime").reset_index(drop=True)
    return out


def write_csv(formatted: pd.DataFrame, path: str) -> None:
    formatted.to_csv(path, index=False, columns=CSV_COLUMNS)


def write_manifest(path: str, symbol: str, timeframe_bar_counts: dict, broker_offset_seconds: int,
                    digits: int) -> None:
    """A small sidecar file so XTR (or anyone else reading the CSVs from
    Drive) can confirm freshness and assumptions without a side-channel
    message - the timezone question XTR's own spec explicitly flagged as
    something it needed stated, not guessed.
    """
    manifest = {
        "symbol": symbol,
        "datetime_timezone": "UTC",
        "datetime_format": DATETIME_FORMAT + " (UTC, no offset suffix)",
        "quote_digits": digits,
        "broker_utc_offset_hours": broker_offset_seconds / 3600.0,
        "bars_per_file": timeframe_bar_counts,
        "exported_at_utc": datetime.now(timezone.utc).strftime(DATETIME_FORMAT),
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)


def export_once(gw, symbol: str, timeframes: list, bars: int, out_dir: str,
                 local_now_fn) -> dict:
    """Pulls, formats and writes one CSV per timeframe plus the manifest.
    Returns {timeframe: path} for whatever wants to know what just got
    written (the poll loop's logging, or the Drive uploader).
    """
    import os
    os.makedirs(out_dir, exist_ok=True)
    spec = gw.symbol_spec(symbol)
    offset = detect_broker_utc_offset_seconds(gw, symbol, local_now_fn)
    paths = {}
    counts = {}
    for tf in timeframes:
        raw = gw.get_bars(symbol, tf, bars + 1)
        formatted = format_bars(raw, offset, spec.digits, bars)
        path = os.path.join(out_dir, f"{symbol}_{tf}.csv")
        write_csv(formatted, path)
        paths[tf] = path
        counts[tf] = len(formatted)
    manifest_path = os.path.join(out_dir, f"{symbol}_manifest.json")
    write_manifest(manifest_path, symbol, counts, offset, spec.digits)
    paths["manifest"] = manifest_path
    return paths

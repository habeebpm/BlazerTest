#!/usr/bin/env python3
"""
Execution simulator: how many trades does a configuration actually FILL?

Counting qualifying signals overstates trading, sometimes by more than 2x: a
signal only becomes a trade if a position slot is free and no opposing
position is open, and slots stay occupied until the stop or trailing stop is
hit. This walks bars in order, opens and closes positions under the real
position rules, and reports fills rather than signals.

    python simulate.py                      # shipped config, synthetic bars
    python simulate.py --compare            # a few configurations side by side
    python simulate.py --csv bars.csv       # your own M5 bars
    python simulate.py --spread 0.40        # a wider spread

CSV format: a header row with time,open,high,low,close (extra columns ignored).
Export one from MT5 with Tools -> History Center, or from the Strategy Tester.

Every synthetic figure is a random walk with no edge in it by construction, so
treat fill COUNTS as indicative of frequency and ignore any notion of profit.
Only the MT5 Strategy Tester on real tick data can speak to profitability.
"""
from __future__ import annotations

import argparse
from dataclasses import replace

import numpy as np
import pandas as pd

import strategy as st
from config import TradeConfig

POINT = 0.01
BARS_PER_DAY = 288          # M5
LOT_OUNCES = 100            # 1 XAUUSD lot = 100 oz


def synthetic_frames(cfg: TradeConfig, n: int = 1500) -> list[pd.DataFrame]:
    """Random-walk M5 bars across several drift regimes."""
    seeds = [(0.20, 17), (-0.20, 23), (0.0, 9), (0.12, 3), (-0.10, 41),
             (0.05, 55), (-0.05, 61)]
    frames = []
    for drift, seed in seeds:
        rng = np.random.default_rng(seed)
        closes = 2000.0 + np.cumsum(drift / 3 + rng.normal(0.0, 0.87, n))
        idx = pd.date_range("2026-01-01", periods=n, freq="5min")
        noise = np.abs(rng.normal(0.0, 0.35, n))
        work = pd.DataFrame({"open": np.concatenate([[closes[0]], closes[:-1]]),
                             "high": closes + noise, "low": closes - noise,
                             "close": closes}, index=idx)
        htf_closes = np.concatenate([np.full(300, closes[0]), closes[::48]])
        htf_idx = pd.date_range(end=work.index[-1], periods=len(htf_closes), freq="4h")
        trend = pd.DataFrame({"open": htf_closes, "high": htf_closes,
                              "low": htf_closes, "close": htf_closes}, index=htf_idx)
        frames.append(st.compute_indicators(work, trend, cfg))
    return frames


def csv_frames(path: str, cfg: TradeConfig) -> list[pd.DataFrame]:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise SystemExit(f"{path} is missing column(s): {', '.join(sorted(missing))}")
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time").sort_index()
    trend = df.resample("4h").agg({"open": "first", "high": "max",
                                   "low": "min", "close": "last"}).dropna()
    return [st.compute_indicators(df, trend, cfg)]


def simulate(cfg: TradeConfig, frames: list[pd.DataFrame], spread: float = 0.25,
             warmup: int = 300) -> dict:
    """Walk the bars, open and close positions, and count what actually fills."""
    sl_dist = cfg.sl_distance(POINT)
    trail = cfg.trail_distance(POINT)
    trail_start = cfg.trail_start_distance(POINT)

    fills = bars = blocked_cap = blocked_opposite = 0
    holds: list[int] = []

    for df in frames:
        positions: list[dict] = []
        for i in range(warmup, len(df)):
            bar = df.iloc[i]
            high, low, close = bar["high"], bar["low"], bar["close"]

            # manage open positions against this bar
            open_still = []
            for pos in positions:
                if pos["dir"] == "buy":
                    if close - pos["entry"] >= trail_start:
                        pos["sl"] = max(pos["sl"], close - trail)
                    if low <= pos["sl"]:
                        holds.append(i - pos["bar"])
                        continue
                else:
                    if pos["entry"] - close >= trail_start:
                        pos["sl"] = min(pos["sl"], close + trail)
                    if high >= pos["sl"]:
                        holds.append(i - pos["bar"])
                        continue
                open_still.append(pos)
            positions = open_still

            sig = st.evaluate(df, cfg, i)
            if not sig.direction:
                continue
            if len(positions) >= cfg.max_open_positions:
                blocked_cap += 1
                continue
            if (not cfg.allow_opposite_positions
                    and any(p["dir"] != sig.direction for p in positions)):
                blocked_opposite += 1
                continue

            entry = close + (spread if sig.direction == "buy" else -spread)
            positions.append({
                "dir": sig.direction, "entry": entry, "bar": i,
                "sl": entry - sl_dist if sig.direction == "buy" else entry + sl_dist})
            fills += 1
        bars += len(df) - warmup

    signals = fills + blocked_cap + blocked_opposite
    return {
        "fills": fills,
        "fills_per_day": BARS_PER_DAY * fills / bars if bars else 0.0,
        "signals_per_day": BARS_PER_DAY * signals / bars if bars else 0.0,
        "avg_hold_bars": float(np.mean(holds)) if holds else 0.0,
        "blocked_cap": blocked_cap,
        "blocked_opposite": blocked_opposite,
        "bars": bars,
    }


def report(name: str, cfg: TradeConfig, res: dict, spread: float) -> None:
    cost = spread * cfg.lots * LOT_OUNCES
    print(f"\n{name}")
    print(f"  fills          {res['fills_per_day']:>6.1f}/day   "
          f"(signals {res['signals_per_day']:.1f}/day - "
          f"{res['signals_per_day'] - res['fills_per_day']:.1f} never filled)")
    print(f"  avg hold       {res['avg_hold_bars']:>6.0f} bars  "
          f"({res['avg_hold_bars']*5/60:.1f} hours)")
    print(f"  blocked        {res['blocked_cap']} by the position cap, "
          f"{res['blocked_opposite']} by an opposing position")
    print(f"  capacity       {2 * BARS_PER_DAY / max(res['avg_hold_bars'], 1):.1f}/day "
          f"is the ceiling at {cfg.max_open_positions} concurrent positions")
    print(f"  spread cost    ${cost:.2f}/trade -> "
          f"${cost * res['fills_per_day']:.2f}/day, "
          f"${cost * res['fills_per_day'] * 20:.2f}/month at {spread:.2f} spread")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="M5 bars with time,open,high,low,close")
    ap.add_argument("--spread", type=float, default=0.25, help="price spread (default 0.25)")
    ap.add_argument("--compare", action="store_true", help="compare several configurations")
    args = ap.parse_args()

    base = TradeConfig()
    frames = csv_frames(args.csv, base) if args.csv else synthetic_frames(base)
    source = args.csv if args.csv else "synthetic random-walk bars (no edge by construction)"
    print(f"Source: {source}")

    report("shipped configuration", base, simulate(base, frames, args.spread), args.spread)

    if args.compare:
        variants = [
            ("score gate off", replace(base, min_confidence=0.0)),
            ("gate off, 5 concurrent, hedging",
             replace(base, min_confidence=0.0, max_open_positions=5,
                     allow_opposite_positions=True)),
            ("all filters off (gate 0, no cross, no confirm, ADX 18)",
             replace(base, min_confidence=0.0, cross_lookback=0,
                     require_confirmation=False, adx_min_level=18.0)),
            ("all filters off + 40/20 pip stops",
             replace(base, min_confidence=0.0, cross_lookback=0,
                     require_confirmation=False, adx_min_level=18.0,
                     stop_loss_units=40.0, trailing_stop_units=20.0,
                     trail_start_units=20.0)),
        ]
        for name, cfg in variants:
            report(name, cfg, simulate(cfg, frames, args.spread), args.spread)

    print("\nFill counts describe frequency only. Nothing here estimates profit -")
    print("use the MT5 Strategy Tester on real tick data for that.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

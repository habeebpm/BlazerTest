#!/usr/bin/env python3
"""
Behavioural statistics for the shipped configuration.

WHAT THIS CAN AND CANNOT MEASURE
--------------------------------
Everything here runs on synthetic random-walk bars, which by construction
contain no edge. That makes the output trustworthy for BEHAVIOUR - how often
the strategy fires, what blocks it, how long it holds, how the score is
distributed, how sensitive the settings are - and worthless for PROFIT. No
win rate, profit factor or expectancy is reported, because any such number
from a random walk would be an artefact of the noise plus the spread.

Only the MT5 Strategy Tester on real tick data can answer the profit question.

    python report.py            # core statistics
    python report.py --sweep    # add the parameter-sensitivity sweep
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from statistics import mean, pstdev

import numpy as np
import pandas as pd

import strategy as st
from config import TradeConfig
from simulate import simulate, POINT, BARS_PER_DAY, LOT_OUNCES

# (label, drift per bar, sigma per bar, seed)
REGIMES = [
    ("strong uptrend",    0.10, 0.87, 17),
    ("mild uptrend",      0.04, 0.87, 3),
    ("strong downtrend", -0.10, 0.87, 23),
    ("mild downtrend",   -0.04, 0.87, 41),
    ("directionless",     0.00, 0.87, 9),
    ("directionless #2",  0.00, 0.87, 55),
    ("quiet (low vol)",   0.02, 0.45, 61),
    ("volatile",          0.02, 1.60, 77),
    ("very volatile",     0.00, 2.40, 83),
]


def make_frame(cfg: TradeConfig, drift: float, sigma: float, seed: int,
               n: int = 1500) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 2000.0 + np.cumsum(drift + rng.normal(0.0, sigma, n))
    idx = pd.date_range("2026-01-05", periods=n, freq="5min")   # a Monday
    noise = np.abs(rng.normal(0.0, sigma * 0.4, n))
    work = pd.DataFrame({"open": np.concatenate([[closes[0]], closes[:-1]]),
                         "high": closes + noise, "low": closes - noise,
                         "close": closes}, index=idx)
    htf_c = np.concatenate([np.full(300, closes[0]), closes[::48]])
    htf_i = pd.date_range(end=work.index[-1], periods=len(htf_c), freq="4h")
    trend = pd.DataFrame({"open": htf_c, "high": htf_c, "low": htf_c,
                          "close": htf_c}, index=htf_i)
    return st.compute_indicators(work, trend, cfg)


def entry_detail(cfg: TradeConfig, frames: list[pd.DataFrame]) -> dict:
    """Per-entry detail the position simulator does not return."""
    scores, dirs, counts, hours = [], [], [], []
    for df in frames:
        for i in range(300, len(df)):
            if not cfg.in_session(df.index[i]):
                continue
            sig = st.evaluate(df, cfg, i)
            if not sig.direction:
                continue
            side = sig.buy if sig.direction == "buy" else sig.sell
            scores.append(side.confidence)
            dirs.append(sig.direction)
            counts.append(side.count)
            hours.append(df.index[i].hour)
    return {"scores": scores, "dirs": dirs, "counts": counts, "hours": hours}


def bar(n: int, total: int, width: int = 28) -> str:
    return "#" * int(round(width * n / total)) if total else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    cfg = TradeConfig()
    print("=" * 74)
    print("XAUUSD CONFLUENCE EA - BEHAVIOURAL STATISTICS")
    print("=" * 74)
    print(f"lots {cfg.lots} | {cfg.working_timeframe} entries, {cfg.trend_timeframe} trend | "
          f"SL {cfg.stop_loss_units:g}/trail {cfg.trailing_stop_units:g} {cfg.distance_unit}")
    print(f"{cfg.min_confluences}-of-3 confluences, >={cfg.min_confirmed} confirmed, "
          f"score >= {cfg.min_confidence:g} | max {cfg.max_open_positions} positions | "
          f"session {cfg.session_start_hour}:00-{cfg.session_end_hour}:00 "
          f"GMT{cfg.session_gmt_offset:+g}")

    frames = [make_frame(cfg, d, s, seed) for _, d, s, seed in REGIMES]

    # ---------------- per-regime entry rate ----------------
    print("\n" + "-" * 74)
    print("ENTRIES BY MARKET REGIME  (each ~1200 M5 bars ~ 4.2 trading days)")
    print("-" * 74)
    print(f"{'regime':<20} {'fills/day':>10} {'signals/day':>12} {'avg hold':>10}")
    per_regime = []
    for (label, d, s, seed), df in zip(REGIMES, frames):
        r = simulate(cfg, [df])
        per_regime.append(r["fills_per_day"])
        print(f"{label:<20} {r['fills_per_day']:>10.1f} {r['signals_per_day']:>12.1f} "
              f"{r['avg_hold_bars']:>8.0f}b")
    print(f"\n{'across all regimes':<20} mean {mean(per_regime):.1f}/day  "
          f"sd {pstdev(per_regime):.1f}  range {min(per_regime):.1f}-{max(per_regime):.1f}")

    # ---------------- aggregate ----------------
    agg = simulate(cfg, frames)
    print("\n" + "-" * 74)
    print("WHERE THE SIGNALS GO  (all regimes pooled)")
    print("-" * 74)
    total = agg["signals_per_day"]
    rows = [
        ("qualifying signals", total),
        ("blocked: outside session", BARS_PER_DAY * agg["blocked_session"] / agg["bars"]),
        ("blocked: position cap", BARS_PER_DAY * agg["blocked_cap"] / agg["bars"]),
        ("blocked: opposing trade", BARS_PER_DAY * agg["blocked_opposite"] / agg["bars"]),
        ("ACTUAL FILLS", agg["fills_per_day"]),
    ]
    for label, v in rows:
        print(f"  {label:<28} {v:>6.2f}/day  {bar(int(v*10), int(total*10))}")

    print(f"\n  fill rate: {100*agg['fills_per_day']/total:.0f}% of qualifying signals become trades")
    print(f"  weekly (5 days): {agg['fills_per_day']*5:.0f}   "
          f"monthly (22 days): {agg['fills_per_day']*22:.0f}   "
          f"yearly (260): {agg['fills_per_day']*260:.0f}")

    # ---------------- holding / capacity ----------------
    print("\n" + "-" * 74)
    print("HOLDING TIME AND CAPACITY")
    print("-" * 74)
    h = agg["avg_hold_bars"]
    print(f"  average hold            {h:.0f} M5 bars ({h*5/60:.1f} hours)")
    print(f"  capacity ceiling        {cfg.max_open_positions * BARS_PER_DAY / max(h,1):.1f} fills/day "
          f"at {cfg.max_open_positions} concurrent positions")
    print(f"  utilisation             {100*agg['fills_per_day']/(cfg.max_open_positions*BARS_PER_DAY/max(h,1)):.0f}% "
          f"of that ceiling")

    # ---------------- entry detail ----------------
    det = entry_detail(cfg, frames)
    sc = np.array(det["scores"])
    print("\n" + "-" * 74)
    print(f"ENTRY QUALITY  ({len(sc)} qualifying signals sampled)")
    print("-" * 74)
    if len(sc):
        print(f"  score   min {sc.min():.0f}   median {np.median(sc):.0f}   "
              f"mean {sc.mean():.1f}   max {sc.max():.0f}")
        for lo, hi in [(50,55),(55,60),(60,70),(70,80),(80,101)]:
            n = int(((sc>=lo)&(sc<hi)).sum())
            print(f"    {lo:>3}-{hi-1:<3} {n:>4} ({100*n/len(sc):>3.0f}%)  {bar(n, len(sc))}")
        buys = det["dirs"].count("buy")
        print(f"\n  direction   {buys} buy / {len(sc)-buys} sell "
              f"({100*buys/len(sc):.0f}% long)")
        c = det["counts"]
        print(f"  confluences 2/3: {c.count(2)} ({100*c.count(2)/len(c):.0f}%)   "
              f"3/3: {c.count(3)} ({100*c.count(3)/len(c):.0f}%)")

    # ---------------- money ----------------
    print("\n" + "-" * 74)
    print("RISK AND COST PER DAY")
    print("-" * 74)
    per_dollar = cfg.lots * LOT_OUNCES
    risk = cfg.sl_distance(POINT) * per_dollar
    spread_cost = 0.25 * per_dollar
    f = agg["fills_per_day"]
    print(f"  risk per trade          ${risk:.2f}  (SL {cfg.stop_loss_units:g} "
          f"{cfg.distance_unit} = ${cfg.sl_distance(POINT):.2f} of price)")
    print(f"  max concurrent risk     ${risk*cfg.max_open_positions:.2f} "
          f"({cfg.max_open_positions} correlated positions)")
    print(f"  spread cost             ${spread_cost:.2f}/trade -> ${spread_cost*f:.2f}/day, "
          f"${spread_cost*f*22:.0f}/month")
    print(f"  daily-loss breaker      {cfg.max_daily_loss_pct:g}% - trips after "
          f"{{:.1f}} losers on a $1000 account".format(1000*cfg.max_daily_loss_pct/100/risk))
    print(f"  break-even requirement  the strategy must clear "
          f"${spread_cost*f*22:.0f}/month in spread before any profit")

    if args.sweep:
        print("\n" + "-" * 74)
        print("PARAMETER SENSITIVITY  (fills/day - frequency only, not profit)")
        print("-" * 74)
        base = agg["fills_per_day"]
        sweeps = [
            ("min_confidence", "min_confidence", [0, 40, 45, 50, 55, 60]),
            ("max_open_positions", "max_open_positions", [1, 2, 3, 4, 6, 8]),
            ("cross_lookback", "cross_lookback", [1, 4, 8, 12, 20]),
            ("adx_min_level", "adx_min_level", [15, 18, 22, 26, 30]),
            ("min_confluences", "min_confluences", [2, 3]),
        ]
        for label, field, values in sweeps:
            cells = []
            for v in values:
                c2 = replace(cfg, **{field: v})
                cells.append(f"{v}={simulate(c2, frames)['fills_per_day']:.1f}")
            print(f"  {label:<20} " + "  ".join(cells))
        print(f"\n  (shipped configuration = {base:.1f} fills/day)")

    print("\n" + "=" * 74)
    print("NOT MEASURED: win rate, profit factor, expectancy, drawdown.")
    print("Synthetic random walks contain no edge, so any P&L figure here would")
    print("be noise minus spread. Use the MT5 Strategy Tester on real tick data.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

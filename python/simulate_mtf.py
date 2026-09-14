#!/usr/bin/env python3
"""
Execution simulator for XAUUSD_MTF_RSI_MACD_BB_EA.mq5 - how many trades does the
multi-timeframe RSI/MACD/Bollinger-Bands/swing-structure strategy actually fire?

Mirrors simulate.py's methodology for the Confluence EA, on the same kind of
synthetic random-walk bars (no edge in them by construction - this answers
"how often would it try to trade", never "would it make money"). Reuses the
MT5-matching indicator formulas from indicators.py (Wilder RSI, SMA-signal
MACD, population-stdev Bollinger Bands) so the numbers here are consistent
with what the .mq5 file actually computes, and implements the same 4-criteria
per-timeframe scoring, the same timeframe-weighted combined score, the same
swing-low fractal/pivot scan, and the same position-cap / opposite-direction /
session rules as XAUUSD_MTF_RSI_MACD_BB_EA.mq5's OnTick().

    python simulate_mtf.py                  # Default preset, synthetic bars
    python simulate_mtf.py --compare        # Default vs Max-frequency preset
    python simulate_mtf.py --csv bars.csv   # your own M15 bars (time,open,high,low,close)

CSV format: a header row with time,open,high,low,close (extra columns ignored).
H1 and H4 bars are derived from the same M15 series by resampling, so all
three timeframes come from one consistent price path - exactly like a real
multi-timeframe read of one market.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from indicators import bollinger, macd as macd_ind, rsi as rsi_ind

M15_PER_DAY = 96             # bars/day at M15
POINT = 0.01                 # XAUUSD point on a 2-digit feed
PIP = POINT * 10.0           # 1 pip = $0.10 per 0.01 lot = 0.10 price units
LOT_OUNCES = 100


@dataclass(frozen=True)
class MtfConfig:
    """Mirrors the EA's inputs. Field names match the .mq5 inputs in spirit,
    not literally, to stay readable; see the comment on each for the input it
    stands in for."""
    rsi_period: int = 14                    # InpRsiPeriod
    rsi_lookback: int = 2                    # InpRsiLookback
    rsi_buy_level: float = 47.0              # InpRsiBuyLevel
    rsi_sell_level: float = 47.0             # InpRsiSellLevel
    macd_fast: int = 12                      # InpMacdFast
    macd_slow: int = 26                      # InpMacdSlow
    macd_signal: int = 9                     # InpMacdSignal
    bb_period: int = 20                      # InpBandsPeriod
    bb_dev: float = 2.0                      # InpBandsDeviation
    swing_scan_bars: int = 30                # InpSwingScanBars
    swing_pivot_width: int = 2               # InpSwingPivotWidth
    weight_tf1: float = 1.0                  # InpWeightTF1
    weight_tf2: float = 1.5                  # InpWeightTF2
    weight_tf3: float = 2.0                  # InpWeightTF3
    entry_threshold: float = 55.0            # InpEntryThreshold
    min_agreeing_tf: int = 2                 # InpMinAgreeingTF
    swing_bonus: float = 15.0                # InpSwingBonusPoints
    max_open_positions: int = 4              # InpMaxOpenPositions
    allow_opposite: bool = False             # InpAllowOpposite
    sl_pips: float = 60.0                    # InpStopLossPips ($6.00)
    trail_start_pips: float = 30.0           # InpTrailStartPips ($3.00)
    trail_pips: float = 30.0                 # InpTrailPips ($3.00)
    use_take_profit: bool = True             # InpUseTakeProfit
    tp_pips: float = 100.0                   # InpTakeProfitPips ($10.00)
    lots: float = 0.01                       # InpFixedLot
    use_session_filter: bool = True          # manual-window stand-in for the
    session_start_hour: float = 6.0          # broker-session filter (real
    session_end_hour: float = 23.0           # session data isn't available
    session_gmt_offset: float = 4.0          # to a synthetic simulator)

    def sl_distance(self) -> float:
        return self.sl_pips * PIP

    def trail_start_distance(self) -> float:
        return self.trail_start_pips * PIP

    def trail_distance(self) -> float:
        return self.trail_pips * PIP

    def tp_distance(self) -> float:
        return self.tp_pips * PIP if self.use_take_profit else 0.0

    def in_session(self, hour: int) -> bool:
        if not self.use_session_filter:
            return True
        s, e = self.session_start_hour, self.session_end_hour
        if s == e:
            return True  # 0/0/0/0 sentinel the EA also uses for "24h"
        h = (hour + self.session_gmt_offset) % 24
        return (s <= h < e) if s < e else (h >= s or h < e)


# ------------------------------------------------------------- data ----

def synthetic_m15_frames(n: int = 3500) -> list[pd.DataFrame]:
    """Random-walk M15 bars across several drift regimes - same idea as
    simulate.py's synthetic_frames, just at M15 instead of M5. n=3500 bars
    (~36 days) per regime leaves ~1900 bars evaluated after warmup once H4
    indicators have enough history."""
    seeds = [(0.20, 17), (-0.20, 23), (0.0, 9), (0.12, 3), (-0.10, 41),
             (0.05, 55), (-0.05, 61)]
    frames = []
    for drift, seed in seeds:
        rng = np.random.default_rng(seed)
        closes = 2000.0 + np.cumsum(drift / 3 + rng.normal(0.0, 0.55, n))
        idx = pd.date_range("2026-01-01", periods=n, freq="15min")
        noise = np.abs(rng.normal(0.0, 0.30, n))
        df = pd.DataFrame({"open": np.concatenate([[closes[0]], closes[:-1]]),
                           "high": closes + noise, "low": closes - noise,
                           "close": closes}, index=idx)
        frames.append(df)
    return frames


def csv_m15_frame(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise SystemExit(f"{path} is missing column(s): {', '.join(sorted(missing))}")
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").sort_index()


def resample_ohlc(df15: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df15.resample(rule).agg({"open": "first", "high": "max",
                                    "low": "min", "close": "last"}).dropna()


# ------------------------------------------------------- indicators ----

def tf_indicator_arrays(df: pd.DataFrame, cfg: MtfConfig) -> dict:
    close = df["close"]
    _, _, hist = macd_ind(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    _, mid, _ = bollinger(close, cfg.bb_period, cfg.bb_dev)
    return {
        "close": close.to_numpy(),
        "low": df["low"].to_numpy(),
        "rsi": rsi_ind(close, cfg.rsi_period).to_numpy(),
        "hist": hist.to_numpy(),
        "mid": mid.to_numpy(),
    }


def recent_swing_low(low: np.ndarray, end_idx: int, pivot_width: int,
                     scan_bars: int) -> float | None:
    """Mirrors FindRecentSwingLow() in the .mq5 file exactly: scans back from
    the last closed bar (end_idx) over scan_bars bars for the first (most
    recent) fractal pivot low - a bar strictly below the low of pivot_width
    bars on both sides of it."""
    for i in range(pivot_width, scan_bars):
        idx = end_idx - i
        if idx - pivot_width < 0:
            break
        candidate = low[idx]
        is_pivot = True
        for k in range(1, pivot_width + 1):
            if low[idx + k] <= candidate or low[idx - k] <= candidate:
                is_pivot = False
                break
        if is_pivot:
            return candidate
    return None


def tf_scores(ind: dict, end_idx: int, cfg: MtfConfig) -> tuple[int, int, bool, bool]:
    """Mirrors EvaluateTimeframe(): returns (buyCount, sellCount,
    swingHoldBuy, swingBreakSell) for the closed bar at end_idx."""
    lookback = max(1, cfg.rsi_lookback)
    if end_idx - lookback < 0 or end_idx - 1 < 0:
        return 0, 0, False, False

    rsi_now, rsi_prev = ind["rsi"][end_idx], ind["rsi"][end_idx - lookback]
    hist1, hist2 = ind["hist"][end_idx], ind["hist"][end_idx - 1]
    close1, mid1 = ind["close"][end_idx], ind["mid"][end_idx]

    if any(np.isnan(v) for v in (rsi_now, rsi_prev, hist1, hist2, mid1)):
        return 0, 0, False, False

    swing_low = recent_swing_low(ind["low"], end_idx, cfg.swing_pivot_width,
                                 cfg.swing_scan_bars)
    have_swing = swing_low is not None

    b1 = rsi_now > cfg.rsi_buy_level and rsi_now > rsi_prev
    b2 = hist1 > 0.0
    b3 = close1 > mid1
    b4 = have_swing and close1 > swing_low
    buy_count = int(b1) + int(b2) + int(b3) + int(b4)

    s1 = rsi_now < cfg.rsi_sell_level and rsi_now < rsi_prev
    s2 = hist1 < 0.0 and hist1 < hist2
    s3 = close1 < mid1
    s4 = have_swing and close1 < swing_low
    sell_count = int(s1) + int(s2) + int(s3) + int(s4)

    return buy_count, sell_count, b4, s4


def combined_signal(tf_results: list[tuple[int, int, bool, bool]],
                    cfg: MtfConfig) -> tuple[str | None, float, int]:
    """Mirrors EvaluateMtf() + Qualifies(): returns (direction or None,
    combinedScore, agreeingTF)."""
    weights = (cfg.weight_tf1, cfg.weight_tf2, cfg.weight_tf3)
    nets = [b - s for b, s, _, _ in tf_results]
    total_w = sum(weights)
    weighted_sum = sum(n * w for n, w in zip(nets, weights))
    score = 100.0 * weighted_sum / (4.0 * total_w) if total_w else 0.0
    sign = 1 if score > 0 else (-1 if score < 0 else 0)

    swing_confirmed = False
    if sign > 0:
        swing_confirmed = any(r[2] for r in tf_results)
    elif sign < 0:
        swing_confirmed = any(r[3] for r in tf_results)
    if swing_confirmed:
        score += sign * cfg.swing_bonus
    score = max(-100.0, min(100.0, score))
    sign = 1 if score > 0 else (-1 if score < 0 else 0)

    agreeing = 0
    if sign > 0:
        agreeing = sum(1 for n in nets if n >= 3)
    elif sign < 0:
        agreeing = sum(1 for n in nets if n <= -3)

    direction = None
    if sign != 0 and abs(score) >= cfg.entry_threshold and agreeing >= cfg.min_agreeing_tf:
        direction = "buy" if sign > 0 else "sell"
    return direction, score, agreeing


# --------------------------------------------------------- simulation ----

def simulate(df15: pd.DataFrame, cfg: MtfConfig, spread: float = 0.30,
            warmup_h4_bars: int = 100) -> dict:
    h1 = resample_ohlc(df15, "1h")
    h4 = resample_ohlc(df15, "4h")
    ind15 = tf_indicator_arrays(df15, cfg)
    ind1h = tf_indicator_arrays(h1, cfg)
    ind4h = tf_indicator_arrays(h4, cfg)

    sl_dist, trail_start, trail_dist = (cfg.sl_distance(), cfg.trail_start_distance(),
                                        cfg.trail_distance())
    tp_dist = cfg.tp_distance()

    warmup_m15 = max(warmup_h4_bars * 16, cfg.bb_period + cfg.swing_scan_bars + 5)
    n = len(df15)

    positions: list[dict] = []
    fills = signals = blocked_cap = blocked_opposite = blocked_session = 0
    tp_exits = sl_exits = 0
    holds: list[int] = []
    eligible_bars = session_bars = 0

    for i in range(warmup_m15, n):
        bar = df15.iloc[i]
        high, low, close = bar["high"], bar["low"], bar["close"]

        # manage open positions against this bar first. Stop/trail is
        # checked before the take-profit within the same bar - a common,
        # slightly conservative simplification when only OHLC (not tick)
        # data is available to tell which was actually touched first.
        still_open = []
        for pos in positions:
            if pos["dir"] == "buy":
                if close - pos["entry"] >= trail_start:
                    pos["sl"] = max(pos["sl"], close - trail_dist)
                if low <= pos["sl"]:
                    holds.append(i - pos["bar"]); sl_exits += 1
                    continue
                if tp_dist > 0.0 and high >= pos["entry"] + tp_dist:
                    holds.append(i - pos["bar"]); tp_exits += 1
                    continue
            else:
                if pos["entry"] - close >= trail_start:
                    pos["sl"] = min(pos["sl"], close + trail_dist)
                if high >= pos["sl"]:
                    holds.append(i - pos["bar"]); sl_exits += 1
                    continue
                if tp_dist > 0.0 and low <= pos["entry"] - tp_dist:
                    holds.append(i - pos["bar"]); tp_exits += 1
                    continue
            still_open.append(pos)
        positions = still_open

        elapsed = (i + 1) * 15
        h1_idx = elapsed // 60 - 1
        h4_idx = elapsed // 240 - 1
        if h1_idx < 0 or h4_idx < 0 or h1_idx >= len(h1) or h4_idx >= len(h4):
            continue

        eligible_bars += 1
        in_sess = cfg.in_session(df15.index[i].hour)
        session_bars += in_sess

        tf1 = tf_scores(ind15, i, cfg)
        tf2 = tf_scores(ind1h, h1_idx, cfg)
        tf3 = tf_scores(ind4h, h4_idx, cfg)
        direction, score, agreeing = combined_signal([tf1, tf2, tf3], cfg)
        if direction is None:
            continue
        signals += 1

        if not in_sess:
            blocked_session += 1
            continue
        if len(positions) >= cfg.max_open_positions:
            blocked_cap += 1
            continue
        if not cfg.allow_opposite and any(p["dir"] != direction for p in positions):
            blocked_opposite += 1
            continue

        entry = close + (spread if direction == "buy" else -spread)
        positions.append({
            "dir": direction, "entry": entry, "bar": i,
            "sl": entry - sl_dist if direction == "buy" else entry + sl_dist,
        })
        fills += 1

    return {
        "fills": fills, "signals": signals,
        "blocked_cap": blocked_cap, "blocked_opposite": blocked_opposite,
        "blocked_session": blocked_session,
        "tp_exits": tp_exits, "sl_exits": sl_exits,
        "eligible_bars": eligible_bars,
        "session_share": session_bars / eligible_bars if eligible_bars else 0.0,
        "fills_per_day": M15_PER_DAY * fills / eligible_bars if eligible_bars else 0.0,
        "signals_per_day": M15_PER_DAY * signals / eligible_bars if eligible_bars else 0.0,
        "avg_hold_bars": float(np.mean(holds)) if holds else 0.0,
    }


def simulate_many(frames: list[pd.DataFrame], cfg: MtfConfig, spread: float) -> dict:
    agg = {"fills": 0, "signals": 0, "blocked_cap": 0, "blocked_opposite": 0,
          "blocked_session": 0, "tp_exits": 0, "sl_exits": 0,
          "eligible_bars": 0, "holds": []}
    for df in frames:
        r = simulate(df, cfg, spread)
        for k in ("fills", "signals", "blocked_cap", "blocked_opposite",
                 "blocked_session", "tp_exits", "sl_exits", "eligible_bars"):
            agg[k] += r[k]
        if r["avg_hold_bars"]:
            agg["holds"].append(r["avg_hold_bars"])
    eb = agg["eligible_bars"]
    return {
        **agg,
        "fills_per_day": M15_PER_DAY * agg["fills"] / eb if eb else 0.0,
        "signals_per_day": M15_PER_DAY * agg["signals"] / eb if eb else 0.0,
        "session_share": 1.0 - agg["blocked_session"] / max(agg["signals"], 1),
        "avg_hold_bars": float(np.mean(agg["holds"])) if agg["holds"] else 0.0,
        "days": eb / M15_PER_DAY,
    }


def report(name: str, cfg: MtfConfig, res: dict, spread: float) -> None:
    cost = spread * cfg.lots * LOT_OUNCES
    print(f"\n{name}  ({res['days']:.0f} simulated days)")
    print(f"  signals        {res['signals_per_day']:>6.2f}/day")
    print(f"  fills          {res['fills_per_day']:>6.2f}/day  "
          f"({res['signals_per_day'] - res['fills_per_day']:.2f}/day never filled)")
    print(f"  blocked        {res['blocked_cap']} by the 4-position cap, "
          f"{res['blocked_opposite']} by an opposing position, "
          f"{res['blocked_session']} outside the session")
    exits = res['tp_exits'] + res['sl_exits']
    tp_share = 100.0 * res['tp_exits'] / exits if exits else 0.0
    print(f"  exits          {res['tp_exits']} take-profit ({tp_share:.0f}%), "
          f"{res['sl_exits']} stop/trail ({100.0-tp_share:.0f}%)"
          if exits else "  exits          none yet")
    print(f"  avg hold       {res['avg_hold_bars']:>6.0f} bars "
          f"({res['avg_hold_bars']*15/60:.1f} hours)"
          if res['avg_hold_bars'] else "  avg hold       n/a (no exits yet)")
    print(f"  session        {cfg.session_start_hour:g}:00-{cfg.session_end_hour:g}:00 "
          f"GMT{cfg.session_gmt_offset:+g} = {(cfg.session_end_hour-cfg.session_start_hour)%24 or 24:.0f}h/day"
          if cfg.use_session_filter else "  session        filter off (24h)")
    print(f"  spread cost    ${cost:.2f}/trade -> ${cost*res['fills_per_day']:.2f}/day, "
          f"${cost*res['fills_per_day']*20:.2f}/month at {spread:.2f} spread")
    tp_line = f", ${cfg.tp_pips*PIP:.2f} target" if cfg.use_take_profit else " (no take-profit)"
    print(f"  $6/$3{'/$10' if cfg.use_take_profit else ''} risk   "
          f"${cfg.sl_pips*PIP:.2f} stop, ${cfg.trail_pips*PIP:.2f} trail{tp_line} "
          f"(per {cfg.lots:.2f}-lot position, up to {cfg.max_open_positions} at once)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="M15 bars with time,open,high,low,close")
    ap.add_argument("--spread", type=float, default=0.30, help="price spread (default 0.30)")
    ap.add_argument("--compare", action="store_true",
                    help="Default preset vs the Max-frequency preset")
    args = ap.parse_args()

    default_cfg = MtfConfig()
    frames = [csv_m15_frame(args.csv)] if args.csv else synthetic_m15_frames()
    source = args.csv if args.csv else "synthetic random-walk bars (no edge by construction)"
    print(f"Source: {source}")

    report("Default preset", default_cfg,
          simulate_many(frames, default_cfg, args.spread), args.spread)

    if args.compare:
        no_tp_cfg = replace(default_cfg, use_take_profit=False)
        report("same, but no take-profit (trail-only, for comparison)", no_tp_cfg,
              simulate_many(frames, no_tp_cfg, args.spread), args.spread)

        max_freq_cfg = replace(default_cfg, allow_opposite=True,
                               use_session_filter=True,
                               session_start_hour=0.0, session_end_hour=0.0)
        report("Max-frequency preset (opposite entries + 24h session; "
              "this synthetic sim can't model the spread-cap or minute-buffer "
              "changes, only what they're for)",
              max_freq_cfg, simulate_many(frames, max_freq_cfg, args.spread), args.spread)

        no_gate_cfg = replace(default_cfg, entry_threshold=0.0, min_agreeing_tf=1)
        report("gates fully open (threshold 0, 1-of-3 agreement) - upper bound, "
              "not a recommended configuration",
              no_gate_cfg, simulate_many(frames, no_gate_cfg, args.spread), args.spread)

    print("\nSignal/fill COUNTS describe frequency only, on synthetic bars with no")
    print("edge in them by construction. Nothing here estimates profit, win rate,")
    print("or drawdown - only the MT5 Strategy Tester on real tick data can.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

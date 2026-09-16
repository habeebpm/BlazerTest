#!/usr/bin/env python3
"""
Historical backtest harness for claude_signal_bot.py.

The XTR pipeline moved its trading decision off deterministic rules and onto
Claude's real-time judgment (see CLAUDE_SIGNAL_PIPELINE.md), which means
there is no rule table to replay against history the way `simulate.py`
replays the other, mechanical EA. What CAN be backtested honestly is the
whole pipeline running against real historical bars, calling the REAL
production functions from claude_signal_bot.py for every step - the exact
same direction_for/regime_for/compute_hints/build_analysis_prompt/
validate_decision/position_size code a live run would use, not a
reimplementation that could drift from it. This module supplies the parts a
historical replay needs that the live system gets from MQL5 or from waiting
in real time:

  - resampling raw M1 OHLC into M5/M15/H1 and computing the same MT5-style
    indicators (via indicators.py) that ClaudeSignalEA.mq5 exports live
  - building a ChartSnapshot at each historical M5 bar close using only
    data that would actually have been "closed" as of that moment (no
    look-ahead)
  - simulating each trade's outcome by scanning forward through the M1
    path for a stop/target/trailing-stop touch or a time-decay expiry,
    since there's no live EA here to fill and manage the position

    python backtest_xtr.py --selftest                        # offline checks, no data/key needed
    python backtest_xtr.py --csv bars.csv --stub              # harness demo, no API key (NOT Claude - see below)
    python backtest_xtr.py --csv bars.csv --max-cycles 20     # real Claude backtest, capped for cost
    python backtest_xtr.py --csv bars.csv                     # real Claude backtest, full file

`--csv` is a plain M1 OHLC file: a header row `time,open,high,low,close`,
ascending chronological order, `time` parseable by pandas (e.g.
`2026.09.16 10:31` matching the rest of this project's convention, or ISO).

IMPORTANT: `--stub` does NOT call Claude. It substitutes a content-blind
mechanical stand-in (the same advisory hint functions Claude is shown, with
no judgment applied) purely to exercise this harness's own mechanics -
snapshot building, sizing, outcome simulation, state bookkeeping - without
needing an API key or spending money. Its trade decisions are not a
strategy and its results are not a performance backtest of anything. A real
backtest requires ANTHROPIC_API_KEY (or --api-key) and costs one real API
call per evaluated cycle - use --max-cycles to bound that cost. See
BACKTEST.md for a worked-through cost estimate and the simplifications this
harness makes (fixed synthetic spread, OHLC-only intrabar ordering, no
weekend/gap handling beyond what's in the data).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

import claude_signal_bot as bot
import indicators as ind

TRAIL_USD_DEFAULT = 4.0   # matches ClaudeSignalEA.mq5's InpTrailingUSD/InpTrailStartUSD default
M5_IND_KEYS_BASIC = ("ema9", "ema21", "rsi14", "macd_hist")
M5_IND_KEYS_FULL = M5_IND_KEYS_BASIC + ("adx14", "atr14", "bb_upper", "bb_lower")


# --------------------------------------------------------------------------
# Data loading / resampling
# --------------------------------------------------------------------------

def load_m1_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing required column(s) {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").drop_duplicates("time").set_index("time")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    return df[["open", "high", "low", "close"]]


def resample_ohlc(m1: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Bars labeled by their OPEN time (pandas label='left'), matching MT5's
    own convention (rates[i].time is the bar's open time) and this repo's
    export format."""
    agg = (
        m1.resample(f"{minutes}min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    return agg


def compute_indicator_series(df: pd.DataFrame, full: bool = False) -> dict:
    """MT5-matching indicators (via indicators.py) over the WHOLE series.
    pandas' rolling/ewm are inherently causal - the value at index i depends
    only on rows <= i - so indexing this at position i is exactly "as of
    that bar's close", the same closed-bar semantics the EA uses."""
    close, high, low = df["close"], df["high"], df["low"]
    out = {"ema9": ind.ema(close, 9), "ema21": ind.ema(close, 21), "rsi14": ind.rsi(close, 14)}
    _, _, hist = ind.macd(close)
    out["macd_hist"] = hist
    if full:
        adx14, _, _ = ind.adx(high, low, close, 14)
        out["adx14"] = adx14
        out["atr14"] = ind.atr(high, low, close, 14)
        bb_upper, _, bb_lower = ind.bollinger(close, 20, 2.0)
        out["bb_upper"], out["bb_lower"] = bb_upper, bb_lower
    return out


# --------------------------------------------------------------------------
# No-look-ahead "as of this instant" bar selection
# --------------------------------------------------------------------------

def closed_bars_as_of(df: pd.DataFrame, bar_minutes: int, as_of: pd.Timestamp, n: int) -> pd.DataFrame:
    """The last `n` bars of `df` (labeled by open time) that have fully
    CLOSED by `as_of`. A bar labeled T spans [T, T+bar_minutes); it is
    closed once as_of >= T + bar_minutes, i.e. T <= as_of - bar_minutes.
    Using `T <= as_of` instead (the naive version) would leak the bar that
    just started exactly at as_of on a shared timeframe boundary (e.g. an
    M15 bar starting exactly when the M5 bar that triggered this cycle
    closes) - this off-by-one is why it's a dedicated helper, not an inline
    slice at each call site."""
    cutoff = as_of - timedelta(minutes=bar_minutes)
    return df.loc[:cutoff].tail(n)


def _bars_frame(df_slice: pd.DataFrame) -> pd.DataFrame:
    """Reshapes a time-indexed OHLC slice into the same shape
    parse_chart_file produces: a `time` column (not the index) plus
    open/high/low/close, oldest first - what check_liquidity_sweep and the
    prompt's bar dumps expect."""
    out = df_slice.reset_index()
    out.columns = ["time", "open", "high", "low", "close"]
    return out


def build_snapshot_at(as_of: pd.Timestamp, m1: pd.DataFrame, m5: pd.DataFrame, m15: pd.DataFrame,
                       h1: pd.DataFrame, m5_ind: dict, m15_ind: dict, h1_ind: dict,
                       symbol: str, equity: float, spread_price: float) -> "bot.ChartSnapshot | None":
    """Builds the exact same ChartSnapshot dataclass claude_signal_bot's
    live path constructs from the EA's export file - here, from historical
    bars instead. Returns None if there isn't yet enough closed history on
    any timeframe (early in the warm-up window)."""
    m1_closed = closed_bars_as_of(m1, 1, as_of, 30)
    m5_closed = closed_bars_as_of(m5, 5, as_of, 30)
    m15_closed = closed_bars_as_of(m15, 15, as_of, 30)
    h1_closed = closed_bars_as_of(h1, 60, as_of, 30)
    if m1_closed.empty or m5_closed.empty or m15_closed.empty or h1_closed.empty:
        return None

    idx5, idx15, idx1h = m5_closed.index[-1], m15_closed.index[-1], h1_closed.index[-1]

    def row(ind_dict: dict, idx, keys) -> dict:
        out = {}
        for k in keys:
            v = ind_dict[k].loc[idx]
            out[k] = None if pd.isna(v) else float(v)
        return out

    m5_row = row(m5_ind, idx5, M5_IND_KEYS_FULL)
    m15_row = {**row(m15_ind, idx15, M5_IND_KEYS_BASIC), "adx14": None, "atr14": None, "bb_upper": None, "bb_lower": None}
    h1_row = {**row(h1_ind, idx1h, M5_IND_KEYS_BASIC), "adx14": None, "atr14": None, "bb_upper": None, "bb_lower": None}

    if any(m5_row[k] is None for k in M5_IND_KEYS_BASIC):
        return None   # still inside the indicator warm-up window

    macd_hist_m5 = m5_ind["macd_hist"].loc[:idx5].tail(10).tolist()
    close_price = float(m5_closed["close"].iloc[-1])
    half_spread = spread_price / 2.0

    # No live EA here to compute/export htf_align, so derive it locally via
    # the identical rule (bot.htf_gate_from_directions) - see that
    # function's docstring for why this is the one place Python computes
    # it rather than trusting an exported value.
    htf_align = bot.htf_gate_from_directions(
        bot.direction_for(m5_row), bot.direction_for(m15_row), bot.direction_for(h1_row))

    return bot.ChartSnapshot(
        symbol=symbol, digits=2, point=0.01, tick_value=1.0, tick_size=0.01,
        volume_min=0.01, volume_max=50.0, volume_step=0.01,
        bid=close_price - half_spread, ask=close_price + half_spread,
        spread=int(round(spread_price / 0.01)), equity=equity, exported_at=as_of.isoformat(),
        htf_align=htf_align,
        ind={"M5": m5_row, "M15": m15_row, "H1": h1_row},
        macd_hist_m5=macd_hist_m5,
        bars={"M1": _bars_frame(m1_closed), "M5": _bars_frame(m5_closed),
              "M15": _bars_frame(m15_closed), "H1": _bars_frame(h1_closed)},
    )


# --------------------------------------------------------------------------
# Trade outcome simulation - the harness stands in for the EA's fill/manage
# loop, since there's no live terminal to do it here.
# --------------------------------------------------------------------------

def _finish(exit_price: float, exit_time, direction: str, entry_price: float,
            lot: float, value_per_unit: float, reason: str) -> dict:
    if direction == "BUY":
        profit = (exit_price - entry_price) * lot * value_per_unit
    else:
        profit = (entry_price - exit_price) * lot * value_per_unit
    return {"exit_time": exit_time, "exit_price": exit_price, "reason": reason,
            "profit": round(profit, 2), "outcome": "WIN" if profit >= 0 else "LOSS"}


def simulate_outcome(m1: pd.DataFrame, entry_time, direction: str, entry_price: float, sl: float,
                      tp: float, lot: float, value_per_unit: float, decay_seconds: float,
                      trail_usd: float = TRAIL_USD_DEFAULT) -> dict:
    """Scans forward M1-bar-by-bar from entry_time, applying the same fixed-
    dollar trailing stop ClaudeSignalEA.mq5 runs live (only ever tightens),
    until SL/trailed-SL, TP, or the time-decay horizon is hit.

    Simplification, clearly not silent: OHLC bars carry no intrabar
    sequencing, so a bar whose range contains both the (possibly trailed)
    stop and the target is resolved stop-first, and the trailing update
    from that same bar's favorable extreme is applied before checking the
    stop - both standard, slightly conservative choices for OHLC-only
    backtesting, not a claim about the true tick-level path.
    """
    horizon_end = entry_time + timedelta(seconds=decay_seconds)
    path = m1.loc[entry_time:horizon_end]
    path = path[path.index > entry_time]

    trail_distance = (trail_usd / (lot * value_per_unit)) if (lot > 0 and value_per_unit > 0) else None
    current_sl = sl
    last_close, last_time = entry_price, entry_time

    for t, r in path.iterrows():
        last_close, last_time = float(r["close"]), t
        if trail_distance is not None:
            if direction == "BUY" and (r["high"] - entry_price) >= trail_distance:
                current_sl = max(current_sl, r["high"] - trail_distance)
            elif direction == "SELL" and (entry_price - r["low"]) >= trail_distance:
                current_sl = min(current_sl, r["low"] + trail_distance)

        if direction == "BUY":
            if r["low"] <= current_sl:
                return _finish(current_sl, t, direction, entry_price, lot, value_per_unit,
                                "TRAIL" if current_sl > sl else "SL")
            if r["high"] >= tp:
                return _finish(tp, t, direction, entry_price, lot, value_per_unit, "TP")
        else:
            if r["high"] >= current_sl:
                return _finish(current_sl, t, direction, entry_price, lot, value_per_unit,
                                "TRAIL" if current_sl < sl else "SL")
            if r["low"] <= tp:
                return _finish(tp, t, direction, entry_price, lot, value_per_unit, "TP")

    return _finish(last_close, last_time, direction, entry_price, lot, value_per_unit, "EXPIRED")


# --------------------------------------------------------------------------
# Content-blind stub decider - NOT Claude, see the module docstring
# --------------------------------------------------------------------------

def stub_decision(snap: "bot.ChartSnapshot", state: dict, cfg: "bot.BotConfig") -> dict:
    """A mechanical stand-in with the SAME signature/return shape as
    bot.analyze_with_claude, used only to exercise this harness's mechanics
    offline. It applies no judgment - it takes whichever computed hint
    fires, uninterpreted - and must never be mistaken for a strategy result
    (see the module docstring's IMPORTANT note)."""
    m5_direction = bot.direction_for(snap.ind["M5"])
    m15_direction = bot.direction_for(snap.ind["M15"])
    h1_direction = bot.direction_for(snap.ind["H1"])
    regime = bot.regime_for(snap.ind["M5"])
    hints = bot.compute_hints(snap, m5_direction, regime)

    candidate = hints["trend_continuation_pullback"] or hints["rsi_extreme_bounce"]
    decision = {
        "action": candidate["direction"] if candidate else "NONE",
        "setup_type": candidate["setup_type"] if candidate else None,
        "confidence": 65.0 if candidate else 0.0,
        "self_correction": "stub decider - not Claude, no judgment applied",
        "reasoning": "stub decider - not Claude, no judgment applied",
    }
    if candidate:
        atr = snap.ind["M5"].get("atr14") or 0.5
        price = snap.ask if decision["action"] == "BUY" else snap.bid
        dist = max(1.0 * atr, 0.01)
        decision["sl"] = price - dist if decision["action"] == "BUY" else price + dist
        decision["tp"] = price + 1.75 * dist if decision["action"] == "BUY" else price - 1.75 * dist
    else:
        decision["sl"] = decision["tp"] = None
    decision.update({"m5_direction": m5_direction, "m15_direction": m15_direction,
                      "h1_direction": h1_direction, "regime": regime, "hints": hints})
    return decision


# --------------------------------------------------------------------------
# Main replay loop - reuses claude_signal_bot's real functions directly
# --------------------------------------------------------------------------

def run_backtest(m1: pd.DataFrame, cfg: "bot.BotConfig", symbol: str, start_equity: float,
                  warmup_bars: int = 250, decide_fn=None, max_cycles: int | None = None,
                  spread_price: float = 0.25, trail_usd: float = TRAIL_USD_DEFAULT,
                  log=lambda *a, **k: None) -> dict:
    decide_fn = decide_fn or bot.analyze_with_claude

    m5 = resample_ohlc(m1, 5)
    m15 = resample_ohlc(m1, 15)
    h1 = resample_ohlc(m1, 60)
    m5_ind = compute_indicator_series(m5, full=True)
    m15_ind = compute_indicator_series(m15)
    h1_ind = compute_indicator_series(h1)

    state = {"last_signal_id": 0, "last_bar_time": None, "outcome_lines_seen": 0,
             "ack_lines_seen": 0, "standdown": {}, "pending": {}, "trade_history": {},
             "htf_gate_skips": 0}
    equity = start_equity
    trades: list[dict] = []
    cycles_evaluated = 0
    open_exit_time = None

    for i in range(min(warmup_bars, len(m5) - 1), len(m5)):
        as_of = m5.index[i] + timedelta(minutes=5)

        m5_dir_now = "MIXED"
        m5_basic = {k: (None if pd.isna(m5_ind[k].loc[m5.index[i]]) else float(m5_ind[k].loc[m5.index[i]]))
                    for k in M5_IND_KEYS_BASIC}
        if all(v is not None for v in m5_basic.values()):
            m5_dir_now = bot.direction_for(m5_basic)
        bot.update_standdown(state, [], m5_dir_now)   # cheap - lets standdown-reset tracking progress every bar

        if open_exit_time is not None and as_of < open_exit_time:
            continue
        open_exit_time = None

        if max_cycles is not None and cycles_evaluated >= max_cycles:
            break

        snap = build_snapshot_at(as_of, m1, m5, m15, h1, m5_ind, m15_ind, h1_ind, symbol, equity, spread_price)
        if snap is None:
            continue

        # Same mechanical cost pre-filter as the live pipeline (run_once) -
        # counted separately from cycles_evaluated so the backtest report
        # shows the actual reduction in Claude calls, not just trade counts.
        if cfg.require_htf_gate and snap.htf_align == "NONE":
            state["htf_gate_skips"] += 1
            log(f"[{as_of}] mechanical HTF gate: htf_align=NONE, skipping (no Claude call)")
            continue

        cycles_evaluated += 1

        try:
            decision = decide_fn(snap, state, cfg)
        except Exception as exc:
            log(f"[{as_of}] analysis failed, skipping: {exc}")
            continue

        ok, reason = bot.validate_decision(decision, snap, state, cfg)
        if not ok:
            log(f"[{as_of}] rejected: {reason}")
            continue
        if decision["action"] == "NONE":
            continue

        price = snap.ask if decision["action"] == "BUY" else snap.bid
        stop_distance = abs(price - float(decision["sl"]))
        lot = bot.position_size(snap, cfg, stop_distance)
        decision["entry"] = price
        decision["lot"] = lot

        state["last_signal_id"] += 1
        signal_id = state["last_signal_id"]
        bot.register_pending(state, signal_id, decision["setup_type"])
        bot.register_trade_history(state, signal_id, decision)

        value_per_unit = snap.tick_value / snap.tick_size
        result = simulate_outcome(m1, as_of, decision["action"], price, float(decision["sl"]),
                                   float(decision["tp"]), lot, value_per_unit, cfg.time_decay_seconds, trail_usd)

        outcome_record = {"signal_id": str(signal_id), "setup_type": decision["setup_type"],
                           "direction": decision["action"], "profit": result["profit"], "outcome": result["outcome"]}
        bot.resolve_pending(state, [outcome_record])
        bot.update_standdown(state, [outcome_record], m5_dir_now)

        equity += result["profit"]
        open_exit_time = result["exit_time"]

        trades.append({
            "entry_time": as_of, "exit_time": result["exit_time"], "setup_type": decision["setup_type"],
            "direction": decision["action"], "confidence": decision["confidence"],
            "entry": price, "sl": decision["sl"], "tp": decision["tp"], "exit_price": result["exit_price"],
            "reason": result["reason"], "profit": result["profit"], "equity_after": round(equity, 2),
            "self_correction": decision.get("self_correction", ""), "reasoning": decision["reasoning"],
        })
        log(f"[{as_of}] {decision['action']} {decision['setup_type']} conf={decision['confidence']:.0f} "
            f"-> {result['outcome']} ({result['reason']}) P&L={result['profit']:+.2f} equity={equity:.2f}")

    return summarize(trades, start_equity, equity, cycles_evaluated, state["htf_gate_skips"])


def summarize(trades: list[dict], start_equity: float, end_equity: float,
              cycles_evaluated: int, htf_gate_skips: int = 0) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["profit"] >= 0)
    peak, max_dd, eq = start_equity, 0.0, start_equity
    for t in trades:
        eq = t["equity_after"]
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)

    by_setup: dict[str, dict] = {}
    for t in trades:
        rec = by_setup.setdefault(t["setup_type"], {"n": 0, "wins": 0, "pnl": 0.0})
        rec["n"] += 1
        rec["pnl"] = round(rec["pnl"] + t["profit"], 2)
        if t["profit"] >= 0:
            rec["wins"] += 1

    total_bars = cycles_evaluated + htf_gate_skips
    return {
        "cycles_evaluated": cycles_evaluated, "htf_gate_skips": htf_gate_skips,
        "htf_gate_skip_rate_pct": round(htf_gate_skips / total_bars * 100.0, 1) if total_bars else 0.0,
        "trades": n, "wins": wins, "losses": n - wins,
        "win_rate_pct": round(wins / n * 100.0, 1) if n else 0.0,
        "start_equity": start_equity, "end_equity": round(end_equity, 2),
        "total_pnl": round(end_equity - start_equity, 2), "max_drawdown": round(max_dd, 2),
        "by_setup_type": by_setup, "trade_log": trades,
    }


# --------------------------------------------------------------------------
# Selftest - offline, synthetic data, no API key or real data file needed
# --------------------------------------------------------------------------

def _synthetic_m1(hours: int = 48, start="2026-09-14 00:00:00", trend: float = 0.02, seed: int = 5) -> pd.DataFrame:
    import numpy as np
    rng = np.random.default_rng(seed)
    n = hours * 60
    idx = pd.date_range(start=start, periods=n, freq="1min")
    closes = 2340.0 + np.cumsum(trend + rng.normal(0.0, 0.08, n))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    noise = np.abs(rng.normal(0.0, 0.05, n))
    highs = np.maximum(opens, closes) + noise
    lows = np.minimum(opens, closes) - noise
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def selftest() -> None:
    print("backtest_xtr selftest")

    # --- resample_ohlc: verify OHLC aggregation against a hand-built case ---
    tiny = pd.DataFrame(
        {"open": [1, 2, 3, 4, 5], "high": [1.5, 2.5, 3.5, 4.5, 5.5],
         "low": [0.5, 1.5, 2.5, 3.5, 4.5], "close": [1.2, 2.2, 3.2, 4.2, 5.2]},
        index=pd.date_range("2026-01-01 00:00", periods=5, freq="1min"),
    )
    r = resample_ohlc(tiny, 5)
    assert len(r) == 1
    row = r.iloc[0]
    assert row["open"] == 1 and row["close"] == 5.2 and row["high"] == 5.5 and row["low"] == 0.5
    print("  resample_ohlc aggregates OHLC correctly: OK")

    # --- closed_bars_as_of: the exact boundary case that motivated the helper ---
    m15 = pd.DataFrame({"open": range(4), "high": range(4), "low": range(4), "close": range(4)},
                        index=pd.to_datetime(["2026-01-01 13:00", "2026-01-01 13:15",
                                               "2026-01-01 13:30", "2026-01-01 13:45"]))
    as_of_on_boundary = pd.Timestamp("2026-01-01 13:15")   # the 13:00 bar just closed exactly now
    got = closed_bars_as_of(m15, 15, as_of_on_boundary, 5)
    assert list(got.index) == [pd.Timestamp("2026-01-01 13:00")], \
        f"must include the bar that JUST closed, not the one that just started: {list(got.index)}"
    as_of_mid_bar = pd.Timestamp("2026-01-01 13:05")   # the 13:00 bar is still forming
    got2 = closed_bars_as_of(m15, 15, as_of_mid_bar, 5)
    assert got2.empty, f"the still-forming 13:00 bar must not be visible yet: {list(got2.index)}"
    print("  closed_bars_as_of respects bar-close boundaries (no look-ahead): OK")

    # --- simulate_outcome: TP, SL, trailing-stop-beats-raw-SL, and time-decay expiry ---
    m1 = pd.DataFrame(
        {"open": [100.0] * 5, "high": [100.2, 101.0, 102.0, 101.5, 101.0],
         "low": [99.8, 99.5, 100.5, 100.0, 99.9], "close": [100.1, 100.8, 101.8, 100.8, 100.5]},
        index=pd.date_range("2026-01-01 10:01", periods=5, freq="1min"),
    )
    entry_time = pd.Timestamp("2026-01-01 10:00")
    tp_hit = simulate_outcome(m1, entry_time, "BUY", 100.0, 99.0, 102.0, 0.01, 100.0, 6000)
    assert tp_hit["reason"] == "TP" and tp_hit["outcome"] == "WIN"
    print(f"  simulate_outcome hits TP: OK ({tp_hit})")

    sl_only = pd.DataFrame(
        {"open": [100.0], "high": [100.1], "low": [98.5], "close": [98.6]},
        index=pd.date_range("2026-01-01 10:01", periods=1, freq="1min"),
    )
    sl_hit = simulate_outcome(sl_only, entry_time, "BUY", 100.0, 99.0, 105.0, 0.01, 100.0, 6000)
    assert sl_hit["reason"] == "SL" and sl_hit["outcome"] == "LOSS"
    print(f"  simulate_outcome hits SL: OK ({sl_hit})")

    # bar1 runs up (trailing $4 -> 4.0 price-distance at lot=0.01, value_per_unit=100), setting the
    # stop to 100.0 without touching it that same bar (low stays above it); bar2 pulls back and is
    # caught by the now-tighter trailed stop (100.5) - a genuine two-bar run-up-then-pullback, not an
    # artifact of the same-bar trail-then-check simplification
    trail_path = pd.DataFrame(
        {"open": [100.0, 103.5], "high": [104.0, 104.5], "low": [101.5, 100.2], "close": [103.5, 100.5]},
        index=pd.date_range("2026-01-01 10:01", periods=2, freq="1min"),
    )
    trailed = simulate_outcome(trail_path, entry_time, "BUY", 100.0, 95.0, 120.0, 0.01, 100.0, 6000, trail_usd=4.0)
    assert trailed["reason"] == "TRAIL" and trailed["exit_price"] == 100.5 and trailed["exit_time"] == trail_path.index[1]
    print(f"  simulate_outcome exits at a trailed stop set on an earlier bar, not the original SL: OK ({trailed})")

    flat_path = pd.DataFrame(
        {"open": [100.0], "high": [100.3], "low": [99.8], "close": [100.1]},
        index=pd.date_range("2026-01-01 10:01", periods=1, freq="1min"),
    )
    expired = simulate_outcome(flat_path, entry_time, "BUY", 100.0, 95.0, 120.0, 0.01, 100.0, 60)
    assert expired["reason"] == "EXPIRED"
    print(f"  simulate_outcome expires via time-decay when neither level is touched: OK ({expired})")

    # --- build_snapshot_at produces a real, usable ChartSnapshot ---
    m1_syn = _synthetic_m1(hours=30)
    m5 = resample_ohlc(m1_syn, 5)
    m15 = resample_ohlc(m1_syn, 15)
    h1 = resample_ohlc(m1_syn, 60)
    m5_ind = compute_indicator_series(m5, full=True)
    m15_ind = compute_indicator_series(m15)
    h1_ind = compute_indicator_series(h1)
    as_of = m5.index[200] + timedelta(minutes=5)
    snap = build_snapshot_at(as_of, m1_syn, m5, m15, h1, m5_ind, m15_ind, h1_ind, "XAUUSD", 5000.0, 0.25)
    assert snap is not None
    assert bot.direction_for(snap.ind["M5"]) in ("BULLISH", "BEARISH", "MIXED")
    assert len(snap.bars["M1"]) > 0 and "time" in snap.bars["M1"].columns
    print(f"  build_snapshot_at produces a valid ChartSnapshot: OK (M5 dir={bot.direction_for(snap.ind['M5'])})")

    # --- end-to-end with the stub decider - proves the whole loop runs and
    #     that state (standdown/trade_history) evolves, without any network call ---
    m1_trend = _synthetic_m1(hours=60, trend=0.05, seed=11)
    cfg = bot.BotConfig(
        data_file=Path("unused"), signal_file=Path("unused"), ack_file=Path("unused"),
        outcome_file=Path("unused"), state_file=Path("unused"),
        risk_percent=2.0, fallback_equity=5000.0, min_confidence=50.0,
        min_atr_mult=0.1, max_atr_mult=5.0, max_concurrent_signals=1, time_decay_seconds=600,
    )
    report = run_backtest(m1_trend, cfg, "XAUUSD", start_equity=5000.0, warmup_bars=250,
                           decide_fn=stub_decision, max_cycles=15)
    assert report["cycles_evaluated"] > 0
    assert set(report) >= {"trades", "win_rate_pct", "total_pnl", "max_drawdown", "by_setup_type", "trade_log",
                            "htf_gate_skips", "htf_gate_skip_rate_pct"}
    assert report["trades"] == len(report["trade_log"])
    if report["trades"] > 0:
        assert all(t["reasoning"] == "stub decider - not Claude, no judgment applied" for t in report["trade_log"])
    print(f"  run_backtest end-to-end with the stub decider: OK "
          f"(cycles={report['cycles_evaluated']} htf_gate_skips={report['htf_gate_skips']} "
          f"trades={report['trades']} pnl={report['total_pnl']})")

    # the HTF gate (on by default, cfg.require_htf_gate) must actually reduce reported
    # Claude-call cycles relative to running with it off, for the same underlying data
    nogate_cfg = bot.BotConfig(
        data_file=Path("unused"), signal_file=Path("unused"), ack_file=Path("unused"),
        outcome_file=Path("unused"), state_file=Path("unused"),
        risk_percent=2.0, fallback_equity=5000.0, min_confidence=50.0,
        min_atr_mult=0.1, max_atr_mult=5.0, max_concurrent_signals=1, time_decay_seconds=600,
        require_htf_gate=False,
    )
    nogate_report = run_backtest(m1_trend, nogate_cfg, "XAUUSD", start_equity=5000.0, warmup_bars=250,
                                  decide_fn=stub_decision, max_cycles=None)
    gated_report_same_window = run_backtest(m1_trend, cfg, "XAUUSD", start_equity=5000.0, warmup_bars=250,
                                             decide_fn=stub_decision, max_cycles=None)
    assert gated_report_same_window["htf_gate_skips"] > 0
    assert nogate_report["htf_gate_skips"] == 0
    assert (gated_report_same_window["cycles_evaluated"] + gated_report_same_window["htf_gate_skips"]
            == nogate_report["cycles_evaluated"])
    print(f"  require_htf_gate measurably cuts Claude-call cycles vs. --no-htf-gate: OK "
          f"(gated {gated_report_same_window['cycles_evaluated']} calls + "
          f"{gated_report_same_window['htf_gate_skips']} skipped, "
          f"vs {nogate_report['cycles_evaluated']} calls with the gate off)")

    # --- run_backtest wired to the REAL bot.analyze_with_claude path, with only the
    #     network call itself mocked - proves the harness calls production code, not a copy ---
    from unittest.mock import patch
    fake_reply = json.dumps({
        "action": "NONE", "setup_type": None, "sl": None, "tp": None,
        "confidence": 0, "self_correction": "nothing notable", "reasoning": "mocked reply",
    })
    with patch("claude_signal_bot.call_claude_analysis", return_value=fake_reply):
        real_path_report = run_backtest(m1_trend, cfg, "XAUUSD", start_equity=5000.0,
                                         warmup_bars=250, decide_fn=None, max_cycles=3)
    assert real_path_report["cycles_evaluated"] == 3
    assert real_path_report["trades"] == 0   # the mocked reply always says NONE
    print("  run_backtest's default decide_fn is the real bot.analyze_with_claude: OK")

    print("ALL SELFTESTS PASSED")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true", help="run offline harness checks and exit")
    parser.add_argument("--csv", help="M1 OHLC CSV: time,open,high,low,close (ascending)")
    parser.add_argument("--stub", action="store_true",
                         help="use the content-blind stub decider instead of Claude - "
                              "harness verification only, NOT a strategy backtest")
    parser.add_argument("--api-key", default=None, help="defaults to $ANTHROPIC_API_KEY")
    parser.add_argument("--model", default=bot.DEFAULT_MODEL)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--start-equity", type=float, default=5000.0)
    parser.add_argument("--risk-percent", type=float, default=2.0)
    parser.add_argument("--min-confidence", type=float, default=60.0)
    parser.add_argument("--min-atr-mult", type=float, default=0.25)
    parser.add_argument("--max-atr-mult", type=float, default=3.0)
    parser.add_argument("--max-concurrent-signals", type=int, default=1)
    parser.add_argument("--no-htf-gate", action="store_true",
                         help="disable the mechanical 2-of-3 HTF pre-filter (on by default) and call "
                              "Claude on every cycle - costs more API calls, but doesn't skip cycles a "
                              "contrarian 4.1/4.3 setup could fire on; see CLAUDE_SIGNAL_PIPELINE.md")
    parser.add_argument("--time-decay-seconds", type=float, default=600.0)
    parser.add_argument("--trail-usd", type=float, default=TRAIL_USD_DEFAULT)
    parser.add_argument("--spread", type=float, default=0.25,
                         help="fixed synthetic spread in price units (e.g. 0.25 = $0.25 for XAUUSD)")
    parser.add_argument("--warmup-bars", type=int, default=250, help="M5 bars skipped for indicator warm-up")
    parser.add_argument("--max-cycles", type=int, default=None,
                         help="cap the number of decision calls (cost control for real Claude runs)")
    parser.add_argument("--out", default=None, help="write the trade log to this CSV path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        selftest()
        return 0

    if not args.csv:
        print("--csv is required unless --selftest is passed", file=sys.stderr)
        return 1

    api_key = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not args.stub and not api_key:
        print("No ANTHROPIC_API_KEY / --api-key, and --stub was not passed - there is nothing to call.\n"
              "Pass --stub to verify the harness mechanics only (NOT a real backtest, see the module\n"
              "docstring), or provide a real key to actually backtest Claude's decisions against this data.",
              file=sys.stderr)
        return 1

    m1 = load_m1_csv(Path(args.csv))
    cfg = bot.BotConfig(
        data_file=Path("unused"), signal_file=Path("unused"), ack_file=Path("unused"),
        outcome_file=Path("unused"), state_file=Path("unused"),
        model=args.model, api_key=api_key, risk_percent=args.risk_percent,
        fallback_equity=args.start_equity, time_decay_seconds=args.time_decay_seconds,
        min_confidence=args.min_confidence, min_atr_mult=args.min_atr_mult, max_atr_mult=args.max_atr_mult,
        max_concurrent_signals=args.max_concurrent_signals, require_htf_gate=not args.no_htf_gate,
    )
    decide_fn = stub_decision if args.stub else None
    log = print if args.verbose else (lambda *a, **k: None)

    if args.stub:
        print("*** --stub: using a content-blind mechanical stand-in, NOT Claude. "
              "These results are not a strategy backtest. ***")

    report = run_backtest(m1, cfg, args.symbol, args.start_equity, args.warmup_bars,
                           decide_fn, args.max_cycles, args.spread, args.trail_usd, log)

    print(json.dumps({k: v for k, v in report.items() if k != "trade_log"}, indent=2, default=str))
    if args.out:
        pd.DataFrame(report["trade_log"]).to_csv(args.out, index=False)
        print(f"trade log written to {args.out} ({len(report['trade_log'])} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

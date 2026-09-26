"""
Offline self-test - runs anywhere, no MT5 terminal, no MetaTrader5 package,
no Anthropic API key and no network needed. Mirrors the testing philosophy
of the other self-tests: every piece of decision logic is pure or
dependency-injected, so it's exercised here with synthetic data and fakes.

Covers:
  * indicator math (EMA/RSI/MACD/ATR/Bollinger/ADX-DI/Stochastic) sanity,
    including macd_hist_shape()'s peak/decline detection (the extended-entry
    deceleration check in claude_advisor.SYSTEM_PROMPT)
  * SMC liquidity-sweep detection and premium/discount zoning
  * candle pattern detection
  * market structure (swing points, HH/HL/LH/LL, BOS/CHoCH), order blocks,
    fair value gaps (open and filled), and previous day/week high-low
  * the dollar -> price-distance conversion executor.execute() relies on
  * executor.gate()/execute() gating logic against a fake MT5 gateway,
    including exit_style routing (sl_to_tp1/breakeven_r_decay both send no
    broker TP, fixed_tp sends a real one) and the ValueError an unrecognized
    exit_style raises instead of silently mimicking a known one
  * claude_advisor.get_verdict() wiring against a fake Anthropic client
  * claude_advisor's Claude-API error classification (out of credits,
    bad key, rate limit, overload, network) - built from the real
    anthropic SDK exception classes when the package is installed
  * backtest.HistoricalGateway's no-lookahead guarantee (a higher timeframe
    bar isn't visible until its own CLOSE time, not just its open time),
    both its exit simulations - exit_style="fixed_tp" (arm-then-drop-TP,
    the old design) and exit_style="sl_to_tp1" (lock-SL-then-trail, the
    live default) - and a tiny end-to-end mechanical-mode backtest run
  * telegram_alert.send_alert()'s blank-credentials no-op, successful post,
    and never-raises-on-failure behavior against a fake poster (no network),
    and format_full_conviction_message()'s executed/rejected wording
"""
from __future__ import annotations

import csv
import dataclasses
import types
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import tempfile as _tempfile

# Never touch the real keys.txt: main.main() loads keys on start.
os.environ["GOLDTRADER_KEYS_FILE"] = os.path.join(_tempfile.mkdtemp(prefix="gt_keys_"), "keys.txt")

import backtest
import calibration_report
import claude_advisor
import econ_calendar
import first_run
import executor
import main as main_mod
import market_intel
import ml_advisor
import news_check
import paths
import relay_supervisor
import services
import tactics
import telegram_alert
import xtr_logic
import mt5_gateway as gw
from claude_advisor import ConfluenceLeg, ConfluenceVerdict
from config import AdvisorConfig


FAILED_CHECKS: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    line = f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else "")
    print(line)
    if not ok:
        FAILED_CHECKS.append(line[:2000])
    return ok


def shipped_copy(name: str) -> str:
    """The package's own version of a file you may edit: <name>.new when the
    updater kept your copy, else <name>."""
    new = os.path.join(paths.PACKAGE_ROOT, name + ".new")
    return new if os.path.exists(new) else os.path.join(paths.PACKAGE_ROOT, name)


def is_pristine(name: str) -> bool:
    """True when shipped_copy(name) is exactly what the package ships: the
    .new copy, a development checkout, or the file the last update installed
    and you have not changed. Only then are its DEFAULT values tested - your
    own settings (relay off, other options) never fail a test."""
    import hashlib
    import json
    path = shipped_copy(name)
    if path.endswith(".new") or os.path.isdir(os.path.join(paths.PACKAGE_ROOT, "..", ".git")):
        return True
    try:
        with open(os.path.join(paths.PACKAGE_ROOT, "logs", "update_state.json"), encoding="utf-8") as f:
            shipped = json.load(f).get("shipped", {}).get(name)
        with open(path, "rb") as f:
            return shipped == hashlib.sha256(f.read()).hexdigest()
    except (OSError, ValueError):
        return False

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



def make_trending_df(n: int = 250, start: float = 2300.0, drift: float = 0.6,
                      noise: float = 0.15, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = start + np.cumsum(np.full(n, drift) + rng.normal(0, noise, n))
    opens = np.roll(closes, 1)
    opens[0] = start
    highs = np.maximum(opens, closes) + rng.uniform(0.05, 0.3, n)
    lows = np.minimum(opens, closes) - rng.uniform(0.05, 0.3, n)
    times = pd.date_range("2026-09-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"time": times, "open": opens, "high": highs, "low": lows,
                         "close": closes, "volume": rng.integers(50, 200, n)})


# --------------------------------------------------------------------------- #
# indicators
# --------------------------------------------------------------------------- #
def test_indicators() -> bool:
    print("\n=== 1. indicators ===")
    ok = True

    flat = pd.Series([2350.0] * 50)
    ok &= check("EMA of a flat series equals the flat value",
                abs(market_intel.ema(flat, 20).iloc[-1] - 2350.0) < 1e-9)

    rising = pd.Series(np.linspace(2300, 2400, 60))
    rsi_rising = market_intel.rsi(rising).iloc[-1]
    ok &= check("RSI on a pure uptrend is near 100", rsi_rising > 95, rsi_rising)

    falling = pd.Series(np.linspace(2400, 2300, 60))
    rsi_falling = market_intel.rsi(falling).iloc[-1]
    ok &= check("RSI on a pure downtrend is near 0", rsi_falling < 5, rsi_falling)

    df = make_trending_df()
    macd_line, signal_line, hist = market_intel.macd(df["close"])
    ok &= check("MACD line is positive on a strong uptrend", macd_line.iloc[-1] > 0, macd_line.iloc[-1])

    atr14 = market_intel.atr(df)
    ok &= check("ATR is positive and finite", (atr14.dropna() > 0).all())

    _, _, _, pct_b, bandwidth = market_intel.bollinger(df["close"])
    ok &= check("Bollinger bandwidth is positive", (bandwidth.dropna() > 0).all())

    adx, plus_di, minus_di = market_intel.adx_di(df)
    ok &= check("On a strong uptrend +DI ends above -DI", plus_di.iloc[-1] > minus_di.iloc[-1],
                f"+DI={plus_di.iloc[-1]:.1f} -DI={minus_di.iloc[-1]:.1f}")
    ok &= check("ADX stays within 0-100", (adx.between(0, 100)).all())

    k, d = market_intel.stochastic(df)
    ok &= check("Stochastic %K/%D stay within 0-100", k.between(0, 100).all() and d.between(0, 100).all())

    # macd_hist_shape: peak detection for the extended-entry deceleration
    # check (claude_advisor.SYSTEM_PROMPT) - peak is read in the direction
    # the histogram already leans (max for bullish, min for bearish).
    bull_rising = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    ok &= check("bullish histogram still rising bar-over-bar is not declining_from_peak",
                not market_intel.macd_hist_shape(bull_rising)["declining_from_peak"])

    bull_peaked = pd.Series([0.1, 0.3, 0.5, 0.6, 0.5, 0.4])
    ok &= check("bullish histogram that ticked down from its window peak is declining_from_peak",
                market_intel.macd_hist_shape(bull_peaked)["declining_from_peak"])

    bear_accelerating = pd.Series([-0.1, -0.2, -0.3, -0.4, -0.5, -0.6])
    ok &= check("bearish histogram still getting more negative is not declining_from_peak",
                not market_intel.macd_hist_shape(bear_accelerating)["declining_from_peak"])

    bear_retreating = pd.Series([-0.1, -0.3, -0.6, -0.7, -0.6, -0.5])
    ok &= check("bearish histogram retreating back toward zero from its trough is declining_from_peak",
                market_intel.macd_hist_shape(bear_retreating)["declining_from_peak"])

    new_peak = pd.Series([0.1, 0.05, 0.2, 0.15, 0.3, 0.35])
    ok &= check("the current bar itself being the window's new peak is not declining_from_peak",
                not market_intel.macd_hist_shape(new_peak)["declining_from_peak"])

    return ok


# --------------------------------------------------------------------------- #
# SMC + price action
# --------------------------------------------------------------------------- #
def test_smc_and_price_action() -> bool:
    print("\n=== 2. SMC structure + price action ===")
    ok = True

    # Flat range bars (2340-2350) as the reference window, then a sweep below
    # the range low that reclaims - should read as a bullish sweep.
    # detect_liquidity_sweep requires len(closed) >= recent_bars + ref_bars + 1,
    # so generate one extra ref bar beyond the ref_bars=n_ref passed below.
    n_ref = 41
    n_ref_bars_generated = n_ref + 1
    times = pd.date_range("2026-09-01", periods=n_ref_bars_generated + 5, freq="15min", tz="UTC")
    ref = pd.DataFrame({
        "time": times[:n_ref_bars_generated],
        "open": [2345.0] * n_ref_bars_generated, "close": [2346.0] * n_ref_bars_generated,
        "high": [2350.0] * n_ref_bars_generated, "low": [2340.0] * n_ref_bars_generated,
        "volume": [100] * n_ref_bars_generated,
    })
    sweep_bars = pd.DataFrame({
        "time": times[n_ref_bars_generated:],
        "open":  [2341.0, 2335.0, 2338.0, 2342.0, 2344.0],
        "close": [2338.0, 2336.0, 2343.0, 2344.0, 2346.0],
        "high":  [2342.0, 2338.0, 2344.0, 2345.0, 2347.0],
        "low":   [2337.0, 2330.0, 2334.0, 2341.0, 2343.0],   # bar 1 pierces well below 2340
        "volume": [100] * 5,
    })
    closed = pd.concat([ref, sweep_bars], ignore_index=True)
    sweep = market_intel.detect_liquidity_sweep(closed, recent_bars=5, ref_bars=n_ref, min_pierce_price=0.5)
    ok &= check("a stop-hunt below range low that reclaims reads as a bullish sweep",
                sweep["swept"] and sweep["direction"] == "buy", sweep)

    no_sweep = market_intel.detect_liquidity_sweep(ref, recent_bars=5, ref_bars=n_ref - 5, min_pierce_price=0.5)
    ok &= check("a flat range with no pierce reports no sweep", not no_sweep["swept"], no_sweep)

    zone_df = pd.DataFrame({
        "open": [2350.0] * 19 + [2305.0], "close": [2350.0] * 19 + [2310.0],
        "high": [2400.0] * 19 + [2312.0], "low": [2300.0] * 19 + [2305.0],
    })
    zone_low = market_intel.premium_discount_zone(zone_df, lookback=20)
    ok &= check("a close near the bottom of its recent range reads as discount",
                zone_low["zone"] == "discount", zone_low)
    zone_high = market_intel.premium_discount_zone(
        pd.DataFrame({"open": [2350.0] * 19 + [2395.0], "close": [2350.0] * 19 + [2390.0],
                      "high": [2400.0] * 19 + [2392.0], "low": [2300.0] * 19 + [2388.0]}),
        lookback=20)
    ok &= check("a close near the top of its recent range reads as premium",
                zone_high["zone"] == "premium", zone_high)

    bullish_engulf = pd.DataFrame({
        "open":  [2350.0, 2348.0], "close": [2348.0, 2352.0],
        "high":  [2351.0, 2353.0], "low":   [2347.0, 2347.0],
    })
    features = market_intel.candle_features(bullish_engulf)
    ok &= check("a clean bullish engulfing candle is flagged", features["bullish_engulfing"], features)

    return ok


def triangle_wave(pivots: list, bars_per_leg: int) -> pd.DataFrame:
    """Builds a clean zigzag DataFrame from pivot prices alternating
    low/high/low/high/... by linearly interpolating `bars_per_leg` bars
    between each pair - used to construct deterministic swing points for
    testing market_structure()/find_swing_points() without hand-typing every
    OHLC value."""
    path = []
    for i in range(len(pivots) - 1):
        seg = np.linspace(pivots[i], pivots[i + 1], bars_per_leg + 1)
        path.extend(seg[:-1].tolist())
    path.append(pivots[-1])
    path = np.array(path)
    return pd.DataFrame({"open": path, "close": path, "high": path + 1.0, "low": path - 1.0})


def test_market_structure_ob_fvg_levels() -> bool:
    print("\n=== 3. market structure (BOS/CHoCH), order blocks, FVGs, daily/weekly levels ===")
    ok = True

    # Ascending staircase of swing points (padding low/high at each end so
    # the interior pivots get confirmed by find_swing_points' order=2).
    pivots = [95, 140, 110, 150, 120, 160, 130]
    df = triangle_wave(pivots, bars_per_leg=5)
    structure = market_intel.market_structure(df, order=2)
    ok &= check("an ascending HH/HL staircase reads as a bullish trend",
                structure["trend"] == "bullish", structure)
    ok &= check("the last swing high/low are labeled HH/HL",
                structure["last_swing_high"]["label"] == "HH"
                and structure["last_swing_low"]["label"] == "HL", structure)
    ok &= check("no structural break yet - last_event is None",
                structure["last_event"] is None, structure)

    # Extend with a leg that breaks back below the last confirmed swing low
    # while the trend was bullish -> a bearish Change of Character.
    break_leg = pd.DataFrame({
        "open": [125.0, 118.0, 105.0], "close": [125.0, 118.0, 105.0],
        "high": [126.0, 119.0, 106.0], "low": [124.0, 117.0, 104.0],
    })
    df_choch = pd.concat([df, break_leg], ignore_index=True)
    choch = market_intel.market_structure(df_choch, order=2)
    ok &= check("closing below the last confirmed swing low (bullish trend) reads as a bearish CHoCH",
                choch["last_event"] == {"type": "CHoCH", "direction": "bearish",
                                        "broke_level": structure["last_swing_low"]["price"]}, choch)

    # Order block: a quiet range, one modest bearish candle, then an
    # unmistakably oversized bullish displacement candle.
    quiet = pd.DataFrame({"open": [2340.0] * 20, "close": [2340.5] * 20,
                          "high": [2341.0] * 20, "low": [2339.5] * 20})
    bearish_candle = pd.DataFrame({"open": [2340.5], "close": [2339.6], "high": [2341.0], "low": [2339.4]})
    displacement = pd.DataFrame({"open": [2339.6], "close": [2355.0], "high": [2355.5], "low": [2339.5]})
    after = pd.DataFrame({"open": [2355.0] * 3, "close": [2354.0, 2356.0, 2358.0],
                          "high": [2356.0, 2357.0, 2359.0], "low": [2353.0, 2355.0, 2357.0]})
    ob_df = pd.concat([quiet, bearish_candle, displacement, after], ignore_index=True)
    obs = market_intel.detect_order_blocks(ob_df, lookback=10, displacement_atr_mult=1.5)
    ok &= check("the last opposite candle before a real displacement move is the bullish order block",
                obs["bullish_order_block"] is not None
                and obs["bullish_order_block"]["high"] == 2341.0
                and obs["bullish_order_block"]["low"] == 2339.4, obs)
    ok &= check("no bearish displacement occurred, so no bearish order block is reported",
                obs["bearish_order_block"] is None, obs)

    # Fair value gap: candle[i-2].high < candle[i].low leaves an unfilled gap.
    fvg_df = pd.DataFrame({
        "open":  [2340.0, 2341.0, 2352.0, 2353.0, 2354.0],
        "close": [2341.0, 2340.5, 2353.0, 2354.0, 2355.0],
        "high":  [2342.0, 2341.5, 2354.0, 2355.0, 2356.0],
        "low":   [2339.0, 2340.0, 2352.5, 2353.0, 2354.0],
    })
    gaps = market_intel.detect_fair_value_gaps(fvg_df, lookback=10)
    ok &= check("an unfilled 3-candle imbalance is detected as a bullish FVG",
                any(g["direction"] == "bullish" and g["top"] == 2352.5 and g["bottom"] == 2342.0
                    for g in gaps), gaps)

    filled_df = pd.concat([fvg_df, pd.DataFrame({
        "open": [2354.0], "close": [2340.0], "high": [2354.0], "low": [2339.0],
    })], ignore_index=True)
    gaps_after_fill = market_intel.detect_fair_value_gaps(filled_df, lookback=10)
    ok &= check("a gap that price later trades back through is no longer reported as open",
                gaps_after_fill == [], gaps_after_fill)

    # Daily/weekly levels: the LAST bar of each is the still-forming one and
    # must be dropped, leaving the previous complete day/week.
    class FakeDWGateway:
        def get_bars(self, symbol, timeframe_name, count):
            if timeframe_name == "D1":
                return pd.DataFrame({"high": [2400.0, 2410.0, 2420.0], "low": [2390.0, 2395.0, 2405.0]})
            if timeframe_name == "W1":
                return pd.DataFrame({"high": [2450.0, 2460.0], "low": [2380.0, 2400.0]})
            raise ValueError(timeframe_name)

    levels = market_intel.daily_weekly_levels(FakeDWGateway(), "XAUUSD")
    ok &= check("daily/weekly levels use the last COMPLETE day/week, not the still-forming one",
                levels == {"prev_day_high": 2410.0, "prev_day_low": 2395.0,
                          "prev_week_high": 2450.0, "prev_week_low": 2380.0}, levels)

    return ok


# --------------------------------------------------------------------------- #
# dollar -> price distance
# --------------------------------------------------------------------------- #
def test_price_distance() -> bool:
    print("\n=== 4. dollar -> price distance conversion ===")
    ok = True
    # Same SymbolSpec shape used in the Telegram copier's tests
    # (tick_value=1.0, tick_size=0.01 -> $1 of P&L per 0.01 price move per 1.0 lot).
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0,
                         spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                         tick_value=1.0, tick_size=0.01)
    dist = gw.price_distance_for_dollars(spec, dollars=6.0, lots=0.01)
    ok &= check("$6 at 0.01 lots is a 6.0 price-unit distance at these tick values",
                abs(dist - 6.0) < 1e-9, dist)
    dist_half_lot = gw.price_distance_for_dollars(spec, dollars=6.0, lots=0.02)
    ok &= check("doubling lot size halves the price distance for the same dollar risk",
                abs(dist_half_lot - 3.0) < 1e-9, dist_half_lot)
    return ok


# --------------------------------------------------------------------------- #
# executor gating (fake MT5 gateway - no real connection)
# --------------------------------------------------------------------------- #
class FakeTick:
    def __init__(self, bid, ask):
        self.bid, self.ask = bid, ask


class FakeResult:
    def __init__(self, retcode=10009, order=12345, price=2350.0):
        self.retcode, self.order, self.price = retcode, order, price


TEST_NOW = datetime(2026, 9, 23, 14, 0, tzinfo=timezone.utc)


class FakeGateway:
    """Implements exactly the mt5_gateway functions executor.py calls."""
    SymbolSpec = gw.SymbolSpec

    def __init__(self, same_dir_open: int = 0, bid: float = 2350.0, ask: float = 2350.2,
                 equity: float = 10000.0, bars_df=None, now=None, positions=None, pending=None,
                 result=None):
        self.same_dir_open = same_dir_open
        self.bid, self.ask = bid, ask
        self.orders_sent = []
        self.last_additional_magics = None
        self.equity = equity
        self.bars_df = bars_df
        self._now = now
        self.positions = positions or []
        self.pending = pending or []
        self.result = result

    def count_same_direction(self, symbol, magic, direction, additional_magics=()):
        self.last_additional_magics = additional_magics
        return self.same_dir_open

    def open_positions(self, symbol, magic):
        return self.positions

    def symbol_positions(self, symbol):
        return self.positions

    def pending_orders(self, symbol):
        return self.pending

    def account_equity(self):
        return self.equity

    def now(self):
        # A fixed Wednesday 14:00 UTC (10:00 New York), inside the default trading hours:
        # a test's outcome must never depend on the time of day it runs.
        return self._now or TEST_NOW

    def get_bars(self, symbol, timeframe_name, count):
        return self.bars_df.tail(count).reset_index(drop=True)

    def get_tick(self, symbol):
        return FakeTick(self.bid, self.ask)

    def price_distance_for_dollars(self, spec, dollars, lots):
        return gw.price_distance_for_dollars(spec, dollars, lots)

    def place_market_order(self, spec, direction, lots, sl_price, tp_price, magic, comment,
                            deviation_points, dry_run):
        self.orders_sent.append((direction, lots, sl_price, tp_price))
        if dry_run:
            return None
        return self.result or FakeResult()


def make_verdict(direction="buy", confluence_count=3, conviction="full") -> ConfluenceVerdict:
    leg = ConfluenceLeg(direction=direction if direction != "none" else "neutral",
                        passes=True, confirmed=True, note="test")
    return ConfluenceVerdict(trend=leg, momentum=leg, strength=leg,
                             confluence_count=confluence_count, direction=direction,
                             conviction=conviction, smc_alignment="aligned",
                             reasoning="synthetic test verdict")


def test_executor() -> bool:
    print("\n=== 5. executor gating ===")
    ok = True
    cfg = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs", use_risk_percent=False)
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0,
                         spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                         tick_value=1.0, tick_size=0.01)

    fg = FakeGateway(same_dir_open=0)
    d = executor.execute(fg, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("a 3/3 full-conviction buy is executed", d.executed, d.reject_reason)
    ok &= check("the fake order was actually sent with the right lot size (use_risk_percent=False here)",
                fg.orders_sent and fg.orders_sent[0][1] == cfg.fixed_lot, fg.orders_sent)
    ok &= check("exit_style=sl_to_tp1 (the default) sends tp=0.0 - no broker take-profit at all",
                fg.orders_sent[0][3] == 0.0, fg.orders_sent)

    cfg_fixed_tp = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs", exit_style="fixed_tp")
    fg_fixed = FakeGateway(same_dir_open=0)
    executor.execute(fg_fixed, cfg_fixed_tp, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("exit_style=fixed_tp sends a real non-zero broker take-profit",
                fg_fixed.orders_sent and fg_fixed.orders_sent[0][3] > 0, fg_fixed.orders_sent)

    cfg_breakeven = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                  exit_style="breakeven_r_decay")
    fg_breakeven = FakeGateway(same_dir_open=0)
    executor.execute(fg_breakeven, cfg_breakeven, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("exit_style=breakeven_r_decay also sends tp=0.0 - no broker take-profit, same as "
                "sl_to_tp1 (the live MQL5 EA's own SL is the only exit mechanism)",
                fg_breakeven.orders_sent and fg_breakeven.orders_sent[0][3] == 0.0, fg_breakeven.orders_sent)

    cfg_bad_style = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                  exit_style="not_a_real_style")
    fg_bad = FakeGateway(same_dir_open=0)
    raised_bad_style = None
    try:
        executor.execute(fg_bad, cfg_bad_style, make_verdict("buy", 3, "full"), spec, trades_today=0)
    except ValueError as exc:
        raised_bad_style = exc
    ok &= check("an unrecognized exit_style raises ValueError instead of silently behaving like "
                "sl_to_tp1", raised_bad_style is not None, raised_bad_style)

    with open(executor._csv_path(cfg, "trades.csv")) as f:
        trade_rows = list(csv.DictReader(f))
    ok &= check("trades.csv tags every row with source=Claude_Sig",
                trade_rows and all(r["source"] == "Claude_Sig" for r in trade_rows), trade_rows)
    with open(executor._csv_path(cfg, "decisions.csv")) as f:
        decision_rows = list(csv.DictReader(f))
    ok &= check("decisions.csv tags every row with source=Claude_Sig",
                decision_rows and all(r["source"] == "Claude_Sig" for r in decision_rows), decision_rows)

    fg2 = FakeGateway(same_dir_open=0)
    d2 = executor.execute(fg2, cfg, make_verdict("buy", 2, "partial"), spec, trades_today=0)
    ok &= check("a merely 'partial' conviction is rejected, not executed",
                not d2.executed and "conviction" in d2.reject_reason, d2.reject_reason)

    fg3 = FakeGateway(same_dir_open=0)
    d3 = executor.execute(fg3, cfg, make_verdict("buy", 1, "full"), spec, trades_today=0)
    ok &= check("only 1/3 confluences agreeing is rejected even at full conviction",
                not d3.executed and "confluence_count" in d3.reject_reason, d3.reject_reason)

    fg4 = FakeGateway(same_dir_open=5)
    d4 = executor.execute(fg4, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("the same-direction position cap (5) blocks a 6th buy",
                not d4.executed and "already 5 open" in d4.reject_reason, d4.reject_reason)
    ok &= check("with no shared_cap_magic_numbers configured, gate() asks the gateway to count "
                "only this system's own magic (an empty additional_magics tuple)",
                fg4.last_additional_magics == [], fg4.last_additional_magics)

    cfg_shared = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                               shared_cap_magic_numbers=[20260922])
    fg4b = FakeGateway(same_dir_open=0)
    executor.execute(fg4b, cfg_shared, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("shared_cap_magic_numbers is passed straight through to the gateway's "
                "count_same_direction() call, so a unified EA's other-source magic gets folded "
                "into the same cap",
                fg4b.last_additional_magics == [20260922], fg4b.last_additional_magics)

    fg5 = FakeGateway(same_dir_open=0)
    d5 = executor.execute(fg5, cfg, make_verdict("none", 0, "none"), spec, trades_today=0)
    ok &= check("direction='none' is rejected outright",
                not d5.executed and "no actionable direction" in d5.reject_reason, d5.reject_reason)

    cfg_capped = AdvisorConfig(dry_run=True, max_trades_per_day=2, log_dir="/tmp/claudesmc_selftest_logs")
    fg6 = FakeGateway(same_dir_open=0)
    d6 = executor.execute(fg6, cfg_capped, make_verdict("sell", 3, "full"), spec, trades_today=2)
    ok &= check("max_trades_per_day blocks a trade once the cap is reached",
                not d6.executed and "max trades/day" in d6.reject_reason, d6.reject_reason)

    fg7 = FakeGateway(same_dir_open=0)
    d7 = executor.execute(fg7, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0,
                          daily_block_reason="daily loss circuit breaker triggered")
    ok &= check("a non-empty daily_block_reason blocks the trade before any other gate runs",
                not d7.executed and d7.reject_reason == "daily loss circuit breaker triggered",
                d7.reject_reason)
    ok &= check("daily_block_reason short-circuits before the gateway is ever asked for "
                "same-direction positions", fg7.last_additional_magics is None)

    # --- news/calendar blackout windows (in_news_blackout()) ---
    cfg_blackout = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                 news_blackout_windows=[("2026-10-03T12:25:00Z", "2026-10-03T12:40:00Z")])
    inside = executor.in_news_blackout(
        cfg_blackout, now=datetime(2026, 10, 3, 12, 30, tzinfo=timezone.utc))
    ok &= check("a time inside the configured window is reported as a blackout",
                "news blackout" in inside and "2026-10-03T12:25:00Z" in inside, inside)
    before = executor.in_news_blackout(
        cfg_blackout, now=datetime(2026, 10, 3, 12, 24, 59, tzinfo=timezone.utc))
    ok &= check("one second before the window starts is NOT a blackout", before == "", before)
    after = executor.in_news_blackout(
        cfg_blackout, now=datetime(2026, 10, 3, 12, 40, 1, tzinfo=timezone.utc))
    ok &= check("one second after the window ends is NOT a blackout", after == "", after)
    ok &= check("empty news_blackout_windows (the default) never blocks anything",
                executor.in_news_blackout(AdvisorConfig()) == "")

    cfg_blackout_now = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                     news_blackout_windows=[("2020-01-01T00:00:00Z", "2030-01-01T00:00:00Z")])
    fg9 = FakeGateway(same_dir_open=0)
    d9 = executor.execute(fg9, cfg_blackout_now, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("gate() actually rejects a trade evaluated inside a currently-active blackout window",
                not d9.executed and "news blackout" in d9.reject_reason, d9.reject_reason)

    # gate() must source "now" from gateway.now() - NOT the real wall clock
    # directly - so a backtest run (HistoricalGateway.now() == the
    # simulated replay clock) judges news_blackout_windows against the bar
    # being evaluated, not whatever real date the test/backtest happens to
    # run on. A window that only matches a historical date the real clock
    # is nowhere near proves gate() actually asked the gateway, not
    # datetime.now(), for "now".
    cfg_blackout_historical = AdvisorConfig(
        dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
        news_blackout_windows=[("2020-06-15T00:00:00Z", "2020-06-15T23:59:59Z")])
    fg_real_clock = FakeGateway(same_dir_open=0)
    d_real_clock = executor.execute(fg_real_clock, cfg_blackout_historical, make_verdict("buy", 3, "full"),
                                    spec, trades_today=0)
    ok &= check("a historical-only blackout window does NOT block a trade evaluated today",
                d_real_clock.executed, d_real_clock.reject_reason)

    fg_sim_clock = FakeGateway(same_dir_open=0, now=datetime(2020, 6, 15, 12, 0, tzinfo=timezone.utc))
    d_sim_clock = executor.execute(fg_sim_clock, cfg_blackout_historical, make_verdict("buy", 3, "full"),
                                   spec, trades_today=0)
    ok &= check("gate() sources 'now' from gateway.now() - a gateway simulating a historical clock "
                "inside the same window DOES get blocked, proving gate() didn't just use the real "
                "wall clock",
                not d_sim_clock.executed and "news blackout" in d_sim_clock.reject_reason,
                d_sim_clock.reject_reason)

    # --- ATR-adaptive initial stop-loss (sl_mode="atr") ---
    atr_bars = make_trending_df(n=40, start=2350.0, drift=0.0, noise=0.5, seed=3)
    cfg_atr = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                            sl_mode="atr", sl_atr_mult=1.5, sl_atr_period=14,
                            sl_dollars_min=3.0, sl_dollars_max=15.0)
    atr_dist = executor.atr_sl_distance(FakeGateway(bars_df=atr_bars), cfg_atr, spec)
    ok &= check("atr_sl_distance() returns a positive price distance with enough bar history",
                atr_dist is not None and atr_dist > 0, atr_dist)

    min_dist = gw.price_distance_for_dollars(spec, cfg_atr.sl_dollars_min, cfg_atr.fixed_lot)
    max_dist = gw.price_distance_for_dollars(spec, cfg_atr.sl_dollars_max, cfg_atr.fixed_lot)
    ok &= check("the ATR distance is clamped within [sl_dollars_min, sl_dollars_max]",
                min_dist <= atr_dist <= max_dist, (min_dist, atr_dist, max_dist))

    too_short_bars = make_trending_df(n=5)
    ok &= check("not enough bar history returns None (callers fall back to fixed sl_dollars)",
                executor.atr_sl_distance(FakeGateway(bars_df=too_short_bars), cfg_atr, spec) is None)

    fg10 = FakeGateway(same_dir_open=0, bars_df=atr_bars)
    executor.execute(fg10, cfg_atr, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("execute() actually uses the ATR-derived SL distance under sl_mode='atr'",
                fg10.orders_sent and abs((2350.2 - fg10.orders_sent[0][2]) - atr_dist) < 1e-6,
                (fg10.orders_sent, atr_dist))

    cfg_atr_no_history = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                       sl_mode="atr", sl_dollars=6.0)
    fg11 = FakeGateway(same_dir_open=0, bars_df=too_short_bars)
    executor.execute(fg11, cfg_atr_no_history, make_verdict("buy", 3, "full"), spec, trades_today=0)
    fallback_dist = gw.price_distance_for_dollars(spec, cfg_atr_no_history.sl_dollars,
                                                   cfg_atr_no_history.fixed_lot)
    ok &= check("sl_mode='atr' falls back to the fixed sl_dollars distance when there isn't enough "
                "history yet",
                fg11.orders_sent and abs((2350.2 - fg11.orders_sent[0][2]) - fallback_dist) < 1e-6,
                (fg11.orders_sent, fallback_dist))

    cfg_bad_sl_mode = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                    sl_mode="not_a_real_mode")
    fg12 = FakeGateway(same_dir_open=0, bars_df=atr_bars)
    raised_bad_sl_mode = None
    try:
        executor.execute(fg12, cfg_bad_sl_mode, make_verdict("buy", 3, "full"), spec, trades_today=0)
    except ValueError as exc:
        raised_bad_sl_mode = exc
    ok &= check("an unrecognized sl_mode raises ValueError instead of silently behaving like 'fixed'",
                raised_bad_sl_mode is not None, raised_bad_sl_mode)

    # --- equity-scaled lot sizing (position_size()) ---
    cfg_fixed = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs", use_risk_percent=False)
    ok &= check("use_risk_percent=False always returns fixed_lot",
                executor.position_size(FakeGateway(equity=50000.0), cfg_fixed, spec, sl_dist=1.0)
                == cfg_fixed.fixed_lot)

    cfg_risk = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                             use_risk_percent=True, risk_percent=0.2, sl_dollars=6.0, fixed_lot=0.01)
    # sl_dist for $6 at 0.01 lots, tick_value=1.0/tick_size=0.01 -> sl_dist = 6*0.01/(1.0*0.01) = 6.0
    sl_dist = gw.price_distance_for_dollars(spec, cfg_risk.sl_dollars, cfg_risk.fixed_lot)
    # loss_per_lot = sl_dist/tick_size*tick_value = 6.0/0.01*1.0 = 600; at equity=6000,
    # target=6000*0.2%=12 -> exactly 0.02 lots (a clean step multiple, so flooring to
    # volume_step doesn't obscure the equity->lot relationship being checked below).
    lots_6k = executor.position_size(FakeGateway(equity=6000.0), cfg_risk, spec, sl_dist)
    ok &= check("risk_percent sizes up from a small reference lot at a realistic equity",
                abs(lots_6k - 0.02) < 1e-9, lots_6k)

    lots_60k = executor.position_size(FakeGateway(equity=60000.0), cfg_risk, spec, sl_dist)
    ok &= check("10x the equity produces 10x the lot (risk stays a constant pct of equity)",
                abs(lots_60k - lots_6k * 10) < 1e-9, (lots_60k, lots_6k))

    # Regression: equity=9000 -> exact lots = 9000*0.002/600 = 0.03, but
    # 0.03 is not exactly representable in binary floating point (it's
    # stored as ~0.029999999999999995), so a plain `lots // step` floors
    # it to 0.02 instead of 0.03 - a silent 33% under-risk. The epsilon in
    # position_size() must absorb this.
    lots_9k = executor.position_size(FakeGateway(equity=9000.0), cfg_risk, spec, sl_dist)
    ok &= check("a lot that is an exact step multiple isn't floored down a whole step by float "
                "imprecision (0.03 must not become 0.02)",
                abs(lots_9k - 0.03) < 1e-9, lots_9k)

    cfg_risk_capped = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                    use_risk_percent=True, risk_percent=5.0, max_lot_size=1.0)
    lots_capped = executor.position_size(FakeGateway(equity=1000000.0), cfg_risk_capped, spec, sl_dist)
    ok &= check("max_lot_size caps a risk-sized lot even at a huge equity",
                lots_capped == 1.0, lots_capped)

    lots_degenerate = executor.position_size(FakeGateway(equity=0.0), cfg_risk, spec, sl_dist)
    ok &= check("zero/invalid equity falls back to fixed_lot rather than raising",
                lots_degenerate == cfg_risk.fixed_lot, lots_degenerate)

    # A tiny account: the risk-sized lot would round to less than the
    # broker's 0.01 minimum - clamped UP to volume_min (a warning is
    # logged, but the return value is still the safe, placeable minimum,
    # never zero and never an exception).
    lots_tiny_account = executor.position_size(FakeGateway(equity=100.0), cfg_risk, spec, sl_dist)
    ok &= check("a risk-sized lot below volume_min is clamped up to volume_min, not left at 0",
                lots_tiny_account == spec.volume_min, lots_tiny_account)

    fg8 = FakeGateway(same_dir_open=0, equity=6000.0)
    executor.execute(fg8, cfg_risk, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("execute() actually sends the risk-sized lot, not fixed_lot",
                fg8.orders_sent and abs(fg8.orders_sent[0][1] - lots_6k) < 1e-9, fg8.orders_sent)

    # --- daily loss BUDGET (max_daily_loss_pct as a real cap) ---
    # Defaults: 2% risk, $6 SL at 0.01 lot = 6.0 price -> 0.33 lots risks
    # $198 at $10k; the 10% cap is $1000 of day-start equity.
    cfg_budget = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs")

    def open_buy(ticket, sl=2344.2, volume=0.33):
        return {"ticket": ticket, "direction": "buy", "volume": volume, "price_open": 2350.2,
                "sl": sl, "tp": 0.0}

    fg_b0 = FakeGateway(equity=10000.0)
    d_b0 = executor.execute(fg_b0, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("a first 2%-risk trade fits comfortably inside the 10% daily budget",
                d_b0.executed, d_b0.reject_reason)

    four_open = [open_buy(i) for i in range(4)]   # each still risks 5.8 price x 0.33 = $191.40
    fg_b1 = FakeGateway(equity=10000.0, positions=four_open)
    d_b1 = executor.execute(fg_b1, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("4 open + 1 new at ~2% each (~$964) still fits the $1000 budget",
                d_b1.executed, d_b1.reject_reason)

    five_open = [open_buy(i) for i in range(5)]
    fg_b2 = FakeGateway(equity=10000.0, positions=five_open)
    d_b2 = executor.execute(fg_b2, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("5 open + 1 new would risk ~$1155 at once - refused, so the 10% cap is a real cap "
                "and not just a trigger that fires after the damage",
                not d_b2.executed and d_b2.reject_reason.startswith("daily loss budget"),
                d_b2.reject_reason)

    fg_b3 = FakeGateway(equity=9500.0, positions=[open_buy(0), open_buy(1)])
    d_b3 = executor.execute(fg_b3, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("today's drawdown so far counts too: $500 down + 2 open + new (~$1069) is refused",
                not d_b3.executed and d_b3.reject_reason.startswith("daily loss budget"),
                d_b3.reject_reason)

    locked = [open_buy(i, sl=2351.0) for i in range(5)]   # SL already above bid: profit locked
    fg_b4 = FakeGateway(equity=10000.0, positions=locked)
    d_b4 = executor.execute(fg_b4, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("positions whose SL is already locked in profit add no open risk",
                d_b4.executed, d_b4.reject_reason)

    fg_b5 = FakeGateway(equity=10000.0, positions=five_open)
    d_b5 = executor.execute(fg_b5, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("no day_start_equity passed (0) skips the budget check entirely",
                d_b5.executed, d_b5.reject_reason)

    # Pending orders (e.g. UnifiedTrader's Telegram BuyLimits) count too -
    # a fast move can fill them all at once. Each: 0.33 lot x 6.0 = $198.
    def pending_buy(ticket):
        return {"ticket": ticket, "magic": 20260922, "direction": "buy", "volume": 0.33,
                "price_open": 2348.0, "sl": 2342.0}
    fg_b6 = FakeGateway(equity=10000.0, pending=[pending_buy(i) for i in range(4)])
    d_b6 = executor.execute(fg_b6, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("4 resting 2% limit orders + 1 new trade (~$990) still fit the $1000 budget",
                d_b6.executed, d_b6.reject_reason)
    fg_b7 = FakeGateway(equity=10000.0, pending=[pending_buy(i) for i in range(5)])
    d_b7 = executor.execute(fg_b7, cfg_budget, make_verdict("buy", 3, "full"), spec, trades_today=0,
                            day_start_equity=10000.0)
    ok &= check("...but 5 resting limit orders + 1 new trade (~$1188) are refused - pending "
                "orders' risk counts toward the daily budget",
                not d_b7.executed and d_b7.reject_reason.startswith("daily loss budget"),
                d_b7.reject_reason)

    # A live order the broker refuses must never be reported as a trade.
    cfg_live = AdvisorConfig(dry_run=False, log_dir="/tmp/claudesmc_selftest_logs")
    fg_rej = FakeGateway(equity=10000.0, result=FakeResult(retcode=10019, order=0))
    d_rej = executor.execute(fg_rej, cfg_live, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("a live order rejected by the broker (10019 no money) is NOT executed",
                not d_rej.executed and "retcode=10019" in d_rej.reject_reason, d_rej)
    fg_ok = FakeGateway(equity=10000.0, result=FakeResult(retcode=10009, order=777))
    d_ok = executor.execute(fg_ok, cfg_live, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("a filled live order (10009 done) is executed with its ticket",
                d_ok.executed and d_ok.ticket == "777", d_ok)

    # Trading a different fixed lot never moves the SL price distance - the
    # $ amounts are priced at reference_lot, exactly like the EAs' lock/trail.
    cfg_lot = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                            use_risk_percent=False, fixed_lot=0.05)
    fg_lot = FakeGateway()
    executor.execute(fg_lot, cfg_lot, make_verdict("buy", 3, "full"), spec, trades_today=0)
    sent = fg_lot.orders_sent[0]
    ok &= check("fixed_lot=0.05 trades 0.05 lots but keeps the 6.0-price SL of the 0.01 reference "
                "lot (so the EA's TP1 lock/trail distances still match it)",
                sent[1] == 0.05 and abs((fg_lot.ask - sent[2]) - 6.0) < 1e-9, sent)

    ok &= check("verdict_independent_block() includes the daily trade limit, so main.run_once() "
                "can skip the paid Claude call when it's already reached",
                executor.verdict_independent_block(FakeGateway(), AdvisorConfig(max_trades_per_day=2), 2)
                .startswith("max trades/day"))

    return ok


def test_stale_csv_header_warning() -> bool:
    print("\n=== 5b. executor._append_row(): stale CSV header detection ===")
    ok = True
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "decisions.csv")
        # Simulate a file written by an OLDER version, missing a column a
        # newer `fieldnames` list adds - exactly what happens after
        # upgrading a live deployment's decisions.csv to include "ticket".
        old_fields = ["time", "source", "direction"]
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(old_fields)
            csv.writer(f).writerow(["2026-01-01T00:00:00+00:00", "Claude_Sig", "buy"])

        new_fields = ["time", "source", "direction", "ticket"]
        logged = []
        handler = logging.Handler()
        handler.emit = lambda record: logged.append(record.getMessage())
        executor.log.addHandler(handler)
        try:
            executor._append_row(path, new_fields,
                                 {"time": "x", "source": "Claude_Sig", "direction": "buy", "ticket": "1"})
            executor._append_row(path, new_fields,
                                 {"time": "y", "source": "Claude_Sig", "direction": "sell", "ticket": "2"})
        finally:
            executor.log.removeHandler(handler)

        ok &= check("a stale (older-layout) header on an existing file is warned about",
                    any("older column layout" in m for m in logged), logged)
        ok &= check("the warning is only logged ONCE per file, not once per appended row",
                    sum("older column layout" in m for m in logged) == 1, logged)

        path2 = os.path.join(tmp, "trades.csv")
        logged2 = []
        handler2 = logging.Handler()
        handler2.emit = lambda record: logged2.append(record.getMessage())
        executor.log.addHandler(handler2)
        try:
            executor._append_row(path2, ["a", "b"], {"a": "1", "b": "2"})
        finally:
            executor.log.removeHandler(handler2)
        ok &= check("a brand-new file (no existing header to compare against) never warns",
                    logged2 == [], logged2)

    return ok


# --------------------------------------------------------------------------- #
# claude_advisor wiring (fake Anthropic client)
# --------------------------------------------------------------------------- #
class FakeParsedResponse:
    def __init__(self, parsed_output, stop_reason="end_turn", stop_details=None):
        self.parsed_output = parsed_output
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class FakeMessages:
    def __init__(self, canned: ConfluenceVerdict):
        self.canned = canned
        self.last_call = None

    def parse(self, **kwargs):
        self.last_call = kwargs
        return FakeParsedResponse(self.canned)


class FakeAnthropicClient:
    def __init__(self, canned: ConfluenceVerdict):
        from types import SimpleNamespace
        self.messages = FakeMessages(canned)
        self.beta = SimpleNamespace(messages=self.messages)   # refusal-fallback path


def test_claude_advisor_wiring() -> bool:
    print("\n=== 6. claude_advisor wiring (fake client, no network) ===")
    ok = True
    canned = make_verdict("sell", 3, "full")
    fake_client = FakeAnthropicClient(canned)
    cfg = AdvisorConfig()
    fake_features = {"symbol": "XAUUSD", "note": "synthetic snapshot for the fake client"}

    verdict = claude_advisor.get_verdict(fake_client, cfg, fake_features)
    ok &= check("get_verdict returns exactly what the (fake) client handed back",
                verdict is canned)
    ok &= check("the model id from config was actually passed through",
                fake_client.messages.last_call["model"] == cfg.claude_model,
                fake_client.messages.last_call.get("model"))
    ok &= check("output_format is the ConfluenceVerdict schema",
                fake_client.messages.last_call["output_format"] is ConfluenceVerdict)
    import json
    dumped = json.loads(fake_client.messages.last_call["messages"][0]["content"])
    ok &= check("the feature snapshot round-trips through JSON cleanly",
                dumped == fake_features, dumped)
    call = fake_client.messages.last_call
    ok &= check("rollout settings: claude-opus-5, max_tokens 16000 (thinking counts toward it), "
                "server-side refusal fallbacks 'default' with its beta header",
                cfg.claude_model == "claude-opus-5" and call["max_tokens"] == 16000
                and call.get("fallbacks") == "default"
                and call.get("betas") == ["server-side-fallback-2026-07-01"], call.get("betas"))

    from types import SimpleNamespace
    for label, resp in [
        ("a refusal", FakeParsedResponse(None, "refusal", SimpleNamespace(category="cyber"))),
        ("a max_tokens cut-off", FakeParsedResponse(None, "max_tokens")),
    ]:
        c = FakeAnthropicClient(canned)
        c.messages.parse = lambda _r=resp, **kw: _r
        v = claude_advisor.get_verdict(c, cfg, fake_features)
        ok &= check(f"{label} becomes a 'no trade' verdict (no crash, no paid retry of the bar)",
                    v.direction == "none" and v.conviction == "none"
                    and v.reasoning.startswith("No verdict this cycle"), v.reasoning)

    class _Rejecting:
        def __init__(self):
            self.calls = []

        def parse(self, **kw):
            self.calls.append(kw)
            if "fallbacks" in kw:
                err = RuntimeError("fallbacks: unknown parameter")
                err.status_code = 400
                raise err
            return FakeParsedResponse(canned)
    rej = _Rejecting()
    c = SimpleNamespace(messages=rej, beta=SimpleNamespace(messages=rej))
    claude_advisor._fallbacks_rejected["value"] = False
    try:
        v1 = claude_advisor.get_verdict(c, cfg, fake_features)
        v2 = claude_advisor.get_verdict(c, cfg, fake_features)
    finally:
        claude_advisor._fallbacks_rejected["value"] = False
    ok &= check("if the API rejects the fallbacks option, the call is retried once without it and "
                "later calls skip it (never a dead trader)",
                v1 is canned and v2 is canned and len(rej.calls) == 3
                and "fallbacks" in rej.calls[0] and "fallbacks" not in rej.calls[1]
                and "fallbacks" not in rej.calls[2], [sorted(k for k in c_ if k in ("fallbacks",)) for c_ in rej.calls])

    return ok


class FakeMessagesRaising:
    """A `.messages` stand-in whose parse() raises a given exception, so
    get_verdict()'s error-wrapping can be tested without a live API call."""
    def __init__(self, exc: Exception):
        self.exc = exc

    def parse(self, **kwargs):
        raise self.exc


class FakeClientRaising:
    def __init__(self, exc: Exception):
        from types import SimpleNamespace
        self.messages = FakeMessagesRaising(exc)
        self.beta = SimpleNamespace(messages=self.messages)


def test_claude_error_classification() -> bool:
    print("\n=== 7. Claude API error classification (out-of-credits / rate-limit / ...) ===")
    ok = True
    try:
        import anthropic
        import httpx2
    except ImportError as exc:
        print(f"  [SKIP] `anthropic` package not installed - can't build real SDK exceptions ({exc})")
        return True

    cfg = AdvisorConfig()
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def status_error(status: int, error_type: str, message: str):
        body = {"type": "error", "error": {"type": error_type, "message": message}}
        resp = httpx2.Response(status, request=req, json=body)
        return anthropic.APIStatusError(message, response=resp, body=body)

    cases = [
        # (exception, expect_retryable, must_contain)
        (status_error(402, "billing_error", "Your credit balance is too low."),
         False, "out of credits"),
        (status_error(401, "authentication_error", "invalid x-api-key"), False, "API key"),
        (status_error(403, "permission_error", "not allowed"), False, "denied"),
        (status_error(404, "not_found_error", "model: claude-old"), False, "--model"),
        (status_error(429, "rate_limit_error", "too many requests"), True, "rate-limited"),
        (status_error(529, "overloaded_error", "overloaded"), True, "service issue"),
        (anthropic.APIConnectionError(message="network down", request=req), True, "reach"),
        (ValueError("something unrelated broke"), False, "unexpectedly"),
        (TypeError("Could not resolve authentication method. Expected one of api_key, ..."), False,
         "put ANTHROPIC_API_KEY in keys.txt"),
    ]
    for exc, expect_retryable, must_contain in cases:
        wrapped = claude_advisor._wrap_api_error(exc)
        label = type(exc).__name__
        ok &= check(f"{label} -> ClaudeUnavailableError(retryable={expect_retryable})",
                    isinstance(wrapped, claude_advisor.ClaudeUnavailableError)
                    and wrapped.retryable == expect_retryable
                    and must_contain in str(wrapped),
                    str(wrapped))

    # get_verdict() itself must surface the wrapped error, not the raw SDK one -
    # this is what main.py's `except claude_advisor.ClaudeUnavailableError` relies on.
    billing_exc = status_error(402, "billing_error", "Your credit balance is too low.")
    raised = None
    try:
        claude_advisor.get_verdict(FakeClientRaising(billing_exc), cfg, {"symbol": "XAUUSD"})
    except Exception as exc:
        raised = exc
    ok &= check("get_verdict() raises ClaudeUnavailableError (not the raw SDK exception) on a "
                "billing failure", isinstance(raised, claude_advisor.ClaudeUnavailableError)
                and not raised.retryable, raised)
    ok &= check("the original SDK exception is preserved as __cause__ for debugging",
                raised.__cause__ is billing_exc, raised.__cause__)

    return ok


# --------------------------------------------------------------------------- #
# full snapshot pipeline (fake MT5 bars, real indicator/SMC code)
# --------------------------------------------------------------------------- #
class FakeIntelGateway:
    """Returns synthetic bars/tick/spec shaped exactly like mt5_gateway's real
    functions, so build_feature_snapshot() runs its actual indicator/SMC code
    end-to-end without a live MT5 connection.
    """
    def __init__(self):
        self._df = make_trending_df(n=320)

    def get_bars(self, symbol, timeframe_name, count):
        # last row stands in for the still-forming bar build_feature_snapshot drops
        return self._df.tail(count).reset_index(drop=True)

    def get_tick(self, symbol):
        last = self._df["close"].iloc[-1]
        return FakeTick(bid=last - 0.1, ask=last + 0.1)

    def symbol_spec(self, symbol):
        return gw.SymbolSpec(name=symbol, point=0.01, digits=2, stops_level_points=0,
                             spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                             tick_value=1.0, tick_size=0.01)

    def recent_closed_trades(self, symbol, magic, count, lookback_days=14):
        return []


def test_full_snapshot_pipeline() -> bool:
    print("\n=== 8. full feature-snapshot pipeline (fake bars, real indicator code) ===")
    ok = True
    # An isolated log_dir, not the default "logs" - same reasoning every
    # executor-touching test below uses its own tmp/log_dir: ml_win_probability
    # reads whatever model file happens to exist at cfg.log_dir, so a bare
    # default here would read a REAL model from an actual deployment's
    # logs/ directory if this were ever run from one, rather than the clean
    # "no model trained yet" state this test asserts on.
    cfg = AdvisorConfig(bars_per_timeframe=320, log_dir="/tmp/claudesmc_selftest_logs")
    snapshot = market_intel.build_feature_snapshot(FakeIntelGateway(), cfg)

    ok &= check("snapshot has the expected top-level sections",
                {"primary_indicators", "trend_bias", "smc", "last_closed_candle",
                 "recent_candles", "session", "recent_performance"} <= snapshot.keys(),
                list(snapshot.keys()))
    ok &= check("recent_candles carries exactly 20 candles", len(snapshot["recent_candles"]) == 20,
                len(snapshot["recent_candles"]))
    ok &= check("recent_performance reports no data yet against a gateway with no trade history",
                snapshot["recent_performance"] == {"trade_count": 0,
                                                     "note": "no closed trades yet under this magic number"},
                snapshot["recent_performance"])
    ok &= check("dxy is null when dxy_symbol is unset (the default)",
                snapshot["dxy"] is None, snapshot["dxy"])
    ok &= check("consensus is null when consensus_magic_numbers is unset (the default)",
                snapshot["consensus"] is None, snapshot["consensus"])
    ok &= check("ml_win_probability is null before any local model has been trained",
                snapshot["ml_win_probability"] is None, snapshot["ml_win_probability"])
    ok &= check("economic_calendar is null when the gateway has no exported calendar to read",
                snapshot["economic_calendar"] is None, snapshot["economic_calendar"])

    import json
    try:
        # No default=str here on purpose: every value in the snapshot should
        # already be a native JSON type (see market_intel's explicit
        # float()/bool()/int() casts) - claude_advisor.py's default=str is a
        # last-resort safety net, not something this pipeline should rely on.
        json.dumps(snapshot)
        serializes = True
    except TypeError as exc:
        serializes = False
        print(f"    json.dumps failed: {exc}")
    ok &= check("the whole snapshot is natively JSON-serializable (no numpy leaks)",
                serializes)

    return ok


class FakeDxyGateway:
    """Only implements get_bars() - enough to test market_intel.dxy_context()
    in isolation. raise_on_get_bars simulates the DXY symbol not being
    available on this broker/account (wrong name, not in Market Watch).
    """
    def __init__(self, df=None, raise_on_get_bars=False):
        self.df = df
        self.raise_on_get_bars = raise_on_get_bars

    def get_bars(self, symbol, timeframe_name, count):
        if self.raise_on_get_bars:
            raise RuntimeError(f"symbol_info({symbol}) returned None: unknown symbol")
        return self.df.tail(count).reset_index(drop=True)


def test_dxy_context() -> bool:
    print("\n=== 8d. market_intel.dxy_context() ===")
    ok = True

    cfg_off = AdvisorConfig()
    ok &= check("dxy_symbol unset (the default) returns None without ever calling the gateway",
                market_intel.dxy_context(FakeDxyGateway(raise_on_get_bars=True), cfg_off) is None)

    cfg_on = AdvisorConfig(dxy_symbol="USDX")
    rising_df = make_trending_df(n=80, start=100.0, drift=0.05, noise=0.01, seed=11)
    result = market_intel.dxy_context(FakeDxyGateway(rising_df), cfg_on)
    ok &= check("a configured, available DXY symbol returns a populated context dict",
                result is not None and result["symbol"] == "USDX" and result["vs_ema20"] == "above",
                result)

    ok &= check("an unavailable DXY symbol (gateway raises) is a safe no-op, not a crash",
                market_intel.dxy_context(FakeDxyGateway(raise_on_get_bars=True), cfg_on) is None)

    too_short_df = make_trending_df(n=10)
    ok &= check("not enough history yet returns None rather than a garbage EMA",
                market_intel.dxy_context(FakeDxyGateway(too_short_df), cfg_on) is None)

    return ok


class FakeConsensusGateway:
    """Only implements open_positions() - enough to test
    market_intel.consensus_context() in isolation. positions_by_magic maps
    magic -> list of {"direction": "buy"|"sell"} dicts, same shape
    mt5_gateway.open_positions() returns.
    """
    def __init__(self, positions_by_magic, raise_error=False):
        self.positions_by_magic = positions_by_magic
        self.calls = []
        self.raise_error = raise_error

    def open_positions(self, symbol, magic):
        self.calls.append((symbol, magic))
        if self.raise_error:
            raise RuntimeError("simulated MT5 IPC hiccup")
        return self.positions_by_magic.get(magic, [])


def test_consensus_context() -> bool:
    print("\n=== 8e. market_intel.consensus_context() ===")
    ok = True

    cfg_off = AdvisorConfig()
    fake_off = FakeConsensusGateway({})
    ok &= check("consensus_magic_numbers unset (the default) returns None without calling the gateway",
                market_intel.consensus_context(fake_off, cfg_off) is None and fake_off.calls == [],
                fake_off.calls)

    cfg_on = AdvisorConfig(consensus_magic_numbers=[20260922])
    fake_on = FakeConsensusGateway({20260922: [{"direction": "buy"}, {"direction": "buy"},
                                                {"direction": "sell"}]})
    result = market_intel.consensus_context(fake_on, cfg_on)
    ok &= check("positions from every configured magic are counted by direction",
                result == {"other_system_buy_positions": 2, "other_system_sell_positions": 1}, result)

    cfg_multi = AdvisorConfig(consensus_magic_numbers=[111, 222])
    fake_multi = FakeConsensusGateway({111: [{"direction": "buy"}], 222: [{"direction": "sell"}] * 3})
    result_multi = market_intel.consensus_context(fake_multi, cfg_multi)
    ok &= check("multiple configured magics are combined into one summary",
                result_multi == {"other_system_buy_positions": 1, "other_system_sell_positions": 3},
                result_multi)

    cfg_none_open = AdvisorConfig(consensus_magic_numbers=[999])
    result_empty = market_intel.consensus_context(FakeConsensusGateway({}), cfg_none_open)
    ok &= check("no open positions under the configured magic reports zero, not None",
                result_empty == {"other_system_buy_positions": 0, "other_system_sell_positions": 0},
                result_empty)

    cfg_failing = AdvisorConfig(consensus_magic_numbers=[20260922])
    result_failing = market_intel.consensus_context(
        FakeConsensusGateway({}, raise_error=True), cfg_failing)
    ok &= check("a gateway failure (transient MT5 hiccup) degrades to None, not an uncaught "
                "exception that would abort the whole evaluation cycle",
                result_failing is None, result_failing)

    return ok


class FakePerformanceGateway:
    """Only implements recent_closed_trades() - enough to test
    market_intel.recent_performance_summary() in isolation from the rest of
    build_feature_snapshot()'s gateway surface.
    """
    def __init__(self, trades, raise_error=False):
        self.trades = trades
        self.raise_error = raise_error
        self.last_lookback_days = None

    def recent_closed_trades(self, symbol, magic, count, lookback_days=14):
        self.last_lookback_days = lookback_days
        if self.raise_error:
            raise RuntimeError("simulated MT5 IPC hiccup")
        return self.trades[:count]


def test_recent_performance_summary() -> bool:
    print("\n=== 8b. market_intel.recent_performance_summary() ===")
    ok = True
    cfg = AdvisorConfig()

    trades = [
        {"direction": "buy", "pnl_dollars": 5.0, "ticket": 1},
        {"direction": "sell", "pnl_dollars": -3.0, "ticket": 2},
        {"direction": "buy", "pnl_dollars": 4.0, "ticket": 3},
    ]
    fake_gw = FakePerformanceGateway(trades)
    summary = market_intel.recent_performance_summary(fake_gw, cfg)
    ok &= check("trade_count/wins/losses/win_rate are computed correctly",
                summary["trade_count"] == 3 and summary["wins"] == 2 and summary["losses"] == 1
                and abs(summary["win_rate_pct"] - 66.7) < 0.1, summary)
    ok &= check("a generous lookback_days (365, not mt5_gateway.recent_closed_trades()'s own "
                "14-day default) is passed through, so 'last 10 trades' genuinely means the last "
                "10 regardless of how many days they're spread over on a low-frequency system",
                fake_gw.last_lookback_days == 365, fake_gw.last_lookback_days)
    ok &= check("net_pnl_dollars sums every trade's P&L",
                abs(summary["net_pnl_dollars"] - 6.0) < 1e-9, summary)
    ok &= check("last_5_results carries every trade when there are fewer than 5",
                len(summary["last_5_results"]) == 3, summary)

    empty_summary = market_intel.recent_performance_summary(FakePerformanceGateway([]), cfg)
    ok &= check("no closed trades yet is reported as a safe 'no data' shape, not an error",
                empty_summary == {"trade_count": 0,
                                   "note": "no closed trades yet under this magic number"},
                empty_summary)

    many_trades = [{"direction": "buy", "pnl_dollars": 1.0, "ticket": i} for i in range(8)]
    many_summary = market_intel.recent_performance_summary(FakePerformanceGateway(many_trades), cfg)
    ok &= check("last_5_results is capped at 5 even with more trades available",
                len(many_summary["last_5_results"]) == 5, many_summary)

    failing_summary = market_intel.recent_performance_summary(
        FakePerformanceGateway([], raise_error=True), cfg)
    ok &= check("a gateway failure (transient MT5 hiccup) degrades to a safe 'no data' shape "
                "rather than an uncaught exception that would abort the whole evaluation cycle "
                "- this one is called unconditionally every cycle, unlike dxy/consensus context",
                failing_summary == {"trade_count": 0,
                                     "note": "performance data temporarily unavailable"},
                failing_summary)

    return ok


class FakeMt5History:
    """Stands in for the MetaTrader5 module's history_deals_get() +
    DEAL_ENTRY_OUT/DEAL_TYPE_BUY/DEAL_TYPE_SELL constants, so
    mt5_gateway.recent_closed_trades() is tested without a real terminal.
    """
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_IN = 0
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1

    class Deal:
        def __init__(self, symbol, magic, entry, type_, time, profit, swap, commission, position_id):
            self.symbol, self.magic, self.entry, self.type = symbol, magic, entry, type_
            self.time, self.profit, self.swap, self.commission = time, profit, swap, commission
            self.position_id = position_id

    def __init__(self, deals):
        self._deals = deals

    def history_deals_get(self, date_from, date_to):
        return self._deals


def test_mt5_gateway_recent_closed_trades() -> bool:
    print("\n=== 8c. mt5_gateway.recent_closed_trades() ===")
    ok = True

    fake_m = FakeMt5History([
        # A closed BUY position: the exit deal is a SELL.
        FakeMt5History.Deal("XAUUSD", 20260921, FakeMt5History.DEAL_ENTRY_OUT,
                            FakeMt5History.DEAL_TYPE_SELL, 1000, 5.0, -0.2, -0.5, 101),
        # A closed SELL position: the exit deal is a BUY.
        FakeMt5History.Deal("XAUUSD", 20260921, FakeMt5History.DEAL_ENTRY_OUT,
                            FakeMt5History.DEAL_TYPE_BUY, 2000, -3.0, -0.1, -0.5, 102),
        # The matching ENTRY deal for the same position - must be excluded.
        FakeMt5History.Deal("XAUUSD", 20260921, FakeMt5History.DEAL_ENTRY_IN,
                            FakeMt5History.DEAL_TYPE_BUY, 900, 0.0, 0.0, 0.0, 101),
        # A different symbol - must be excluded.
        FakeMt5History.Deal("EURUSD", 20260921, FakeMt5History.DEAL_ENTRY_OUT,
                            FakeMt5History.DEAL_TYPE_SELL, 1500, 9.0, 0.0, 0.0, 103),
        # A different magic (e.g. the Telegram side's own trades) - excluded.
        FakeMt5History.Deal("XAUUSD", 999, FakeMt5History.DEAL_ENTRY_OUT,
                            FakeMt5History.DEAL_TYPE_SELL, 1600, 9.0, 0.0, 0.0, 104),
        # Position 105 closed in two parts - one trade, P&L summed, last close time.
        FakeMt5History.Deal("XAUUSD", 20260921, FakeMt5History.DEAL_ENTRY_OUT,
                            FakeMt5History.DEAL_TYPE_SELL, 3000, 4.0, 0.0, 0.0, 105),
        FakeMt5History.Deal("XAUUSD", 20260921, FakeMt5History.DEAL_ENTRY_OUT,
                            FakeMt5History.DEAL_TYPE_SELL, 3600, -1.0, 0.0, 0.0, 105),
    ])
    gw._mt5 = fake_m
    try:
        trades = gw.recent_closed_trades("XAUUSD", 20260921, count=10)
    finally:
        gw._mt5 = None

    ok &= check("a position closed in two parts is ONE trade (P&L summed, time of the last close)",
                trades[0]["ticket"] == 105 and abs(trades[0]["pnl_dollars"] - 3.0) < 1e-9
                and sum(1 for t in trades if t["ticket"] == 105) == 1, trades[:1])
    trades = trades[1:]
    ok &= check("only this symbol+magic's DEAL_ENTRY_OUT deals are returned",
                len(trades) == 2, trades)
    ok &= check("newest first", trades[0]["ticket"] == 102 and trades[1]["ticket"] == 101, trades)
    ok &= check("a closed BUY position's exit (SELL) deal is reported as direction=buy",
                trades[1]["direction"] == "buy", trades[1])
    ok &= check("a closed SELL position's exit (BUY) deal is reported as direction=sell",
                trades[0]["direction"] == "sell", trades[0])
    ok &= check("net P&L is profit+swap+commission",
                abs(trades[1]["pnl_dollars"] - 4.3) < 1e-9, trades[1])

    gw._mt5 = FakeMt5History([])
    try:
        empty = gw.recent_closed_trades("XAUUSD", 20260921, count=10)
    finally:
        gw._mt5 = None
    ok &= check("no history at all returns an empty list, not an error", empty == [], empty)

    return ok


class FakeMt5Terminal:
    """Stands in for the MetaTrader5 module's terminal_info(), so
    mt5_gateway.write_common_file() is tested without a real terminal.
    """
    class TerminalInfo:
        def __init__(self, commondata_path):
            self.commondata_path = commondata_path

    def __init__(self, commondata_path, fail=False):
        self.commondata_path = commondata_path
        self.fail = fail

    def terminal_info(self):
        return None if self.fail else FakeMt5Terminal.TerminalInfo(self.commondata_path)

    def last_error(self):
        return "simulated terminal_info() failure"


class FakeMt5Book:
    """positions_get/orders_get/symbol_info for mt5_gateway's cap and fill helpers."""
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY_LIMIT, ORDER_TYPE_SELL_LIMIT, ORDER_TYPE_BUY_STOP, ORDER_TYPE_SELL_STOP = 2, 3, 4, 5
    ORDER_TYPE_BUY_STOP_LIMIT = 6
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2

    class Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def __init__(self, positions, orders, filling_mode=2):
        self._positions, self._orders, self._filling = positions, orders, filling_mode

    def positions_get(self, symbol=None):
        return self._positions

    def orders_get(self, symbol=None):
        return self._orders

    def symbol_info(self, symbol):
        return self.Obj(filling_mode=self._filling)


def test_gateway_cap_and_filling() -> bool:
    print("\n=== 8g. mt5_gateway: pending orders in the shared cap, account-wide risk, fill mode ===")
    ok = True
    O = FakeMt5Book.Obj
    positions = [O(ticket=1, magic=20260921, type=0, volume=0.33, price_open=2350.0, sl=2344.0, tp=0.0),
                 O(ticket=2, magic=555, type=0, volume=0.10, price_open=2350.0, sl=2344.0, tp=0.0)]
    orders = [O(ticket=3, magic=20260922, type=2, volume_current=0.33, price_open=2348.0, sl=2342.0),
              O(ticket=4, magic=20260922, type=3, volume_current=0.33, price_open=2360.0, sl=2366.0)]
    gw._mt5 = FakeMt5Book(positions, orders)
    try:
        ok &= check("count_same_direction counts the shared magic's resting BUY limit too (the EA's "
                    "shared cap already does)",
                    gw.count_same_direction("XAUUSD", 20260921, "buy", (20260922,)) == 2)
        ok &= check("...but not orders of magics outside the shared set",
                    gw.count_same_direction("XAUUSD", 20260921, "buy") == 1)
        ok &= check("symbol_positions() returns every magic (the budget is account-wide)",
                    {p["magic"] for p in gw.symbol_positions("XAUUSD")} == {20260921, 555})
        ok &= check("pending_orders() maps limit types to directions",
                    [o["direction"] for o in gw.pending_orders("XAUUSD")] == ["buy", "sell"])
        m = gw._mt5
        ok &= check("fill mode: IOC when the symbol allows it",
                    gw._filling_mode(m, "XAUUSD") == m.ORDER_FILLING_IOC)
        m._filling = 1
        ok &= check("fill mode: FOK when only FOK is allowed (a fixed IOC would be rejected, 10030)",
                    gw._filling_mode(m, "XAUUSD") == m.ORDER_FILLING_FOK)
        m._filling = 0
        ok &= check("fill mode: RETURN when neither is offered",
                    gw._filling_mode(m, "XAUUSD") == m.ORDER_FILLING_RETURN)
    finally:
        gw._mt5 = None
    return ok


def test_write_common_file() -> bool:
    print("\n=== 8f. mt5_gateway.write_common_file() ===")
    ok = True
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        gw._mt5 = FakeMt5Terminal(tmp)
        try:
            gw.write_common_file("claudesmc_last_verdict.txt", "BUY XAUUSD | conviction=full")
            written_path = os.path.join(tmp, "Files", "claudesmc_last_verdict.txt")
            ok &= check("the file is written under <commondata_path>/Files/<filename>",
                        os.path.exists(written_path), written_path)
            with open(written_path) as f:
                content = f.read()
            ok &= check("the file's content matches exactly what was written",
                        content == "BUY XAUUSD | conviction=full", content)

            gw.write_common_file("claudesmc_last_verdict.txt", "SELL XAUUSD | conviction=partial")
            with open(written_path) as f:
                overwritten = f.read()
            ok &= check("a second write overwrites the file rather than appending",
                        overwritten == "SELL XAUUSD | conviction=partial", overwritten)

            ok &= check("read_common_file() returns what was written",
                        gw.read_common_file("claudesmc_last_verdict.txt")
                        == "SELL XAUUSD | conviction=partial")
            ok &= check("read_common_file() returns None for a file that doesn't exist (e.g. no "
                        "UnifiedTrader_EA pause file yet)",
                        gw.read_common_file("claudesmc_pause.txt") is None)
            with open(os.path.join(tmp, "Files", "claudesmc_pause.txt"), "wb") as f:
                f.write("paused".encode("utf-16"))   # BOM + UTF-16, as some MQL5 builds write
            ok &= check("read_common_file() decodes a BOM-prefixed UTF-16 file from MQL5",
                        gw.read_common_file("claudesmc_pause.txt") == "paused",
                        gw.read_common_file("claudesmc_pause.txt"))
        finally:
            gw._mt5 = None

    gw._mt5 = FakeMt5Terminal("", fail=True)
    raised_no_terminal = None
    try:
        gw.write_common_file("x.txt", "y")
    except RuntimeError as exc:
        raised_no_terminal = exc
    finally:
        gw._mt5 = None
    ok &= check("terminal_info() returning None raises RuntimeError rather than crashing obscurely",
                raised_no_terminal is not None, raised_no_terminal)

    return ok


def _flat_spec(**overrides) -> gw.SymbolSpec:
    base = dict(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
               volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)
    base.update(overrides)
    return gw.SymbolSpec(**base)


def test_backtest_no_lookahead_and_reset() -> bool:
    print("\n=== 9. backtest.HistoricalGateway: no-lookahead + reset() warmup ===")
    ok = True

    m15 = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=100, freq="15min", tz="UTC"),
                        "open": range(100), "high": range(100), "low": range(100), "close": range(100)})
    h4 = pd.DataFrame({"time": pd.date_range("2026-01-01", periods=10, freq="4h", tz="UTC"),
                       "open": range(10), "high": range(10), "low": range(10), "close": range(10)})
    gateway = backtest.HistoricalGateway("XAUUSD", {"M15": m15, "H4": h4}, _flat_spec())

    ok &= check("account_equity() starts at starting_equity (default 10000) with no closed trades",
                gateway.account_equity() == 10000.0, gateway.account_equity())
    gateway.closed_trades.append(backtest.ClosedTrade(
        entry_time=m15["time"].iloc[0], exit_time=m15["time"].iloc[1], direction="buy", lots=0.01,
        entry_price=2350.0, exit_price=2355.0, sl=2344.0, tp=None, exit_reason="tp", pnl_dollars=5.0))
    gateway.closed_trades.append(backtest.ClosedTrade(
        entry_time=m15["time"].iloc[1], exit_time=m15["time"].iloc[2], direction="sell", lots=0.01,
        entry_price=2355.0, exit_price=2358.0, sl=2361.0, tp=None, exit_reason="sl", pnl_dollars=-3.0))
    ok &= check("account_equity() reflects starting_equity plus every realized closed-trade P&L "
                "so far - needed so executor.position_size() (use_risk_percent) works against this "
                "gateway instead of raising AttributeError",
                gateway.account_equity() == 10002.0, gateway.account_equity())

    # Floating P&L on a still-open sim position must count too - real MT5's
    # ACCOUNT_EQUITY (what mt5_gateway.account_equity() reads live) already
    # includes unrealized P&L, and BacktestDayState's daily loss breaker
    # needs to see a position deep underwater the same way live trading's
    # breaker would, not just once it actually closes.
    mini_m15 = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=3, freq="15min", tz="UTC"),
        "open": [2350.0, 2360.0, 2360.0], "high": [2350.0] * 3, "low": [2350.0] * 3,
        "close": [2350.0] * 3,
    })
    floating_gw = backtest.HistoricalGateway("XAUUSD", {"M15": mini_m15}, _flat_spec(),
                                             starting_equity=10000.0)
    floating_gw.primary_timeframe = "M15"
    floating_gw.cursor = 0
    floating_gw.sim_positions = [backtest.SimPosition(
        ticket=1, direction="buy", lots=0.01, entry_time=mini_m15["time"].iloc[0],
        entry_price=2350.0, sl=2344.0, tp=None)]
    # get_tick() at cursor=0 uses the NEXT bar's open (2360.0), spread =
    # bars are bid prices -> bid=2360.0 (ask = bid + 25 points); floating =
    # (2360.0-2350.0)/0.01 x 1.0 x 0.01 = 10.0, so equity = 10000 + 0 + 10.0.
    ok &= check("account_equity() adds floating P&L on open sim_positions, priced off get_tick() "
                "(the same one-bar-ahead, never-the-evaluated-bar's-own-close price used "
                "everywhere else) - not just realized closed-trade P&L",
                abs(floating_gw.account_equity() - 10010.0) < 1e-9, floating_gw.account_equity())
    tick = floating_gw.get_tick("XAUUSD")
    ok &= check("bars are bid prices: bid = next open, ask = bid + spread (buy and sell each pay "
                "exactly one spread per round trip, matching the ask-side exits)",
                abs(tick.bid - 2360.0) < 1e-9 and abs(tick.ask - 2360.25) < 1e-9, (tick.bid, tick.ask))

    raised_wrong_symbol = None
    try:
        gateway.get_bars("EURUSD", "M15", 5)
    except ValueError as exc:
        raised_wrong_symbol = exc
    ok &= check("get_bars() for a DIFFERENT symbol than this backtest loaded raises ValueError "
                "instead of silently returning XAUUSD's own bars mislabeled as EURUSD's (the trap "
                "market_intel.dxy_context() would otherwise fall into if pointed at a backtest run)",
                raised_wrong_symbol is not None, raised_wrong_symbol)

    ok &= check("open_positions() reports no positions when none are simulated open",
                gateway.open_positions("XAUUSD", 20260921) == [])
    gateway.sim_positions = [backtest.SimPosition(
        ticket=1, direction="buy", lots=0.01, entry_time=m15["time"].iloc[0], entry_price=2350.0,
        sl=2344.0, tp=None)]
    ok &= check("open_positions() mirrors mt5_gateway.open_positions()'s shape from the backtest's "
                "own simulated position book - needed so market_intel.consensus_context() works "
                "against this gateway instead of raising AttributeError",
                gateway.open_positions("XAUUSD", 999) == [
                    {"ticket": 1, "direction": "buy", "volume": 0.01, "price_open": 2350.0,
                     "sl": 2344.0, "tp": None}],
                gateway.open_positions("XAUUSD", 999))
    gateway.sim_positions = []

    reset_ok = gateway.reset("M15", warmup_bars=3)
    ok &= check("reset() succeeds with enough history on every timeframe", reset_ok)
    ok &= check("reset() lands on the earliest bar where H4 (the binding constraint) has "
                "warmup_bars closed bars, not earlier or later",
                gateway.current_time == pd.Timestamp("2026-01-01 12:15", tz="UTC"), gateway.current_time)
    ok &= check("now() returns the same simulated replay clock as current_time - executor.gate() "
                "calls this instead of the real wall clock so news_blackout_windows is judged "
                "against the bar being evaluated in a backtest, not the real run date",
                gateway.now() == gateway.current_time, gateway.now())

    real_now = gw.now()
    ok &= check("mt5_gateway.now() returns real, current, UTC wall-clock time",
                real_now.tzinfo is not None and abs((real_now - datetime.now(timezone.utc))
                                                     .total_seconds()) < 5,
                real_now)

    # Before the first H4 bar (00:00-04:00) has closed, it must not be
    # queryable at all - the whole point of the no-lookahead guarantee.
    gateway.cursor = 10  # bar close 02:45
    try:
        gateway.get_bars("XAUUSD", "H4", 5)
        ok = check("H4 raises rather than exposing a bar that hasn't closed yet", False)
    except RuntimeError:
        ok &= check("H4 raises rather than exposing a bar that hasn't closed yet", True)

    # The instant that H4 bar's own CLOSE time (04:00) is reached - not its
    # open time (00:00), which is much earlier - it becomes visible.
    gateway.cursor = 15  # bar close 04:00
    visible = gateway.get_bars("XAUUSD", "H4", 5).iloc[:-1]  # drop the forming-bar placeholder
    ok &= check("that H4 bar becomes visible exactly at its own close time",
                len(visible) == 1 and visible["time"].iloc[0] == h4["time"].iloc[0],
                visible["time"].tolist())

    return ok


def test_backtest_exit_simulation() -> bool:
    print("\n=== 10. backtest.HistoricalGateway: SL / TP / arm-then-trail exit simulation ===")
    ok = True

    def make_gateway(spec):
        bar = pd.DataFrame({"time": [pd.Timestamp("2026-01-01", tz="UTC")],
                            "open": [2350.0], "high": [2350.0], "low": [2350.0], "close": [2350.0]})
        g = backtest.HistoricalGateway("XAUUSD", {"M15": bar}, spec)
        g.primary_timeframe = "M15"
        g.cursor = 0
        return g

    def set_bar(g, o, h, l, c):
        g.bars["M15"] = pd.DataFrame({"time": [pd.Timestamp("2026-01-01", tz="UTC")],
                                      "open": [o], "high": [h], "low": [l], "close": [c]})

    spec = _flat_spec()
    # exit_style="fixed_tp" explicitly - this is the OLD design (a real
    # broker TP at entry+tp1_dist, arming drops it in favor of a trail
    # started from the current high/low), kept only for backtest.py
    # --compare; see test_backtest_exit_simulation_sl_to_tp1 below for the
    # live default's own exit logic.
    cfg = AdvisorConfig(tp1_dollars=3.0, trail_dollars=1.0, exit_style="fixed_tp")

    g1 = make_gateway(spec)
    g1.sim_positions = [backtest.SimPosition(ticket=1, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=2360.0)]
    set_bar(g1, 2350.0, 2353.5, 2349.5, 2353.0)  # profit_at_high 3.5 >= arm_dist 3.0
    g1.manage_positions(cfg)
    pos = g1.sim_positions[0]
    ok &= check("reaching the arm threshold arms the trail and drops the fixed TP",
                pos.armed and pos.tp is None and abs(pos.sl - 2352.5) < 1e-9,
                (pos.sl, pos.tp, pos.armed))
    set_bar(g1, 2353.0, 2353.2, 2352.0, 2352.3)  # pulls back onto the new trailing SL (2352.5)
    g1.manage_positions(cfg)
    ok &= check("a later pullback onto the armed trailing stop closes the position with reason 'trail'",
                len(g1.closed_trades) == 1 and g1.closed_trades[0].exit_reason == "trail"
                and abs(g1.closed_trades[0].exit_price - 2352.5) < 1e-9, g1.closed_trades)

    g2 = make_gateway(spec)
    g2.sim_positions = [backtest.SimPosition(ticket=2, direction="sell", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2356.0, tp=2340.0)]
    set_bar(g2, 2350.0, 2357.0, 2349.0, 2355.0)  # high touches 2357 >= sl 2356
    g2.manage_positions(cfg)
    ok &= check("a sell position's stop-loss being touched closes it at exactly the SL price for -$6",
                g2.closed_trades and g2.closed_trades[0].exit_reason == "sl"
                and g2.closed_trades[0].exit_price == 2356.0 and g2.closed_trades[0].pnl_dollars == -6.0,
                g2.closed_trades)

    g3 = make_gateway(spec)
    g3.sim_positions = [backtest.SimPosition(ticket=3, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=2352.0)]  # tp closer than arm_dist (3.0)
    set_bar(g3, 2350.0, 2352.5, 2349.8, 2352.0)  # profit_at_high 2.5 < arm_dist 3.0 - never arms
    g3.manage_positions(cfg)
    ok &= check("the fixed TP fires on its own when price reaches it before the arm threshold",
                g3.closed_trades and g3.closed_trades[0].exit_reason == "tp"
                and g3.closed_trades[0].pnl_dollars == 2.0, g3.closed_trades)

    # A broker minimum stop distance wider than the trail distance must
    # block the trail from moving at all - mirrors the bug already fixed
    # once in UnifiedTrader_EA.mq5's own history (see its file header).
    wide_stop_spec = _flat_spec(stops_level_points=200)  # 2.0 price units
    g4 = make_gateway(wide_stop_spec)
    g4.sim_positions = [backtest.SimPosition(ticket=4, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=2360.0)]
    set_bar(g4, 2350.0, 2353.5, 2349.5, 2353.0)  # would arm, but candidate SL is only 1.0 away - too tight
    g4.manage_positions(cfg)
    pos4 = g4.sim_positions[0] if g4.sim_positions else None
    ok &= check("a broker minimum stop distance wider than the trail keeps the fixed TP in place",
                pos4 is not None and not pos4.armed and pos4.tp == 2360.0 and pos4.sl == 2344.0,
                pos4)

    return ok


def test_backtest_exit_simulation_sl_to_tp1() -> bool:
    print("\n=== 11. backtest.HistoricalGateway: lock-SL-then-trail (exit_style=sl_to_tp1, live default) ===")
    ok = True

    def make_gateway(spec):
        bar = pd.DataFrame({"time": [pd.Timestamp("2026-01-01", tz="UTC")],
                            "open": [2350.0], "high": [2350.0], "low": [2350.0], "close": [2350.0]})
        g = backtest.HistoricalGateway("XAUUSD", {"M15": bar}, spec)
        g.primary_timeframe = "M15"
        g.cursor = 0
        return g

    def set_bar(g, o, h, l, c):
        g.bars["M15"] = pd.DataFrame({"time": [pd.Timestamp("2026-01-01", tz="UTC")],
                                      "open": [o], "high": [h], "low": [l], "close": [c]})

    spec = _flat_spec()
    cfg = AdvisorConfig(tp1_dollars=3.0, trail_dollars=1.0, exit_style="sl_to_tp1")

    # tp=0.0 mirrors exactly what executor.execute() sends under this style -
    # the position's only exit mechanism is its stop-loss.
    g1 = make_gateway(spec)
    g1.sim_positions = [backtest.SimPosition(ticket=1, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=0.0)]
    set_bar(g1, 2350.0, 2353.5, 2349.5, 2353.0)  # profit_at_high 3.5 >= tp1_dist 3.0
    g1.manage_positions(cfg)
    pos = g1.sim_positions[0]
    ok &= check("reaching tp1_dist locks the SL to EXACTLY entry+tp1_dist, not a trail-from-high value",
                pos.armed and abs(pos.sl - 2353.0) < 1e-9, (pos.sl, pos.armed))

    set_bar(g1, 2353.0, 2355.0, 2353.5, 2354.5)  # a new high past the lock - trail should now tighten
    g1.manage_positions(cfg)
    ok &= check("once armed, later bars trail trail_dist behind new highs",
                abs(g1.sim_positions[0].sl - 2354.0) < 1e-9, g1.sim_positions[0].sl)

    set_bar(g1, 2354.5, 2354.6, 2353.5, 2354.0)  # pulls back onto the trailing SL (2354.0)
    g1.manage_positions(cfg)
    ok &= check("a pullback onto the trailing stop closes the position with reason 'trail'",
                len(g1.closed_trades) == 1 and g1.closed_trades[0].exit_reason == "trail"
                and abs(g1.closed_trades[0].exit_price - 2354.0) < 1e-9, g1.closed_trades)

    # Realism: a bar that OPENS through the stop fills at that open (gap),
    # and a sell's stop fires on the ask (bid bar + 25-point spread).
    gg = make_gateway(spec)
    gg.sim_positions = [backtest.SimPosition(ticket=9, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=0.0)]
    set_bar(gg, 2341.0, 2342.0, 2340.0, 2341.5)
    gg.manage_positions(cfg)
    ok &= check("a gap through a buy's stop fills at the bar open, not the stop",
                len(gg.closed_trades) == 1 and abs(gg.closed_trades[0].exit_price - 2341.0) < 1e-9,
                gg.closed_trades)
    gs = make_gateway(spec)
    gs.sim_positions = [backtest.SimPosition(ticket=10, direction="sell", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2356.0, tp=0.0)]
    set_bar(gs, 2350.0, 2355.8, 2349.8, 2355.0)   # bid high 2355.80 -> ask high 2356.05
    gs.manage_positions(cfg)
    ok &= check("a sell's stop fires on the ask side of the bar (bid high + spread)",
                len(gs.closed_trades) == 1 and gs.closed_trades[0].exit_reason == "sl"
                and abs(gs.closed_trades[0].exit_price - 2356.0) < 1e-9, gs.closed_trades)

    # A stop-out before ever reaching tp1_dist closes at the original SL.
    g2 = make_gateway(spec)
    g2.sim_positions = [backtest.SimPosition(ticket=2, direction="sell", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2356.0, tp=0.0)]
    set_bar(g2, 2350.0, 2357.0, 2349.0, 2355.0)  # high touches 2357 >= sl 2356, well before arming
    g2.manage_positions(cfg)
    ok &= check("a sell position stopped out before arming closes at the original SL for -$6",
                g2.closed_trades and g2.closed_trades[0].exit_reason == "sl"
                and g2.closed_trades[0].exit_price == 2356.0 and g2.closed_trades[0].pnl_dollars == -6.0,
                g2.closed_trades)

    # A broker minimum stop distance wider than the lock's headroom from
    # current price must defer arming rather than lock a too-tight SL.
    wide_stop_spec = _flat_spec(stops_level_points=200)  # 2.0 price units
    g3 = make_gateway(wide_stop_spec)
    g3.sim_positions = [backtest.SimPosition(ticket=3, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=0.0)]
    set_bar(g3, 2350.0, 2353.1, 2349.5, 2353.0)  # lock level 2353.0, high only 0.1 past it - too tight
    g3.manage_positions(cfg)
    pos3 = g3.sim_positions[0] if g3.sim_positions else None
    ok &= check("arming is deferred when locking would violate the broker's minimum stop distance",
                pos3 is not None and not pos3.armed and pos3.sl == 2344.0, pos3)

    # Regression: with risk-% sizing the traded lot is much bigger than the
    # reference fixed_lot. TP1/trail must stay the SAME price distances as at
    # fixed_lot (like the SL) - not shrink with volume (which once meant
    # risking ~$200 at the stop to lock only ~$6).
    g4 = make_gateway(spec)
    g4.sim_positions = [backtest.SimPosition(ticket=4, direction="buy", lots=0.33,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=0.0)]
    set_bar(g4, 2350.0, 2352.0, 2349.5, 2351.5)  # +2.0 price: would already arm if TP1 shrank to ~0.09
    g4.manage_positions(cfg)
    ok &= check("a 0.33-lot position is NOT locked by a 2.0 price move - TP1 is a fixed price "
                "distance at the reference lot, not tp1_dollars re-priced at 0.33 lots",
                not g4.sim_positions[0].armed and g4.sim_positions[0].sl == 2344.0, g4.sim_positions[0])
    set_bar(g4, 2351.5, 2353.5, 2351.0, 2353.0)
    g4.manage_positions(cfg)
    ok &= check("...and it locks at EXACTLY the same price as a 0.01-lot position would (entry+3.0)",
                g4.sim_positions[0].armed and abs(g4.sim_positions[0].sl - 2353.0) < 1e-9,
                g4.sim_positions[0])

    return ok


class FakeDayStateGateway:
    """Only current_time/account_equity() - enough to test
    backtest.BacktestDayState in isolation from a real HistoricalGateway.
    """
    def __init__(self, current_time, equity):
        self.current_time = current_time
        self._equity = equity

    def account_equity(self):
        return self._equity


def test_backtest_day_state() -> bool:
    print("\n=== 11b. backtest.BacktestDayState: daily loss breaker wired into a backtest ===")
    ok = True
    cfg = AdvisorConfig(max_daily_loss_pct=3.0)
    state = backtest.BacktestDayState()
    day1 = pd.Timestamp("2026-01-01", tz="UTC")
    g = FakeDayStateGateway(day1, 10000.0)

    ok &= check("no block reason on the first call of a new simulated day (day-start equity just "
                "anchored, nothing to compare against yet)",
                state.block_reason(g, cfg) == "", state.block_reason(g, cfg))

    g._equity = 9500.0  # -5%, same simulated day
    ok &= check("the breaker trips once simulated equity is down max_daily_loss_pct on the same day",
                state.block_reason(g, cfg) == "daily loss circuit breaker triggered",
                state.block_reason(g, cfg))

    g._equity = 10000.0  # recovers, still same day
    ok &= check("the breaker stays latched for the rest of the simulated day even if equity "
                "recovers",
                state.block_reason(g, cfg) == "daily loss circuit breaker triggered",
                state.block_reason(g, cfg))

    g.current_time = pd.Timestamp("2026-01-02", tz="UTC")
    ok &= check("a new simulated day resets the latch and re-anchors day-start equity - this is "
                "what makes max_daily_loss_pct actually engage during a backtest, instead of "
                "silently never applying the way it would if backtest.py never called this",
                state.block_reason(g, cfg) == "", state.block_reason(g, cfg))

    cfg_off = AdvisorConfig(max_daily_loss_pct=0.0)  # explicitly disabled
    state_off = backtest.BacktestDayState()
    g_off = FakeDayStateGateway(day1, 10000.0)
    state_off.block_reason(g_off, cfg_off)
    g_off._equity = 5000.0  # -50%
    ok &= check("max_daily_loss_pct=0 never blocks anything, even a 50% drawdown",
                state_off.block_reason(g_off, cfg_off) == "", state_off.block_reason(g_off, cfg_off))

    return ok


def test_backtest_end_to_end_mechanical() -> bool:
    print("\n=== 12. backtest.py end-to-end run (--mechanical, tiny synthetic dataset, no API) ===")
    ok = True
    rng = np.random.default_rng(11)

    def make_series(start, periods, freq, drift, noise, start_price):
        times = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
        closes = start_price + np.cumsum(np.full(periods, drift) + rng.normal(0, noise, periods))
        opens = np.roll(closes, 1)
        opens[0] = start_price
        highs = np.maximum(opens, closes) + rng.uniform(0.05, 0.3, periods)
        lows = np.minimum(opens, closes) - rng.uniform(0.05, 0.3, periods)
        return pd.DataFrame({"time": times, "open": opens, "high": highs, "low": lows,
                             "close": closes, "volume": rng.integers(50, 200, periods)})

    # Every timeframe needs > cfg.bars_per_timeframe (300) bars for reset()'s
    # warmup, with the M15 window sitting well inside the others' ranges.
    w1 = make_series("2018-01-01", 350, "7D", 0.5, 3.0, 2300.0)
    d1 = make_series("2018-01-01", 2500, "1D", 0.07, 1.0, 2300.0)
    h4 = make_series("2025-06-01", 900, "4h", 0.02, 0.4, 2340.0)
    m15 = make_series("2026-01-01", 600, "15min", 0.005, 0.15, 2350.0)

    cfg = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_backtest_selftest_logs")
    gateway = backtest.HistoricalGateway("XAUUSD", {"M15": m15, "H4": h4, "D1": d1, "W1": w1}, _flat_spec())
    reset_ok = gateway.reset(cfg.primary_timeframe, cfg.bars_per_timeframe)
    ok &= check("enough synthetic history exists to warm up every timeframe", reset_ok)
    if not reset_ok:
        return ok

    backtest.run_backtest(gateway, cfg, client=None, mechanical=True)
    summary = backtest.summarize(gateway.closed_trades)
    ok &= check("the backtest completes and produces a summary dict without error",
                "total_trades" in summary, summary)
    if gateway.closed_trades:
        ok &= check("every closed trade's P&L matches its own entry/exit/lots math",
                    all(abs(t.pnl_dollars - (1 if t.direction == "buy" else -1)
                            * (t.exit_price - t.entry_price) / gateway.spec.tick_size
                            * gateway.spec.tick_value * t.lots) < 0.01
                        for t in gateway.closed_trades),
                    [(t.direction, t.entry_price, t.exit_price, t.pnl_dollars) for t in gateway.closed_trades])

    out_path = "/tmp/claudesmc_backtest_selftest_trades.csv"
    backtest.write_trades_csv(gateway.closed_trades, out_path)
    with open(out_path) as f:
        rows = list(csv.DictReader(f))
    ok &= check("the trade log CSV has one row per closed trade",
                len(rows) == len(gateway.closed_trades), len(rows))

    # XTR gate replay: short D1/W1 history is enough (they only feed the last
    # 2 closed daily/weekly levels), and the gate is an entry filter only -
    # every trade in the gated variant keeps the exact same SL distance.
    m5 = make_series("2025-12-28", 1800, "5min", 0.002, 0.08, 2349.0)
    h1 = make_series("2025-12-01", 900, "1h", 0.01, 0.3, 2345.0)
    bars = {"M15": m15, "H4": h4, "D1": d1.tail(8), "W1": w1.tail(8), "M5": m5, "H1": h1}
    gws, cfgs = {}, {}
    for mode in ("off", "block_opposed"):
        gws[mode] = backtest.HistoricalGateway("XAUUSD", bars, _flat_spec())
        ok &= check(f"reset() warms up with only 8 D1/W1 bars ({mode})",
                    gws[mode].reset(cfg.primary_timeframe, cfg.bars_per_timeframe))
        cfgs[mode] = dataclasses.replace(cfg, xtr_gate=mode, log_dir=f"/tmp/claudesmc_bt_xtr_{mode}")
    stats = backtest.run_backtest_compare(gws, cfgs, client=None, mechanical=True)
    ok &= check("XTR compare run reports evaluated bars and per-variant block counts",
                stats["evaluated"] > 0 and stats["xtr_blocks"]["off"] == 0, stats)
    dists = {mode: {round(abs(t.entry_price - t.sl), 4) for t in gws[mode].closed_trades} for mode in gws}
    ok &= check("XTR never changes the SL distance of a trade it lets through",
                dists["block_opposed"] <= dists["off"] or not gws["block_opposed"].closed_trades, dists)
    rct = gws["off"].recent_closed_trades("XAUUSD", 0, 5)
    ok &= check("recent_closed_trades() carries ticket + time (XTR stand-down joins on them)",
                all(r["ticket"] and r["time"] is not None for r in rct), rct[:2])

    # The command line with a broker suffix: --symbol must reach the snapshot
    # too (it used to stop at "only has bars for 'XAUUSDm', not 'XAUUSD'").
    with _tempfile.TemporaryDirectory() as tmp:
        files = {}
        for tf, df in (("M15", m15), ("H4", h4), ("D1", d1), ("W1", w1)):
            files[tf] = os.path.join(tmp, f"{tf}.csv")
            df.to_csv(files[tf], index=False)
        rc = backtest.main(["--bars-csv", files["M15"], "--trend-csv", files["H4"], "--daily-csv", files["D1"],
                            "--weekly-csv", files["W1"], "--symbol", "XAUUSDm", "--mechanical", "--yes",
                            "--out", os.path.join(tmp, "trades.csv")])
        ok &= check("backtest --symbol XAUUSDm (broker suffix) runs to the end", rc == 0, rc)

    return ok


def test_telegram_alert() -> bool:
    print("\n=== 13. telegram_alert: full-conviction notification (fake poster, no network) ===")
    ok = True

    calls = []
    def fake_poster(url, payload):
        calls.append((url, payload))

    def raising_poster(url, payload):
        raise RuntimeError("network down")

    ok &= check("blank bot_token/chat_id is a safe no-op - no HTTP call, returns False",
                telegram_alert.send_alert("", "", "hello", poster=fake_poster) is False
                and calls == [], calls)

    sent = telegram_alert.send_alert("TOKEN123", "CHAT456", "hello world", poster=fake_poster)
    ok &= check("both credentials set posts to the right sendMessage URL with the right payload, "
                "returns True",
                sent is True and calls
                and calls[0][0] == "https://api.telegram.org/botTOKEN123/sendMessage"
                and calls[0][1] == {"chat_id": "CHAT456", "text": "hello world"}, calls)

    ok &= check("a poster that raises is caught, not propagated - returns False",
                telegram_alert.send_alert("TOKEN123", "CHAT456", "hello", poster=raising_poster) is False)

    verdict = make_verdict("buy", 3, "full")
    msg_exec = telegram_alert.format_full_conviction_message("XAUUSD", verdict, executed=True)
    ok &= check("the executed message names the direction, symbol, confluence count and EXECUTED",
                "BUY XAUUSD" in msg_exec and "confluence 3/3" in msg_exec and "EXECUTED" in msg_exec,
                msg_exec)

    msg_rej = telegram_alert.format_full_conviction_message(
        "XAUUSD", verdict, executed=False, reject_reason="already 5 open buy position(s) (max 5)")
    ok &= check("a rejected verdict's message includes the reject_reason, not just NOT executed",
                "NOT executed - already 5 open buy position(s) (max 5)" in msg_rej, msg_rej)

    empty_digest = telegram_alert.format_performance_digest("XAUUSD", "Daily", [])
    ok &= check("an empty trade list reports 'no closed trades', not a division by zero",
                empty_digest == "Daily digest for XAUUSD: no closed trades.", empty_digest)

    mixed_digest = telegram_alert.format_performance_digest("XAUUSD", "Weekly", [
        {"pnl_dollars": 5.0}, {"pnl_dollars": -2.0}, {"pnl_dollars": 3.0}, {"pnl_dollars": -1.0},
    ])
    ok &= check("wins/losses/net P&L/win rate are all correct in the digest text",
                mixed_digest == "Weekly digest for XAUUSD: 4 trades, 2W/2L (50% win rate), "
                                 "net P&L $+5.00",
                mixed_digest)

    verdict_digest_exec = telegram_alert.format_verdict_digest(
        "XAUUSD", make_verdict("buy", 3, "full"), executed=True)
    ok &= check("format_verdict_digest() works for an executed full-conviction verdict",
                "BUY XAUUSD" in verdict_digest_exec and "conviction=full" in verdict_digest_exec
                and "EXECUTED" in verdict_digest_exec, verdict_digest_exec)

    verdict_digest_none = telegram_alert.format_verdict_digest(
        "XAUUSD", make_verdict("none", 0, "none"), executed=False, reject_reason="no actionable direction")
    ok &= check("format_verdict_digest() also works for a 'none' conviction verdict (the Why button "
                "must always have something current to echo, not just full-conviction ones)",
                "conviction=none" in verdict_digest_none and "NOT executed" in verdict_digest_none,
                verdict_digest_none)

    heartbeat_msg = telegram_alert.format_heartbeat_message("XAUUSD", 42.0)
    ok &= check("format_heartbeat_message() names the symbol and minutes since last success",
                "XAUUSD" in heartbeat_msg and "42 min" in heartbeat_msg, heartbeat_msg)
    ok &= check("...with no equity line when equity isn't known", "Equity" not in heartbeat_msg,
                heartbeat_msg)
    heartbeat_eq = telegram_alert.format_heartbeat_message("XAUUSD", 5.0, 10245.30, 10213.20)
    ok &= check("heartbeat shows current equity and today's change in $ and %",
                "Equity $10,245.30 (today $+32.10, +0.31%)" in heartbeat_eq, heartbeat_eq)
    digest_eq = telegram_alert.format_performance_digest("XAUUSD", "Daily", [{"pnl_dollars": 4.0}],
                                                         equity=10004.0)
    ok &= check("the daily digest ends with the current equity",
                digest_eq.endswith("\nEquity $10,004.00") and "100% win rate" in digest_eq, digest_eq)

    stale_msg = telegram_alert.format_stale_cycle_alert("XAUUSD", 90.0)
    ok &= check("format_stale_cycle_alert() names the symbol and minutes stuck",
                "WARNING" in stale_msg and "XAUUSD" in stale_msg and "90 min" in stale_msg, stale_msg)

    return ok


def test_day_roll_daily_limits() -> bool:
    print("\n=== 14. main.DayRoll: daily loss circuit breaker / daily target ===")
    ok = True

    cfg = AdvisorConfig(max_daily_loss_pct=3.0)
    day = main_mod.DayRoll()
    day.date = main_mod.DayRoll().today()
    day.roll(10000.0)
    ok &= check("roll() anchors day_start_equity on the first call of the day",
                day.day_start_equity == 10000.0, day.day_start_equity)

    day.check_daily_limits(cfg, 9950.0)
    ok &= check("a 0.5% drawdown does not trip a 3% daily loss limit",
                day.block_reason() == "", day.block_reason())

    day.check_daily_limits(cfg, 9690.0)
    ok &= check("a 3.1% drawdown trips the daily loss circuit breaker",
                day.block_reason() == "daily loss circuit breaker triggered", day.block_reason())

    day.check_daily_limits(cfg, 10000.0)
    ok &= check("the breaker stays latched for the rest of the day even if equity recovers",
                day.block_reason() == "daily loss circuit breaker triggered", day.block_reason())

    cfg_target = AdvisorConfig(use_daily_target=True, daily_target_pct=2.0)
    day2 = main_mod.DayRoll()
    day2.roll(10000.0)
    day2.check_daily_limits(cfg_target, 10100.0)
    ok &= check("a 1% gain does not trip a 2% daily target",
                day2.block_reason() == "", day2.block_reason())
    day2.check_daily_limits(cfg_target, 10250.0)
    ok &= check("a 2.5% gain trips the daily profit target",
                day2.block_reason() == "daily profit target already reached", day2.block_reason())

    cfg_off = AdvisorConfig(max_daily_loss_pct=0.0)
    day3 = main_mod.DayRoll()
    day3.roll(10000.0)
    day3.check_daily_limits(cfg_off, 5000.0)
    ok &= check("max_daily_loss_pct=0 disables the breaker even on a 50% drawdown",
                day3.block_reason() == "", day3.block_reason())

    yesterday = main_mod.DayRoll().today() - timedelta(days=1)
    day4 = main_mod.DayRoll()
    day4.date = yesterday
    day4.roll(10000.0)
    day4.check_daily_limits(cfg, 9000.0)
    ok &= check("breaker latched before the day rolls over", day4.block_reason() != "")
    day4.date = yesterday  # simulate the next trading-day boundary being reached
    day4.roll(9000.0)
    ok &= check("roll() into a new UTC day resets the latch and re-anchors day_start_equity",
                day4.block_reason() == "" and day4.day_start_equity == 9000.0,
                (day4.block_reason(), day4.day_start_equity))

    day5 = main_mod.DayRoll()
    ok &= check("roll() returns None on the very first call - no previous day to digest yet",
                day5.roll(10000.0) is None)
    ok &= check("roll() returns None on a same-day call",
                day5.roll(10050.0) is None)
    day5.date = yesterday
    gap = day5.roll(10100.0)
    ok &= check("roll() returns a (gap_start, gap_end) range on a genuine UTC day transition - "
                "both ends are 'yesterday' for the ordinary, no-outage case",
                gap == (yesterday, yesterday), gap)

    today = main_mod.DayRoll().today()
    long_ago = today - timedelta(days=6)
    day6 = main_mod.DayRoll()
    day6.date = long_ago
    outage_gap = day6.roll(10000.0)
    ok &= check("after a multi-day outage, roll() returns the FULL gap (from the last day it "
                "was tracking through the day before today), not just a single stale date - "
                "nothing in between is silently dropped",
                outage_gap == (long_ago, today - timedelta(days=1)), outage_gap)

    # Persistence: a restart later the same UTC day keeps its anchor and latch.
    import tempfile, json
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "day_state.json")
        d1 = main_mod.DayRoll(state_path=path)
        d1.roll(10000.0)
        d1.check_daily_limits(AdvisorConfig(max_daily_loss_pct=3.0), 9690.0)
        d1.trades_today = 2
        d1.save()
        d2 = main_mod.DayRoll(state_path=path)
        ok &= check("a restart the same day restores day-start equity, the TRIGGERED breaker and "
                    "the trade count (no fresh daily budget from a reduced equity)",
                    d2.day_start_equity == 10000.0 and d2.daily_loss_hit and d2.trades_today == 2,
                    vars(d2))
        with open(path, "w") as f:
            json.dump({"date": "2000-01-01", "day_start_equity": 1.0, "daily_loss_hit": True}, f)
        d3 = main_mod.DayRoll(state_path=path)
        ok &= check("state saved on an earlier day is ignored",
                    d3.day_start_equity == 0.0 and not d3.daily_loss_hit, vars(d3))

    return ok


def test_calibration_report() -> bool:
    print("\n=== 15. calibration_report.py: decisions/trades join and bucketing ===")
    ok = True

    decisions = [
        {"executed": "True", "conviction": "full", "confluence_count": "3", "ticket": "101"},
        {"executed": "False", "conviction": "partial", "confluence_count": "2", "ticket": ""},
        {"executed": "True", "conviction": "full", "confluence_count": "2", "ticket": "102"},
        {"executed": "True", "conviction": "full", "confluence_count": "3", "ticket": "103"},
    ]
    trades = [
        {"ticket": "101"},
        {"ticket": "102"},
        {"ticket": "103"},
    ]
    pairs, unmatched = calibration_report.join_decisions_and_trades(decisions, trades)
    ok &= check("only executed=True rows are joined, by ticket, skipping rejected rows",
                len(pairs) == 3 and unmatched == 0, pairs)
    ok &= check("each decision is paired with the trade sharing its OWN ticket",
                pairs[0][1]["ticket"] == "101" and pairs[1][1]["ticket"] == "102"
                and pairs[2][1]["ticket"] == "103", pairs)

    short_trades = trades[:2]
    pairs2, unmatched2 = calibration_report.join_decisions_and_trades(decisions, short_trades)
    ok &= check("an executed decision whose ticket has no matching trade row is reported as "
                "unmatched, not silently misaligned",
                unmatched2 == 1 and len(pairs2) == 2, (unmatched2, len(pairs2)))

    # The whole point of matching by ticket instead of row order: a process
    # killed between log_decision() and log_trade() for the MIDDLE signal
    # (ticket 102's trade never got written) must not shift every pair
    # after it - a naive positional zip would have paired decision 103
    # with a two-trade list's [1] entry (nonexistent) or, worse, with
    # whatever the next trade in the file happened to be.
    trades_missing_middle = [{"ticket": "101"}, {"ticket": "103"}]
    pairs3, unmatched3 = calibration_report.join_decisions_and_trades(decisions, trades_missing_middle)
    ok &= check("a trade missing from the MIDDLE of the file (not just the tail) still pairs every "
                "OTHER decision with its own correct ticket, rather than shifting them all by one",
                len(pairs3) == 2 and unmatched3 == 1
                and pairs3[0] == (decisions[0], trades_missing_middle[0])
                and pairs3[1] == (decisions[3], trades_missing_middle[1]),
                pairs3)

    # Regression: a decision with a REAL ticket that has no matching trade
    # row must be reported unmatched, never silently steal the fallback
    # trade meant for a later, genuinely-ticketless decision - otherwise
    # both end up wrong (one paired to an unrelated trade, the other
    # wrongly reported unmatched) with no warning either way.
    decisions_with_a_lost_ticket = [
        {"executed": "True", "conviction": "full", "confluence_count": "3", "ticket": "101"},
        {"executed": "True", "conviction": "full", "confluence_count": "3", "ticket": "999"},  # trade row lost
        {"executed": "True", "conviction": "full", "confluence_count": "3", "ticket": ""},      # genuine dry-run
    ]
    trades_with_one_dry_run = [{"ticket": "101"}, {"ticket": ""}]
    pairs4, unmatched4 = calibration_report.join_decisions_and_trades(
        decisions_with_a_lost_ticket, trades_with_one_dry_run)
    ok &= check("the decision with the lost ticket (999) is reported unmatched rather than "
                "stealing the dry-run trade meant for the decision after it",
                unmatched4 == 1, (pairs4, unmatched4))
    ok &= check("the genuinely-ticketless decision still gets its own dry-run trade, not "
                "wrongly reported unmatched because 999 stole it first",
                len(pairs4) == 2 and pairs4[-1] == (decisions_with_a_lost_ticket[2],
                                                    trades_with_one_dry_run[1]),
                pairs4)

    pnl_by_ticket = {"101": 5.0, "102": -3.0, "103": 4.0}
    buckets = calibration_report.summarize_by_bucket(pairs, pnl_by_ticket)
    b_full_3 = buckets[("full", 3)]
    ok &= check("conviction=full/confluence=3 bucket aggregates its 2 trades correctly",
                b_full_3.n_trades == 2 and b_full_3.wins == 2 and b_full_3.losses == 0
                and abs(b_full_3.net_pnl - 9.0) < 1e-9, b_full_3)
    b_full_2 = buckets[("full", 2)]
    ok &= check("conviction=full/confluence=2 bucket is kept separate from confluence=3",
                b_full_2.n_trades == 1 and b_full_2.losses == 1 and b_full_2.net_pnl == -3.0, b_full_2)
    ok &= check("win_rate_pct()/avg_pnl() compute correctly",
                b_full_3.win_rate_pct() == 100.0 and abs(b_full_3.avg_pnl() - 4.5) < 1e-9,
                (b_full_3.win_rate_pct(), b_full_3.avg_pnl()))

    no_pnl_bucket = calibration_report.summarize_by_bucket(pairs, {})
    ok &= check("a ticket with no matching MT5 P&L (e.g. dry-run trades) counts toward n_trades "
                "but never n_with_pnl, and win_rate_pct()/avg_pnl() report None rather than "
                "dividing by zero",
                no_pnl_bucket[("full", 3)].n_with_pnl == 0
                and no_pnl_bucket[("full", 3)].win_rate_pct() is None
                and no_pnl_bucket[("full", 3)].avg_pnl() is None)

    freq = calibration_report.conviction_frequency(decisions)
    ok &= check("conviction_frequency() counts EVERY decision, executed or not",
                freq == {"full": 3, "partial": 1}, freq)

    report_text = calibration_report.format_report(buckets, freq, unmatched=0)
    ok &= check("format_report() names every bucket and the frequency table, with no warning "
                "when unmatched=0",
                "conviction=full" in report_text and "confluence=3/3" in report_text
                and "partial" in report_text and "WARNING" not in report_text, report_text)

    report_with_unmatched = calibration_report.format_report(buckets, freq, unmatched=1)
    ok &= check("format_report() surfaces an unmatched-decisions warning with the actual count "
                "substituted in",
                "WARNING" in report_with_unmatched and "1 executed decision" in report_with_unmatched,
                report_with_unmatched)

    ok &= check("load_csv() on a nonexistent path returns an empty list, not an error",
                calibration_report.load_csv("/tmp/definitely_does_not_exist_12345.csv") == [])

    partial_close_trades = [
        {"ticket": "201", "pnl_dollars": 3.0},   # first partial close
        {"ticket": "202", "pnl_dollars": 5.0},   # an ordinary single-close trade
        {"ticket": "201", "pnl_dollars": 2.0},   # second (final) partial close, same ticket
    ]
    pnl_map = calibration_report.build_pnl_by_ticket(partial_close_trades)
    ok &= check("build_pnl_by_ticket() SUMS multiple rows sharing a ticket (a position closed in "
                "more than one partial exit), rather than the last one silently overwriting the "
                "rest of that trade's realized P&L",
                pnl_map == {"201": 5.0, "202": 5.0}, pnl_map)

    return ok


def test_main_cli_config() -> bool:
    print("\n=== 16b. main.py CLI -> AdvisorConfig (risk defaults, --lots semantics) ===")
    ok = True

    def cfg_for(*argv):
        return main_mod.build_config(main_mod.build_parser().parse_args(list(argv)))

    cfg = cfg_for()
    ok &= check("no flags: 2% risk sizing and the 10% daily cap are on (the shipped defaults)",
                cfg.use_risk_percent and cfg.risk_percent == 2.0 and cfg.max_daily_loss_pct == 10.0
                and cfg.dry_run, cfg)
    cfg = cfg_for("--lots", "0.05")
    ok &= check("an explicit --lots alone means 'trade exactly this lot' (risk sizing off)",
                not cfg.use_risk_percent and cfg.fixed_lot == 0.05, cfg)
    cfg = cfg_for("--lots", "0.05", "--risk-percent", "1")
    ok &= check("--lots with --risk-percent keeps risk sizing (--lots is then only the fallback lot)",
                cfg.use_risk_percent and cfg.risk_percent == 1.0 and cfg.fixed_lot == 0.05, cfg)
    cfg = cfg_for("--risk-percent", "0", "--max-daily-loss", "0")
    ok &= check("--risk-percent 0 / --max-daily-loss 0 turn both features off",
                not cfg.use_risk_percent and cfg.max_daily_loss_pct == 0.0, cfg)

    class FakePauseFile:
        def __init__(self, text=None, fail=False):
            self.text, self.fail = text, fail

        def read_common_file(self, filename):
            if self.fail:
                raise RuntimeError("terminal_info() failed")
            return self.text

    cfg = AdvisorConfig()
    ok &= check("PauseClaudeHab: the EA's pause file saying 'paused' pauses Claude entries",
                main_mod.claude_paused(cfg, FakePauseFile("paused")))
    ok &= check("ResumeClaudeHab: 'running' resumes them",
                not main_mod.claude_paused(cfg, FakePauseFile("running")))
    ok &= check("no pause file at all (no UnifiedTrader_EA) means running",
                not main_mod.claude_paused(cfg, FakePauseFile(None)))
    main_mod.claude_paused(cfg, FakePauseFile("running"), retry_delay=0)
    ok &= check("an unreadable pause file keeps the last state read (running) rather than halting trading",
                not main_mod.claude_paused(cfg, FakePauseFile(fail=True), retry_delay=0))
    main_mod.claude_paused(cfg, FakePauseFile("paused"), retry_delay=0)
    ok &= check("...and a read failure while PAUSED keeps it paused - a hiccup never lifts a pause",
                main_mod.claude_paused(cfg, FakePauseFile(fail=True), retry_delay=0))
    ok &= check("a half-written/empty file (caught mid-write) also keeps the last state",
                main_mod.claude_paused(cfg, FakePauseFile(""), retry_delay=0)
                and main_mod.claude_paused(cfg, FakePauseFile("pau"), retry_delay=0))

    class FlakyOnce(FakePauseFile):
        def __init__(self):
            super().__init__("running")
            self.calls = 0

        def read_common_file(self, filename):
            self.calls += 1
            return "" if self.calls == 1 else self.text
    flaky = FlakyOnce()
    ok &= check("one bad read is retried, and the retry's real content wins",
                not main_mod.claude_paused(cfg, flaky, retry_delay=0) and flaky.calls == 2)
    main_mod.claude_paused(cfg, FakePauseFile("running"), retry_delay=0)
    ok &= check("claude_pause_filename='' disables the check",
                not main_mod.claude_paused(AdvisorConfig(claude_pause_filename=""),
                                           FakePauseFile("paused")))
    return ok


def test_digest_lookback_days() -> bool:
    print("\n=== 16. main.digest_lookback_days() ===")
    ok = True

    ended_yesterday = datetime(2026, 9, 22, tzinfo=timezone.utc).date()
    now = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
    ok &= check("the normal case (no outage - the day that just ended is 'yesterday') returns 1, "
                "the ordinary one-day gap between 'today' and the day that just ended",
                main_mod.digest_lookback_days(ended_yesterday, now=now) == 1,
                main_mod.digest_lookback_days(ended_yesterday, now=now))

    ended_five_days_ago = datetime(2026, 9, 18, tzinfo=timezone.utc).date()
    ok &= check("after a multi-day process outage, lookback reaches back far enough to still "
                "cover the day that ended while the process was down",
                main_mod.digest_lookback_days(ended_five_days_ago, now=now) == 5,
                main_mod.digest_lookback_days(ended_five_days_ago, now=now))

    single_day = datetime(2026, 9, 22, tzinfo=timezone.utc).date()  # a Tuesday
    ok &= check("sundays_in_range() finds nothing in a single non-Sunday day (the ordinary case)",
                main_mod.sundays_in_range(single_day, single_day) == [])

    gap_spanning_sunday = main_mod.sundays_in_range(
        datetime(2026, 9, 18, tzinfo=timezone.utc).date(),   # Friday
        datetime(2026, 9, 22, tzinfo=timezone.utc).date())   # Tuesday - spans Sun 9/20
    ok &= check("sundays_in_range() finds the Sunday a multi-day outage gap crossed, so the week "
                "that completed during the outage still gets a weekly digest",
                gap_spanning_sunday == [datetime(2026, 9, 20, tzinfo=timezone.utc).date()],
                gap_spanning_sunday)

    gap_spanning_two_sundays = main_mod.sundays_in_range(
        datetime(2026, 9, 18, tzinfo=timezone.utc).date(),   # Friday
        datetime(2026, 9, 27, tzinfo=timezone.utc).date())   # the following Sunday
    ok &= check("a gap spanning two Sundays returns both, oldest first, so an outage crossing "
                "multiple week boundaries gets a digest for each",
                gap_spanning_two_sundays == [datetime(2026, 9, 20, tzinfo=timezone.utc).date(),
                                             datetime(2026, 9, 27, tzinfo=timezone.utc).date()],
                gap_spanning_two_sundays)

    return ok


def test_heartbeat() -> bool:
    print("\n=== 17. main.Heartbeat: heartbeat ping / stale-cycle alert ===")
    ok = True

    cfg_off = AdvisorConfig()
    hb = main_mod.Heartbeat()
    ok &= check("due_heartbeat() is False when heartbeat_interval_hours=0 (the default)",
                hb.due_heartbeat(cfg_off) is False)
    ok &= check("due_stale_alert() is False immediately after construction (nothing stale yet)",
                hb.due_stale_alert(AdvisorConfig(stale_cycle_alert_minutes=60.0)) is False)

    cfg_hb = AdvisorConfig(heartbeat_interval_hours=1.0)
    hb2 = main_mod.Heartbeat()
    hb2.last_heartbeat_sent = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    ok &= check("due_heartbeat() is True once heartbeat_interval_hours has elapsed",
                hb2.due_heartbeat(cfg_hb) is True)
    hb2.mark_heartbeat_sent()
    ok &= check("mark_heartbeat_sent() resets the timer - due_heartbeat() is False right after",
                hb2.due_heartbeat(cfg_hb) is False)

    cfg_stale = AdvisorConfig(stale_cycle_alert_minutes=30.0)
    hb3 = main_mod.Heartbeat()
    hb3.last_successful_cycle = datetime.now(timezone.utc) - timedelta(minutes=31)
    ok &= check("due_stale_alert() is True once stale_cycle_alert_minutes has elapsed with no "
                "successful cycle",
                hb3.due_stale_alert(cfg_stale) is True)
    hb3.mark_stale_alert_sent()
    ok &= check("the stale alert is latched - due_stale_alert() stays False once sent, even "
                "though nothing has recovered",
                hb3.due_stale_alert(cfg_stale) is False)
    hb3.mark_cycle_success()
    ok &= check("mark_cycle_success() clears the latch (unlatched for the next time it goes stale) "
                "and resets minutes_since_last_success() to ~0",
                hb3.stale_alert_sent is False and hb3.minutes_since_last_success() < 0.1)

    cfg_stale_off = AdvisorConfig(stale_cycle_alert_minutes=0.0)
    hb4 = main_mod.Heartbeat()
    hb4.last_successful_cycle = datetime.now(timezone.utc) - timedelta(hours=5)
    ok &= check("stale_cycle_alert_minutes=0 disables the check even after a long silence",
                hb4.due_stale_alert(cfg_stale_off) is False)

    return ok


# --------------------------------------------------------------------------- #
# ml_advisor.py: feature extraction, snapshot logging, offline training
# --------------------------------------------------------------------------- #
def make_synthetic_ml_snapshot(*, rsi14=60.0, adx14=30.0, direction_bias="bullish") -> dict:
    """A build_feature_snapshot()-shaped dict carrying just the fields
    ml_advisor.extract_features() reads - enough to test it in isolation
    without a real build_feature_snapshot() call.
    """
    return {
        "primary_indicators": {
            "close": 2000.0, "ema20": 2001.0, "ema50": 1998.0, "ema200": 1990.0,
            "rsi14": rsi14, "macd_hist": 0.5, "macd_hist_prev": 0.4,
            "macd_hist_shape": {"declining_from_peak": False},
            "adx14": adx14, "plus_di": 30.0, "minus_di": 15.0,
            "stoch_k": 55.0, "stoch_d": 50.0, "atr14": 1.2,
            "bollinger_percent_b": 0.6, "bollinger_bandwidth": 0.02,
        },
        "trend_bias": {"close": 2000.0, "ema200": 1980.0},
        "smc": {
            "liquidity_sweep": {"swept": True},
            "premium_discount": {"position_pct": 0.3},
            "market_structure": {"trend": direction_bias, "last_event": {"type": "BOS"}},
            "order_blocks": {"bullish_order_block": {"price_inside_zone": True},
                             "bearish_order_block": None},
            "fair_value_gaps": [{"direction": "bullish"}],
        },
        "last_closed_candle": {"body_pct_of_range": 0.7, "bullish": True},
        "session": {"hour_utc": 13, "session_overlap": True},
        "recent_performance": {"trade_count": 5, "win_rate_pct": 60.0},
        "dxy": {"vs_ema20": "below", "change_pct_last_10_bars": -0.5},
        "consensus": {"other_system_buy_positions": 2, "other_system_sell_positions": 0},
    }


def test_ml_advisor() -> bool:
    print("\n=== 18. ml_advisor: feature extraction, snapshot logging, offline training ===")
    ok = True
    import tempfile

    snap = make_synthetic_ml_snapshot()
    features = ml_advisor.extract_features(snap)
    ok &= check("extract_features() reads through every nested section correctly",
                features["rsi14"] == 60.0 and features["adx14"] == 30.0
                and features["liquidity_swept"] == 1.0 and features["structure_bullish"] == 1.0
                and features["price_inside_bullish_ob"] == 1.0 and features["bos_event"] == 1.0
                and features["dxy_above_ema20"] == 0.0 and features["consensus_buy_positions"] == 2.0,
                features)
    ok &= check("direction_is_buy defaults to 0.0 when no direction is passed",
                features["direction_is_buy"] == 0.0, features["direction_is_buy"])

    buy_features = ml_advisor.extract_features(snap, "buy")
    sell_features = ml_advisor.extract_features(snap, "sell")
    ok &= check("extract_features(snap, 'buy') sets direction_is_buy=1.0, "
                "extract_features(snap, 'sell') sets it 0.0 - the SAME market state scored per "
                "candidate direction, everything else about the vector unchanged",
                buy_features["direction_is_buy"] == 1.0 and sell_features["direction_is_buy"] == 0.0
                and {k: v for k, v in buy_features.items() if k != "direction_is_buy"}
                == {k: v for k, v in sell_features.items() if k != "direction_is_buy"},
                (buy_features, sell_features))

    empty_features = ml_advisor.extract_features({})
    ok &= check("extract_features({}) never raises and returns every FEATURE_NAMES key with a "
                "neutral default, the same convention as every other optional context source",
                set(empty_features.keys()) == set(ml_advisor.FEATURE_NAMES), empty_features)

    with tempfile.TemporaryDirectory() as tmp:
        cfg = AdvisorConfig(log_dir=tmp, magic=999)

        ml_advisor.log_snapshot(cfg, snap, "buy", "1001")
        rows = ml_advisor._load_snapshots(cfg)
        ok &= check("log_snapshot() appends a row keyed by ticket/direction plus every feature",
                    len(rows) == 1 and rows[0]["ticket"] == "1001" and rows[0]["direction"] == "buy"
                    and float(rows[0]["rsi14"]) == 60.0, rows)

        ok &= check("win_probability_context() is None before any local model has been trained",
                    ml_advisor.win_probability_context(cfg, snap) is None)

        try:
            import joblib  # noqa: F401
            import sklearn  # noqa: F401
        except ImportError as exc:
            print(f"  [SKIP] scikit-learn/joblib not installed - training/scoring checks skipped ({exc})")
            ok &= check("without scikit-learn, train_model() declines gracefully instead of raising",
                        ml_advisor.train_model(cfg, gateway=FakePerformanceGateway([]))["trained"] is False)
            return ok

        no_data_cfg = AdvisorConfig(log_dir=os.path.join(tmp, "empty"), magic=999)
        no_data_result = ml_advisor.train_model(no_data_cfg, gateway=FakePerformanceGateway([]))
        ok &= check("train_model() gracefully declines rather than raising when no snapshots have "
                    "been logged yet",
                    no_data_result == {"trained": False, "waiting": True,
                                        "reason": "no logged snapshots yet (logs/ml_snapshots.csv is "
                                                  "empty or missing) - needs at least one executed "
                                                  "trade first."},
                    no_data_result)

        # Log enough synthetic labeled trades to clear min_samples, alternating
        # win/loss (a classifier needs both classes) with a FakePerformanceGateway
        # wired to matching tickets/P&L for train_model()'s MT5-history join -
        # the exact same ticket-based join calibration_report.py already uses.
        fake_trades = []
        for i in range(20):
            ticket = str(2000 + i)
            win = i % 2 == 0
            snap_i = make_synthetic_ml_snapshot(rsi14=65.0 if win else 35.0,
                                                adx14=32.0 if win else 18.0,
                                                direction_bias="bullish" if win else "bearish")
            ml_advisor.log_snapshot(cfg, snap_i, "buy" if win else "sell", ticket)
            fake_trades.append({"ticket": ticket, "pnl_dollars": 5.0 if win else -5.0})

        result = ml_advisor.train_model(cfg, gateway=FakePerformanceGateway(fake_trades), min_samples=10)
        ok &= check("train_model() trains successfully once enough labeled (real-P&L) trades exist",
                    result.get("trained") is True and result["n_samples"] == 20, result)
        ok &= check("it reports OUT-OF-SAMPLE (cross-validated) accuracy next to the base rate, not "
                    "an in-sample score that is ~100% for any boosted model",
                    0.0 <= result["cv_accuracy"] <= 1.0 and result["base_rate"] == 0.5, result)
        ok &= check("the trained model is persisted to disk",
                    os.path.exists(ml_advisor._model_path(cfg)), ml_advisor._model_path(cfg))

        context = ml_advisor.win_probability_context(
            cfg, make_synthetic_ml_snapshot(rsi14=65.0, adx14=32.0, direction_bias="bullish"))
        ok &= check("win_probability_context() scores a snapshot once per candidate direction "
                    "once a model is trained",
                    context is not None and 0.0 <= context["win_probability_pct_buy"] <= 100.0
                    and 0.0 <= context["win_probability_pct_sell"] <= 100.0
                    and context["trained_on_n_trades"] == 20
                    and context["base_rate_pct"] == 50.0 and "cv_accuracy_pct" in context, context)

        under_min_cfg = AdvisorConfig(log_dir=os.path.join(tmp, "under"), magic=999)
        ml_advisor.log_snapshot(under_min_cfg, snap, "buy", "9001")
        under_result = ml_advisor.train_model(
            under_min_cfg, gateway=FakePerformanceGateway([{"ticket": "9001", "pnl_dollars": 5.0}]),
            min_samples=30)
        ok &= check("train_model() declines rather than training on too few labeled trades",
                    under_result == {"trained": False, "waiting": True,
                                      "reason": "only 1 labeled trade(s) with real MT5 P&L so far - "
                                                "need at least 30 before training a useful model."},
                    under_result)

    return ok

# --------------------------------------------------------------------------- #
# econ_calendar.py: MT5's calendar, exported by the MQL5 EA
# --------------------------------------------------------------------------- #
SAMPLE_CALENDAR = """# exported_at_utc=2026-10-02 12:25:00
time_utc,currency,importance,event,actual,forecast,previous,impact
2026-10-02 08:00:00,USD,moderate,"ISM Manufacturing PMI",49.1,50.2,48.7,negative
2026-10-02 10:00:00,USD,high,"JOLTS, Job Openings",8.9,8.1,8.0,positive
2026-10-02 11:00:00,EUR,high,"ECB Rate Decision",3.5,3.5,3.75,na
2026-10-02 12:30:00,USD,high,"Nonfarm Payrolls",,180,150,na
2026-10-02 14:00:00,USD,low,"Baker Hughes Rig Count",,,480,na
not-a-date,USD,high,"broken row",,,,na
"""


class FakeCalendarGateway:
    def __init__(self, text=None, fail=False, now=None):
        self.text, self.fail, self._now = text, fail, now

    def read_common_file(self, filename):
        if self.fail:
            raise RuntimeError("terminal_info() failed")
        return self.text

    def now(self):
        return self._now


def test_econ_calendar() -> bool:
    print("\n=== 19. econ_calendar: MT5 calendar export -> blackout + Claude context ===")
    ok = True
    utc = timezone.utc
    events, exported_at = econ_calendar.parse_calendar_csv(SAMPLE_CALENDAR)
    ok &= check("the export parses, skipping the malformed row, oldest first",
                len(events) == 5 and events[0].event == "ISM Manufacturing PMI"
                and events[-1].event == "Baker Hughes Rig Count", [e.event for e in events])
    ok &= check("exported_at_utc is read from the comment line",
                exported_at == datetime(2026, 10, 2, 12, 25, tzinfo=utc), exported_at)
    nfp = events[3]
    ok &= check("an unreleased event has actual=None but keeps forecast/previous",
                nfp.actual is None and nfp.forecast == 180.0 and nfp.previous == 150.0, nfp)
    ok &= check("gold impact: a USD-positive surprise is bearish for gold, USD-negative bullish",
                "bearish" in econ_calendar.gold_impact(events[1])
                and "bullish" in econ_calendar.gold_impact(events[0])
                and econ_calendar.gold_impact(events[2]) == "", None)

    cfg = AdvisorConfig()
    at = lambda h, m: datetime(2026, 10, 2, h, m, tzinfo=utc)
    ok &= check("NFP at 12:30 blocks new entries from 12:15...",
                "Nonfarm Payrolls" in econ_calendar.blackout_reason(events, at(12, 15), cfg),
                econ_calendar.blackout_reason(events, at(12, 15), cfg))
    ok &= check("...through 12:45 (15 minutes each side by default)",
                econ_calendar.blackout_reason(events, at(12, 45), cfg) != ""
                and econ_calendar.blackout_reason(events, at(12, 46), cfg) == ""
                and econ_calendar.blackout_reason(events, at(12, 14), cfg) == "")
    ok &= check("a high-impact EUR event doesn't block (only news_currencies=['USD'] is watched)",
                econ_calendar.blackout_reason(events, at(11, 0), cfg) == "")
    ok &= check("a moderate USD event doesn't block at the default 'high' threshold...",
                econ_calendar.blackout_reason(events, at(8, 0), cfg) == "")
    ok &= check("...but does at news_min_importance='moderate'",
                econ_calendar.blackout_reason(events, at(8, 0),
                                              AdvisorConfig(news_min_importance="moderate")) != "")
    ok &= check("news_auto_blackout=False never blocks",
                econ_calendar.blackout_reason(events, at(12, 30),
                                              AdvisorConfig(news_auto_blackout=False)) == "")
    raised = None
    try:
        econ_calendar.blackout_reason(events, at(12, 30), AdvisorConfig(news_min_importance="hihg"))
    except ValueError as exc:
        raised = exc
    ok &= check("a typo in news_min_importance raises instead of silently meaning something else",
                raised is not None, raised)

    ctx = econ_calendar.calendar_context(events, exported_at, at(12, 25), cfg)
    ok &= check("context lists recent USD releases (moderate+) with surprise and gold impact",
                [r["event"] for r in ctx["recent_releases"]] == ["ISM Manufacturing PMI",
                                                                 "JOLTS, Job Openings"]
                and abs(ctx["recent_releases"][1]["surprise"] - 0.8) < 1e-9
                and "bearish" in ctx["recent_releases"][1]["gold_impact"], ctx["recent_releases"])
    ok &= check("upcoming: NFP in 5 minutes (the low-impact rig count and EUR event left out)",
                [u["event"] for u in ctx["upcoming_24h"]] == ["Nonfarm Payrolls"]
                and ctx["upcoming_24h"][0]["minutes_until"] == 5
                and ctx["minutes_to_next_blackout_event"] == 5, ctx["upcoming_24h"])
    ok &= check("data age is reported from exported_at", ctx["data_age_minutes"] == 0, ctx)

    ok &= check("load_events: no read_common_file on the gateway (backtest) -> None",
                econ_calendar.load_events(object(), cfg) is None)
    ok &= check("load_events: file not exported yet -> None",
                econ_calendar.load_events(FakeCalendarGateway(None), cfg) is None)
    ok &= check("load_events: read error -> None, never raises",
                econ_calendar.load_events(FakeCalendarGateway(fail=True), cfg) is None)
    ok &= check("load_events: econ_calendar_filename='' disables it",
                econ_calendar.load_events(FakeCalendarGateway(SAMPLE_CALENDAR),
                                          AdvisorConfig(econ_calendar_filename="")) is None)

    # Through the real gate: a full-conviction verdict inside the NFP window
    # is refused; the same verdict outside it isn't.
    class GateGateway(FakeGateway):
        def __init__(self, now):
            super().__init__(now=now)

        def read_common_file(self, filename):
            return SAMPLE_CALENDAR

    reason_in = executor.gate(GateGateway(at(12, 20)), cfg, make_verdict("buy", 3, "full"), 0)
    reason_out = executor.gate(GateGateway(at(13, 0)), cfg, make_verdict("buy", 3, "full"), 0)
    ok &= check("executor.gate() refuses an entry inside the calendar blackout",
                reason_in.startswith("news blackout") and "Nonfarm" in reason_in, reason_in)
    ok &= check("...and allows the same entry outside it", reason_out == "", reason_out)

    snap_ctx = market_intel.economic_calendar_context(GateGateway(at(12, 25)), cfg)
    ok &= check("market_intel.economic_calendar_context() builds the same context from the gateway",
                snap_ctx is not None and snap_ctx["minutes_to_next_blackout_event"] == 5, snap_ctx)
    return ok



# --------------------------------------------------------------------------- #
# breaking-news check (fake feeds + fake Claude client - no network)
# --------------------------------------------------------------------------- #
NEWS_NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Google News</title>
<item><title>Gold jumps as Fed signals surprise emergency cut - Reuters</title>
  <link>https://example.com/1</link><pubDate>Wed, 23 Sep 2026 11:30:00 GMT</pubDate>
  <source url="https://reuters.com">Reuters</source></item>
<item><title>Dollar slides after missile strike near Gulf shipping lane - Bloomberg</title>
  <pubDate>Wed, 23 Sep 2026 11:45:00 GMT</pubDate><source url="x">Bloomberg</source></item>
<item><title>Celebrity chef opens new restaurant</title>
  <pubDate>Wed, 23 Sep 2026 11:50:00 GMT</pubDate></item>
<item><title>Gold steady ahead of payrolls - old story</title>
  <pubDate>Wed, 23 Sep 2026 06:00:00 GMT</pubDate></item>
<item><title>No date on this gold headline</title></item>
</channel></rss>"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>FX</title>
<entry><title>Treasury yields spike on tariff announcement</title>
  <updated>2026-09-23T11:55:00Z</updated></entry>
<entry><title>Gold jumps as Fed signals surprise emergency cut - Reuters</title>
  <updated>2026-09-23T11:31:00Z</updated></entry>
</feed>"""


class _NewsBlock:
    def __init__(self, type_, text=""):
        self.type, self.text = type_, text


class _NewsResponse:
    def __init__(self, blocks, stop_reason="end_turn", searches=None):
        from types import SimpleNamespace
        self.content = blocks
        self.stop_reason = stop_reason
        self.usage = SimpleNamespace(server_tool_use=(SimpleNamespace(web_search_requests=searches)
                                                      if searches is not None else None))


class FakeNewsClient:
    """client.messages.create(**kw) replays `script` (a response or an
    exception per call) and records every call's kwargs."""
    def __init__(self, script):
        from types import SimpleNamespace
        self.script = list(script)
        self.calls = []
        self.messages = self
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _news_json(**overrides):
    base = {"surprise_news": False, "severity": "none", "impact_on_trade": "neutral",
            "block_trade": False, "headline": "", "summary": "Nothing unscheduled in the window."}
    base.update(overrides)
    return json.dumps(base)


def test_breaking_news_check() -> bool:
    print("\n=== 29. breaking-news check before entry (fake feeds + fake Claude, no network) ===")
    ok = True

    rss = news_check.parse_feed(SAMPLE_RSS)
    ok &= check("RSS 2.0 items parse with title, source and UTC time",
                len(rss) == 5 and rss[0].source == "Reuters"
                and rss[0].published_utc == datetime(2026, 9, 23, 11, 30, tzinfo=timezone.utc), rss[:1])
    atom = news_check.parse_feed(SAMPLE_ATOM)
    ok &= check("Atom entries parse too (namespaced tags, ISO timestamps)",
                len(atom) == 2 and atom[0].published_utc == datetime(2026, 9, 23, 11, 55, tzinfo=timezone.utc),
                atom)
    ok &= check("unparseable XML returns [] instead of raising", news_check.parse_feed("<rss><oops") == [])

    cfg = AdvisorConfig(news_feeds=["rss://a", "atom://b", "dead://c"], news_check_lookback_minutes=180,
                        news_check_web_search=True)
    feeds = {"rss://a": SAMPLE_RSS, "atom://b": SAMPLE_ATOM}

    def fetcher(url):
        if url not in feeds:
            raise OSError("feed down")
        return feeds[url]

    errors = []
    heads = news_check.fetch_headlines(cfg, NEWS_NOW, fetcher, errors)
    titles = [h.title for h in heads]
    ok &= check("headlines are filtered to the lookback window and to gold/dollar keywords, "
                "deduplicated across feeds, newest first",
                titles == ["Treasury yields spike on tariff announcement",
                           "Dollar slides after missile strike near Gulf shipping lane - Bloomberg",
                           "Gold jumps as Fed signals surprise emergency cut - Reuters"], titles)
    ok &= check("a dead feed is recorded and skipped, never raised", len(errors) == 1 and "feed down" in errors[0],
                errors)

    ok &= check("extract_json_object picks the LAST verdict object, ignoring braces in the prose",
                news_check.extract_json_object('I checked {Reuters}. {"a": 1} Then: {"block_trade": false,'
                                               ' "x": {"y": 2}}') == {"block_trade": False, "x": {"y": 2}})

    # 1) Clear, via web search.
    client = FakeNewsClient([_NewsResponse([_NewsBlock("server_tool_use"),
                                            _NewsBlock("web_search_tool_result"),
                                            _NewsBlock("text", "Searched gold and Fed news. "),
                                            _NewsBlock("text", _news_json())], searches=2)])
    r = news_check.check_before_trade(client, cfg, "buy", 2650.35, now=NEWS_NOW, fetcher=fetcher)
    call = client.calls[0]
    ok &= check("a clear verdict does not block, and reports its sources",
                r.ran and r.block_reason == "" and r.used_web_search and r.headline_count == 3
                and r.note.startswith("clear") and "2 web searches + 3 headlines" in r.note, r.note)
    ok &= check("the web search server tool is passed, and the headlines + trade reach Claude",
                call["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}]
                and call.get("fallbacks") == "default"
                and '"entry_direction": "buy"' in call["messages"][0]["content"]
                and "Treasury yields spike" in call["messages"][0]["content"]
                and "180 minutes" in call["system"], call.get("tools"))

    # 2) pause_turn continuation.
    client = FakeNewsClient([
        _NewsResponse([_NewsBlock("server_tool_use"), _NewsBlock("text", "Still searching...")],
                      stop_reason="pause_turn", searches=1),
        _NewsResponse([_NewsBlock("text", _news_json())], searches=1)])
    r = news_check.check_before_trade(client, cfg, "sell", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("a pause_turn response is continued (assistant turn sent back) until Claude finishes",
                len(client.calls) == 2 and client.calls[1]["messages"][-1]["role"] == "assistant"
                and r.ran and r.block_reason == "", [c["messages"][-1]["role"] for c in client.calls])

    # 3) Blocking news.
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json(
        surprise_news=True, severity="high", impact_on_trade="against", block_trade=True,
        headline="Fed emergency hike", summary="Surprise 50bp emergency hike - dollar spiking."))],
        searches=1)])
    r = news_check.check_before_trade(client, cfg, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("high-severity news against the trade blocks it, naming the story",
                "breaking news (high, against): Fed emergency hike" == r.block_reason
                and r.note.startswith("BLOCKED"), r.block_reason)

    # 4) Mechanical override: Claude forgot block_trade on a high/against surprise.
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json(
        surprise_news=True, severity="high", impact_on_trade="against", block_trade=False,
        headline="Ceasefire announced"))], searches=1)])
    r = news_check.check_before_trade(client, cfg, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("a high-severity surprise against the trade blocks even if block_trade came back false",
                r.block_reason.startswith("breaking news (high, against)"), r.block_reason)

    # 5) Supportive medium news: trades, but the note says so.
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json(
        surprise_news=True, severity="medium", impact_on_trade="supports",
        summary="Missile strike lifts safe-haven demand."))], searches=1)])
    r = news_check.check_before_trade(client, cfg, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("supportive surprise news does not block, and the note describes it",
                r.block_reason == "" and r.note.startswith("medium surprise, supports this trade"), r.note)

    # 6) Web search refused -> headline-only retry.
    client = FakeNewsClient([RuntimeError("web search is not enabled for this organization"),
                             _NewsResponse([_NewsBlock("text", _news_json())])])
    r = news_check.check_before_trade(client, cfg, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("if the web-search call fails, it retries once WITHOUT tools on the headlines alone",
                len(client.calls) == 2 and "tools" not in client.calls[1] and r.ran
                and not r.used_web_search and "3 headlines" in r.note
                and any("not enabled" in e for e in r.errors), (r.note, r.errors))

    # 7) Everything fails: fail-open vs fail-closed.
    boom = [RuntimeError("Claude unreachable"), RuntimeError("Claude unreachable")]
    r = news_check.check_before_trade(FakeNewsClient(list(boom)), cfg, "buy", 2650.0, now=NEWS_NOW,
                                      fetcher=fetcher)
    ok &= check("with no check possible, fail-open (default) trades and says so",
                not r.ran and r.block_reason == "" and r.note.startswith("unavailable"), r.note)
    cfg_closed = AdvisorConfig(news_feeds=cfg.news_feeds, news_check_fail_closed=True,
                               news_check_web_search=True)
    r = news_check.check_before_trade(FakeNewsClient(list(boom)), cfg_closed, "buy", 2650.0,
                                      now=NEWS_NOW, fetcher=fetcher)
    ok &= check("news_check_fail_closed=True refuses the entry instead",
                r.block_reason.startswith("breaking-news check unavailable"), r.block_reason)

    # 8) No headlines + web search failing -> no blind headline-only call.
    cfg_nofeeds = AdvisorConfig(news_feeds=[], news_check_web_search=True)
    client = FakeNewsClient([RuntimeError("web search disabled")])
    r = news_check.check_before_trade(client, cfg_nofeeds, "buy", 2650.0, now=NEWS_NOW)
    ok &= check("with no headlines, a failed web search is NOT followed by a blind call with nothing to read",
                len(client.calls) == 1 and not r.ran and r.block_reason == "", len(client.calls))

    # 9) Web search answered without searching and no headlines -> can't count as checked.
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json())], searches=0)])
    r = news_check.check_before_trade(client, cfg_nofeeds, "buy", 2650.0, now=NEWS_NOW)
    ok &= check("a verdict with zero searches and zero headlines is treated as 'unavailable', not 'clear'",
                not r.ran and r.note.startswith("unavailable"), r.note)

    # 10) Malformed verdict.
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json(severity="extreme"))], searches=1)])
    r = news_check.check_before_trade(client, cfg, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("a verdict with an invalid field value is treated as unavailable, never as clear",
                not r.ran and "unreadable verdict" in r.note, r.note)

    # 11) Switched off.
    client = FakeNewsClient([])
    r = news_check.check_before_trade(client, AdvisorConfig(breaking_news_check=False), "buy", 2650.0)
    ok &= check("breaking_news_check=False makes no call and never blocks",
                client.calls == [] and r.block_reason == "" and not r.ran)

    cfg_noweb = AdvisorConfig(news_feeds=cfg.news_feeds, news_check_model="claude-sonnet-5")
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json())])])
    r = news_check.check_before_trade(client, cfg_noweb, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("DEFAULT is free news only: no web search tool is sent, the free headlines are judged; "
                "news_check_model overrides the model",
                not AdvisorConfig().news_check_web_search and "tools" not in client.calls[0]
                and client.calls[0]["model"] == "claude-sonnet-5" and r.ran
                and r.note == "clear - no surprise news [3 headlines]", (client.calls[0].get("model"), r.note))
    client = FakeNewsClient([])
    r = news_check.check_before_trade(client, AdvisorConfig(news_feeds=["dead://x"]), "buy", 2650.0,
                                      now=NEWS_NOW, fetcher=fetcher)
    ok &= check("free-only with every feed down makes NO Claude call and reports unavailable (fail-open)",
                client.calls == [] and not r.ran and r.block_reason == "" and r.note.startswith("unavailable"),
                r.note)
    defaults = AdvisorConfig().news_feeds
    ok &= check("the default free feeds are several independent https publishers",
                len(defaults) >= 4 and all(u.startswith("https://") for u in defaults)
                and len({u.split("/")[2] for u in defaults}) >= 3, defaults)

    # --- hardening against hostile / broken feeds ---
    bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
            '<rss><channel><item><title>gold &lol2;</title><pubDate>Wed, 23 Sep 2026 11:30:00 GMT'
            '</pubDate></item></channel></rss>')
    ok &= check("XML declaring entities (billion-laughs vector) is refused", news_check.parse_feed(bomb) == [])
    doctype_only = ('<?xml version="1.0"?><!DOCTYPE rss PUBLIC "-//Netscape Communications//DTD RSS 0.91//EN" '
                    '"http://my.netscape.com/publish/formats/rss-0.91.dtd"><rss><channel><item>'
                    '<title>Gold rallies</title><pubDate>Wed, 23 Sep 2026 11:30:00 GMT</pubDate></item>'
                    '</channel></rss>')
    ok &= check("a plain DOCTYPE line (old RSS 0.91) still parses - no external DTD is fetched",
                [h.title for h in news_check.parse_feed(doctype_only)] == ["Gold rallies"])
    long_rss = SAMPLE_RSS.replace("Gold jumps as Fed", "Gold " + "x" * 5000 + " jumps as Fed")
    ok &= check("titles are whitespace-collapsed and capped (limits prompt size and injected text)",
                max(len(h.title) for h in news_check.parse_feed(long_rss)) == news_check.MAX_TITLE_CHARS)
    try:
        news_check._http_get("file:///etc/passwd")
        ok &= check("a non-http(s) feed URL is refused", False)
    except ValueError:
        ok &= check("a non-http(s) feed URL (file://...) is refused before any read", True)

    class _BigResp:
        def __init__(self, n):
            self.n = n

        def read(self, limit=-1):
            return b"x" * (self.n if limit < 0 else min(self.n, limit))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    import urllib.request as _ur
    real_urlopen = _ur.urlopen
    try:
        _ur.urlopen = lambda req, timeout=None: _BigResp(news_check.MAX_FEED_BYTES + 50)
        try:
            news_check._http_get("https://example.com/huge.xml")
            ok &= check("an oversized feed is refused", False)
        except ValueError:
            ok &= check("an oversized feed is refused after reading at most the cap (+1 byte)", True)
        _ur.urlopen = lambda req, timeout=None: _BigResp(1000)
        ok &= check("a normal-size feed downloads", len(news_check._http_get("https://example.com/ok.xml")) == 1000)
    finally:
        _ur.urlopen = real_urlopen

    import threading as _th
    release = _th.Event()

    def stalling_fetcher(url):
        if url == "slow://x":
            release.wait(5)      # a server dripping bytes forever
            return SAMPLE_RSS
        return fetcher(url)
    t0 = datetime.now()
    got = news_check._download_all(["rss://a", "slow://x"], stalling_fetcher, deadline=0.3)
    elapsed = (datetime.now() - t0).total_seconds()
    release.set()
    ok &= check("an overall deadline caps the download step; a stalled feed is reported, the rest kept",
                elapsed < 2 and got[0][1] == SAMPLE_RSS and "timed out" in got[1][2], (elapsed, got[1][2]))

    rows = news_check.feed_report(cfg, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("feed_report: one row per feed, in order, with items/relevant counts and the dead feed's error",
                [r["ok"] for r in rows] == [True, True, False] and rows[0]["items"] == 5
                and rows[0]["relevant_recent"] == 2 and rows[0]["newest_minutes_ago"] == 10
                and "feed down" in rows[2]["error"], rows)
    return ok



class FakeRunOnceGateway(FakeGateway):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.files = {}

    def write_common_file(self, name, text):
        self.files[name] = text


def _run_once_wiring(spec) -> bool:
    """main.run_once() end to end with every external piece faked: the news
    check gets the live plan, a block reaches the alert, and a clear check
    lets the order through with the levels in the alert."""
    import threading
    ok = True
    verdict = make_verdict("buy", 3, "full")
    verdict.take_profit_targets = [2365.0]
    originals = (main_mod.gw, main_mod.market_intel.build_feature_snapshot,
                 main_mod.claude_advisor.get_verdict, main_mod.news_check.check_before_trade,
                 main_mod.telegram_alert.send_alert, main_mod.claude_paused)
    sent, news_calls = [], []
    try:
        main_mod.market_intel.build_feature_snapshot = lambda g, c: {"fake": True}
        main_mod.claude_advisor.get_verdict = lambda client, c, f: verdict
        main_mod.telegram_alert.send_alert = lambda token, chat, text, **kw: sent.append(text) or True
        main_mod.claude_paused = lambda c, gateway=None: False
        for blocked in (True, False):
            sent.clear()
            fake = FakeRunOnceGateway(bid=2350.0, ask=2350.2)
            main_mod.gw = fake

            def fake_news(client, c, direction, price, **kw):
                news_calls.append((direction, price))
                return news_check.NewsCheckResult(
                    ran=True, block_reason="breaking news (high, against): tariff shock" if blocked else "",
                    note="BLOCKED - tariff shock" if blocked else "clear - no surprise news [1 web search]")
            main_mod.news_check.check_before_trade = fake_news
            cfg = AdvisorConfig(dry_run=True, use_risk_percent=False, log_dir="/tmp/claudesmc_selftest_logs",
                                telegram_alert_bot_token="T", telegram_alert_chat_id="C",
                                claude_pause_filename="", send_performance_digest=False)
            day = main_mod.DayRoll()
            main_mod.run_once(object(), cfg, spec, day)
            for t in threading.enumerate():
                if t is not threading.current_thread() and t.daemon:
                    t.join(timeout=2)
            msg = sent[-1] if sent else ""
            if blocked:
                ok &= check("run_once: a news block stops the order, and the alert shows why plus the levels",
                            fake.orders_sent == [] and "NOT executed - breaking news (high, against): "
                            "tariff shock" in msg and "SL: 2344.20" in msg and "News check: BLOCKED" in msg
                            and news_calls[-1] == ("buy", 2350.2) and day.trades_today == 0, msg)
            else:
                ok &= check("run_once: a clear check sends the order; the alert has entry, SL, TP1, TP2 "
                            "and the news result",
                            len(fake.orders_sent) == 1 and "EXECUTED (dry-run" in msg
                            and "Entry: 2350.20" in msg and "TP1: 2356.20" in msg and "TP2: 2365.00" in msg
                            and "News check: clear" in msg and day.trades_today == 1, msg)

        # Anything failing AFTER the order (here the alert text) must not make
        # run_once raise: main() would retry the bar and could order again.
        fake = FakeRunOnceGateway(bid=2350.0, ask=2350.2)
        main_mod.gw = fake
        main_mod.news_check.check_before_trade = lambda *a, **kw: news_check.NewsCheckResult(ran=True)
        broken = main_mod.telegram_alert.format_full_conviction_message
        main_mod.telegram_alert.format_full_conviction_message = lambda *a, **kw: 1 / 0
        day = main_mod.DayRoll()
        try:
            main_mod.run_once(object(), cfg, spec, day)
            raised = False
        except Exception:
            raised = True
        finally:
            main_mod.telegram_alert.format_full_conviction_message = broken
        ok &= check("run_once: an error after the order is logged, never raised (no retry -> no second "
                    "order), and the trade is counted",
                    not raised and len(fake.orders_sent) == 1 and day.trades_today == 1)
    finally:
        (main_mod.gw, main_mod.market_intel.build_feature_snapshot, main_mod.claude_advisor.get_verdict,
         main_mod.news_check.check_before_trade, main_mod.telegram_alert.send_alert,
         main_mod.claude_paused) = originals
    return ok

def test_entry_levels_and_alert() -> bool:
    print("\n=== 30. entry levels, pre-trade check wiring and the full-conviction alert ===")
    ok = True
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0,
                         spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                         tick_value=1.0, tick_size=0.01)
    cfg = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs", use_risk_percent=False)

    fg = FakeGateway(bid=2350.0, ask=2350.2)
    plan = executor.build_plan(fg, cfg, spec, "buy")
    ok &= check("build_plan: buy at ask, SL $6 / TP1 $6 / trail $3 at the 0.01 reference lot",
                abs(plan.entry_price - 2350.2) < 1e-9 and abs(plan.sl_price - 2344.2) < 1e-9
                and abs(plan.tp1_price - 2356.2) < 1e-9 and abs(plan.trail_distance - 3.0) < 1e-9
                and plan.broker_tp == 0.0 and abs(plan.risk_money - 6.0) < 1e-9, plan)
    sell = executor.build_plan(fg, cfg, spec, "sell")
    ok &= check("build_plan: sell at bid mirrors the levels",
                abs(sell.sl_price - 2356.0) < 1e-9 and abs(sell.tp1_price - 2344.0) < 1e-9, sell)

    seen = []

    def blocking_check(direction, p):
        seen.append((direction, p.entry_price))
        return "breaking news (high, against): Fed emergency hike", "BLOCKED - Fed emergency hike"

    fg_block = FakeGateway()
    d = executor.execute(fg_block, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0,
                         pre_trade_check=blocking_check)
    ok &= check("a blocking pre-trade check stops the order and is the reject reason",
                not d.executed and fg_block.orders_sent == [] and d.reject_reason.startswith("breaking news")
                and d.news_note.startswith("BLOCKED") and d.plan is not None and seen == [("buy", 2350.2)],
                (d, seen))

    fg_moving = FakeGateway(bid=2350.0, ask=2350.2)

    def clear_check_price_moves(direction, p):
        fg_moving.bid, fg_moving.ask = 2352.0, 2352.2   # price moved during the check
        return "", "clear - no surprise news [1 web search]"

    d = executor.execute(fg_moving, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0,
                         pre_trade_check=clear_check_price_moves)
    ok &= check("after a clear check the entry is RE-PRICED from a fresh tick (SL still $6 from the new price)",
                d.executed and abs(fg_moving.orders_sent[0][2] - 2346.2) < 1e-9
                and abs(d.plan.tp1_price - 2358.2) < 1e-9 and d.news_note.startswith("clear"),
                fg_moving.orders_sent)

    calls = []
    d = executor.execute(FakeGateway(), cfg, make_verdict("buy", 2, "partial"), spec, trades_today=0,
                         pre_trade_check=lambda *a: calls.append(a) or ("", ""))
    ok &= check("the (paid) news check never runs for a verdict the other gates already reject",
                not d.executed and calls == [], calls)

    verdict = make_verdict("buy", 3, "full")
    verdict.take_profit_targets = [2353.0, 2361.5, 2370.0, 2390.0]
    msg = telegram_alert.format_full_conviction_message(
        "XAUUSD", verdict, executed=True, plan=plan, news_note="clear - no surprise news [2 web searches]",
        dry_run=True)
    ok &= check("the alert shows direction, entry, SL and TP1 with money, then the trail",
                "BUY XAUUSD" in msg and "Entry: 2350.20 (market, 0.01 lot)" in msg
                and "SL: 2344.20 (risk $6.00)" in msg
                and "TP1: 2356.20 - stop moves here to lock +$6.00, then trails 3.00 behind price" in msg, msg)
    ok &= check("Claude's structure targets beyond TP1 become TP2/TP3 (a level short of TP1 is not a TP)",
                "TP2: 2361.50" in msg and "TP3: 2370.00" in msg and "2353.00" not in msg
                and "TP4" not in msg, msg)
    ok &= check("dry-run and the news check result are spelled out",
                "EXECUTED (dry-run - no real order sent)" in msg
                and "News check: clear - no surprise news [2 web searches]" in msg, msg)
    sell_verdict = make_verdict("sell", 3, "full")
    sell_verdict.take_profit_targets = [2340.0, 2346.0, 2330.0]
    sell_msg = telegram_alert.format_full_conviction_message("XAUUSD", sell_verdict, False,
                                                             "already 5 open sell position(s) (max 5)",
                                                             plan=sell)
    ok &= check("sell targets are filtered below TP1, nearest first; a rejection still shows its levels",
                "TP2: 2340.00" in sell_msg and "TP3: 2330.00" in sell_msg and "2346.00" not in sell_msg
                and "SL: 2356.00" in sell_msg and "NOT executed - already 5" in sell_msg, sell_msg)

    cfg_fixed = AdvisorConfig(dry_run=True, use_risk_percent=False, exit_style="fixed_tp")
    fixed_plan = executor.build_plan(FakeGateway(), cfg_fixed, spec, "buy")
    fixed_msg = telegram_alert.format_full_conviction_message("XAUUSD", verdict, True, plan=fixed_plan)
    ok &= check("exit_style=fixed_tp shows TP1 as a real broker take-profit, and no TP2/TP3 beyond it",
                abs(fixed_plan.broker_tp - 2356.2) < 1e-9 and "TP1: 2356.20 broker take-profit" in fixed_msg
                and "TP2" not in fixed_msg,
                fixed_msg)

    try:
        from anthropic.lib._parse._transform import transform_schema
        required = transform_schema(ConfluenceVerdict).get("required", [])
        ok &= check("take_profit_targets is REQUIRED in the schema Claude receives",
                    "take_profit_targets" in required, required)
    except ImportError:
        print("  [SKIP] anthropic not installed - schema check skipped")
    ok &= check("...but optional in Python (mechanical/backtest verdicts don't set it)",
                make_verdict().take_profit_targets == [])

    ok &= _run_once_wiring(spec)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # trades.csv/decisions.csv "locked" (a directory can't be opened for
        # append - the same OSError family as Excel's lock on Windows).
        os.makedirs(os.path.join(tmp, "trades.csv"))
        os.makedirs(os.path.join(tmp, "decisions.csv"))
        cfg_locked = AdvisorConfig(dry_run=False, use_risk_percent=False, log_dir=tmp)
        fg_locked = FakeGateway()
        try:
            d = executor.execute(fg_locked, cfg_locked, make_verdict("buy", 3, "full"), spec, trades_today=0)
            raised = None
        except Exception as exc:
            raised, d = exc, None
        ok &= check("a locked trades.csv/decisions.csv (e.g. open in Excel) never aborts execute() after "
                    "the order - it is reported executed exactly once, so main.py can't resend it",
                    raised is None and d.executed and len(fg_locked.orders_sent) == 1, raised or d)

    posted = []

    def invalid_url_poster(url, payload):
        raise ValueError(f"URL can't contain control characters. {url!r}")
    telegram_alert.send_alert("123:SECRET\n", "42", "x", poster=lambda u, p: posted.append((u, p)))
    ok &= check("a token/chat id with stray whitespace (env var paste) is stripped before use",
                posted and posted[0][0].endswith("/bot123:SECRET/sendMessage") and posted[0][1]["chat_id"] == "42",
                posted)
    import io
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logging.getLogger("telegram_alert").addHandler(handler)
    try:
        telegram_alert.send_alert("123:SECRET", "42", "x", poster=invalid_url_poster)
    finally:
        logging.getLogger("telegram_alert").removeHandler(handler)
    ok &= check("a failed send never writes the bot token into the log", "SECRET" not in buf.getvalue()
                and "<bot-token>" in buf.getvalue(), buf.getvalue().strip())
    long_posted = []
    telegram_alert.send_alert("t", "c", "y" * 9000, poster=lambda u, p: long_posted.append(p))
    ok &= check("messages are capped under Telegram's 4096-char limit instead of being rejected",
                len(long_posted[0]["text"]) == 4000)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "day_state.json")
        day = main_mod.DayRoll(state_path=path)
        day.day_start_equity, day.daily_loss_hit = 10000.0, True
        day.save()
        ok &= check("DayRoll.save() writes atomically (no .tmp left behind) and restores the breaker",
                    not os.path.exists(path + ".tmp") and main_mod.DayRoll(state_path=path).daily_loss_hit)
        with open(path, "w") as f:
            f.write("[1, 2, 3]")
        ok &= check("a state file with the wrong JSON shape starts fresh instead of crashing",
                    main_mod.DayRoll(state_path=path).trades_today == 0)

    saved = {k: os.environ.get(k) for k in ("TELEGRAM_ALERT_BOT_TOKEN", "TELEGRAM_ALERT_CHAT_ID")}
    try:
        os.environ["TELEGRAM_ALERT_BOT_TOKEN"] = "ENVTOKEN"
        os.environ["TELEGRAM_ALERT_CHAT_ID"] = "ENVCHAT"
        parser = main_mod.build_parser()
        c_env = main_mod.build_config(parser.parse_args([]))
        c_flag = main_mod.build_config(parser.parse_args(["--telegram-alert-chat-id", "FLAGCHAT"]))
        ok &= check("alert credentials fall back to TELEGRAM_ALERT_* environment variables; flags win",
                    c_env.telegram_alert_bot_token == "ENVTOKEN" and c_env.telegram_alert_chat_id == "ENVCHAT"
                    and c_flag.telegram_alert_chat_id == "FLAGCHAT", (c_env.telegram_alert_chat_id,
                                                                      c_flag.telegram_alert_chat_id))
        c_news = main_mod.build_config(parser.parse_args(["--news-check-no-web-search",
                                                          "--news-check-fail-closed"]))
        c_off = main_mod.build_config(parser.parse_args(["--no-news-check"]))
        ok &= check("--news-check-no-web-search / --news-check-fail-closed / --no-news-check map to config",
                    not c_news.news_check_web_search and c_news.news_check_fail_closed
                    and c_news.breaking_news_check and not c_off.breaking_news_check)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return ok


# --------------------------------------------------------------------------- #
# XTR alignment gate (xtr_logic.py)
# --------------------------------------------------------------------------- #
def _tf(cls="bullish", close=2650.0, rsi=58.0, hist=0.2, hist_prev=0.1, adx=30.0, atr=2.0,
        upper=2660.0, lower=2640.0):
    return xtr_logic.TfRead(close=close, ema9=0, ema21=0, rsi14=rsi, macd_hist=hist, macd_hist_prev=hist_prev,
                            bb_upper=upper, bb_lower=lower, adx14=adx, atr14=atr, cls=cls)


def _assess(m5=None, m15="bullish", h1="bullish", recent=None):
    m5 = m5 or _tf()
    return xtr_logic.XtrAssessment(m5=m5, m15_class=m15, h1_class=h1,
                                   m5_direction=xtr_logic.m5_signal(m5),
                                   bounce_direction=xtr_logic.bounce_signal(m5),
                                   m5_recent_classes=recent if recent is not None else ["bullish"] * 6)


class _BarsGateway:
    def __init__(self, frames):
        self.frames = frames

    def get_bars(self, symbol, tf, count):
        return self.frames[tf].tail(count).reset_index(drop=True)


def test_xtr_logic() -> bool:
    print("\n=== 31. XTR alignment gate (M5 trigger, M15/H1 alignment, filters, stand-down) ===")
    ok = True
    cfg = AdvisorConfig(xtr_gate="block_opposed")
    X = xtr_logic

    ok &= check("a timeframe is clear only when EMA9/21, RSI and MACD histogram ALL agree (2 of 3 = mixed)",
                X.classify(2, 1, 55, 0.1) == X.BULLISH and X.classify(1, 2, 45, -0.1) == X.BEARISH
                and X.classify(2, 1, 55, -0.1) == X.MIXED and X.classify(1, 2, 55, -0.1) == X.MIXED)
    grades = {(m15, h1): X.grade_conviction("buy", m15, h1)
              for m15 in ("bullish", "bearish", "mixed") for h1 in ("bullish", "bearish", "mixed")}
    ok &= check("conviction: both agree = full, one = reduced, both mixed = unaligned, any clear opposite = opposed",
                grades[("bullish", "bullish")] == X.FULL and grades[("bullish", "mixed")] == X.REDUCED
                and grades[("mixed", "bullish")] == X.REDUCED and grades[("mixed", "mixed")] == X.UNALIGNED
                and grades[("bullish", "bearish")] == X.OPPOSED and grades[("bearish", "mixed")] == X.OPPOSED,
                grades)

    d = X.evaluate("buy", _assess(m15="bearish"), cfg)
    ok &= check("a BUY against a clearly bearish M15 is blocked (the spec's most important rule)",
                d.conviction == X.OPPOSED and "M15 clearly bearish" in d.block_reason, d.block_reason)
    d = X.evaluate("sell", _assess(m15="bearish", h1="bearish", m5=_tf("bearish", rsi=42, hist=-0.2, hist_prev=-0.1),
                                   recent=["bearish"] * 6), cfg)
    ok &= check("a SELL with M15 and H1 both bearish is full conviction trend continuation, allowed at full size",
                d.conviction == X.FULL and d.setup_type == X.TREND_CONTINUATION and not d.block_reason, d)

    chase_decel = _assess(m5=_tf(rsi=68, hist=0.3, hist_prev=0.5))
    chase_accel = _assess(m5=_tf(rsi=68, hist=0.5, hist_prev=0.3))
    d1, d2 = X.evaluate("buy", chase_decel, cfg), X.evaluate("buy", chase_accel, cfg)
    ok &= check("6a: an extended BUY (RSI > 65) is blocked when the M5 histogram stopped accelerating, allowed "
                "while it still rises", d1.setup_type == X.EXTENDED_CHASE and "no longer accelerating" in d1.block_reason
                and d2.setup_type == X.EXTENDED_CHASE and not d2.block_reason, (d1.block_reason, d2.block_reason))

    before = _assess(m5=_tf("mixed", rsi=52, hist=-0.12, hist_prev=-0.3), recent=["bearish", "bearish", "mixed"])
    after = _assess(m5=_tf("bullish", rsi=55, hist=0.05, hist_prev=-0.12), recent=["bearish", "bearish", "mixed"])
    d1, d2 = X.evaluate("buy", before, cfg), X.evaluate("buy", after, cfg)
    ok &= check("6b: a bounce-failure BUY before the histogram crossed zero waits (trade 59), after the cross passes",
                d1.setup_type == X.BOUNCE_FAILURE_REVERSAL and "crossed zero" in d1.block_reason
                and d2.setup_type == X.BOUNCE_FAILURE_REVERSAL and not d2.block_reason, (d1, d2))

    bounce = _assess(m5=_tf("bearish", close=2639.0, rsi=27, hist=-0.4, hist_prev=-0.5, adx=18, lower=2640.0),
                     m15="mixed", h1="bullish", recent=["bearish"] * 6)
    d = X.evaluate("buy", bounce, cfg)
    ok &= check("5a: RSI < 30 at the lower band in a ranging market is an RSI-extreme bounce (not a 'failed bounce'), "
                "flagged 6c, allowed",
                d.setup_type == X.RSI_EXTREME_BOUNCE and not d.block_reason and "RSI 27" in d.caution
                and d.regime == X.RANGING, d)

    strict = AdvisorConfig(xtr_gate="require_alignment")
    no_trigger = _assess(m5=_tf("mixed", rsi=52, hist=-0.1, hist_prev=-0.2), recent=["mixed"] * 6)
    unaligned = _assess(m15="mixed", h1="mixed")
    ok &= check("require_alignment: no clean M5 trigger -> blocked; both HTFs mixed -> blocked; "
                "block_opposed allows both",
                "no clean M5 trigger" in X.evaluate("buy", no_trigger, strict).block_reason
                and "both mixed" in X.evaluate("buy", unaligned, strict).block_reason
                and not X.evaluate("buy", no_trigger, cfg).block_reason
                and not X.evaluate("buy", unaligned, cfg).block_reason)

    import tempfile
    from datetime import timedelta as _td
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "xtr_state.json")
        sd = X.XtrStanddown(path)
        a = _assess(m5=_tf(close=2650.0, atr=2.0, adx=22))
        dec = X.evaluate("buy", a, cfg, sd)
        t0 = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        sd.record_entry(101, dec, 2650.0, a)
        sd.record_entry(102, dec, 2651.0, a)
        sd.update_from_closed([{"ticket": 101, "pnl_dollars": -6.0, "time": t0},
                               {"ticket": 102, "pnl_dollars": -6.0, "time": t0 + _td(minutes=20)}])
        ok &= check("sec. 8: two losses on the same setup type within 1 ATR -> that setup stands down",
                    sd.active(dec.setup_type, 'buy')
                    and "stand-down" in X.evaluate("buy", a, cfg, sd).block_reason, sd.losses)
        ok &= check("the stand-down survives a restart (state file, written atomically)",
                    X.XtrStanddown(path).active(dec.setup_type, 'buy') and not os.path.exists(path + ".tmp"))
        sd.update_from_closed([{"ticket": 101, "pnl_dollars": -6.0, "time": t0}])
        ok &= check("a closed trade is never counted twice", sd.losses[X.XtrStanddown.key(dec.setup_type, "buy")]["count"] == 2)
        sd.release_if_due(_assess(m5=_tf(close=2652.0, adx=35)))
        ok &= check("no release for a close still inside the range, even with ADX >= 30",
                    sd.active(dec.setup_type, 'buy'))
        sd.release_if_due(_assess(m5=_tf(close=2656.0, adx=34.1)))
        ok &= check("released by a decisive close beyond the range with ADX >= 30 (trade 54)",
                    not sd.active(dec.setup_type, 'buy'))

        sd2 = X.XtrStanddown(None)
        a_mixed = _assess(m5=_tf(close=2650.0, atr=2.0, adx=22), m15="mixed", h1="bullish")
        dec2 = X.evaluate("buy", a_mixed, cfg, sd2)
        for tk, px in ((1, 2650.0), (2, 2650.5)):
            sd2.record_entry(tk, dec2, px, a_mixed)
        sd2.update_from_closed([{"ticket": 1, "pnl_dollars": -5, "time": t0},
                                {"ticket": 2, "pnl_dollars": -5, "time": t0 + _td(minutes=5)}])
        before_flip = sd2.active(dec2.setup_type, 'buy')
        sd2.release_if_due(_assess(m5=_tf(close=2650.0, adx=22), m15="bullish", h1="bullish"))
        ok &= check("released when an HTF turns clearly in favor where it was mixed at the last loss",
                    before_flip and not sd2.active(dec2.setup_type, 'buy'))

        sd3 = X.XtrStanddown(None)
        for tk, px in ((7, 2650.0), (8, 2670.0)):
            sd3.record_entry(tk, dec, px, a)
        sd3.update_from_closed([{"ticket": 7, "pnl_dollars": -5, "time": t0},
                                {"ticket": 8, "pnl_dollars": -5, "time": t0 + _td(minutes=5)}])
        ok &= check("two losses far apart (different ranges) do not stand down",
                    not sd3.active(dec.setup_type, 'buy') and sd3.losses[X.XtrStanddown.key(dec.setup_type, "buy")]["count"] == 1)
        sd3.record_entry(9, dec, 2670.5, a)
        sd3.update_from_closed([{"ticket": 9, "pnl_dollars": 6, "time": t0 + _td(minutes=9)}])
        ok &= check("a win resets that setup's loss count", X.XtrStanddown.key(dec.setup_type, "buy") not in sd3.losses)

        sd7 = X.XtrStanddown(None)
        for tk, px in ((31, 2650.0), (32, 2650.5)):
            sd7.record_entry(tk, dec, px, a)
        sd7.update_from_closed([{"ticket": 31, "pnl_dollars": -5, "time": t0},
                                {"ticket": 32, "pnl_dollars": -5, "time": t0 + _td(minutes=5)}])
        ok &= check("the stand-down is per direction: two failed buys never block a sell",
                    sd7.active(dec.setup_type, "buy") and not sd7.active(dec.setup_type, "sell"))
        sd4 = X.XtrStanddown(None)
        for tk, d_ in ((21, "buy"), (22, "sell")):
            sd4.record_entry(tk, X.evaluate(d_, a, cfg, None), 2650.0, a)
        sd4.update_from_closed([{"ticket": 21, "pnl_dollars": -5, "time": t0},
                                {"ticket": 22, "pnl_dollars": -5, "time": t0 + _td(minutes=5)}])
        ok &= check("a buy loss then a sell loss is not two losses in a row on one setup",
                    not any(sd4.active(s_, d_) for s_ in (X.TREND_CONTINUATION, X.EXTENDED_CHASE,
                                                           X.RSI_EXTREME_BOUNCE, X.BOUNCE_FAILURE_REVERSAL)
                            for d_ in ("buy", "sell")), sd4.losses)
        bad = os.path.join(tmp, "bad_state.json")
        with open(bad, "w") as f:
            json.dump({"open": {"5": {"setup": "x"}, "6": dict(sd4.open.get("21", {}) or
                       {"setup": "trend_continuation", "entry": 2650.0, "atr": 2.0, "direction": "buy",
                        "m15": "bullish", "h1": "bullish"})},
                       "losses": {"trend_continuation": {"count": 2}, "old|buy": {"count": "2"}},
                       "seen": [1, "2"]}, f)
        sd5 = X.XtrStanddown(bad)
        sd5.release_if_due(a)
        ok &= check("a malformed state file loads without raising: bad records dropped, good kept",
                    list(sd5.open) == ["6"] and sd5.losses == {} and sd5.seen == ["1", "2"],
                    (sd5.open, sd5.losses, sd5.seen))
        sd6 = X.XtrStanddown(None)
        for tk in range(X.XtrStanddown.MAX_OPEN + 5):
            sd6.record_entry(tk + 1, dec, 2650.0, a)
        ok &= check("tickets awaiting a close are capped (oldest dropped)",
                    len(sd6.open) == X.XtrStanddown.MAX_OPEN and "1" not in sd6.open, len(sd6.open))

    def accelerating(sign):
        # A trend that keeps accelerating: EMA9 > EMA21, RSI > 50 and a
        # positive (rising) MACD histogram all at once - a steady linear
        # drift would leave the histogram hovering around zero ("mixed").
        df = make_trending_df(n=220, drift=0.0, noise=0.02)
        df["close"] = 2650.0 + sign * 0.004 * (np.arange(220) ** 2)
        df["open"] = df["close"].shift(1).fillna(df["close"].iloc[0])
        df["high"] = df[["open", "close"]].max(axis=1) + 0.1
        df["low"] = df[["open", "close"]].min(axis=1) - 0.1
        return df
    up, down = accelerating(+1), accelerating(-1)
    gw_up = _BarsGateway({"M5": up, "M15": up, "H1": up})
    a_up = X.assess(gw_up, "XAUUSD", 200)
    ok &= check("assess() reads M5/M15/H1 from the gateway (closed bars) - a steady uptrend reads bullish everywhere",
                a_up.m15_class == X.BULLISH and a_up.h1_class == X.BULLISH and a_up.m5_direction == "buy",
                (a_up.m15_class, a_up.h1_class, a_up.m5_direction))
    try:
        X.assess(_BarsGateway({"M5": up.tail(30), "M15": up, "H1": up}), "XAUUSD", 200)
        short_raises = False
    except ValueError:
        short_raises = True
    ok &= check("too little history raises (the caller then skips the gate) instead of reading unwarmed EMAs",
                short_raises)

    split = X.assess(_BarsGateway({"M5": up, "M15": up, "H1": down}), "XAUUSD", 200)
    ok &= check("M15 bullish vs H1 bearish blocks every direction -> the paid Claude call is skipped",
                main_mod.xtr_skip_reason(cfg, split).startswith("XTR: M15 bullish vs H1 bearish")
                and main_mod.xtr_skip_reason(cfg, a_up) == ""
                and main_mod.xtr_skip_reason(AdvisorConfig(xtr_gate="off"), split) == "")
    ok &= check("the snapshot section Claude reads previews both directions",
                X.snapshot_context(split)["buy"]["conviction"] == X.OPPOSED
                and X.snapshot_context(a_up)["buy"]["conviction"] == X.FULL
                and X.snapshot_context(None) is None)

    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
                         volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)
    cfg_risk = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs", use_risk_percent=True,
                             risk_percent=2.0, xtr_gate="block_opposed")
    ok &= check("gate off (the default): the reading is graded but never blocks",
                X.evaluate("buy", _assess(h1="bearish"), AdvisorConfig()).block_reason == ""
                and X.evaluate("buy", _assess(h1="bearish"), AdvisorConfig()).conviction == X.OPPOSED)
    fg_block = FakeGateway()
    blocked = X.evaluate("buy", _assess(h1="bearish"), cfg_risk)
    d = executor.execute(fg_block, cfg_risk, make_verdict("buy", 3, "full"), spec, trades_today=0, xtr=blocked)
    ok &= check("executor: an XTR block rejects the entry with its reason and sends no order",
                not d.executed and fg_block.orders_sent == [] and d.reject_reason.startswith("XTR:"), d.reject_reason)
    full = X.evaluate("buy", _assess(), cfg_risk)
    fg_plain, fg_trend, fg_range = FakeGateway(), FakeGateway(), FakeGateway()
    ranging_reduced = X.evaluate("buy", _assess(m5=_tf(adx=18), m15="mixed"), cfg_risk)
    executor.execute(fg_plain, cfg_risk, make_verdict("buy", 3, "full"), spec, trades_today=0)
    executor.execute(fg_trend, cfg_risk, make_verdict("buy", 3, "full"), spec, trades_today=0, xtr=full)
    executor.execute(fg_range, cfg_risk, make_verdict("buy", 3, "full"), spec, trades_today=0, xtr=ranging_reduced)
    ok &= check("XTR never changes lot size, SL or TP: the same order with no XTR, full conviction, or "
                "ranging + reduced conviction",
                fg_plain.orders_sent == fg_trend.orders_sent == fg_range.orders_sent
                and len(fg_plain.orders_sent) == 1, (fg_plain.orders_sent, fg_range.orders_sent))

    msg = telegram_alert.format_full_conviction_message("XAUUSD", make_verdict(), True, xtr_note=full.summary())
    ok &= check("the Telegram alert carries the XTR reading",
                "XTR: FULL conviction, trend continuation, trending" in msg, msg)
    import threading as _threading
    originals = (main_mod.gw, main_mod.market_intel.build_feature_snapshot, main_mod.claude_advisor.get_verdict,
                 main_mod.news_check.check_before_trade, main_mod.telegram_alert.send_alert,
                 main_mod.claude_paused, main_mod.xtr_logic.assess)
    sent, calls = [], []
    try:
        fake = FakeRunOnceGateway(bid=2350.0, ask=2350.2)
        main_mod.gw = fake
        main_mod.market_intel.build_feature_snapshot = lambda g, c: {"fake": True}
        main_mod.claude_advisor.get_verdict = lambda client, c, f: calls.append(f) or make_verdict("buy", 3, "full")
        main_mod.news_check.check_before_trade = lambda *a, **k: news_check.NewsCheckResult(ran=True, note="clear")
        main_mod.telegram_alert.send_alert = lambda t, c, text, **kw: sent.append(text) or True
        main_mod.claude_paused = lambda c, gateway=None: False
        main_mod.xtr_logic.assess = lambda g, sym, bars=200, recent=6: _assess(m15="mixed", h1="bearish")
        cfg_run = AdvisorConfig(dry_run=True, use_risk_percent=False, log_dir="/tmp/claudesmc_selftest_logs",
                                telegram_alert_bot_token="T", telegram_alert_chat_id="C",
                                claude_pause_filename="", send_performance_digest=False,
                                xtr_gate="block_opposed")
        day = main_mod.DayRoll()
        main_mod.run_once(object(), cfg_run, spec, day, X.XtrStanddown(None))
        for t in _threading.enumerate():
            if t is not _threading.current_thread() and t.daemon:
                t.join(timeout=2)
        msg = sent[-1] if sent else ""
        ok &= check("run_once: Claude sees the xtr section; a BUY against a clearly bearish H1 is refused, the "
                    "alert says why, no order is sent",
                    calls and calls[0].get("xtr", {}).get("h1_class") == "bearish" and fake.orders_sent == []
                    and "NOT executed - XTR: H1 clearly bearish" in msg and day.trades_today == 0, msg)
    finally:
        (main_mod.gw, main_mod.market_intel.build_feature_snapshot, main_mod.claude_advisor.get_verdict,
         main_mod.news_check.check_before_trade, main_mod.telegram_alert.send_alert,
         main_mod.claude_paused, main_mod.xtr_logic.assess) = originals

    ok &= check("--xtr-gate maps to config",
                main_mod.build_config(main_mod.build_parser().parse_args(["--xtr-gate", "block_opposed"]))
                .xtr_gate == "block_opposed" and AdvisorConfig().xtr_gate == "off")
    return ok

def test_relay_supervisor() -> bool:
    print("\n=== relay_supervisor: telegram_relay_bridge.py as main.py's supervised child ===")
    import sys as _sys
    import time as _time
    ok = True
    py = _sys.executable

    def wait_until(cond, timeout=10.0):
        end = _time.monotonic() + timeout
        while _time.monotonic() < end:
            if cond():
                return True
            _time.sleep(0.05)
        return cond()

    fatal = []
    sup = relay_supervisor.ChildSupervisor("relay", [py, "-c", "raise SystemExit(2)"], ".",
                                           fatal=relay_supervisor.RELAY_FATAL,
                                           on_fatal=fatal.append, first_delay=0.05)
    sup.start()
    ok &= check("exit 2 (not logged in): no restart, on_fatal called once with the login hint",
                wait_until(lambda: len(fatal) == 1) and sup.starts == 1
                and "relay_login.bat" in fatal[0], (sup.starts, fatal))
    sup.stop()

    crash = relay_supervisor.ChildSupervisor("relay", [py, "-c", "raise SystemExit(1)"], ".",
                                             fatal=relay_supervisor.RELAY_FATAL,
                                             first_delay=0.05, max_delay=0.1)
    crash.start()
    ok &= check("a crash (exit 1) is restarted with backoff", wait_until(lambda: crash.starts >= 3),
                crash.starts)
    crash.stop()
    n = crash.starts
    _time.sleep(0.3)
    ok &= check("stop() ends the restart loop", crash.starts == n, (n, crash.starts))

    longrun = relay_supervisor.ChildSupervisor("relay", [py, "-c", "import time; time.sleep(60)"], ".")
    longrun.start()
    ok &= check("the bridge keeps running while healthy", wait_until(longrun.running))
    longrun.stop(timeout=10)
    ok &= check("stop() terminates a running bridge", not longrun.running())

    # The real bridge with no Telegram credentials in its environment exits 3
    # (configuration) - fatal, not restarted.
    saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH")}
    try:
        cfg_fatal = []
        real = relay_supervisor.ChildSupervisor("relay", relay_supervisor.relay_command(),
                                                relay_supervisor.BRIDGE_DIR, fatal=relay_supervisor.RELAY_FATAL,
                                                on_fatal=cfg_fatal.append, first_delay=0.05)
        real.start()
        ok &= check("the real bridge without credentials stops for good as a configuration problem",
                    wait_until(lambda: len(cfg_fatal) == 1, 30) and "configuration" in cfg_fatal[0]
                    and real.starts == 1, (real.starts, cfg_fatal))
        real.stop()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

    import tempfile
    made = []

    class FakeSup:
        def __init__(self, name, command, cwd, fatal=None, on_fatal=None):
            self.name, self.command, self.cwd, self.fatal, self.on_fatal = name, command, cwd, fatal, on_fatal

        def start(self):
            made.append(self)

    class FakeJob(FakeSup):
        def __init__(self, name, command, cwd, every_days, state_path, log_path):
            super().__init__(name, command, cwd)
            self.every_days, self.state_path, self.log_path = every_days, state_path, log_path

    with tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "p.ini")
        with open(ini, "w") as f:
            f.write("[relay_bridge]\nenabled = false\n[xtr_export]\nenabled = true\n"
                    "args = --out-dir C:\\XTR_Data --bars 150 ; a comment\n"
                    "[ml_retrain]\nenabled = yes\nevery_days = 3\n"
                    "[calibration_report]\nenabled = true\nevery_days = 0\n")
        p = services.load_preset(ini)
        ok &= check("settings.ini parses: backslash paths kept, inline comment dropped, a bad "
                    "every_days switches only that service off (reported)",
                    p.xtr_export.args == ["--out-dir", "C:\\XTR_Data", "--bars", "150"]
                    and p.ml_retrain.enabled and p.ml_retrain.every_days == 3.0
                    and not p.calibration_report.enabled and len(p.errors) == 1
                    and p.enabled_names() == ["xtr_export", "ml_retrain"], (p, p.errors))
        ok &= check("a missing preset = every companion off",
                    services.load_preset(os.path.join(tmp, "nope.ini")).enabled_names() == [])
        cfg_c = AdvisorConfig(log_dir=os.path.join(tmp, "logs"))
        alerts = []
        started = services.start_services(cfg_c, p, alert=alerts.append, force_relay=True,
                                          supervisor_cls=FakeSup, job_cls=FakeJob)
        names = [x.name for x in started]
        ok &= check("start_services: --relay forces the bridge on; xtr_export + ml_retrain started; "
                    "disabled calibration_report not", names == ["Telegram relay bridge",
                                                                 "XTR price export", "ml_retrain"], names)
        relay_s, xtr_s, ml_j = started
        ok &= check("the bridge runs --no-login in its own folder; xtr_export gets the preset args",
                    "--no-login" in relay_s.command and relay_s.cwd == relay_supervisor.BRIDGE_DIR
                    and xtr_s.command[-4:] == ["--out-dir", "C:\\XTR_Data", "--bars", "150"]
                    and xtr_s.cwd == services.XTR_EXPORT_DIR, (relay_s.command, xtr_s.command))
        ok &= check("the ML job reads main.py's own logs folder and runs every_days",
                    ml_j.every_days == 3.0 and os.path.join(tmp, "logs") in ml_j.command
                    and ml_j.log_path.endswith("ml_retrain.log"), ml_j.command)
        xtr_s.on_fatal("bad options")
        ok &= check("a companion that stops for good raises one alert", len(alerts) == 1
                    and "XTR price export stopped" in alerts[0], alerts)

        # A scheduled job: runs once when due, remembers it, not again until due.
        clock = [1_000_000.0]
        job = services.PeriodicJob("demo", [py, "-c", "print('hello')"], tmp, 7,
                                   os.path.join(tmp, "st.json"), os.path.join(tmp, "demo.log"),
                                   clock=lambda: clock[0])
        first_due = job.due()
        rc = job.run_now()
        clock[0] += 6 * 86400
        not_yet = not job.due()
        clock[0] += 2 * 86400
        with open(os.path.join(tmp, "demo.log")) as f:
            logged = f.read()
        ok &= check("PeriodicJob: due at first, output logged, not due within the period, due after",
                    first_due and rc == 0 and "hello" in logged and not_yet and job.due(), logged)
        again = services.PeriodicJob("demo", [py, "-c", "pass"], tmp, 7, os.path.join(tmp, "st.json"),
                                     os.path.join(tmp, "demo.log"), clock=lambda: 1_000_000.0 + 86400)
        ok &= check("the last run survives a main.py restart (state file)", not again.due())

    real_preset = services.load_preset(shipped_copy("settings.ini"))
    ok &= check("settings.ini loads without errors", not real_preset.errors, real_preset.errors)
    if is_pristine("settings.ini"):          # the package's defaults - never your own choices
        ok &= check("the shipped settings.ini: relay (the signal path), ML retrain (daily), conviction "
                    "report and scorecard on by default; Python Drive export off (VPS only)",
                    real_preset.enabled_names() == ["relay_bridge", "ml_retrain", "calibration_report", "scorecard"]
                    and real_preset.ml_retrain.every_days == 1.0, real_preset.enabled_names())
    env = {"ANTHROPIC_API_KEY": "sk-ant-abcdefgh1234", "TELEGRAM_ALERT_CHAT_ID": "12345",
           "TELEGRAM_API_ID": "999"}
    lines, missing = main_mod.settings_report(relay_on=True, env=env)
    text = "\n".join(lines)
    ok &= check("settings report: secrets masked to the last 4, plain values shown, required-but-"
                "missing listed (relay on)",
                "********1234" in text and "sk-ant-abcdefgh" not in text and "12345" in text
                and missing == ["TELEGRAM_API_HASH", "TELEGRAM_SOURCE_CHANNELS", "TELEGRAM_RELAY_GROUP"],
                (text, missing))
    _, missing_off = main_mod.settings_report(relay_on=False, env={})
    ok &= check("relay off: only ANTHROPIC_API_KEY is required", missing_off == ["ANTHROPIC_API_KEY"],
                missing_off)
    args = main_mod.build_parser().parse_args(["--relay"])
    ok &= check("--relay / --relay-login / --preset parse", args.relay and not args.relay_login
                and args.preset == services.DEFAULT_PRESET
                and main_mod.build_parser().parse_args(["--relay-login"]).relay_login)
    return ok


def test_first_run_wizard() -> bool:
    print("\n=== first_run: every setting asked in one go on the first start ===")
    import tempfile
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        marker = os.path.join(tmp, ".setup_done")
        ok &= check("never prompts without someone at the keyboard",
                    not first_run.should_run({}, marker, interactive=False))
        ok &= check("first interactive start -> wizard", first_run.should_run({"ANTHROPIC_API_KEY": "x"},
                                                                              marker, interactive=True))
        open(marker, "w").close()
        ok &= check("after setup: not again while the API key is there",
                    not first_run.should_run({"ANTHROPIC_API_KEY": "x"}, marker, interactive=True))
        ok &= check("API key missing again -> wizard again",
                    first_run.should_run({}, marker, interactive=True))
        os.remove(marker)

        ini = os.path.join(tmp, "preset.ini")
        with open(ini, "w") as f:
            f.write("; my comment\n[relay_bridge]\n; keep me\nenabled = false\nargs =\n"
                    "[xtr_export]\nenabled = false\n")
        answers = iter([
            "abc",                     # chat id typo -> questioned
            "n",                       # ...don't use it
            "123456789",               # chat id again
            "",                        # relay: Enter = yes (the standard path)
            "12345",                   # api_id
            "@goldsignals,-100123",    # source channels
            "-1001234567890",          # relay group
            "n",                       # log in now: later
            "y",                       # send test message
        ])
        secrets = iter(["",                                               # API key: keep
                        "1234567:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",      # bot token
                        "0123456789abcdef0123456789abcdef"])               # api_hash
        env = {"ANTHROPIC_API_KEY": "sk-ant-existing-key-9999"}
        saved, sent, lines = {}, [], []
        prompts = []

        def ask(prompt):
            prompts.append(prompt)
            return next(answers)

        def ask_secret(prompt):
            prompts.append(prompt)
            return next(secrets)

        rc = first_run.run_wizard(ini, env=env, ask=ask, ask_secret=ask_secret,
                                  out=lines.append, saver=lambda n, v: saved.__setitem__(n, v),
                                  send_test=lambda t, c: sent.append((t, c)) or True, marker=marker)
        ok &= check("the current API key is shown masked and kept on Enter",
                    rc == 0 and "ANTHROPIC_API_KEY" not in saved and any("ends ...9999" in p for p in prompts)
                    and not any("sk-ant-existing" in p for p in prompts), prompts[:1])
        ok &= check("every other setting saved in the same run, relay included by default",
                    saved == {"TELEGRAM_ALERT_BOT_TOKEN": "1234567:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
                              "TELEGRAM_ALERT_CHAT_ID": "123456789", "TELEGRAM_API_ID": "12345",
                              "TELEGRAM_API_HASH": "0123456789abcdef0123456789abcdef",
                              "TELEGRAM_SOURCE_CHANNELS": "@goldsignals,-100123",
                              "TELEGRAM_RELAY_GROUP": "-1001234567890"}, saved)
        ok &= check("a value in the wrong format is questioned, then asked again",
                    any("usual format" in p for p in prompts))
        with open(ini) as f:
            text = f.read()
        ok &= check("relay switched on in settings.ini, comments and other sections untouched",
                    "[relay_bridge]\n; keep me\nenabled = true\n" in text and text.startswith("; my comment")
                    and "[xtr_export]\nenabled = false" in text, text)
        ok &= check("test Telegram message sent with the new token/chat id, marker written",
                    sent == [("1234567:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", "123456789")]
                    and os.path.exists(marker))
        ok &= check("it says what to put in the EA's InpChannelId1",
                    any("InpChannelId1 = -1001234567890" in x for x in lines), lines[-6:])

        # Relay group not known yet: the login lists it, then it is asked again.
        logins, saved3 = [], {}
        seq3 = iter(["", "", "-", "", "-", "", "-1009876543210", "n"])
        env3 = {"ANTHROPIC_API_KEY": "sk-ant-existing-key-9999", "TELEGRAM_ALERT_CHAT_ID": "123456789",
                "TELEGRAM_API_ID": "12345", "TELEGRAM_SOURCE_CHANNELS": "@goldsignals"}
        first_run.run_wizard(ini, env=env3, ask=lambda p: next(seq3), ask_secret=lambda p: "",
                             out=lines.append, saver=lambda n, v: saved3.__setitem__(n, v),
                             login=lambda: logins.append(1), marker=marker)
        ok &= check("login offered right away (Enter = yes); an unknown relay group is asked again after it",
                    logins == [1] and saved3.get("TELEGRAM_RELAY_GROUP") == "-1009876543210", (logins, saved3))

        env2, saved2 = {}, {}
        seq = iter(["-", "n"])      # chat id: skip; relay: no (bot is admin of the channel itself)
        first_run.run_wizard(ini, env=env2, ask=lambda p: next(seq), ask_secret=lambda p: "-",
                             out=lines.append, saver=lambda n, v: saved2.__setitem__(n, v), marker=marker)
        ok &= check("skipping everything saves nothing and reports the API key as still missing",
                    saved2 == {} and any("Still missing: ANTHROPIC_API_KEY" in x for x in lines))
        with open(ini) as f:
            ok &= check("answering 'n' to the relay switches it off in settings.ini",
                        "enabled = false" in f.read().split("[xtr_export]")[0])
    args = main_mod.build_parser().parse_args(["--setup"])
    ok &= check("main.py --setup parses", args.setup)
    return ok


def _raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def test_tactics() -> bool:
    print("\n=== 34. entry tactics: trading hours, Friday cutoff, spread guard, trend strength ===")
    ok = True
    T = tactics
    cfg = AdvisorConfig()
    utc = timezone.utc
    ok &= check("Claude's defaults: the tested New York hours (Sunday = the 18:15 reopen slot), Friday 16:00 "
                "cutoff, 50-point spread guard, ADX filter off",
                cfg.trade_windows == "08:00-16:45,18:15-20:00" and cfg.trade_timezone == "America/New_York"
                and cfg.trade_days == "Sun-Fri" and cfg.friday_cutoff_ny == "16:00"
                and cfg.max_spread_points == 50 and cfg.min_adx == 0, T.describe(cfg))
    ny_in = datetime(2026, 9, 23, 13, 0, tzinfo=utc)       # Wednesday 09:00 New York = 17:00 Oman
    ny_out = datetime(2026, 9, 23, 6, 0, tzinfo=utc)       # 02:00 New York (Asia) = 10:00 Oman
    ny_break = datetime(2026, 9, 23, 21, 30, tzinfo=utc)   # 17:30 New York - gold's daily break
    sunday_reopen = datetime(2026, 9, 20, 22, 30, tzinfo=utc)  # Sunday 18:30 New York = Monday 02:30 Oman
    ok &= check("Claude: New York 09:00 and the Sunday-evening reopen slot open; Asia (02:00) and the "
                "daily break refused; winter follows US daylight saving",
                T.time_block(cfg, ny_in) == "" and T.time_block(cfg, sunday_reopen) == ""
                and "outside trading hours" in T.time_block(cfg, ny_out)
                and "outside trading hours" in T.time_block(cfg, ny_break)
                and T.time_block(cfg, datetime(2026, 1, 15, 13, 30, tzinfo=utc)) == ""
                and T.time_block(cfg, datetime(2026, 1, 15, 12, 30, tzinfo=utc)) != "")
    oman = AdvisorConfig(trade_windows="06:00-23:00", trade_timezone="Asia/Muscat", trade_days="Mon-Fri")
    ok &= check("Claude's hours in Oman time for the dashboard: 16:00-00:45, 02:15-04:00 in summer, an hour "
                "later in winter",
                T.windows_in(cfg, "Asia/Muscat", datetime(2026, 7, 15, 12, tzinfo=utc)) == "16:00-00:45, 02:15-04:00"
                and T.windows_in(cfg, "Asia/Muscat", datetime(2026, 1, 15, 12, tzinfo=utc)) == "17:00-01:45, 03:15-05:00"
                and T.windows_in(AdvisorConfig(trade_windows=""), "Asia/Muscat", ny_in) == "any hour")
    ok &= check("windows parse to minutes; a midnight-wrapping window works",
                T.parse_windows("08:00-16:45,18:15-20:00") == [(480, 1005), (1095, 1200)]
                and T.in_windows(23 * 60, T.parse_windows("20:00-02:00"))
                and T.in_windows(60, T.parse_windows("20:00-02:00"))
                and not T.in_windows(12 * 60, T.parse_windows("20:00-02:00")))
    bad = []
    for spec in ("8-17", "08:00", "25:00-26:00", "08:61-09:00"):
        try:
            T.parse_windows(spec)
        except ValueError:
            bad.append(spec)
    ok &= check("a malformed window is an error, never 'any hour'", len(bad) == 4, bad)
    ok &= check("trading days: Mon-Fri, lists, wrap-around ranges, '' = every day; a typo is an error",
                T.parse_days("Mon-Fri") == {0, 1, 2, 3, 4} and T.parse_days("Mon,Wed") == {0, 2}
                and T.parse_days("Sun-Thu") == {6, 0, 1, 2, 3} and T.parse_days("") == set(range(7))
                and _raises(lambda: T.parse_days("Mon-Fry")))
    wed_open = datetime(2026, 9, 23, 2, 0, tzinfo=utc)      # Wednesday 06:00 Oman (UTC+4)
    wed_early = datetime(2026, 9, 23, 1, 59, tzinfo=utc)    # 05:59 Oman
    wed_last = datetime(2026, 9, 23, 18, 59, tzinfo=utc)    # 22:59 Oman
    wed_close = datetime(2026, 9, 23, 19, 0, tzinfo=utc)    # 23:00 Oman
    ok &= check("Oman time (any zone works): 06:00 and 22:59 allowed, 05:59 and 23:00 refused",
                T.time_block(oman, wed_open) == "" and T.time_block(oman, wed_last) == ""
                and "05:59 Oman time" in T.time_block(oman, wed_early)
                and "outside trading hours" in T.time_block(oman, wed_close), T.time_block(oman, wed_early))
    saturday = datetime(2026, 9, 26, 8, 0, tzinfo=utc)      # Saturday 12:00 Oman
    sunday = datetime(2026, 9, 27, 8, 0, tzinfo=utc)
    monday = datetime(2026, 9, 28, 2, 0, tzinfo=utc)        # Monday 06:00 Oman
    ok &= check("market days only: Saturday and Sunday refused, Monday 06:00 Oman open",
                "not a trading day" in T.time_block(oman, saturday) and "Sunday" in T.time_block(oman, sunday)
                and T.time_block(oman, monday) == "", T.time_block(oman, saturday))
    ok &= check("Oman has no daylight saving: 06:00 Oman is 02:00 UTC in January as in September",
                T.time_block(oman, datetime(2026, 1, 14, 2, 0, tzinfo=utc)) == ""
                and T.time_block(oman, datetime(2026, 1, 14, 1, 59, tzinfo=utc)) != "")
    ny = AdvisorConfig(trade_timezone="America/New_York", trade_windows="08:00-16:45", trade_days="")
    ok &= check("any time zone works, daylight saving followed (New York: 13:30 UTC in January is "
                "08:30 EST, in; 12:30 UTC is out)",
                T.time_block(ny, datetime(2026, 1, 15, 13, 30, tzinfo=utc)) == ""
                and "New York time" in T.time_block(ny, datetime(2026, 1, 15, 12, 30, tzinfo=utc)))
    ok &= check("a pandas Timestamp (the backtest clock) works like a datetime",
                T.time_block(cfg, pd.Timestamp(ny_in)) == "" and T.time_block(cfg, pd.Timestamp(ny_out)) != "")
    friday = datetime(2026, 9, 25, 20, 30, tzinfo=utc)      # Friday 16:30 New York (00:30 Sat Oman)
    friday_in = datetime(2026, 9, 25, 18, 30, tzinfo=utc)   # Friday 14:30 New York = 22:30 Oman
    ok &= check("Friday: 14:30 New York is fine; after 16:00 New York no new entry (the weekend gap)",
                T.time_block(cfg, friday_in) == "" and "Friday" in T.time_block(cfg, friday))
    anyhour = AdvisorConfig(trade_windows="", trade_days="", friday_cutoff_ny="")
    ok &= check("'' switches every time check off", T.time_block(anyhour, wed_early) == ""
                and T.time_block(anyhour, friday) == "" and T.time_block(anyhour, saturday) == "")
    ok &= check("a wrong time zone name is caught at start-up",
                _raises(lambda: T.validate(AdvisorConfig(trade_timezone="Oman/Muscat"))))
    summer, asia = ny_in, ny_out
    ok &= check("spread guard: 60 points refused at a 50-point limit, 50 allowed, 0 = off",
                "spread 60" in T.spread_block(cfg, 60) and T.spread_block(cfg, 50) == ""
                and T.spread_block(AdvisorConfig(max_spread_points=0), 500) == "")
    feats = {"primary_indicators": {"adx14": 18.2}}
    ok &= check("trend strength: off by default; min_adx=25 refuses ADX 18.2 and allows 30",
                T.regime_block(cfg, feats) == ""
                and "ADX14 18.2" in T.regime_block(AdvisorConfig(min_adx=25), feats)
                and T.regime_block(AdvisorConfig(min_adx=25), {"primary_indicators": {"adx14": 30}}) == "")

    class Gw:
        def __init__(self, when, spread_points):
            self.when, self.spread = when, spread_points

        def now(self):
            return self.when

        def symbol_spec(self, symbol):
            return gw.SymbolSpec(name=symbol, point=0.01, digits=2, stops_level_points=0,
                                 spread_points=self.spread, volume_min=0.01, volume_max=5.0,
                                 volume_step=0.01, tick_value=1.0, tick_size=0.01)

        def get_tick(self, symbol):
            return backtest._FakeTick(bid=4300.0, ask=4300.0 + self.spread * 0.01)

    quiet = AdvisorConfig(news_auto_blackout=False)
    ok &= check("verdict_independent_block (before the Claude call): hours and live spread are checked",
                executor.verdict_independent_block(Gw(summer, 20), quiet, 0) == ""
                and "outside trading hours" in executor.verdict_independent_block(Gw(asia, 20), quiet, 0)
                and "spread 80" in executor.verdict_independent_block(Gw(summer, 80), quiet, 0))

    def cfg_for(*argv):
        return main_mod.build_config(main_mod.build_parser().parse_args(list(argv)))
    c = cfg_for("--trade-hours", "any", "--friday-cutoff", "off", "--max-spread", "0", "--min-adx", "25")
    ok &= check("start.bat options map to config ('any' / 'off' / 0 switch off)",
                c.trade_windows == "" and c.friday_cutoff_ny == "" and c.max_spread_points == 0
                and c.min_adx == 25)
    ok &= check("a mistyped option or time stops with the settings-error code (no endless restarts)",
                main_mod.main(["--trade-hours", "8-17", "--check"]) == main_mod.SETTINGS_ERROR
                and main_mod.main(["--no-such-option"]) == main_mod.SETTINGS_ERROR)

    # Backtest realism: the daily break, the weekend and the reopen spread.
    t0 = pd.Timestamp("2026-09-23 20:15", tz="UTC")       # 16:15 New York
    times = [t0 + pd.Timedelta(minutes=15 * i) for i in range(3)] + [pd.Timestamp("2026-09-23 22:00", tz="UTC")]
    bars = pd.DataFrame({"time": times, "open": 4300.0, "high": 4301.0, "low": 4299.0, "close": 4300.0,
                         "volume": 0})
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
                         volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)
    g = backtest.HistoricalGateway("XAUUSD", {"M15": bars}, spec, spread_points=25, rollover_spread_points=80)
    g.primary_timeframe, g.cursor = "M15", 1
    open_before = g.market_closed_now()
    g.cursor = 2                                           # the 16:45 bar - the next one is the 18:00 reopen
    ok &= check("backtest: an entry decided on the last bar before the daily break is refused "
                "(live: market closed), not filled at the reopen",
                not open_before and g.market_closed_now())
    ok &= check("backtest: the spread is widened around the reopen only",
                abs(g.spread_at(pd.Timestamp("2026-09-23 22:00", tz="UTC")) - 0.80) < 1e-9
                and abs(g.spread_at(pd.Timestamp("2026-09-23 20:15", tz="UTC")) - 0.25) < 1e-9)
    return ok


def _mql_inputs(path: str) -> dict:
    """`input <type> <Name> = <value>;` defaults of an EA, as strings."""
    import re
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*input\s+\w+\s+(Inp\w+)\s*=\s*("[^"]*"|[^;]+);', line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def _preset(path: str) -> dict:
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith(";") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.split("||")[0].strip()
    return out


def test_ea_preset_python_consistency() -> bool:
    print("\n=== 35. EA inputs <-> presets <-> Python settings <-> start.bat agree ===")
    import paths
    import shlex
    ok = True
    mt5 = os.path.join(paths.PACKAGE_ROOT, "mt5")
    ea = _mql_inputs(os.path.join(mt5, "Experts", "UnifiedTrader_EA.mq5"))
    logger = _mql_inputs(os.path.join(mt5, "Experts", "TelegramSMC_TradeLogger.mq5"))
    ea_set = _preset(os.path.join(mt5, "Presets", "UnifiedTrader_EA_Default.set"))
    log_set = _preset(os.path.join(mt5, "Presets", "TelegramSMC_TradeLogger_Unified.set"))
    ok &= check("every preset line names a real EA input (a typo would be ignored by MT5)",
                set(ea_set) <= set(ea) and set(log_set) <= set(logger),
                (sorted(set(ea_set) - set(ea)), sorted(set(log_set) - set(logger))))
    cfg = AdvisorConfig()

    def num(v):
        return float(v)
    same = {
        "InpSlDollars": cfg.sl_dollars, "InpTp1Dollars": cfg.tp1_dollars, "InpTrailDollars": cfg.trail_dollars,
        "InpReferenceLot": cfg.reference_lot, "InpMaxPositionsPerDirection": cfg.max_open_positions_per_direction,
        "InpRiskPercent": cfg.risk_percent, "InpMaxDailyLossPct": cfg.max_daily_loss_pct,
        "InpClaudeMagicNumber": cfg.magic, "InpMaxSpreadPoints": cfg.max_spread_points,
    }
    bad = {k: (ea.get(k), ea_set.get(k), v) for k, v in same.items()
           if num(ea[k]) != v or (k in ea_set and num(ea_set[k]) != v)}
    ok &= check("lot/SL/TP1/trail/cap/risk/daily cap/magic/spread limit: EA default = preset = Python",
                not bad, bad)
    from zoneinfo import ZoneInfo
    oman_offset = ZoneInfo("Asia/Muscat").utcoffset(datetime(2026, 1, 15)).total_seconds() / 3600
    oman_offset_summer = ZoneInfo("Asia/Muscat").utcoffset(datetime(2026, 7, 15)).total_seconds() / 3600
    ok &= check("Telegram window: EA default = preset = Python mirror (06:00-23:00 Oman = UTC+4 all year, "
                "Monday-Friday)",
                ea["InpTradeHours"] == ea_set.get("InpTradeHours") == cfg.telegram_trade_windows == "06:00-23:00"
                and num(ea["InpTradeUtcOffsetHours"]) == num(ea_set["InpTradeUtcOffsetHours"])
                == cfg.telegram_utc_offset_hours == oman_offset == oman_offset_summer
                and ea["InpTradeWeekdaysOnly"] == ea_set.get("InpTradeWeekdaysOnly") == "true"
                and cfg.telegram_weekdays_only,
                (ea.get("InpTradeHours"), ea.get("InpTradeUtcOffsetHours"), cfg.telegram_trade_windows))
    ok &= check("Telegram stop: EA default = preset = Python mirror (the signal's stop within $3-$20, "
                "else the fixed stop)",
                ea["InpTelegramUseSignalSl"] == ea_set.get("InpTelegramUseSignalSl") == "true"
                and cfg.telegram_use_signal_sl
                and num(ea["InpSignalSlMinDistance"]) == num(ea_set["InpSignalSlMinDistance"])
                == cfg.signal_sl_min_distance == 3.0
                and num(ea["InpSignalSlMaxDistance"]) == num(ea_set["InpSignalSlMaxDistance"])
                == cfg.signal_sl_max_distance == 20.0,
                (ea.get("InpTelegramUseSignalSl"), ea.get("InpSignalSlMinDistance"),
                 ea.get("InpSignalSlMaxDistance")))
    ok &= check("Telegram exits: EA default = preset = Python mirror (two halves: TP +$4 / break-even then "
                "$3 trail)",
                ea["InpTelegramSplit"] == ea_set.get("InpTelegramSplit") == "true" and cfg.telegram_split
                and num(ea["InpTelegramTp1Dollars"]) == num(ea_set["InpTelegramTp1Dollars"])
                == cfg.telegram_tp1_dollars == 4.0
                and num(ea["InpTelegramTrailDollars"]) == num(ea_set["InpTelegramTrailDollars"])
                == cfg.telegram_trail_dollars == 3.0,
                (ea.get("InpTelegramSplit"), ea.get("InpTelegramTp1Dollars"), ea.get("InpTelegramTrailDollars")))
    ok &= check("Claude window: the tested New York hours (not the Oman window)",
                cfg.trade_windows == "08:00-16:45,18:15-20:00" and cfg.trade_timezone == "America/New_York")
    with open(os.path.join(mt5, "Experts", "UnifiedTrader_EA.mq5"), encoding="utf-8") as f:
        ea_src = f.read()
    body = ea_src[ea_src.index("bool PlaceCopiedOrder(bool isBuy"):]
    body = body[:body.index("\n}\n")]
    ok &= check("EA: a Telegram order the broker refuses is never logged as copied (PlaceCopiedOrder "
                "returns the broker's answer)",
                body.rstrip().endswith("return(ok);") and "return(true);" in body.split("if(InpDryRun)")[1][:1200])
    proc = ea_src[ea_src.index("void ProcessSignal(const SignalMsg &msg"):]
    proc = proc[:proc.index("\n}\n")]
    mgmt = ea_src[ea_src.index("void ManagePositionExit(ulong ticket, long magic)"):]
    mgmt = mgmt[:mgmt.index("\n}\n")]
    tg = mgmt[mgmt.index("if(magic == InpTelegramMagicNumber && InpTelegramSplit)"):]
    ok &= check("EA Telegram split: half A = the take-profit (counts as the trade), half B = no TP (does not "
                "count again); only with room for both in the 5-per-direction cap",
                "PlaceCopiedOrder(isBuy, orderPrice, isPending, slDist, lotsA, tpDist, true," in proc
                and "PlaceCopiedOrder(isBuy, orderPrice, isPending, slDist, lotsB, 0.0, false," in proc
                and "sameDir + 2 <= InpMaxPositionsPerDirection" in proc
                and proc.index("lotsA, tpDist, true,") < proc.index("lotsB, 0.0, false,"))
    ok &= check("EA Telegram split: half A (broker TP) is left alone; half B goes to break-even at +TP1, then "
                "trails - never below the entry, tightening only; Claude's management unchanged",
                tg.index("if(currentTp != 0.0)") < tg.index("return;") < tg.index("PositionModify")
                and "MathMax(openPrice, tick.bid - trail)" in tg and "MathMin(openPrice, tick.ask + trail)" in tg
                and "tick.bid - openPrice < trigger" in tg and "openPrice - tick.ask < trigger" in tg
                and mgmt.index("InpTelegramSplit)") < mgmt.index("bool useBreakevenDecay"))
    order = [proc.find(x) for x in ("TelegramStopDistance(msg, isBuy, orderPrice", "PositionSizeLots(slDist)",
                                    "DailyRiskBudgetReason(lots, slDist)",
                                    "MarginGuardReason(isBuy, lots, slDist)", "PlaceCopiedOrder(isBuy, orderPrice")]
    ok &= check("EA: a Telegram trade's lot is sized from its real stop, and the daily budget and margin "
                "guard check that same stop and lot before the order",
                all(i >= 0 for i in order) and order == sorted(order)
                and "DailyRiskBudgetReason(PositionSizeLots())" not in proc, order)
    sl_fn = ea_src[ea_src.index("int FindSlLabel(const string &text"):]
    sl_fn = sl_fn[:sl_fn.index("\n}\n")]
    ok &= check("EA: a bare 'Stop: 4342' is read as the stop, never the order type in 'SELL STOP 2350'",
                'FindWholeWord(text, "STOP", pos)' in sl_fn and 'PrecededByWord(text, idx, "SELL")' in sl_fn
                and 'PrecededByWord(text, idx, "BUY")' in sl_fn)
    ok &= check("margin guard on in both the EA/preset and Python",
                ea["InpMarginGuard"] == "true" and ea_set.get("InpMarginGuard") == "true" and cfg.margin_guard)
    files = {"InpLastVerdictFilename": cfg.last_verdict_filename,
             "InpClaudePauseFilename": cfg.claude_pause_filename,
             "InpCalendarExportFile": cfg.econ_calendar_filename}
    bad = {k: (ea.get(k), ea_set.get(k), v) for k, v in files.items()
           if ea[k] != v or ea_set.get(k, v) != v}
    ok &= check("shared file names (Why button, Claude pause, calendar) match", not bad, bad)
    ok &= check("trade logger journals both magics under the EA's source tags",
                int(log_set["InpMagicNumber"]) == int(ea["InpTelegramMagicNumber"])
                and int(log_set["InpMagicNumber2"]) == cfg.magic
                and log_set["InpSourceLabel2"] == cfg.comment, log_set)
    # The package's own start.bat (start.bat.new when the updater kept yours):
    # a start.bat you edited (dry-run, other options) never fails a test.
    with open(shipped_copy("start.bat"), encoding="utf-8") as f:
        line = next(ln for ln in f if ln.strip().lower().startswith("set gt_args="))
    gt_args = shlex.split(line.split("=", 1)[1])
    started = main_mod.build_config(main_mod.build_parser().parse_args(gt_args))
    ok &= check("start.bat's options parse; its shared cap counts the EA's Telegram magic",
                started.shared_cap_magic_numbers == [int(ea["InpTelegramMagicNumber"])], gt_args)
    shipped_rules = (started.risk_percent, started.max_daily_loss_pct, started.sl_dollars, started.tp1_dollars,
                     started.trail_dollars, started.max_open_positions_per_direction)
    ok &= check("the fixed trading rules (2% risk, 10% cap, $6/$6/$3, 5 per direction) - start.bat changes "
                "none of them unless you add an option yourself",
                shipped_rules == (2.0, 10.0, 6.0, 6.0, 3.0, 5)
                or any(a in gt_args for a in ("--risk-percent", "--max-daily-loss", "--sl-dollars", "--tp-dollars",
                                              "--trail-dollars", "--max-positions")), (shipped_rules, gt_args))
    return ok


def test_broker_clock_and_trading_day() -> bool:
    print("\n=== 36. broker server clock -> UTC, trading day shared with the EA ===")
    ok = True

    def utc(s):
        return pd.Timestamp(s, tz="UTC")

    def server_epoch(s):
        return int(utc(s).timestamp())     # MT5 encodes the server wall clock as if it were UTC
    saved = gw._CLOCK["fixed_offset"]
    try:
        gw._CLOCK["fixed_offset"] = None
        conv = gw.server_to_utc([server_epoch("2026-07-01 12:00"), server_epoch("2026-01-15 12:00")])
        ok &= check("a New York + 7h broker: 12:00 server is 09:00 UTC in summer, 10:00 UTC in winter",
                    conv.iloc[0] == utc("2026-07-01 09:00") and conv.iloc[1] == utc("2026-01-15 10:00"),
                    conv.tolist())
        reopen = gw.server_to_utc([server_epoch("2026-03-09 01:00")]).iloc[0]
        ok &= check("the week US clocks change is handled (Monday 01:00 server = Sunday 18:00 New York)",
                    reopen == utc("2026-03-08 22:00"), reopen)
        gw._CLOCK["fixed_offset"] = 0
        ok &= check("a broker on a fixed UTC clock is used as is",
                    gw.server_to_utc([server_epoch("2026-07-01 12:00")]).iloc[0] == utc("2026-07-01 12:00"))
    finally:
        gw._CLOCK["fixed_offset"] = saved

    class Tick:
        def __init__(self, t, msc):
            self.time, self.time_msc = t, msc

    class FakeMt5:
        """live=True: every read is a newer quote (market open); False: the
        same last quote forever (weekend / daily break)."""
        def __init__(self, t, live=True):
            self.t, self.live, self.n = t, live, 0

        def symbol_info_tick(self, symbol):
            self.n += 1 if self.live else 0
            return Tick(self.t, self.t * 1000 + self.n)
    import time as _time
    real_mt5 = gw.mt5
    fake_clock = [0.0]
    naps = []

    def nap(sec):
        naps.append(sec)
        fake_clock[0] += sec
    offset = lambda sym: gw.server_utc_offset_seconds(sym, sleep=nap, clock=lambda: fake_clock[0])  # noqa: E731
    try:
        gw._TICK_SEEN.clear()
        now = int(_time.time())
        expected = gw.ny_close_offset_seconds(pd.Timestamp(now, unit="s", tz="UTC"))
        gw.mt5 = (lambda f: (lambda: f))(FakeMt5(now + expected + 20))
        ok &= check("a live tick reveals the broker offset (New York + 7h model kept)",
                    offset("XAUUSD") == expected and gw._CLOCK["fixed_offset"] is None)
        gw.mt5 = (lambda f: (lambda: f))(FakeMt5(now + 3600 + 5))
        ok &= check("a different fixed offset switches the clock model",
                    offset("XAUUSD") == 3600 and gw._CLOCK["fixed_offset"] == 3600)
        gw.mt5 = (lambda f: (lambda: f))(FakeMt5(now + expected - 600))
        ok &= check("a stale tick (market closed) tells nothing and changes nothing",
                    offset("XAUUSD") is None and gw._CLOCK["fixed_offset"] == 3600)
        # The 26 Sep log: Friday's last gold quote, read exactly 6h later on
        # Saturday, looked like a live "UTC-6h" clock. A quote that never
        # moves is now rejected whatever its age.
        gw._TICK_SEEN.clear()
        naps.clear()
        gw.mt5 = (lambda f: (lambda: f))(FakeMt5(now - 6 * 3600 + 10, live=False))
        first = offset("XAUUSD")
        second = offset("XAUUSD")
        ok &= check("weekend: Friday's last quote aged a whole 6h is NOT read as a UTC-6h broker clock "
                    "(the clock model stays as it was)",
                    first is None and second is None and gw._CLOCK["fixed_offset"] == 3600 and 0 < sum(naps) <= 7,
                    (first, second, naps))
    finally:
        gw.mt5 = real_mt5
        gw._CLOCK["fixed_offset"] = saved
        gw._TICK_SEEN.clear()

    td = tactics.trading_day
    ok &= check("trading day rolls at 17:00 New York (server midnight): 20:59 UTC in summer is still "
                "today, 21:00 is tomorrow; in winter it rolls at 22:00 UTC",
                td(utc("2026-09-23 20:59")).isoformat() == "2026-09-23"
                and td(utc("2026-09-23 21:00")).isoformat() == "2026-09-24"
                and td(utc("2026-01-15 21:30")).isoformat() == "2026-01-15"
                and td(utc("2026-01-15 22:00")).isoformat() == "2026-01-16")
    ok &= check("with a known server offset the server's own date is used",
                td(utc("2026-09-23 22:30"), 0).isoformat() == "2026-09-23")

    day = main_mod.DayRoll()
    day.observe_server_offset(7200)
    first = day.server_offset
    day.observe_server_offset(7200)
    ok &= check("DayRoll adopts a broker offset only after two agreeing ticks", first is None
                and day.server_offset == 7200)
    day.date = day.today() + timedelta(days=1)
    before = day.date
    ok &= check("the trading day never moves backwards (learning the offset can't reset a tripped breaker)",
                day.roll(10000.0) is None and day.date == before)
    return ok


def test_scorecard() -> bool:
    print("\n=== 37. demo scorecard: real results per source + fixed verdict rules ===")
    import scorecard as S
    ok = True
    per_price, sl = 100.0, 6.0            # XAUUSD: $100 per 1.0 move per lot, $6 stop at 0.01 lot

    def trade(r, magic=20260921, vol=0.10):
        return {"pnl_dollars": r * vol * per_price * sl, "volume": vol, "magic": magic}
    s = S.summarize([trade(1.0), trade(-1.0), trade(2.0, vol=0.3)], per_price, sl)
    ok &= check("R is the result in multiples of the $6 stop, whatever the lot",
                s["trades"] == 3 and abs(s["avg_r"] - (2.0 / 3)) < 1e-3 and s["win_pct"] == 66.7, s)
    ok &= check("fewer than 30 trades: TOO EARLY, whatever the numbers",
                S.verdict(s)[0] == "TOO EARLY" and "3/30" in S.verdict(s)[1])
    good = S.summarize([trade(1.5), trade(-1.0)] * 20, per_price, sl)
    bad = S.summarize([trade(-1.0), trade(-1.0), trade(0.5)] * 12, per_price, sl)
    flat = S.summarize([trade(1.0), trade(-1.0)] * 20, per_price, sl)
    ok &= check("40 trades at +0.25R each on average: ON TRACK", S.verdict(good)[0] == "ON TRACK", good)
    ok &= check("a losing record: STOP AND REVIEW (with the pause buttons named)",
                S.verdict(bad)[0] == "STOP AND REVIEW" and "Pause" in S.verdict(bad)[1], bad)
    ok &= check("break-even: NOT PROVEN YET", S.verdict(flat)[0] == "NOT PROVEN YET", flat)
    streak = S.summarize([trade(1.0)] * 20 + [trade(-1.0)] * 11 + [trade(1.0)] * 20, per_price, sl)
    ok &= check("an 11R drawdown stops it even when the total is positive",
                S.verdict(streak)[0] == "STOP AND REVIEW" and "drawdown" in S.verdict(streak)[1], streak)
    book = [trade(1.0), trade(-1.0, magic=20260922), trade(0.5, magic=99)]
    out = S.build(book, {"Claude": 20260921, "Telegram signals": 20260922}, per_price, sl)
    ok &= check("split per source; the combined book ignores other magics (manual/other EAs)",
                out["Claude"]["trades"] == 1 and out["Telegram signals"]["trades"] == 1
                and out["Combined"]["trades"] == 2, out)
    text = S.format_report("XAUUSD", out | {"Empty": {"trades": 0}}, 120)
    ok &= check("the report names every source, its verdict and the no-edge chance",
                "Claude: TOO EARLY" in text and "Telegram signals" in text and "chance of no real edge" in text
                and "Empty: no closed trades yet" in text, text)
    if is_pristine("settings.ini"):          # the package's default, never your own choice
        p = services.load_preset(shipped_copy("settings.ini"))
        ok &= check("settings.ini runs the scorecard weekly by default",
                    p.scorecard.enabled and p.scorecard.every_days == 7.0 and not p.errors, p)
    return ok


def test_margin_guard() -> bool:
    print("\n=== 38. margin guard (small accounts on high leverage) ===")
    ok = True
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
                         volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)

    class MarginGw(FakeGateway):
        def __init__(self, need, free, **kw):
            super().__init__(**kw)
            self.need, self.free = need, free

        def margin_status(self, spec, direction, lots):
            return None if self.need is None else (self.need, self.free)
    cfg = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs", use_risk_percent=True)
    pos = [{"direction": "buy", "volume": 0.03, "sl": 2344.0, "price_open": 2350.0, "ticket": 1, "magic": 1}] * 4
    # 1:100 on a $1,000 account: 0.03 lot of gold needs ~$70; 4 open trades risk ~$18 each.
    tight = MarginGw(70.0, 100.0, equity=1000.0, positions=pos)
    d = executor.execute(tight, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("$30 of free margin left after the trade cannot cover ~$90 of stops -> refused, no order",
                not d.executed and "margin guard" in d.reject_reason and tight.orders_sent == [], d.reject_reason)
    roomy = MarginGw(14.0, 700.0, equity=1000.0, positions=pos)
    d = executor.execute(roomy, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("1:500 leaves plenty -> the same entry goes through", d.executed, d.reject_reason)
    unknown = MarginGw(None, 0.0, equity=1000.0, positions=pos)
    d = executor.execute(unknown, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("margin unknown (MT5 silent / backtest) never blocks", d.executed, d.reject_reason)
    off = MarginGw(70.0, 100.0, equity=1000.0, positions=pos)
    d = executor.execute(off, AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs",
                                            margin_guard=False), make_verdict("buy", 3, "full"), spec, 0)
    ok &= check("margin_guard=False switches it off", d.executed, d.reject_reason)
    late = MarginGw(14.0, 700.0, equity=1000.0, positions=pos)

    def news_then_margin_gone(direction, plan):
        late.free = 100.0            # other trades filled during the news check
        return "", "clear"
    d = executor.execute(late, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0,
                         pre_trade_check=news_then_margin_gone)
    ok &= check("re-checked after the news check: margin gone meanwhile -> refused, no order",
                not d.executed and "margin guard" in d.reject_reason and late.orders_sent == [], d.reject_reason)
    return ok


def test_claude_prescreen() -> bool:
    print("\n=== 39. Claude pre-screen (no paid call when no trade is possible) ===")
    ok = True

    def feats(ema20, ema50, macd, sig, rsi, hist, hist_prev, adx, pdi, mdi, close=2400.0, ema200=2300.0):
        return {"trend_bias": {"close": close, "ema200": ema200},
                "primary_indicators": {"ema20": ema20, "ema50": ema50, "atr14": 4.0, "macd_line": macd,
                                       "macd_signal": sig, "rsi14": rsi, "macd_hist": hist,
                                       "macd_hist_prev": hist_prev, "adx14": adx, "plus_di": pdi,
                                       "minus_di": mdi}}
    strong = feats(2395, 2390, 1.0, 0.5, 60, 0.5, 0.3, 30, 30, 15)       # 3 buy legs, confirmed
    weak = feats(2395, 2390, -1.0, 0.5, 45, -0.5, -0.3, 15, 20, 18)      # only the trend leg
    unconfirmed = feats(2390.5, 2390, 1.0, 0.5, 52, 0.2, 0.3, 23, 22, 20)  # 3 legs, none confirmed
    cfg = AdvisorConfig()
    ok &= check("on by default; 3 confirmed legs -> Claude is asked", cfg.claude_prescreen
                and tactics.prescreen_block(cfg, strong) == "")
    ok &= check("1 agreeing leg -> no trade possible, no Claude call",
                "pre-screen" in tactics.prescreen_block(cfg, weak))
    ok &= check("legs agree but none confirmed -> no 'full' possible, no call; with partial conviction "
                "allowed it is asked",
                tactics.prescreen_block(cfg, unconfirmed) != ""
                and tactics.prescreen_block(AdvisorConfig(require_full_conviction=False), unconfirmed) == "")
    ok &= check("--no-prescreen / missing numbers never block",
                tactics.prescreen_block(AdvisorConfig(claude_prescreen=False), weak) == ""
                and tactics.prescreen_block(cfg, {"session": {}}) == "")
    v = backtest.mechanical_verdict({**strong, "smc": {"liquidity_sweep": {"direction": None},
                                                         "premium_discount": {"zone": "discount"}}})
    ok &= check("the backtest stand-in votes with the same shared legs",
                v.direction == "buy" and v.confluence_count == 3 and v.conviction == "full", v)
    return ok


def test_keys_file() -> bool:
    print("\n=== 40. keys.txt: one private file every program reads and the questions write ===")
    import tempfile
    import keys
    ok = True
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "keys.txt")
        created = keys.ensure_file(path, {"ANTHROPIC_API_KEY": "sk-ant-old-1234", "OTHER": "x"})
        text = open(path, encoding="utf-8").read()
        ok &= check("created once from the template, pre-filled with keys already on this PC",
                    created and not keys.ensure_file(path, {}) and "ANTHROPIC_API_KEY=sk-ant-old-1234\n" in text
                    and all(f"\n{n}=" in text for n in keys.NAMES) and "OTHER" not in text
                    and "PRIVATE" in text)
        with open(path, "a", encoding="utf-8") as f:
            f.write('# TELEGRAM_API_ID=999\nTELEGRAM_ALERT_CHAT_ID = "123456789"\n')
        got = keys.read(path)
        ok &= check("read: comments ignored, spaces and quotes stripped",
                    got["TELEGRAM_ALERT_CHAT_ID"] == "123456789" and got["TELEGRAM_API_ID"] == "", got)
        env = {"TELEGRAM_API_ID": "keep-me", "ANTHROPIC_API_KEY": "sk-ant-env"}
        loaded = keys.load(path, env)
        ok &= check("load: file values win, empty values leave the setting alone",
                    env["ANTHROPIC_API_KEY"] == "sk-ant-old-1234" and env["TELEGRAM_API_ID"] == "keep-me"
                    and set(loaded) == {"ANTHROPIC_API_KEY", "TELEGRAM_ALERT_CHAT_ID"}, env)
        before = open(path, encoding="utf-8").read()
        ok &= check("save: updates the line in place, every comment kept",
                    keys.save("TELEGRAM_API_HASH", "abc", path) and keys.read(path)["TELEGRAM_API_HASH"] == "abc"
                    and open(path, encoding="utf-8").read().count("#") == before.count("#")
                    and before.count("TELEGRAM_API_HASH=") == open(path, encoding="utf-8").read().count(
                        "TELEGRAM_API_HASH="))
        ok &= check("save: a name not in the file yet is appended",
                    keys.save("MY_EXTRA", "1", path) and keys.read(path)["MY_EXTRA"] == "1")
        crlf = os.path.join(d, "crlf.txt")
        with open(crlf, "w", encoding="utf-8", newline="") as f:
            f.write("\ufeff# notepad\r\nANTHROPIC_API_KEY=\r\n")
        ok &= check("a Notepad file (BOM, CRLF) is read and saved correctly",
                    keys.save("ANTHROPIC_API_KEY", "sk-ant-new", crlf) and keys.read(crlf)["ANTHROPIC_API_KEY"]
                    == "sk-ant-new" and "sk-ant-new\r\n" in open(crlf, encoding="utf-8-sig", newline="").read())
        missing = os.path.join(d, "none", "keys.txt")
        ok &= check("a missing file reads as empty", keys.read(missing) == {})
        ok &= check("keys.txt sits in the GoldTrader folder; the self-tests use a throwaway copy",
                    keys.KEYS_FILE == os.path.join(paths.PACKAGE_ROOT, "keys.txt")
                    and keys.keys_path() != keys.KEYS_FILE, keys.keys_path())
        old = os.environ["GOLDTRADER_KEYS_FILE"]
        os.environ["GOLDTRADER_KEYS_FILE"] = path
        try:
            saved = first_run.save_permanent("TELEGRAM_SOURCE_CHANNELS", "@goldsignals")
            ok &= check("the setting questions save into keys.txt (and this process)",
                        saved and keys.read(path)["TELEGRAM_SOURCE_CHANNELS"] == "@goldsignals"
                        and os.environ.get("TELEGRAM_SOURCE_CHANNELS") == "@goldsignals")
        finally:
            os.environ.pop("TELEGRAM_SOURCE_CHANNELS", None)
            os.environ["GOLDTRADER_KEYS_FILE"] = old
    lines = []
    answers = iter(["123456789", "n"])                   # chat id, relay: no
    secrets = iter(["sk-ant-abcdefghijklmnop", "-"])     # API key, bot token: skip
    first_run.run_wizard(os.path.join(tempfile.gettempdir(), "no_such_settings.ini"), env={},
                         ask=lambda p: next(answers), ask_secret=lambda p: next(secrets),
                         out=lines.append, saver=lambda n, v: False,
                         marker=os.path.join(tempfile.mkdtemp(), "marker"))
    ok &= check("keys.txt not writable: the questions say so instead of claiming it was saved",
                sum("Could not write" in x for x in lines) == 2
                and any("0 setting(s) saved" in x for x in lines), lines[-1:])
    gi = os.path.join(os.path.dirname(paths.PACKAGE_ROOT), ".gitignore")
    if os.path.exists(gi):
        ok &= check("keys.txt is in .gitignore - never uploaded",
                    "GoldTrader/keys.txt" in open(gi, encoding="utf-8").read())
    return ok


def test_three_legs_to_claude_to_trade() -> bool:
    print("\n=== 41. 3/3 legs -> Claude asked -> 'full' executed (live), anything less not ===")
    ok = True
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
                         volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)

    def feats(ema20, ema50, macd, sig, rsi, hist, hist_prev, adx, pdi, mdi):
        return {"trend_bias": {"close": 2400.0, "ema200": 2300.0},
                "primary_indicators": {"ema20": ema20, "ema50": ema50, "atr14": 4.0, "macd_line": macd,
                                       "macd_signal": sig, "rsi14": rsi, "macd_hist": hist,
                                       "macd_hist_prev": hist_prev, "adx14": adx, "plus_di": pdi,
                                       "minus_di": mdi}}
    three_buy = feats(2395, 2390, 1.0, 0.5, 60, 0.5, 0.3, 30, 30, 15)          # trend, momentum, strength: buy
    one_leg = feats(2395, 2390, -1.0, 0.5, 45, -0.5, -0.3, 15, 20, 18)         # trend only
    unconfirmed = feats(2390.5, 2390, 1.0, 0.5, 52, 0.2, 0.3, 23, 22, 20)      # 3 buy legs, none confirmed
    legs = tactics.vote_legs(three_buy)
    ok &= check("the sample bar really has all 3 legs pointing buy, confirmed",
                all(d == "buy" and c for d, c in legs.values()), legs)

    originals = (main_mod.gw, main_mod.market_intel.build_feature_snapshot, main_mod.claude_advisor.get_verdict,
                 main_mod.news_check.check_before_trade, main_mod.telegram_alert.send_alert,
                 main_mod.claude_paused)

    def cycle(features, answer):
        asked = []
        fake = FakeRunOnceGateway(bid=2350.0, ask=2350.2)
        main_mod.gw = fake
        main_mod.market_intel.build_feature_snapshot = lambda g, c: dict(features)

        def claude(client, c, f):
            asked.append(f)
            return answer
        main_mod.claude_advisor.get_verdict = claude
        cfg = AdvisorConfig(dry_run=False, use_risk_percent=False, log_dir="/tmp/claudesmc_selftest_logs",
                            claude_pause_filename="", send_performance_digest=False)   # = start.bat --live
        day = main_mod.DayRoll()
        main_mod.run_once(object(), cfg, spec, day)
        return asked, fake.orders_sent, day.trades_today

    try:
        main_mod.news_check.check_before_trade = lambda *a, **kw: news_check.NewsCheckResult(ran=True)
        main_mod.telegram_alert.send_alert = lambda *a, **kw: True
        main_mod.claude_paused = lambda c, gateway=None: False

        asked, orders, n = cycle(three_buy, make_verdict("buy", 3, "full"))
        ok &= check("3/3 buy legs: Claude is asked once, with the bar's indicators; its 'full' BUY is sent "
                    "to MT5 as a live order and counted",
                    len(asked) == 1 and asked[0]["primary_indicators"]["adx14"] == 30
                    and len(orders) == 1 and orders[0][0] == "buy" and n == 1, (len(asked), orders))
        asked, orders, n = cycle(three_buy, make_verdict("sell", 3, "full"))
        ok &= check("Claude's direction is the one traded (a 'full' SELL -> a sell order)",
                    len(orders) == 1 and orders[0][0] == "sell", orders)
        asked, orders, _ = cycle(three_buy, make_verdict("buy", 3, "partial"))
        ok &= check("3/3 legs but Claude says 'partial': asked, NO order", len(asked) == 1 and orders == [])
        asked, orders, _ = cycle(three_buy, make_verdict("none", 0, "none"))
        ok &= check("3/3 legs but Claude says no trade: asked, NO order", len(asked) == 1 and orders == [])
        asked, orders, _ = cycle(one_leg, make_verdict("buy", 3, "full"))
        ok &= check("only 1 leg agrees: Claude is NOT asked (no trade possible), no order",
                    asked == [] and orders == [])
        asked, orders, _ = cycle(unconfirmed, make_verdict("buy", 3, "full"))
        ok &= check("3 legs agree but none confirmed ('full' needs one): Claude NOT asked, no order",
                    asked == [] and orders == [])
    finally:
        (main_mod.gw, main_mod.market_intel.build_feature_snapshot, main_mod.claude_advisor.get_verdict,
         main_mod.news_check.check_before_trade, main_mod.telegram_alert.send_alert,
         main_mod.claude_paused) = originals
    return ok


class DashGateway(FakeGateway):
    """FakeGateway plus what status_report.py reads for the web dashboard."""
    def __init__(self, files_dir, closed=(), fail_account=False, **kw):
        super().__init__(**kw)
        self.files_dir, self.closed, self.fail_account = files_dir, list(closed), fail_account
        self.common = {"claudesmc_pause.txt": "paused", "claudesmc_last_verdict.txt": "BUY 3/3 full - executed"}

    def account_summary(self):
        if self.fail_account:
            raise RuntimeError("account_info() failed")
        return {"balance": 10000.0, "equity": self.equity, "margin": 70.0, "margin_free": 9900.0,
                "currency": "USD", "leverage": 500}

    def closed_trades(self, symbol, magics, lookback_days=14):
        return [t for t in self.closed if t["magic"] in magics]

    def read_common_file(self, name):
        return self.common.get(name)

    def terminal_files_dir(self):
        return self.files_dir


def test_status_report() -> bool:
    print("\n=== 42. dashboard file logs/status.json (web page data) ===")
    import tempfile
    import status_report as SR
    ok = True
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
                         volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)
    with tempfile.TemporaryDirectory() as d:
        logs, files = os.path.join(d, "logs"), os.path.join(d, "Files")
        os.makedirs(logs), os.makedirs(files)
        with open(os.path.join(files, "TelegramSMC_Signals.csv"), "wb") as f:     # MT5 FILE_ANSI (cp1252)
            f.write(('time_utc,chat_id,action,direction,accepted,sanity_reason,raw_text\r\n'
                     '"2026.09.24 09:00:00","-100","OPEN","BUY","1","","GOLD BUY 4300 \u2013 SL 4290"\r\n'
                     '"2026.09.24 09:05:00","-100","OPEN","SELL","0","spread 60 points","<b>SELL</b> now"\r\n'
                     ).encode("cp1252"))
        with open(os.path.join(logs, "decisions.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["time", "direction", "conviction", "reasoning"])
            for i in range(50):
                w.writerow([f"t{i}", "buy", "full", "line one\nline two"])
        t0 = datetime(2026, 9, 20, 14, tzinfo=timezone.utc)
        closed = [{"time": t0 + timedelta(hours=i), "magic": 20260921 if i % 2 else 20260922,
                   "direction": "buy", "pnl_dollars": 6.0 if i % 3 else -6.0, "volume": 0.01, "ticket": i}
                  for i in range(12)]
        pos = [{"ticket": 7, "magic": 20260922, "direction": "sell", "volume": 0.03, "price_open": 4300.0,
                "sl": 4306.0, "tp": 0.0, "profit": -1.5, "time": t0}]
        cfg = AdvisorConfig(dry_run=False, log_dir=logs, shared_cap_magic_numbers=[20260922])
        day = main_mod.DayRoll()
        day.day_start_equity, day.trades_today = 10000.0, 2
        g = DashGateway(files, closed=closed, equity=10012.5, positions=pos)
        SR._state.update(last=0.0, scorecard=None, scorecard_at=0.0)
        rep = SR.build(g, cfg, spec, day, now=1_790_000_000)
        ok &= check("Claude's hours are also given in the owner's clock (Oman)",
                    rep["claude_hours_local"] and rep["local_zone"] == "Oman", rep.get("claude_hours_local"))
        ok &= check("account, day, pause state and last verdict are reported",
                    rep["account"]["equity"] == 10012.5 and rep["day"]["start_equity"] == 10000.0
                    and rep["claude_paused"] is True and "executed" in rep["last_verdict"]
                    and rep["mode"] == "live" and rep["problems"] == [], rep.get("problems"))
        ok &= check("open positions are tagged by source (Telegram magic from --shared-cap-magic)",
                    rep["positions"][0]["source"] == "Telegram" and rep["positions"][0]["profit"] == -1.5)
        ok &= check("closed trades newest first, tagged Claude/Telegram",
                    len(rep["closed"]) == 12 and rep["closed"][0]["ticket"] == "11"
                    and {c["source"] for c in rep["closed"]} == {"Claude", "Telegram"})
        sc = rep["scorecard"]
        ok &= check("the scorecard is computed live per source with its verdict",
                    set(sc) == {"Claude", "Telegram", "Combined"} and sc["Combined"]["trades"] == 12
                    and sc["Claude"]["verdict"] == "TOO EARLY", {k: v.get("verdict") for k, v in sc.items()})
        ok &= check("Claude decisions: the last 40, newest first, multi-line reasoning kept",
                    len(rep["decisions"]) == SR.MAX_DECISIONS and rep["decisions"][0]["time"] == "t49"
                    and rep["decisions"][0]["reasoning"] == "line one\nline two")
        sig = rep["signals"]
        ok &= check("the EA's ANSI signal log is read (cp1252 dash kept), newest first, raw text as-is",
                    len(sig) == 2 and sig[0]["sanity_reason"] == "spread 60 points"
                    and sig[0]["raw_text"] == "<b>SELL</b> now" and "\u2013" in sig[1]["raw_text"], sig)
        g_bad = DashGateway(os.path.join(d, "nowhere"), closed=closed, fail_account=True, equity=1.0)
        rep = SR.build(g_bad, cfg, spec, day, now=1_790_000_000)
        ok &= check("a failing part is left out and named; everything else is still reported",
                    "account" not in rep and any("account" in p for p in rep["problems"])
                    and len(rep["closed"]) == 12 and rep["signals"] == [], rep["problems"])
        SR.note_claude_problem("Claude API billing error (HTTP 402) - out of credits")
        stopped = SR.build(g, cfg, spec, day, now=1_790_000_000)["claude_problem"]
        SR.note_claude_problem("")
        ok &= check("a Claude stop that needs you (credits, key) is reported until Claude answers again",
                    "out of credits" in stopped and SR.build(g, cfg, spec, day, now=1_790_000_000)["claude_problem"] == "")
        SR._state.update(last=0.0)
        wrote = [SR.maybe_write(g, cfg, spec, day, now=1000.0), SR.maybe_write(g, cfg, spec, day, now=1030.0),
                 SR.maybe_write(g, cfg, spec, day, now=1061.0)]
        path = os.path.join(logs, "status.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        ok &= check("written at most once a minute, atomically (no temp file left), valid JSON",
                    wrote == [True, False, True] and data["version"] == 1
                    and not os.path.exists(path + ".tmp"), wrote)
        SR._state.update(last=0.0)
        ro = AdvisorConfig(log_dir=os.path.join(path, "not-a-dir"))
        ok &= check("an unwritable logs folder never raises (trading unaffected)",
                    SR.maybe_write(g, ro, spec, day, now=5000.0) is False)
        SR._state.update(last=0.0, scorecard=None, scorecard_at=0.0, warned=False)
    return ok


def test_dashboard_password() -> bool:
    print("\n=== 43. web dashboard password (salted PBKDF2, never the password itself) ===")
    import base64
    import hashlib
    import tempfile
    import dashboard_password as DP
    ok = True
    stored = DP.hash_password("correct horse 1", salt=b"0123456789abcdef", iterations=1000)
    scheme, iters, salt, digest = stored.split("$")
    ok &= check("format pbkdf2-sha256$iterations$salt$hash - the one Login.aspx verifies",
                scheme == "pbkdf2-sha256" and iters == "1000" and base64.b64decode(salt) == b"0123456789abcdef"
                and base64.b64decode(digest) == hashlib.pbkdf2_hmac("sha256", b"correct horse 1",
                                                                    b"0123456789abcdef", 1000, 32))
    ok &= check("right password verifies, wrong or garbled ones do not",
                DP.verify("correct horse 1", stored) and not DP.verify("correct horse 2", stored)
                and not DP.verify("x", "nonsense") and "correct horse" not in stored)
    ok &= check("a new random salt every time (two hashes of one password differ)",
                DP.hash_password("same password") != DP.hash_password("same password"))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "App_Data", "password.txt")
        answers = iter(["short", "long enough 1", "different 12", "long enough 1", "long enough 1"])
        lines = []
        DP.PASSWORD_FILE, old = path, DP.PASSWORD_FILE     # never the real file
        try:
            rcs = [DP.main(ask=lambda p: next(answers), out=lines.append) for _ in range(3)]
        finally:
            DP.PASSWORD_FILE = old
        ok &= check("too short or not repeated the same: nothing saved; otherwise saved as a hash",
                    rcs == [1, 1, 0] and os.path.exists(path)
                    and DP.verify("long enough 1", open(path).read()), (rcs, lines))
    gi = os.path.join(os.path.dirname(paths.PACKAGE_ROOT), ".gitignore")
    if os.path.exists(gi):
        ok &= check("the password file is in .gitignore",
                    "GoldTrader/dashboard/App_Data/password.txt" in open(gi, encoding="utf-8").read())
    return ok


class FakeMt5Journal:
    """history_deals_get() + history_orders_get() for mt5_gateway.journal_positions()."""
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_OUT_BY = 0, 1, 3
    DEAL_TYPE_BUY, DEAL_TYPE_SELL = 0, 1

    class Deal:
        def __init__(self, pid, magic, entry, type_, time, price, volume, profit=0.0,
                     commission=0.0, reason=3, symbol="XAUUSD"):
            self.position_id, self.magic, self.entry, self.type = pid, magic, entry, type_
            self.time, self.price, self.volume, self.profit = time, price, volume, profit
            self.commission, self.swap, self.fee, self.reason = commission, 0.0, 0.0, reason
            self.symbol, self.comment = symbol, "tg"

    class Order:
        def __init__(self, pid, time_setup, sl):
            self.position_id, self.time_setup, self.sl = pid, time_setup, sl

    def __init__(self, deals, orders):
        self._deals, self._orders = deals, orders

    def history_deals_get(self, date_from, date_to):
        return self._deals

    def history_orders_get(self, date_from, date_to):
        return self._orders


def test_trade_journal() -> bool:
    print("\n=== 44. trade journal for Google Drive (every closed trade, decisions, signals) ===")
    import trade_journal as TJ
    ok = True
    t0 = int(datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc).timestamp())
    D, O, M = FakeMt5Journal.Deal, FakeMt5Journal.Order, FakeMt5Journal
    fake = M([
        # Telegram BUY: stop moved by hand to +$2.86, filled at 4295.00 (the real first trade).
        D(201, 20260922, M.DEAL_ENTRY_IN, M.DEAL_TYPE_BUY, t0, 4293.14, 1.66, commission=-5.0),
        D(201, 0, M.DEAL_ENTRY_OUT, M.DEAL_TYPE_SELL, t0 + 3600, 4295.00, 1.66, profit=308.76, reason=4),
        # Claude SELL closed in two parts: EA close, then the locked stop (+1R less $0.40 slippage).
        D(202, 20260921, M.DEAL_ENTRY_IN, M.DEAL_TYPE_SELL, t0 + 60, 4300.0, 1.0),
        D(202, 20260921, M.DEAL_ENTRY_OUT, M.DEAL_TYPE_BUY, t0 + 600, 4296.0, 0.5, profit=200.0, reason=3),
        D(202, 20260921, M.DEAL_ENTRY_OUT, M.DEAL_TYPE_BUY, t0 + 1200, 4294.4, 0.5, profit=280.0, reason=4),
        # Telegram BUY stopped at the original -$6 stop.
        D(203, 20260922, M.DEAL_ENTRY_IN, M.DEAL_TYPE_BUY, t0 + 90, 4280.0, 1.0),
        D(203, 20260922, M.DEAL_ENTRY_OUT, M.DEAL_TYPE_SELL, t0 + 900, 4274.0, 1.0, profit=-600.0, reason=4),
        # Still open, another symbol, another magic: all left out.
        D(204, 20260922, M.DEAL_ENTRY_IN, M.DEAL_TYPE_BUY, t0 + 100, 4290.0, 1.0),
        D(205, 20260922, M.DEAL_ENTRY_IN, M.DEAL_TYPE_BUY, t0, 1.1, 1.0, symbol="EURUSD"),
        D(205, 20260922, M.DEAL_ENTRY_OUT, M.DEAL_TYPE_SELL, t0 + 50, 1.2, 1.0, symbol="EURUSD"),
        D(206, 999, M.DEAL_ENTRY_IN, M.DEAL_TYPE_BUY, t0, 4290.0, 1.0),
        D(206, 999, M.DEAL_ENTRY_OUT, M.DEAL_TYPE_SELL, t0 + 50, 4291.0, 1.0, reason=0),
    ], [O(201, t0, 4287.14), O(202, t0 + 60, 4306.0), O(203, t0 + 90, 4274.0)])
    gw._mt5, old_clock = fake, gw._CLOCK["fixed_offset"]
    gw._CLOCK["fixed_offset"] = 0            # server clock = UTC here
    try:
        pos = gw.journal_positions("XAUUSD", [20260921, 20260922])
    finally:
        gw._mt5, gw._CLOCK["fixed_offset"] = None, old_clock
    ok &= check("only closed positions of this symbol and both magics, oldest close first",
                [p["ticket"] for p in pos] == [203, 202, 201], [p["ticket"] for p in pos])
    by = {p["ticket"]: p for p in pos}
    ok &= check("a position closed in two parts is one row: exit price volume-weighted, last reason",
                abs(by[202]["exit_price"] - 4295.2) < 1e-9 and by[202]["volume"] == 1.0
                and by[202]["exit_reason"] == "stop loss" and by[202]["direction"] == "sell")
    ok &= check("money over all deals (entry commission too), first stop from the order",
                abs(by[201]["net"] - 303.76) < 1e-9 and by[201]["initial_sl"] == 4287.14
                and by[201]["magic"] == 20260922, by[201])

    names = {20260921: "Claude", 20260922: "Telegram"}
    rows = {r["ticket"]: r for r in TJ.trade_rows(pos, names, 6.0, "Asia/Muscat")}
    r1 = rows[201]
    ok &= check("the hand-moved stop is flagged; R = move / $6 stop",
                r1["note"] == "stop moved by hand" and r1["result_r"] == 0.31 and r1["move"] == 1.86
                and r1["source"] == "Telegram" and r1["exit_reason"] == "stop loss", r1)
    ok &= check("the EA's own stops are not flagged (-1R, and the +1R lock with slippage)",
                rows[203]["note"] == "" and rows[203]["result_r"] == -1.0
                and rows[202]["note"] == "" and rows[202]["result_r"] == 0.8, (rows[203], rows[202]))
    be_rows = {r["ticket"]: r for r in TJ.trade_rows(pos, names, 6.0, "Asia/Muscat", breakeven_magics=(20260922,))}
    ok &= check("Telegram split: half B stopped at break-even or better is the rule, not 'moved by hand'",
                be_rows[201]["note"] == "" and be_rows[203]["note"] == "" and be_rows[203]["result_r"] == -1.0,
                (be_rows[201]["note"], be_rows[203]["note"]))
    ok &= check("times in UTC and Oman (UTC+4), minutes open",
                r1["open_time_utc"] == "2026-09-25 09:00:00" and r1["open_time_local"] == "2026-09-25 13:00:00"
                and r1["minutes_open"] == 60, r1)

    # A Telegram trade opened with the signal's own $14 stop: 1R is $14, not $6.
    sig = [{"ticket": 301, "magic": 20260922, "direction": "sell", "volume": 0.71, "open_time": None,
            "close_time": None, "entry_price": 4328.3, "exit_price": 4342.3, "initial_sl": 4342.3,
            "exit_reason": "stop loss", "profit": -994.0, "swap": 0.0, "commission": 0.0, "net": -994.0},
           {"ticket": 302, "magic": 20260922, "direction": "sell", "volume": 0.71, "open_time": None,
            "close_time": None, "entry_price": 4328.3, "exit_price": 4322.3, "initial_sl": 4342.3,
            "exit_reason": "stop loss", "profit": 426.0, "swap": 0.0, "commission": 0.0, "net": 426.0}]
    srow = {r["ticket"]: r for r in TJ.trade_rows(sig, names, 6.0, "Asia/Muscat", 6.0)}
    ok &= check("a signal-stop trade: R against its own $14 stop (-1R at the stop, +0.43R at the $6 lock), "
                "never flagged as moved by hand",
                srow[301]["result_r"] == -1.0 and srow[301]["stop_distance"] == 14.0 and srow[301]["note"] == ""
                and srow[302]["result_r"] == 0.43 and srow[302]["note"] == "", srow)
    import scorecard as SC
    s14 = SC.summarize([{"pnl_dollars": -994.0, "volume": 0.71, "risk_distance": 14.0},
                        {"pnl_dollars": -600.0, "volume": 1.0}], 100.0, 6.0)
    ok &= check("scorecard: a trade with its own stop distance is -1R at that stop; without, the $6 stop",
                abs(s14["avg_r"] - (-1.0)) < 0.01, s14)
    gw._mt5, old_clock = fake, gw._CLOCK["fixed_offset"]
    gw._CLOCK["fixed_offset"] = 0
    try:
        ct = {t["ticket"]: t for t in gw.closed_trades("XAUUSD", [20260921, 20260922])}
    finally:
        gw._mt5, gw._CLOCK["fixed_offset"] = None, old_clock
    ok &= check("closed_trades carries each trade's first-stop distance for the scorecard",
                abs(ct[203]["risk_distance"] - 6.0) < 1e-9 and abs(ct[202]["risk_distance"] - 6.0) < 1e-9, ct)

    class FakeGateway:
        def __init__(self, positions):
            self.positions = positions

        def journal_positions(self, symbol, magics):
            return self.positions

        def price_distance_for_dollars(self, spec, dollars, lots):
            return 6.0

        def terminal_files_dir(self):
            return self.files_dir

    with _tempfile.TemporaryDirectory() as d:
        cfg = AdvisorConfig()
        cfg.log_dir = os.path.join(d, "logs")
        drive = os.path.join(d, "MyMQChartDrive")
        os.makedirs(drive)
        cfg.journal_folder = os.path.join(drive, "GoldTrader")
        os.makedirs(cfg.log_dir)
        with open(os.path.join(cfg.log_dir, "decisions.csv"), "w", encoding="utf-8") as f:
            f.write("time,conviction\n2026-09-25,full\n")
        g = FakeGateway(pos)
        g.files_dir = os.path.join(d, "mt5files")
        os.makedirs(g.files_dir)
        with open(os.path.join(g.files_dir, "TelegramSMC_Signals.csv"), "w", encoding="utf-16") as f:
            f.write("time_utc,action\r\n2026-09-25,OPEN\r\n")
        changed = TJ.write_all(g, cfg, None)
        in_drive = sorted(os.listdir(cfg.journal_folder))
        ok &= check("the Drive subfolder is created and gets trades, Claude decisions and signals",
                    in_drive == sorted([TJ.TRADES_FILE, TJ.DECISIONS_FILE, TJ.SIGNALS_FILE])
                    and len(changed) == 6, (in_drive, changed))
        text = open(os.path.join(cfg.journal_folder, TJ.TRADES_FILE), encoding="utf-8-sig").read()
        sig = open(os.path.join(cfg.journal_folder, TJ.SIGNALS_FILE), encoding="utf-8-sig").read()
        ok &= check("readable CSV: header + one row per trade; the EA's UTF-16 log becomes UTF-8",
                    text.splitlines()[0] == ",".join(TJ.COLUMNS) and len(text.splitlines()) == 4
                    and sig == "time_utc,action\n2026-09-25,OPEN\n", sig)
        ok &= check("unchanged content is not rewritten (Drive uploads only real changes)",
                    TJ.write_all(g, cfg, None) == [])
        auto_base = os.path.join(d, "G", "My Drive", "MyMQChartDrive")
        os.makedirs(auto_base)
        real_candidates = TJ.drive_candidates
        TJ.drive_candidates = lambda: [os.path.join(d, "G", "MyDrive", "MyMQChartDrive"), auto_base]
        TJ._state.pop("found", None)
        TJ._state["next_scan"] = 0.0
        try:
            cfg.journal_folder = "auto"
            TJ.write_all(g, cfg, None)
        finally:
            TJ.drive_candidates = real_candidates
            TJ._state.pop("found", None)
            TJ._state["next_scan"] = 0.0
        ok &= check("'auto' finds MyMQChartDrive under 'My Drive' (with a space) and writes to its "
                    "GoldTrader subfolder",
                    os.path.exists(os.path.join(auto_base, "GoldTrader", TJ.TRADES_FILE)))
        cfg.journal_folder = os.path.join(d, "no-such-drive", "GoldTrader")
        TJ._state["warned"].clear()
        TJ.write_all(g, cfg, None)
        ok &= check("Drive missing: the journal stays in logs\\journal, no error",
                    not os.path.exists(cfg.journal_folder)
                    and os.path.exists(os.path.join(cfg.log_dir, "journal", TJ.TRADES_FILE)))

        class Broken:
            def journal_positions(self, symbol, magics):
                raise RuntimeError("MT5 gone")

        TJ._state["last"] = 0.0
        ok &= check("a failure never reaches trading (maybe_write returns False, no exception)",
                    TJ.maybe_write(Broken(), cfg, None) is False)
        TJ._state["last"] = 0.0
    return ok


def test_signal_replay() -> bool:
    print("\n=== 45. signal-history replay (the EA's parser and rules on past messages) ===")
    import ea_signal_parser as EP
    import signal_replay as SR
    ok = True
    cases = {
        "Gold Short Zone:4328.3-4338.3\n\nStop: 4342.3\n\nTarget 1: 4324.3\nTarget 2: 4320": ("sell", 4328.3, 4338.3, 4342.3),
        "XAUUSD SELL STOP 2350 SL 2360 TP 2340": ("sell", 2350.0, 0.0, 2360.0),
        "Gold sell stop 4330\nStop: 4340": ("sell", 4330.0, 0.0, 4340.0),
        "Gold buy-stop 4300 stop loss 4290": ("buy", 4300.0, 0.0, 4290.0),
        "Gold buy - stop hunt done 4300-4295, target 4310": ("buy", 4300.0, 4295.0, 0.0),
        "Gold sell 4330-4335 don't stop believing, stop at 4341": ("sell", 4330.0, 4335.0, 4341.0),
        "XAUUSD BUY 2350-2345 SL 2340 TP1 2360 TP2 2370": ("buy", 2350.0, 2345.0, 2340.0),
    }
    got = {m: (x.direction, x.entry_a, x.entry_b, x.sl) for m, x in ((m, EP.parse(m)) for m in cases)}
    ok &= check("the Python parser reads messages exactly like the EA (zone, 'Stop:', never 'SELL STOP' as the stop)",
                got == cases, {m[:30]: (got[m], cases[m]) for m in cases if got[m] != cases[m]})
    ok &= check("CLOSE / CANCEL messages and TP list",
                EP.parse("Close all gold trades now").action == EP.CLOSE
                and EP.parse("cancel the gold buy limit").action == EP.CANCEL
                and EP.parse("XAUUSD BUY 2350-2345 SL 2340 TP1 2360 TP2 2370").tps == [2360.0, 2370.0])

    def bars_from(start, path):
        rows, prev = [], path[0]
        for i, c in enumerate(path):
            rows.append({"time": start + timedelta(minutes=i), "open": prev, "high": max(prev, c),
                         "low": min(prev, c), "close": c})
            prev = c
        return pd.DataFrame(rows)

    def lin(a, b, n):
        return [a + (b - a) * (i + 1) / n for i in range(n)]

    t0 = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)             # Tuesday 10:00 Oman
    # The single-position rule (InpTelegramSplit=false: lock +$6, trail $3):
    st = SR.Settings(start_equity=10000, spread=0.30, htf_filter=False, split=False)
    path = [4330.0] * 121 + lin(4330, 4318, 10) + lin(4318, 4330, 10) + [4330.0] * 5
    msg = {"time": t0 + timedelta(minutes=120, seconds=30), "media": "",
           "text": "Gold Short Zone:4328.3-4338.3\n\nStop: 4342.3\n\nTarget 1: 4324.3\nTarget 2: 4320"}
    res = SR.run([msg], SR.Prices(bars_from(t0, path), htf=False), st)
    a = {v: r.trades[0] for v, r in res.items() if r.trades}
    ok &= check("split off: your zone sell at 4330; fixed $6 stop locks +$6 and trails out at 4321.30 = +1.45R",
                a["fixed"]["entry"] == 4330.0 and a["fixed"]["stop"] == 4336.0 and a["fixed"]["lots"] == 0.33
                and a["fixed"]["exit"] == 4321.3 and a["fixed"]["exit_reason"] == "trail"
                and a["fixed"]["result_r"] == 1.45, a.get("fixed"))
    ok &= check("same trade with the signal's stop (4342.30, $12.30 away): 0.16 lot (still 2%), +0.71R",
                a["signal"]["stop"] == 4342.3 and a["signal"]["stop_from"] == "signal"
                and a["signal"]["lots"] == 0.16 and a["signal"]["result_r"] == 0.71, a.get("signal"))
    ok &= check("provider reference: its stop and 'Target 1: 4324.3' -> +0.46R",
                a["provider"]["exit"] == 4324.3 and a["provider"]["exit_reason"] == "target"
                and a["provider"]["result_r"] == 0.46, a.get("provider"))
    t1 = t0 + timedelta(days=1)
    path = [4350.0] * 121 + lin(4350, 4336, 3) + lin(4336, 4352, 8) + lin(4352, 4340, 6) + [4340.0] * 3
    msg = {"time": t1 + timedelta(minutes=120, seconds=10), "media": "", "text": "XAUUSD BUY 4340-4345 SL 4335 TP 4360"}
    res = SR.run([msg], SR.Prices(bars_from(t1, path), htf=False), st)
    b = {v: r.trades[0] for v, r in res.items() if r.trades}
    ok &= check("buy limit at 4345: the $6 stop is hit in the dip (-1R); the signal's $10 stop survives and "
                "locks +$6 (+0.6R)",
                b["fixed"]["order"] == "LIMIT" and b["fixed"]["result_r"] == -1.0
                and b["signal"]["result_r"] == 0.6 and b["signal"]["exit"] == 4351.0, b)
    t2 = t0 + timedelta(days=2)
    msgs = [(100, "Gold sell now 4330-4333 SL 4340"), (150, "Close all gold trades now"),
            (160, "Gold sell 4320-4325 SL 4332"), (170, "Gold buy 4200-4205 SL 4190"), (180, "Good morning traders!")]
    res = SR.run([{"time": t2 + timedelta(minutes=m, seconds=5), "media": "", "text": x} for m, x in msgs],
                 SR.Prices(bars_from(t2, [4330.0] * 200), htf=False), st)
    r = res["fixed"]
    ok &= check("CLOSE message closes the trade; stale zone, too far from price and chat are not traded",
                [x["exit_reason"] for x in r.trades] == ["close message"]
                and r.skips["price already beyond the zone (stale)"] == 1
                and r.skips["price more than $20 from the zone"] == 1 and r.skips["not a trade message"] == 1,
                dict(r.skips))
    ok &= check("Telegram hours: Saturday and 23:30 Oman out, 22:30 Oman in",
                SR.hours_reason(datetime(2026, 9, 26, 8, 0, tzinfo=timezone.utc), st) != ""
                and SR.hours_reason(datetime(2026, 9, 22, 19, 30, tzinfo=timezone.utc), st) != ""
                and SR.hours_reason(datetime(2026, 9, 22, 18, 30, tzinfo=timezone.utc), st) == "")
    t3 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    up = [4000 + i * 0.01 for i in range(7 * 24 * 60)]
    mt = t3 + timedelta(days=6, hours=6, seconds=30)
    px = 4000 + ((mt - t3).total_seconds() // 60) * 0.01
    pr = SR.Prices(bars_from(t3, up), htf=True)
    st2 = SR.Settings(start_equity=10000, spread=0.30, htf_filter=True, split=False)
    sell = SR.run([{"time": mt, "media": "", "text": f"Gold sell {px - 1:.1f}-{px + 2:.1f} SL {px + 8:.1f}"}], pr, st2)
    buy = SR.run([{"time": mt, "media": "", "text": f"Gold buy {px - 2:.1f}-{px + 1:.1f} SL {px - 8:.1f}"}], pr, st2)
    ok &= check("M15/H1 filter: a sell into a clear uptrend is skipped, the buy is taken",
                sell["fixed"].skips.get("XTR HTF filter: M15 is clearly bullish") == 1
                and len(buy["fixed"].trades) == 1, (dict(sell["fixed"].skips), len(buy["fixed"].trades)))
    # --- the EA as shipped: two halves (A: take-profit +$4; B: break-even there, then $3 trail) ---
    sp = SR.Settings(start_equity=10000, spread=0.30, htf_filter=False)
    path = [4330.0] * 121 + lin(4330, 4318, 10) + lin(4318, 4330, 10) + [4330.0] * 5
    msg = {"time": t0 + timedelta(minutes=120, seconds=30), "media": "",
           "text": "Gold Short Zone:4328.3-4338.3\n\nStop: 4342.3\n\nTarget 1: 4324.3\nTarget 2: 4320"}
    legs = {x["leg"]: x for x in SR.run([msg], SR.Prices(bars_from(t0, path), htf=False), sp)["fixed"].trades}
    ok &= check("split: your zone sell, 0.33 lot -> 0.16 closes at +$4 (4326, +0.67R); 0.17 goes to break-even "
                "at +$4 and trails $3 out at 4321.30 (+1.45R)",
                set(legs) == {"A", "B"} and legs["A"]["lots"] == 0.16 and legs["B"]["lots"] == 0.17
                and legs["A"]["exit"] == 4326.0 and legs["A"]["exit_reason"] == "target"
                and legs["A"]["result_r"] == 0.67 and legs["B"]["exit"] == 4321.3
                and legs["B"]["exit_reason"] == "trail" and legs["B"]["result_r"] == 1.45
                and legs["A"]["stop"] == legs["B"]["stop"] == 4336.0, legs)
    back = [4330.0] * 121 + lin(4330, 4325.7, 4) + lin(4325.7, 4340, 12) + [4340.0] * 5
    legs = {x["leg"]: x for x in SR.run([msg], SR.Prices(bars_from(t0, back), htf=False), sp)["fixed"].trades}
    ok &= check("split: +$4 reached then a reversal -> half A banked +$4, half B out at +$1 (break-even step, "
                "never a loss)",
                legs["A"]["exit"] == 4326.0 and legs["B"]["exit"] == 4329.0 and legs["B"]["move"] == 1.0, legs)
    small = SR.run([msg], SR.Prices(bars_from(t0, path), htf=False),
                   SR.Settings(start_equity=300, spread=0.30, htf_filter=False))["fixed"].trades
    ok &= check("split: a 0.01 lot cannot be halved -> one position that takes profit at +$4",
                len(small) == 1 and small[0]["leg"] == "A" and small[0]["lots"] == 0.01
                and small[0]["exit"] == 4326.0, small)
    one_slot = SR.run([msg], SR.Prices(bars_from(t0, path), htf=False),
                      SR.Settings(start_equity=10000, spread=0.30, htf_filter=False, max_per_direction=1))
    ok &= check("split: only one slot of the 5-per-direction cap left -> one position (take-profit +$4)",
                [x["leg"] for x in one_slot["fixed"].trades] == ["A"] and one_slot["fixed"].trades[0]["lots"] == 0.33,
                one_slot["fixed"].trades)
    text = SR.summary_text(res, st, [{}] * 5, "test", "test chat")
    ok &= check("summary names every version and the reasons for not trading",
                all(f"== {v}:" in text for v in SR.VARIANTS) and "not traded:" in text)
    return ok


def test_btc_profile() -> bool:
    print("\n=== 46. BTCUSD profile (own rules, own files, same brain; gold unchanged) ===")
    import backtest
    import econ_calendar
    import news_check
    import profiles
    import status_report as SR
    import trade_journal as TJ
    ok = True
    gold, btc = AdvisorConfig(), profiles.apply(AdvisorConfig(), "btc")
    ok &= check("the gold profile changes nothing", profiles.apply(AdvisorConfig(), "gold") == gold)
    ok &= check("btc: BTCUSD, own magic/log folder/pause/verdict files/journal prefix, companion jobs only, "
                "no Telegram shared cap",
                btc.symbol == "BTCUSD" and btc.magic == profiles.BTC_MAGIC != gold.magic
                and btc.log_dir.endswith(os.path.join("logs", "btc"))
                and btc.claude_pause_filename != gold.claude_pause_filename
                and btc.last_verdict_filename != gold.last_verdict_filename
                and btc.journal_prefix == "BTC_" and btc.run_companions and btc.companion_jobs_only
                and not gold.companion_jobs_only and btc.shared_cap_magic_numbers == [], btc)
    ok &= check("btc rules: stop 1x M15 ATR (0.20%-2.0%), lock +1R, trail 0.5R, 2% risk, 5% daily cap, "
                "3 per direction, 24/7, spread limit 0.06% of price",
                (btc.sl_mode, btc.sl_atr_mult, btc.sl_atr_timeframe, btc.sl_pct_min, btc.sl_pct_max) ==
                ("atr", 1.0, "M15", 0.20, 2.0) and (btc.lock_mode, btc.lock_r, btc.trail_r) == ("r", 1.0, 0.5)
                and (btc.risk_percent, btc.max_daily_loss_pct, btc.max_open_positions_per_direction) == (2.0, 5.0, 3)
                and btc.trade_windows == "" and tactics.parse_days(btc.trade_days) == set(range(7))
                and btc.friday_cutoff_ny == "" and btc.max_spread_points == 0 and btc.max_spread_pct == 0.06)
    ok &= check("gold keeps its tested rules ($6/$6/$3 dollars, 2%, 10%, 5, New York hours)",
                (gold.sl_mode, gold.lock_mode, gold.sl_dollars, gold.tp1_dollars, gold.trail_dollars,
                 gold.risk_percent, gold.max_daily_loss_pct, gold.max_open_positions_per_direction,
                 gold.trade_windows) == ("fixed", "dollars", 6.0, 6.0, 3.0, 2.0, 10.0, 5,
                                         "08:00-16:45,18:15-20:00"))
    args = main_mod.build_parser().parse_args(["--profile", "btc", "--symbol", "BTCUSDm", "--max-daily-loss", "4"])
    c = main_mod.build_config(args)
    ok &= check("start --profile btc applies the profile first; start options still win (--symbol, "
                "--max-daily-loss)",
                c.instrument == "btc" and c.symbol == "BTCUSDm" and c.max_daily_loss_pct == 4.0
                and c.magic == profiles.BTC_MAGIC and c.companion_jobs_only)
    ok &= check("gold start (no --profile) is the unchanged default",
                main_mod.build_config(main_mod.build_parser().parse_args([])).instrument == "gold")

    # --- stop, lock, trail in BTC terms ---
    spec = gw.SymbolSpec(name="BTCUSD", point=0.01, digits=2, stops_level_points=0, spread_points=0,
                         volume_min=0.01, volume_max=100.0, volume_step=0.01, tick_value=0.01, tick_size=0.01)
    bars = make_trending_df(n=40, start=100000.0, drift=0.0, noise=250.0, seed=5)
    fg = FakeGateway(bid=100000.0, ask=100010.0, bars_df=bars, equity=10000.0)   # 2% = $200, under the 5-lot cap
    cfg = dataclasses.replace(btc, dry_run=True, log_dir="/tmp/claudesmc_selftest_logs")
    atr = float(market_intel.atr(bars.iloc[:-1], 14).iloc[-1])
    dist = executor.atr_sl_distance(fg, cfg, spec)
    price = float(bars.iloc[:-1]["close"].iloc[-1])
    ok &= check("stop = 1 x M15 ATR when inside 0.20%-2.0% of price",
                abs(dist - max(price * 0.002, min(atr, price * 0.02))) < 1e-6, (dist, atr))
    tight = dataclasses.replace(cfg, sl_pct_min=5.0, sl_pct_max=6.0)
    ok &= check("the % bounds hold (a floor of 5% of price wins over a smaller ATR)",
                abs(executor.atr_sl_distance(fg, tight, spec) - price * 0.05) < 1e-6)
    plan = executor.build_plan(fg, cfg, spec, "buy")
    risk = plan.entry_price - plan.sl_price
    ok &= check("build_plan: lock at +1R and trail 0.5R of the trade's own stop; lot risks 2% of equity",
                abs(plan.tp1_price - (plan.entry_price + risk)) < 1e-6
                and abs(plan.trail_distance - 0.5 * risk) < 1e-6 and plan.broker_tp == 0.0
                and 200.0 - risk * 0.01 - 1e-6 <= plan.risk_money <= 200.0 + 1e-6, plan)
    wide = FakeGateway(bid=100000.0, ask=100080.0, bars_df=bars)
    d = executor.execute(wide, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("spread above 0.06% of price blocks the entry", not d.executed and "% of price" in d.reject_reason,
                d.reject_reason)

    # --- deep debug: price-scaled thresholds, account-wide margin guard, companion jobs ---
    ok &= check("market orders may fill 2000 points ($20) away for BTC; gold keeps its 30",
                btc.deviation_points == 2000 and gold.deviation_points == 30, (btc.deviation_points,))
    gspec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0, spread_points=25,
                          volume_min=0.01, volume_max=5.0, volume_step=0.01, tick_value=1.0, tick_size=0.01)
    closed = bars.iloc[:-1].reset_index(drop=True)
    ok &= check("sweep threshold: BTC = 5% of the M15 ATR (scales with price); gold = its fixed pips",
                abs(market_intel.sweep_min_pierce(btc, spec, closed)
                    - 0.05 * float(market_intel.atr(closed).iloc[-1])) < 1e-9
                and abs(market_intel.sweep_min_pierce(gold, gspec, closed)
                        - gold.sweep_min_pierce_pips * 0.1) < 1e-9
                and abs(market_intel.sweep_min_pierce(btc, spec, closed.iloc[:5]) - btc.sweep_min_pierce_pips * 0.1) < 1e-9)

    class AccountGw(FakeGateway):
        def __init__(self, other_risk, **kw):
            super().__init__(**kw)
            self.other_risk = other_risk

        def margin_status(self, spec, direction, lots):
            return 50.0, 600.0                  # $550 free after the trade

        def account_open_risk(self):
            return self.other_risk              # gold's open stops on the same account
    mcfg = dataclasses.replace(cfg, margin_guard=True)
    quiet = AccountGw(0.0, bid=100000.0, ask=100010.0, bars_df=bars, equity=10000.0)
    d = executor.execute(quiet, mcfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("margin guard: nothing open elsewhere -> the BTC entry goes through", d.executed, d.reject_reason)
    busy = AccountGw(500.0, bid=100000.0, ask=100010.0, bars_df=bars, equity=10000.0)
    d = executor.execute(busy, mcfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("margin guard counts the whole account: $500 of gold stops + this trade > $550 free -> refused",
                not d.executed and "margin guard" in d.reject_reason and busy.orders_sent == [], d.reject_reason)

    ok &= check("daily budget counts every open stop on the account: $400 of gold stops + a $200 BTC entry "
                "> 5% of $10,000 on a weekday -> refused; fits the 10% weekend cap",
                "daily loss budget" in executor.daily_risk_budget_reason(busy_budget := AccountGw(
                    400.0, bid=100000.0, ask=100010.0, bars_df=bars, equity=10000.0), btc, spec, 10000.0, 200.0)
                and executor.daily_risk_budget_reason(busy_budget, tactics.effective_limits(
                    btc, pd.Timestamp("2026-09-26 12:00", tz="UTC")), spec, 10000.0, 200.0) == ""
                and executor.daily_risk_budget_reason(AccountGw(0.0, bid=100000.0, ask=100010.0, bars_df=bars,
                                                                equity=10000.0), btc, spec, 10000.0, 200.0) == "")

    class BrokenGw(AccountGw):
        def account_open_risk(self):
            raise RuntimeError("MT5 not answering")
    ok &= check("account risk unreadable -> falls back to this symbol's own stops (never crashes)",
                executor.total_open_risk(BrokenGw(0.0, bid=100000.0, ask=100010.0, bars_df=bars), btc, spec) == 0.0)
    gea_src = open(os.path.join(paths.PACKAGE_ROOT, "mt5", "Experts", "UnifiedTrader_EA.mq5"),
                   encoding="utf-8").read()
    ok &= check("gold EA: daily budget, margin guard and Stats all count the whole account",
                "double committed = drawdown + AccountOpenRiskMoney() + newRisk;" in gea_src
                and "double worst = AccountOpenRiskMoney() +" in gea_src
                and "100.0 * AccountOpenRiskMoney() / g_dayStartEquity" in gea_src)

    import services
    started = []

    class Sup:
        def __init__(self, name, command, cwd, fatal=None, on_fatal=None):
            self.name, self.command = name, command

        def start(self):
            started.append(self)

    class Job(Sup):
        def __init__(self, name, command, cwd, every_days, state_path, log_path):
            super().__init__(name, command, cwd)
    with _tempfile.TemporaryDirectory() as tmp:
        ini = os.path.join(tmp, "p.ini")
        with open(ini, "w") as f:
            f.write("[relay_bridge]\nenabled = true\n[xtr_export]\nenabled = true\n"
                    "[ml_retrain]\nenabled = true\n[calibration_report]\nenabled = true\n"
                    "[scorecard]\nenabled = true\n")
        pre = services.load_preset(ini)
        bcfg = dataclasses.replace(btc, log_dir=os.path.join(tmp, "btc"))
        services.start_services(bcfg, pre, force_relay=True, supervisor_cls=Sup, job_cls=Job, jobs_only=True)
        got = {x.name: x.command for x in started}
        sc = got.get("scorecard", [])
        ok &= check("BTC companions: ML retrain, calibration and scorecard on BTCUSD / its magic / logs\\btc; "
                    "no second Telegram relay or price export",
                    set(got) == {"ml_retrain", "calibration_report", "scorecard"}
                    and all("BTCUSD" in cmd and str(profiles.BTC_MAGIC) in cmd for cmd in got.values())
                    and os.path.join(tmp, "btc") in got["ml_retrain"]
                    and sc[sc.index("--telegram-magic") + 1] == "0", got)
    import scorecard
    one = scorecard.build([{"magic": profiles.BTC_MAGIC, "pnl_dollars": 10.0, "volume": 0.1, "risk_distance": 500.0}],
                          {"Claude": profiles.BTC_MAGIC}, 0.01, 500.0)
    two = scorecard.build([], {"Claude": 1, "Telegram signals": 2}, 1.0, 6.0)
    ok &= check("scorecard: one source (BTC) has no duplicate Combined line; gold keeps it",
                set(one) == {"Claude"} and "Combined" in two, (list(one), list(two)))

    # --- weekends: gold closed -> BTC gets gold's weekday allowance ---
    U = lambda x: pd.Timestamp(x, tz="UTC")            # noqa: E731
    closed = tactics.gold_market_closed
    ok &= check("gold closed = Friday 17:00 to Sunday 18:00 New York (summer and winter time)",
                not closed(U("2026-09-25 20:59")) and closed(U("2026-09-25 21:00"))      # Fri, EDT
                and closed(U("2026-09-26 12:00")) and closed(U("2026-09-27 21:59"))     # Sat, Sun
                and not closed(U("2026-09-27 22:00")) and not closed(U("2026-09-28 12:00"))
                and not closed(U("2026-12-04 21:59")) and closed(U("2026-12-04 22:00"))  # Fri, EST
                and closed(U("2026-12-06 22:59")) and not closed(U("2026-12-06 23:00")))
    sat, wed = U("2026-09-26 12:00"), U("2026-09-23 12:00")
    we, wd = tactics.effective_limits(btc, sat), tactics.effective_limits(btc, wed)
    ok &= check("BTC on Saturday/Sunday: 10% daily cap, 5 per direction (gold's weekday allowance); "
                "weekdays 5% / 3; risk per trade, stop, lock, trail unchanged",
                (we.max_daily_loss_pct, we.max_open_positions_per_direction) == (10.0, 5)
                and (wd.max_daily_loss_pct, wd.max_open_positions_per_direction) == (5.0, 3)
                and (we.risk_percent, we.sl_atr_mult, we.lock_r, we.trail_r) == (2.0, 1.0, 1.0, 0.5)
                and wd is btc and btc.max_daily_loss_pct == 5.0,
                (we.max_daily_loss_pct, we.max_open_positions_per_direction))
    reopen = tactics.gold_reopen_after
    ok &= check("gold's weekly reopen: Sunday 18:00 New York (22:00 UTC in summer, 23:00 in winter)",
                reopen(U("2026-09-26 12:00")) == U("2026-09-27 22:00").to_pydatetime()
                and reopen(U("2026-09-27 21:00")) == U("2026-09-27 22:00").to_pydatetime()
                and reopen(U("2026-09-27 23:00")) == U("2026-10-04 22:00").to_pydatetime()
                and reopen(U("2026-12-05 12:00")) == U("2026-12-06 23:00").to_pydatetime(),
                reopen(U("2026-09-26 12:00")))
    ok &= check("weekend: the gold program polls nothing; Bitcoin keeps going; gold polls on weekdays",
                tactics.market_closed_for(gold, sat) and not tactics.market_closed_for(btc, sat)
                and not tactics.market_closed_for(gold, wed))
    msrc = open(os.path.join(paths.APP_DIR, "main.py"), encoding="utf-8").read()
    loop = msrc[msrc.index("weekend_idle = False"):]
    ok &= check("main loop: the closed-market skip comes before any MT5 price or clock read",
                loop.index("tactics.market_closed_for(cfg, now_utc)") < loop.index("gw.server_utc_offset_seconds")
                < loop.index("market_intel.last_closed_time")
                and "if not tactics.market_closed_for(cfg, datetime.now(timezone.utc)):\n"
                    "            gw.server_utc_offset_seconds(cfg.symbol)" in msrc)
    ok &= check("gold never changes (no weekend allowance)",
                tactics.effective_limits(gold, sat) is gold and not tactics.weekend_mode(gold, sat))
    down6 = FakeGateway(bid=100000.0, ask=100010.0, bars_df=bars, equity=9400.0)   # 6% down on the day
    ok &= check("daily budget, account 6% down: a 2% BTC entry fits on Saturday (10%), refused on a weekday (5%)",
                executor.daily_risk_budget_reason(down6, dataclasses.replace(we, log_dir=cfg.log_dir), spec,
                                                  10000.0, 200.0) == ""
                and "daily loss budget" in executor.daily_risk_budget_reason(down6, wd, spec, 10000.0, 200.0))
    real_gw = main_mod.gw
    try:
        main_mod.gw = types.SimpleNamespace(now=lambda: sat.to_pydatetime())
        live_sat = main_mod.weekend_limits(btc)
        main_mod.gw = types.SimpleNamespace(now=lambda: wed.to_pydatetime())
        live_wed = main_mod.weekend_limits(btc)
    finally:
        main_mod.gw = real_gw
    ok &= check("the BTC instance applies it each cycle (and switches back when gold reopens)",
                live_sat.max_daily_loss_pct == 10.0 and live_wed is btc)
    bsrc = open(os.path.join(paths.APP_DIR, "backtest.py"), encoding="utf-8").read()
    ok &= check("the backtest replays the same weekend limits",
                "tactics.effective_limits(cfgs[s], g.current_time)" in bsrc)

    # --- backtest: R-based lock and trail ---
    pos = backtest.SimPosition(ticket=1, direction="buy", lots=0.1, entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                               entry_price=100000.0, sl=99500.0, tp=None, risk=500.0)
    bt = dataclasses.replace(cfg, exit_style="sl_to_tp1")
    backtest.HistoricalGateway._manage_sl_to_tp1(None, bt, pos, 100400.0, 99900.0, 0.0)
    before_lock = (pos.sl, pos.armed)
    backtest.HistoricalGateway._manage_sl_to_tp1(None, bt, pos, 100600.0, 100100.0, 0.0)
    locked = (pos.sl, pos.armed)
    backtest.HistoricalGateway._manage_sl_to_tp1(None, bt, pos, 101200.0, 100700.0, 0.0)
    ok &= check("backtest: no lock below +1R; lock exactly at +1R (100500); then trail 0.5R (250) behind the high",
                before_lock == (99500.0, False) and locked == (100500.0, True) and pos.sl == 100950.0,
                (before_lock, locked, pos.sl))

    # --- intelligence ---
    from claude_advisor import SYSTEM_PROMPT as GOLD_P, BTC_SYSTEM_PROMPT as BTC_P, system_prompt
    shared = GOLD_P[GOLD_P.index("THE THREE CONFLUENCES"):GOLD_P.index("`daily_weekly_levels`")]
    ok &= check("Claude: BTC prompt = the same confluence/SMC rules, Bitcoin market notes instead of gold's",
                system_prompt(cfg) is BTC_P and system_prompt(gold) is GOLD_P and shared in BTC_P
                and "BTCUSD (Bitcoin)" in BTC_P and "BITCOIN MARKET NOTES" in BTC_P and "btc_impact" in BTC_P
                and "XAUUSD (gold)" not in BTC_P and "gold_impact" not in BTC_P
                and "XAUUSD (gold)" in GOLD_P and "BITCOIN" not in GOLD_P)
    ok &= check("news check: crypto shocks and feeds for BTC, gold's unchanged",
                news_check.system_prompt(cfg) is news_check.BTC_SYSTEM_PROMPT
                and "stablecoin" in news_check.BTC_SYSTEM_PROMPT and "gold" not in news_check.BTC_SYSTEM_PROMPT.lower()
                and news_check.system_prompt(gold) is news_check.SYSTEM_PROMPT
                and any("coindesk" in u for u in btc.news_feeds) and "bitcoin" in btc.news_keywords
                and "{lookback}" in news_check.BTC_SYSTEM_PROMPT)

    class Ev:
        currency, impact = "USD", "positive"
    ok &= check("calendar: a USD-positive surprise reads bearish for Bitcoin",
                "bearish for Bitcoin" in econ_calendar.btc_impact(Ev()))

    # --- files: journal, status, EA, preset, installer ---
    with _tempfile.TemporaryDirectory() as tmp:
        jc = dataclasses.replace(cfg, log_dir=tmp)

        class JG:
            def journal_positions(self, symbol, magics):
                return []

            def price_distance_for_dollars(self, spec, dollars, lots):
                return 6.0

            def terminal_files_dir(self):
                raise AssertionError("BTC must not read the gold EA's Telegram signal log")
        names = set(TJ.build(JG(), jc, None))
        ok &= check("journal: BTC_ files, no Telegram signal log", names == {"BTC_trades.csv"}, names)
        sg = DashGateway(tmp)
        day = main_mod.DayRoll()
        rep = SR.build(sg, jc, spec, day)
        ok &= check("dashboard report: Claude only (no Telegram card, signals or hours), R-based rules",
                    rep["signals"] == [] and rep["telegram_hours"] == "" and rep["rules"]["lock_mode"] == "r"
                    and SR.source_magics(jc) == {profiles.BTC_MAGIC: "Claude"}
                    and not rep["rules"]["telegram_signal_sl"], rep.get("rules"))
    mt5 = os.path.join(paths.PACKAGE_ROOT, "mt5")
    bea = _mql_inputs(os.path.join(mt5, "Experts", "BTCTrader_EA.mq5"))
    bset = _preset(os.path.join(mt5, "Presets", "BTCTrader_EA_Default.set"))
    gea = _mql_inputs(os.path.join(mt5, "Experts", "UnifiedTrader_EA.mq5"))
    gset = _preset(os.path.join(mt5, "Presets", "UnifiedTrader_EA_Default.set"))
    ok &= check("BTCTrader_EA default = preset = profile (magic, lock R, trail R); gold EA's BTC buttons "
                "use the same magic and pause file",
                int(bea["InpMagicNumber"]) == int(bset["InpMagicNumber"]) == btc.magic
                and float(bea["InpLockR"]) == float(bset["InpLockR"]) == btc.lock_r
                and float(bea["InpTrailR"]) == float(bset["InpTrailR"]) == btc.trail_r
                and int(gea["InpBtcMagicNumber"]) == int(gset["InpBtcMagicNumber"]) == btc.magic
                and gea["InpBtcPauseFilename"] == gset["InpBtcPauseFilename"] == btc.claude_pause_filename,
                (bea, bset))
    src = open(os.path.join(mt5, "Experts", "BTCTrader_EA.mq5"), encoding="utf-8").read()
    ok &= check("BTCTrader_EA: R from the opening order's stop, lock/trail in R, broker TP cleared, never opens",
                "HistoryOrderGetDouble(o, ORDER_SL)" in src and "r * InpLockR" in src and "r * InpTrailR" in src
                and "PositionModify(ticket, slToSend, 0.0)" in src and ".Buy(" not in src and ".Sell(" not in src)
    import importlib.util
    spec_g = importlib.util.spec_from_file_location("gt_launcher", os.path.join(paths.PACKAGE_ROOT, "goldtrader.py"))
    gt = importlib.util.module_from_spec(spec_g)
    spec_g.loader.exec_module(gt)
    ok &= check("setup.bat installs and compiles BTCTrader_EA and its preset",
                ("mt5/Experts/BTCTrader_EA.mq5", "Experts/BTCTrader_EA.mq5") in gt.MT5_FILES
                and ("mt5/Presets/BTCTrader_EA_Default.set", "Presets/BTCTrader_EA_Default.set") in gt.MT5_FILES
                and "Experts/BTCTrader_EA.mq5" in gt.MT5_COMPILE)
    ok &= check("BTCTrader_EA exports BTCUSD M5/M15/H1 price files to the same Drive folder as gold's",
                bea["InpXtrExport"] == bset["InpXtrExport"] == "true"
                and bea["InpXtrExportName"].strip('"') == bset["InpXtrExportName"] == "BTCUSD"
                and int(bea["InpXtrExportBars"]) == int(bset["InpXtrExportBars"]) == 5000
                and bset["InpXtrExportCopyTo"] == gset["InpXtrExportCopyTo"]
                and bset["InpXtrExportFolder"] == gset["InpXtrExportFolder"]
                and src.count("XtrExpMaybeExport(InpXtrExport, _Symbol") == 2
                and "#include <XtrBarExport.mqh>" in src, (bea, bset))

    # --- backtest from the Drive price files ---
    with _tempfile.TemporaryDirectory() as tmp:
        t = pd.date_range("2026-08-02 00:00", periods=24 * 10, freq="1h", tz="UTC")   # Sun 2 Aug, 10 days
        h1 = pd.DataFrame({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": np.arange(240) + 100000.0,
                           "high": np.arange(240) + 100050.0, "low": np.arange(240) + 99950.0,
                           "close": np.arange(240) + 100010.0, "volume": 1})
        h1.to_csv(os.path.join(tmp, "BTCUSD_H1.csv"), index=False)
        m15 = pd.DataFrame({"datetime": pd.date_range("2026-08-02", periods=960, freq="15min", tz="UTC")
                            .strftime("%Y-%m-%d %H:%M:%S"), "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5})
        m15.to_csv(os.path.join(tmp, "BTCUSD_M15.csv"), index=False)
        got = backtest.load_csv_folder(tmp, "BTCUSD_", "M15", "H4")
        h4 = got["H4"]
        ok &= check("Drive price files load (datetime column, no volume -> 0); H4/D1/W1 built from H1",
                    set(got) == {"M15", "H1", "H4", "D1", "W1"} and (got["M15"]["volume"] == 0).all()
                    and str(got["M15"]["time"].dt.tz) == "UTC" and len(h4) == 60 and len(got["D1"]) == 10,
                    {k: len(v) for k, v in got.items()})
        ok &= check("H4 = first open / max high / min low / last close of its own four H1 bars (no look-ahead)",
                    (h4.iloc[1][["open", "high", "low", "close"]].tolist()
                     == [100004.0, 100057.0, 99954.0, 100017.0]) and h4["time"].iloc[1].hour == 4,
                    h4.iloc[1].tolist())
        ok &= check("W1 bars open Sunday 00:00 UTC", (got["W1"]["time"].dt.dayofweek == 6).all()
                    and len(got["W1"]) == 2, got["W1"]["time"].tolist())
        try:
            backtest.load_csv_folder(tmp, "XAUUSD_", "M15", "H4")
            missing = False
        except FileNotFoundError:
            missing = True
        ok &= check("a missing primary file is a clear error", missing)
    with _tempfile.TemporaryDirectory() as tmp:
        first = main_mod.acquire_instance_lock(tmp)
        second = main_mod.acquire_instance_lock(tmp)
        main_mod.release_instance_lock(first)           # the first program ends
        third = main_mod.acquire_instance_lock(tmp)
        ok &= check("one copy per instance: a second one is refused; after the first ends it starts again",
                    first is not None and second is None and third is not None)
        if third is not None:
            main_mod.release_instance_lock(third)
    with open(shipped_copy("start.bat"), newline="") as f:
        bat = f.read()
    ok &= check("start.bat starts gold and BTC together (CRLF) - your own BTC_ARGS (dry-run, suffix) is fine",
                "start-all %GT_ARGS% --btc %BTC_ARGS%" in bat and "set btc_args=" in bat.lower()
                and "\r\n" in bat)
    return ok


def main() -> int:
    print("Claude-SMC Trader self-test\n")
    results = [
        test_indicators(),
        test_smc_and_price_action(),
        test_market_structure_ob_fvg_levels(),
        test_price_distance(),
        test_executor(),
        test_stale_csv_header_warning(),
        test_claude_advisor_wiring(),
        test_claude_error_classification(),
        test_full_snapshot_pipeline(),
        test_recent_performance_summary(),
        test_dxy_context(),
        test_consensus_context(),
        test_mt5_gateway_recent_closed_trades(),
        test_write_common_file(),
        test_gateway_cap_and_filling(),
        test_backtest_no_lookahead_and_reset(),
        test_backtest_exit_simulation(),
        test_backtest_exit_simulation_sl_to_tp1(),
        test_backtest_day_state(),
        test_backtest_end_to_end_mechanical(),
        test_telegram_alert(),
        test_day_roll_daily_limits(),
        test_calibration_report(),
        test_main_cli_config(),
        test_digest_lookback_days(),
        test_heartbeat(),
        test_ml_advisor(),
        test_econ_calendar(),
        test_breaking_news_check(),
        test_entry_levels_and_alert(),
        test_xtr_logic(),
        test_relay_supervisor(),
        test_first_run_wizard(),
        test_tactics(),
        test_ea_preset_python_consistency(),
        test_broker_clock_and_trading_day(),
        test_scorecard(),
        test_margin_guard(),
        test_claude_prescreen(),
        test_keys_file(),
        test_three_legs_to_claude_to_trade(),
        test_status_report(),
        test_dashboard_password(),
        test_trade_journal(),
        test_signal_replay(),
        test_btc_profile(),
    ]
    print()
    save_test_report("app", FAILED_CHECKS, paths.PACKAGE_ROOT)
    if all(results):
        print(f"ALL PASS ({len(results)}/{len(results)} suites)")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} suites failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

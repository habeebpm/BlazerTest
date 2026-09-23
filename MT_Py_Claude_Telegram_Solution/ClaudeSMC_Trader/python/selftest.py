"""
Offline self-test - runs anywhere, no MT5 terminal, no MetaTrader5 package,
no Anthropic API key and no network needed. Mirrors the testing philosophy
of ../../python/copier_selftest.py: every piece of decision logic is pure or
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
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import backtest
import calibration_report
import claude_advisor
import econ_calendar
import executor
import main as main_mod
import market_intel
import ml_advisor
import news_check
import telegram_alert
import mt5_gateway as gw
from claude_advisor import ConfluenceLeg, ConfluenceVerdict
from config import AdvisorConfig


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


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
        return self._now or gw.now()

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
    ok &= check("a historical-only blackout window does NOT block a trade under the real gateway's "
                "own real-wall-clock now()",
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
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class FakeMessages:
    def __init__(self, canned: ConfluenceVerdict):
        self.canned = canned
        self.last_call = None

    def parse(self, **kwargs):
        self.last_call = kwargs
        return FakeParsedResponse(self.canned)


class FakeAnthropicClient:
    def __init__(self, canned: ConfluenceVerdict):
        self.messages = FakeMessages(canned)


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
        self.messages = FakeMessagesRaising(exc)


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
        (status_error(429, "rate_limit_error", "too many requests"), True, "rate-limited"),
        (status_error(529, "overloaded_error", "overloaded"), True, "service issue"),
        (anthropic.APIConnectionError(message="network down", request=req), True, "reach"),
        (ValueError("something unrelated broke"), False, "unexpectedly"),
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
    ])
    gw._mt5 = fake_m
    try:
        trades = gw.recent_closed_trades("XAUUSD", 20260921, count=10)
    finally:
        gw._mt5 = None

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
    # 0.01 x 25 = 0.25 -> bid=2359.875; floating = (2359.875-2350.0)/0.01
    # x 1.0 x 0.01 = 9.875, so equity = 10000 + 0 (no closed trades) + 9.875.
    ok &= check("account_equity() adds floating P&L on open sim_positions, priced off get_tick() "
                "(the same one-bar-ahead, never-the-evaluated-bar's-own-close price used "
                "everywhere else) - not just realized closed-trade P&L",
                abs(floating_gw.account_equity() - 10009.875) < 1e-9, floating_gw.account_equity())

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
    # once in ClaudeSMC_TradeManager.mq5's own history (see its file header).
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
    day.date = main_mod.datetime.now(main_mod.timezone.utc).date()
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

    yesterday = main_mod.datetime.now(main_mod.timezone.utc).date() - timedelta(days=1)
    day4 = main_mod.DayRoll()
    day4.date = yesterday
    day4.roll(10000.0)
    day4.check_daily_limits(cfg, 9000.0)
    ok &= check("breaker latched before the day rolls over", day4.block_reason() != "")
    day4.date = yesterday  # simulate the next UTC day boundary being reached
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

    today = main_mod.datetime.now(main_mod.timezone.utc).date()
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
    ok &= check("an unreadable pause file counts as running rather than halting trading",
                not main_mod.claude_paused(cfg, FakePauseFile(fail=True)))
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
                    no_data_result == {"trained": False,
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
                    under_result == {"trained": False,
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
        self.script = list(script)
        self.calls = []
        self.messages = self

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

    cfg = AdvisorConfig(news_feeds=["rss://a", "atom://b", "dead://c"], news_check_lookback_minutes=180)
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
                call["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
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
    cfg_closed = AdvisorConfig(news_feeds=cfg.news_feeds, news_check_fail_closed=True)
    r = news_check.check_before_trade(FakeNewsClient(list(boom)), cfg_closed, "buy", 2650.0,
                                      now=NEWS_NOW, fetcher=fetcher)
    ok &= check("news_check_fail_closed=True refuses the entry instead",
                r.block_reason.startswith("breaking-news check unavailable"), r.block_reason)

    # 8) No headlines + web search failing -> no blind headline-only call.
    cfg_nofeeds = AdvisorConfig(news_feeds=[])
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

    cfg_noweb = AdvisorConfig(news_feeds=cfg.news_feeds, news_check_web_search=False,
                              news_check_model="claude-sonnet-5")
    client = FakeNewsClient([_NewsResponse([_NewsBlock("text", _news_json())])])
    news_check.check_before_trade(client, cfg_noweb, "buy", 2650.0, now=NEWS_NOW, fetcher=fetcher)
    ok &= check("news_check_web_search=False sends no tools; news_check_model overrides the model",
                "tools" not in client.calls[0] and client.calls[0]["model"] == "claude-sonnet-5",
                client.calls[0].get("model"))
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
    ]
    print()
    if all(results):
        print(f"ALL PASS ({len(results)}/{len(results)} suites)")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} suites failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

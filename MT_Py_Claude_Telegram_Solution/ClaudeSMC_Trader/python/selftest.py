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
import logging
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import backtest
import calibration_report
import claude_advisor
import executor
import main as main_mod
import market_intel
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
                 equity: float = 10000.0, bars_df=None, now=None):
        self.same_dir_open = same_dir_open
        self.bid, self.ask = bid, ask
        self.orders_sent = []
        self.last_additional_magics = None
        self.equity = equity
        self.bars_df = bars_df
        self._now = now

    def count_same_direction(self, symbol, magic, direction, additional_magics=()):
        self.last_additional_magics = additional_magics
        return self.same_dir_open

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
        return None if dry_run else FakeResult()


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
    cfg = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs")
    spec = gw.SymbolSpec(name="XAUUSD", point=0.01, digits=2, stops_level_points=0,
                         spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                         tick_value=1.0, tick_size=0.01)

    fg = FakeGateway(same_dir_open=0)
    d = executor.execute(fg, cfg, make_verdict("buy", 3, "full"), spec, trades_today=0)
    ok &= check("a 3/3 full-conviction buy is executed", d.executed, d.reject_reason)
    ok &= check("the fake order was actually sent with the right lot size",
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
    cfg_fixed = AdvisorConfig(dry_run=True, log_dir="/tmp/claudesmc_selftest_logs")
    ok &= check("use_risk_percent=False (the default) always returns fixed_lot",
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

    def recent_closed_trades(self, symbol, magic, count):
        return []


def test_full_snapshot_pipeline() -> bool:
    print("\n=== 8. full feature-snapshot pipeline (fake bars, real indicator code) ===")
    ok = True
    cfg = AdvisorConfig(bars_per_timeframe=320)
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

    def recent_closed_trades(self, symbol, magic, count):
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
    summary = market_intel.recent_performance_summary(FakePerformanceGateway(trades), cfg)
    ok &= check("trade_count/wins/losses/win_rate are computed correctly",
                summary["trade_count"] == 3 and summary["wins"] == 2 and summary["losses"] == 1
                and abs(summary["win_rate_pct"] - 66.7) < 0.1, summary)
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

    cfg_off = AdvisorConfig()  # max_daily_loss_pct=0 (disabled)
    state_off = backtest.BacktestDayState()
    g_off = FakeDayStateGateway(day1, 10000.0)
    state_off.block_reason(g_off, cfg_off)
    g_off._equity = 5000.0  # -50%
    ok &= check("max_daily_loss_pct=0 (the default) never blocks anything, even a 50% drawdown",
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

    cfg_off = AdvisorConfig()
    day3 = main_mod.DayRoll()
    day3.roll(10000.0)
    day3.check_daily_limits(cfg_off, 5000.0)
    ok &= check("max_daily_loss_pct=0 (the default) disables the breaker even on a 50% drawdown",
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
        test_backtest_no_lookahead_and_reset(),
        test_backtest_exit_simulation(),
        test_backtest_exit_simulation_sl_to_tp1(),
        test_backtest_day_state(),
        test_backtest_end_to_end_mechanical(),
        test_telegram_alert(),
        test_day_roll_daily_limits(),
        test_calibration_report(),
        test_digest_lookback_days(),
        test_heartbeat(),
    ]
    print()
    if all(results):
        print(f"ALL PASS ({len(results)}/{len(results)} suites)")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} suites failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

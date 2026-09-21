"""
Offline self-test - runs anywhere, no MT5 terminal, no MetaTrader5 package,
no Anthropic API key and no network needed. Mirrors the testing philosophy
of ../../python/copier_selftest.py: every piece of decision logic is pure or
dependency-injected, so it's exercised here with synthetic data and fakes.

Covers:
  * indicator math (EMA/RSI/MACD/ATR/Bollinger/ADX-DI/Stochastic) sanity
  * SMC liquidity-sweep detection and premium/discount zoning
  * candle pattern detection
  * market structure (swing points, HH/HL/LH/LL, BOS/CHoCH), order blocks,
    fair value gaps (open and filled), and previous day/week high-low
  * the dollar -> price-distance conversion executor.execute() relies on
  * executor.gate()/execute() gating logic against a fake MT5 gateway
  * claude_advisor.get_verdict() wiring against a fake Anthropic client
  * claude_advisor's Claude-API error classification (out of credits,
    bad key, rate limit, overload, network) - built from the real
    anthropic SDK exception classes when the package is installed
  * backtest.HistoricalGateway's no-lookahead guarantee (a higher timeframe
    bar isn't visible until its own CLOSE time, not just its open time),
    its arm-then-trail exit simulation (SL/TP/trail/min-stop-dist), and a
    tiny end-to-end mechanical-mode backtest run
"""
from __future__ import annotations

import csv

import numpy as np
import pandas as pd

import backtest
import claude_advisor
import executor
import market_intel
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

    def __init__(self, same_dir_open: int = 0, bid: float = 2350.0, ask: float = 2350.2):
        self.same_dir_open = same_dir_open
        self.bid, self.ask = bid, ask
        self.orders_sent = []

    def count_same_direction(self, symbol, magic, direction):
        return self.same_dir_open

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

    fg5 = FakeGateway(same_dir_open=0)
    d5 = executor.execute(fg5, cfg, make_verdict("none", 0, "none"), spec, trades_today=0)
    ok &= check("direction='none' is rejected outright",
                not d5.executed and "no actionable direction" in d5.reject_reason, d5.reject_reason)

    cfg_capped = AdvisorConfig(dry_run=True, max_trades_per_day=2, log_dir="/tmp/claudesmc_selftest_logs")
    fg6 = FakeGateway(same_dir_open=0)
    d6 = executor.execute(fg6, cfg_capped, make_verdict("sell", 3, "full"), spec, trades_today=2)
    ok &= check("max_trades_per_day blocks a trade once the cap is reached",
                not d6.executed and "max trades/day" in d6.reject_reason, d6.reject_reason)

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


def test_full_snapshot_pipeline() -> bool:
    print("\n=== 8. full feature-snapshot pipeline (fake bars, real indicator code) ===")
    ok = True
    cfg = AdvisorConfig(bars_per_timeframe=320)
    snapshot = market_intel.build_feature_snapshot(FakeIntelGateway(), cfg)

    ok &= check("snapshot has the expected top-level sections",
                {"primary_indicators", "trend_bias", "smc", "last_closed_candle",
                 "recent_candles", "session"} <= snapshot.keys(), list(snapshot.keys()))
    ok &= check("recent_candles carries exactly 20 candles", len(snapshot["recent_candles"]) == 20,
                len(snapshot["recent_candles"]))

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

    reset_ok = gateway.reset("M15", warmup_bars=3)
    ok &= check("reset() succeeds with enough history on every timeframe", reset_ok)
    ok &= check("reset() lands on the earliest bar where H4 (the binding constraint) has "
                "warmup_bars closed bars, not earlier or later",
                gateway.current_time == pd.Timestamp("2026-01-01 12:15", tz="UTC"), gateway.current_time)

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
    # Independent of the shipped default (where tp_arm_dollars doubles as the
    # fixed-TP distance, so the trail can never win the race against the
    # standing TP order - see the backtest README section), so this
    # specifically exercises the arm-then-trail code path on its own merits.
    cfg = AdvisorConfig(tp_arm_dollars=3.0, trail_dollars=1.0)

    g1 = make_gateway(spec)
    g1.open_positions = [backtest.SimPosition(ticket=1, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=2360.0)]
    set_bar(g1, 2350.0, 2353.5, 2349.5, 2353.0)  # profit_at_high 3.5 >= arm_dist 3.0
    g1.manage_positions(cfg)
    pos = g1.open_positions[0]
    ok &= check("reaching the arm threshold arms the trail and drops the fixed TP",
                pos.armed and pos.tp is None and abs(pos.sl - 2352.5) < 1e-9,
                (pos.sl, pos.tp, pos.armed))
    set_bar(g1, 2353.0, 2353.2, 2352.0, 2352.3)  # pulls back onto the new trailing SL (2352.5)
    g1.manage_positions(cfg)
    ok &= check("a later pullback onto the armed trailing stop closes the position with reason 'trail'",
                len(g1.closed_trades) == 1 and g1.closed_trades[0].exit_reason == "trail"
                and abs(g1.closed_trades[0].exit_price - 2352.5) < 1e-9, g1.closed_trades)

    g2 = make_gateway(spec)
    g2.open_positions = [backtest.SimPosition(ticket=2, direction="sell", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2356.0, tp=2340.0)]
    set_bar(g2, 2350.0, 2357.0, 2349.0, 2355.0)  # high touches 2357 >= sl 2356
    g2.manage_positions(cfg)
    ok &= check("a sell position's stop-loss being touched closes it at exactly the SL price for -$6",
                g2.closed_trades and g2.closed_trades[0].exit_reason == "sl"
                and g2.closed_trades[0].exit_price == 2356.0 and g2.closed_trades[0].pnl_dollars == -6.0,
                g2.closed_trades)

    g3 = make_gateway(spec)
    g3.open_positions = [backtest.SimPosition(ticket=3, direction="buy", lots=0.01,
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
    g4.open_positions = [backtest.SimPosition(ticket=4, direction="buy", lots=0.01,
                         entry_time=pd.Timestamp("2026-01-01", tz="UTC"),
                         entry_price=2350.0, sl=2344.0, tp=2360.0)]
    set_bar(g4, 2350.0, 2353.5, 2349.5, 2353.0)  # would arm, but candidate SL is only 1.0 away - too tight
    g4.manage_positions(cfg)
    pos4 = g4.open_positions[0] if g4.open_positions else None
    ok &= check("a broker minimum stop distance wider than the trail keeps the fixed TP in place",
                pos4 is not None and not pos4.armed and pos4.tp == 2360.0 and pos4.sl == 2344.0,
                pos4)

    return ok


def test_backtest_end_to_end_mechanical() -> bool:
    print("\n=== 11. backtest.py end-to-end run (--mechanical, tiny synthetic dataset, no API) ===")
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


def main() -> int:
    print("Claude-SMC Trader self-test\n")
    results = [
        test_indicators(),
        test_smc_and_price_action(),
        test_market_structure_ob_fvg_levels(),
        test_price_distance(),
        test_executor(),
        test_claude_advisor_wiring(),
        test_claude_error_classification(),
        test_full_snapshot_pipeline(),
        test_backtest_no_lookahead_and_reset(),
        test_backtest_exit_simulation(),
        test_backtest_end_to_end_mechanical(),
    ]
    print()
    if all(results):
        print(f"ALL PASS ({len(results)}/{len(results)} suites)")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} suites failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

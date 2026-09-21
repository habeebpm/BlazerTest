"""
Offline self-test - runs anywhere, no MT5 terminal, no MetaTrader5 package,
no Anthropic API key and no network needed. Mirrors the testing philosophy
of ../../python/copier_selftest.py: every piece of decision logic is pure or
dependency-injected, so it's exercised here with synthetic data and fakes.

Covers:
  * indicator math (EMA/RSI/MACD/ATR/Bollinger/ADX-DI/Stochastic) sanity
  * SMC liquidity-sweep detection and premium/discount zoning
  * candle pattern detection
  * the dollar -> price-distance conversion executor.execute() relies on
  * executor.gate()/execute() gating logic against a fake MT5 gateway
  * claude_advisor.get_verdict() wiring against a fake Anthropic client
"""
from __future__ import annotations

import csv

import numpy as np
import pandas as pd

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


# --------------------------------------------------------------------------- #
# dollar -> price distance
# --------------------------------------------------------------------------- #
def test_price_distance() -> bool:
    print("\n=== 3. dollar -> price distance conversion ===")
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
    print("\n=== 4. executor gating ===")
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
    print("\n=== 5. claude_advisor wiring (fake client, no network) ===")
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
    print("\n=== 6. full feature-snapshot pipeline (fake bars, real indicator code) ===")
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


def main() -> int:
    print("Claude-SMC Trader self-test\n")
    results = [
        test_indicators(),
        test_smc_and_price_action(),
        test_price_distance(),
        test_executor(),
        test_claude_advisor_wiring(),
        test_full_snapshot_pipeline(),
    ]
    print()
    if all(results):
        print(f"ALL PASS ({len(results)}/{len(results)} suites)")
        return 0
    print(f"FAILURES: {results.count(False)}/{len(results)} suites failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

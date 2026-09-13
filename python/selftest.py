"""
Strategy self-test - runs anywhere, no MetaTrader5 package or terminal needed.

Builds synthetic XAUUSD-like bars with known regimes and asserts that:
  * indicators produce sane values,
  * the three confluences fire on a clean trend and stay quiet in chop,
  * the trailing-stop arithmetic tightens and never loosens,
  * preflight-style distance maths behaves as documented.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import indicators as ind
import strategy as st
from config import TradeConfig


def make_bars(closes: np.ndarray, start: str, freq: str) -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(closes), freq=freq)
    noise = np.abs(np.random.default_rng(7).normal(0.0, 0.35, len(closes)))
    return pd.DataFrame(
        {
            "open": np.concatenate([[closes[0]], closes[:-1]]),
            "high": closes + noise,
            "low": closes - noise,
            "close": closes,
        },
        index=idx,
    )


def trending_series(n: int, start_price: float, slope: float, seed: int = 3) -> np.ndarray:
    """Random-walk prices with a drift, scaled like real XAUUSD M15 bars.

    The noise-to-drift ratio matters: a near-noiseless ramp pins RSI near 90
    and never re-crosses the EMAs, which is nothing like a real gold chart and
    makes the confluence test meaningless.
    """
    rng = np.random.default_rng(seed)
    return start_price + np.cumsum(slope + rng.normal(0.0, 1.5, n))


def choppy_series(n: int, start_price: float, seed: int = 11) -> np.ndarray:
    """Mean-reverting, directionless price action - the regime ADX should veto."""
    rng = np.random.default_rng(seed)
    prices = [start_price]
    for _ in range(n - 1):
        pull = 0.06 * (start_price - prices[-1])      # mean reversion
        prices.append(prices[-1] + pull + rng.normal(0.0, 1.2))
    return np.array(prices)


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))
    return ok


def test_indicators() -> bool:
    print("\nIndicators")
    closes = trending_series(500, 2000.0, 0.35)
    df = make_bars(closes, "2026-01-01", "15min")
    ok = True

    e20 = ind.ema(df["close"], 20)
    ok &= check("EMA tracks an uptrend", e20.iloc[-1] > e20.iloc[-50])

    r = ind.rsi(df["close"], 14)
    ok &= check("RSI within 0-100", bool(r.dropna().between(0, 100).all()),
                f"last={r.iloc[-1]:.1f}")
    ok &= check("RSI above 50 in an uptrend", r.iloc[-1] > 50, f"{r.iloc[-1]:.1f}")

    main, sig, hist = ind.macd(df["close"])
    ok &= check("MACD positive in an uptrend", main.iloc[-1] > 0, f"{main.iloc[-1]:.2f}")
    ok &= check("MACD hist == main - signal",
                bool(np.allclose((main - sig).dropna(), hist.dropna())))

    adx, pdi, mdi = ind.adx(df["high"], df["low"], df["close"], 14)
    ok &= check("+DI > -DI in an uptrend", pdi.iloc[-1] > mdi.iloc[-1],
                f"+DI={pdi.iloc[-1]:.1f} -DI={mdi.iloc[-1]:.1f}")
    ok &= check("ADX within 0-100", bool(adx.dropna().between(0, 100).all()),
                f"last={adx.iloc[-1]:.1f}")

    a = ind.atr(df["high"], df["low"], df["close"], 14)
    ok &= check("ATR positive", a.iloc[-1] > 0, f"{a.iloc[-1]:.2f}")

    up, mid, lo = ind.bollinger(df["close"], 20, 2.0)
    ok &= check("Bollinger ordering upper>mid>lower",
                bool((up.dropna() >= mid.dropna()).all() and (mid.dropna() >= lo.dropna()).all()))
    return bool(ok)


def build_frame(work_closes: np.ndarray, cfg: TradeConfig) -> pd.DataFrame:
    """Working-timeframe M15 bars plus a matching H4 trend frame."""
    work = make_bars(work_closes, "2026-01-01", "15min")
    # H4 = every 16th M15 bar; give it enough history for EMA200
    htf_closes = np.concatenate([
        np.full(300, work_closes[0]),          # flat prehistory
        work_closes[::16],
    ])
    htf_idx = pd.date_range(end=work.index[-1], periods=len(htf_closes), freq="4h")
    trend = pd.DataFrame({"close": htf_closes}, index=htf_idx)
    trend["open"] = trend["close"]
    trend["high"] = trend["close"]
    trend["low"] = trend["close"]
    return st.compute_indicators(work, trend, cfg)


def test_confluences() -> bool:
    print("\nThree confluences")
    cfg = TradeConfig()
    ok = True

    # --- strong uptrend: expect buy confluences to light up somewhere ---
    up = build_frame(trending_series(900, 2000.0, 0.20, seed=17), cfg)
    buy_hits, sell_hits = 0, 0
    for i in range(300, len(up)):
        s = st.evaluate(up, cfg, i)
        buy_hits += s.direction == "buy"
        sell_hits += s.direction == "sell"
    ok &= check("uptrend produces buy signals", buy_hits > 0, f"{buy_hits} buys")
    ok &= check("uptrend produces no sell signals", sell_hits == 0, f"{sell_hits} sells")

    # --- strong downtrend: mirror image ---
    down = build_frame(trending_series(900, 2400.0, -0.20, seed=23), cfg)
    buy_hits, sell_hits = 0, 0
    for i in range(300, len(down)):
        s = st.evaluate(down, cfg, i)
        buy_hits += s.direction == "buy"
        sell_hits += s.direction == "sell"
    ok &= check("downtrend produces sell signals", sell_hits > 0, f"{sell_hits} sells")
    ok &= check("downtrend produces no buy signals", buy_hits == 0, f"{buy_hits} buys")

    # --- chop: the ADX/strength confluence should keep us out ---
    chop = build_frame(choppy_series(900, 2100.0), cfg)
    trades = sum(st.evaluate(chop, cfg, i).direction is not None
                 for i in range(300, len(chop)))
    ok &= check("range-bound market trades rarely", trades <= 3, f"{trades} signals")

    # --- a signal really does require all three, never 2/3 ---
    sig = st.evaluate(up, cfg, len(up) - 1)
    two_of_three_traded = False
    for i in range(300, len(up)):
        s = st.evaluate(up, cfg, i)
        if s.direction is not None:
            side = s.buy if s.direction == "buy" else s.sell
            if side.count != 3:
                two_of_three_traded = True
    ok &= check("no trade is ever taken on fewer than 3/3", not two_of_three_traded)
    ok &= check("Signal.summary renders", isinstance(sig.summary(), str), sig.summary())
    return bool(ok)


def test_distances() -> bool:
    print("\nDistance / unit maths (XAUUSD, point = 0.01)")
    point = 0.01
    ok = True
    expect = {"point": (0.06, 0.03), "pip": (0.60, 0.30), "usd": (6.00, 3.00)}
    for unit, (sl_want, trail_want) in expect.items():
        cfg = TradeConfig(distance_unit=unit)
        ok &= check(f"{unit}: SL=${sl_want:.2f} trail=${trail_want:.2f}",
                    abs(cfg.sl_distance(point) - sl_want) < 1e-9
                    and abs(cfg.trail_distance(point) - trail_want) < 1e-9)
    cfg = TradeConfig()
    typical_spread = 25 * point  # $0.25, a normal gold spread
    ok &= check("default 6 'points' is correctly detected as below spread",
                cfg.sl_distance(point) < typical_spread,
                f"SL ${cfg.sl_distance(point):.2f} < spread ${typical_spread:.2f}")
    return bool(ok)


def test_trailing() -> bool:
    print("\nTrailing stop arithmetic")
    cfg = TradeConfig(distance_unit="usd")   # $3 trail, sane for gold
    point = 0.01
    trail = cfg.trail_distance(point)
    ok = True

    entry = 2000.00
    entry_sl = entry - cfg.sl_distance(point)
    sl = entry_sl
    stops = []
    for bid in [2001.0, 2003.0, 2002.0, 2006.0, 2005.5, 2010.0]:
        if bid - entry >= cfg.trail_start_distance(point):
            candidate = bid - trail
            if candidate > sl:
                sl = candidate
        stops.append(sl)

    ok &= check("stop never loosens", all(b >= a for a, b in zip(stops, stops[1:])),
                " -> ".join(f"{s:.2f}" for s in stops))
    ok &= check("stop follows price up", stops[-1] == 2010.0 - trail, f"{stops[-1]:.2f}")
    ok &= check("stop locks in profit above entry", stops[-1] > entry, f"{stops[-1]:.2f} > {entry}")
    ok &= check("initial stop is SL distance below entry",
                abs(entry_sl - (entry - 6.0)) < 1e-9, f"{entry_sl:.2f}")

    # sell side
    entry = 2000.00
    sl = entry + cfg.sl_distance(point)
    stops = []
    for ask in [1999.0, 1997.0, 1998.0, 1994.0, 1990.0]:
        if entry - ask >= cfg.trail_start_distance(point):
            candidate = ask + trail
            if candidate < sl:
                sl = candidate
        stops.append(sl)
    ok &= check("sell stop never loosens", all(b <= a for a, b in zip(stops, stops[1:])),
                " -> ".join(f"{s:.2f}" for s in stops))
    ok &= check("sell stop follows price down", stops[-1] == 1990.0 + trail, f"{stops[-1]:.2f}")
    return bool(ok)


def run() -> int:
    print("=" * 68)
    print("XAUUSD confluence bot - strategy self-test (no MT5 required)")
    print("=" * 68)
    results = [
        test_indicators(),
        test_confluences(),
        test_distances(),
        test_trailing(),
    ]
    print("\n" + "=" * 68)
    if all(results):
        print("ALL CHECKS PASSED")
        return 0
    print("SOME CHECKS FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(run())

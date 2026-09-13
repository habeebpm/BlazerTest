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
    # At 2/3 the trend leg is optional, so a pullback can produce a counter-trend
    # sell. Buys must still dominate heavily.
    ok &= check("uptrend is dominated by buys", buy_hits > 5 * sell_hits,
                f"{buy_hits} buys vs {sell_hits} counter-trend sells")

    # --- strong downtrend: mirror image ---
    down = build_frame(trending_series(900, 2400.0, -0.20, seed=23), cfg)
    buy_hits, sell_hits = 0, 0
    for i in range(300, len(down)):
        s = st.evaluate(down, cfg, i)
        buy_hits += s.direction == "buy"
        sell_hits += s.direction == "sell"
    ok &= check("downtrend produces sell signals", sell_hits > 0, f"{sell_hits} sells")
    ok &= check("downtrend is dominated by sells", sell_hits > 5 * buy_hits,
                f"{sell_hits} sells vs {buy_hits} counter-trend buys")

    # --- chop: the ADX/strength confluence should keep us out ---
    chop = build_frame(choppy_series(900, 2100.0), cfg)
    trades = sum(st.evaluate(chop, cfg, i).direction is not None
                 for i in range(300, len(chop)))
    trend_trades = max(buy_hits + sell_hits, 1)
    ok &= check("range-bound market trades less than a trending one",
                trades < trend_trades, f"{trades} chop signals vs {trend_trades} in a trend")

    # requiring the trend leg should suppress counter-trend entries entirely
    strict_cfg = TradeConfig(require_trend_confluence=True)
    counter = 0
    for i in range(300, len(up)):
        sg = st.evaluate(up, strict_cfg, i)
        counter += sg.direction == "sell"
    ok &= check("require_trend_confluence removes counter-trend entries",
                counter == 0, f"{counter} counter-trend signals in an uptrend")

    sig = st.evaluate(up, cfg, len(up) - 1)
    ok &= check("Signal.summary renders", isinstance(sig.summary(), str), sig.summary())
    return bool(ok)


def test_entry_rule() -> bool:
    """The 2-of-3 rule with a confirmation requirement."""
    print("\nEntry rule (>= 2/3 with >= 1 confirmed)")
    # These checks isolate the confluence rule, so the confidence gate is
    # switched off here; the gate gets its own checks in test_confidence.
    cfg = TradeConfig(min_confidence=0.0)
    ok = True

    frames = [build_frame(trending_series(900, 2000.0, d, seed=sd), cfg)
              for d, sd in [(0.20, 17), (-0.20, 23), (0.0, 9), (0.12, 3)]]

    below_min = 0
    unconfirmed = 0
    vetoed_traded = 0
    signals = 0
    for df in frames:
        for i in range(300, len(df)):
            sg = st.evaluate(df, cfg, i)
            if sg.direction is None:
                continue
            signals += 1
            side = sg.buy if sg.direction == "buy" else sg.sell
            if side.count < cfg.min_confluences:
                below_min += 1
            if side.confirmed_count < cfg.min_confirmed:
                unconfirmed += 1
            if side.vetoed:
                vetoed_traded += 1

    ok &= check("never trades below the minimum confluence count", below_min == 0,
                f"{signals} signals, {below_min} under {cfg.min_confluences}/3")
    ok &= check("never trades without a confirmed confluence", unconfirmed == 0,
                f"{unconfirmed} unconfirmed entries")
    ok &= check("never trades into a Bollinger veto", vetoed_traded == 0)
    ok &= check("2/3 actually produces signals", signals > 0, f"{signals} signals")

    # 2/3 must be strictly more permissive than 3/3, and confirmation must bite
    def count(**kw):
        """Signals across the same frames under a different entry rule.

        The indicator columns do not depend on the entry rule, so the frames
        built above are reused - rebuilding one per bar is needlessly slow.
        """
        kw.setdefault("min_confidence", 0.0)
        c = TradeConfig(**kw)
        return sum(st.evaluate(df, c, i).direction is not None
                   for df in frames for i in range(300, len(df)))

    strict = count(min_confluences=3, require_confirmation=False)
    loose = count(min_confluences=2, require_confirmation=False)
    default = count()
    two_conf = count(min_confluences=2, require_confirmation=True, min_confirmed=2)
    print(f"      3/3={strict}  2/3 raw={loose}  2/3+1conf={default}  2/3+2conf={two_conf}")
    ok &= check("2/3 trades more often than 3/3", default > strict)
    ok &= check("confirmation filters out weak 2/3 signals", default < loose,
                f"{loose - default} signals rejected for having no confirmed leg")
    ok &= check("requiring 2 confirmations is stricter still", two_conf < default)

    # A hand-built bar: exactly 2 passes, zero confirmed -> must NOT trade.
    # The trend leg needs a real cross inside the lookback window, so the
    # PREVIOUS bar has to sit with fast below slow.
    df = frames[0].copy()
    i = len(df) - 1
    r, r_prev = df.index[i], df.index[i - 1]
    df.loc[r_prev, ["ema_fast", "ema_slow"]] = [1999.90, 2000.00]   # pre-cross
    df.loc[r, ["htf_close", "htf_ema"]] = [2100.0, 2000.0]          # trend bias up
    df.loc[r, ["ema_fast", "ema_slow"]] = [2000.10, 2000.00]        # just crossed, gap tiny
    df.loc[r, ["adx", "plus_di", "minus_di"]] = [22.5, 26.0, 24.0]  # passes, not confirmed
    df.loc[r, ["macd", "macd_signal"]] = [-1.0, -0.5]               # momentum fails
    df.loc[r, "rsi"] = 52.0
    df.loc[r, ["bb_upper", "bb_lower"]] = [9999.0, 0.0]             # no veto
    df.loc[r, "close"] = 2000.05
    marginal = st.evaluate(df, cfg, i)
    side = marginal.buy
    ok &= check("staged setup really is 2/3 with 0 confirmed",
                side.count == 2 and side.confirmed_count == 0,
                f"marks={side.marks()} count={side.count} confirmed={side.confirmed_count}")
    ok &= check("hand-built marginal 2/3 with 0 confirmed is rejected",
                marginal.direction is None,
                f"{side.count}/3 passes, {side.confirmed_count} confirmed -> "
                f"{marginal.direction or 'no trade'}")
    ok &= check("same setup trades once confirmation is not required",
                st.evaluate(df, TradeConfig(require_confirmation=False,
                                            min_confidence=0.0), i).direction == "buy")
    return bool(ok)


def test_confidence() -> bool:
    """The 0-100 setup-quality score."""
    print("\nConfidence score (setup quality, not a win probability)")
    cfg = TradeConfig(min_confidence=0.0)   # score every bar, gate tested below
    ok = True

    frames = [build_frame(trending_series(900, 2000.0, d, seed=sd), cfg)
              for d, sd in [(0.20, 17), (-0.20, 23), (0.0, 9), (0.12, 3)]]

    all_scores, taken = [], []
    for df in frames:
        for i in range(300, len(df)):
            sg = st.evaluate(df, cfg, i)
            for side in (sg.buy, sg.sell):
                all_scores.append(side.confidence)
            if sg.direction:
                s2 = sg.buy if sg.direction == "buy" else sg.sell
                taken.append(s2.confidence)

    ok &= check("every score is within 0-100",
                all(0.0 <= v <= 100.0 for v in all_scores),
                f"min {min(all_scores):.0f} max {max(all_scores):.0f}")
    ok &= check("a no-confluence bar scores 0", min(all_scores) == 0.0)
    ok &= check("qualifying setups score at least 40",
                taken and min(taken) >= 40.0 - 1e-9, f"floor {min(taken):.1f}")

    # more evidence must score higher than less
    best, worst = max(taken), min(taken)
    ok &= check("the score spreads across setups", best > worst + 15,
                f"{worst:.0f} .. {best:.0f}")

    # a 3/3 all-confirmed bar must outscore the staged 2/3 zero-confirmed bar
    df = frames[0].copy()
    i = len(df) - 1
    r, r_prev = df.index[i], df.index[i - 1]
    df.loc[r_prev, ["ema_fast", "ema_slow"]] = [1999.90, 2000.00]
    df.loc[r, ["htf_close", "htf_ema"]] = [2100.0, 2000.0]
    df.loc[r, ["ema_fast", "ema_slow"]] = [2010.0, 2000.0]        # wide gap
    df.loc[r, ["macd", "macd_signal"]] = [2.0, 1.0]
    df.loc[r, ["macd_hist"]] = [1.0]
    df.loc[df.index[i - 1], "macd_hist"] = 0.2                     # expanding
    df.loc[r, "macd"] = 2.0
    df.loc[df.index[i - 1], "macd"] = 1.0                          # rising
    df.loc[r, "rsi"] = 65.0
    df.loc[r, ["adx", "plus_di", "minus_di"]] = [40.0, 45.0, 10.0]
    df.loc[r, ["bb_upper", "bb_lower"]] = [9999.0, 0.0]
    df.loc[r, "close"] = 2010.0
    df.loc[r, "atr"] = 2.0
    strong = st.evaluate(df, cfg, i).buy
    ok &= check("a strong 3/3 all-confirmed setup scores high",
                strong.count == 3 and strong.confirmed_count == 3 and strong.confidence >= 85,
                f"marks={strong.marks()} score={strong.confidence:.1f}")

    # the gate works and a veto zeroes the score
    gated = TradeConfig(min_confidence=95.0)
    blocked = sum(st.evaluate(df2, gated, i).direction is not None
                  for df2 in frames for i in range(300, len(df2)))
    ok &= check("a high min_confidence gate suppresses weak setups", blocked == 0,
                f"{blocked} signals survive a 95% gate")
    df.loc[r, "bb_upper"] = 2000.0        # price now at/above the upper band
    vetoed = st.evaluate(df, cfg, i).buy
    ok &= check("a Bollinger veto zeroes the score",
                vetoed.vetoed and vetoed.confidence == 0.0,
                f"score={vetoed.confidence:.1f}")

    # --- the shipped 65% gate ---
    shipped = TradeConfig()
    ok &= check("the shipped default gate is 65", shipped.min_confidence == 65.0)

    ungated, gated_n, below = 0, 0, 0
    for df2 in frames:
        for i in range(300, len(df2)):
            if st.evaluate(df2, TradeConfig(min_confidence=0.0), i).direction:
                ungated += 1
            sg = st.evaluate(df2, shipped, i)
            if sg.direction:
                gated_n += 1
                side = sg.buy if sg.direction == "buy" else sg.sell
                if side.confidence < 65.0:
                    below += 1
    ok &= check("no trade is taken below the 65 gate", below == 0, f"{below} leaked through")
    ok &= check("the 65 gate materially reduces trading", gated_n < ungated * 0.25,
                f"{gated_n} of {ungated} signals survive "
                f"({100*gated_n/max(ungated,1):.0f}%)")
    ok &= check("some setups still clear 65", gated_n > 0, f"{gated_n} signals")
    return bool(ok)


def test_distances() -> bool:
    print("\nDistance / unit maths (XAUUSD, point = 0.01)")
    point = 0.01
    ok = True
    # 60 / 30 units under each interpretation
    expect = {"point": (0.60, 0.30), "pip": (6.00, 3.00), "usd": (60.00, 30.00)}
    for unit, (sl_want, trail_want) in expect.items():
        cfg = TradeConfig(distance_unit=unit)
        ok &= check(f"{unit}: SL=${sl_want:.2f} trail=${trail_want:.2f}",
                    abs(cfg.sl_distance(point) - sl_want) < 1e-9
                    and abs(cfg.trail_distance(point) - trail_want) < 1e-9)

    cfg = TradeConfig()   # the shipped default: 60 pips / 30 pips
    typical_spread = 25 * point          # $0.25, a normal gold spread
    wide_spread = 40 * point             # $0.40, a wide one
    ok &= check("default SL is 60 pips = $6.00",
                abs(cfg.sl_distance(point) - 6.00) < 1e-9)
    ok &= check("default trail is 30 pips = $3.00",
                abs(cfg.trail_distance(point) - 3.00) < 1e-9)
    ok &= check("default SL clears a normal spread with margin",
                cfg.sl_distance(point) > 4 * typical_spread,
                f"SL $6.00 vs spread ${typical_spread:.2f}")
    ok &= check("default trail clears even a wide spread",
                cfg.trail_distance(point) > 2 * wide_spread,
                f"trail $3.00 vs spread ${wide_spread:.2f}")

    # a too-tight config must still be recognised as unusable
    tight = TradeConfig(distance_unit="point", stop_loss_units=6.0)
    ok &= check("a 6-point stop is still detected as below spread",
                tight.sl_distance(point) < typical_spread,
                f"SL ${tight.sl_distance(point):.2f} < spread ${typical_spread:.2f}")
    return bool(ok)


def test_trailing() -> bool:
    print("\nTrailing stop arithmetic")
    cfg = TradeConfig()   # shipped default: $6.00 stop, $3.00 trail
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
        test_entry_rule(),
        test_confidence(),
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

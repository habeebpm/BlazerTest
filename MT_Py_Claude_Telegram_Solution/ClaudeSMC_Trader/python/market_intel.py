"""
Builds the "trading intelligence" snapshot handed to Claude - every piece of
market context this solution can pull from the MT5 chart, computed with pure,
dependency-injected pandas/numpy functions so they're unit-testable without a
live MT5 connection (see selftest.py).

Deliberately mirrors the pass/confirm thresholds already documented and
measured in ../../python/README.md's three-confluence framework (Trend /
Momentum / Strength) - this module computes the same raw numbers that
mechanical bot uses, but does NOT pre-judge pass/fail itself. Claude does that
judgment call (see claude_advisor.py's system prompt, which states the exact
thresholds) - that's the point of asking an LLM to "validate" the confluences
instead of a hard-coded if-statement: it can weigh context (a barely-passing
ADX next to a fresh liquidity sweep against it, say) the mechanical version
can't.

What's fed to Claude, all computed here:
  - Trend:      EMA20/EMA50/EMA200 (primary TF), H4 EMA200 macro bias, ATR
  - Momentum:   RSI(14), MACD(12,26,9) line/signal/histogram, Stochastic,
                a 6-bar MACD histogram window + whether it's declining from
                its recent peak (for the extended-entry check - see
                claude_advisor.SYSTEM_PROMPT)
  - Strength:   ADX(14), +DI/-DI
  - Volatility: ATR(14), Bollinger Bands(20,2) %B and bandwidth
  - SMC:        liquidity sweep (stop-hunt-then-reclaim) detection,
                premium/discount zone within the recent swing range,
                market structure (HH/HL/LH/LL, trend, latest BOS/CHoCH),
                the most recent unmitigated bullish/bearish order block,
                still-open fair value gaps (imbalances)
  - Levels:     previous day/week high/low (PDH/PDL, PWH/PWL)
  - Price action: last closed candle's body/wick ratios, engulfing/pin-bar
  - Session:    active session(s), day of week, hour (UTC)
  - Raw data:   last 20 closed candles' OHLC, current bid/ask/spread

Honest limitation: no fundamentals/news, no order-flow/DOM, no cross-asset
correlation (DXY, yields, ...) - see the top-level README's "Honest
limitations" section for why and how to extend this.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import mt5_gateway as gw
from config import AdvisorConfig

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# indicators - pure functions over a DataFrame with open/high/low/close
# --------------------------------------------------------------------------- #
def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    result = 100 - 100 / (1 + rs)
    # avg_loss == 0 (no losses at all in the lookback) leaves rs undefined
    # above rather than "infinite" - true RSI there is 100, unless avg_gain
    # is also 0 (a truly flat series), which reads as neutral 50 instead.
    result = result.where(avg_loss != 0, np.where(avg_gain != 0, 100.0, 50.0))
    return result.fillna(50.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def macd_hist_shape(hist: pd.Series, lookback: int = 6) -> dict:
    """A short window of MACD histogram bars, so Claude can tell decelerating
    momentum from accelerating momentum - something a single-bar macd_hist
    vs macd_hist_prev comparison can't see. Added for the "extended-entry"
    momentum check in claude_advisor.SYSTEM_PROMPT: a chased entry where the
    histogram already peaked and is now retreating (still on the right side
    of zero, still passing the mechanical momentum test) is a materially
    weaker setup than one where momentum is still building - measured in
    practice against trade history (a loss on an extended entry with a
    already-declining histogram, versus wins on extended entries where it
    was still accelerating).

    "Peak" is read in the direction the histogram already leans: the highest
    value in the window for bullish (positive) momentum, the lowest (most
    negative) for bearish - so this works the same for either direction
    without needing to know the trade's intended direction up front.
    """
    recent = hist.tail(lookback)
    current = float(recent.iloc[-1])
    peak = float(recent.max()) if current >= 0 else float(recent.min())
    declining = current < peak if current >= 0 else current > peak
    return {
        "lookback_bars": lookback,
        "values": [round(float(v), 4) for v in recent.tolist()],
        "peak_in_window": round(peak, 4),
        "declining_from_peak": bool(declining),
    }


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def bollinger(series: pd.Series, n: int = 20, k: float = 2.0):
    mid = series.rolling(n).mean()
    std = series.rolling(n).std()
    upper = mid + k * std
    lower = mid - k * std
    percent_b = (series - lower) / (upper - lower).replace(0.0, np.nan)
    bandwidth = (upper - lower) / mid.replace(0.0, np.nan)
    return mid, upper, lower, percent_b, bandwidth


def adx_di(df: pd.DataFrame, n: int = 14):
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr_n = true_range(df).ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / tr_n.replace(0.0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / tr_n.replace(0.0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = dx.ewm(alpha=1 / n, adjust=False).mean()
    return adx.fillna(0.0), plus_di.fillna(0.0), minus_di.fillna(0.0)


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3, smooth: int = 3):
    low_n = df["low"].rolling(k).min()
    high_n = df["high"].rolling(k).max()
    percent_k = 100 * (df["close"] - low_n) / (high_n - low_n).replace(0.0, np.nan)
    percent_k_smoothed = percent_k.rolling(smooth).mean()
    percent_d = percent_k_smoothed.rolling(d).mean()
    return percent_k_smoothed.fillna(50.0), percent_d.fillna(50.0)


# --------------------------------------------------------------------------- #
# SMC structure
# --------------------------------------------------------------------------- #
def detect_liquidity_sweep(closed: pd.DataFrame, recent_bars: int, ref_bars: int,
                            min_pierce_price: float) -> dict:
    """A stop-hunt-then-reclaim within the last `recent_bars` closed bars:
    price pierces beyond the extreme of the prior `ref_bars` bars and a later
    (or the same) bar closes back on the right side of that level - the same
    definition used by the MQL5 Telegram-copier EA's SMC filter.
    """
    n = len(closed)
    if n < recent_bars + ref_bars + 1:
        return {"swept": False, "direction": None, "level": None, "pierce": 0.0}

    ref_window = closed.iloc[-(recent_bars + ref_bars):-recent_bars]
    recent_window = closed.iloc[-recent_bars:]
    prior_low = ref_window["low"].min()
    prior_high = ref_window["high"].max()

    for idx in range(len(recent_window)):
        bar = recent_window.iloc[idx]
        if bar["low"] < prior_low - min_pierce_price:
            if (recent_window["close"].iloc[idx:] > prior_low).any():
                return {"swept": True, "direction": "buy", "level": float(prior_low),
                        "pierce": float(prior_low - bar["low"])}
        if bar["high"] > prior_high + min_pierce_price:
            if (recent_window["close"].iloc[idx:] < prior_high).any():
                return {"swept": True, "direction": "sell", "level": float(prior_high),
                        "pierce": float(bar["high"] - prior_high)}
    return {"swept": False, "direction": None, "level": None, "pierce": 0.0}


def premium_discount_zone(closed: pd.DataFrame, lookback: int) -> dict:
    window = closed.iloc[-lookback:]
    range_low = float(window["low"].min())
    range_high = float(window["high"].max())
    last_close = float(closed["close"].iloc[-1])
    pct = 0.5 if range_high == range_low else (last_close - range_low) / (range_high - range_low)
    zone = "discount" if pct < 0.45 else "premium" if pct > 0.55 else "equilibrium"
    return {"zone": zone, "range_low": range_low, "range_high": range_high, "position_pct": round(pct, 3)}


def find_swing_points(closed: pd.DataFrame, order: int = 3) -> tuple:
    """Fractal swing highs/lows: bar i is a swing high if its high is the
    strict max of the `order` bars on each side of it (a swing low mirrors
    this for lows). Needs `order` bars confirmed on both sides, so the most
    recent `order` closed bars can never yet produce a new confirmed swing -
    that's inherent to the definition, not a bug: a swing point isn't real
    until price has moved away from it.

    Returns (swing_highs, swing_lows), each a list of (bar_index, price)
    tuples in chronological order.
    """
    highs = closed["high"].to_numpy()
    lows = closed["low"].to_numpy()
    n = len(closed)
    swing_highs, swing_lows = [], []
    for i in range(order, n - order):
        window_h = highs[i - order:i + order + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            swing_highs.append((i, float(highs[i])))
        window_l = lows[i - order:i + order + 1]
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            swing_lows.append((i, float(lows[i])))
    return swing_highs, swing_lows


def market_structure(closed: pd.DataFrame, order: int = 3) -> dict:
    """The ICT-style market-structure read: classifies each confirmed swing
    high/low as higher/lower than the one before it (HH/LH, HL/LL), derives
    the prevailing trend from the two most recent, and reports the most
    recent structural break - BOS (Break of Structure: the latest closed
    bar's close breaks past the most recent confirmed swing level in the
    direction the trend was ALREADY going - continuation) or CHoCH (Change
    of Character: the same kind of break, but AGAINST the prevailing trend -
    the first sign of a possible reversal). This is the single most-watched
    SMC/ICT signal after the sweep already computed above: a CHoCH against
    an otherwise-bullish setup is a real reason for caution; a fresh BOS in
    the trade's own direction is real corroborating evidence.
    """
    swing_highs, swing_lows = find_swing_points(closed, order)
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return {"trend": "undefined", "last_event": None, "last_swing_high": None,
                "last_swing_low": None}

    high_label = "HH" if swing_highs[-1][1] > swing_highs[-2][1] else "LH"
    low_label = "HL" if swing_lows[-1][1] > swing_lows[-2][1] else "LL"
    if high_label == "HH" and low_label == "HL":
        trend = "bullish"
    elif high_label == "LH" and low_label == "LL":
        trend = "bearish"
    else:
        trend = "transitional"

    last_swing_high_price = swing_highs[-1][1]
    last_swing_low_price = swing_lows[-1][1]
    last_close = float(closed["close"].iloc[-1])

    last_event = None
    if last_close > last_swing_high_price:
        last_event = {"type": "BOS" if trend == "bullish" else "CHoCH", "direction": "bullish",
                      "broke_level": last_swing_high_price}
    elif last_close < last_swing_low_price:
        last_event = {"type": "BOS" if trend == "bearish" else "CHoCH", "direction": "bearish",
                      "broke_level": last_swing_low_price}

    return {
        "trend": trend,
        "last_swing_high": {"price": last_swing_high_price, "label": high_label},
        "last_swing_low": {"price": last_swing_low_price, "label": low_label},
        "last_event": last_event,
    }


def detect_order_blocks(closed: pd.DataFrame, lookback: int, displacement_atr_mult: float = 1.5) -> dict:
    """The most recent bullish and bearish order block within the last
    `lookback` closed bars: the standard ICT definition is the last
    opposite-colored candle immediately before a "displacement" candle (a
    strong, wide-range expansion move) - the last footprint of positioning
    before price was driven away from it. A displacement candle here is one
    whose range is at least `displacement_atr_mult` x ATR14 AND whose body
    is at least 60% of its own range (a decisive close, not just a long-
    wicked bar). Reports whether current price is trading back inside each
    zone (a classic "mitigation" entry trigger).
    """
    padded = closed.tail(lookback + 15).reset_index(drop=True)  # extra history so ATR has a real warm-up
    atr14 = atr(padded)
    n = len(padded)
    start = max(1, n - lookback)
    bullish_ob, bearish_ob = None, None

    for i in range(start, n):
        bar = padded.iloc[i]
        rng = bar["high"] - bar["low"]
        body = abs(bar["close"] - bar["open"])
        cur_atr = atr14.iloc[i]
        if cur_atr <= 0 or rng < displacement_atr_mult * cur_atr or body < 0.6 * rng:
            continue
        prev = padded.iloc[i - 1]
        displacement_is_bullish = bar["close"] > bar["open"]
        if displacement_is_bullish and prev["close"] < prev["open"]:
            bullish_ob = {"high": float(prev["high"]), "low": float(prev["low"]), "bars_ago": n - 1 - i}
        elif not displacement_is_bullish and prev["close"] > prev["open"]:
            bearish_ob = {"high": float(prev["high"]), "low": float(prev["low"]), "bars_ago": n - 1 - i}

    last_close = float(closed["close"].iloc[-1])
    for ob in (bullish_ob, bearish_ob):
        if ob is not None:
            ob["price_inside_zone"] = bool(ob["low"] <= last_close <= ob["high"])
    return {"bullish_order_block": bullish_ob, "bearish_order_block": bearish_ob}


def detect_fair_value_gaps(closed: pd.DataFrame, lookback: int, max_reported: int = 5) -> list:
    """Still-open 3-candle imbalances (ICT "fair value gaps") within the last
    `lookback` bars: candle[i-2].high < candle[i].low leaves an unfilled
    bullish gap between them (price moved so fast candle[i-1] never traded
    that range); candle[i-2].low > candle[i].high leaves an unfilled bearish
    gap. A gap counts as "open" only if no later closed bar has traded back
    through it - a filled gap is no longer meaningful and is dropped.
    Returned most-recent-first, capped at `max_reported` to keep the
    snapshot compact.
    """
    window = closed.tail(lookback + 2).reset_index(drop=True)
    n = len(window)
    gaps = []
    for i in range(2, n):
        c0, c2 = window.iloc[i - 2], window.iloc[i]
        if c0["high"] < c2["low"]:
            gap = {"direction": "bullish", "top": float(c2["low"]), "bottom": float(c0["high"]),
                   "bars_ago": n - 1 - i}
        elif c0["low"] > c2["high"]:
            gap = {"direction": "bearish", "top": float(c0["low"]), "bottom": float(c2["high"]),
                   "bars_ago": n - 1 - i}
        else:
            continue

        later = window.iloc[i + 1:]
        if gap["direction"] == "bullish":
            filled = bool((later["low"] <= gap["bottom"]).any())
        else:
            filled = bool((later["high"] >= gap["top"]).any())
        if not filled:
            gaps.append(gap)

    gaps.sort(key=lambda g: g["bars_ago"])
    return gaps[:max_reported]


def daily_weekly_levels(gateway, symbol: str) -> dict:
    """Previous completed day's and week's high/low - cheap, extremely
    commonly watched reference levels (PDH/PDL, PWH/PWL) for both entries
    and where a stop is likely to get run. Fetched as native D1/W1 bars
    rather than resampled from an intraday timeframe, since a manual
    resample doesn't reliably line up with the broker's own daily/weekly
    boundaries (weekend gaps, DST, differing session-close conventions).
    """
    daily_closed = gateway.get_bars(symbol, "D1", 3).iloc[:-1]
    weekly_closed = gateway.get_bars(symbol, "W1", 3).iloc[:-1]
    prev_day = daily_closed.iloc[-1]
    prev_week = weekly_closed.iloc[-1]
    return {
        "prev_day_high": round(float(prev_day["high"]), 2),
        "prev_day_low": round(float(prev_day["low"]), 2),
        "prev_week_high": round(float(prev_week["high"]), 2),
        "prev_week_low": round(float(prev_week["low"]), 2),
    }


def dxy_context(gateway, cfg: AdvisorConfig, timeframe: str = "H1", bars: int = 60) -> dict | None:
    """Optional US Dollar Index context (see config.py's dxy_symbol) - XAUUSD
    is usually (not always) inversely correlated with dollar strength, so a
    fresh DXY move price hasn't caught up with yet is useful corroborating/
    contradicting context for Claude (see claude_advisor.SYSTEM_PROMPT),
    never a hard gate. Returns None when dxy_symbol is blank (the default,
    disables this section entirely) or when the symbol isn't available on
    this broker/account (not in Market Watch, wrong name, not enough
    history yet) - never raises, never blocks the rest of the snapshot.
    """
    if not cfg.dxy_symbol:
        return None
    try:
        df = gateway.get_bars(cfg.dxy_symbol, timeframe, bars)
        closed = df.iloc[:-1]
        if len(closed) < 21:
            return None
        last_close = float(closed["close"].iloc[-1])
        ema20 = float(ema(closed["close"], 20).iloc[-1])
        ref_close = float(closed["close"].iloc[-11])
        change_pct = (last_close - ref_close) / ref_close * 100.0 if ref_close else 0.0
        return {
            "symbol": cfg.dxy_symbol,
            "timeframe": timeframe,
            "last_close": round(last_close, 3),
            "vs_ema20": "above" if last_close > ema20 else "below",
            "change_pct_last_10_bars": round(change_pct, 3),
        }
    except Exception as exc:
        log.warning("dxy_context: %r unavailable (%s) - continuing without DXY context.",
                    cfg.dxy_symbol, exc)
        return None


def consensus_context(gateway, cfg: AdvisorConfig) -> dict | None:
    """Read-only summary of OTHER trading systems' open positions on this
    same symbol (see config.py's consensus_magic_numbers) - purely
    informational, never gates anything (executor.gate() knows nothing
    about this). None when consensus_magic_numbers is empty (the default),
    or when the gateway call itself fails (transient MT5 hiccup) - this is
    context only, so a failure here must never abort the whole evaluation
    cycle the way an uncaught exception from build_feature_snapshot() would.
    """
    if not cfg.consensus_magic_numbers:
        return None
    try:
        buys = sells = 0
        for magic in cfg.consensus_magic_numbers:
            for p in gateway.open_positions(cfg.symbol, magic):
                if p["direction"] == "buy":
                    buys += 1
                else:
                    sells += 1
        return {"other_system_buy_positions": buys, "other_system_sell_positions": sells}
    except Exception as exc:
        log.warning("consensus_context: could not read other systems' positions (%s) - "
                    "continuing without consensus context.", exc)
        return None


def recent_performance_summary(gateway, cfg: AdvisorConfig, count: int = 10) -> dict:
    """Win/loss context from this system's own last `count` closed trades -
    qualitative input for Claude's reasoning (see claude_advisor.SYSTEM_PROMPT),
    never a hard gate: executor.gate() knows nothing about recent performance,
    so a cold streak narrows Claude's own conviction rather than being
    enforced as a rule here. Returns a safe "no data yet" shape rather than
    raising when the account has no closed trades under this magic yet
    (a brand new account, or a fresh magic number).
    """
    try:
        trades = gateway.recent_closed_trades(cfg.symbol, cfg.magic, count)
    except Exception as exc:
        # Called unconditionally every cycle (unlike dxy_context/
        # consensus_context, which are opt-in) - a transient MT5 hiccup
        # here must never abort the whole evaluation cycle. Degrades to
        # the same shape as "no trades yet", which claude_advisor.
        # SYSTEM_PROMPT already tells Claude to just ignore.
        log.warning("recent_performance_summary: could not read trade history (%s) - "
                    "continuing without it this cycle.", exc)
        return {"trade_count": 0, "note": "performance data temporarily unavailable"}
    if not trades:
        return {"trade_count": 0, "note": "no closed trades yet under this magic number"}
    wins = [t for t in trades if t["pnl_dollars"] > 0]
    losses = [t for t in trades if t["pnl_dollars"] < 0]
    return {
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100.0, 1),
        "net_pnl_dollars": round(sum(t["pnl_dollars"] for t in trades), 2),
        "last_5_results": [
            {"direction": t["direction"], "pnl_dollars": round(t["pnl_dollars"], 2)}
            for t in trades[:5]
        ],
    }


# --------------------------------------------------------------------------- #
# price action
# --------------------------------------------------------------------------- #
def candle_features(closed: pd.DataFrame) -> dict:
    last, prev = closed.iloc[-1], closed.iloc[-2]
    body = abs(last["close"] - last["open"])
    upper_wick = last["high"] - max(last["close"], last["open"])
    lower_wick = min(last["close"], last["open"]) - last["low"]
    rng = last["high"] - last["low"]
    bullish_engulfing = (last["close"] > last["open"] and prev["close"] < prev["open"]
                         and last["close"] >= prev["open"] and last["open"] <= prev["close"])
    bearish_engulfing = (last["close"] < last["open"] and prev["close"] > prev["open"]
                         and last["open"] >= prev["close"] and last["close"] <= prev["open"])
    pin_bar_bull = lower_wick > 2 * body and upper_wick < body
    pin_bar_bear = upper_wick > 2 * body and lower_wick < body
    return {
        "bullish": bool(last["close"] > last["open"]),
        "body_pct_of_range": round(float(body / rng), 3) if rng > 0 else 0.0,
        "bullish_engulfing": bool(bullish_engulfing),
        "bearish_engulfing": bool(bearish_engulfing),
        "pin_bar_bullish": bool(pin_bar_bull),
        "pin_bar_bearish": bool(pin_bar_bear),
    }


def session_info(ts_utc: pd.Timestamp) -> dict:
    hour = ts_utc.hour
    sessions = []
    if 0 <= hour < 9:
        sessions.append("Asian")
    if 7 <= hour < 16:
        sessions.append("London")
    if 12 <= hour < 21:
        sessions.append("New York")
    return {
        "hour_utc": hour,
        "day_of_week": ts_utc.strftime("%A"),
        "active_sessions": sessions or ["Off-hours"],
        "session_overlap": len(sessions) >= 2,
    }


# --------------------------------------------------------------------------- #
# indicator bundle for one timeframe (used by build_feature_snapshot)
# --------------------------------------------------------------------------- #
def timeframe_indicators(df: pd.DataFrame) -> dict:
    close = df["close"]
    ema20, ema50, ema200 = ema(close, 20), ema(close, 50), ema(close, 200)
    macd_line, macd_signal, macd_hist = macd(close)
    adx, plus_di, minus_di = adx_di(df)
    stoch_k, stoch_d = stochastic(df)
    _, _, _, pct_b, bandwidth = bollinger(close)
    atr14 = atr(df)

    def last(series: pd.Series) -> float:
        return round(float(series.iloc[-1]), 4)

    return {
        "close": last(close),
        "ema20": last(ema20), "ema50": last(ema50), "ema200": last(ema200),
        "rsi14": last(rsi(close)),
        "macd_line": last(macd_line), "macd_signal": last(macd_signal), "macd_hist": last(macd_hist),
        "macd_hist_prev": round(float(macd_hist.iloc[-2]), 4),
        "macd_hist_shape": macd_hist_shape(macd_hist),
        "adx14": last(adx), "plus_di": last(plus_di), "minus_di": last(minus_di),
        "stoch_k": last(stoch_k), "stoch_d": last(stoch_d),
        "atr14": last(atr14),
        "bollinger_percent_b": last(pct_b), "bollinger_bandwidth": last(bandwidth),
    }


# --------------------------------------------------------------------------- #
# orchestration - the only function that touches MT5
# --------------------------------------------------------------------------- #
def last_closed_time(gateway, symbol: str, timeframe_name: str) -> pd.Timestamp:
    """The open-time of the most recently CLOSED bar (i.e. excluding the
    currently-forming one) - used by main.py to detect a new bar.
    """
    df = gateway.get_bars(symbol, timeframe_name, 2)
    return df["time"].iloc[-2]


def build_feature_snapshot(gateway, cfg: AdvisorConfig) -> dict:
    primary = gateway.get_bars(cfg.symbol, cfg.primary_timeframe, cfg.bars_per_timeframe)
    trend = gateway.get_bars(cfg.symbol, cfg.trend_timeframe, cfg.bars_per_timeframe)
    # Drop the still-forming last bar from each - every computation below
    # must only ever see closed bars, or the snapshot would change mid-bar.
    primary_closed = primary.iloc[:-1].reset_index(drop=True)
    trend_closed = trend.iloc[:-1].reset_index(drop=True)

    tick = gateway.get_tick(cfg.symbol)
    spec = gateway.symbol_spec(cfg.symbol)

    sweep = detect_liquidity_sweep(primary_closed, cfg.sweep_recent_bars, cfg.sweep_ref_bars,
                                    cfg.sweep_min_pierce_pips * spec.point * 10)
    zone = premium_discount_zone(primary_closed, cfg.sweep_recent_bars + cfg.sweep_ref_bars)
    structure = market_structure(primary_closed, cfg.structure_swing_order)
    order_blocks = detect_order_blocks(primary_closed, cfg.order_block_lookback_bars,
                                        cfg.order_block_displacement_atr_mult)
    fvgs = detect_fair_value_gaps(primary_closed, cfg.fvg_lookback_bars)
    levels = daily_weekly_levels(gateway, cfg.symbol)
    performance = recent_performance_summary(gateway, cfg)
    dxy = dxy_context(gateway, cfg)
    consensus = consensus_context(gateway, cfg)

    recent_candles = primary_closed.tail(20)[["time", "open", "high", "low", "close", "volume"]].copy()
    recent_candles["time"] = recent_candles["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    # int64/bool_ don't subclass Python's int/bool, so json.dumps() rejects them
    # outright (unlike float64, which does subclass float and serializes fine) -
    # cast explicitly rather than relying on claude_advisor's `default=str` escape
    # hatch, which would silently turn these into quoted strings.
    recent_candles["volume"] = recent_candles["volume"].astype(int)

    return {
        "symbol": cfg.symbol,
        "timestamp_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_price": {"bid": tick.bid, "ask": tick.ask, "spread_points": spec.spread_points},
        "session": session_info(primary_closed["time"].iloc[-1]),
        "primary_timeframe": cfg.primary_timeframe,
        "primary_indicators": timeframe_indicators(primary_closed),
        "trend_timeframe": cfg.trend_timeframe,
        "trend_bias": {
            "close": round(float(trend_closed["close"].iloc[-1]), 4),
            "ema200": round(float(ema(trend_closed["close"], 200).iloc[-1]), 4),
        },
        "smc": {
            "liquidity_sweep": sweep,
            "premium_discount": zone,
            "market_structure": structure,
            "order_blocks": order_blocks,
            "fair_value_gaps": fvgs,
        },
        "daily_weekly_levels": levels,
        "dxy": dxy,
        "consensus": consensus,
        "recent_performance": performance,
        "last_closed_candle": candle_features(primary_closed),
        "recent_candles": recent_candles.to_dict(orient="records"),
    }

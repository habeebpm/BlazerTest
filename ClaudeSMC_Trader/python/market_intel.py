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
  - Momentum:   RSI(14), MACD(12,26,9) line/signal/histogram, Stochastic
  - Strength:   ADX(14), +DI/-DI
  - Volatility: ATR(14), Bollinger Bands(20,2) %B and bandwidth
  - SMC:        liquidity sweep (stop-hunt-then-reclaim) detection,
                premium/discount zone within the recent swing range
  - Price action: last closed candle's body/wick ratios, engulfing/pin-bar
  - Session:    active session(s), day of week, hour (UTC)
  - Raw data:   last 20 closed candles' OHLC, current bid/ask/spread

Honest limitation: no fundamentals/news, no order-flow/DOM, no cross-asset
correlation (DXY, yields, ...) - see the top-level README's "Honest
limitations" section for why and how to extend this.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import mt5_gateway as gw
from config import AdvisorConfig


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
        "smc": {"liquidity_sweep": sweep, "premium_discount": zone},
        "last_closed_candle": candle_features(primary_closed),
        "recent_candles": recent_candles.to_dict(orient="records"),
    }

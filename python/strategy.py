"""
The three confluences.

A trade is taken only when ALL THREE agree on the same direction:

  1. TREND     - H4 close vs EMA(200) sets the macro bias, and on the working
                 timeframe EMA(20) must be on the correct side of EMA(50) with
                 a *fresh* cross within `cross_lookback` closed bars.
  2. MOMENTUM  - MACD main on the correct side of its signal line AND turning
                 the right way, with RSI past the midline but not yet in the
                 overbought/oversold zone that would mean chasing.
  3. STRENGTH  - ADX at or above the threshold (the market is actually
                 trending, not chopping) with +DI/-DI pointing the same way.

Everything here is pure pandas: no MetaTrader5 import, so the logic can be
unit-tested and backtested on any machine, including this Linux container.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

import indicators as ind
from config import TradeConfig


@dataclass
class Confluences:
    """Per-direction verdict on each of the three confluences."""
    trend: bool = False
    momentum: bool = False
    strength: bool = False
    reasons: dict = field(default_factory=dict)

    @property
    def all_agree(self) -> bool:
        return self.trend and self.momentum and self.strength

    @property
    def count(self) -> int:
        return int(self.trend) + int(self.momentum) + int(self.strength)


@dataclass
class Signal:
    direction: str | None          # "buy" | "sell" | None
    buy: Confluences
    sell: Confluences
    atr: float
    close: float
    bar_time: pd.Timestamp | None

    def summary(self) -> str:
        def fmt(tag: str, c: Confluences) -> str:
            marks = "".join(
                "Y" if v else "n" for v in (c.trend, c.momentum, c.strength)
            )
            return f"{tag}[T/M/S={marks} {c.count}/3]"
        return f"{fmt('BUY', self.buy)} {fmt('SELL', self.sell)} -> {self.direction or 'no trade'}"


def compute_indicators(work: pd.DataFrame, trend: pd.DataFrame,
                       cfg: TradeConfig) -> pd.DataFrame:
    """Attach every indicator column to a copy of the working-timeframe frame.

    `work` and `trend` are OHLC frames indexed by bar open time, oldest first.
    The higher-timeframe EMA is merged in as-of, so each working bar sees only
    the last *closed* higher-timeframe bar - no look-ahead.
    """
    df = work.copy()

    df["ema_fast"] = ind.ema(df["close"], cfg.ema_fast)
    df["ema_slow"] = ind.ema(df["close"], cfg.ema_slow)
    df["macd"], df["macd_signal"], df["macd_hist"] = ind.macd(
        df["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal
    )
    df["rsi"] = ind.rsi(df["close"], cfg.rsi_period)
    df["adx"], df["plus_di"], df["minus_di"] = ind.adx(
        df["high"], df["low"], df["close"], cfg.adx_period
    )
    df["atr"] = ind.atr(df["high"], df["low"], df["close"], cfg.atr_period)
    df["bb_upper"], df["bb_mid"], df["bb_lower"] = ind.bollinger(
        df["close"], cfg.bands_period, cfg.bands_deviation
    )

    htf = pd.DataFrame({
        "htf_close": trend["close"],
        "htf_ema": ind.ema(trend["close"], cfg.trend_ema_period),
    })
    # Shift the HTF frame forward one bar: a working bar may only use the
    # higher-timeframe bar that had already CLOSED when it opened.
    htf = htf.shift(1).dropna()

    df = pd.merge_asof(
        df.sort_index(),
        htf.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward",
    )
    return df


def _crossed_recently(df: pd.DataFrame, idx: int, lookback: int, bullish: bool) -> bool:
    """Did ema_fast cross ema_slow within `lookback` bars ending at idx?"""
    if lookback <= 0:
        return True  # alignment alone is enough
    start = max(1, idx - lookback + 1)
    for i in range(start, idx + 1):
        prev_fast, prev_slow = df["ema_fast"].iloc[i - 1], df["ema_slow"].iloc[i - 1]
        fast, slow = df["ema_fast"].iloc[i], df["ema_slow"].iloc[i]
        if bullish and prev_fast <= prev_slow and fast > slow:
            return True
        if not bullish and prev_fast >= prev_slow and fast < slow:
            return True
    return False


def evaluate(df: pd.DataFrame, cfg: TradeConfig, idx: int = -1) -> Signal:
    """Evaluate the three confluences on bar `idx` (default: the last row).

    Call this with a frame whose final row is the last CLOSED bar.
    """
    if idx < 0:
        idx = len(df) + idx
    row = df.iloc[idx]
    prev = df.iloc[idx - 1]

    required = ["htf_close", "htf_ema", "ema_fast", "ema_slow", "macd",
                "macd_signal", "rsi", "adx", "plus_di", "minus_di", "atr"]
    if row[required].isna().any():
        empty = Confluences(reasons={"warmup": "not enough history yet"})
        return Signal(None, empty, empty, float("nan"), float(row["close"]), df.index[idx])

    buy, sell = Confluences(), Confluences()

    # ---- Confluence 1: TREND -------------------------------------------------
    htf_bull = row["htf_close"] > row["htf_ema"]
    htf_bear = row["htf_close"] < row["htf_ema"]
    buy.trend = bool(
        htf_bull and row["ema_fast"] > row["ema_slow"]
        and _crossed_recently(df, idx, cfg.cross_lookback, bullish=True)
    )
    sell.trend = bool(
        htf_bear and row["ema_fast"] < row["ema_slow"]
        and _crossed_recently(df, idx, cfg.cross_lookback, bullish=False)
    )
    for side, c in (("buy", buy), ("sell", sell)):
        c.reasons["trend"] = (
            f"htf_close={row['htf_close']:.2f} vs htf_ema={row['htf_ema']:.2f}, "
            f"ema{cfg.ema_fast}={row['ema_fast']:.2f} vs ema{cfg.ema_slow}={row['ema_slow']:.2f}"
        )

    # ---- Confluence 2: MOMENTUM ---------------------------------------------
    macd_bull = row["macd"] > row["macd_signal"] and row["macd"] > prev["macd"]
    macd_bear = row["macd"] < row["macd_signal"] and row["macd"] < prev["macd"]
    rsi_ok_buy = cfg.rsi_midline < row["rsi"] < cfg.rsi_upper_block
    rsi_ok_sell = cfg.rsi_lower_block < row["rsi"] < cfg.rsi_midline
    buy.momentum = bool(macd_bull and rsi_ok_buy)
    sell.momentum = bool(macd_bear and rsi_ok_sell)
    for c in (buy, sell):
        c.reasons["momentum"] = (
            f"macd={row['macd']:.3f} signal={row['macd_signal']:.3f} rsi={row['rsi']:.1f}"
        )

    # ---- Confluence 3: STRENGTH ---------------------------------------------
    trending = row["adx"] >= cfg.adx_min_level
    buy.strength = bool(trending and row["plus_di"] > row["minus_di"])
    sell.strength = bool(trending and row["minus_di"] > row["plus_di"])
    for c in (buy, sell):
        c.reasons["strength"] = (
            f"adx={row['adx']:.1f} (min {cfg.adx_min_level}) "
            f"+DI={row['plus_di']:.1f} -DI={row['minus_di']:.1f}"
        )

    direction = None
    if buy.all_agree and not sell.all_agree:
        direction = "buy"
    elif sell.all_agree and not buy.all_agree:
        direction = "sell"

    return Signal(direction, buy, sell, float(row["atr"]), float(row["close"]), df.index[idx])

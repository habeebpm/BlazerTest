"""
The three confluences.

Each confluence votes on a direction at two strengths:

  1. TREND     - pass: H4 close vs EMA(200) sets the bias and EMA(20) is on the
                       correct side of EMA(50) with a fresh cross within
                       `cross_lookback` closed bars.
                 confirmed: the EMAs have genuinely separated
                       (gap >= confirm_ema_gap_atr x ATR), not merely touched.
  2. MOMENTUM  - pass: MACD main on the correct side of its signal and turning
                       the right way, RSI past the midline but not overbought
                       / oversold.
                 confirmed: the MACD histogram is still expanding AND RSI is
                       confirm_rsi_margin beyond the midline.
  3. STRENGTH  - pass: ADX >= adx_min_level with +DI/-DI aligned.
                 confirmed: ADX >= confirm_adx_level AND the DI spread is at
                       least confirm_di_gap wide.

A trade needs `min_confluences` passes (default 2 of 3) and, when
`require_confirmation` is on, at least `min_confirmed` of those passes must be
confirmed. Requiring 2/3 alone would let two barely-passing readings open a
position; the confirmation tier means at least one leg of the thesis is
unambiguous.

A Bollinger veto sits outside the vote: no buying at or above the upper band,
no selling at or below the lower band, however many confluences agree.

Note what 2-of-3 implies: the trend confluence is not mandatory, so momentum
plus strength can open a position against the higher-timeframe trend. Set
`require_trend_confluence` to force the trend leg to be one of the two.

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
    trend_confirmed: bool = False
    momentum_confirmed: bool = False
    strength_confirmed: bool = False
    vetoed: bool = False
    veto_reason: str = ""
    reasons: dict = field(default_factory=dict)

    @property
    def all_agree(self) -> bool:
        return self.trend and self.momentum and self.strength

    @property
    def count(self) -> int:
        return int(self.trend) + int(self.momentum) + int(self.strength)

    @property
    def confirmed_count(self) -> int:
        return (int(self.trend_confirmed) + int(self.momentum_confirmed)
                + int(self.strength_confirmed))

    def marks(self) -> str:
        """Compact per-confluence state: C=confirmed, y=passed, n=failed."""
        pairs = ((self.trend, self.trend_confirmed),
                 (self.momentum, self.momentum_confirmed),
                 (self.strength, self.strength_confirmed))
        return "".join("C" if conf else ("y" if ok else "n") for ok, conf in pairs)

    def qualifies(self, cfg: TradeConfig) -> bool:
        """Does this direction meet the configured entry bar?"""
        if self.vetoed:
            return False
        if self.count < cfg.min_confluences:
            return False
        if cfg.require_confirmation and self.confirmed_count < cfg.min_confirmed:
            return False
        if getattr(cfg, "require_trend_confluence", False) and not self.trend:
            return False
        return True


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
            veto = " VETO" if c.vetoed else ""
            return (f"{tag}[T/M/S={c.marks()} {c.count}/3 "
                    f"conf={c.confirmed_count}{veto}]")
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

    A direction is returned when it collects at least `cfg.min_confluences`
    passes with at least `cfg.min_confirmed` of them confirmed (when
    `cfg.require_confirmation` is set), is not Bollinger-vetoed, and the
    opposite direction does not also qualify.

    Call this with a frame whose final row is the last CLOSED bar.
    """
    if idx < 0:
        idx = len(df) + idx
    row = df.iloc[idx]
    prev = df.iloc[idx - 1]

    required = ["htf_close", "htf_ema", "ema_fast", "ema_slow", "macd",
                "macd_signal", "macd_hist", "rsi", "adx", "plus_di", "minus_di",
                "atr", "bb_upper", "bb_lower"]
    if row[required].isna().any():
        empty = Confluences(reasons={"warmup": "not enough history yet"})
        return Signal(None, empty, empty, float("nan"), float(row["close"]), df.index[idx])

    buy, sell = Confluences(), Confluences()
    atr = float(row["atr"])

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
    # confirmed when the EMAs have actually separated, not just crossed
    ema_gap = abs(row["ema_fast"] - row["ema_slow"])
    gap_needed = cfg.confirm_ema_gap_atr * atr
    buy.trend_confirmed = bool(buy.trend and ema_gap >= gap_needed)
    sell.trend_confirmed = bool(sell.trend and ema_gap >= gap_needed)
    for c in (buy, sell):
        c.reasons["trend"] = (
            f"htf_close={row['htf_close']:.2f} vs htf_ema={row['htf_ema']:.2f}, "
            f"ema{cfg.ema_fast}={row['ema_fast']:.2f} vs ema{cfg.ema_slow}={row['ema_slow']:.2f}, "
            f"gap={ema_gap:.2f} (confirm >= {gap_needed:.2f})"
        )

    # ---- Confluence 2: MOMENTUM ---------------------------------------------
    macd_bull = row["macd"] > row["macd_signal"] and row["macd"] > prev["macd"]
    macd_bear = row["macd"] < row["macd_signal"] and row["macd"] < prev["macd"]
    rsi_ok_buy = cfg.rsi_midline < row["rsi"] < cfg.rsi_upper_block
    rsi_ok_sell = cfg.rsi_lower_block < row["rsi"] < cfg.rsi_midline
    buy.momentum = bool(macd_bull and rsi_ok_buy)
    sell.momentum = bool(macd_bear and rsi_ok_sell)
    # confirmed when the histogram is still expanding and RSI is decisively past 50
    hist_expanding_up = row["macd_hist"] > prev["macd_hist"]
    hist_expanding_down = row["macd_hist"] < prev["macd_hist"]
    buy.momentum_confirmed = bool(
        buy.momentum and hist_expanding_up
        and row["rsi"] >= cfg.rsi_midline + cfg.confirm_rsi_margin
    )
    sell.momentum_confirmed = bool(
        sell.momentum and hist_expanding_down
        and row["rsi"] <= cfg.rsi_midline - cfg.confirm_rsi_margin
    )
    for c in (buy, sell):
        c.reasons["momentum"] = (
            f"macd={row['macd']:.3f} signal={row['macd_signal']:.3f} "
            f"hist={row['macd_hist']:+.3f} (prev {prev['macd_hist']:+.3f}) "
            f"rsi={row['rsi']:.1f}"
        )

    # ---- Confluence 3: STRENGTH ---------------------------------------------
    trending = row["adx"] >= cfg.adx_min_level
    buy.strength = bool(trending and row["plus_di"] > row["minus_di"])
    sell.strength = bool(trending and row["minus_di"] > row["plus_di"])
    # confirmed on a stronger ADX with a genuinely wide DI spread
    di_gap = abs(row["plus_di"] - row["minus_di"])
    strong_adx = row["adx"] >= cfg.confirm_adx_level
    buy.strength_confirmed = bool(buy.strength and strong_adx and di_gap >= cfg.confirm_di_gap)
    sell.strength_confirmed = bool(sell.strength and strong_adx and di_gap >= cfg.confirm_di_gap)
    for c in (buy, sell):
        c.reasons["strength"] = (
            f"adx={row['adx']:.1f} (pass {cfg.adx_min_level}, confirm {cfg.confirm_adx_level}) "
            f"+DI={row['plus_di']:.1f} -DI={row['minus_di']:.1f} gap={di_gap:.1f}"
        )

    # ---- Bollinger veto (outside the vote) ----------------------------------
    if cfg.use_bands_veto:
        if row["close"] >= row["bb_upper"]:
            buy.vetoed = True
            buy.veto_reason = (f"close {row['close']:.2f} at/above upper band "
                               f"{row['bb_upper']:.2f} - move already extended")
        if row["close"] <= row["bb_lower"]:
            sell.vetoed = True
            sell.veto_reason = (f"close {row['close']:.2f} at/below lower band "
                                f"{row['bb_lower']:.2f} - move already extended")

    # ---- decision ------------------------------------------------------------
    buy_ok = buy.qualifies(cfg)
    sell_ok = sell.qualifies(cfg)
    direction = None
    if buy_ok and not sell_ok:
        direction = "buy"
    elif sell_ok and not buy_ok:
        direction = "sell"

    return Signal(direction, buy, sell, atr, float(row["close"]), df.index[idx])

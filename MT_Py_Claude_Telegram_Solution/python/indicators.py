"""
Pure-pandas indicator implementations that mirror MetaTrader 5's built-ins.

Where MT5 differs from the "textbook" formula, we follow MT5 so that the Python
bot and the MQL5 Expert Advisor produce the same signals:

  * iMACD  - the signal line is a SIMPLE moving average of the MACD main line
             (most other platforms use an EMA).
  * iATR   - MetaTrader averages True Range with an SMA, not Wilder smoothing.
  * iRSI   - Wilder smoothing (SMMA).
  * iADX   - Wilder smoothing of +DM/-DM/TR and of DX.
  * iBands - SMA mid-line with a population standard deviation (ddof=0).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (MT5 MODE_EMA)."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average (MT5 MODE_SMA)."""
    return series.rolling(period).mean()


def smma(series: pd.Series, period: int) -> pd.Series:
    """Wilder / smoothed moving average (MT5 MODE_SMMA)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
        method: str = "sma") -> pd.Series:
    """Average True Range. method='sma' matches MetaTrader's iATR."""
    tr = true_range(high, low, close)
    return sma(tr, period) if method == "sma" else smma(tr, period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index with Wilder smoothing (matches iRSI)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = smma(gain, period)
    avg_loss = smma(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0: an unbroken run of gains is RSI 100; a totally flat
    # series (no gains and no losses either) is undefined, call it 50.
    flat = (avg_gain == 0.0) & (avg_loss == 0.0)
    out = out.fillna(100.0)
    out[flat] = 50.0
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9,
         signal_method: str = "sma") -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD main/signal/histogram. signal_method='sma' matches MT5's iMACD."""
    main = ema(close, fast) - ema(close, slow)
    sig = sma(main, signal) if signal_method == "sma" else ema(main, signal)
    return main, sig, main - sig


def adx(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Average Directional Index -> (adx, +DI, -DI), Wilder smoothed (iADX)."""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )

    tr_smoothed = smma(true_range(high, low, close), period).replace(0.0, np.nan)
    plus_di = 100.0 * smma(plus_dm, period) / tr_smoothed
    minus_di = 100.0 * smma(minus_dm, period) / tr_smoothed

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return smma(dx.fillna(0.0), period), plus_di, minus_di


def bollinger(close: pd.Series, period: int = 20, deviation: float = 2.0
              ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands -> (upper, middle, lower), population stdev like iBands."""
    middle = sma(close, period)
    std = close.rolling(period).std(ddof=0)
    return middle + deviation * std, middle, middle - deviation * std

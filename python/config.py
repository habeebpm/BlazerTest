"""
Configuration for the XAUUSD confluence trading bot.

Everything the user asked for lives at the top of TradeConfig:
    lots = 0.02, stop_loss = 6 points, trailing_stop = 3 points.

--------------------------------------------------------------------------
READ THIS ABOUT "POINTS"
--------------------------------------------------------------------------
"Point" is ambiguous on gold, and getting it wrong is expensive:

    distance_unit = "point"  -> 1 unit = symbol_info.point  = 0.01 USD
                                (6 points = $0.06 of price movement)
    distance_unit = "pip"    -> 1 unit = 10 * point         = 0.10 USD
                                (6 pips = $0.60)
    distance_unit = "usd"    -> 1 unit = 1.00 USD of price
                                (6 = $6.00 -> ~600 broker points)

The default below is "point", i.e. your numbers taken literally. On a normal
XAUUSD feed the spread alone is 15-40 points and the broker's minimum stop
distance is often 0-50 points, so a 6-point stop is *below the spread* and the
broker will reject the order (or stop you out instantly on the spread).

preflight_check() in mt5_client.py verifies this against your live broker
values at startup and refuses to trade rather than bleeding money. If it tells
you the distance is too small, set distance_unit = "usd" (6 = $6.00 stop,
$3.00 trail), which is a sane gold configuration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class TradeConfig:
    # ---------------- what the user asked for ----------------
    symbol: str = "XAUUSD"
    lots: float = 0.02
    stop_loss_units: float = 6.0        # SL distance, in `distance_unit`
    trailing_stop_units: float = 3.0    # trail distance, in `distance_unit`
    distance_unit: str = "point"        # "point" | "pip" | "usd"  (see module docstring)
    take_profit_units: float = 0.0      # 0 = no fixed TP, exits are handled by the trail

    # Start trailing once the trade is this far in profit. Defaults to the
    # trail distance itself, so the stop starts following immediately.
    trail_start_units: float = 3.0

    # ---------------- strategy ----------------
    working_timeframe: str = "M15"      # entry timeframe
    trend_timeframe: str = "H4"         # macro trend timeframe
    trend_ema_period: int = 200
    ema_fast: int = 20
    ema_slow: int = 50
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    rsi_period: int = 14
    rsi_upper_block: float = 70.0
    rsi_lower_block: float = 30.0
    rsi_midline: float = 50.0
    adx_period: int = 14
    adx_min_level: float = 22.0
    atr_period: int = 14
    bands_period: int = 20
    bands_deviation: float = 2.0

    # How recently the EMA cross must have happened for the trend confluence
    # to count as a fresh trigger, in closed bars. This matters more than it
    # looks: at the exact bar the EMAs cross, ADX is still building (typically
    # 17-20) and MACD has not yet confirmed, so demanding the cross on the very
    # last closed bar means the three confluences essentially never align -
    # measured at ~1 signal per 2000 M15 bars. A window of 8 bars lets momentum
    # and strength confirm a still-fresh cross and yields roughly one signal per
    # day on M15. Set 0 to drop the cross requirement and accept alignment alone.
    cross_lookback: int = 8

    # ---------------- guards (not part of the 3 confluences) ----------------
    max_open_positions: int = 1
    max_trades_per_day: int = 6
    max_daily_loss_pct: float = 3.0
    max_spread_points: int = 350        # always in broker points
    use_session_filter: bool = True
    session_start_hour: int = 7         # broker/server time
    session_end_hour: int = 20
    close_before_weekend: bool = True
    weekend_close_hour: int = 20        # Friday, server time

    # ---------------- execution ----------------
    magic: int = 20260908
    deviation_points: int = 30          # max slippage
    comment: str = "XAUUSD-Confluence-Py"
    poll_seconds: float = 5.0           # trailing-stop / position management cadence

    # ---------------- connection (never hardcode credentials) ----------------
    login: int | None = None
    password: str | None = None
    server: str | None = None
    terminal_path: str | None = None    # e.g. C:/Program Files/MetaTrader 5/terminal64.exe

    @classmethod
    def from_env(cls, **overrides) -> "TradeConfig":
        """Build config, taking credentials and any MT5_* overrides from the env."""
        env_map = {
            "symbol": ("MT5_SYMBOL", str),
            "lots": ("MT5_LOTS", float),
            "stop_loss_units": ("MT5_SL_UNITS", float),
            "trailing_stop_units": ("MT5_TRAIL_UNITS", float),
            "trail_start_units": ("MT5_TRAIL_START_UNITS", float),
            "distance_unit": ("MT5_DISTANCE_UNIT", str),
            "login": ("MT5_LOGIN", int),
            "password": ("MT5_PASSWORD", str),
            "server": ("MT5_SERVER", str),
            "terminal_path": ("MT5_PATH", str),
        }
        kwargs: dict = {}
        for field_name, (env_name, caster) in env_map.items():
            raw = os.environ.get(env_name)
            if raw not in (None, ""):
                kwargs[field_name] = caster(raw)
        kwargs.update(overrides)
        return cls(**kwargs)

    def unit_size(self, point: float) -> float:
        """Convert one `distance_unit` into a price distance for this symbol."""
        if self.distance_unit == "point":
            return point
        if self.distance_unit == "pip":
            return point * 10.0
        if self.distance_unit == "usd":
            return 1.0
        raise ValueError(f"distance_unit must be point|pip|usd, got {self.distance_unit!r}")

    def sl_distance(self, point: float) -> float:
        return self.stop_loss_units * self.unit_size(point)

    def trail_distance(self, point: float) -> float:
        return self.trailing_stop_units * self.unit_size(point)

    def trail_start_distance(self, point: float) -> float:
        return self.trail_start_units * self.unit_size(point)

    def tp_distance(self, point: float) -> float:
        return self.take_profit_units * self.unit_size(point)

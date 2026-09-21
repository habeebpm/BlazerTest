"""
Configuration for the XAUUSD confluence trading bot.

Everything the user asked for lives at the top of TradeConfig:
    lots = 0.02, stop_loss = 6 points, trailing_stop = 3 points.

--------------------------------------------------------------------------
DISTANCE UNITS
--------------------------------------------------------------------------
Gold distances are quoted three different ways, and mixing them up is
expensive. One unit of `distance_unit` means:

    "point" -> symbol_info.point = 0.01 USD   (60 units = $0.60)
    "pip"   -> 10 * point        = 0.10 USD   (60 units = $6.00)  <-- default
    "usd"   -> 1.00 USD of price              (60 units = $60.00)

The defaults above are 60 pips stop / 30 pips trail = $6.00 / $3.00 on a
2-digit XAUUSD feed, which clears a normal 15-40 point ($0.15-$0.40) spread
with room to spare.

preflight_check() in mt5_client.py re-checks these against your broker's live
spread and minimum stop distance at startup and refuses to trade if the stop
is too tight to survive. Note that a CLOSED market reports a stale or padded
spread, so preflight downgrades spread complaints to warnings while the
session is shut.

"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class TradeConfig:
    # ---------------- what the user asked for ----------------
    symbol: str = "XAUUSD"
    lots: float = 0.01
    # Set use_risk_percent to size from equity instead, so the lot grows with
    # the account. 0.2% is the 1.0% daily cap divided by five, so five losing
    # trades are absorbed before the day halts.
    use_risk_percent: bool = False
    risk_percent: float = 0.2
    max_lot_size: float = 5.0   # hard cap on a risk-sized lot
    stop_loss_units: float = 60.0       # SL distance, in `distance_unit`
    trailing_stop_units: float = 30.0   # trail distance, in `distance_unit`
    distance_unit: str = "pip"          # "point" | "pip" | "usd"  (see module docstring)
    take_profit_units: float = 0.0      # 0 = no fixed TP, exits are handled by the trail

    # Start trailing once the trade is this far in profit. Defaults to the
    # trail distance itself, so the stop starts following immediately.
    trail_start_units: float = 30.0

    # ---------------- strategy ----------------
    working_timeframe: str = "M5"       # entry timeframe (evaluated on each close)
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

    # ---------------- how many confluences are required ----------------
    # A trade needs `min_confluences` of the three (trend / momentum / strength)
    # agreeing on a direction. At 2 the bot trades more often but on weaker
    # evidence, so `require_confirmation` demands that at least one of the
    # passing confluences also clears a stricter "confirmed" threshold - that
    # stops two barely-passing readings (ADX 22.1 with RSI 50.4) from opening
    # a position.
    min_confluences: int = 2
    require_confirmation: bool = True
    min_confirmed: int = 1

    # Side effect of dropping to 2/3: the trend confluence is no longer
    # mandatory, so momentum + strength can open a trade AGAINST the H4 trend
    # (measured at roughly 1 in 20 signals, and chop trades more often too).
    # Set this True to keep counter-trend entries out while still only needing
    # 2 of 3 - the trend leg then has to be one of the two.
    require_trend_confluence: bool = False

    # Minimum setup-quality score (0-100) required to enter; a score exactly
    # equal to this passes. 0 disables the gate.
    #
    # Set to 50: only setups scoring 50 or better are traded. The qualifying
    # floor is 40, so this drops the weakest band. Measured at about 6.9
    # fills/day over 24h with the shipped 4 positions (45 gave 8.5, the gate
    # off gives 10.0); the session filter trims that further - see
    # simulate.py, which applies the window.
    #
    # Note every confirmation test is evaluated on the working timeframe (M5):
    # the EMA gap uses the M5 EMAs and M5 ATR, the MACD histogram and RSI are
    # M5, and the ADX/DI spread is M5. Only the EMA(200) macro bias reads the
    # higher timeframe (trend_timeframe, H4), and that feeds the trend
    # confluence's PASS test, not its confirmation.
    #
    # Measured fills/day by gate and position cap (all other filters on):
    #     gate 50: 4.7 (2 pos)  5.9 (3)  6.9 (4)  7.6 (5)  8.1 (6)
    #     gate 45: 5.6          7.3      8.5      9.5      10.1
    #     gate off:6.4          8.4     10.0     11.2      12.0
    # Set 45 with 6 positions for ~10/day while keeping a gate, at the cost of
    # more correlated exposure; set 50 to return to the selective ~4.7/day.
    #
    # IMPORTANT: this score measures how strong the indicator agreement is,
    # NOT the probability that a trade wins - nothing in this project
    # estimates a win rate. See the confidence notes in README.md.
    min_confidence: float = 50.0

    # Confirmation thresholds - the stronger version of each confluence
    confirm_ema_gap_atr: float = 0.25   # trend:    EMA separation >= this x ATR
    confirm_rsi_margin: float = 5.0     # momentum: RSI this far past the midline
    confirm_adx_level: float = 28.0     # strength: ADX at least this (base 22)
    confirm_di_gap: float = 8.0         # strength: |+DI - -DI| at least this

    # Bollinger veto: never open into an already-extended move (price at or
    # beyond the band in the trade's direction). Not a confluence - a veto.
    use_bands_veto: bool = True

    # ---------------- guards (not part of the 3 confluences) ----------------
    max_open_positions: int = 4
    max_trades_per_day: int = 0         # 0 = unlimited (daily loss limit still applies)

    # Up to four positions at once, but only in the SAME direction. Note they
    # are correlated - same symbol, same way - so an adverse move loses on all
    # of them together. One XAUUSD lot is 100oz, so $1 of price is $1 per 0.01
    # lot: a $6.00 stop risks $6 per trade and $24 across four positions. A
    # simultaneous buy and sell pays the spread twice and nets to nothing on a
    # netting account, so an opposing signal is skipped while a position is open.
    allow_opposite_positions: bool = False
    # Daily frame. Keep the target and the loss cap proportionate: a 0.5%
    # target against a 3% cap needs 86% of days to be winners just to break
    # even; at 2:1 it needs 67%.
    max_daily_loss_pct: float = 1.0
    use_daily_target: bool = False      # hard stop at the target; off by default
    daily_target_pct: float = 0.5
    close_on_target: bool = True        # bank the day rather than leave it floating

    # Profit lock: unlike the hard target this does NOT stop a winning day. It
    # arms once the day peaks above lock_after_pct and halts only if that peak
    # gain is given back by give_back_pct, so upside stays open while a day
    # that ran up cannot round-trip to the loss cap.
    lock_daily_gains: bool = True
    lock_after_pct: float = 0.5
    give_back_pct: float = 50.0
    max_spread_points: int = 350        # always in broker points
    use_session_filter: bool = True
    # Session hours are expressed in GMT + this offset, NOT broker server time,
    # so the window means the same wall-clock hours whatever offset the broker
    # runs on and whether or not it observes DST. Oman (GST) is GMT+4, no DST.
    session_gmt_offset: float = 4.0
    session_start_hour: int = 6         # 06:00 Oman = 02:00 GMT
    session_end_hour: int = 23          # 23:00 Oman = 19:00 GMT
    close_before_weekend: bool = True
    weekend_close_hour: int = 20        # Friday, session zone

    # Close-before-market-close. The MetaTrader5 Python API exposes no session
    # schedule (the MQL5 EA reads the broker's real hours via
    # SymbolInfoSessionTrade; Python cannot), so these work off the configured
    # window end instead.
    entry_open_buffer_min: int = 5      # no entries for N min after the window opens
    entry_close_buffer_min: int = 30    # no entries in the last N min
    flatten_before_close: bool = True   # close everything before the window ends
    flatten_before_close_min: int = 10  # how many minutes before

    # ---------------- execution ----------------
    magic: int = 20260908
    deviation_points: int = 30          # max slippage
    comment: str = "XAUUSD-Confluence-Py"
    poll_seconds: float = 5.0           # trailing-stop / position management cadence
    # Entries are evaluated once per CLOSED working-timeframe bar (M5 by
    # default), so this cadence only governs trailing-stop upkeep.

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

    def session_now(self) -> "datetime":
        """Current time in the zone the session hours are expressed in."""
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) + timedelta(hours=self.session_gmt_offset)

    def minutes_to_close(self, when=None) -> float:
        """Minutes until the session window ends (inf when the filter is off)."""
        if not self.use_session_filter:
            return float("inf")
        t = when if when is not None else self.session_now()
        minutes = t.hour * 60 + t.minute + t.second / 60.0
        end = self.session_end_hour * 60 + self.session_end_min_or_zero()
        delta = end - minutes
        if delta < 0:
            delta += 24 * 60          # window ends after midnight
        return delta

    def session_end_min_or_zero(self) -> int:
        return 0

    def can_enter(self, when=None) -> bool:
        """In session AND clear of the open/close buffers."""
        if not self.in_session(when):
            return False
        t = when if when is not None else self.session_now()
        minutes = t.hour * 60 + t.minute
        start = self.session_start_hour * 60
        since_open = minutes - start
        if since_open < 0:
            since_open += 24 * 60
        if since_open < self.entry_open_buffer_min:
            return False
        return self.minutes_to_close(when) > self.entry_close_buffer_min

    def in_session(self, when=None) -> bool:
        """Is `when` (default: now) inside the configured session window?"""
        if not self.use_session_filter:
            return True
        t = when if when is not None else self.session_now()
        minutes = t.hour * 60 + t.minute
        start = self.session_start_hour * 60
        end = self.session_end_hour * 60
        if start == end:
            return True
        if start < end:
            return start <= minutes < end
        return minutes >= start or minutes < end      # wraps midnight

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

#!/usr/bin/env python3
"""
Backtests this solution against historical XAUUSD bars: walks forward one
primary-timeframe candle at a time, builds the exact same feature snapshot
main.py would (market_intel.build_feature_snapshot), asks Claude to
validate the confluences exactly as live trading does (claude_advisor.
get_verdict), gates and "opens" trades through the real executor.gate()/
executor.execute() code path, and simulates each position's exit (SL / TP /
the $3 arm-then-trail, mirroring UnifiedTrader_EA.mq5's logic) against
the bars that follow. Reports a trade log and summary stats.

WHY THIS EXISTS: the docs/REFERENCE.md's own "Honest limitations" section says nothing
here has been backtested. This closes that gap - genuinely, not with a
mechanical stand-in, by calling the real Claude API at every historical
decision point by default (see --mechanical below for the free alternative).

THE MOST IMPORTANT PROPERTY OF ANY BACKTEST: no lookahead. HistoricalGateway
below only ever exposes data that would actually have been known at the
simulated "now" - a higher-timeframe bar isn't visible until its own close
time has passed relative to the primary-timeframe bar just evaluated, not
just its open time. Get this wrong and every other number in this file is
worthless (silently over-optimistic). See HistoricalGateway.get_bars().

    COST WARNING: by default this calls the real Claude API once per
    evaluated bar. A 3-month M15 backtest is roughly 8,000 candles - even
    filtered to trading hours that's thousands of real API calls. main()
    prints an estimate and asks for confirmation before spending anything;
    pass --mechanical to test the engine itself for free first (a
    non-LLM stand-in applying the exact same pass/confirm rules Claude is
    told to use - NOT a backtest of Claude's judgment, just of the plumbing).

Usage:
    # Free dry run of the engine/exit-simulation plumbing, no API key needed
    python backtest.py --bars-csv m15.csv --trend-csv h4.csv \\
        --daily-csv d1.csv --weekly-csv w1.csv --mechanical

    # Real backtest - calls the real Claude API, costs money
    python backtest.py --bars-csv m15.csv --trend-csv h4.csv \\
        --daily-csv d1.csv --weekly-csv w1.csv --yes

    # Pull history straight from a running MT5 terminal instead of CSVs
    python backtest.py --from-mt5 --start 2026-01-01 --end 2026-04-01 --yes

CSV format for --bars-csv/--trend-csv/--daily-csv/--weekly-csv: columns
time,open,high,low,close,volume - time as anything pandas can parse, one row
per CLOSED historical bar (ascending, no still-forming bar).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import logging
import os
from dataclasses import dataclass

import pandas as pd

import claude_advisor
import executor
import keys
import market_intel
import mt5_gateway as gw
import paths
import profiles
import tactics
import xtr_logic
from config import AdvisorConfig

log = logging.getLogger("backtest")

TIMEFRAME_DURATIONS = {
    "M1": pd.Timedelta(minutes=1), "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15), "M30": pd.Timedelta(minutes=30),
    "H1": pd.Timedelta(hours=1), "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1), "W1": pd.Timedelta(weeks=1),
}

AUX_WARMUP_BARS = 5   # D1/W1 history needed before the first evaluated bar
ROLLOVER_START, ROLLOVER_END = 16 * 60 + 55, 18 * 60 + 15   # daily reopen, New York minutes

TRADE_FIELDS = ["entry_time", "exit_time", "direction", "lots", "entry_price",
                "exit_price", "sl", "tp", "exit_reason", "pnl_dollars"]


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


class _FakeTick:
    def __init__(self, bid: float, ask: float):
        self.bid, self.ask = bid, ask


class _FakeOrderResult:
    def __init__(self, retcode, order, price):
        self.retcode, self.order, self.price = retcode, order, price


@dataclass
class SimPosition:
    ticket: int
    direction: str
    lots: float
    entry_time: pd.Timestamp
    entry_price: float
    sl: float
    tp: float | None
    armed: bool = False
    risk: float = 0.0        # entry-to-opening-stop distance (1R) - lock_mode="r" (BTC)


@dataclass
class ClosedTrade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    lots: float
    entry_price: float
    exit_price: float
    sl: float
    tp: float | None
    exit_reason: str
    pnl_dollars: float
    ticket: int = 0


class HistoricalGateway:
    """Replays pre-loaded historical bars and acts as a paper broker - same
    combined "market data + order placement" shape as the real mt5_gateway
    module, so executor.gate()/executor.execute() work against this
    unmodified. Two concerns live here deliberately together, same as the
    real gateway: (1) market-data replay (get_bars/get_tick/advance - see
    the no-lookahead note in the module docstring) and (2) paper-broker
    bookkeeping (place_market_order/count_same_direction/manage_positions -
    manage_positions is backtest-only; nothing else in this codebase calls
    it, since live trading's equivalent is UnifiedTrader_EA.mq5
    running inside MT5, not Python).
    """
    def __init__(self, symbol: str, bars: dict, spec, spread_points: int = 25,
                 starting_equity: float = 10000.0, rollover_spread_points: int = 0):
        """bars: {"M15": df, "H4": df, "D1": df, "W1": df, ...} - each a
        DataFrame of time(tz-aware ascending)/open/high/low/close/volume,
        every row a genuinely CLOSED historical bar. starting_equity backs
        account_equity() below - only used if a config passed through here
        sets use_risk_percent (see executor.position_size()); ignored
        otherwise, same as the real gateway's own equity call.
        """
        self.symbol = symbol
        self.bars = {tf: df.sort_values("time").reset_index(drop=True) for tf, df in bars.items()}
        self.spec = spec
        self.spread_points = spread_points
        self.rollover_spread_points = rollover_spread_points
        self.starting_equity = starting_equity
        self.primary_timeframe = None
        self.cursor = 0
        self.sim_positions: list = []
        self.closed_trades: list = []
        self._next_ticket = 1

    def reset(self, primary_timeframe: str, warmup_bars: int) -> bool:
        """Positions the cursor at the first primary-timeframe bar for which
        EVERY timeframe already has at least `warmup_bars` of closed history
        - so the very first evaluated snapshot isn't starved of context (a
        fresh EMA200, for instance, is meaningless with 5 bars behind it).
        Returns False if there isn't enough history to start at all.
        """
        self.primary_timeframe = primary_timeframe
        min_start_time = None
        for tf, df in self.bars.items():
            need = min(warmup_bars, AUX_WARMUP_BARS) if tf in ("D1", "W1") else warmup_bars
            if len(df) <= need:
                return False
            candidate = df["time"].iloc[need]
            if min_start_time is None or candidate > min_start_time:
                min_start_time = candidate
        # D1/W1 only ever feed market_intel.daily_weekly_levels() (the last
        # 2 closed bars), so demanding `warmup_bars` of them (300 weeks = ~6
        # years) would refuse any short CSV or --from-mt5 range for no reason.
        primary_times = self.bars[primary_timeframe]["time"]
        idx = int(primary_times.searchsorted(min_start_time, side="left"))
        if idx >= len(primary_times):
            return False
        self.cursor = idx
        return True

    @property
    def current_time(self) -> pd.Timestamp:
        """The CLOSE time of the primary-timeframe bar just evaluated - the
        simulated "now". Computed as open + duration rather than read off a
        next row, so it's well-defined even for the very last bar.
        """
        bar_open = self.bars[self.primary_timeframe]["time"].iloc[self.cursor]
        return bar_open + TIMEFRAME_DURATIONS[self.primary_timeframe]

    def spread_at(self, ts: pd.Timestamp) -> float:
        """Spread (price units) at bar time `ts`: the constant spread, widened
        to rollover_spread_points around the daily reopen (16:55-18:15 New
        York), where real gold spreads are several times wider."""
        points = self.spread_points
        if self.rollover_spread_points:
            ny = ts.tz_convert(tactics.NEW_YORK) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(
                tactics.NEW_YORK)
            minute = ny.hour * 60 + ny.minute
            if ROLLOVER_START <= minute < ROLLOVER_END:
                points = max(points, self.rollover_spread_points)
        return self.spec.point * points

    @property
    def current_bar(self) -> pd.Series:
        return self.bars[self.primary_timeframe].iloc[self.cursor]

    def now(self) -> pd.Timestamp:
        """The simulated replay clock (self.current_time) - executor.gate()
        calls this (via `gateway.now()`) instead of the real wall clock, so
        config.py's news_blackout_windows is judged against the bar being
        evaluated rather than whatever real date the backtest happens to
        run on (mirrors mt5_gateway.now()'s own docstring/reasoning).
        """
        return self.current_time

    def market_closed_now(self) -> bool:
        """True when the next primary bar does not start right after the one
        just evaluated - the daily break or the weekend. A live market order
        sent then is refused (market closed); the replay must not fill it at
        the reopen price instead."""
        primary = self.bars[self.primary_timeframe]
        next_idx = self.cursor + 1
        return next_idx < len(primary) and primary["time"].iloc[next_idx] > self.current_time

    def advance(self) -> bool:
        """Moves to the next primary-timeframe bar. Returns False once the
        data is exhausted (backtest is over)."""
        self.cursor += 1
        return self.cursor < len(self.bars[self.primary_timeframe])

    # --- market data (no-lookahead) ------------------------------------- #
    def get_bars(self, symbol: str, timeframe_name: str, count: int) -> pd.DataFrame:
        if symbol != self.symbol:
            # This gateway only ever holds ONE instrument's bars (self.bars,
            # preloaded for self.symbol) - silently returning self.symbol's
            # data for a different symbol (e.g. market_intel.dxy_context()
            # asking for cfg.dxy_symbol) would look like real DXY history
            # but actually just be XAUUSD's own price series mislabeled.
            # Fail loudly instead: a backtest run genuinely can't answer for
            # a second instrument without also being handed its bars.
            raise ValueError(f"HistoricalGateway only has bars for {self.symbol!r}, not {symbol!r} "
                              "- this backtest replay only ever loaded one instrument's history.")
        df = self.bars[timeframe_name]
        duration = TIMEFRAME_DURATIONS[timeframe_name]
        closed = df[df["time"] + duration <= self.current_time].tail(count - 1)
        if len(closed) == 0:
            raise RuntimeError(f"no closed {timeframe_name} history before {self.current_time} yet "
                               "- reset()'s warmup_bars should have prevented this")
        # A duplicate of the last closed bar stands in for the still-forming
        # bar every caller here expects as the final row and always drops
        # (see mt5_gateway.get_bars's own docstring) - never read, only its
        # presence/position matters.
        return pd.concat([closed, closed.tail(1)], ignore_index=True)

    def get_tick(self, symbol: str):
        # The first price this system could actually have traded at: the
        # OPEN of the bar immediately after the one just evaluated - never
        # that bar's own close, which isn't confirmed/tradeable until the
        # decision is already made on it.
        primary = self.bars[self.primary_timeframe]
        next_idx = self.cursor + 1
        if next_idx < len(primary):
            price = float(primary["open"].iloc[next_idx])
            fill_time = primary["time"].iloc[next_idx]
        else:
            price = float(primary["close"].iloc[self.cursor])
            fill_time = self.current_time
        # Bars are BID prices (as MT5's are): bid = the bar, ask = bar +
        # spread - the same convention manage_positions() prices exits with,
        # so a buy and a sell each pay exactly one spread per round trip.
        spread = self.spread_at(fill_time)
        return _FakeTick(bid=price, ask=price + spread)

    def symbol_spec(self, symbol: str):
        return self.spec

    def price_distance_for_dollars(self, spec, dollars: float, lots: float) -> float:
        return gw.price_distance_for_dollars(spec, dollars, lots)

    # --- paper broker ----------------------------------------------------- #
    def count_same_direction(self, symbol: str, magic: int, direction: str, additional_magics=()) -> int:
        # additional_magics accepted only for interface parity with the real
        # mt5_gateway.count_same_direction() - a backtest run only ever
        # simulates one system's own position book (self.sim_positions),
        # so there is nothing else to count regardless of what's passed here.
        return sum(1 for p in self.sim_positions if p.direction == direction)

    def symbol_positions(self, symbol: str) -> list[dict]:
        """All positions on the symbol - in a backtest, only this system's."""
        return self.open_positions(symbol, 0)

    def pending_orders(self, symbol: str) -> list[dict]:
        """A backtest only ever places market orders - never a pending one."""
        return []

    def open_positions(self, symbol: str, magic: int) -> list[dict]:
        """Mirrors mt5_gateway.open_positions()'s shape - magic accepted
        only for interface parity, same reasoning as count_same_direction()
        above: a backtest run only ever simulates ONE system's own position
        book (self.sim_positions), so there's nothing else to report
        regardless of which magic is asked for. Needed so market_intel.
        consensus_context() (consensus_magic_numbers) works against this
        gateway instead of raising AttributeError.
        """
        return [{"ticket": p.ticket, "direction": p.direction, "volume": p.lots,
                 "price_open": p.entry_price, "sl": p.sl, "tp": p.tp}
                for p in self.sim_positions]

    def recent_closed_trades(self, symbol: str, magic: int, count: int,
                              lookback_days: int = 14) -> list[dict]:
        """Mirrors mt5_gateway.recent_closed_trades()'s shape from this
        backtest's OWN simulated trade history so far (self.closed_trades) -
        magic/symbol accepted only for interface parity, same reasoning as
        count_same_direction() above: a backtest run only ever has one
        system's own trades to look back on. lookback_days is accepted for
        interface parity too but not applied - a backtest's "recent past"
        is inherently bounded by what has closed so far in the simulation,
        there's no separate real-calendar window to filter against. No
        lookahead risk: by the time market_intel.build_feature_snapshot()
        calls this for bar N, self.closed_trades only contains trades that
        closed strictly before bar N (manage_positions() runs on each bar
        as it closes, before the next evaluation), so this is exactly the
        information a live run would have had at that same point in time too.
        """
        out = [{"direction": t.direction, "pnl_dollars": t.pnl_dollars, "ticket": t.ticket,
                "time": t.exit_time} for t in self.closed_trades]
        out.reverse()  # closed_trades is oldest-first; recent_closed_trades() is newest-first
        return out[:count]

    def account_equity(self) -> float:
        """starting_equity plus every realized P&L so far, PLUS floating P&L
        on still-open sim_positions - real MT5's ACCOUNT_EQUITY (what the
        real mt5_gateway.account_equity() reads) includes unrealized P&L
        too, and BacktestDayState's daily loss breaker needs to see a
        position that's deep underwater but hasn't hit its SL yet the same
        way live trading's breaker would, or it stays unrealistically
        optimistic about a bad day until positions actually close. Same
        no-lookahead property as recent_closed_trades() above: closed_trades
        only ever contains trades closed strictly before "now", and the
        floating leg is priced off get_tick() - the same one-bar-ahead,
        never-the-evaluated-bar's-own-close price used everywhere else.
        """
        realized = sum(t.pnl_dollars for t in self.closed_trades)
        floating = 0.0
        if self.sim_positions:
            tick = self.get_tick(self.symbol)
            for p in self.sim_positions:
                current_price = tick.bid if p.direction == "buy" else tick.ask
                sign = 1.0 if p.direction == "buy" else -1.0
                price_move = sign * (current_price - p.entry_price)
                floating += price_move / self.spec.tick_size * self.spec.tick_value * p.lots
        return self.starting_equity + realized + floating

    def place_market_order(self, spec, direction: str, lots: float, sl_price: float, tp_price: float,
                            magic: int, comment: str, deviation_points: int, dry_run: bool):
        tick = self.get_tick(spec.name)
        price = tick.ask if direction == "buy" else tick.bid
        ticket = self._next_ticket
        self._next_ticket += 1
        self.sim_positions.append(SimPosition(
            ticket=ticket, direction=direction, lots=lots, entry_time=self.current_time,
            entry_price=price, sl=sl_price, tp=tp_price, risk=abs(price - sl_price) if sl_price else 0.0,
        ))
        return _FakeOrderResult(retcode=10009, order=ticket, price=price)

    def manage_positions(self, cfg: AdvisorConfig) -> None:
        """The backtest's stand-in for the live trade-management EA: checks
        every open simulated position against the bar that JUST closed
        (self.current_bar) for a stop-out or take-profit, and arms/tightens
        the stop for FUTURE bars per cfg.exit_style - see
        _manage_fixed_tp/_manage_sl_to_tp1 for each style's own logic.

        A position can be tested against the SAME bar it just opened on
        (entry and exit inside one bar is realistic), but a trail that arms
        on bar N is only tested for a stop-out starting bar N+1 - conflating
        "armed" and "hit by its own new stop" within the identical bar would
        need genuine intrabar (tick-level) sequencing this bar-level
        simulation doesn't have. This is a documented simplification, not a
        bug - see the docs/REFERENCE.md's backtest section.
        """
        bar = self.current_bar
        high, low = float(bar["high"]), float(bar["low"])
        min_stop_dist = self.spec.stops_level_points * self.spec.point
        if cfg.exit_style == "fixed_tp":
            manage_one = self._manage_fixed_tp
        elif cfg.exit_style == "sl_to_tp1":
            manage_one = self._manage_sl_to_tp1
        else:
            # No backtest simulation of "breakeven_r_decay" exists yet (it's
            # live-only, implemented in the MQL5 EAs - see config.py's
            # module docstring) - fail loudly rather than silently running
            # it through _manage_sl_to_tp1, which doesn't have its earlier
            # breakeven-at-ATR/decay-window step and would misrepresent it.
            raise ValueError(
                f"backtest.py has no simulation for exit_style={cfg.exit_style!r} yet - only "
                f"'sl_to_tp1' and 'fixed_tp' are supported here.")
        bar_open = float(bar["open"])
        spread = self.spread_at(bar["time"])
        still_open = []
        for pos in self.sim_positions:
            # Bars are bid prices. A sell is closed at the ASK, so its stop,
            # lock and trail fire on the ask-side bar (bid + spread) - exactly
            # like MT5 - rather than 25 points late on the bid.
            if pos.direction == "sell":
                h, l, o = high + spread, low + spread, bar_open + spread
            else:
                h, l, o = high, low, bar_open
            stop_before = pos.sl
            exit_price, exit_reason = manage_one(cfg, pos, h, l, min_stop_dist)
            if exit_price is None:
                still_open.append(pos)
                continue
            # A bar that OPENS through the stop (daily break, weekend, news
            # gap) fills at that worse open, not at the stop price.
            if exit_reason in ("sl", "trail"):
                if pos.direction == "buy" and o < stop_before:
                    exit_price = o
                elif pos.direction == "sell" and o > stop_before:
                    exit_price = o
            self._close_position(pos, exit_price, exit_reason, self.current_time)
        self.sim_positions = still_open

    def _manage_fixed_tp(self, cfg: AdvisorConfig, pos: SimPosition, high: float, low: float,
                          min_stop_dist: float):
        """The original design: a real fixed take-profit at entry+tp1_dist,
        which is the SAME price the trail arms at - so the standing TP
        almost always wins that race and the trail rarely gets a real
        chance to engage. Kept only so --compare has the old behavior to
        measure exit_style=sl_to_tp1 against; not used live.
        """
        # At the reference lot (cfg.reference_lot), exactly like the live MQL5
        # managers - see _manage_sl_to_tp1's own note.
        arm_dist = self.price_distance_for_dollars(self.spec, cfg.tp1_dollars, cfg.reference_lot)
        trail_dist = self.price_distance_for_dollars(self.spec, cfg.trail_dollars, cfg.reference_lot)
        if pos.direction == "buy":
            if low <= pos.sl:
                return pos.sl, "trail" if pos.armed else "sl"
            if pos.tp is not None and high >= pos.tp:
                return pos.tp, "tp"
            profit_at_high = high - pos.entry_price
            if profit_at_high >= arm_dist:
                candidate = high - trail_dist
                if candidate > pos.sl and (high - candidate) >= min_stop_dist:
                    pos.sl, pos.tp, pos.armed = candidate, None, True
        else:
            if high >= pos.sl:
                return pos.sl, "trail" if pos.armed else "sl"
            if pos.tp is not None and low <= pos.tp:
                return pos.tp, "tp"
            profit_at_low = pos.entry_price - low
            if profit_at_low >= arm_dist:
                candidate = low + trail_dist
                if candidate < pos.sl and (candidate - low) >= min_stop_dist:
                    pos.sl, pos.tp, pos.armed = candidate, None, True
        return None, None

    def _manage_sl_to_tp1(self, cfg: AdvisorConfig, pos: SimPosition, high: float, low: float,
                           min_stop_dist: float):
        """The recommended design (matches UnifiedTrader_EA.mq5's
        live logic): no broker take-profit exists on this position at all
        (see executor.py - place_market_order was called with tp=0 under
        this style), so the ONLY exit mechanism is the stop-loss. Once
        floating profit reaches tp1_dist, the SL is moved to EXACTLY that
        price - locking in tp1_dollars of profit, no more, no less, in one
        deterministic step - rather than jumping straight to a trailing
        level that depends on how far price had already run past the arm
        point by the time this check fires. Only on LATER bars does the SL
        continue trailing trail_dist behind new highs/lows.
        """
        # TP1/trail are dollars at the REFERENCE lot (cfg.reference_lot), i.e.
        # fixed price distances - the same way the entry SL is sized and the
        # live MQL5 managers convert them. Converting at pos.lots would
        # shrink them as a risk-sized lot grows (risking ~$200 to lock ~$6).
        if cfg.lock_mode == "r" and pos.risk > 0:
            # BTCTrader_EA: lock at +lock_r x the trade's own stop, trail trail_r x it
            tp1_dist, trail_dist = pos.risk * cfg.lock_r, pos.risk * cfg.trail_r
        else:
            tp1_dist = self.price_distance_for_dollars(self.spec, cfg.tp1_dollars, cfg.reference_lot)
            trail_dist = self.price_distance_for_dollars(self.spec, cfg.trail_dollars, cfg.reference_lot)
        if pos.direction == "buy":
            if low <= pos.sl:
                return pos.sl, "trail" if pos.armed else "sl"
            if not pos.armed:
                # The lock level is fixed (entry+tp1_dist), but placing it
                # still needs enough room from the CURRENT price to satisfy
                # the broker's own minimum stop distance - same check the
                # trailing step below already applies, just against a fixed
                # target instead of a moving one. If price has only just
                # touched tp1_dist, arming waits for it to move a little
                # further before the lock can actually be placed.
                candidate = pos.entry_price + tp1_dist
                if high >= candidate and (high - candidate) >= min_stop_dist:
                    pos.sl, pos.armed = candidate, True
            else:
                candidate = high - trail_dist
                if candidate > pos.sl and (high - candidate) >= min_stop_dist:
                    pos.sl = candidate
        else:
            if high >= pos.sl:
                return pos.sl, "trail" if pos.armed else "sl"
            if not pos.armed:
                candidate = pos.entry_price - tp1_dist
                if low <= candidate and (candidate - low) >= min_stop_dist:
                    pos.sl, pos.armed = candidate, True
            else:
                candidate = low + trail_dist
                if candidate < pos.sl and (candidate - low) >= min_stop_dist:
                    pos.sl = candidate
        return None, None

    def close_all_at_market(self, reason: str = "backtest_end") -> None:
        """Marks every still-open position closed at the last known close
        price - called once after the backtest loop ends so open trades
        still count toward the summary instead of vanishing from the stats.
        Reads the primary timeframe's LAST row directly rather than through
        self.cursor/current_time, since by the time this runs the final
        advance() has already pushed the cursor one past the end (that's
        what signals "no more bars" to the caller) - going through the
        normal cursor-based accessors here would index out of bounds.
        """
        if not self.sim_positions:
            return
        primary = self.bars[self.primary_timeframe]
        last_close = float(primary["close"].iloc[-1])
        last_time = primary["time"].iloc[-1] + TIMEFRAME_DURATIONS[self.primary_timeframe]
        for pos in self.sim_positions:
            self._close_position(pos, last_close, reason, last_time)
        self.sim_positions = []

    def _close_position(self, pos: SimPosition, exit_price: float, reason: str,
                         exit_time: pd.Timestamp) -> None:
        sign = 1.0 if pos.direction == "buy" else -1.0
        price_move = sign * (exit_price - pos.entry_price)
        pnl = price_move / self.spec.tick_size * self.spec.tick_value * pos.lots
        self.closed_trades.append(ClosedTrade(
            entry_time=pos.entry_time, exit_time=exit_time, direction=pos.direction, lots=pos.lots,
            entry_price=pos.entry_price, exit_price=exit_price, sl=pos.sl, tp=pos.tp,
            exit_reason=reason, pnl_dollars=round(pnl, 2), ticket=pos.ticket,
        ))


def load_bars_csv(path: str) -> pd.DataFrame:
    """time (or the Drive price export's `datetime`), open, high, low, close
    [, volume] - bar OPEN times in UTC. A missing volume column reads as 0."""
    df = pd.read_csv(path)
    if "time" not in df.columns and "datetime" in df.columns:
        df = df.rename(columns={"datetime": "time"})
    if "volume" not in df.columns:
        df["volume"] = df["tick_volume"] if "tick_volume" in df.columns else 0
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


RESAMPLE_RULES = {"H4": "4h", "D1": "1D", "W1": "7D"}
WEEK_ORIGIN = pd.Timestamp("2023-01-01", tz="UTC")      # a Sunday: weeks open Sunday 00:00 UTC


def resample_bars(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """H4 / D1 / W1 bars built from finer ones (UTC boundaries; weeks from
    Sunday). Periods without any source bar (a closed market) are dropped."""
    origin = WEEK_ORIGIN if tf == "W1" else "epoch"
    out = (df.set_index("time")
             .resample(RESAMPLE_RULES[tf], origin=origin, label="left", closed="left")
             .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
             .dropna(subset=["open"]).reset_index())
    return out


def load_csv_folder(folder: str, prefix: str, primary_tf: str, trend_tf: str) -> dict:
    """Every <prefix><TF>.csv in `folder` (M5, M15, H1, H4, D1, W1) - the
    Google Drive price files: the EAs' export (XAUUSD_M15.csv, BTCUSD_M15.csv,
    datetime column) or backtest --to-drive's (BTC_prices_M15.csv). A missing
    H4 / D1 / W1 is built from H1 (or from the primary timeframe)."""
    bars = {}
    for tf in ("M5", "M15", "H1", "H4", "D1", "W1"):
        path = os.path.join(folder, f"{prefix}{tf}.csv")
        if os.path.exists(path):
            bars[tf] = load_bars_csv(path)
    if primary_tf not in bars:
        raise FileNotFoundError(f"{os.path.join(folder, prefix + primary_tf + '.csv')} not found")
    source = bars.get("H1", bars[primary_tf])
    for tf in (trend_tf, "D1", "W1"):
        if tf not in bars:
            if tf not in RESAMPLE_RULES:
                raise FileNotFoundError(f"{prefix}{tf}.csv not found in {folder}")
            bars[tf] = resample_bars(source, tf)
            log.info("%s: built from %s (%d bars)", tf, "H1" if "H1" in bars else primary_tf, len(bars[tf]))
    return bars


def mechanical_verdict(features: dict):
    """A free, non-LLM stand-in for claude_advisor.get_verdict() that applies
    the EXACT pass/confirm thresholds documented in claude_advisor.
    SYSTEM_PROMPT mechanically, for testing the backtest engine's plumbing
    (data replay, gating, exit simulation) without spending on the real API.
    This is NOT a backtest of Claude's judgment - it can't weigh the SMC
    context the way the prompt asks Claude to, it only votes on the three
    mechanical legs. Treat its results as an engine smoke test, never as a
    performance claim for the real system.
    """
    from claude_advisor import ConfluenceLeg, ConfluenceVerdict

    legs3 = tactics.vote_legs(features)
    trend_dir, trend_confirmed = legs3["trend"]
    momentum_dir, momentum_confirmed = legs3["momentum"]
    strength_dir, strength_confirmed = legs3["strength"]

    legs = {"buy": 0, "sell": 0}
    confirmed = {"buy": 0, "sell": 0}
    for d, c in ((trend_dir, trend_confirmed), (momentum_dir, momentum_confirmed),
                (strength_dir, strength_confirmed)):
        if d in legs:
            legs[d] += 1
            if c:
                confirmed[d] += 1

    direction = max(legs, key=legs.get) if max(legs.values()) > 0 else "none"
    confluence_count = legs.get(direction, 0)
    has_confirmed = confirmed.get(direction, 0) >= 1
    smc = features["smc"]
    sweep_ok = smc["liquidity_sweep"]["direction"] in (direction, None)
    zone_ok = (direction == "buy" and smc["premium_discount"]["zone"] != "premium") or \
              (direction == "sell" and smc["premium_discount"]["zone"] != "discount")
    if confluence_count >= 2 and has_confirmed and sweep_ok and zone_ok:
        conviction = "full"
    elif confluence_count >= 2:
        conviction = "partial"
    else:
        conviction = "none"

    def leg(d, c):
        return ConfluenceLeg(direction=d if d in ("buy", "sell") else "neutral",
                             passes=d == direction, confirmed=c and d == direction,
                             note="mechanical")

    return ConfluenceVerdict(
        trend=leg(trend_dir, trend_confirmed), momentum=leg(momentum_dir, momentum_confirmed),
        strength=leg(strength_dir, strength_confirmed), confluence_count=confluence_count,
        direction=direction if direction in ("buy", "sell") else "none", conviction=conviction,
        smc_alignment="mechanical - not a real SMC read", reasoning="mechanical stand-in, no LLM call",
    )


def estimate_call_count(gateway: HistoricalGateway) -> int:
    return len(gateway.bars[gateway.primary_timeframe]) - gateway.cursor


class BacktestDayState:
    """Local equivalent of main.DayRoll's daily-loss/target tracking,
    scoped to a single backtest run/gateway - without this,
    config.py's max_daily_loss_pct/use_daily_target would silently never
    engage during a backtest (unlike use_risk_percent/sl_mode=atr/
    dxy_symbol/consensus_magic_numbers, which HistoricalGateway already
    supports), making a backtest of a config that relies on the breaker
    unrealistically optimistic about how much it would actually trade.
    """
    def __init__(self):
        self.date = None
        self.start_equity = 0.0
        self.loss_hit = False
        self.target_hit = False

    def block_reason(self, gateway: HistoricalGateway, cfg: AdvisorConfig) -> str:
        day = tactics.trading_day(gateway.current_time)   # the live trading day (17:00 New York)
        equity = gateway.account_equity()
        if day != self.date:
            self.date = day
            self.start_equity = equity
            self.loss_hit = False
            self.target_hit = False
        if self.start_equity > 0:
            move_pct = (equity - self.start_equity) / self.start_equity * 100.0
            if cfg.max_daily_loss_pct > 0 and not self.loss_hit and -move_pct >= cfg.max_daily_loss_pct:
                self.loss_hit = True
            if cfg.use_daily_target and not self.target_hit and move_pct >= cfg.daily_target_pct:
                self.target_hit = True
        if self.loss_hit:
            return "daily loss circuit breaker triggered"
        if self.target_hit:
            return "daily profit target already reached"
        return ""


def run_backtest(gateway: HistoricalGateway, cfg: AdvisorConfig, client, mechanical: bool) -> dict:
    """Single-variant run - the same loop as run_backtest_compare() with one
    entry, so both paths share one implementation (XTR gate included)."""
    return run_backtest_compare({"run": gateway}, {"run": cfg}, client, mechanical)


def _xtr_reading(gateway: HistoricalGateway, cfg: AdvisorConfig):
    """xtr_logic.assess() on the replay (closed bars only) - None when the
    M5/H1 history isn't loaded or is still too short, mirroring main.
    xtr_assessment(): a missing reading skips the gate, never blocks."""
    try:
        return xtr_logic.assess(gateway, cfg.symbol, cfg.xtr_bars)
    except (KeyError, RuntimeError, ValueError, IndexError):
        return None


def run_backtest_compare(gateways: dict, cfgs: dict, client, mechanical: bool) -> None:
    """Runs both exit styles side by side against IDENTICAL market data and
    an IDENTICAL sequence of Claude verdicts - one call to build_feature_
    snapshot/get_verdict per bar, not one per style, since which confluence
    verdict Claude returns for a given bar depends only on market data, not
    on how positions get exited. Only executor.gate()/execute() run once per
    style, because the two styles' own position books (and therefore their
    same-direction caps) can genuinely diverge once positions start closing
    at different times - so which signals each style actually accepts is not
    guaranteed to stay identical, only the underlying verdict stream is.

    `gateways` must all wrap the SAME underlying bars (each its own
    HistoricalGateway instance, reset() with the same warmup) so their
    cursors stay in lockstep - advance() is called on every one of them
    every iteration via a list comprehension, not `all(g.advance() for ...)`,
    specifically because `all()` over a generator short-circuits on the
    first False and would silently skip advancing the remaining gateways.
    """
    styles = list(gateways.keys())
    primary_gw = gateways[styles[0]]
    shared_cfg = cfgs[styles[0]]
    day_trades = {s: {} for s in styles}
    day_states = {s: BacktestDayState() for s in styles}
    # Per-variant XTR stand-down memory (sec. 8), in memory only - each
    # variant's own closed trades teach it, exactly like the live state file.
    xtr_states = {s: xtr_logic.XtrStanddown(None) for s in styles}
    xtr_blocks = {s: 0 for s in styles}
    # The XTR reading is Claude's context whatever the gate (live does the same).
    xtr_on = all(tf in primary_gw.bars for tf in ("M5", "H1"))
    evaluated = 0
    while True:
        for s in styles:
            gateways[s].manage_positions(cfgs[s])
        try:
            features = market_intel.build_feature_snapshot(primary_gw, shared_cfg)
        except RuntimeError:
            if not all([gateways[s].advance() for s in styles]):
                break
            continue

        xtr_a = _xtr_reading(primary_gw, shared_cfg) if xtr_on else None
        features["xtr"] = xtr_logic.snapshot_context(xtr_a, shared_cfg.xtr_gate)
        if mechanical:
            verdict = mechanical_verdict(features)
        elif executor.verdict_independent_block(primary_gw, shared_cfg, 0):
            # Outside the trading hours / news blackout: live makes no call either.
            verdict = claude_advisor.neutral_verdict(executor.verdict_independent_block(primary_gw, shared_cfg, 0))
        elif tactics.prescreen_block(shared_cfg, features):
            # Same saving as live: no Claude call when no trade is possible.
            verdict = claude_advisor.neutral_verdict(tactics.prescreen_block(shared_cfg, features))
        else:
            verdict = claude_advisor.get_verdict(client, shared_cfg, features)
        evaluated += 1

        for s in styles:
            g = gateways[s]
            cfg = tactics.effective_limits(cfgs[s], g.current_time)    # BTC's weekend allowance
            day = tactics.trading_day(g.current_time)
            trades_today = day_trades[s].get(day, 0)
            block_reason = (day_states[s].block_reason(g, cfg) or tactics.regime_block(cfg, features)
                            or ("market closed (daily break / weekend)" if g.market_closed_now() else ""))
            # Same order as main.run_once(): learn from closed trades, lift a
            # due stand-down, grade Claude's direction, then execute() -
            # which only ever REJECTS on xtr.block_reason (lot/SL/TP untouched).
            xtr_decision = None
            if xtr_a is not None and cfg.xtr_gate != "off" and verdict.direction in ("buy", "sell"):
                st = xtr_states[s]
                st.update_from_closed(g.recent_closed_trades(cfg.symbol, cfg.magic, count=50))
                st.release_if_due(xtr_a)
                xtr_decision = xtr_logic.evaluate(verdict.direction, xtr_a, cfg, st)
            executor.set_log_clock(g.now)
            try:
                decision = executor.execute(g, cfg, verdict, g.spec, trades_today, block_reason,
                                            day_start_equity=day_states[s].start_equity,
                                            xtr=xtr_decision)
            finally:
                executor.set_log_clock(None)
            # Counted only when XTR was the deciding reject (every other gate
            # already passed) - not every non-tradeable bar it also disliked.
            if xtr_decision is not None and xtr_decision.block_reason \
                    and decision.reject_reason == xtr_decision.block_reason:
                xtr_blocks[s] += 1
            if decision.executed:
                day_trades[s][day] = trades_today + 1
                if xtr_decision is not None and decision.plan:
                    xtr_states[s].record_entry(decision.ticket, xtr_decision,
                                               decision.plan.entry_price, xtr_a)

        if evaluated % 200 == 0:
            log.info("Evaluated %d bars (%s) - closed trades: %s", evaluated, primary_gw.current_time,
                     {s: len(gateways[s].closed_trades) for s in styles})

        if not all([gateways[s].advance() for s in styles]):
            break

    for s in styles:
        gateways[s].close_all_at_market()
    return {"evaluated": evaluated, "xtr_blocks": xtr_blocks}


def summarize(trades: list, starting_equity: float = 0.0) -> dict:
    if not trades:
        return {"total_trades": 0}
    wins = [t for t in trades if t.pnl_dollars > 0]
    losses = [t for t in trades if t.pnl_dollars <= 0]
    net = sum(t.pnl_dollars for t in trades)
    gross_win = sum(t.pnl_dollars for t in wins)
    gross_loss = -sum(t.pnl_dollars for t in losses)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    max_dd_pct = 0.0          # from the running peak of the account, the usual definition
    for t in trades:
        equity += t.pnl_dollars
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if starting_equity > 0:
            max_dd_pct = max(max_dd_pct, (peak - equity) / (starting_equity + peak) * 100.0)
    return {
        "total_trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "net_pnl_dollars": round(net, 2),
        "avg_win_dollars": round(sum(t.pnl_dollars for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_dollars": round(sum(t.pnl_dollars for t in losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_dollars": round(max_dd, 2),
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0 else None),
        "expectancy_dollars": round(net / len(trades), 2),
        **({"return_pct": round(100 * net / starting_equity, 2),
            "max_drawdown_pct": round(max_dd_pct, 2)} if starting_equity > 0 else {}),
    }


def write_trades_csv(trades: list, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
        writer.writeheader()
        for t in trades:
            writer.writerow({
                "entry_time": t.entry_time, "exit_time": t.exit_time, "direction": t.direction,
                "lots": t.lots, "entry_price": t.entry_price, "exit_price": t.exit_price,
                "sl": t.sl, "tp": t.tp, "exit_reason": t.exit_reason, "pnl_dollars": t.pnl_dollars,
            })


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bars-csv", dest="bars_csv", help="primary-timeframe (M15) CSV")
    parser.add_argument("--trend-csv", dest="trend_csv", help="trend-timeframe (H4) CSV")
    parser.add_argument("--daily-csv", dest="daily_csv", help="D1 CSV")
    parser.add_argument("--weekly-csv", dest="weekly_csv", help="W1 CSV")
    parser.add_argument("--m5-csv", dest="m5_csv", help="M5 CSV (XTR alignment gate)")
    parser.add_argument("--h1-csv", dest="h1_csv", help="H1 CSV (XTR alignment gate)")
    parser.add_argument("--xtr-gate", choices=["off", "block_opposed", "require_alignment"],
                        dest="xtr_gate",
                        help="XTR entry filter to replay (needs M5+H1 history: --m5-csv/--h1-csv or "
                             "--from-mt5). Default: config.py's xtr_gate when that history is "
                             "loaded, otherwise off. Entry filter only - lot, SL, TP unchanged.")
    parser.add_argument("--compare-xtr", action="store_true", dest="compare_xtr",
                        help="run the XTR gate OFF and ON side by side on identical data/verdicts "
                             "(combines with --compare for all four variants)")
    parser.add_argument("--csv-folder", dest="csv_folder",
                        help="read every timeframe from this folder instead (e.g. your Google Drive "
                             "price folder): <prefix>M15.csv, <prefix>H1.csv, ... - H4/D1/W1 are built "
                             "from H1 when absent")
    parser.add_argument("--csv-prefix", dest="csv_prefix",
                        help="file name prefix in --csv-folder (default: the symbol + '_', e.g. "
                             "BTCUSD_ for the EA's export; BTC_prices_ for backtest --to-drive files)")
    parser.add_argument("--from-mt5", action="store_true", dest="from_mt5",
                        help="pull history from a running MT5 terminal instead of CSVs")
    parser.add_argument("--months", type=float, help="with --from-mt5: the last N months (instead of --start/--end)")
    parser.add_argument("--to-drive", action="store_true", dest="to_drive",
                        help="also copy the summary, the trades and the price history to Google Drive "
                             "(MyMQChartDrive\\GoldTrader, the profile's file prefix) for analysis")
    parser.add_argument("--start", help="range start (with --from-mt5), e.g. 2026-01-01")
    parser.add_argument("--end", help="range end (with --from-mt5)")
    parser.add_argument("--profile", default="gold", choices=profiles.names(),
                        help="which market's rules (profiles.py): gold (default) or btc")
    parser.add_argument("--symbol", help="symbol (default: the profile's - XAUUSD / BTCUSD)")
    parser.add_argument("--spread-pct", type=float, dest="spread_pct",
                        help="spread as %% of price instead of --spread-points (btc default 0.02)")
    parser.add_argument("--rollover-spread-points", type=int, default=80, dest="rollover_spread_points",
                        help="spread around the daily reopen, 16:55-18:15 New York (default 80; 0 = "
                             "the normal spread all day)")
    parser.add_argument("--trade-hours", dest="trade_hours",
                        help="entry trading hours in config.py's trade_timezone (default: the live "
                             "setting, 08:00-16:45,18:15-20:00 New York; 'any' = no restriction)")
    parser.add_argument("--friday-cutoff", dest="friday_cutoff",
                        help="no new entry on Friday from this New York time ('off' = none)")
    parser.add_argument("--max-spread", type=int, dest="max_spread",
                        help="no entry while the spread is above this many points (0 = off)")
    parser.add_argument("--min-adx", type=float, dest="min_adx",
                        help="no entry while M15 ADX14 is below this (default 0 = off)")
    parser.add_argument("--spread-points", type=int, default=25, dest="spread_points",
                        help="simulated spread in points for CSV runs (XAUUSD 0.01 point: 25 = $0.25)")
    parser.add_argument("--mechanical", action="store_true",
                        help="use a free non-LLM stand-in instead of the real Claude API - tests the "
                             "engine only, NOT a backtest of Claude's actual judgment")
    parser.add_argument("--exit-style", choices=["sl_to_tp1", "fixed_tp"], default="sl_to_tp1",
                        dest="exit_style", help="which exit logic to run (default sl_to_tp1, the live "
                                                "default - see config.py); ignored with --compare")
    parser.add_argument("--compare", action="store_true",
                        help="run BOTH exit styles side by side against the identical bar sequence AND "
                             "the identical Claude verdicts (one API call stream, not two) - the direct "
                             "way to measure exit_style=sl_to_tp1 against the original fixed_tp design")
    parser.add_argument("--model", help="override Claude model id for this run")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation prompt")
    parser.add_argument("--out", default=os.path.join(paths.LOG_DIR, "backtest_trades.csv"))
    parser.add_argument("--max-daily-loss", type=float, dest="max_daily_loss",
                        help="daily loss cap in pct (breaker + open-risk budget, see main.py's own flag "
                             "of the same name / backtest.BacktestDayState) - default 10 (config.py); "
                             "pass 0 to not simulate it")
    parser.add_argument("--daily-target", type=float, dest="daily_target",
                        help="simulate the daily profit target alongside --max-daily-loss")
    parser.add_argument("--risk-percent", type=float, dest="risk_percent",
                        help="pct of equity risked per trade (see main.py's own flag of the same name) "
                             "- default 2 (config.py); pass 0 to simulate the fixed 0.01 lot instead")
    parser.add_argument("--sl-mode", choices=["fixed", "atr"], dest="sl_mode",
                        help="simulate ATR-adaptive initial stop-loss instead of the fixed sl_dollars "
                             "distance (see main.py's own flag of the same name)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    keys.load()     # ANTHROPIC_API_KEY for a paid (non --mechanical) run

    base_cfg = profiles.apply(AdvisorConfig(dry_run=True), args.profile)
    args.symbol = args.symbol or base_cfg.symbol
    btc = base_cfg.instrument == "btc"
    if btc and args.out == os.path.join(paths.LOG_DIR, "backtest_trades.csv"):
        args.out = os.path.join(base_cfg.log_dir, "backtest_trades.csv")
    if args.model:
        base_cfg.claude_model = args.model
    if args.max_daily_loss is not None:
        base_cfg.max_daily_loss_pct = args.max_daily_loss
    if args.daily_target is not None:
        base_cfg.daily_target_pct = args.daily_target
        base_cfg.use_daily_target = args.daily_target > 0
    if args.risk_percent is not None:
        base_cfg.risk_percent = args.risk_percent
        base_cfg.use_risk_percent = args.risk_percent > 0
    if args.sl_mode:
        base_cfg.sl_mode = args.sl_mode
    if args.trade_hours is not None:
        base_cfg.trade_windows = "" if args.trade_hours.strip().lower() in ("", "any", "off") \
            else args.trade_hours
    if args.friday_cutoff is not None:
        base_cfg.friday_cutoff_ny = "" if args.friday_cutoff.strip().lower() in ("", "off") \
            else args.friday_cutoff
    if args.max_spread is not None:
        base_cfg.max_spread_points = args.max_spread
    if args.min_adx is not None:
        base_cfg.min_adx = args.min_adx
    try:
        tactics.validate(base_cfg)
    except ValueError as exc:
        log.error("Setting not understood: %s", exc)
        return 1
    log.info("Entry tactics: %s", tactics.describe(base_cfg))

    if args.from_mt5 and args.months and not (args.start or args.end):
        end = pd.Timestamp.now(tz="UTC").normalize() + pd.Timedelta(days=1)
        args.start = (end - pd.Timedelta(days=int(args.months * 30.5))).strftime("%Y-%m-%d")
        args.end = end.strftime("%Y-%m-%d")
        log.info("Period: %s to %s (last %g months)", args.start, args.end, args.months)
    if args.from_mt5:
        if not args.start or not args.end:
            log.error("--from-mt5 requires --start and --end (or --months)")
            return 1
        gw.connect()
        bars = {
            base_cfg.primary_timeframe: gw.get_bars_range(args.symbol, base_cfg.primary_timeframe,
                                                           args.start, args.end),
            base_cfg.trend_timeframe: gw.get_bars_range(args.symbol, base_cfg.trend_timeframe,
                                                        args.start, args.end),
            "D1": gw.get_bars_range(args.symbol, "D1", args.start, args.end),
            "W1": gw.get_bars_range(args.symbol, "W1", args.start, args.end),
        }
        if args.xtr_gate != "off" or args.compare_xtr:
            for tf in ("M5", "H1"):
                bars[tf] = gw.get_bars_range(args.symbol, tf, args.start, args.end)
        spec = gw.symbol_spec(args.symbol)
    else:
        if args.csv_folder:
            prefix = args.csv_prefix if args.csv_prefix is not None else f"{args.symbol}_"
            try:
                found = load_csv_folder(args.csv_folder, prefix, base_cfg.primary_timeframe,
                                        base_cfg.trend_timeframe)
            except FileNotFoundError as exc:
                log.error("%s", exc)
                return 1
            bars = {tf: found[tf] for tf in (base_cfg.primary_timeframe, base_cfg.trend_timeframe, "D1", "W1")}
            # M5 + H1 only for the XTR gate: the M5 file is the shortest and
            # would otherwise shorten the whole replay through its warm-up
            if args.xtr_gate not in (None, "off") or args.compare_xtr:
                bars.update({tf: found[tf] for tf in ("M5", "H1") if tf in found})
            primary_times = bars[base_cfg.primary_timeframe]["time"]
            args.start = args.start or primary_times.iloc[0].strftime("%Y-%m-%d")
            args.end = args.end or primary_times.iloc[-1].strftime("%Y-%m-%d")
            log.info("Prices from %s (%s*.csv): %s", args.csv_folder, prefix,
                     ", ".join(f"{tf} {len(df)} bars" for tf, df in bars.items()))
        else:
            required = [args.bars_csv, args.trend_csv, args.daily_csv, args.weekly_csv]
            if not all(required):
                log.error("Provide --csv-folder, --bars-csv/--trend-csv/--daily-csv/--weekly-csv, "
                          "or use --from-mt5.")
                return 1
            bars = {
                base_cfg.primary_timeframe: load_bars_csv(args.bars_csv),
                base_cfg.trend_timeframe: load_bars_csv(args.trend_csv),
                "D1": load_bars_csv(args.daily_csv),
                "W1": load_bars_csv(args.weekly_csv),
            }
            if args.m5_csv and args.h1_csv:
                bars["M5"] = load_bars_csv(args.m5_csv)
                bars["H1"] = load_bars_csv(args.h1_csv)
        if btc:     # the usual BTCUSD CFD: 1 lot = 1 BTC, so $1 of price = $1 a lot
            spec = gw.SymbolSpec(name=args.symbol, point=0.01, digits=2, stops_level_points=0,
                                 spread_points=0, volume_min=0.01, volume_max=100.0, volume_step=0.01,
                                 tick_value=0.01, tick_size=0.01)
        else:
            spec = gw.SymbolSpec(name=args.symbol, point=0.01, digits=2, stops_level_points=0,
                                 spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                                 tick_value=1.0, tick_size=0.01)
    spread_pct = args.spread_pct if args.spread_pct is not None else (0.02 if btc else None)
    if spread_pct is not None:
        # A spread in % of price (BTC): points at the period's median price
        median = float(bars[base_cfg.primary_timeframe]["close"].median())
        args.spread_points = max(1, int(round(median * spread_pct / 100.0 / spec.point)))
        if btc:
            args.rollover_spread_points = 0       # no gold-style daily reopen widening
        log.info("Spread: %.3f%% of price = %d points at the median price %.2f", spread_pct,
                 args.spread_points, median)

    have_xtr_bars = "M5" in bars and "H1" in bars
    xtr_mode = args.xtr_gate or (base_cfg.xtr_gate if have_xtr_bars else "off")
    if (xtr_mode != "off" or args.compare_xtr) and not have_xtr_bars:
        log.error("The XTR gate needs M5 and H1 history - pass --m5-csv and --h1-csv (or --from-mt5).")
        return 1
    if args.compare_xtr and xtr_mode == "off":
        xtr_mode = base_cfg.xtr_gate if base_cfg.xtr_gate != "off" else "block_opposed"
    exit_styles = ["sl_to_tp1", "fixed_tp"] if args.compare else [args.exit_style]
    xtr_modes = ["off", xtr_mode] if args.compare_xtr else [xtr_mode]
    log.info("XTR alignment gate: %s (entry filter only - lot, SL and TP unchanged)",
             " vs ".join(xtr_modes))
    styles = []
    gateways, cfgs = {}, {}
    for style in exit_styles:
        for mode in xtr_modes:
            name = style if not args.compare_xtr else f"{style}_xtr-{mode}"
            styles.append(name)
            gateways[name] = HistoricalGateway(args.symbol, bars, spec,
                                               spread_points=args.spread_points,
                                               rollover_spread_points=args.rollover_spread_points)
            if not gateways[name].reset(base_cfg.primary_timeframe, base_cfg.bars_per_timeframe):
                log.error("Not enough historical data to even warm up (need >%d bars per timeframe).",
                          base_cfg.bars_per_timeframe)
                return 1
            cfgs[name] = dataclasses.replace(base_cfg, exit_style=style, xtr_gate=mode,
                                             log_dir=os.path.join(paths.LOG_DIR, "backtest" + (
                                                 "_btc" if btc else ""), name))

    n_calls = estimate_call_count(gateways[styles[0]])
    if args.mechanical:
        log.info("Running %d bar evaluations in --mechanical mode (free, no API calls).", n_calls)
        client = None
    else:
        log.warning("This will make approximately %d real Claude API calls (one per evaluated bar, "
                   "regardless of how many exit styles are being compared). Check "
                   "https://console.anthropic.com/ for current pricing before proceeding.", n_calls)
        if not args.yes:
            reply = input(f"Proceed with ~{n_calls} Claude API calls? [y/N] ").strip().lower()
            if reply != "y":
                log.info("Aborted - pass --yes to skip this prompt, or --mechanical for a free run.")
                return 0
        client = claude_advisor.build_client()

    if len(styles) > 1:
        stats = run_backtest_compare(gateways, cfgs, client, args.mechanical)
        for style in styles:
            summary = summarize(gateways[style].closed_trades, gateways[style].starting_equity)
            summary["xtr_blocked_entries"] = stats["xtr_blocks"][style]
            # os.path.splitext rather than str.replace(".csv", ...) - the
            # latter is a no-op (and silently collides both styles onto the
            # same file) whenever --out doesn't contain the literal
            # substring ".csv", e.g. an extensionless path.
            base, ext = os.path.splitext(args.out)
            out_path = f"{base}_{style}{ext or '.csv'}"
            write_trades_csv(gateways[style].closed_trades, out_path)
            log.info("[%s] %s", style, summary)
            log.info("[%s] Trade log written to %s", style, out_path)
    else:
        style = styles[0]
        stats = run_backtest(gateways[style], cfgs[style], client, args.mechanical)
        summary = summarize(gateways[style].closed_trades, gateways[style].starting_equity)
        summary["xtr_blocked_entries"] = stats["xtr_blocks"]["run"]
        write_trades_csv(gateways[style].closed_trades, args.out)
        log.info("Backtest complete (%s) - %s", style, summary)
        log.info("Trade log written to %s", args.out)
        if args.to_drive:
            publish_to_drive(base_cfg, args, summary, bars)
    return 0


def publish_to_drive(cfg, args, summary: dict, bars: dict) -> None:
    """Summary, trades and the price history (every timeframe used) into the
    journal's Drive folder, with the profile's prefix (GoldTrader_ / BTC_)."""
    import trade_journal
    drive = trade_journal.target_folder(cfg)
    if not drive:
        log.warning("Google Drive folder not found - the results stay in %s", args.out)
        return
    lines = [f"{cfg.journal_prefix.rstrip('_')} backtest ({cfg.instrument}, {args.symbol}) "
             f"{args.start or ''} to {args.end or ''} - {'mechanical' if args.mechanical else 'Claude'}",
             f"stop {cfg.sl_mode} x{cfg.sl_atr_mult:g}" + (f" ({cfg.sl_pct_min:g}%-{cfg.sl_pct_max:g}% of price)"
                                                           if cfg.sl_pct_min or cfg.sl_pct_max else "")
             + (f", lock +{cfg.lock_r:g}R, trail {cfg.trail_r:g}R" if cfg.lock_mode == "r" else
                f", lock ${cfg.tp1_dollars:g}, trail ${cfg.trail_dollars:g}")
             + f", risk {cfg.risk_percent:g}%, spread {args.spread_points} points", ""]
    lines += [f"{k}: {v}" for k, v in summary.items()]
    files = {"backtest_summary.txt": "\n".join(lines) + "\n"}
    with open(args.out, encoding="utf-8") as f:
        files["backtest_trades.csv"] = f.read()
    for tf, df in bars.items():
        out = df[["time", "open", "high", "low", "close"]].copy()
        out["time"] = pd.to_datetime(out["time"], utc=True).dt.strftime("%Y-%m-%d %H:%M")
        files[f"prices_{tf}.csv"] = out.to_csv(index=False)
    for name, text in files.items():
        trade_journal.write_if_changed(os.path.join(drive, cfg.journal_prefix + name), text)
    log.info("Copied to Google Drive (%s): %s", drive, ", ".join(cfg.journal_prefix + n for n in files))


if __name__ == "__main__":
    raise SystemExit(main())

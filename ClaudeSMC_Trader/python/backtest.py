#!/usr/bin/env python3
"""
Backtests this solution against historical XAUUSD bars: walks forward one
primary-timeframe candle at a time, builds the exact same feature snapshot
main.py would (market_intel.build_feature_snapshot), asks Claude to
validate the confluences exactly as live trading does (claude_advisor.
get_verdict), gates and "opens" trades through the real executor.gate()/
executor.execute() code path, and simulates each position's exit (SL / TP /
the $3 arm-then-trail, mirroring ClaudeSMC_TradeManager.mq5's logic) against
the bars that follow. Reports a trade log and summary stats.

WHY THIS EXISTS: the README's own "Honest limitations" section says nothing
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
import logging
import os
from dataclasses import dataclass

import pandas as pd

import claude_advisor
import executor
import market_intel
import mt5_gateway as gw
from config import AdvisorConfig

log = logging.getLogger("backtest")

TIMEFRAME_DURATIONS = {
    "M1": pd.Timedelta(minutes=1), "M5": pd.Timedelta(minutes=5),
    "M15": pd.Timedelta(minutes=15), "M30": pd.Timedelta(minutes=30),
    "H1": pd.Timedelta(hours=1), "H4": pd.Timedelta(hours=4),
    "D1": pd.Timedelta(days=1), "W1": pd.Timedelta(weeks=1),
}

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


class HistoricalGateway:
    """Replays pre-loaded historical bars and acts as a paper broker - same
    combined "market data + order placement" shape as the real mt5_gateway
    module, so executor.gate()/executor.execute() work against this
    unmodified. Two concerns live here deliberately together, same as the
    real gateway: (1) market-data replay (get_bars/get_tick/advance - see
    the no-lookahead note in the module docstring) and (2) paper-broker
    bookkeeping (place_market_order/count_same_direction/manage_positions -
    manage_positions is backtest-only; nothing else in this codebase calls
    it, since live trading's equivalent is ClaudeSMC_TradeManager.mq5
    running inside MT5, not Python).
    """
    def __init__(self, symbol: str, bars: dict, spec, spread_points: int = 25):
        """bars: {"M15": df, "H4": df, "D1": df, "W1": df, ...} - each a
        DataFrame of time(tz-aware ascending)/open/high/low/close/volume,
        every row a genuinely CLOSED historical bar.
        """
        self.symbol = symbol
        self.bars = {tf: df.sort_values("time").reset_index(drop=True) for tf, df in bars.items()}
        self.spec = spec
        self.spread_points = spread_points
        self.primary_timeframe = None
        self.cursor = 0
        self.open_positions: list = []
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
            if len(df) <= warmup_bars:
                return False
            candidate = df["time"].iloc[warmup_bars]
            if min_start_time is None or candidate > min_start_time:
                min_start_time = candidate
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

    @property
    def current_bar(self) -> pd.Series:
        return self.bars[self.primary_timeframe].iloc[self.cursor]

    def advance(self) -> bool:
        """Moves to the next primary-timeframe bar. Returns False once the
        data is exhausted (backtest is over)."""
        self.cursor += 1
        return self.cursor < len(self.bars[self.primary_timeframe])

    # --- market data (no-lookahead) ------------------------------------- #
    def get_bars(self, symbol: str, timeframe_name: str, count: int) -> pd.DataFrame:
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
        price = float(primary["open"].iloc[next_idx]) if next_idx < len(primary) \
            else float(primary["close"].iloc[self.cursor])
        spread = self.spec.point * self.spread_points
        return _FakeTick(bid=price - spread / 2, ask=price + spread / 2)

    def symbol_spec(self, symbol: str):
        return self.spec

    def price_distance_for_dollars(self, spec, dollars: float, lots: float) -> float:
        return gw.price_distance_for_dollars(spec, dollars, lots)

    # --- paper broker ----------------------------------------------------- #
    def count_same_direction(self, symbol: str, magic: int, direction: str) -> int:
        return sum(1 for p in self.open_positions if p.direction == direction)

    def place_market_order(self, spec, direction: str, lots: float, sl_price: float, tp_price: float,
                            magic: int, comment: str, deviation_points: int, dry_run: bool):
        tick = self.get_tick(spec.name)
        price = tick.ask if direction == "buy" else tick.bid
        ticket = self._next_ticket
        self._next_ticket += 1
        self.open_positions.append(SimPosition(
            ticket=ticket, direction=direction, lots=lots, entry_time=self.current_time,
            entry_price=price, sl=sl_price, tp=tp_price,
        ))
        return _FakeOrderResult(retcode=10009, order=ticket, price=price)

    def manage_positions(self, cfg: AdvisorConfig) -> None:
        """The backtest's stand-in for ClaudeSMC_TradeManager.mq5: checks
        every open simulated position against the bar that JUST closed
        (self.current_bar) for a stop-out or take-profit, and arms/tightens
        the trailing stop for FUTURE bars exactly like that EA does - only
        replacing the fixed TP once the trailing SL actually moves this bar,
        never on profit alone (mirrors the bug fix in that EA's history).

        A position can be tested against the SAME bar it just opened on
        (entry and exit inside one bar is realistic), but a trail that arms
        on bar N is only tested for a stop-out starting bar N+1 - conflating
        "armed" and "hit by its own new stop" within the identical bar would
        need genuine intrabar (tick-level) sequencing this bar-level
        simulation doesn't have. This is a documented simplification, not a
        bug - see the README's backtest section.
        """
        bar = self.current_bar
        high, low = float(bar["high"]), float(bar["low"])
        min_stop_dist = self.spec.stops_level_points * self.spec.point
        still_open = []
        for pos in self.open_positions:
            arm_dist = self.price_distance_for_dollars(self.spec, cfg.tp_arm_dollars, pos.lots)
            trail_dist = self.price_distance_for_dollars(self.spec, cfg.trail_dollars, pos.lots)

            exit_price, exit_reason = None, None
            if pos.direction == "buy":
                if low <= pos.sl:
                    exit_price, exit_reason = pos.sl, "trail" if pos.armed else "sl"
                elif pos.tp is not None and high >= pos.tp:
                    exit_price, exit_reason = pos.tp, "tp"
                else:
                    profit_at_high = high - pos.entry_price
                    if profit_at_high >= arm_dist:
                        candidate = high - trail_dist
                        if candidate > pos.sl and (high - candidate) >= min_stop_dist:
                            pos.sl = candidate
                            pos.tp = None
                            pos.armed = True
            else:
                if high >= pos.sl:
                    exit_price, exit_reason = pos.sl, "trail" if pos.armed else "sl"
                elif pos.tp is not None and low <= pos.tp:
                    exit_price, exit_reason = pos.tp, "tp"
                else:
                    profit_at_low = pos.entry_price - low
                    if profit_at_low >= arm_dist:
                        candidate = low + trail_dist
                        if candidate < pos.sl and (candidate - low) >= min_stop_dist:
                            pos.sl = candidate
                            pos.tp = None
                            pos.armed = True

            if exit_price is None:
                still_open.append(pos)
                continue
            self._close_position(pos, exit_price, exit_reason, self.current_time)
        self.open_positions = still_open

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
        if not self.open_positions:
            return
        primary = self.bars[self.primary_timeframe]
        last_close = float(primary["close"].iloc[-1])
        last_time = primary["time"].iloc[-1] + TIMEFRAME_DURATIONS[self.primary_timeframe]
        for pos in self.open_positions:
            self._close_position(pos, last_close, reason, last_time)
        self.open_positions = []

    def _close_position(self, pos: SimPosition, exit_price: float, reason: str,
                         exit_time: pd.Timestamp) -> None:
        sign = 1.0 if pos.direction == "buy" else -1.0
        price_move = sign * (exit_price - pos.entry_price)
        pnl = price_move / self.spec.tick_size * self.spec.tick_value * pos.lots
        self.closed_trades.append(ClosedTrade(
            entry_time=pos.entry_time, exit_time=exit_time, direction=pos.direction, lots=pos.lots,
            entry_price=pos.entry_price, exit_price=exit_price, sl=pos.sl, tp=pos.tp,
            exit_reason=reason, pnl_dollars=round(pnl, 2),
        ))


def load_bars_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df.sort_values("time").reset_index(drop=True)


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

    ind = features["primary_indicators"]
    trend_bias = features["trend_bias"]

    trend_up = trend_bias["close"] > trend_bias["ema200"] and ind["ema20"] > ind["ema50"]
    trend_down = trend_bias["close"] < trend_bias["ema200"] and ind["ema20"] < ind["ema50"]
    trend_confirmed = abs(ind["ema20"] - ind["ema50"]) >= 0.25 * ind["atr14"]
    trend_dir = "buy" if trend_up else "sell" if trend_down else "neutral"

    momentum_up = ind["macd_line"] > ind["macd_signal"] and 50 <= ind["rsi14"] <= 70
    momentum_down = ind["macd_line"] < ind["macd_signal"] and 30 <= ind["rsi14"] <= 50
    momentum_confirmed = ((ind["macd_hist"] > ind["macd_hist_prev"] and ind["rsi14"] >= 55) or
                          (ind["macd_hist"] < ind["macd_hist_prev"] and ind["rsi14"] <= 45))
    momentum_dir = "buy" if momentum_up else "sell" if momentum_down else "neutral"

    strength_up = ind["adx14"] >= 22 and ind["plus_di"] > ind["minus_di"]
    strength_down = ind["adx14"] >= 22 and ind["minus_di"] > ind["plus_di"]
    strength_confirmed = ind["adx14"] >= 28 and abs(ind["plus_di"] - ind["minus_di"]) >= 8
    strength_dir = "buy" if strength_up else "sell" if strength_down else "neutral"

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


def run_backtest(gateway: HistoricalGateway, cfg: AdvisorConfig, client, mechanical: bool) -> None:
    day_trades = {}  # date -> count, so max_trades_per_day applies per simulated day too
    evaluated = 0
    while True:
        gateway.manage_positions(cfg)
        try:
            features = market_intel.build_feature_snapshot(gateway, cfg)
        except RuntimeError:
            # Not enough closed history yet for some timeframe at this point
            # (shouldn't happen after reset()'s warmup, but degrade safely).
            if not gateway.advance():
                break
            continue

        verdict = mechanical_verdict(features) if mechanical else claude_advisor.get_verdict(
            client, cfg, features)
        evaluated += 1

        day = gateway.current_time.date()
        trades_today = day_trades.get(day, 0)
        decision = executor.execute(gateway, cfg, verdict, gateway.spec, trades_today)
        if decision.executed:
            day_trades[day] = trades_today + 1

        if evaluated % 200 == 0:
            log.info("Evaluated %d bars, %d trades closed so far (%s)",
                     evaluated, len(gateway.closed_trades), gateway.current_time)

        if not gateway.advance():
            break

    # The final loop iteration already ran manage_positions() against the
    # last bar before breaking - do NOT call it again here, the cursor is
    # now one past the end (that's what "no more bars" means).
    gateway.close_all_at_market()


def summarize(trades: list) -> dict:
    if not trades:
        return {"total_trades": 0}
    wins = [t for t in trades if t.pnl_dollars > 0]
    losses = [t for t in trades if t.pnl_dollars <= 0]
    net = sum(t.pnl_dollars for t in trades)
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in trades:
        equity += t.pnl_dollars
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return {
        "total_trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "net_pnl_dollars": round(net, 2),
        "avg_win_dollars": round(sum(t.pnl_dollars for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_dollars": round(sum(t.pnl_dollars for t in losses) / len(losses), 2) if losses else 0.0,
        "max_drawdown_dollars": round(max_dd, 2),
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
    parser.add_argument("--from-mt5", action="store_true", dest="from_mt5",
                        help="pull history from a running MT5 terminal instead of CSVs")
    parser.add_argument("--start", help="range start (with --from-mt5), e.g. 2026-01-01")
    parser.add_argument("--end", help="range end (with --from-mt5)")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--mechanical", action="store_true",
                        help="use a free non-LLM stand-in instead of the real Claude API - tests the "
                             "engine only, NOT a backtest of Claude's actual judgment")
    parser.add_argument("--model", help="override Claude model id for this run")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation prompt")
    parser.add_argument("--out", default="logs/backtest_trades.csv")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    cfg = AdvisorConfig(dry_run=True, log_dir="logs/backtest")
    if args.model:
        cfg.claude_model = args.model

    if args.from_mt5:
        if not args.start or not args.end:
            log.error("--from-mt5 requires --start and --end")
            return 1
        gw.connect()
        bars = {
            cfg.primary_timeframe: gw.get_bars_range(args.symbol, cfg.primary_timeframe, args.start, args.end),
            cfg.trend_timeframe: gw.get_bars_range(args.symbol, cfg.trend_timeframe, args.start, args.end),
            "D1": gw.get_bars_range(args.symbol, "D1", args.start, args.end),
            "W1": gw.get_bars_range(args.symbol, "W1", args.start, args.end),
        }
        spec = gw.symbol_spec(args.symbol)
    else:
        required = [args.bars_csv, args.trend_csv, args.daily_csv, args.weekly_csv]
        if not all(required):
            log.error("Provide --bars-csv/--trend-csv/--daily-csv/--weekly-csv, or use --from-mt5.")
            return 1
        bars = {
            cfg.primary_timeframe: load_bars_csv(args.bars_csv),
            cfg.trend_timeframe: load_bars_csv(args.trend_csv),
            "D1": load_bars_csv(args.daily_csv),
            "W1": load_bars_csv(args.weekly_csv),
        }
        spec = gw.SymbolSpec(name=args.symbol, point=0.01, digits=2, stops_level_points=0,
                             spread_points=25, volume_min=0.01, volume_max=5.0, volume_step=0.01,
                             tick_value=1.0, tick_size=0.01)

    gateway = HistoricalGateway(args.symbol, bars, spec)
    if not gateway.reset(cfg.primary_timeframe, cfg.bars_per_timeframe):
        log.error("Not enough historical data to even warm up (need >%d bars per timeframe).",
                  cfg.bars_per_timeframe)
        return 1

    n_calls = estimate_call_count(gateway)
    if args.mechanical:
        log.info("Running %d bar evaluations in --mechanical mode (free, no API calls).", n_calls)
        client = None
    else:
        log.warning("This will make approximately %d real Claude API calls (one per evaluated bar). "
                   "Check https://console.anthropic.com/ for current pricing before proceeding.", n_calls)
        if not args.yes:
            reply = input(f"Proceed with ~{n_calls} Claude API calls? [y/N] ").strip().lower()
            if reply != "y":
                log.info("Aborted - pass --yes to skip this prompt, or --mechanical for a free run.")
                return 0
        client = claude_advisor.build_client()

    run_backtest(gateway, cfg, client, args.mechanical)

    summary = summarize(gateway.closed_trades)
    write_trades_csv(gateway.closed_trades, args.out)
    log.info("Backtest complete - %s", summary)
    log.info("Trade log written to %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

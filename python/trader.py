#!/usr/bin/env python3
"""
XAUUSD confluence trading bot for MetaTrader 5.

Evaluates on every M5 candle close and opens up to 2 positions of 0.02 lots
each on XAUUSD, with a 60-pip stop-loss and a 30-pip trailing stop,
but only when at least 2 of the 3 confluences (trend, momentum, strength) agree
and at least one of them is confirmed at its stricter threshold.

When the market is closed the bot keeps evaluating and logging confluences but
sends no orders, and resumes trading by itself once quotes start moving again.

    python trader.py --check            # connect, print spec + preflight, exit
    python trader.py --signal           # evaluate the confluences once and exit
    python trader.py                    # run the loop in DRY-RUN (no orders)
    python trader.py --live             # run the loop and actually place orders
    python trader.py --selftest         # strategy self-test, no MT5 needed

Requires Windows + MetaTrader 5 terminal for anything that touches the market.
Credentials come from the environment, never from this file:
    set MT5_LOGIN=12345678
    set MT5_PASSWORD=...
    set MT5_SERVER=YourBroker-Demo
"""
from __future__ import annotations

import argparse
import csv
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import mt5_client as mc
import strategy as st
from config import TradeConfig

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRADE_LOG = LOG_DIR / "trades.csv"

log = logging.getLogger("trader")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "trader.log", encoding="utf-8"),
        ],
    )


def record_trade(row: dict) -> None:
    new_file = not TRADE_LOG.exists()
    with TRADE_LOG.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(row))
        if new_file:
            writer.writeheader()
        writer.writerow(row)


class Bot:
    def __init__(self, cfg: TradeConfig, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.spec: mc.SymbolSpec | None = None
        self.last_bar_time: pd.Timestamp | None = None
        self.day: datetime.date | None = None
        self.day_start_equity = 0.0
        self.trades_today = 0
        self.daily_loss_hit = False
        self.daily_target_hit = False
        self.daily_lock_hit = False
        self.day_peak_equity = 0.0
        self.running = True
        self.quotes = mc.QuoteMonitor()
        self.market_open = True
        self._closed_logged = False

    # ---------------- lifecycle ----------------
    def start(self, probe_market: bool = True) -> list[str]:
        mc.connect(self.cfg)
        self.spec = mc.get_symbol_spec(self.cfg)

        if probe_market:
            self.market_open, reason = mc.market_status(self.cfg)
            log.info("Market check: %s (%s)",
                     "OPEN" if self.market_open else "CLOSED", reason)
            if not self.market_open:
                log.info("While the market is closed the bot keeps evaluating "
                         "confluences on the last closed bars but places no orders.")

        problems = mc.preflight_check(self.cfg, self.spec, market_open=self.market_open)
        self.day_start_equity = mc.account_equity()
        self.day = datetime.now(timezone.utc).date()
        return problems

    def stop(self, *_args) -> None:
        self.running = False
        log.info("Shutdown requested - finishing current cycle.")

    # ---------------- guards (separate from the 3 confluences) ----------------
    def roll_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day = today
            self.day_start_equity = mc.account_equity()
            self.trades_today = 0
            self.daily_loss_hit = False
            self.daily_target_hit = False
            self.daily_lock_hit = False
            self.day_peak_equity = self.day_start_equity
            log.info("New trading day - counters reset (equity %.2f)", self.day_start_equity)

        if self.day_start_equity <= 0:
            return

        equity = mc.account_equity()
        move_pct = (equity - self.day_start_equity) / self.day_start_equity * 100.0

        self.day_peak_equity = max(self.day_peak_equity, self.day_start_equity, equity)
        peak_pct = (self.day_peak_equity - self.day_start_equity) / self.day_start_equity * 100.0

        # Profit lock - protects a day that ran up without capping it
        if (self.cfg.lock_daily_gains and not self.daily_lock_hit
                and peak_pct >= self.cfg.lock_after_pct):
            floor_pct = peak_pct * (1.0 - self.cfg.give_back_pct / 100.0)
            if move_pct <= floor_pct:
                self.daily_lock_hit = True
                log.info("Profit lock: peaked at +%.2f%%, gave back to +%.2f%% "
                         "(floor +%.2f%%) - done for today.", peak_pct, move_pct, floor_pct)
                if self.cfg.close_on_target:
                    for pos in mc.get_positions(self.cfg):
                        mc.close_position(self.cfg, self.spec, pos, self.dry_run)

        if not self.daily_loss_hit and -move_pct >= self.cfg.max_daily_loss_pct:
            self.daily_loss_hit = True
            log.warning("Daily loss limit hit (%.2f%%) - no new entries today.", move_pct)

        # Daily target: bank the day once it is made. Measured on equity, so
        # floating profit counts - hence close_on_target realises it by default.
        if (self.cfg.use_daily_target and not self.daily_target_hit
                and move_pct >= self.cfg.daily_target_pct):
            self.daily_target_hit = True
            log.info("Daily profit target reached (+%.2f%% >= +%.2f%%) - done for today.",
                     move_pct, self.cfg.daily_target_pct)
            if self.cfg.close_on_target:
                for pos in mc.get_positions(self.cfg):
                    log.info("Banking the day: closing ticket %s", pos.ticket)
                    mc.close_position(self.cfg, self.spec, pos, self.dry_run)

    def entry_blocked(self) -> str | None:
        """Return a reason string if new entries are not allowed right now."""
        cfg = self.cfg
        now = datetime.now()

        if not self.market_open:
            return "market is closed"
        if self.daily_loss_hit:
            return "daily loss limit reached"
        if self.daily_target_hit:
            return f"daily profit target (+{self.cfg.daily_target_pct:g}%) already reached"
        if self.daily_lock_hit:
            return "profit lock triggered - the day gave back too much of its peak"
        if cfg.max_trades_per_day > 0 and self.trades_today >= cfg.max_trades_per_day:
            return f"max trades/day reached ({self.trades_today})"
        if len(mc.get_positions(cfg)) >= cfg.max_open_positions:
            return f"max open positions reached ({cfg.max_open_positions})"
        if not cfg.can_enter():
            zone = cfg.session_now()
            if not cfg.in_session():
                return (f"outside session {cfg.session_start_hour}:00-"
                        f"{cfg.session_end_hour}:00 GMT{cfg.session_gmt_offset:+g} "
                        f"(now {zone:%H:%M} there)")
            return (f"inside the session but within an entry buffer "
                    f"({cfg.minutes_to_close():.0f} min to close)")
        now = cfg.session_now()
        if cfg.close_before_weekend and now.weekday() == 4 and now.hour >= cfg.weekend_close_hour:
            return "weekend close window"
        spread = mc.get_symbol_spec(cfg).spread_points
        if spread > cfg.max_spread_points:
            return f"spread too wide ({spread} > {cfg.max_spread_points} points)"
        return None

    # ---------------- strategy ----------------
    def opposite_position_blocks(self, direction: str) -> str | None:
        """Block a signal that opposes an open position (see allow_opposite_positions)."""
        if self.cfg.allow_opposite_positions:
            return None
        m = mc.mt5()
        wanted = m.POSITION_TYPE_BUY if direction == "buy" else m.POSITION_TYPE_SELL
        for pos in mc.get_positions(self.cfg):
            if pos.type != wanted:
                return (f"an opposing position is open (ticket {pos.ticket}); "
                        "set allow_opposite_positions to permit hedging")
        return None

    def latest_signal(self) -> st.Signal:
        cfg = self.cfg
        work = mc.get_closed_bars(cfg.symbol, cfg.working_timeframe, 500)
        trend = mc.get_closed_bars(cfg.symbol, cfg.trend_timeframe, 300)
        df = st.compute_indicators(work, trend, cfg)
        return st.evaluate(df, cfg)

    def check_for_entry(self) -> None:
        """Evaluate the confluences once per newly closed working-timeframe bar."""
        sig = self.latest_signal()
        if sig.bar_time == self.last_bar_time:
            return  # already evaluated this bar
        self.last_bar_time = sig.bar_time

        log.info("Bar %s close=%.2f | %s", sig.bar_time, sig.close, sig.summary())

        if sig.direction is None:
            return

        blocked = self.entry_blocked() or self.opposite_position_blocks(sig.direction)
        if blocked:
            log.info("Signal %s suppressed: %s", sig.direction.upper(), blocked)
            return

        side = sig.buy if sig.direction == "buy" else sig.sell
        names = ("trend", "momentum", "strength")
        passed = [n for n, ok in zip(names, (side.trend, side.momentum, side.strength)) if ok]
        confirmed = [n for n, ok in zip(
            names, (side.trend_confirmed, side.momentum_confirmed, side.strength_confirmed)) if ok]
        log.info("ENTRY %s - setup score %.0f%% | %d/3 confluences (%s), confirmed: %s",
                 sig.direction.upper(), side.confidence, side.count, ", ".join(passed),
                 ", ".join(confirmed) if confirmed else "none")
        log.info("    score parts: trend %.1f + momentum %.1f + strength %.1f "
                 "(setup quality, not a win probability)",
                 side.parts["trend"], side.parts["momentum"], side.parts["strength"])
        for name in names:
            log.info("    %-8s %s%s", name,
                     "CONFIRMED " if name in confirmed else ("pass " if name in passed else "fail "),
                     side.reasons[name])

        result = mc.open_position(self.cfg, self.spec, sig.direction, self.dry_run)
        self.trades_today += 1
        record_trade({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bar_time": str(sig.bar_time),
            "direction": sig.direction,
            "confluences": side.count,
            "confirmed": side.confirmed_count,
            "score": side.confidence,
            "marks": side.marks(),
            "lots": getattr(result, "volume", self.cfg.lots),
            "price": getattr(result, "price", sig.close),
            "sl_units": self.cfg.stop_loss_units,
            "trail_units": self.cfg.trailing_stop_units,
            "unit": self.cfg.distance_unit,
            "mode": "dry-run" if self.dry_run else "live",
            "retcode": getattr(result, "retcode", ""),
            "ticket": getattr(result, "order", ""),
        })

    # ---------------- trailing stop ----------------
    def refresh_market_state(self) -> None:
        """Update open/closed state from quote liveness, logging each transition."""
        live = self.quotes.update(mc.get_tick(self.cfg.symbol))
        if live and not self.market_open:
            log.info("Quotes resumed - market is OPEN, trading re-enabled.")
            self._closed_logged = False
        elif not live and self.market_open and not self._closed_logged:
            log.info("Quotes frozen for %.0fs - treating the market as CLOSED; "
                     "no orders will be sent until they resume.", self.quotes.frozen_for)
            self._closed_logged = True
        self.market_open = live

    def manage_trailing(self) -> None:
        cfg, spec = self.cfg, self.spec
        if not self.market_open:
            return  # stop modifications would only be rejected
        positions = mc.get_positions(cfg)
        if not positions:
            return

        tick = mc.get_tick(cfg.symbol)
        point = spec.point
        trail_dist = cfg.trail_distance(point)
        start_dist = cfg.trail_start_distance(point)
        min_stop = spec.stops_level_points * point
        m = mc.mt5()

        for pos in positions:
            is_buy = pos.type == m.POSITION_TYPE_BUY
            if is_buy:
                profit = tick.bid - pos.price_open
                if profit < start_dist:
                    continue
                new_sl = tick.bid - trail_dist
                # only ever tighten, and respect the broker's minimum distance
                if new_sl <= pos.sl or (tick.bid - new_sl) < min_stop:
                    continue
            else:
                profit = pos.price_open - tick.ask
                if profit < start_dist:
                    continue
                new_sl = tick.ask + trail_dist
                if (pos.sl > 0 and new_sl >= pos.sl) or (new_sl - tick.ask) < min_stop:
                    continue
            mc.modify_stop(cfg, pos, new_sl, spec.digits, self.dry_run)

    def flatten_before_close(self) -> None:
        """Close everything shortly before the session window ends, and before
        the weekend. The MQL5 EA does this off the broker's real session end;
        here it works off the configured window."""
        cfg = self.cfg
        if not self.market_open:
            return
        now = cfg.session_now()

        if cfg.flatten_before_close and cfg.use_session_filter:
            remaining = cfg.minutes_to_close()
            if 0 <= remaining <= cfg.flatten_before_close_min:
                for pos in mc.get_positions(cfg):
                    log.info("Session close in %.0f min: closing ticket %s",
                             remaining, pos.ticket)
                    mc.close_position(cfg, self.spec, pos, self.dry_run)
                return

        if (cfg.close_before_weekend and now.weekday() == 4
                and now.hour >= cfg.weekend_close_hour):
            for pos in mc.get_positions(cfg):
                log.info("Weekend flatten: closing ticket %s", pos.ticket)
                mc.close_position(cfg, self.spec, pos, self.dry_run)

    # ---------------- main loop ----------------
    def run(self) -> None:
        mode = "DRY-RUN (no orders will be sent)" if self.dry_run else "LIVE TRADING"
        log.info("Bot running in %s. Ctrl+C to stop.", mode)
        while self.running:
            try:
                self.roll_day()
                self.refresh_market_state()
                self.manage_trailing()
                self.flatten_before_close()
                self.check_for_entry()
            except Exception:
                log.exception("Cycle failed; retrying after %.0fs", self.cfg.poll_seconds)
            time.sleep(self.cfg.poll_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true",
                        help="actually place orders (default is dry-run)")
    parser.add_argument("--check", action="store_true",
                        help="connect, print symbol spec and preflight result, exit")
    parser.add_argument("--signal", action="store_true",
                        help="evaluate the three confluences once and exit")
    parser.add_argument("--selftest", action="store_true",
                        help="run the strategy self-test (no MT5 required)")
    parser.add_argument("--unit", choices=["point", "pip", "usd"],
                        help="override distance_unit for SL/trailing distances")
    parser.add_argument("--lots", type=float, help="override the fixed lot size")
    parser.add_argument("--risk-percent", type=float, dest="risk_percent",
                        help="size from equity instead of a fixed lot, risking this %% per trade")
    parser.add_argument("--timeframe", help="working timeframe for entries (default M5)")
    parser.add_argument("--max-positions", type=int, dest="max_positions",
                        help="max positions open at once (default 2)")
    parser.add_argument("--max-trades-per-day", type=int, dest="max_trades_per_day",
                        help="0 = unlimited (the default)")
    parser.add_argument("--daily-target", type=float, dest="daily_target",
                        help="hard daily profit target in %% (0 = off, the default)")
    parser.add_argument("--give-back", type=float, dest="give_back",
                        help="%% of the day's peak gain that may be given back before "
                             "trading stops (100 = lock off)")
    parser.add_argument("--max-daily-loss", type=float, dest="max_daily_loss",
                        help="daily loss cap in %% of the day's opening equity")
    parser.add_argument("--sl-units", type=float, dest="sl_units",
                        help="override stop-loss distance, in --unit units")
    parser.add_argument("--trail-units", type=float, dest="trail_units",
                        help="override trailing distance, in --unit units")
    parser.add_argument("--min-confluences", type=int, dest="min_confluences",
                        choices=[1, 2, 3], help="confluences required to enter (default 2)")
    parser.add_argument("--no-confirmation", action="store_true",
                        help="drop the 'at least one confirmed' requirement")
    parser.add_argument("--require-trend", action="store_true",
                        help="the trend confluence must be one of the passing ones "
                             "(blocks counter-trend entries)")
    parser.add_argument("--min-confirmed", type=int, dest="min_confirmed",
                        choices=[1, 2, 3], help="confirmed confluences required (default 1)")
    parser.add_argument("--min-confidence", type=float, dest="min_confidence",
                        help="minimum setup score 0-100 to enter (default 0 = no gate); "
                             "this is setup quality, not a win probability")
    parser.add_argument("--force", action="store_true",
                        help="trade even if preflight reports problems (not advised)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.selftest:
        import selftest
        return selftest.run()

    overrides = {}
    if args.unit:
        overrides["distance_unit"] = args.unit
    if args.lots:
        overrides["lots"] = args.lots
    if args.sl_units:
        overrides["stop_loss_units"] = args.sl_units
    if args.trail_units:
        overrides["trailing_stop_units"] = args.trail_units
        overrides["trail_start_units"] = args.trail_units
    if args.risk_percent is not None:
        overrides["risk_percent"] = args.risk_percent
        overrides["use_risk_percent"] = args.risk_percent > 0
    if args.timeframe:
        overrides["working_timeframe"] = args.timeframe.upper()
    if args.max_positions is not None:
        overrides["max_open_positions"] = args.max_positions
    if args.max_trades_per_day is not None:
        overrides["max_trades_per_day"] = args.max_trades_per_day
    if args.daily_target is not None:
        overrides["daily_target_pct"] = args.daily_target
        overrides["use_daily_target"] = args.daily_target > 0
    if args.max_daily_loss is not None:
        overrides["max_daily_loss_pct"] = args.max_daily_loss
    if args.give_back is not None:
        overrides["give_back_pct"] = args.give_back
        overrides["lock_daily_gains"] = args.give_back < 100
    if args.min_confluences:
        overrides["min_confluences"] = args.min_confluences
    if args.no_confirmation:
        overrides["require_confirmation"] = False
    if args.require_trend:
        overrides["require_trend_confluence"] = True
    if args.min_confirmed:
        overrides["min_confirmed"] = args.min_confirmed
    if args.min_confidence is not None:
        overrides["min_confidence"] = args.min_confidence
    cfg = TradeConfig.from_env(**overrides)

    bot = Bot(cfg, dry_run=not args.live)
    signal.signal(signal.SIGINT, bot.stop)
    signal.signal(signal.SIGTERM, bot.stop)

    try:
        problems = bot.start(probe_market=not args.signal)

        if args.check:
            print(f"\nMarket: {'OPEN' if bot.market_open else 'CLOSED'}")
            print(f"Symbol: {bot.spec.name}  point={bot.spec.point}  "
                  f"digits={bot.spec.digits}  spread={bot.spec.spread_points} pts  "
                  f"stops_level={bot.spec.stops_level_points} pts")
            print(f"Order:  {cfg.lots} lots  SL {cfg.stop_loss_units:g} {cfg.distance_unit} "
                  f"= ${cfg.sl_distance(bot.spec.point):.2f}  "
                  f"trail {cfg.trailing_stop_units:g} {cfg.distance_unit} "
                  f"= ${cfg.trail_distance(bot.spec.point):.2f}")
            print("Preflight:", "FAILED" if problems else "OK")
            for p in problems:
                print("  *", p)
            if not bot.market_open:
                print("\nNote: spread-based checks are advisory while the market is "
                      "closed. Re-run --check after the session opens.")
            return 1 if problems else 0

        if args.signal:
            sig = bot.latest_signal()
            print(f"\nBar {sig.bar_time}  close={sig.close:.2f}")
            for tag, side in (("BUY", sig.buy), ("SELL", sig.sell)):
                state = lambda ok, conf: "CONFIRMED" if conf else ("pass" if ok else "fail")
                print(f"  {tag}: score {side.confidence:.0f}% | "
                      f"{side.count}/3 confluences, {side.confirmed_count} confirmed"
                      + ("  [VETOED: " + side.veto_reason + "]" if side.vetoed else ""))
                print(f"      trend    {state(side.trend, side.trend_confirmed)}")
                print(f"      momentum {state(side.momentum, side.momentum_confirmed)}")
                print(f"      strength {state(side.strength, side.strength_confirmed)}")
                for k, v in side.reasons.items():
                    print(f"        {k}: {v}")
                print(f"      score parts: trend {side.parts['trend']:.1f} + "
                      f"momentum {side.parts['momentum']:.1f} + "
                      f"strength {side.parts['strength']:.1f}")
                print(f"      qualifies: {side.qualifies(cfg)}")
            print(f"  need >= {cfg.min_confluences}/3 with >= "
                  f"{cfg.min_confirmed if cfg.require_confirmation else 0} confirmed"
                  + (f", score >= {cfg.min_confidence:.0f}%" if cfg.min_confidence else ""))
            print("  (score = setup quality, NOT a probability that the trade wins)")
            print(f"  => {sig.direction or 'no trade'}")
            return 0

        if problems and args.live and not args.force:
            log.error("Refusing to trade live until the preflight problems above are fixed. "
                      "Re-run with --unit usd, or pass --force to override.")
            return 1

        bot.run()
        return 0
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    finally:
        mc.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())

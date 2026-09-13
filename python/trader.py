#!/usr/bin/env python3
"""
XAUUSD confluence trading bot for MetaTrader 5.

Opens 0.02 lots of XAUUSD with the configured stop-loss and manages a trailing
stop, but only when all THREE confluences (trend, momentum, strength) agree.

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
        self.running = True

    # ---------------- lifecycle ----------------
    def start(self) -> list[str]:
        mc.connect(self.cfg)
        self.spec = mc.get_symbol_spec(self.cfg)
        problems = mc.preflight_check(self.cfg, self.spec)
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
            log.info("New trading day - counters reset (equity %.2f)", self.day_start_equity)

        if not self.daily_loss_hit and self.day_start_equity > 0:
            equity = mc.account_equity()
            loss_pct = (self.day_start_equity - equity) / self.day_start_equity * 100.0
            if loss_pct >= self.cfg.max_daily_loss_pct:
                self.daily_loss_hit = True
                log.warning("Daily loss limit hit (%.2f%%) - no new entries today.", loss_pct)

    def entry_blocked(self) -> str | None:
        """Return a reason string if new entries are not allowed right now."""
        cfg = self.cfg
        now = datetime.now()

        if self.daily_loss_hit:
            return "daily loss limit reached"
        if self.trades_today >= cfg.max_trades_per_day:
            return f"max trades/day reached ({self.trades_today})"
        if len(mc.get_positions(cfg)) >= cfg.max_open_positions:
            return "max open positions reached"
        if cfg.use_session_filter and not (cfg.session_start_hour <= now.hour < cfg.session_end_hour):
            return f"outside session {cfg.session_start_hour}:00-{cfg.session_end_hour}:00"
        if cfg.close_before_weekend and now.weekday() == 4 and now.hour >= cfg.weekend_close_hour:
            return "weekend close window"
        spread = mc.get_symbol_spec(cfg).spread_points
        if spread > cfg.max_spread_points:
            return f"spread too wide ({spread} > {cfg.max_spread_points} points)"
        return None

    # ---------------- strategy ----------------
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

        blocked = self.entry_blocked()
        if blocked:
            log.info("Signal %s suppressed: %s", sig.direction.upper(), blocked)
            return

        side = sig.buy if sig.direction == "buy" else sig.sell
        log.info("ALL 3 CONFLUENCES AGREE (%s): %s | %s | %s", sig.direction.upper(),
                 side.reasons["trend"], side.reasons["momentum"], side.reasons["strength"])

        result = mc.open_position(self.cfg, self.spec, sig.direction, self.dry_run)
        self.trades_today += 1
        record_trade({
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bar_time": str(sig.bar_time),
            "direction": sig.direction,
            "lots": self.cfg.lots,
            "price": getattr(result, "price", sig.close),
            "sl_units": self.cfg.stop_loss_units,
            "trail_units": self.cfg.trailing_stop_units,
            "unit": self.cfg.distance_unit,
            "mode": "dry-run" if self.dry_run else "live",
            "retcode": getattr(result, "retcode", ""),
            "ticket": getattr(result, "order", ""),
        })

    # ---------------- trailing stop ----------------
    def manage_trailing(self) -> None:
        cfg, spec = self.cfg, self.spec
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

    def flatten_for_weekend(self) -> None:
        now = datetime.now()
        if not (self.cfg.close_before_weekend and now.weekday() == 4
                and now.hour >= self.cfg.weekend_close_hour):
            return
        for pos in mc.get_positions(self.cfg):
            log.info("Weekend flatten: closing ticket %s", pos.ticket)
            mc.close_position(self.cfg, self.spec, pos, self.dry_run)

    # ---------------- main loop ----------------
    def run(self) -> None:
        mode = "DRY-RUN (no orders will be sent)" if self.dry_run else "LIVE TRADING"
        log.info("Bot running in %s. Ctrl+C to stop.", mode)
        while self.running:
            try:
                self.roll_day()
                self.manage_trailing()
                self.flatten_for_weekend()
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
    parser.add_argument("--lots", type=float, help="override lot size")
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
    cfg = TradeConfig.from_env(**overrides)

    bot = Bot(cfg, dry_run=not args.live)
    signal.signal(signal.SIGINT, bot.stop)
    signal.signal(signal.SIGTERM, bot.stop)

    try:
        problems = bot.start()

        if args.check:
            print("\nPreflight:", "FAILED" if problems else "OK")
            for p in problems:
                print("  *", p)
            return 1 if problems else 0

        if args.signal:
            sig = bot.latest_signal()
            print(f"\nBar {sig.bar_time}  close={sig.close:.2f}")
            for tag, side in (("BUY", sig.buy), ("SELL", sig.sell)):
                print(f"  {tag}: trend={side.trend} momentum={side.momentum} "
                      f"strength={side.strength}  ({side.count}/3)")
                for k, v in side.reasons.items():
                    print(f"      {k}: {v}")
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

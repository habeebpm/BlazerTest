#!/usr/bin/env python3
"""
Orchestration loop: on every newly closed candle on the primary timeframe,
build the full market-intelligence snapshot, ask Claude to validate the
three confluences against it, and execute only when Claude calls "full
conviction". See README.md for setup and `python selftest.py` for the
offline test suite (no MT5 terminal or Anthropic API key needed for that).

Usage:
    python main.py --check                 # connect, print symbol spec, exit
    python main.py --once                  # one evaluation cycle, then exit
    python main.py                         # run continuously (dry-run by default)
    python main.py --live                  # actually send orders
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

import claude_advisor
import executor
import market_intel
import mt5_gateway as gw
from config import AdvisorConfig

log = logging.getLogger("main")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def build_config(args: argparse.Namespace) -> AdvisorConfig:
    cfg = AdvisorConfig()
    if args.symbol:
        cfg.symbol = args.symbol
    if args.lots is not None:
        cfg.fixed_lot = args.lots
    if args.max_positions is not None:
        cfg.max_open_positions_per_direction = args.max_positions
    if args.sl_dollars is not None:
        cfg.sl_dollars = args.sl_dollars
    if args.tp_dollars is not None:
        cfg.tp_arm_dollars = args.tp_dollars
    if args.trail_dollars is not None:
        cfg.trail_dollars = args.trail_dollars
    if args.model:
        cfg.claude_model = args.model
    if args.poll_seconds is not None:
        cfg.poll_seconds = args.poll_seconds
    if args.min_confluence is not None:
        cfg.min_confluence_count = args.min_confluence
    if args.allow_partial_conviction:
        cfg.require_full_conviction = False
    cfg.dry_run = not args.live
    return cfg


class DayRoll:
    """Tracks trades_today and resets it at UTC midnight, same convention as
    the Telegram copier's Copier.roll_day().
    """
    def __init__(self):
        self.date = datetime.now(timezone.utc).date()
        self.trades_today = 0

    def roll(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self.date:
            self.date = today
            self.trades_today = 0


def run_once(client, cfg: AdvisorConfig, spec, day: DayRoll) -> None:
    day.roll()
    features = market_intel.build_feature_snapshot(gw, cfg)
    verdict = claude_advisor.get_verdict(client, cfg, features)
    log.info("Claude verdict: direction=%s conviction=%s confluence=%d/3 - %s",
              verdict.direction, verdict.conviction, verdict.confluence_count, verdict.reasoning)
    decision = executor.execute(gw, cfg, verdict, spec, day.trades_today)
    if decision.executed:
        day.trades_today += 1


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", help="override the traded symbol (default XAUUSD)")
    parser.add_argument("--lots", type=float, help="override fixed lot size (default 0.01)")
    parser.add_argument("--max-positions", type=int, dest="max_positions",
                        help="max concurrent same-direction positions (default 5)")
    parser.add_argument("--sl-dollars", type=float, dest="sl_dollars", help="stop-loss in USD (default 6)")
    parser.add_argument("--tp-dollars", type=float, dest="tp_dollars",
                        help="initial take-profit / trail-arm level in USD (default 6)")
    parser.add_argument("--trail-dollars", type=float, dest="trail_dollars",
                        help="trailing distance once armed, in USD (default 3) - enforced by the "
                             "MQL5 ClaudeSMC_TradeManager EA, not this script; see README.md")
    parser.add_argument("--model", help="Claude model id (default claude-opus-5)")
    parser.add_argument("--min-confluence", type=int, dest="min_confluence",
                        help="minimum agreeing confluences out of 3 (default 2)")
    parser.add_argument("--allow-partial-conviction", action="store_true",
                        help="execute on 'partial' conviction too, not just 'full' (not recommended)")
    parser.add_argument("--poll-seconds", type=int, dest="poll_seconds",
                        help="how often to check for a new closed bar (default 30)")
    parser.add_argument("--live", action="store_true", help="send real orders (default is dry-run)")
    parser.add_argument("--once", action="store_true", help="run a single evaluation cycle and exit")
    parser.add_argument("--check", action="store_true",
                        help="connect to MT5, print the symbol spec, exit - no Claude call")
    parser.add_argument("--login", type=int, help="MT5 account login (optional, if not already logged in)")
    parser.add_argument("--password", help="MT5 account password")
    parser.add_argument("--server", help="MT5 broker server name")
    parser.add_argument("--terminal-path", dest="terminal_path", help="path to terminal64.exe")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    cfg = build_config(args)

    gw.connect(login=args.login, password=args.password, server=args.server,
               terminal_path=args.terminal_path)
    spec = gw.symbol_spec(cfg.symbol)

    if args.check:
        log.info("Connected. Symbol spec for %s: %s", cfg.symbol, spec)
        log.info("Config: lot=%.2f max_same_dir=%d sl=$%.2f tp_arm=$%.2f trail=$%.2f "
                  "min_confluence=%d/3 require_full=%s model=%s dry_run=%s",
                  cfg.fixed_lot, cfg.max_open_positions_per_direction, cfg.sl_dollars,
                  cfg.tp_arm_dollars, cfg.trail_dollars, cfg.min_confluence_count,
                  cfg.require_full_conviction, cfg.claude_model, cfg.dry_run)
        return 0

    if cfg.dry_run:
        log.info("Running in DRY-RUN - no real orders will be sent. Pass --live to trade for real.")

    client = claude_advisor.build_client()
    day = DayRoll()

    if args.once:
        run_once(client, cfg, spec, day)
        return 0

    log.info("Watching %s for a new closed %s candle every %ds - Ctrl+C to stop.",
              cfg.symbol, cfg.primary_timeframe, cfg.poll_seconds)
    last_bar_time = None
    while True:
        try:
            bar_time = market_intel.last_closed_time(gw, cfg.symbol, cfg.primary_timeframe)
            if bar_time != last_bar_time:
                last_bar_time = bar_time
                run_once(client, cfg, spec, day)
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0
        except Exception:
            log.exception("Error during evaluation cycle - will retry next poll")
        time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

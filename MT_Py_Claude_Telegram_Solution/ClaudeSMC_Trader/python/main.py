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
import threading
import time
from datetime import datetime, timedelta, timezone

import claude_advisor
import executor
import market_intel
import mt5_gateway as gw
import telegram_alert
from config import AdvisorConfig

log = logging.getLogger("main")

# How long to back off after a non-retryable Claude failure (out of credits,
# bad API key, permission denied) - these need manual action, so retrying
# every cfg.poll_seconds (default 30s) would just spam the same failure.
CLAUDE_UNAVAILABLE_BACKOFF_SECONDS = 1800


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
    if args.risk_percent is not None:
        cfg.risk_percent = args.risk_percent
        cfg.use_risk_percent = args.risk_percent > 0
    if args.max_daily_loss is not None:
        cfg.max_daily_loss_pct = args.max_daily_loss
    if args.daily_target is not None:
        cfg.daily_target_pct = args.daily_target
        cfg.use_daily_target = args.daily_target > 0
    if args.shared_cap_magic:
        cfg.shared_cap_magic_numbers = [int(m) for m in args.shared_cap_magic.split(",") if m.strip()]
    if args.sl_dollars is not None:
        cfg.sl_dollars = args.sl_dollars
    if args.sl_mode:
        cfg.sl_mode = args.sl_mode
    if args.sl_atr_mult is not None:
        cfg.sl_atr_mult = args.sl_atr_mult
    if args.tp_dollars is not None:
        cfg.tp1_dollars = args.tp_dollars
    if args.trail_dollars is not None:
        cfg.trail_dollars = args.trail_dollars
    if args.exit_style:
        cfg.exit_style = args.exit_style
    if args.breakeven_atr_mult is not None:
        cfg.breakeven_atr_mult = args.breakeven_atr_mult
    if args.breakeven_atr_period is not None:
        cfg.breakeven_atr_period = args.breakeven_atr_period
    if args.decay_window_minutes is not None:
        cfg.decay_window_minutes = args.decay_window_minutes
    if args.telegram_alert_bot_token:
        cfg.telegram_alert_bot_token = args.telegram_alert_bot_token
    if args.telegram_alert_chat_id:
        cfg.telegram_alert_chat_id = args.telegram_alert_chat_id
    if args.no_performance_digest:
        cfg.send_performance_digest = False
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
    the Telegram copier's Copier.roll_day(). Also tracks the day's starting
    account equity so main.py can evaluate the daily loss circuit breaker
    (see config.py's max_daily_loss_pct/use_daily_target/daily_target_pct)
    without executor.py or mt5_gateway.py needing to know about calendar
    days at all - same separation as ../../python/trader.py's Bot.roll_day().
    """
    def __init__(self):
        self.date = datetime.now(timezone.utc).date()
        self.trades_today = 0
        self.day_start_equity = 0.0
        self.daily_loss_hit = False
        self.daily_target_hit = False

    def roll(self, equity: float):
        """Returns the date that just ended if today is a genuinely new UTC
        day (so callers can fire a once-per-day digest - see main.py's
        send_performance_digests()), else None. Never returns a date on the
        very first call (process just started - there's no "previous day"
        to digest yet, only an anchor point)."""
        today = datetime.now(timezone.utc).date()
        if today != self.date:
            ended_date = self.date
            self.date = today
            self.trades_today = 0
            self.day_start_equity = equity
            self.daily_loss_hit = False
            self.daily_target_hit = False
            return ended_date
        if self.day_start_equity <= 0:
            # First cycle ever (process just started mid-day) - anchor here
            # rather than waiting for the next UTC midnight.
            self.day_start_equity = equity
        return None

    def check_daily_limits(self, cfg: AdvisorConfig, equity: float) -> None:
        if self.day_start_equity <= 0:
            return
        move_pct = (equity - self.day_start_equity) / self.day_start_equity * 100.0
        if (cfg.max_daily_loss_pct > 0 and not self.daily_loss_hit
                and -move_pct >= cfg.max_daily_loss_pct):
            self.daily_loss_hit = True
            log.warning("Daily loss limit hit (%.2f%% <= -%.2f%%) - no new entries until the "
                        "next UTC day.", move_pct, cfg.max_daily_loss_pct)
        if (cfg.use_daily_target and not self.daily_target_hit
                and move_pct >= cfg.daily_target_pct):
            self.daily_target_hit = True
            log.info("Daily profit target reached (+%.2f%% >= +%.2f%%) - no new entries until "
                     "the next UTC day.", move_pct, cfg.daily_target_pct)

    def block_reason(self) -> str:
        if self.daily_loss_hit:
            return "daily loss circuit breaker triggered"
        if self.daily_target_hit:
            return "daily profit target already reached"
        return ""


def send_performance_digests(cfg: AdvisorConfig, ended_date) -> None:
    """Fires once per UTC day roll (see DayRoll.roll()) with a digest of the
    day that just ended, plus a weekly digest too on the Sunday->Monday
    roll. Reuses mt5_gateway.recent_closed_trades() (see #69's own comment)
    rather than logs/trades.csv, so it reflects real broker fills whether
    or not this process was running the whole time. Runs in a daemon
    thread, same reasoning as the full-conviction alert just above -
    a slow/unreachable Telegram API must never delay the next poll cycle.
    """
    def _send():
        # count=500/2000 are generous ceilings, not real limits - a manual
        # trading system won't produce anywhere near that many trades in a
        # day or week; lookback_days pads a couple of days past the window
        # being reported on purely to tolerate clock/timezone edge cases,
        # the exact date filter below does the real work.
        daily_trades = [t for t in gw.recent_closed_trades(cfg.symbol, cfg.magic, count=500,
                                                            lookback_days=2)
                        if t["time"].date() == ended_date]
        daily_msg = telegram_alert.format_performance_digest(cfg.symbol, "Daily", daily_trades)
        telegram_alert.send_alert(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, daily_msg)

        if ended_date.weekday() == 6:  # Sunday just ended - the UTC week (Mon-Sun) just completed
            week_start = ended_date - timedelta(days=6)
            weekly_trades = [t for t in gw.recent_closed_trades(cfg.symbol, cfg.magic, count=2000,
                                                                 lookback_days=9)
                            if week_start <= t["time"].date() <= ended_date]
            weekly_msg = telegram_alert.format_performance_digest(cfg.symbol, "Weekly", weekly_trades)
            telegram_alert.send_alert(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id,
                                      weekly_msg)

    threading.Thread(target=_send, daemon=True).start()


def run_once(client, cfg: AdvisorConfig, spec, day: DayRoll) -> None:
    equity = gw.account_equity()
    ended_date = day.roll(equity)
    if ended_date is not None and cfg.send_performance_digest and cfg.telegram_alert_bot_token \
            and cfg.telegram_alert_chat_id:
        send_performance_digests(cfg, ended_date)
    day.check_daily_limits(cfg, equity)
    features = market_intel.build_feature_snapshot(gw, cfg)
    verdict = claude_advisor.get_verdict(client, cfg, features)
    log.info("Claude verdict: direction=%s conviction=%s confluence=%d/3 - %s",
              verdict.direction, verdict.conviction, verdict.confluence_count, verdict.reasoning)
    decision = executor.execute(gw, cfg, verdict, spec, day.trades_today, day.block_reason())
    if decision.executed:
        day.trades_today += 1
    if verdict.conviction == "full" and verdict.direction in ("buy", "sell"):
        # Fires on EVERY full-conviction verdict, whether or not it actually
        # executed - gate() can still reject it (position cap, daily trade
        # limit, confluence floor); the message says so either way. Excludes
        # direction="none" (schema-legal alongside conviction="full", though
        # SYSTEM_PROMPT says not to produce it) since gate() always rejects
        # it as "no actionable direction" anyway - not worth an alert. A
        # no-op (and never raises) if telegram_alert_bot_token/chat_id
        # aren't configured - see config.py. Sent from a daemon thread, not
        # inline, so a slow/unreachable Telegram API (up to the 10s urlopen
        # timeout) never delays the next poll cycle - send_alert() itself
        # never raises, so there's nothing here to join or catch.
        message = telegram_alert.format_full_conviction_message(
            cfg.symbol, verdict, decision.executed, decision.reject_reason)
        threading.Thread(
            target=telegram_alert.send_alert,
            args=(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, message),
            daemon=True,
        ).start()


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", help="override the traded symbol (default XAUUSD)")
    parser.add_argument("--lots", type=float, help="override fixed lot size (default 0.01)")
    parser.add_argument("--max-positions", type=int, dest="max_positions",
                        help="max concurrent same-direction positions (default 5)")
    parser.add_argument("--shared-cap-magic", dest="shared_cap_magic",
                        help="comma-separated extra magic number(s) to fold into --max-positions' own "
                             "count (default: none - the cap only counts this system's own magic) - set "
                             "this to the UnifiedTrader EA's InpTelegramMagicNumber if you want the two "
                             "sources to share one combined per-direction cap instead of 5 each; see "
                             "config.py's AdvisorConfig.shared_cap_magic_numbers")
    parser.add_argument("--risk-percent", type=float, dest="risk_percent",
                        help="size each trade from equity instead of a fixed lot, risking this pct "
                             "of equity per trade (default: unset = always trade --lots); pass 0 to "
                             "explicitly disable; see config.py's use_risk_percent")
    parser.add_argument("--max-daily-loss", type=float, dest="max_daily_loss",
                        help="stop new entries after the account is down this many pct on the "
                             "UTC day (default 0 = disabled) - existing positions are left alone, "
                             "ClaudeSMC_TradeManager.mq5 keeps managing them; see config.py")
    parser.add_argument("--daily-target", type=float, dest="daily_target",
                        help="stop new entries once the account is up this many pct on the UTC "
                             "day (default: unset = disabled) - pass 0 to explicitly disable")
    parser.add_argument("--sl-dollars", type=float, dest="sl_dollars", help="stop-loss in USD (default 6)")
    parser.add_argument("--sl-mode", choices=["fixed", "atr"], dest="sl_mode",
                        help="default 'fixed' (always --sl-dollars); 'atr' derives the entry stop "
                             "from recent ATR x --sl-atr-mult instead, clamped to "
                             "[sl_dollars_min, sl_dollars_max] - entry SL only, tp1/trail stay "
                             "fixed dollar amounts either way (see config.py)")
    parser.add_argument("--sl-atr-mult", type=float, dest="sl_atr_mult",
                        help="--sl-mode=atr only: stop = ATR x this multiplier (default 1.5)")
    parser.add_argument("--tp-dollars", type=float, dest="tp_dollars",
                        help="TP1 level in USD (default 6) - the profit level that locks in the "
                             "stop-loss (exit_style=sl_to_tp1) or the fixed broker take-profit "
                             "(exit_style=fixed_tp)")
    parser.add_argument("--trail-dollars", type=float, dest="trail_dollars",
                        help="trailing distance once armed, in USD (default 3) - enforced by the "
                             "MQL5 ClaudeSMC_TradeManager EA, not this script; see README.md")
    parser.add_argument("--exit-style", choices=["sl_to_tp1", "breakeven_r_decay", "fixed_tp"],
                        dest="exit_style",
                        help="default sl_to_tp1 (see config.py's module docstring for why); "
                             "breakeven_r_decay adds an earlier breakeven step before the same "
                             "tp1/trail lock, both live in the MQL5 EAs (InpExitStyle - keep it in "
                             "sync by hand); fixed_tp is only implemented here and in backtest.py "
                             "--compare, not in either live MQL5 EA")
    parser.add_argument("--breakeven-atr-mult", type=float, dest="breakeven_atr_mult",
                        help="exit_style=breakeven_r_decay only: move SL to breakeven once profit "
                             "reaches this x the position's own M5 ATR (default 0.5) - enforced by "
                             "the MQL5 EA's InpBreakevenAtrMult, not this script; see README.md")
    parser.add_argument("--breakeven-atr-period", type=int, dest="breakeven_atr_period",
                        help="exit_style=breakeven_r_decay only: ATR period for the breakeven trigger "
                             "(default 14) - enforced by the MQL5 EA's InpAtrPeriod, not this script")
    parser.add_argument("--decay-window-minutes", type=float, dest="decay_window_minutes",
                        help="exit_style=breakeven_r_decay only: force the SL to breakeven after this "
                             "many minutes even short of the ATR trigger (default 15) - enforced by "
                             "the MQL5 EA's InpDecayWindowMinutes, not this script")
    parser.add_argument("--telegram-alert-bot-token", dest="telegram_alert_bot_token",
                        help="bot token from @BotFather - sends a one-way Telegram message on every "
                             "'full' conviction verdict, executed or not (default: unset, alerts off). "
                             "See README.md for setup; see telegram_alert.py for what's sent")
    parser.add_argument("--telegram-alert-chat-id", dest="telegram_alert_chat_id",
                        help="chat id to send full-conviction alerts to (default: unset, alerts off)")
    parser.add_argument("--no-performance-digest", action="store_true", dest="no_performance_digest",
                        help="disable the daily/weekly performance digest (on by default once "
                             "telegram-alert-bot-token/chat-id are set - see config.py)")
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
    if cfg.shared_cap_magic_numbers:
        log.warning(
            "shared_cap_magic_numbers=%s is set - this only folds those magics' positions into THIS "
            "process's own count_same_direction() check. The other side (UnifiedTrader_EA.mq5's own "
            "InpMaxPositionsPerDirection) is a SEPARATE number in a separate file - it must be set to "
            "the SAME value as --max-positions/max_open_positions_per_direction (%d) or the 'shared' "
            "cap silently becomes asymmetric (whichever side has the lower number stops first, the "
            "other keeps opening past it). Nothing here can verify that for you - check it by hand.",
            cfg.shared_cap_magic_numbers, cfg.max_open_positions_per_direction)

    gw.connect(login=args.login, password=args.password, server=args.server,
               terminal_path=args.terminal_path)
    spec = gw.symbol_spec(cfg.symbol)

    if args.check:
        log.info("Connected. Symbol spec for %s: %s", cfg.symbol, spec)
        log.info("Config: lot=%s max_same_dir=%d shared_cap_magics=%s sl_mode=%s sl=$%.2f tp1=$%.2f trail=$%.2f "
                  "exit_style=%s (breakeven_atr_mult=%.2f breakeven_atr_period=%d decay_window_minutes=%.1f) "
                  "min_confluence=%d/3 require_full=%s max_daily_loss=%s daily_target=%s model=%s "
                  "dry_run=%s telegram_alerts=%s performance_digest=%s",
                  f"risk {cfg.risk_percent:g}% of equity (max {cfg.max_lot_size:g})"
                  if cfg.use_risk_percent else f"fixed {cfg.fixed_lot:g}",
                  cfg.max_open_positions_per_direction, cfg.shared_cap_magic_numbers,
                  f"atr(x{cfg.sl_atr_mult:g})" if cfg.sl_mode == "atr" else "fixed",
                  cfg.sl_dollars, cfg.tp1_dollars, cfg.trail_dollars, cfg.exit_style,
                  cfg.breakeven_atr_mult, cfg.breakeven_atr_period, cfg.decay_window_minutes,
                  cfg.min_confluence_count, cfg.require_full_conviction,
                  f"{cfg.max_daily_loss_pct:g}%" if cfg.max_daily_loss_pct > 0 else "off",
                  f"{cfg.daily_target_pct:g}%" if cfg.use_daily_target else "off",
                  cfg.claude_model, cfg.dry_run,
                  "on" if (cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id) else "off",
                  "on" if (cfg.send_performance_digest and cfg.telegram_alert_bot_token
                           and cfg.telegram_alert_chat_id) else "off")
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
    sleep_seconds = cfg.poll_seconds
    while True:
        try:
            bar_time = market_intel.last_closed_time(gw, cfg.symbol, cfg.primary_timeframe)
            if bar_time != last_bar_time:
                # last_bar_time only advances AFTER a successful cycle - if
                # run_once() raises (Claude down, MT5 hiccup, ...), this same
                # bar is retried on the next poll instead of being silently
                # skipped forever.
                run_once(client, cfg, spec, day)
                last_bar_time = bar_time
            sleep_seconds = cfg.poll_seconds
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0
        except claude_advisor.ClaudeUnavailableError as exc:
            if exc.retryable:
                log.warning("Claude temporarily unavailable this cycle - %s", exc)
                sleep_seconds = cfg.poll_seconds
            else:
                log.warning(
                    "Claude unavailable and this looks like it needs manual action (credits/API "
                    "key/permissions) rather than a retry - backing off to every %d minutes "
                    "instead of polling every %ds until it's fixed. No new signals will be "
                    "evaluated in the meantime, but ClaudeSMC_TradeManager.mq5 keeps managing any "
                    "already-open positions on its own, and the wholly independent Telegram "
                    "copier stack (../../python/, ../../MQL5/) is completely unaffected - run it "
                    "standalone if you want trading to continue while this is down. Reason: %s",
                    CLAUDE_UNAVAILABLE_BACKOFF_SECONDS // 60, cfg.poll_seconds, exc)
                sleep_seconds = CLAUDE_UNAVAILABLE_BACKOFF_SECONDS
        except Exception:
            log.exception("Error during evaluation cycle - will retry next poll")
            sleep_seconds = cfg.poll_seconds
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Orchestration loop: on every newly closed candle on the primary timeframe,
build the full market-intelligence snapshot, ask Claude to validate the
three confluences against it, and execute only when Claude calls "full
conviction". See docs/REFERENCE.md for setup and `python selftest.py` for the
offline test suite (no MT5 terminal or Anthropic API key needed for that).

Usage:
    python main.py --check                 # connect, print symbol spec, exit
    python main.py --once                  # one evaluation cycle, then exit
    python main.py                         # run continuously (dry-run by default)
    python main.py --live                  # actually send orders
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import claude_advisor
import executor
import first_run
import keys
import market_intel
import ml_advisor
import mt5_gateway as gw
import news_check
import relay_supervisor
import services
import tactics
import telegram_alert
import xtr_logic
from config import AdvisorConfig

log = logging.getLogger("main")

# How long to back off after a non-retryable Claude failure (out of credits,
# bad API key, permission denied) - these need manual action, so retrying
# every cfg.poll_seconds (default 30s) would just spam the same failure.
CLAUDE_UNAVAILABLE_BACKOFF_SECONDS = 1800

# An unexpected error retries the same closed bar at most this many times.
MAX_ATTEMPTS_PER_BAR = 3


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
        # An explicit lot means "trade exactly this lot" unless --risk-percent
        # is ALSO given (handled just below, which re-enables risk sizing).
        cfg.use_risk_percent = False
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
    # Flags win; otherwise the TELEGRAM_ALERT_BOT_TOKEN/TELEGRAM_ALERT_CHAT_ID
    # environment variables (keeps the token off the command line).
    cfg.telegram_alert_bot_token = (args.telegram_alert_bot_token
                                    or os.environ.get("TELEGRAM_ALERT_BOT_TOKEN", "")
                                    or cfg.telegram_alert_bot_token)
    cfg.telegram_alert_chat_id = (args.telegram_alert_chat_id
                                  or os.environ.get("TELEGRAM_ALERT_CHAT_ID", "")
                                  or cfg.telegram_alert_chat_id)
    if args.no_performance_digest:
        cfg.send_performance_digest = False
    if args.heartbeat_hours is not None:
        cfg.heartbeat_interval_hours = args.heartbeat_hours
    if args.stale_cycle_minutes is not None:
        cfg.stale_cycle_alert_minutes = args.stale_cycle_minutes
    if args.model:
        cfg.claude_model = args.model
    if args.poll_seconds is not None:
        cfg.poll_seconds = args.poll_seconds
    if args.no_news_blackout:
        cfg.news_auto_blackout = False
    if args.no_news_check:
        cfg.breaking_news_check = False
    if args.news_check_web_search and not args.news_check_no_web_search:
        cfg.news_check_web_search = True
    if args.news_check_fail_closed:
        cfg.news_check_fail_closed = True
    if args.xtr_gate:
        cfg.xtr_gate = args.xtr_gate
    if args.min_confluence is not None:
        cfg.min_confluence_count = args.min_confluence
    if args.allow_partial_conviction:
        cfg.require_full_conviction = False
    if args.no_prescreen:
        cfg.claude_prescreen = False
    if args.trade_hours is not None:
        cfg.trade_windows_ny = "" if args.trade_hours.strip().lower() in ("", "any", "off") \
            else args.trade_hours
    if args.friday_cutoff is not None:
        cfg.friday_cutoff_ny = "" if args.friday_cutoff.strip().lower() in ("", "off") \
            else args.friday_cutoff
    if args.max_spread is not None:
        cfg.max_spread_points = args.max_spread
    if args.min_adx is not None:
        cfg.min_adx = args.min_adx
    tactics.validate(cfg)   # a typo stops here, not silently later
    cfg.dry_run = not args.live
    return cfg


class DayRoll:
    """Tracks trades_today and the day's starting account equity (the daily
    loss circuit breaker - see config.py's max_daily_loss_pct/
    use_daily_target/daily_target_pct) per TRADING day: the broker server's
    day, exactly like UnifiedTrader_EA's own daily cap, so both sources'
    caps roll at the same moment (tactics.trading_day(); server midnight is
    17:00 New York at the usual gold brokers). The server's UTC offset is
    learned from live ticks (observe_server_offset) and kept across
    restarts. The day only ever moves forward.
    """
    def __init__(self, state_path: str | None = None):
        self.server_offset = None
        self._offset_candidate = None
        saved = None
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    saved = json.load(f)
                offset = saved.get("server_offset")
                self.server_offset = int(offset) if offset is not None else None
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                log.warning("Could not read %s (%s) - starting today's state fresh.", state_path, exc)
                saved = None
        self.date = self.today()
        self.trades_today = 0
        self.day_start_equity = 0.0
        self.daily_loss_hit = False
        self.daily_target_hit = False
        # Persisted so a restart later the same trading day keeps its anchor,
        # latched breaker and trade count - re-anchoring on every restart
        # would hand a down-9% day a fresh 10% budget.
        self.state_path = state_path
        if saved is not None:
            try:
                if saved.get("date") == self.date.isoformat():
                    self.trades_today = int(saved.get("trades_today", 0))
                    self.day_start_equity = float(saved.get("day_start_equity", 0.0))
                    self.daily_loss_hit = bool(saved.get("daily_loss_hit", False))
                    self.daily_target_hit = bool(saved.get("daily_target_hit", False))
                    log.info("Restored today's state: day-start equity %.2f, %d trade(s)%s.",
                             self.day_start_equity, self.trades_today,
                             ", daily loss breaker TRIGGERED" if self.daily_loss_hit else "")
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                log.warning("Could not read %s (%s) - starting today's state fresh.", state_path, exc)

    def save(self) -> None:
        if not self.state_path:
            return
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            # temp file + os.replace: a crash mid-write can never leave a
            # half-written file that would reset a triggered breaker.
            tmp_path = self.state_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump({"date": self.date.isoformat(), "trades_today": self.trades_today,
                           "day_start_equity": self.day_start_equity,
                           "daily_loss_hit": self.daily_loss_hit,
                           "daily_target_hit": self.daily_target_hit,
                           "server_offset": self.server_offset}, f)
            os.replace(tmp_path, self.state_path)
        except OSError as exc:
            log.warning("Could not save %s (%s) - a restart today would re-anchor the daily cap.",
                        self.state_path, exc)

    def today(self):
        return tactics.trading_day(datetime.now(timezone.utc), self.server_offset)

    def observe_server_offset(self, offset: int | None) -> None:
        """The broker's UTC offset from a live tick (mt5_gateway.
        server_utc_offset_seconds). Adopted once two readings in a row agree,
        so one stale tick can never move the day boundary."""
        if offset is None or offset == self.server_offset:
            self._offset_candidate = None
            return
        if offset != self._offset_candidate:
            self._offset_candidate = offset
            return
        log.info("Broker server time is UTC%+.1fh - the daily cap rolls at server midnight, like the EA's.",
                 offset / 3600)
        self.server_offset, self._offset_candidate = offset, None
        self.save()

    def roll(self, equity: float):
        """Returns the (gap_start, gap_end) INCLUSIVE trading-day range that
        needs a digest if today is a genuinely new trading day, else None -
        see main.py's send_performance_digests(). Ordinarily gap_start ==
        gap_end == yesterday, but if the poll loop was down across more
        than one day boundary (self.date only advances while it's actually
        running), gap_start is the last day it was tracking and gap_end is
        the day before today, so a caller can still account for every day
        in between rather than silently dropping all but one of them.
        Never returns a range on the very first call (process just
        started - there's no "previous day" to digest yet, only an anchor
        point)."""
        today = self.today()
        if today > self.date:
            gap_start = self.date
            gap_end = today - timedelta(days=1)
            self.date = today
            self.trades_today = 0
            self.day_start_equity = equity
            self.daily_loss_hit = False
            self.daily_target_hit = False
            return (gap_start, gap_end)
        if self.day_start_equity <= 0:
            # First cycle ever (process just started mid-day) - anchor here
            # rather than waiting for the next day boundary.
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
                        "next trading day.", move_pct, cfg.max_daily_loss_pct)
        if (cfg.use_daily_target and not self.daily_target_hit
                and move_pct >= cfg.daily_target_pct):
            self.daily_target_hit = True
            log.info("Daily profit target reached (+%.2f%% >= +%.2f%%) - no new entries until "
                     "the next trading day.", move_pct, cfg.daily_target_pct)

    def block_reason(self) -> str:
        if self.daily_loss_hit:
            return "daily loss circuit breaker triggered"
        if self.daily_target_hit:
            return "daily profit target already reached"
        return ""


class Heartbeat:
    """Tracks two independent, low-noise Telegram signals about the poll
    loop's own health (see config.py's heartbeat_interval_hours/
    stale_cycle_alert_minutes) - completely separate from anything about
    trading signals, performance, or the daily/weekly digest above.
    """
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.last_heartbeat_sent = now
        self.last_successful_cycle = now
        self.stale_alert_sent = False

    def mark_cycle_success(self) -> None:
        self.last_successful_cycle = datetime.now(timezone.utc)
        self.stale_alert_sent = False

    def minutes_since_last_success(self) -> float:
        return (datetime.now(timezone.utc) - self.last_successful_cycle).total_seconds() / 60.0

    def due_heartbeat(self, cfg: AdvisorConfig) -> bool:
        if cfg.heartbeat_interval_hours <= 0:
            return False
        elapsed_hours = (datetime.now(timezone.utc) - self.last_heartbeat_sent).total_seconds() / 3600.0
        return elapsed_hours >= cfg.heartbeat_interval_hours

    def mark_heartbeat_sent(self) -> None:
        self.last_heartbeat_sent = datetime.now(timezone.utc)

    def due_stale_alert(self, cfg: AdvisorConfig) -> bool:
        if cfg.stale_cycle_alert_minutes <= 0 or self.stale_alert_sent:
            return False
        return self.minutes_since_last_success() >= cfg.stale_cycle_alert_minutes

    def mark_stale_alert_sent(self) -> None:
        self.stale_alert_sent = True


def digest_lookback_days(gap_start, now=None) -> int:
    """How many days of MT5 deal history send_performance_digests() needs
    to fetch to be sure it reaches back to gap_start, even after a
    multi-day process outage (DayRoll.date only advances while the poll
    loop is actually running, so a restart after several days down can
    hand roll() a gap_start well in the past).
    """
    now = now or datetime.now(timezone.utc)
    return max(0, (now.date() - gap_start).days)


def sundays_in_range(gap_start, gap_end) -> list:
    """Every UTC Sunday in the inclusive [gap_start, gap_end] range -
    send_performance_digests() sends one weekly digest per Sunday found
    here, so a UTC week boundary crossed entirely during a multi-day
    outage still gets its own digest rather than being silently skipped.
    """
    return [gap_start + timedelta(days=i) for i in range((gap_end - gap_start).days + 1)
            if (gap_start + timedelta(days=i)).weekday() == 6]


def send_performance_digests(cfg: AdvisorConfig, gap_start, gap_end,
                             equity: float | None = None, server_offset: int | None = None) -> None:
    """Fires once per trading-day roll (see DayRoll.roll()) with a digest of
    every day in the inclusive [gap_start, gap_end] range - ordinarily a
    single day (yesterday), but after a multi-day process outage this can
    span several days, and every UTC Sunday found in that range gets its
    own weekly digest too, so nothing between the last successful cycle
    and recovery is silently dropped. Reuses mt5_gateway.
    recent_closed_trades() (see #69's own comment) rather than
    logs/trades.csv, so it reflects real broker fills whether or not this
    process was running the whole time.

    The MT5 query itself runs INLINE, on the caller's thread (run_once()'s,
    i.e. the main loop's) - the MetaTrader5 package's IPC connection to the
    terminal is not safe for concurrent calls from multiple threads, and
    the main loop is making its own MT5 calls (build_feature_snapshot(),
    executor.execute()) in this same cycle, right after this returns.
    Only the actual Telegram network send is threaded, same reasoning as
    the full-conviction alert just above - a slow/unreachable Telegram API
    must never delay the next poll cycle, but the local MT5 IPC call is
    fast and must never race with the rest of this cycle's own MT5 calls.

    By the time this is called, DayRoll.roll() has already advanced
    day.date - there is no way to retry this specific gap on the next
    cycle if the MT5 query below fails, so (unlike leaving it to the
    generic except-Exception path several frames up, which would also
    abort this cycle's Claude evaluation/trade execution that runs right
    after run_once() calls this) a transient MT5 hiccup here is caught and
    logged, same reasoning as dxy_context()/consensus_context()/
    recent_performance_summary() - the digest for this gap is lost either
    way, but nothing else about the cycle should be.
    """
    try:
        # A week can start well before gap_start (e.g. a Sunday gap needs
        # the preceding Monday too), so the lookback has to reach back to
        # whichever is earlier - gap_start, or the start of the earliest
        # week being reported on - not gap_start alone.
        sundays = sundays_in_range(gap_start, gap_end)
        earliest_needed = min(gap_start, sundays[0] - timedelta(days=6)) if sundays else gap_start
        # count=500*however many days are generous ceilings, not real
        # limits - a manual trading system won't produce anywhere near
        # that many trades; lookback_days needs to reach back far enough
        # to cover earliest_needed even after a multi-day process outage
        # (exactly the scenario the heartbeat/stale-cycle alert exists to
        # catch) - the exact date-range filters below do the real work
        # either way, this just has to fetch far ENOUGH history.
        lookback_days = digest_lookback_days(earliest_needed) + 2
        gap_days = (gap_end - gap_start).days + 1
        all_trades = gw.recent_closed_trades(cfg.symbol, cfg.magic, count=max(500, 500 * gap_days),
                                             lookback_days=lookback_days)
    except Exception as exc:
        log.warning("send_performance_digests: could not read trade history for %s to %s (%s) - "
                    "this digest is lost, but the rest of the cycle continues.",
                    gap_start, gap_end, exc)
        return

    def day_of(t):
        return tactics.trading_day(t["time"], server_offset)

    gap_trades = [t for t in all_trades if gap_start <= day_of(t) <= gap_end]
    period_label = "Daily" if gap_start == gap_end else f"{gap_start} to {gap_end}"
    daily_msg = telegram_alert.format_performance_digest(cfg.symbol, period_label, gap_trades, equity)

    weekly_msgs = []
    for week_end in sundays:
        week_start = week_end - timedelta(days=6)
        weekly_trades = [t for t in all_trades if week_start <= day_of(t) <= week_end]
        weekly_msgs.append(telegram_alert.format_performance_digest(cfg.symbol, "Weekly", weekly_trades,
                                                                    equity))

    def _send():
        telegram_alert.send_alert(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, daily_msg)
        for msg in weekly_msgs:
            telegram_alert.send_alert(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, msg)

    threading.Thread(target=_send, daemon=True).start()


CLAUDE_PAUSED_TEXT = ("Claude entries are PAUSED from Telegram (PauseClaudeHab/PauseHab) - no "
                      "evaluation until ResumeClaudeHab or ResumeHab.")


_last_pause_state = {"paused": False}


def claude_paused(cfg: AdvisorConfig, gateway=gw, retry_delay: float = 0.2) -> bool:
    """True while UnifiedTrader_EA.mq5's pause file says "paused" - see
    config.py's claude_pause_filename. No file at all (the EA never wrote
    one) counts as running. A read that fails or returns something other
    than "paused"/"running" - e.g. caught mid-write by the EA - is retried
    once, then falls back to the LAST state actually read: a hiccup must
    neither lift a pause nor invent one."""
    if not cfg.claude_pause_filename:
        return False
    problem = ""
    for attempt in range(2):
        try:
            text = gateway.read_common_file(cfg.claude_pause_filename)
        except Exception as exc:
            problem = f"read failed: {exc}"
        else:
            if text is None:
                _last_pause_state["paused"] = False
                return False
            state = text.strip().lower()
            if state in ("paused", "running"):
                _last_pause_state["paused"] = state == "paused"
                return _last_pause_state["paused"]
            problem = f"unexpected content {text[:20]!r}"
        if attempt == 0 and retry_delay > 0:
            time.sleep(retry_delay)
    log.warning("Claude pause file %r: %s - keeping the last known state (%s).",
                cfg.claude_pause_filename, problem,
                "paused" if _last_pause_state["paused"] else "running")
    return _last_pause_state["paused"]


_xtr_warned = {"at": 0.0}


def xtr_assessment(cfg: AdvisorConfig):
    """The XTR M5/M15/H1 reading (context for Claude whatever the gate), or
    None when the bars are unavailable - logged at most every 30 minutes;
    the gate is then skipped, never blocking trading on a data hiccup)."""
    try:
        return xtr_logic.assess(gw, cfg.symbol, cfg.xtr_bars)
    except Exception as exc:
        if time.time() - _xtr_warned["at"] > 1800:
            _xtr_warned["at"] = time.time()
            log.warning("XTR alignment unavailable this cycle (%s) - its gate is skipped.", exc)
        return None


def xtr_skip_reason(cfg: AdvisorConfig, a) -> str:
    """Before the paid Claude call: "" unless the XTR gate would reject
    EVERY direction anyway (M15 and H1 clearly against each other, or - in
    require_alignment mode - no M5 trigger either way)."""
    if a is None or cfg.xtr_gate == "off":
        return ""
    if all(xtr_logic.grade_conviction(d, a.m15_class, a.h1_class) == xtr_logic.OPPOSED
           for d in ("buy", "sell")):
        return f"XTR: M15 {a.m15_class} vs H1 {a.h1_class} - every direction is against a clear HTF"
    if cfg.xtr_gate == "require_alignment" and a.m5_direction is None and a.bounce_direction is None:
        return "XTR: no clean M5 trigger in either direction"
    return ""


def skip_cycle(cfg: AdvisorConfig, reason: str) -> None:
    """A cycle that ends before the Claude call: log it and leave the reason
    for the EA's "Why" button."""
    log.info("No evaluation this cycle (no Claude call): %s", reason)
    if cfg.last_verdict_filename:
        try:
            gw.write_common_file(cfg.last_verdict_filename, f"No evaluation this cycle: {reason}")
        except Exception:
            log.debug("Could not update the last-verdict file.", exc_info=True)


def run_once(client, cfg: AdvisorConfig, spec, day: DayRoll, xtr_state=None) -> None:
    equity = gw.account_equity()
    offset_fn = getattr(gw, "server_utc_offset_seconds", None)
    if offset_fn is not None:
        day.observe_server_offset(offset_fn(cfg.symbol))
    gap = day.roll(equity)
    if gap is not None and cfg.send_performance_digest and cfg.telegram_alert_bot_token \
            and cfg.telegram_alert_chat_id:
        send_performance_digests(cfg, gap[0], gap[1], equity, server_offset=day.server_offset)
    day.check_daily_limits(cfg, equity)
    day.save()
    # Everything that doesn't depend on Claude's answer is checked BEFORE
    # the snapshot and the paid Claude call: a pause, the daily breaker/
    # target, a news blackout, the daily trade limit. Open positions keep
    # being managed by the EA either way.
    skip_reason = (CLAUDE_PAUSED_TEXT if claude_paused(cfg) else
                   day.block_reason() or executor.verdict_independent_block(gw, cfg, day.trades_today))
    xtr_a = None
    if not skip_reason:
        xtr_a = xtr_assessment(cfg)
        skip_reason = xtr_skip_reason(cfg, xtr_a)
    if skip_reason:
        skip_cycle(cfg, skip_reason)
        return
    features = market_intel.build_feature_snapshot(gw, cfg)
    skip_reason = tactics.regime_block(cfg, features) or tactics.prescreen_block(cfg, features)
    if skip_reason:
        skip_cycle(cfg, skip_reason)
        return
    features["xtr"] = xtr_logic.snapshot_context(xtr_a, cfg.xtr_gate)
    verdict = claude_advisor.get_verdict(client, cfg, features)
    log.info("Claude verdict: direction=%s conviction=%s confluence=%d/3 - %s",
              verdict.direction, verdict.conviction, verdict.confluence_count, verdict.reasoning)
    xtr_decision = None
    if xtr_a is not None and verdict.direction in ("buy", "sell"):
        if xtr_state is not None and cfg.xtr_gate != "off":
            try:
                xtr_state.update_from_closed(gw.recent_closed_trades(cfg.symbol, cfg.magic, count=50))
            except Exception:
                log.debug("Could not update the XTR stand-down state.", exc_info=True)
            xtr_state.release_if_due(xtr_a)
        xtr_decision = xtr_logic.evaluate(verdict.direction, xtr_a, cfg, xtr_state)
        log.info("XTR: %s%s", xtr_decision.summary(),
                 f" - {xtr_decision.block_reason}" if xtr_decision.block_reason else "")
    # Re-checked right before ordering: the Claude call takes seconds, and a
    # PauseClaudeHab/PauseHab tapped meanwhile must still stop this entry.
    block = day.block_reason() or (
        "paused from Telegram (PauseClaudeHab/PauseHab) during this evaluation"
        if claude_paused(cfg) else "")

    def pre_trade_check(direction: str, plan) -> tuple[str, str]:
        # Only reached by an entry that cleared every other gate: look for
        # surprise breaking news (news_check.py), then re-check the pause,
        # since the news check itself can take tens of seconds.
        result = news_check.check_before_trade(client, cfg, direction, plan.entry_price)
        if result.block_reason:
            return result.block_reason, result.note
        if claude_paused(cfg):
            return "paused from Telegram (PauseClaudeHab/PauseHab) during the news check", result.note
        return "", result.note

    decision = executor.execute(gw, cfg, verdict, spec, day.trades_today, block,
                                day_start_equity=day.day_start_equity,
                                pre_trade_check=pre_trade_check, xtr=xtr_decision)
    if decision.executed:
        day.trades_today += 1
        day.save()
    # From here on nothing may raise: main() retries a bar whose cycle raised,
    # which after a sent order would ask Claude again and could open a
    # second position. Bookkeeping and alerts are logged and skipped instead.
    try:
        after_decision(cfg, verdict, decision, features, xtr_decision, xtr_state, xtr_a, spec)
    except Exception:
        log.exception("Error after the %s decision - the %s; continuing.", verdict.direction,
                      "order WAS sent" if decision.executed else "entry was not taken")


def after_decision(cfg: AdvisorConfig, verdict, decision, features, xtr_decision, xtr_state, xtr_a,
                   spec) -> None:
    """Bookkeeping, "Why" file and alert after execute() - see run_once()."""
    if decision.executed and xtr_decision is not None and xtr_state is not None and decision.plan:
        xtr_state.record_entry(decision.ticket, xtr_decision, decision.plan.entry_price, xtr_a)
    if decision.executed and decision.ticket:
        # Feeds train_model()/train_ml_model.py's offline training later.
        # Only LIVE trades carry a broker ticket that MT5's deal history can
        # join to a real P&L - a rejected decision or a dry-run "trade" can
        # never be labeled, so logging it would only add dead rows. Never
        # raises (see its own docstring).
        ml_advisor.log_snapshot(cfg, features, verdict.direction, decision.ticket)
    if cfg.last_verdict_filename:
        # Local file I/O, not a network call - kept inline rather than
        # threaded, but still never allowed to crash the evaluation cycle:
        # a permissions/path problem here must never stop trading over a
        # feature that's purely a convenience for the "Why" Telegram button.
        try:
            verdict_text = telegram_alert.format_verdict_digest(
                cfg.symbol, verdict, decision.executed, decision.reject_reason)
            gw.write_common_file(cfg.last_verdict_filename, verdict_text)
        except Exception:
            log.debug("Could not write the last-verdict file for UnifiedTrader_EA.mq5's 'Why' "
                     "button - continuing.", exc_info=True)
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
        plan = decision.plan
        if plan is None:
            # Rejected before any levels were priced (position cap, trade
            # limit, ...) - still show where the entry/SL/TPs would be.
            try:
                plan = executor.build_plan(gw, cfg, spec, verdict.direction)
            except Exception:
                log.debug("Could not price the alert's levels.", exc_info=True)
                plan = None
        message = telegram_alert.format_full_conviction_message(
            cfg.symbol, verdict, decision.executed, decision.reject_reason, plan=plan,
            news_note=decision.news_note, dry_run=cfg.dry_run, digits=getattr(spec, "digits", 2),
            xtr_note=xtr_decision.summary() if xtr_decision is not None else "")
        threading.Thread(
            target=telegram_alert.send_alert,
            args=(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, message),
            daemon=True,
        ).start()


def report_feeds(cfg: AdvisorConfig) -> int:
    """--test-feeds: one download of every free news feed, no MT5/Claude."""
    rows = news_check.feed_report(cfg)
    for r in rows:
        if r["ok"]:
            log.info("OK    %-50s %3d items, %2d relevant in the last %d min, newest %s min ago",
                     r["feed"], r["items"], r["relevant_recent"], cfg.news_check_lookback_minutes,
                     r["newest_minutes_ago"] if r["newest_minutes_ago"] is not None else "?")
        else:
            log.warning("FAIL  %-50s %s", r["feed"], r["error"])
    working = sum(1 for r in rows if r["ok"])
    log.info("%d of %d feeds working.%s", working, len(rows),
             "" if working else " The breaking-news check has nothing to read - check this PC's "
                                "internet/firewall, or edit news_feeds in config.py.")
    return 0 if working else 1


def send_test_alert(cfg: AdvisorConfig, spec) -> int:
    """--test-alert: a sample full-conviction message, priced at the live
    tick, sent synchronously so a bad token/chat id is reported here."""
    if not (cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id):
        log.error("No alert credentials - pass --telegram-alert-bot-token and --telegram-alert-chat-id "
                  "(or set TELEGRAM_ALERT_BOT_TOKEN / TELEGRAM_ALERT_CHAT_ID).")
        return 1
    leg = claude_advisor.ConfluenceLeg(direction="buy", passes=True, confirmed=True, note="sample")
    plan = executor.build_plan(gw, cfg, spec, "buy")
    step = plan.tp1_price - plan.entry_price
    sample = claude_advisor.ConfluenceVerdict(
        trend=leg, momentum=leg, strength=leg, confluence_count=3, direction="buy",
        conviction="full", smc_alignment="sample",
        take_profit_targets=[plan.tp1_price + step, plan.tp1_price + 2 * step],
        reasoning="TEST MESSAGE - not a real signal, sample levels. On a real alert TP2/TP3 are "
                  "Claude's own structure targets.")
    message = telegram_alert.format_full_conviction_message(
        cfg.symbol, sample, executed=False, reject_reason="test alert only", plan=plan,
        news_note="(not run for a test alert)", dry_run=cfg.dry_run, digits=spec.digits)
    if telegram_alert.send_alert(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, message):
        log.info("Test alert sent - check Telegram.")
        return 0
    log.error("Test alert failed - see the warning above (bad token/chat id, or you haven't sent "
              "the bot a message yet).")
    return 1


# Keys and ids kept in keys.txt (GoldTrader folder, see keys.py) and loaded
# on every start. (name, secret, when it is needed)
SAVED_SETTINGS = [
    ("ANTHROPIC_API_KEY", True, "always"),
    ("TELEGRAM_ALERT_BOT_TOKEN", True, "alerts"),
    ("TELEGRAM_ALERT_CHAT_ID", False, "alerts"),
    ("TELEGRAM_API_ID", False, "relay"),
    ("TELEGRAM_API_HASH", True, "relay"),
    ("TELEGRAM_SOURCE_CHANNELS", False, "relay"),
    ("TELEGRAM_RELAY_GROUP", False, "relay"),
    ("MT5_PASSWORD", True, "optional"),
]


def settings_report(relay_on: bool, env=None) -> tuple[list, list]:
    """(lines, missing required names) for the saved environment settings.
    Secrets are masked to their last 4 characters - never printed whole."""
    env = os.environ if env is None else env
    lines, missing = [], []
    for name, secret, need in SAVED_SETTINGS:
        value = (env.get(name) or "").strip()
        required = need == "always" or (need == "relay" and relay_on)
        if value:
            shown = ("*" * 8 + value[-4:]) if secret and len(value) > 4 else ("set" if secret else value)
            lines.append(f"  {name:<26} OK       {shown}")
        else:
            status = "MISSING" if required else "not set"
            note = {"always": "required", "alerts": "no Telegram alerts/buttons from Python",
                    "relay": "needed for the relay bridge", "optional": "fine if MT5 is logged in"}[need]
            lines.append(f"  {name:<26} {status:<8} ({note})")
            if required:
                missing.append(name)
    return lines, missing


def log_settings_report(args, full: bool = True) -> None:
    """--check: every setting; otherwise only a missing required one."""
    preset = services.load_preset(args.preset)
    lines, missing = settings_report(args.relay or preset.relay_bridge.enabled)
    if full:
        log.info("Keys (%s):\n%s", keys.keys_path(), "\n".join(lines))
    if missing:
        log.error("Missing: %s - run settings.bat, or fill it in keys.txt (Notepad) and start again.",
                  ", ".join(missing))


def _send_setup_test(token: str, chat_id: str) -> bool:
    return telegram_alert.send_alert(token, chat_id, "GoldTrader: settings saved - Telegram "
                                     "alerts from the Claude program work.")


def start_companions(cfg: AdvisorConfig, preset_path: str, force_relay: bool = False, **kw) -> list:
    """The optional companion programs switched on in settings.ini (relay
    bridge, Python Drive export, weekly ML retrain / calibration report -
    see services.py). One that stops for good is reported once over the
    Telegram alert, if set up; trading is never affected either way."""
    def alert(text: str) -> None:
        if cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id:
            telegram_alert.send_alert(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, text)

    return services.start_services(cfg, services.load_preset(preset_path), alert=alert,
                                   force_relay=force_relay, **kw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", help="override the traded symbol (default XAUUSD)")
    parser.add_argument("--lots", type=float,
                        help="trade this fixed lot instead of risk-%% sizing (default: risk-sized, "
                             "see --risk-percent; with --risk-percent too, it is only the fallback "
                             "lot). The $ SL/TP1/trail stay priced at config.py's reference_lot "
                             "either way, so this never changes those price distances")
    parser.add_argument("--max-positions", type=int, dest="max_positions",
                        help="max concurrent same-direction positions (default 5)")
    parser.add_argument("--shared-cap-magic", dest="shared_cap_magic",
                        help="comma-separated extra magic number(s) to fold into --max-positions' own "
                             "count (default: none - the cap only counts this system's own magic) - set "
                             "this to the UnifiedTrader EA's InpTelegramMagicNumber if you want the two "
                             "sources to share one combined per-direction cap instead of 5 each; see "
                             "config.py's AdvisorConfig.shared_cap_magic_numbers")
    parser.add_argument("--risk-percent", type=float, dest="risk_percent",
                        help="pct of equity risked per trade (default 2, i.e. risk-%% sizing is ON); "
                             "pass 0 to trade the fixed --lots instead; see config.py's use_risk_percent")
    parser.add_argument("--max-daily-loss", type=float, dest="max_daily_loss",
                        help="daily loss cap in pct of the day's starting equity (default 10): stops "
                             "new entries once hit, and refuses any entry whose risk plus open "
                             "positions' risk would exceed it; pass 0 to disable. Existing positions "
                             "are left alone - UnifiedTrader_EA keeps managing them")
    parser.add_argument("--daily-target", type=float, dest="daily_target",
                        help="stop new entries once the account is up this many pct on the trading "
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
                             "UnifiedTrader_EA (InpTrailDollars), not this script")
    parser.add_argument("--exit-style", choices=["sl_to_tp1", "breakeven_r_decay", "fixed_tp"],
                        dest="exit_style",
                        help="default sl_to_tp1 (see config.py's module docstring for why); "
                             "breakeven_r_decay adds an earlier breakeven step before the same "
                             "tp1/trail lock, in UnifiedTrader_EA (InpExitStyle - keep it in "
                             "sync by hand); fixed_tp is only implemented here and in backtest.py "
                             "--compare, not in the EA")
    parser.add_argument("--breakeven-atr-mult", type=float, dest="breakeven_atr_mult",
                        help="exit_style=breakeven_r_decay only: move SL to breakeven once profit "
                             "reaches this x the position's own M5 ATR (default 0.5) - enforced by "
                             "UnifiedTrader_EA's InpBreakevenAtrMult, not this script")
    parser.add_argument("--breakeven-atr-period", type=int, dest="breakeven_atr_period",
                        help="exit_style=breakeven_r_decay only: ATR period for the breakeven trigger "
                             "(default 14) - enforced by the MQL5 EA's InpAtrPeriod, not this script")
    parser.add_argument("--decay-window-minutes", type=float, dest="decay_window_minutes",
                        help="exit_style=breakeven_r_decay only: force the SL to breakeven after this "
                             "many minutes even short of the ATR trigger (default 15) - enforced by "
                             "the MQL5 EA's InpDecayWindowMinutes, not this script")
    parser.add_argument("--no-news-check", action="store_true", dest="no_news_check",
                        help="skip the pre-trade breaking-news check (news_check.py) - by default "
                             "every entry that passes all other gates first gets one extra Claude "
                             "call looking for surprise news on gold/the dollar")
    parser.add_argument("--news-check-web-search", action="store_true", dest="news_check_web_search",
                        help="let the breaking-news check also use Claude's web search tool (billed per "
                             "search; must be enabled for your API organization) - default: free RSS "
                             "headlines only")
    parser.add_argument("--news-check-no-web-search", action="store_true",
                        dest="news_check_no_web_search", help=argparse.SUPPRESS)  # the default now
    parser.add_argument("--xtr-gate", choices=["off", "block_opposed", "require_alignment"],
                        dest="xtr_gate",
                        help="XTR M5/M15/H1 alignment gate on Claude's entries (default off: the "
                             "reading is context for Claude only; block_opposed: never against a clearly "
                             "opposed M15/H1, plus the momentum filters and the two-loss stand-down; "
                             "require_alignment: also the M5 trigger plus one agreeing M15/H1) - see "
                             "xtr_logic.py")
    parser.add_argument("--news-check-fail-closed", action="store_true", dest="news_check_fail_closed",
                        help="refuse the entry when the breaking-news check cannot run at all "
                             "(default: trade anyway and say so in the log and alert)")
    parser.add_argument("--telegram-alert-bot-token", dest="telegram_alert_bot_token",
                        help="bot token from @BotFather - sends a one-way Telegram message on every "
                             "'full' conviction verdict, executed or not (default: unset, alerts off). "
                             "Normally set once by the first-run questions (--setup)")
    parser.add_argument("--telegram-alert-chat-id", dest="telegram_alert_chat_id",
                        help="chat id to send full-conviction alerts to (default: unset, alerts off)")
    parser.add_argument("--no-performance-digest", action="store_true", dest="no_performance_digest",
                        help="disable the daily/weekly performance digest (on by default once "
                             "telegram-alert-bot-token/chat-id are set - see config.py)")
    parser.add_argument("--heartbeat-hours", type=float, dest="heartbeat_hours",
                        help="send a periodic 'still alive' Telegram ping every N hours (default "
                             "0 = disabled) - reuses telegram-alert-bot-token/chat-id")
    parser.add_argument("--stale-cycle-minutes", type=float, dest="stale_cycle_minutes",
                        help="alert once if no successful evaluation cycle completes in this many "
                             "minutes - the poll loop may be stuck (default 60; pass 0 to disable)")
    parser.add_argument("--model", help="Claude model id (default claude-opus-5)")
    parser.add_argument("--no-news-blackout", action="store_true", dest="no_news_blackout",
                        help="don't block entries around high-impact economic-calendar events "
                             "(on by default; needs the MQL5 EA's calendar export - see "
                             "econ_calendar.py). The calendar is still shown to Claude.")
    parser.add_argument("--trade-hours", dest="trade_hours",
                        help="entries only inside these New York-time windows, e.g. "
                             "\"08:00-16:45\" (default: see config.py trade_windows_ny; "
                             "'any' = no restriction) - entry filter only")
    parser.add_argument("--friday-cutoff", dest="friday_cutoff",
                        help="no new entry on Friday from this New York time (e.g. 16:00; "
                             "'off' = none)")
    parser.add_argument("--max-spread", type=int, dest="max_spread",
                        help="no entry while the spread is above this many points (0 = off)")
    parser.add_argument("--min-adx", type=float, dest="min_adx",
                        help="no entry while M15 ADX14 is below this (0 = off) - checked before "
                             "the Claude call, so a flat market costs nothing")
    parser.add_argument("--min-confluence", type=int, dest="min_confluence",
                        help="minimum agreeing confluences out of 3 (default 2)")
    parser.add_argument("--no-prescreen", action="store_true", dest="no_prescreen",
                        help="ask Claude on every bar in the trading hours, even when fewer than 2 legs "
                             "agree (default: skip those calls - no trade is possible on them)")
    parser.add_argument("--allow-partial-conviction", action="store_true",
                        help="execute on 'partial' conviction too, not just 'full' (not recommended)")
    parser.add_argument("--poll-seconds", type=int, dest="poll_seconds",
                        help="how often to check for a new closed bar (default 30)")
    parser.add_argument("--live", action="store_true", help="send real orders (default is dry-run)")
    parser.add_argument("--once", action="store_true", help="run a single evaluation cycle and exit")
    parser.add_argument("--test-alert", action="store_true", dest="test_alert",
                        help="send a SAMPLE full-conviction alert (levels priced at the current tick, "
                             "no Claude call, no order) to check the Telegram alert setup, then exit")
    parser.add_argument("--test-feeds", action="store_true", dest="test_feeds",
                        help="download every free news feed once and report which work from this PC "
                             "(no MT5, no Claude call), then exit")
    parser.add_argument("--test-news-check", choices=["buy", "sell"], dest="test_news_check",
                        help="run the breaking-news check once for a hypothetical entry at the current "
                             "price (one Claude call, no order) and print the result, then exit")
    parser.add_argument("--check", action="store_true",
                        help="connect to MT5, print the symbol spec, exit - no Claude call")
    parser.add_argument("--preset", default=services.DEFAULT_PRESET,
                        help="companion programs to run alongside (relay bridge, Python Drive export, "
                             "weekly ML retrain / calibration report), each switched on or off there "
                             "(default: settings.ini in the GoldTrader folder)")
    parser.add_argument("--relay", action="store_true",
                        help="also run the Telegram relay bridge (same as enabled = true in "
                             "settings.ini [relay_bridge]) - the bridge (relay/telegram_relay_bridge.py) "
                             "as a supervised background process - restarted after a crash or "
                             "disconnect, stopped with this program; needs TELEGRAM_API_ID/HASH, "
                             "TELEGRAM_SOURCE_CHANNELS, TELEGRAM_RELAY_GROUP and a one-time --relay-login")
    parser.add_argument("--setup", action="store_true",
                        help="enter / change every setting (API key, Telegram ids, relay) in one go - "
                             "saved in keys.txt; also runs by itself on the first start")
    parser.add_argument("--relay-login", action="store_true", dest="relay_login",
                        help="log the relay bridge in to Telegram once (asks for your phone number and "
                             "code) and print the source/relay chat ids, then exit")
    parser.add_argument("--login", type=int, help="MT5 account login (optional, if not already logged in)")
    parser.add_argument("--password", help="MT5 account password (safer: MT5_PASSWORD in keys.txt "
                                           "instead)")
    parser.add_argument("--server", help="MT5 broker server name")
    parser.add_argument("--terminal-path", dest="terminal_path", help="path to terminal64.exe")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


SETTINGS_ERROR = 3   # goldtrader.py start does not restart on this - fix the options first


def main(argv: list | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:          # --help (0) or a mistyped option (argparse's 2)
        return 0 if exc.code in (0, None) else SETTINGS_ERROR

    setup_logging(args.verbose)
    keys.load()     # keys.txt -> this process and every program it starts
    # First start (or ANTHROPIC_API_KEY missing) with someone at the keyboard:
    # ask for every setting in one go, save them permanently, carry on.
    if args.setup or first_run.should_run():
        first_run.run_wizard(args.preset, send_test=_send_setup_test)
        if args.setup:
            return 0
    try:
        cfg = build_config(args)
    except ValueError as exc:
        log.error("Setting not understood: %s - fix it in start.bat (GT_ARGS) and start again.", exc)
        return SETTINGS_ERROR
    if cfg.shared_cap_magic_numbers:
        log.warning(
            "shared_cap_magic_numbers=%s is set - this only folds those magics' positions into THIS "
            "process's own count_same_direction() check. The other side (UnifiedTrader_EA.mq5's own "
            "InpMaxPositionsPerDirection) is a SEPARATE number in a separate file - it must be set to "
            "the SAME value as --max-positions/max_open_positions_per_direction (%d) or the 'shared' "
            "cap silently becomes asymmetric (whichever side has the lower number stops first, the "
            "other keeps opening past it). Nothing here can verify that for you - check it by hand.",
            cfg.shared_cap_magic_numbers, cfg.max_open_positions_per_direction)

    if args.test_feeds:
        return report_feeds(cfg)
    if args.relay_login:
        return relay_supervisor.login()

    # MT5_PASSWORD keeps the password out of the process list / shell
    # history; normally neither is needed (MT5 already logged in).
    log_settings_report(args, full=args.check)
    try:
        gw.connect(login=args.login, password=args.password or os.environ.get("MT5_PASSWORD") or None,
                   server=args.server, terminal_path=args.terminal_path)
        spec = gw.symbol_spec(cfg.symbol)
        gw.server_utc_offset_seconds(cfg.symbol)   # learn the broker clock before reading any bar time
    except RuntimeError as exc:
        # The most common first-run problem - one clear line, not a traceback.
        log.error("Cannot reach MetaTrader 5: %s\n  Fix: start MT5, log in to your account, wait "
                  "until prices move, check %s is in Market Watch, then run this again.",
                  exc, cfg.symbol)
        return 2

    if args.check:
        log.info("Connected. Symbol spec for %s: %s", cfg.symbol, spec)
        log.info("Config: lot=%s max_same_dir=%d shared_cap_magics=%s sl_mode=%s sl=$%.2f tp1=$%.2f trail=$%.2f "
                  "exit_style=%s (breakeven_atr_mult=%.2f breakeven_atr_period=%d decay_window_minutes=%.1f) "
                  "min_confluence=%d/3 require_full=%s max_daily_loss=%s daily_target=%s model=%s "
                  "dry_run=%s telegram_alerts=%s performance_digest=%s heartbeat=%s stale_alert=%s",
                  (f"risk {cfg.risk_percent:g}% of equity (max {cfg.max_lot_size:g})"
                   if cfg.use_risk_percent else f"fixed {cfg.fixed_lot:g}")
                  + f", $ distances at reference lot {cfg.reference_lot:g}",
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
                           and cfg.telegram_alert_chat_id) else "off",
                  f"every {cfg.heartbeat_interval_hours:g}h" if (cfg.heartbeat_interval_hours > 0
                      and cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id) else "off",
                  f"after {cfg.stale_cycle_alert_minutes:g}min" if (cfg.stale_cycle_alert_minutes > 0
                      and cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id) else "off")
        log.info("XTR alignment gate: %s (entry filter only - lot, SL and TP unchanged)", cfg.xtr_gate)
        log.info("Entry tactics: %s (entry filters only - lot, SL and TP unchanged)",
                 tactics.describe(cfg))
        log.info("Breaking-news check before each entry: %s",
                 "off" if not cfg.breaking_news_check else
                 f"{len(cfg.news_feeds)} free RSS feed(s)"
                 + (" + Claude web search" if cfg.news_check_web_search else "")
                 + f", last {cfg.news_check_lookback_minutes} min, "
                 + ("refuse entry if unavailable" if cfg.news_check_fail_closed
                    else "trade anyway if unavailable"))
        return 0

    if args.test_alert:
        return send_test_alert(cfg, spec)

    if cfg.dry_run:
        log.info("Running in DRY-RUN - no real orders will be sent. Pass --live to trade for real.")

    client = claude_advisor.build_client()
    if args.test_news_check:
        tick = gw.get_tick(cfg.symbol)
        price = tick.ask if args.test_news_check == "buy" else tick.bid
        result = news_check.check_before_trade(client, cfg, args.test_news_check, price)
        log.info("News check result: %s", result.note or "(no note)")
        log.info("Blocks the entry: %s | web search used: %s | headlines: %d%s",
                 result.block_reason or "no", result.used_web_search, result.headline_count,
                 f" | problems: {'; '.join(result.errors)}" if result.errors else "")
        return 0
    day = DayRoll(state_path=os.path.join(cfg.log_dir, "day_state.json"))
    xtr_state = xtr_logic.XtrStanddown(os.path.join(cfg.log_dir, "xtr_state.json"))

    if args.once:
        run_once(client, cfg, spec, day, xtr_state)
        return 0

    start_companions(cfg, args.preset, force_relay=args.relay)
    log.info("Entry filters: XTR gate %s; %s (lot, SL, TP and trail unchanged)",
             cfg.xtr_gate, tactics.describe(cfg))

    log.info("Watching %s for a new closed %s candle every %ds - Ctrl+C to stop.",
              cfg.symbol, cfg.primary_timeframe, cfg.poll_seconds)
    last_bar_time = None
    sleep_seconds = cfg.poll_seconds
    heartbeat = Heartbeat()
    failed_bar, failed_attempts = None, 0
    bar_time = None
    claude_alerted = set()     # one Telegram alert per distinct "needs manual action" reason
    while True:
        try:
            gw.server_utc_offset_seconds(cfg.symbol)   # keeps bar times on true UTC (see mt5_gateway)
            bar_time = market_intel.last_closed_time(gw, cfg.symbol, cfg.primary_timeframe)
            if bar_time != last_bar_time:
                # last_bar_time only advances AFTER a successful cycle - if
                # run_once() raises (Claude down, MT5 hiccup, ...), this same
                # bar is retried on the next poll instead of being silently
                # skipped forever - but at most MAX_ATTEMPTS_PER_BAR times, so
                # a repeating error can't re-call Claude (or re-send an order)
                # every poll for the whole bar.
                run_once(client, cfg, spec, day, xtr_state)
                last_bar_time = bar_time
                failed_bar, failed_attempts = None, 0
            heartbeat.mark_cycle_success()
            sleep_seconds = cfg.poll_seconds
        except KeyboardInterrupt:
            log.info("Stopped.")
            return 0
        except claude_advisor.ClaudeUnavailableError as exc:
            # Also counts as a "successful" pass for staleness purposes -
            # Claude being unavailable already has its own distinct
            # backoff/logging right here; it's not a stuck/frozen loop.
            heartbeat.mark_cycle_success()
            if exc.retryable:
                log.warning("Claude temporarily unavailable this cycle - %s", exc)
                sleep_seconds = cfg.poll_seconds
            else:
                reason = str(exc)
                if reason not in claude_alerted and cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id:
                    claude_alerted.add(reason)
                    threading.Thread(
                        target=telegram_alert.send_alert,
                        args=(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id,
                              f"GoldTrader: Claude entries STOPPED - {reason[:300]}\n"
                              "Telegram signals are still copied and open trades still managed. "
                              "Fix it, then restart start.bat."),
                        daemon=True).start()
                log.warning(
                    "Claude unavailable and this looks like it needs manual action (credits/API "
                    "key/permissions) rather than a retry - backing off to every %d minutes "
                    "instead of polling every %ds until it's fixed. No new signals will be "
                    "evaluated in the meantime, but UnifiedTrader_EA keeps managing already-open "
                    "positions and copying Telegram signals on its own - nothing there depends "
                    "on Claude. Reason: %s",
                    CLAUDE_UNAVAILABLE_BACKOFF_SECONDS // 60, cfg.poll_seconds, exc)
                sleep_seconds = CLAUDE_UNAVAILABLE_BACKOFF_SECONDS
        except Exception:
            sleep_seconds = cfg.poll_seconds
            if bar_time is not None and bar_time == failed_bar:
                failed_attempts += 1
            else:
                failed_bar, failed_attempts = bar_time, 1
            if bar_time is not None and failed_attempts >= MAX_ATTEMPTS_PER_BAR:
                log.exception("Error during evaluation cycle - failed %d times on this bar; SKIPPING "
                              "it and waiting for the next one", failed_attempts)
                last_bar_time = bar_time
                failed_bar, failed_attempts = None, 0
            else:
                log.exception("Error during evaluation cycle - will retry next poll")

        if cfg.telegram_alert_bot_token and cfg.telegram_alert_chat_id:
            if heartbeat.due_heartbeat(cfg):
                try:
                    equity_now = gw.account_equity()
                except Exception:
                    equity_now = None   # the ping itself matters more than the equity line
                msg = telegram_alert.format_heartbeat_message(
                    cfg.symbol, heartbeat.minutes_since_last_success(), equity_now,
                    day.day_start_equity)
                threading.Thread(target=telegram_alert.send_alert,
                                 args=(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, msg),
                                 daemon=True).start()
                heartbeat.mark_heartbeat_sent()
            if heartbeat.due_stale_alert(cfg):
                msg = telegram_alert.format_stale_cycle_alert(
                    cfg.symbol, heartbeat.minutes_since_last_success())
                threading.Thread(target=telegram_alert.send_alert,
                                 args=(cfg.telegram_alert_bot_token, cfg.telegram_alert_chat_id, msg),
                                 daemon=True).start()
                heartbeat.mark_stale_alert_sent()

        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:     # Ctrl+C lands here most of the time - no traceback
            log.info("Stopped.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())

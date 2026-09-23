"""
Turns a Claude verdict into a gated go/no-go decision and, if every gate
clears, an actual order - the "execute full conviction signals" step.

`gate()` and `execute()` both take `gateway` as a parameter (the mt5_gateway
module, or a fake with the same functions in selftest.py) rather than
importing it directly - same dependency-injection approach as the Telegram
copier's SignalVerifier, and for the same reason: this logic is testable
without a real MT5 connection.

Every evaluation is logged to logs/decisions.csv (accepted AND rejected, so
you can see why Claude's calls didn't fire); every order actually sent is
also logged to logs/trades.csv.
"""
from __future__ import annotations

import csv
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import econ_calendar
import market_intel
from claude_advisor import ConfluenceVerdict
from config import AdvisorConfig

log = logging.getLogger(__name__)

# Written to every logged row's "source" column - this solution only ever
# produces Claude-validated signals, so it's a constant here, unlike the
# Telegram copier's MQL5 TradeLogger EA (which is reused across systems and
# takes its label as an input instead). Kept as one literal so a decisions.csv
# and trades.csv from this solution are unambiguous even if copied elsewhere
# alongside the Telegram copier's own logs.
SOURCE_TAG = "Claude_Sig"

# TRADE_RETCODE_PLACED / DONE / DONE_PARTIAL - anything else means no position.
ORDER_OK_RETCODES = {10008, 10009, 10010}

DECISION_FIELDS = ["time", "source", "direction", "confluence_count", "conviction",
                    "trend", "momentum", "strength", "smc_alignment",
                    "executed", "reject_reason", "reasoning", "ticket"]
TRADE_FIELDS = ["time", "source", "direction", "lots", "entry_price", "sl", "tp",
                 "mode", "retcode", "ticket"]


@dataclass
class Decision:
    executed: bool
    reject_reason: str = ""
    ticket: str = ""


def _csv_path(cfg: AdvisorConfig, name: str) -> str:
    os.makedirs(cfg.log_dir, exist_ok=True)
    return os.path.join(cfg.log_dir, name)


_warned_stale_header_paths: set = set()


def _append_row(path: str, fieldnames: list, row: dict) -> None:
    new_file = not os.path.exists(path)
    if not new_file and path not in _warned_stale_header_paths:
        # A file from before a field was added to `fieldnames` (e.g. the
        # "ticket" column) keeps its OLD header forever - DictWriter only
        # writes one when the file doesn't exist yet. New rows appended
        # under that stale header silently misalign when read back with
        # csv.DictReader (the extra trailing value lands under the None
        # restkey, not its real column name) - warn loudly, once per file
        # per process, rather than let that happen with no signal at all.
        with open(path, newline="") as f:
            existing_header = f.readline().rstrip("\r\n")
        expected_header = ",".join(fieldnames)
        if existing_header and existing_header != expected_header:
            log.warning(
                "%s has an older column layout than this version writes (has: %r, now writing: "
                "%r) - new rows will misalign when read back against the OLD header still on "
                "file. Rename or archive the existing file so a fresh one starts with the "
                "current header.", path, existing_header, expected_header)
        _warned_stale_header_paths.add(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def log_decision(cfg: AdvisorConfig, verdict: ConfluenceVerdict, executed: bool,
                  reject_reason: str = "", ticket="") -> None:
    """ticket (blank for a rejected decision, or a dry-run trade with no
    real broker ticket) lets calibration_report.py join this row to its
    exact trades.csv counterpart instead of relying on row order, which
    would silently misalign every later pair if the process ever died
    between this call and execute()'s log_trade() call for one signal in
    the middle of the log.
    """
    def leg(l):
        return f"{l.direction}/pass={l.passes}/confirmed={l.confirmed}"

    row = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_TAG,
        "direction": verdict.direction,
        "confluence_count": verdict.confluence_count,
        "conviction": verdict.conviction,
        "trend": leg(verdict.trend), "momentum": leg(verdict.momentum), "strength": leg(verdict.strength),
        "smc_alignment": verdict.smc_alignment,
        "executed": executed,
        "reject_reason": reject_reason,
        "reasoning": verdict.reasoning,
        "ticket": ticket,
    }
    _append_row(_csv_path(cfg, "decisions.csv"), DECISION_FIELDS, row)


def log_trade(cfg: AdvisorConfig, direction: str, lots: float, entry_price: float,
              sl: float, tp: float, mode: str, retcode, ticket) -> None:
    row = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_TAG,
        "direction": direction, "lots": lots, "entry_price": entry_price,
        "sl": sl, "tp": tp, "mode": mode, "retcode": retcode, "ticket": ticket,
    }
    _append_row(_csv_path(cfg, "trades.csv"), TRADE_FIELDS, row)


def in_news_blackout(cfg: AdvisorConfig, now: datetime | None = None) -> str:
    """Returns a description of the matching window if `now` (UTC, defaults
    to the current time) falls inside one of cfg.news_blackout_windows, else
    "". These are hand-maintained windows on top of the automatic
    calendar blackout (econ_calendar.blackout_reason(), also checked in
    gate()) - useful for events the calendar lacks - and a
    malformed entry raises ValueError at gate() time rather than silently
    never matching, so a typo'd date is noticed immediately rather than
    quietly leaving a blackout window unenforced.
    """
    now = now or datetime.now(timezone.utc)
    for start_iso, end_iso in cfg.news_blackout_windows:
        start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        if start <= now <= end:
            return f"inside news blackout window {start_iso} - {end_iso}"
    return ""


def verdict_independent_block(gateway, cfg: AdvisorConfig, trades_today: int) -> str:
    """Every gate that doesn't depend on Claude's verdict - manual news
    windows, the calendar blackout, the daily trade limit. main.run_once()
    checks these BEFORE paying for a Claude call; gate() re-checks them."""
    # gateway.now() rather than a bare datetime.now() call - real
    # mt5_gateway.now() is real wall-clock time, but backtest.
    # HistoricalGateway.now() is the simulated replay clock, so a backtest
    # judges news_blackout_windows against the bar being evaluated, not
    # whatever real date the backtest happens to be run on.
    now = gateway.now()
    blackout = in_news_blackout(cfg, now=now)
    if blackout:
        return blackout
    calendar = econ_calendar.load_events(gateway, cfg)
    if calendar is not None:
        blackout = econ_calendar.blackout_reason(calendar[0], econ_calendar.to_utc_datetime(now), cfg)
        if blackout:
            return blackout
    if cfg.max_trades_per_day and trades_today >= cfg.max_trades_per_day:
        return f"max trades/day reached ({cfg.max_trades_per_day})"
    return ""


def gate(gateway, cfg: AdvisorConfig, verdict: ConfluenceVerdict, trades_today: int,
         daily_block_reason: str = "") -> str:
    """Returns "" if the verdict clears every gate, else the reason it didn't."""
    if daily_block_reason:
        return daily_block_reason
    reason = verdict_independent_block(gateway, cfg, trades_today)
    if reason:
        return reason
    if verdict.direction not in ("buy", "sell"):
        return "no actionable direction"
    if verdict.confluence_count < cfg.min_confluence_count:
        return (f"confluence_count {verdict.confluence_count} below the required "
                f"{cfg.min_confluence_count}")
    if cfg.require_full_conviction and verdict.conviction != "full":
        return f"conviction is {verdict.conviction!r}, not full"
    same_dir_open = gateway.count_same_direction(cfg.symbol, cfg.magic, verdict.direction,
                                                  cfg.shared_cap_magic_numbers)
    if same_dir_open >= cfg.max_open_positions_per_direction:
        return (f"already {same_dir_open} open {verdict.direction} position(s) "
                f"(max {cfg.max_open_positions_per_direction})")
    return ""


def position_size(gateway, cfg: AdvisorConfig, spec, sl_dist: float) -> float:
    """cfg.fixed_lot, or a size derived from current equity when
    use_risk_percent is set - see config.py's own comment on those fields.
    Falls back to fixed_lot on any degenerate input (no equity, no tick
    data, zero sl_dist) rather than risking a divide-by-zero or a wild lot.
    """
    if not cfg.use_risk_percent:
        return cfg.fixed_lot
    equity = gateway.account_equity()
    if equity <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0 or sl_dist <= 0:
        return cfg.fixed_lot
    loss_per_lot = (sl_dist / spec.tick_size) * spec.tick_value
    lots = (equity * cfg.risk_percent / 100.0) / loss_per_lot
    step = spec.volume_step or 0.01
    # A plain `lots // step` silently under-sizes by a whole step whenever
    # floating-point imprecision leaves the true ratio a hair under an
    # integer (e.g. 0.03/0.01 can evaluate to 2.9999999999999996, floored
    # to 2 instead of 3) - the epsilon absorbs that without ever rounding a
    # genuinely-below-the-boundary value up a step.
    lots = math.floor(lots / step + 1e-9) * step
    if lots < spec.volume_min:
        # The broker's minimum lot is a hard floor - there is no smaller
        # order to place - so this clamps UP rather than skipping the
        # trade, but that silently risks more than risk_percent% of equity
        # on a small account. Warn loudly rather than let that pass quietly.
        actual_risk = spec.volume_min * loss_per_lot
        log.warning("Risk-sized lot (%.4f) is below the broker minimum (%.2f) - using the minimum "
                    "instead, which risks $%.2f (%.2f%% of equity) rather than the intended "
                    "risk_percent=%.2f%% ($%.2f). Raise risk_percent or fund the account further "
                    "to bring this back in line.",
                    lots, spec.volume_min, actual_risk, actual_risk / equity * 100.0,
                    cfg.risk_percent, equity * cfg.risk_percent / 100.0)
    lots = max(spec.volume_min, min(lots, spec.volume_max, cfg.max_lot_size))
    return round(lots, 2)


def open_risk_dollars(gateway, cfg: AdvisorConfig, spec) -> float:
    """Money still at risk on cfg.symbol, whatever placed it (this system,
    UnifiedTrader_EA's Telegram side, another EA, a manual trade) - the
    daily cap is an ACCOUNT cap: every open position from the current price
    to its stop, plus every pending order from its entry to its stop. A
    stop already locked beyond the current price (profit protected)
    contributes 0; positions/orders with no stop can't be priced and are
    skipped - every order this solution places carries one. Mirrors
    UnifiedTrader_EA.mq5's OpenRiskMoney().
    """
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0
    per_price = spec.tick_value / spec.tick_size
    tick = gateway.get_tick(cfg.symbol)
    total = 0.0
    for p in gateway.symbol_positions(cfg.symbol):
        if p["sl"] <= 0:
            continue
        dist = tick.bid - p["sl"] if p["direction"] == "buy" else p["sl"] - tick.ask
        if dist > 0:
            total += dist * per_price * p["volume"]
    for o in gateway.pending_orders(cfg.symbol):
        if o["sl"] <= 0:
            continue
        total += abs(o["price_open"] - o["sl"]) * per_price * o["volume"]
    return total


def daily_risk_budget_reason(gateway, cfg: AdvisorConfig, spec, day_start_equity: float,
                             new_trade_risk: float) -> str:
    """Makes max_daily_loss_pct a real cap, not just a stop-new-entries
    trigger: the daily breaker alone only fires AFTER equity is already down
    max_daily_loss_pct, and never closes anything - so e.g. five concurrent
    2%-risk positions could still all stop out together past it. This
    refuses a new entry if today's drawdown so far + what every open
    position still risks to its stop + this trade's own risk would exceed
    max_daily_loss_pct of the day's starting equity. "" if it fits (or the
    cap/day anchor isn't set).
    """
    if cfg.max_daily_loss_pct <= 0 or day_start_equity <= 0:
        return ""
    budget = day_start_equity * cfg.max_daily_loss_pct / 100.0
    drawdown = max(0.0, day_start_equity - gateway.account_equity())
    committed = drawdown + open_risk_dollars(gateway, cfg, spec) + new_trade_risk
    if committed > budget + 1e-9:
        return (f"daily loss budget: today's drawdown + open risk + this trade would total "
                f"${committed:.2f}, over the {cfg.max_daily_loss_pct:g}% cap (${budget:.2f})")
    return ""


def atr_sl_distance(gateway, cfg: AdvisorConfig, spec) -> float | None:
    """ATR-based entry stop-loss price distance for sl_mode="atr" - see
    config.py's own comment for why this is entry-SL only. Returns None
    (callers fall back to the fixed sl_dollars distance) when there isn't
    enough bar history yet, exactly like every other "not enough data"
    path in market_intel.py.
    """
    bars = gateway.get_bars(cfg.symbol, cfg.sl_atr_timeframe, cfg.sl_atr_period + 5)
    closed = bars.iloc[:-1]
    if len(closed) <= cfg.sl_atr_period:
        return None
    atr_value = float(market_intel.atr(closed, cfg.sl_atr_period).iloc[-1])
    if atr_value <= 0:
        return None
    sl_dist = atr_value * cfg.sl_atr_mult
    min_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars_min, cfg.reference_lot)
    max_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars_max, cfg.reference_lot)
    return max(min_dist, min(sl_dist, max_dist))


def _reject(cfg: AdvisorConfig, verdict: ConfluenceVerdict, reason: str) -> Decision:
    log.info("REJECTED %s: %s", verdict.direction, reason)
    log_decision(cfg, verdict, executed=False, reject_reason=reason)
    return Decision(executed=False, reject_reason=reason)


def execute(gateway, cfg: AdvisorConfig, verdict: ConfluenceVerdict, spec,
            trades_today: int, daily_block_reason: str = "",
            day_start_equity: float = 0.0) -> Decision:
    """day_start_equity (main.DayRoll / backtest.BacktestDayState's anchor)
    enables the daily_risk_budget_reason() check; 0 skips it."""
    reason = gate(gateway, cfg, verdict, trades_today, daily_block_reason)
    if reason:
        return _reject(cfg, verdict, reason)

    tick = gateway.get_tick(cfg.symbol)
    entry_price = tick.ask if verdict.direction == "buy" else tick.bid
    # sl_dist is always solved at reference_lot - a fixed price distance,
    # independent of what lot actually ends up trading (see position_size()
    # and config.py's reference_lot comment).
    if cfg.sl_mode == "atr":
        sl_dist = atr_sl_distance(gateway, cfg, spec)
        if sl_dist is None:
            sl_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.reference_lot)
    elif cfg.sl_mode == "fixed":
        sl_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.reference_lot)
    else:
        raise ValueError(f"Unrecognized sl_mode {cfg.sl_mode!r} - must be 'fixed' or 'atr'.")
    lots = position_size(gateway, cfg, spec, sl_dist)
    if spec.tick_size > 0:
        new_trade_risk = sl_dist / spec.tick_size * spec.tick_value * lots
        budget_reason = daily_risk_budget_reason(gateway, cfg, spec, day_start_equity, new_trade_risk)
        if budget_reason:
            return _reject(cfg, verdict, budget_reason)
    if verdict.direction == "buy":
        sl_price = entry_price - sl_dist
    else:
        sl_price = entry_price + sl_dist

    if cfg.exit_style == "fixed_tp":
        # The original design: a real broker take-profit at entry+tp1_dist -
        # the SAME price ClaudeSMC_TradeManager.mq5's old logic would arm the
        # trail at, which is exactly the race condition exit_style=sl_to_tp1
        # exists to avoid. Kept only for backtest.py --compare.
        tp_dist = gateway.price_distance_for_dollars(spec, cfg.tp1_dollars, cfg.reference_lot)
        tp_price = entry_price + tp_dist if verdict.direction == "buy" else entry_price - tp_dist
    elif cfg.exit_style in ("sl_to_tp1", "breakeven_r_decay"):
        # Neither style places a broker take-profit at all - the position's
        # only exit mechanism is the stop-loss, which the live MQL5 EA (or
        # backtest.py's simulation of it) moves according to whichever style
        # is configured there (see config.py's module docstring). An
        # explicit allow-list here rather than a catch-all else: a typo'd
        # exit_style should fail loudly, not silently behave like sl_to_tp1.
        tp_price = 0.0
    else:
        raise ValueError(
            f"Unrecognized exit_style {cfg.exit_style!r} - must be one of "
            f"'sl_to_tp1', 'breakeven_r_decay', 'fixed_tp'.")

    result = gateway.place_market_order(
        spec, verdict.direction, lots, sl_price, tp_price,
        cfg.magic, cfg.comment, cfg.deviation_points, cfg.dry_run,
    )
    retcode = getattr(result, "retcode", "")
    ticket = getattr(result, "order", "")
    if not cfg.dry_run and retcode not in ORDER_OK_RETCODES:
        # A refused order (no money, invalid stops, market closed, unsupported
        # filling, requote, ...) must never be reported as a trade.
        comment = getattr(result, "comment", "") if result is not None else "order_send returned None"
        return _reject(cfg, verdict, f"order rejected by broker: retcode={retcode} {comment}".strip())
    fill_price = getattr(result, "price", entry_price)
    tp_desc = f"tp={tp_price:.2f}" if tp_price else f"no broker TP (locks at ${cfg.tp1_dollars:g} via SL)"
    log.info("ACCEPTED %s %.2f lots @ %.2f sl=%.2f %s (conviction=%s, %d/3)",
              verdict.direction.upper(), lots, fill_price, sl_price, tp_desc,
              verdict.conviction, verdict.confluence_count)
    log_decision(cfg, verdict, executed=True, ticket=ticket)
    log_trade(cfg, verdict.direction, lots, fill_price, sl_price, tp_price,
              "dry-run" if cfg.dry_run else "live", retcode, ticket)
    return Decision(executed=True, ticket=str(ticket))

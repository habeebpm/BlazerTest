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
import os
from dataclasses import dataclass
from datetime import datetime, timezone

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

DECISION_FIELDS = ["time", "source", "direction", "confluence_count", "conviction",
                    "trend", "momentum", "strength", "smc_alignment",
                    "executed", "reject_reason", "reasoning"]
TRADE_FIELDS = ["time", "source", "direction", "lots", "entry_price", "sl", "tp",
                 "mode", "retcode", "ticket"]


@dataclass
class Decision:
    executed: bool
    reject_reason: str = ""


def _csv_path(cfg: AdvisorConfig, name: str) -> str:
    os.makedirs(cfg.log_dir, exist_ok=True)
    return os.path.join(cfg.log_dir, name)


def _append_row(path: str, fieldnames: list, row: dict) -> None:
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def log_decision(cfg: AdvisorConfig, verdict: ConfluenceVerdict, executed: bool,
                  reject_reason: str = "") -> None:
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
    "". This solution has no economic-calendar data source of its own -
    these windows are maintained by hand (see config.py's own comment) - a
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


def gate(gateway, cfg: AdvisorConfig, verdict: ConfluenceVerdict, trades_today: int,
         daily_block_reason: str = "") -> str:
    """Returns "" if the verdict clears every gate, else the reason it didn't."""
    if daily_block_reason:
        return daily_block_reason
    blackout = in_news_blackout(cfg)
    if blackout:
        return blackout
    if verdict.direction not in ("buy", "sell"):
        return "no actionable direction"
    if verdict.confluence_count < cfg.min_confluence_count:
        return (f"confluence_count {verdict.confluence_count} below the required "
                f"{cfg.min_confluence_count}")
    if cfg.require_full_conviction and verdict.conviction != "full":
        return f"conviction is {verdict.conviction!r}, not full"
    if cfg.max_trades_per_day and trades_today >= cfg.max_trades_per_day:
        return f"max trades/day reached ({cfg.max_trades_per_day})"
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
    lots = (lots // step) * step
    lots = max(spec.volume_min, min(lots, spec.volume_max, cfg.max_lot_size))
    return round(lots, 2)


def execute(gateway, cfg: AdvisorConfig, verdict: ConfluenceVerdict, spec,
            trades_today: int, daily_block_reason: str = "") -> Decision:
    reason = gate(gateway, cfg, verdict, trades_today, daily_block_reason)
    if reason:
        log.info("REJECTED %s: %s", verdict.direction, reason)
        log_decision(cfg, verdict, executed=False, reject_reason=reason)
        return Decision(executed=False, reject_reason=reason)

    tick = gateway.get_tick(cfg.symbol)
    entry_price = tick.ask if verdict.direction == "buy" else tick.bid
    # sl_dist is always solved at fixed_lot - a fixed REFERENCE price distance,
    # independent of what lot actually ends up trading (see position_size()
    # and config.py's use_risk_percent comment).
    sl_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.fixed_lot)
    lots = position_size(gateway, cfg, spec, sl_dist)
    if verdict.direction == "buy":
        sl_price = entry_price - sl_dist
    else:
        sl_price = entry_price + sl_dist

    if cfg.exit_style == "fixed_tp":
        # The original design: a real broker take-profit at entry+tp1_dist -
        # the SAME price ClaudeSMC_TradeManager.mq5's old logic would arm the
        # trail at, which is exactly the race condition exit_style=sl_to_tp1
        # exists to avoid. Kept only for backtest.py --compare.
        tp_dist = gateway.price_distance_for_dollars(spec, cfg.tp1_dollars, cfg.fixed_lot)
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
    fill_price = getattr(result, "price", entry_price)
    tp_desc = f"tp={tp_price:.2f}" if tp_price else f"no broker TP (locks at ${cfg.tp1_dollars:g} via SL)"
    log.info("ACCEPTED %s %.2f lots @ %.2f sl=%.2f %s (conviction=%s, %d/3)",
              verdict.direction.upper(), lots, fill_price, sl_price, tp_desc,
              verdict.conviction, verdict.confluence_count)
    log_decision(cfg, verdict, executed=True)
    log_trade(cfg, verdict.direction, lots, fill_price, sl_price, tp_price,
              "dry-run" if cfg.dry_run else "live", retcode, ticket)
    return Decision(executed=True)

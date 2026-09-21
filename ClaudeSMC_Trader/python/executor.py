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


def gate(gateway, cfg: AdvisorConfig, verdict: ConfluenceVerdict, trades_today: int) -> str:
    """Returns "" if the verdict clears every gate, else the reason it didn't."""
    if verdict.direction not in ("buy", "sell"):
        return "no actionable direction"
    if verdict.confluence_count < cfg.min_confluence_count:
        return (f"confluence_count {verdict.confluence_count} below the required "
                f"{cfg.min_confluence_count}")
    if cfg.require_full_conviction and verdict.conviction != "full":
        return f"conviction is {verdict.conviction!r}, not full"
    if cfg.max_trades_per_day and trades_today >= cfg.max_trades_per_day:
        return f"max trades/day reached ({cfg.max_trades_per_day})"
    same_dir_open = gateway.count_same_direction(cfg.symbol, cfg.magic, verdict.direction)
    if same_dir_open >= cfg.max_open_positions_per_direction:
        return (f"already {same_dir_open} open {verdict.direction} position(s) "
                f"(max {cfg.max_open_positions_per_direction})")
    return ""


def execute(gateway, cfg: AdvisorConfig, verdict: ConfluenceVerdict, spec,
            trades_today: int) -> Decision:
    reason = gate(gateway, cfg, verdict, trades_today)
    if reason:
        log.info("REJECTED %s: %s", verdict.direction, reason)
        log_decision(cfg, verdict, executed=False, reject_reason=reason)
        return Decision(executed=False, reject_reason=reason)

    tick = gateway.get_tick(cfg.symbol)
    entry_price = tick.ask if verdict.direction == "buy" else tick.bid
    sl_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.fixed_lot)
    tp_dist = gateway.price_distance_for_dollars(spec, cfg.tp_arm_dollars, cfg.fixed_lot)
    if verdict.direction == "buy":
        sl_price, tp_price = entry_price - sl_dist, entry_price + tp_dist
    else:
        sl_price, tp_price = entry_price + sl_dist, entry_price - tp_dist

    result = gateway.place_market_order(
        spec, verdict.direction, cfg.fixed_lot, sl_price, tp_price,
        cfg.magic, cfg.comment, cfg.deviation_points, cfg.dry_run,
    )
    retcode = getattr(result, "retcode", "")
    ticket = getattr(result, "order", "")
    fill_price = getattr(result, "price", entry_price)
    log.info("ACCEPTED %s %.2f lots @ %.2f sl=%.2f tp=%.2f (conviction=%s, %d/3)",
              verdict.direction.upper(), cfg.fixed_lot, fill_price, sl_price, tp_price,
              verdict.conviction, verdict.confluence_count)
    log_decision(cfg, verdict, executed=True)
    log_trade(cfg, verdict.direction, cfg.fixed_lot, fill_price, sl_price, tp_price,
              "dry-run" if cfg.dry_run else "live", retcode, ticket)
    return Decision(executed=True)

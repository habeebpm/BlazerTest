#!/usr/bin/env python3
"""
Offline conviction calibration report: joins logs/decisions.csv (every
evaluated verdict, executed or not) against logs/trades.csv and, for trades
placed live (not dry-run), each ticket's real closed P&L from MT5's own
history - answering "when Claude called conviction=X/confluence=Y, how did
those trades actually do?"

HONEST SCOPING LIMITATION: with require_full_conviction=True (the live
default - see config.py), executor.gate() only ever lets a "full" verdict
through, so "partial"/"none" rows in decisions.csv never produced a trade to
grade. This report still counts how often each conviction level occurred
(useful on its own - e.g. "full" firing constantly might mean the bar is too
low), but any win-rate/P&L breakdown can only ever be measured across
conviction="full" trades, split by confluence_count (2/3 vs 3/3). Running
some evaluation with --allow-partial-conviction (see main.py) is the only
way to get real outcome data for "partial" calls too.

The decisions<->trades join relies on a property of executor.execute(): it
calls log_decision(executed=True) then log_trade() as the only two writers
to these two files, always in that order, always in a 1:1 pairing - so the
Nth executed=True row in decisions.csv is the Nth row in trades.csv. This
breaks if either CSV is hand-edited or trimmed independently; a length
mismatch is reported rather than silently misaligning rows.

Usage:
    python calibration_report.py                          # uses logs/decisions.csv, logs/trades.csv
    python calibration_report.py --lookback-days 90        # how far back to query MT5 history
    python calibration_report.py --no-mt5                  # skip the live-P&L join (counts only)
"""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import dataclass, field


@dataclass
class Bucket:
    conviction: str
    confluence_count: int
    n_trades: int = 0
    n_with_pnl: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0

    def win_rate_pct(self) -> float | None:
        return round(self.wins / self.n_with_pnl * 100.0, 1) if self.n_with_pnl else None

    def avg_pnl(self) -> float | None:
        return round(self.net_pnl / self.n_with_pnl, 2) if self.n_with_pnl else None


def load_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def join_decisions_and_trades(decisions: list[dict], trades: list[dict]) -> tuple[list[tuple[dict, dict]], int]:
    """Pairs each executed decision with the trade it produced, by row
    order (see module docstring for why this is a valid join here).
    Returns (pairs, mismatch_count) - mismatch_count is how many rows had
    to be dropped because the two files' executed-row counts didn't match
    (0 in the normal case).
    """
    executed = [d for d in decisions if str(d.get("executed", "")).strip() == "True"]
    n = min(len(executed), len(trades))
    mismatch = abs(len(executed) - len(trades))
    return list(zip(executed[:n], trades[:n])), mismatch


def summarize_by_bucket(pairs: list[tuple[dict, dict]], pnl_by_ticket: dict[str, float]) -> dict[tuple, Bucket]:
    """pnl_by_ticket: {ticket_str: pnl_dollars} for LIVE trades only (from
    MT5's real closed-trade history) - dry-run trades have no ticket worth
    looking up, so they're counted toward n_trades but never n_with_pnl.
    """
    buckets: dict[tuple, Bucket] = {}
    for decision, trade in pairs:
        key = (decision["conviction"], int(decision["confluence_count"]))
        b = buckets.setdefault(key, Bucket(conviction=key[0], confluence_count=key[1]))
        b.n_trades += 1
        ticket = str(trade.get("ticket", "")).strip()
        if ticket and ticket in pnl_by_ticket:
            pnl = pnl_by_ticket[ticket]
            b.n_with_pnl += 1
            b.net_pnl += pnl
            if pnl > 0:
                b.wins += 1
            elif pnl < 0:
                b.losses += 1
    return buckets


def conviction_frequency(decisions: list[dict]) -> dict[str, int]:
    """Every conviction level Claude has ever called, executed or not - see
    module docstring's scoping note on why this is shown separately from
    the outcome buckets above.
    """
    freq: dict[str, int] = {}
    for d in decisions:
        freq[d["conviction"]] = freq.get(d["conviction"], 0) + 1
    return freq


def format_report(buckets: dict[tuple, Bucket], freq: dict[str, int], mismatch: int) -> str:
    lines = ["Conviction calibration report", "=" * 30, ""]
    lines.append("How often each conviction level was called (executed or not):")
    for conviction, count in sorted(freq.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {conviction:10s} {count}")
    lines.append("")
    lines.append("Outcomes of executed trades, by conviction/confluence_count "
                 "(win rate and net P&L are only over trades with a real MT5-sourced P&L):")
    if not buckets:
        lines.append("  (no executed trades found)")
    for key in sorted(buckets, key=lambda k: (k[0], -k[1])):
        b = buckets[key]
        win_rate = b.win_rate_pct()
        avg = b.avg_pnl()
        lines.append(
            f"  conviction={b.conviction:8s} confluence={b.confluence_count}/3  "
            f"trades={b.n_trades:3d}  with_pnl={b.n_with_pnl:3d}  "
            f"win_rate={win_rate if win_rate is not None else 'n/a'}%  "
            f"net_pnl=${b.net_pnl:+.2f}  avg_pnl={f'${avg:+.2f}' if avg is not None else 'n/a'}"
        )
    if mismatch:
        lines.append("")
        lines.append(f"WARNING: decisions.csv/trades.csv row-count mismatch of {mismatch} - "
                     "one of the files may have been hand-edited or truncated independently of "
                     f"the other; {mismatch} trailing row(s) were dropped from the join rather "
                     "than risk misaligning them.")
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--decisions", default="logs/decisions.csv")
    parser.add_argument("--trades", default="logs/trades.csv")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--magic", type=int, default=20260921, help="must match config.py's AdvisorConfig.magic")
    parser.add_argument("--lookback-days", type=int, default=180, dest="lookback_days")
    parser.add_argument("--no-mt5", action="store_true", dest="no_mt5",
                        help="skip the live MT5 P&L join - report conviction frequency counts only")
    parser.add_argument("--login", type=int)
    parser.add_argument("--password")
    parser.add_argument("--server")
    parser.add_argument("--terminal-path", dest="terminal_path")
    args = parser.parse_args(argv)

    decisions = load_csv(args.decisions)
    trades = load_csv(args.trades)
    if not decisions:
        print(f"No decisions found at {args.decisions} - nothing to report.")
        return 0

    pairs, mismatch = join_decisions_and_trades(decisions, trades)

    pnl_by_ticket: dict[str, float] = {}
    if not args.no_mt5:
        import mt5_gateway as gw
        gw.connect(login=args.login, password=args.password, server=args.server,
                  terminal_path=args.terminal_path)
        for t in gw.recent_closed_trades(args.symbol, args.magic, count=5000,
                                         lookback_days=args.lookback_days):
            pnl_by_ticket[str(t["ticket"])] = t["pnl_dollars"]

    buckets = summarize_by_bucket(pairs, pnl_by_ticket)
    freq = conviction_frequency(decisions)
    print(format_report(buckets, freq, mismatch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

The decisions<->trades join matches by ticket (executor.log_decision()
records the same ticket log_trade() does, for every executed row) rather
than row order, so a process killed mid-execute() between those two calls
for one signal doesn't misalign every pair after it. Dry-run trades and
decisions.csv rows written before the ticket field existed have no ticket
to match on and fall back to being paired positionally among themselves -
fine for counting, since neither ever has real MT5 P&L to grade anyway.
Any executed decision that still can't be matched to a trade row is
reported, not silently dropped or misaligned.

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
    """Pairs each executed decision with the trade it produced.

    Matched by ticket where both rows have one (every LIVE trade - see
    executor.log_decision()'s own ticket parameter) rather than row order:
    a plain positional zip silently misaligns every pair after the point
    where the process died between log_decision(executed=True) and
    log_trade() for one signal in the middle of the log, not just the
    trailing rows a naive length check would suggest.

    Rows with no ticket (dry-run trades, or decisions.csv rows written
    before this field existed) have no real P&L to grade anyway, so they
    fall back to being paired in the order they appear among themselves -
    good enough for counting purposes, never used for outcome grading
    since dry-run tickets never appear in MT5's real deal history either.

    Returns (pairs, unmatched_count) - unmatched_count is how many
    executed decisions had no trade row to pair with at all (0 in the
    normal case; a mismatched ticket or truncated trades.csv can produce
    this).
    """
    executed = [d for d in decisions if str(d.get("executed", "")).strip() == "True"]

    trades_by_ticket: dict[str, list[dict]] = {}
    fallback_trades = []
    for t in trades:
        ticket = str(t.get("ticket", "")).strip()
        if ticket:
            trades_by_ticket.setdefault(ticket, []).append(t)
        else:
            fallback_trades.append(t)
    fallback_iter = iter(fallback_trades)

    pairs = []
    unmatched = 0
    for d in executed:
        ticket = str(d.get("ticket", "")).strip()
        if ticket:
            # A decision with a REAL ticket must only ever match that exact
            # ticket - never fall through to the ticketless-trade pool,
            # which would let a merely-missing trade row (a genuinely
            # unmatched decision) silently steal the fallback slot meant
            # for a later decision that actually has no ticket at all.
            bucket = trades_by_ticket.get(ticket)
            if bucket:
                pairs.append((d, bucket.pop(0)))
            else:
                unmatched += 1
            continue
        t = next(fallback_iter, None)
        if t is not None:
            pairs.append((d, t))
        else:
            unmatched += 1
    return pairs, unmatched


def build_pnl_by_ticket(trades: list[dict]) -> dict[str, float]:
    """{ticket: total realized P&L} from mt5_gateway.recent_closed_trades()'s
    own rows. Sums rather than overwrites: a position closed in more than
    one partial exit produces multiple rows sharing the same ticket
    (position_id) - summing keeps that trade's FULL realized P&L instead
    of only whichever partial-close row happened to be seen last.
    """
    pnl_by_ticket: dict[str, float] = {}
    for t in trades:
        ticket = str(t["ticket"])
        pnl_by_ticket[ticket] = pnl_by_ticket.get(ticket, 0.0) + t["pnl_dollars"]
    return pnl_by_ticket


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


def format_report(buckets: dict[tuple, Bucket], freq: dict[str, int], unmatched: int) -> str:
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
    if unmatched:
        lines.append("")
        lines.append(f"WARNING: {unmatched} executed decision(s) in decisions.csv had no matching "
                     "row in trades.csv (by ticket, or by position for dry-run/legacy rows) - one "
                     "of the files may have been hand-edited or truncated independently of the "
                     "other; those decisions were excluded from the outcome buckets above rather "
                     "than risk pairing them with the wrong trade.")
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

    pairs, unmatched = join_decisions_and_trades(decisions, trades)

    pnl_by_ticket: dict[str, float] = {}
    if not args.no_mt5:
        import mt5_gateway as gw
        gw.connect(login=args.login, password=args.password, server=args.server,
                  terminal_path=args.terminal_path)
        pnl_by_ticket = build_pnl_by_ticket(gw.recent_closed_trades(
            args.symbol, args.magic, count=5000, lookback_days=args.lookback_days))

    buckets = summarize_by_bucket(pairs, pnl_by_ticket)
    freq = conviction_frequency(decisions)
    print(format_report(buckets, freq, unmatched))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

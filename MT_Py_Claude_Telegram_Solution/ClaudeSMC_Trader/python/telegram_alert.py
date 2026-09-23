"""
Sends one-way Telegram notifications: a "full" conviction verdict alert (see
main.py's run_once()) and an optional daily/weekly performance digest (see
main.py's send_performance_digests()). This is send-only, not a signal source:
it never reads Telegram, never places an order, and is completely
independent of the Telegram signal-copying stack elsewhere in this repo
(../../python/telegram_copier.py, ../../MQL5/Experts/TelegramSMC_Copier.mq5,
UnifiedTrader_EA.mq5's own Telegram side) - a bad bot token or a Telegram
outage here can never affect trading, and vice versa.

Fires on EVERY "full" conviction verdict, whether or not the trade actually
executes - executor.gate() can still reject it (position cap, daily trade
limit, confluence floor, the breaking-news check), and the alert message
says so either way. It carries the entry, SL, TP1 lock (+ trail) and
Claude's TP2/TP3 structure targets, plus the news-check result. Off by
default: config.py's telegram_alert_bot_token/telegram_alert_chat_id are
both "" until you set them (see README.md), and send_alert() is a safe
no-op with either blank.

Uses only the standard library (urllib) - no new dependency beyond what's
already in requirements.txt.

send_alert() takes the HTTP-posting function as a parameter (dependency
injection, same philosophy as executor.py's `gateway` and claude_advisor.py's
`client` parameters) so selftest.py exercises it with a fake poster that
records the call instead of ever touching the network.
"""
from __future__ import annotations

import json
import logging
import urllib.request

log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> None:
    """The real HTTP POST - swapped out in selftest.py for a fake poster."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


def structure_targets(plan, targets, limit: int = 2) -> list[float]:
    """Claude's take_profit_targets that lie BEYOND TP1 in the trade's
    direction, nearest first - a level short of the TP1 lock is not a take
    profit for this exit shape, so it is not shown as one."""
    if plan is None:
        return []
    if plan.direction == "buy":
        beyond = sorted(t for t in targets if t > plan.tp1_price)
    else:
        beyond = sorted((t for t in targets if 0 < t < plan.tp1_price), reverse=True)
    return beyond[:limit]


def format_full_conviction_message(symbol: str, verdict, executed: bool,
                                    reject_reason: str = "", plan=None, news_note: str = "",
                                    dry_run: bool = False, digits: int = 2) -> str:
    """verdict is a claude_advisor.ConfluenceVerdict. `executed`/
    `reject_reason`/`plan`/`news_note` come straight from the
    executor.Decision this verdict produced (plan is an executor.TradePlan,
    or None when no levels could be priced) - see main.py's run_once()."""
    if executed:
        status = "EXECUTED" + (" (dry-run - no real order sent)" if dry_run else "")
    else:
        status = "NOT executed - " + reject_reason if reject_reason else "NOT executed"
    lines = [
        f"Claude full conviction: {verdict.direction.upper()} {symbol} "
        f"(confluence {verdict.confluence_count}/3)",
        f"Status: {status}",
    ]
    if plan is not None:
        f = f"{{:.{digits}f}}".format
        lines.append(f"Entry: {f(plan.entry_price)} (market, {plan.lots:.2f} lot)")
        lines.append(f"SL: {f(plan.sl_price)} (risk ${plan.risk_money:,.2f})")
        if plan.broker_tp:
            # The broker TP closes the whole position at TP1 - nothing further to show.
            lines.append(f"TP1: {f(plan.tp1_price)} broker take-profit (+${plan.tp1_money:,.2f})")
        else:
            lines.append(f"TP1: {f(plan.tp1_price)} - stop moves here to lock +${plan.tp1_money:,.2f}, "
                         f"then trails {f(plan.trail_distance)} behind price")
            for i, target in enumerate(structure_targets(plan, verdict.take_profit_targets), start=2):
                lines.append(f"TP{i}: {f(target)} (Claude's structure target - the trail decides the exit)")
    elif verdict.take_profit_targets:
        lines.append("Targets: " + ", ".join(f"{t:.{digits}f}" for t in verdict.take_profit_targets[:3]))
    if news_note:
        lines.append(f"News check: {news_note}")
    lines.append(f"Reasoning: {verdict.reasoning}")
    return "\n".join(lines)


def format_account_line(equity: float | None, day_start_equity: float | None = None) -> str:
    """"Equity $10,245.30 (today $+32.10, +0.31%)" - "" when equity is
    unknown (an MT5 read failed), so callers can append it unconditionally."""
    if equity is None or equity <= 0:
        return ""
    line = f"Equity ${equity:,.2f}"
    if day_start_equity and day_start_equity > 0:
        change = equity - day_start_equity
        line += f" (today ${change:+,.2f}, {change / day_start_equity * 100.0:+.2f}%)"
    return line


def format_performance_digest(symbol: str, period_label: str, trades: list[dict],
                              equity: float | None = None) -> str:
    """trades: mt5_gateway.recent_closed_trades()'s own shape, already
    filtered to the period being reported on - see main.py's
    send_performance_digests(). period_label is e.g. "Daily" or "Weekly".
    `equity`, when known, is appended as the current account equity.
    """
    account = format_account_line(equity)
    suffix = f"\n{account}" if account else ""
    if not trades:
        return f"{period_label} digest for {symbol}: no closed trades.{suffix}"
    wins = [t for t in trades if t["pnl_dollars"] > 0]
    losses = [t for t in trades if t["pnl_dollars"] < 0]
    net = sum(t["pnl_dollars"] for t in trades)
    win_rate = len(wins) / len(trades) * 100.0
    return (f"{period_label} digest for {symbol}: {len(trades)} trades, "
            f"{len(wins)}W/{len(losses)}L ({win_rate:.0f}% win rate), net P&L ${net:+.2f}{suffix}")


def format_verdict_digest(symbol: str, verdict, executed: bool, reject_reason: str = "") -> str:
    """Plain-text summary of a Claude verdict at ANY conviction level -
    unlike format_full_conviction_message() (which only ever fires for
    conviction="full"), this is written after EVERY evaluation cycle (see
    main.py's run_once()) so UnifiedTrader_EA.mq5's "Why" Telegram command
    always has something current to echo back, even when Claude called
    "none" or "partial". Plain ASCII-safe text - see mt5_gateway.
    write_common_file()'s own note on why.
    """
    status = "EXECUTED" if executed else ("NOT executed - " + reject_reason if reject_reason
                                          else "NOT executed")
    return (
        f"{verdict.direction.upper()} {symbol} | conviction={verdict.conviction} "
        f"confluence={verdict.confluence_count}/3 | {status}\n"
        f"Reasoning: {verdict.reasoning}"
    )


def format_heartbeat_message(symbol: str, minutes_since_last_success: float,
                             equity: float | None = None, day_start_equity: float | None = None) -> str:
    account = format_account_line(equity, day_start_equity)
    return (f"Heartbeat: ClaudeSMC_Trader ({symbol}) is running - last successful evaluation "
            f"cycle {minutes_since_last_success:.0f} min ago." + (f"\n{account}" if account else ""))


def format_stale_cycle_alert(symbol: str, minutes_since_last_success: float) -> str:
    return (f"WARNING: ClaudeSMC_Trader ({symbol}) has had no successful evaluation cycle in "
            f"{minutes_since_last_success:.0f} min - the poll loop may be stuck on a repeating "
            f"error (dropped MT5 connection?). Check the log.")


def send_alert(bot_token: str, chat_id: str, text: str, poster=_post_json) -> bool:
    """Never raises - a Telegram outage (bad token, network down, rate
    limited) must never interrupt the trading loop this is a side-effect of.
    Returns True on an apparent success, False otherwise (and logs why,
    except for the blank-credentials no-op case, which is the normal "alerts
    just aren't configured" state, not a failure worth logging every cycle).
    """
    # A token pasted with a trailing newline/space (common in environment
    # variables) would otherwise make http.client raise InvalidURL - whose
    # message contains the URL, i.e. the token, straight into the log.
    bot_token, chat_id = (bot_token or "").strip(), str(chat_id or "").strip()
    if not bot_token or not chat_id:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    try:
        poster(url, {"chat_id": chat_id, "text": text[:4000]})   # Telegram's limit is 4096
        return True
    except Exception as exc:
        log.warning("Telegram alert failed (bad bot token/chat id, network down, or rate "
                    "limited) - trading continues unaffected: %s",
                    str(exc).replace(bot_token, "<bot-token>"))
        return False

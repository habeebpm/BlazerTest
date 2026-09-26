#!/usr/bin/env python3
"""
Demo scorecard - how the system is REALLY doing, from MT5's own deal history
(real fills, both sources), with a plain verdict against thresholds fixed in
advance, so a good or bad week is never over-read.

    Claude trades    magic 20260921
    Telegram trades  magic 20260922
    Combined

For each: trades, win %, net P&L, average result in R (1R = the risk of the
trade: the stop it was opened with - the $6 stop, or a Telegram signal's own
stop), profit factor, worst
drawdown in R, and the bootstrap chance that the true average trade is zero
or worse. Verdict rules (decided before any demo trade, never tuned on it):

    fewer than 30 trades                -> TOO EARLY    keep running
    chance of no edge >= 80% or a drawdown of 10R (~20% of the account at
    2% risk)                            -> STOP AND REVIEW
    average R > 0 and chance of no edge <= 20%
                                        -> ON TRACK
    anything else                       -> NOT PROVEN YET   keep running

Runs weekly from start.bat (settings.ini [scorecard]) and sends the result
to your Telegram; or any time: python goldtrader.py scorecard
"""
from __future__ import annotations

import argparse
import logging
import os
import random

MIN_TRADES = 30
ON_TRACK_MAX_P = 0.20
STOP_MIN_P = 0.80
STOP_DRAWDOWN_R = 10.0

log = logging.getLogger("scorecard")


def summarize(trades: list, per_price: float, sl_dist: float, seed: int = 7) -> dict:
    """trades: dicts with pnl_dollars and volume, oldest first. per_price =
    account money per 1.0 price move per lot (tick_value / tick_size);
    sl_dist = the fixed stop's price distance (sl_dollars at the reference
    lot) - 1R for a trade without its own risk_distance (a Telegram trade
    opened with the signal's stop is measured against that stop)."""
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    r = [t["pnl_dollars"] / (t["volume"] * per_price * (t.get("risk_distance") or sl_dist))
         if t["volume"] > 0 else 0.0 for t in trades]
    wins = [x for x in trades if x["pnl_dollars"] > 0]
    gross_win = sum(x["pnl_dollars"] for x in wins)
    gross_loss = -sum(x["pnl_dollars"] for x in trades if x["pnl_dollars"] <= 0)
    equity = peak = max_dd = 0.0
    for x in r:
        equity += x
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    rng = random.Random(seed)
    boots = [sum(rng.choice(r) for _ in range(n)) / n for _ in range(2000)]
    return {
        "trades": n,
        "win_pct": round(100.0 * len(wins) / n, 1),
        "net_pnl": round(sum(x["pnl_dollars"] for x in trades), 2),
        "avg_r": round(sum(r) / n, 3),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_dd_r": round(max_dd, 2),
        "p_no_edge": round(sum(1 for b in boots if b <= 0) / len(boots), 3),
    }


def verdict(s: dict) -> tuple:
    """(label, advice) for one summary - the fixed rules in the docstring."""
    n = s.get("trades", 0)
    if n < MIN_TRADES:
        return "TOO EARLY", f"{n}/{MIN_TRADES} trades - keep running, no conclusion yet"
    if s["p_no_edge"] >= STOP_MIN_P or s["max_dd_r"] >= STOP_DRAWDOWN_R:
        why = (f"{s['p_no_edge']:.0%} chance of no edge" if s["p_no_edge"] >= STOP_MIN_P
               else f"drawdown {s['max_dd_r']:.1f}R")
        return "STOP AND REVIEW", f"{why} - pause this source (PauseClaudeHab/PauseTelHab) and review"
    if s["avg_r"] > 0 and s["p_no_edge"] <= ON_TRACK_MAX_P:
        return "ON TRACK", "positive and unlikely to be luck - keep running on demo, then go small"
    return "NOT PROVEN YET", "keep running - the result is still within luck"


def format_report(symbol: str, by_source: dict, days: int) -> str:
    lines = [f"GoldTrader scorecard - {symbol}, last {days} days (MT5 history)"]
    for name, s in by_source.items():
        label, advice = verdict(s)
        if not s.get("trades"):
            lines.append(f"\n{name}: no closed trades yet - {label}")
            continue
        pf = f"{s['profit_factor']:.2f}" if s["profit_factor"] is not None else "n/a"
        lines.append(
            f"\n{name}: {label}\n"
            f"  {s['trades']} trades, win {s['win_pct']}%, net {s['net_pnl']:+.2f}\n"
            f"  avg {s['avg_r']:+.2f}R, profit factor {pf}, worst drawdown {s['max_dd_r']:.1f}R\n"
            f"  chance of no real edge: {s['p_no_edge']:.0%}\n"
            f"  -> {advice}")
    return "\n".join(lines)


def build(trades: list, magics: dict, per_price: float, sl_dist: float) -> dict:
    """{source name: summary} for each magic plus the combined book."""
    out = {}
    for name, magic in magics.items():
        out[name] = summarize([t for t in trades if t["magic"] == magic], per_price, sl_dist)
    if len(magics) > 1:          # one source (the BTC instance): Combined would only repeat it
        out["Combined"] = summarize([t for t in trades if t["magic"] in magics.values()], per_price, sl_dist)
    return out


def main(argv: list | None = None) -> int:
    from config import AdvisorConfig
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--days", type=int, default=120, help="history to include (default 120)")
    ap.add_argument("--magic", type=int, default=AdvisorConfig().magic, help="Claude's magic number")
    ap.add_argument("--telegram-magic", type=int, default=20260922, dest="telegram_magic",
                    help="the EA's Telegram magic number (0 = no Telegram side, e.g. the BTC instance)")
    ap.add_argument("--no-telegram", action="store_true", dest="no_telegram",
                    help="print only, do not send to Telegram")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    import keys
    import mt5_gateway as gw
    import telegram_alert
    keys.load()
    cfg = AdvisorConfig(symbol=args.symbol)
    try:
        gw.connect()
        spec = gw.symbol_spec(args.symbol)
        gw.server_utc_offset_seconds(args.symbol)
        magics = {"Claude": args.magic}
        if args.telegram_magic:
            magics["Telegram signals"] = args.telegram_magic
        trades = gw.closed_trades(args.symbol, list(magics.values()), lookback_days=args.days)
    except RuntimeError as exc:
        log.error("Cannot read MT5 history: %s - start MT5 and try again.", exc)
        return 2
    per_price = spec.tick_value / spec.tick_size if spec.tick_size > 0 else 0.0
    sl_dist = gw.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.reference_lot)
    if per_price <= 0 or sl_dist <= 0:
        log.error("Symbol spec for %s has no tick value/size - cannot price R.", args.symbol)
        return 2
    report = format_report(args.symbol, build(trades, magics, per_price, sl_dist), args.days)
    print(report)
    if not args.no_telegram:
        token = os.environ.get("TELEGRAM_ALERT_BOT_TOKEN", "")
        chat = os.environ.get("TELEGRAM_ALERT_CHAT_ID", "")
        if token and chat:
            telegram_alert.send_alert(token, chat, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

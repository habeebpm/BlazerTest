"""
logs/status.json - everything the web dashboard (dashboard/Default.aspx)
shows, written by main.py's loop at most once a minute:

    account        balance, equity, free margin, today's start and change
    positions      every open position on the symbol, tagged Claude/Telegram
    closed         closed trades per source from MT5's own deal history
    scorecard      the weekly scorecard's numbers and verdict, live
    decisions      Claude's recent verdicts (logs/decisions.csv)
    signals        the EA's Telegram signal log (MQL5\\Files\\TelegramSMC_Signals.csv)

One file, so the web server only ever needs read access to the logs folder
- never to MT5's folders or keys.txt. Written atomically (temp file +
rename), so the page never reads half a file. Never raises: a problem here
must never touch trading.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import scorecard
import tactics

log = logging.getLogger("status")

FILE_NAME = "status.json"
EVERY_SECONDS = 60
SCORECARD_EVERY_SECONDS = 900     # the bootstrap is the slow part - 15 minutes is fresh enough
CLOSED_DAYS = 120                 # same window as the weekly scorecard
MAX_CLOSED = 300
MAX_DECISIONS = 40
MAX_SIGNALS = 40
SIGNALS_FILE = "TelegramSMC_Signals.csv"
TELEGRAM_MAGIC_DEFAULT = 20260922

_state = {"last": 0.0, "scorecard_at": 0.0, "scorecard": None, "warned": False, "claude_problem": ""}


def note_claude_problem(reason: str) -> None:
    """Claude stopped for a reason that needs the owner (credits, API key,
    model) - shown on the dashboard until a cycle succeeds ("" clears it)."""
    _state["claude_problem"] = reason or ""


def source_magics(cfg) -> dict:
    """{magic: "Claude" | "Telegram"} - the Telegram magic is the shared-cap
    magic from start.bat (--shared-cap-magic), else the EA's default."""
    if getattr(cfg, "instrument", "gold") != "gold":      # BTC: Claude only, no Telegram side
        return {int(cfg.magic): "Claude"}
    tel = cfg.shared_cap_magic_numbers[0] if cfg.shared_cap_magic_numbers else TELEGRAM_MAGIC_DEFAULT
    return {int(cfg.magic): "Claude", int(tel): "Telegram"}


def read_text(path: str) -> str | None:
    """A text file MT5 or Python may be writing right now: UTF-16 by BOM,
    else UTF-8, else the Windows ANSI code page (MT5's FILE_ANSI)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def tail_csv(path: str, n: int) -> list:
    """The last n rows of a CSV as dicts, newest first; [] when missing."""
    text = read_text(path)
    if not text:
        return []
    rows = deque(csv.DictReader(io.StringIO(text)), maxlen=n)
    return [{k: (v or "") for k, v in r.items() if k is not None} for r in reversed(rows)]


def _iso(t) -> str:
    if isinstance(t, datetime):
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        return t.astimezone(timezone.utc).isoformat(timespec="seconds")
    return str(t or "")


def _account(gateway) -> dict:
    fn = getattr(gateway, "account_summary", None)
    if fn is not None:
        return fn()
    return {"equity": gateway.account_equity()}


def _positions(gateway, cfg, names: dict) -> list:
    out = []
    for p in gateway.symbol_positions(cfg.symbol):
        out.append({
            "ticket": str(p.get("ticket", "")),
            "source": names.get(int(p.get("magic", 0) or 0), "Other"),
            "direction": p.get("direction", ""),
            "volume": float(p.get("volume", 0.0)),
            "price_open": float(p.get("price_open", 0.0)),
            "sl": float(p.get("sl", 0.0)),
            "tp": float(p.get("tp", 0.0)),
            "profit": float(p.get("profit", 0.0)),
            "time": _iso(p.get("time")),
        })
    return sorted(out, key=lambda r: r["time"], reverse=True)


def _scorecard(gateway, cfg, spec, trades: list, names: dict, now: float) -> dict | None:
    if _state["scorecard"] is not None and now - _state["scorecard_at"] < SCORECARD_EVERY_SECONDS:
        return _state["scorecard"]
    per_price = spec.tick_value / spec.tick_size if spec.tick_size > 0 else 0.0
    sl_dist = gateway.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.reference_lot)
    if per_price <= 0 or sl_dist <= 0:
        return None
    by_source = scorecard.build(trades, {name: magic for magic, name in names.items()}, per_price, sl_dist)
    out = {}
    for name, s in by_source.items():
        label, advice = scorecard.verdict(s)
        out[name] = {**s, "verdict": label, "advice": advice}
    _state["scorecard"], _state["scorecard_at"] = out, now
    return out


def _signals_path(gateway) -> str:
    fn = getattr(gateway, "terminal_files_dir", None)
    if fn is None:
        return ""
    return os.path.join(fn(), SIGNALS_FILE)


def build(gateway, cfg, spec, day, now: float | None = None) -> dict:
    """The whole report. Each part that fails is left out with the reason in
    "problems" - the rest is still written."""
    now = time.time() if now is None else now
    names = source_magics(cfg)
    problems = []
    report = {
        "version": 1,
        "updated_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "symbol": cfg.symbol,
        "mode": "dry-run" if cfg.dry_run else "live",
        "trade_hours": cfg.trade_windows,
        "trade_days": cfg.trade_days,
        "trade_zone": tactics.zone_label(cfg),
        "claude_hours_local": tactics.windows_in(cfg, cfg.display_timezone, datetime.fromtimestamp(now, timezone.utc)),
        "local_zone": tactics.ZONE_LABELS.get(cfg.display_timezone, cfg.display_timezone),
        "telegram_hours": cfg.telegram_trade_windows if cfg.instrument == "gold" else "",
        "telegram_zone": "Oman" if cfg.telegram_utc_offset_hours == 4 else f"UTC{cfg.telegram_utc_offset_hours:+g}",
        "telegram_days": "Mon-Fri" if cfg.telegram_weekdays_only else "",
        "sources": {name: magic for magic, name in names.items()},
        "rules": {"risk_percent": cfg.risk_percent, "sl_dollars": cfg.sl_dollars,
                  "tp1_dollars": cfg.tp1_dollars, "trail_dollars": cfg.trail_dollars,
                  "max_per_direction": cfg.max_open_positions_per_direction,
                  "max_daily_loss_pct": cfg.max_daily_loss_pct,
                  "instrument": cfg.instrument, "lock_mode": cfg.lock_mode,
                  "lock_r": cfg.lock_r, "trail_r": cfg.trail_r, "sl_atr_mult": cfg.sl_atr_mult,
                  "telegram_signal_sl": cfg.telegram_use_signal_sl and cfg.instrument == "gold",
                  "signal_sl_min": cfg.signal_sl_min_distance, "signal_sl_max": cfg.signal_sl_max_distance,
                  "weekend_max_daily_loss_pct": cfg.weekend_max_daily_loss_pct,
                  "weekend_max_per_direction": cfg.weekend_max_positions_per_direction,
                  "weekend_active": tactics.weekend_mode(cfg, datetime.fromtimestamp(now, timezone.utc))},
    }

    def part(key, fn):
        try:
            report[key] = fn()
        except Exception as exc:          # one missing piece never blanks the page
            problems.append(f"{key}: {exc}")

    part("account", lambda: _account(gateway))
    report["day"] = {
        "date": str(day.date), "start_equity": day.day_start_equity, "claude_trades": day.trades_today,
        "daily_loss_hit": day.daily_loss_hit, "daily_target_hit": day.daily_target_hit,
    }
    part("claude_paused", lambda: (gateway.read_common_file(cfg.claude_pause_filename) or "").strip().lower()
         == "paused" if cfg.claude_pause_filename else False)
    part("last_verdict", lambda: (gateway.read_common_file(cfg.last_verdict_filename) or "")
         if cfg.last_verdict_filename else "")
    part("positions", lambda: _positions(gateway, cfg, names))
    trades = []

    def closed():
        trades.extend(gateway.closed_trades(cfg.symbol, list(names), lookback_days=CLOSED_DAYS))
        return [{"time": _iso(t["time"]), "source": names.get(int(t["magic"]), "Other"),
                 "direction": t["direction"], "volume": float(t.get("volume", 0.0)),
                 "pnl": round(float(t["pnl_dollars"]), 2), "ticket": str(t.get("ticket", ""))}
                for t in reversed(trades[-MAX_CLOSED:])]
    part("closed", closed)
    if "closed" in report:
        part("scorecard", lambda: _scorecard(gateway, cfg, spec, trades, names, now))
    part("decisions", lambda: tail_csv(os.path.join(cfg.log_dir, "decisions.csv"), MAX_DECISIONS))
    if cfg.instrument == "gold":             # the Telegram side belongs to the gold instance only
        part("signals", lambda: [
            {k: r.get(k, "") for k in ("time_utc", "action", "direction", "accepted", "sanity_reason",
                                      "smc_reason", "order_type", "order_price", "lots", "sl", "dry_run",
                                      "raw_text")}
            for r in tail_csv(_signals_path(gateway), MAX_SIGNALS)])
    else:
        report["signals"] = []
    report["claude_problem"] = _state["claude_problem"]
    report["problems"] = problems
    return report


def write(path: str, report: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def maybe_write(gateway, cfg, spec, day, now: float | None = None) -> bool:
    """Called on every pass of main.py's loop; writes at most every
    EVERY_SECONDS. True when a file was written. Never raises."""
    now = time.time() if now is None else now
    if now - _state["last"] < EVERY_SECONDS:
        return False
    _state["last"] = now
    try:
        write(os.path.join(cfg.log_dir, FILE_NAME), build(gateway, cfg, spec, day, now))
        return True
    except Exception as exc:
        if not _state["warned"]:
            _state["warned"] = True
            log.warning("Could not write the dashboard file %s (%s) - trading is not affected.",
                        FILE_NAME, exc)
        return False

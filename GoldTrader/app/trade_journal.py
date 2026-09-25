"""
Trade journal for analysis: CSV files written by main.py's loop every few
minutes and copied into config.journal_folder, a Google Drive for Desktop
folder (default G:\\MyDrive\\MyMQChartDrive\\GoldTrader), so they can be read
straight from Drive instead of being uploaded by hand:

    GoldTrader_trades.csv            every closed trade, both sources, from MT5's
                                     own history: times (UTC and local), entry,
                                     exit, first stop, who closed it, money, R
    GoldTrader_claude_decisions.csv  logs/decisions.csv - every Claude evaluation
    GoldTrader_telegram_signals.csv  the EA's signal log - every Telegram message
                                     and why it was or wasn't copied

The trades file is rebuilt from MT5's history each time, so nothing is lost
across restarts. A file is written only when its content changed (Drive then
uploads only real changes). Never raises: a problem here must never touch
trading.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import time
from datetime import timezone
from zoneinfo import ZoneInfo

import status_report
import tactics

log = logging.getLogger("journal")

EVERY_SECONDS = 300
TRADES_FILE = "GoldTrader_trades.csv"
DECISIONS_FILE = "GoldTrader_claude_decisions.csv"
SIGNALS_FILE = "GoldTrader_telegram_signals.csv"
# An exit on the stop between these points can't be the EA's own stop: it
# opens at -1R (the first stop) and only ever moves it to the +$6 lock or
# beyond. As fractions of the first stop's and the lock's distance, so
# slippage on either is allowed for.
HAND_MOVED = (0.8, 0.7)

COLUMNS = ["ticket", "source", "direction", "lots", "open_time_utc", "close_time_utc",
           "open_time_local", "close_time_local", "minutes_open", "entry_price", "exit_price",
           "initial_sl", "stop_distance", "exit_reason", "move", "result_r", "profit", "swap", "commission",
           "net_pnl", "note"]

_state = {"last": 0.0, "warned": set()}


def _warn_once(key: str, text: str, *args) -> None:
    if key not in _state["warned"]:
        _state["warned"].add(key)
        log.warning(text, *args)


def trade_rows(positions: list, names: dict, sl_distance: float, local_zone: str,
               lock_distance: float = 0.0) -> list:
    """journal_positions() output -> CSV rows. 1R is each trade's own first
    stop (a Telegram signal's stop, or the fixed one); `sl_distance` - the
    fixed stop's price distance - when the first stop isn't known.
    `lock_distance`: the +$6 lock's price distance (default: sl_distance)."""
    zone = ZoneInfo(local_zone)
    lock = lock_distance or sl_distance
    out = []
    for p in positions:
        sign = 1.0 if p["direction"] == "buy" else -1.0
        move = sign * (p["exit_price"] - p["entry_price"]) if p["entry_price"] and p["exit_price"] else 0.0
        risk = abs(p["entry_price"] - p["initial_sl"]) if p.get("initial_sl") and p["entry_price"] else 0.0
        risk = risk or sl_distance
        r = move / risk if risk > 0 else 0.0
        note = ""
        if (p["exit_reason"] == "stop loss" and risk > 0
                and -HAND_MOVED[0] * risk < move < HAND_MOVED[1] * lock):
            note = "stop moved by hand"
        opened, closed = p.get("open_time"), p.get("close_time")
        out.append({
            "ticket": p["ticket"],
            "source": names.get(int(p["magic"]), "Other"),
            "direction": p["direction"],
            "lots": round(p["volume"], 2),
            "open_time_utc": _fmt(opened, timezone.utc),
            "close_time_utc": _fmt(closed, timezone.utc),
            "open_time_local": _fmt(opened, zone),
            "close_time_local": _fmt(closed, zone),
            "minutes_open": round((closed - opened).total_seconds() / 60) if opened and closed else "",
            "entry_price": round(p["entry_price"], 2),
            "exit_price": round(p["exit_price"], 2),
            "initial_sl": round(p["initial_sl"], 2) if p.get("initial_sl") else "",
            "stop_distance": round(risk, 2),
            "exit_reason": p["exit_reason"],
            "move": round(move, 2),
            "result_r": round(r, 2),
            "profit": round(p["profit"], 2),
            "swap": round(p["swap"], 2),
            "commission": round(p["commission"], 2),
            "net_pnl": round(p["net"], 2),
            "note": note,
        })
    return out


def _fmt(t, zone) -> str:
    if t is None:
        return ""
    t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return t.astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")


def to_csv(rows: list, columns: list) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_if_changed(path: str, text: str) -> bool:
    """UTF-8 with a BOM (Excel reads it right); untouched when identical."""
    data = ("\ufeff" + text).encode("utf-8")
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    # Written in place, not via a temp file + rename: Drive for Desktop would
    # upload the temp file too. Drive uploads once the file is closed.
    with open(path, "wb") as f:
        f.write(data)
    return True


def target_folder(cfg) -> str:
    """The Drive folder to copy into, created when its parent exists; "" when
    off or not reachable (said once in the log)."""
    folder = (cfg.journal_folder or "").strip()
    if not folder:
        return ""
    if os.path.isdir(folder):
        return folder
    parent = os.path.dirname(folder.rstrip("\\/"))
    if parent and os.path.isdir(parent):
        os.makedirs(folder, exist_ok=True)
        return folder
    _warn_once("folder", "Trade journal: folder %s not found (is Google Drive for Desktop running?) - "
               "the journal stays in the logs folder. Change it with --journal-folder in start.bat.",
               parent or folder)
    return ""


def build(gateway, cfg, spec) -> dict:
    """{file name: CSV text} for every part that could be read."""
    files = {}
    names = status_report.source_magics(cfg)
    try:
        sl_distance = gateway.price_distance_for_dollars(spec, cfg.sl_dollars, cfg.reference_lot)
        lock_distance = gateway.price_distance_for_dollars(spec, cfg.tp1_dollars, cfg.reference_lot)
    except Exception:
        sl_distance = lock_distance = 0.0
    positions = gateway.journal_positions(cfg.symbol, list(names))
    files[TRADES_FILE] = to_csv(trade_rows(positions, names, sl_distance, cfg.display_timezone,
                                           lock_distance), COLUMNS)
    decisions = status_report.read_text(os.path.join(cfg.log_dir, "decisions.csv"))
    if decisions:
        files[DECISIONS_FILE] = decisions.replace("\r\n", "\n")
    try:
        signals = status_report.read_text(status_report._signals_path(gateway))
    except Exception:
        signals = None
    if signals:
        files[SIGNALS_FILE] = signals.replace("\r\n", "\n")
    return files


def write_all(gateway, cfg, spec) -> list:
    """Writes the journal into logs\\journal and the Drive folder; returns the
    paths that changed."""
    files = build(gateway, cfg, spec)
    folders = [os.path.join(cfg.log_dir, "journal")]
    os.makedirs(folders[0], exist_ok=True)
    drive = target_folder(cfg)
    if drive:
        folders.append(drive)
    changed = []
    for folder in folders:
        for name, text in files.items():
            path = os.path.join(folder, name)
            if write_if_changed(path, text):
                changed.append(path)
    return changed


def maybe_write(gateway, cfg, spec, now: float | None = None) -> bool:
    """Called on every pass of main.py's loop; works at most every
    EVERY_SECONDS. Never raises."""
    now = time.time() if now is None else now
    if now - _state["last"] < EVERY_SECONDS:
        return False
    _state["last"] = now
    try:
        changed = write_all(gateway, cfg, spec)
        if changed:
            log.info("Trade journal updated: %s", ", ".join(sorted({os.path.basename(p) for p in changed})))
        return True
    except Exception as exc:
        _warn_once("write", "Could not write the trade journal (%s) - trading is not affected.", exc)
        return False


def describe(cfg) -> str:
    """One line for the start-up log."""
    folder = (cfg.journal_folder or "").strip()
    zone = tactics.ZONE_LABELS.get(cfg.display_timezone, cfg.display_timezone)
    return (f"Trade journal every {EVERY_SECONDS // 60} min -> "
            f"{folder or 'logs only'} (local times: {zone})")

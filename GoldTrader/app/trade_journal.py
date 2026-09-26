"""
Trade journal for analysis: CSV files written by main.py's loop every few
minutes and copied into config.journal_folder, a Google Drive for Desktop
folder (default "auto": MyMQChartDrive\\GoldTrader wherever Drive shows it), so they can be read
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

import legs
import status_report
import tactics

log = logging.getLogger("journal")

EVERY_SECONDS = 300
RESCAN_SECONDS = 1800        # "auto": look for the Drive folder again at most this often
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
           "net_pnl", "note", "leg"]

_state = {"last": 0.0, "warned": set()}


def _warn_once(key: str, text: str, *args) -> None:
    if key not in _state["warned"]:
        _state["warned"].add(key)
        log.warning(text, *args)


def trade_rows(positions: list, names: dict, sl_distance: float, local_zone: str,
               lock_distance: float = 0.0, lock_r: float = 0.0, breakeven_magics=()) -> list:
    """journal_positions() output -> CSV rows. 1R is each trade's own first
    stop (a Telegram signal's stop, or the fixed one); `sl_distance` - the
    fixed stop's price distance - when the first stop isn't known.
    `lock_distance`: the +$6 lock's price distance (default: sl_distance);
    `lock_r` > 0: the lock is that many times each trade's own stop (BTC).
    `breakeven_magics`: sources whose stop goes to break-even by rule (the
    Telegram split's half B) - a stop-out at break-even or better is normal there;
    so is it for any leg 2+ of a split entry (legs.LEG_MARK in its comment).
    "leg": 2, 3 for those legs, 1 for leg 1 / a single position."""
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
        lock_here = lock_r * risk if lock_r > 0 else lock
        follower = legs.is_follower(p.get("comment"))
        by_rule = (int(p.get("magic", 0)) in breakeven_magics or follower) and move >= -0.05
        if (p["exit_reason"] == "stop loss" and risk > 0 and not by_rule
                and -HAND_MOVED[0] * risk < move < HAND_MOVED[1] * lock_here):
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
            "leg": legs.leg_number(p.get("comment")),
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


DRIVE_FOLDER = "MyMQChartDrive"      # the Drive folder the EA's price files already go to
SUBFOLDER = "GoldTrader"


def drive_candidates() -> list:
    """Where Google Drive for Desktop shows MyMQChartDrive on Windows:
    <letter>:\\My Drive\\MyMQChartDrive (Google's own name, with a space) or
    <letter>:\\MyDrive\\MyMQChartDrive, G: first."""
    if os.name != "nt":
        return []
    letters = "GHIJKLMNOPQRSTUVWXYZDEF"
    listdrives = getattr(os, "listdrives", None)          # Python 3.12+ on Windows
    if listdrives is not None:
        try:
            present = {d[0].upper() for d in listdrives()}
            letters = [x for x in letters if x in present]
        except OSError:
            pass
    return [f"{letter}:\\{root}\\{DRIVE_FOLDER}" for letter in letters for root in ("My Drive", "MyDrive")]


def target_folder(cfg) -> str:
    """The Drive folder to copy into; "" when off or not reachable (said
    once in the log). "auto": the GoldTrader subfolder of MyMQChartDrive,
    wherever Drive for Desktop shows it; otherwise the given path, created
    when its parent exists."""
    folder = (cfg.journal_folder or "").strip()
    if not folder:
        return ""
    if folder.lower() == "auto":
        known = _state.get("found")
        if known and os.path.isdir(known):
            return known
        if time.time() < _state.get("next_scan", 0.0):      # not found lately: look again later
            return ""
        _state["next_scan"] = time.time() + RESCAN_SECONDS
        for base in drive_candidates():
            if os.path.isdir(base):
                found = os.path.join(base, SUBFOLDER)
                os.makedirs(found, exist_ok=True)
                if _state.get("found") != found:
                    _state["found"] = found
                    log.info("Trade journal: Google Drive folder found - %s", found)
                return found
        _warn_once("auto", "Trade journal: no Google Drive folder %s found (looked for "
                   "G:\\My Drive\\%s and other drive letters) - is Google Drive for Desktop running? "
                   "The journal stays in the logs folder; or set the folder with "
                   "--journal-folder \"X:\\path\" in start.bat.", DRIVE_FOLDER, DRIVE_FOLDER)
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

    def name(base: str) -> str:              # "GoldTrader_trades.csv" -> "BTC_trades.csv" for BTC
        return cfg.journal_prefix + base[len("GoldTrader_"):]

    lock_r = cfg.lock_r if cfg.lock_mode == "r" else 0.0
    be = tuple(m for m, src in names.items()
               if (src == "Telegram" and getattr(cfg, "telegram_split", False))
               or (src == "Claude" and getattr(cfg, "claude_split", False)))
    files[name(TRADES_FILE)] = to_csv(trade_rows(positions, names, sl_distance, cfg.display_timezone,
                                                 lock_distance, lock_r, breakeven_magics=be), COLUMNS)
    decisions = status_report.read_text(os.path.join(cfg.log_dir, "decisions.csv"))
    if decisions:
        files[name(DECISIONS_FILE)] = decisions.replace("\r\n", "\n")
    if cfg.instrument == "gold":           # only the gold instance has a Telegram side
        try:
            signals = status_report.read_text(status_report._signals_path(gateway))
        except Exception:
            signals = None
        if signals:
            files[name(SIGNALS_FILE)] = signals.replace("\r\n", "\n")
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
    where = ("Google Drive " + DRIVE_FOLDER + "\\" + SUBFOLDER + " (found automatically)"
             if folder.lower() == "auto" else folder or "logs only")
    return f"Trade journal every {EVERY_SECONDS // 60} min -> {where} (local times: {zone})"

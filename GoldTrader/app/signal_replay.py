"""
Signal-history replay: how would the Telegram side have done on your signal
provider's PAST messages? Hours instead of weeks of demo.

    python goldtrader.py replay-signals                 # last 3 months
    python goldtrader.py replay-signals --months 6 --chat @provider

1. Reads the provider's past messages with your own Telegram login (the
   relay's, relay\\tg_relay_bridge.session - asks for phone + code once if
   there is none yet). The chat: --chat, else TELEGRAM_SOURCE_CHANNELS from
   keys.txt, else you pick it from a numbered list.
2. Loads real XAUUSD M1 prices for the same period from MT5.
3. Replays every message the way UnifiedTrader_EA would have read it: the
   same trade-message filter (relay/message_filter.py), the same parser
   (ea_signal_parser.py), the Oman 06:00-23:00 Mon-Fri window, the M15/H1
   filter, the $20 zone-distance check, limit / market / stale, pending
   expiry 240 min, 5 per direction, the 10% daily cap and budget, 2% risk
   lot sizing, the two halves (one takes profit at +$4, the other goes to
   break-even there and trails $3), and CLOSE / CANCEL messages.
4. Three versions side by side:
     fixed     every trade with the fixed $6 stop
     signal    the signal's own stop when $3-$20 away, else $6 (the EA today)
     provider  reference only: the signal's stop and first target, no lock
5. Writes logs\\replay\\ and copies it to Google Drive (MyMQChartDrive\\
   GoldTrader\\GoldTrader_replay_*): summary, every simulated trade, the
   messages and the prices - so Claude can read and re-check it from Drive.

Not modelled: the news blackout (no calendar history), slippage beyond the
fixed spread, the margin guard (assumes enough leverage), edited or deleted
messages (the history shows the final text). Bar-level (M1): when one bar
reaches both the stop and the lock/target, the stop is assumed first.
Nothing here trades or changes a setting.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

import ea_signal_parser as P
import paths

sys.path.insert(0, paths.RELAY_DIR)
import message_filter  # noqa: E402  (relay/, the EA's filter rules in Python)

log = logging.getLogger("replay")

VARIANTS = {
    "fixed": "fixed $6 stop; half at +$4, half break-even then $3 trail (EA with InpTelegramUseSignalSl=false)",
    "signal": "signal's stop if $3-$20 away else $6; half at +$4, half break-even then $3 trail (the EA as "
              "shipped)",
    "provider": "reference: the signal's own stop and first target, no lock/trail",
}
NEW_YORK = ZoneInfo("America/New_York")
OUT_DIR = os.path.join(paths.LOG_DIR, "replay")
PREFIX = "GoldTrader_replay_"


@dataclass
class Settings:
    """The EA inputs that matter for the replay (preset values)."""
    risk_pct: float = 2.0
    fixed_stop: float = 6.0          # price distance ($6 at the 0.01 reference lot = $6 of gold price)
    lock: float = 6.0                # InpTelegramSplit=false: lock at +$6, then trail $3
    trail: float = 3.0
    split: bool = True               # InpTelegramSplit: two halves - A takes profit at +split_tp,
    split_tp: float = 4.0            # B goes to break-even there and trails split_trail
    split_trail: float = 3.0
    signal_min: float = 3.0
    signal_max: float = 20.0
    provider_max: float = 50.0       # "provider": any stop on the right side up to this far
    max_per_direction: int = 5
    daily_loss_pct: float = 10.0
    max_deviation: float = 20.0      # InpMaxEntryDeviationPips 200 x $0.10
    expiry_minutes: int = 240
    spread: float = 0.30
    buffer: float = 0.03             # 3 points
    trade_hours: str = "06:00-23:00"
    utc_offset_hours: float = 4.0
    weekdays_only: bool = True
    htf_filter: bool = True
    per_price_lot: float = 100.0     # account money per 1.0 price move per 1.0 lot (100 oz)
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 5.0
    start_equity: float = 10000.0


@dataclass
class Pos:
    direction: str
    lots: float
    entry: float
    sl: float
    first_sl: float
    tp: float | None
    stop_source: str
    msg_time: datetime
    open_time: datetime | None = None
    order_type: str = "MARKET"
    expires: datetime | None = None
    armed: bool = False
    from_bar: int = 0                # first M1 bar that can touch it (a market fill: the bar after the signal)
    leg: str = ""                    # "A" (take-profit half), "B" (break-even + trail half), "" (one position)


# --- hours, days ---------------------------------------------------------------

def _windows(spec: str) -> list:
    out = []
    for part in spec.split(","):
        a, b = part.strip().split("-")
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        out.append((ha * 60 + ma, hb * 60 + mb))
    return out


def hours_reason(t: datetime, s: Settings) -> str:
    local = t + timedelta(hours=s.utc_offset_hours)
    if s.weekdays_only and local.weekday() >= 5:
        return "outside trading hours (weekend)"
    minute = local.hour * 60 + local.minute
    for a, b in _windows(s.trade_hours):
        if (a <= b and a <= minute < b) or (a > b and (minute >= a or minute < b)):
            return ""
    return "outside trading hours"


def broker_day(t: datetime):
    """The gold broker's server day (New York + 7h) - the EA's daily cap day."""
    return (t.astimezone(NEW_YORK) + timedelta(hours=7)).date()


# --- prices ------------------------------------------------------------------------

class Prices:
    """M1 bid bars (time = bar OPEN, UTC) + the M15/H1 trend class at any time."""

    def __init__(self, bars: pd.DataFrame, htf: bool = True):
        b = bars.sort_values("time").reset_index(drop=True)
        self.time = pd.to_datetime(b["time"], utc=True)
        self.t = [x.to_pydatetime() for x in self.time]
        self.o = b["open"].astype(float).tolist()
        self.h = b["high"].astype(float).tolist()
        self.lo = b["low"].astype(float).tolist()
        self.c = b["close"].astype(float).tolist()
        self.cls = {}
        if htf and len(b) > 0:
            self.cls = {"M15": self._classes(b, "15min"), "H1": self._classes(b, "1h")}

    @staticmethod
    def _classes(b: pd.DataFrame, rule: str) -> pd.Series:
        import xtr_logic
        df = b.set_index(pd.to_datetime(b["time"], utc=True))
        agg = df.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(agg) < 40:
            return pd.Series(dtype=object)
        return xtr_logic._classes(agg.reset_index(drop=True)).set_axis(agg.index)

    def trend_against(self, t: datetime, direction: str) -> str:
        """The EA's XtrHtfOpposes: a CLEAR M15 or H1 (last closed bar) against."""
        want = "bullish" if direction == "buy" else "bearish"
        for name, span in (("M15", timedelta(minutes=15)), ("H1", timedelta(hours=1))):
            s = self.cls.get(name)
            if s is None or len(s) == 0:
                continue
            closed = s[s.index <= pd.Timestamp(t) - span]
            if len(closed) < 1:
                continue
            c = str(closed.iloc[-1]).lower()
            if c in ("bullish", "bearish") and c != want:
                return f"XTR HTF filter: {name} is clearly {c}"
        return ""


# --- the simulation ----------------------------------------------------------------

class Replay:
    def __init__(self, variant: str, prices: Prices, s: Settings):
        self.v, self.p, self.s = variant, prices, s
        self.balance = s.start_equity
        self.open: list[Pos] = []
        self.pending: list[Pos] = []
        self.trades: list[dict] = []
        self.skips = Counter()
        self.i = 0                      # next M1 bar not yet processed
        self.day = None
        self.day_start = s.start_equity
        self.peak = s.start_equity
        self.max_dd = 0.0
        self.orders = 0
        self.targets: list = []

    # prices -------------------------------------------------------------
    def bid_now(self) -> float | None:
        return self.p.c[self.i - 1] if self.i > 0 else None

    def advance_to(self, t: datetime) -> None:
        """Processes every M1 bar that has fully closed by `t`."""
        while self.i < len(self.p.t) and self.p.t[self.i] + timedelta(minutes=1) <= t:
            self._bar(self.i)
            self.i += 1

    def _bar(self, k: int) -> None:
        s, p = self.s, self.p
        bt = p.t[k] + timedelta(minutes=1)
        o, h, lo = p.o[k], p.h[k], p.lo[k]
        still = []
        for q in self.pending:
            if q.expires is not None and p.t[k] >= q.expires:
                self.skips["pending order expired unfilled"] += 1
                continue
            if q.direction == "buy" and lo + s.spread <= q.entry:
                q.entry = min(q.entry, o + s.spread)
            elif q.direction == "sell" and h >= q.entry:
                q.entry = max(q.entry, o)
            else:
                still.append(q)
                continue
            q.open_time, q.from_bar = p.t[k], k       # a fill and the stop in one bar: stop assumed
            self.open.append(q)
        self.pending = still
        keep = []
        for q in self.open:
            if k < q.from_bar:
                keep.append(q)
                continue
            if q.direction == "sell":
                hh, ll, oo = h + s.spread, lo + s.spread, o + s.spread
            else:
                hh, ll, oo = h, lo, o
            price, why = self._manage(q, hh, ll)
            if price is None:
                keep.append(q)
                continue
            if why in ("stop", "trail") and ((q.direction == "buy" and oo < q.sl) or
                                             (q.direction == "sell" and oo > q.sl)):
                price = oo
            self._close(q, price, why, bt)
        self.open = keep

    def _manage(self, q: Pos, h: float, lo: float):
        s = self.s
        if q.direction == "buy":
            if lo <= q.sl:
                return q.sl, "trail" if q.armed else "stop"
            if q.tp is not None and h >= q.tp:
                return q.tp, "target"
            if self.v == "provider" or q.leg == "A":
                return None, None
            if q.leg == "B":                             # break-even at +TP1, then the trail
                if not q.armed:
                    if h >= q.entry + s.split_tp:
                        q.sl = max(q.sl, q.entry, q.entry + s.split_tp - s.split_trail)
                        q.armed = True
                elif h - s.split_trail > q.sl:
                    q.sl = h - s.split_trail
                return None, None
            if not q.armed:
                if h >= q.entry + s.lock:
                    q.sl, q.armed = q.entry + s.lock, True
            elif h - s.trail > q.sl:
                q.sl = h - s.trail
        else:
            if h >= q.sl:
                return q.sl, "trail" if q.armed else "stop"
            if q.tp is not None and lo <= q.tp:
                return q.tp, "target"
            if self.v == "provider" or q.leg == "A":
                return None, None
            if q.leg == "B":
                if not q.armed:
                    if lo <= q.entry - s.split_tp:
                        q.sl = min(q.sl, q.entry, q.entry - s.split_tp + s.split_trail)
                        q.armed = True
                elif lo + s.split_trail < q.sl:
                    q.sl = lo + s.split_trail
                return None, None
            if not q.armed:
                if lo <= q.entry - s.lock:
                    q.sl, q.armed = q.entry - s.lock, True
            elif lo + s.trail < q.sl:
                q.sl = lo + s.trail
        return None, None

    def _close(self, q: Pos, price: float, why: str, t: datetime) -> None:
        sign = 1.0 if q.direction == "buy" else -1.0
        move = sign * (price - q.entry)
        risk = abs(q.entry - q.first_sl)
        pnl = move * q.lots * self.s.per_price_lot
        self.balance += pnl
        self.peak = max(self.peak, self.balance)
        self.max_dd = max(self.max_dd, (self.peak - self.balance) / self.peak if self.peak > 0 else 0.0)
        self.trades.append({
            "version": self.v, "signal_time_utc": _fmt(q.msg_time), "direction": q.direction,
            "order": q.order_type, "open_time_utc": _fmt(q.open_time), "entry": round(q.entry, 2),
            "stop": round(q.first_sl, 2), "stop_distance": round(risk, 2), "stop_from": q.stop_source,
            "target": round(q.tp, 2) if q.tp else "", "lots": q.lots, "leg": q.leg, "close_time_utc": _fmt(t),
            "exit": round(price, 2), "exit_reason": why, "move": round(move, 2),
            "result_r": round(move / risk, 2) if risk > 0 else 0.0, "pnl": round(pnl, 2),
            "risk_distance": risk, "volume": q.lots, "pnl_dollars": pnl,
            "balance": round(self.balance, 2)})

    # account ------------------------------------------------------------
    def equity(self) -> float:
        bid = self.bid_now()
        if bid is None:
            return self.balance
        float_pnl = 0.0
        for q in self.open:
            px = bid if q.direction == "buy" else bid + self.s.spread
            float_pnl += (1 if q.direction == "buy" else -1) * (px - q.entry) * q.lots * self.s.per_price_lot
        return self.balance + float_pnl

    def open_risk(self) -> float:
        bid = self.bid_now() or 0.0
        total = 0.0
        for q in self.open:
            d = (bid - q.sl) if q.direction == "buy" else (q.sl - (bid + self.s.spread))
            if d > 0:
                total += d * q.lots * self.s.per_price_lot
        for q in self.pending:
            total += abs(q.entry - q.sl) * q.lots * self.s.per_price_lot
        return total

    def lots_for(self, dist: float) -> float:
        s = self.s
        eq = self.equity()
        if eq <= 0 or dist <= 0:
            return s.min_lot
        lots = (eq * s.risk_pct / 100.0) / (dist * s.per_price_lot)
        lots = int(lots / s.lot_step + 1e-9) * s.lot_step
        return round(max(s.min_lot, min(lots, s.max_lot)), 2)

    # messages -----------------------------------------------------------
    def message(self, t: datetime, text: str) -> None:
        self.advance_to(t)
        kind, why = message_filter.classify_message(text)
        if kind == message_filter.SKIP:
            self.skips["not a trade message"] += 1
            return
        sig = P.parse(text)
        self.targets = targets_in(text)
        if sig.action == P.CANCEL:
            self.skips["CANCEL message (pending orders cancelled)"] += 1
            self.pending = []
            return
        if sig.action == P.CLOSE:
            bid = self.bid_now()
            if bid is not None and self.open:
                self.skips["CLOSE message (open trades closed)"] += 1
                for q in self.open:
                    self._close(q, bid if q.direction == "buy" else bid + self.s.spread, "close message", t)
                self.open = []
            self.pending = []
            return
        if sig.action != P.OPEN:
            self.skips["no clear buy/sell"] += 1
            return
        self._open(t, sig)

    def _skip(self, why: str) -> None:
        self.skips[why] += 1

    def _open(self, t: datetime, sig) -> None:
        s = self.s
        if not sig.symbol_ok:
            return self._skip("no XAUUSD/GOLD mention")
        r = hours_reason(t, s)
        if r:
            return self._skip(r)
        bid = self.bid_now()
        if bid is None or t - self.p.t[self.i - 1] > timedelta(minutes=10):
            return self._skip("no price at that time (market closed)")
        if s.htf_filter:
            r = self.p.trend_against(t, sig.direction)
            if r:
                return self._skip(r)
        day = broker_day(t)
        if day != self.day:
            self.day, self.day_start = day, self.equity()
        if self.equity() <= self.day_start * (1 - s.daily_loss_pct / 100.0):
            return self._skip("daily loss cap reached")
        same = sum(1 for q in self.open + self.pending if q.direction == sig.direction)
        if same >= s.max_per_direction:
            return self._skip("5 per direction reached")
        lower, upper = (min(sig.entry_a, sig.entry_b), max(sig.entry_a, sig.entry_b)) if sig.has_range \
            else (sig.entry_a, sig.entry_a)
        if lower <= 0:
            return self._skip("no usable entry price")
        buy = sig.direction == "buy"
        edge = upper if buy else lower
        ask = bid + s.spread
        if abs((bid + ask) / 2 - edge) > s.max_deviation:
            return self._skip("price more than $20 from the zone")
        if buy:
            if ask > edge + s.buffer:
                price, order = edge, "LIMIT"
            elif ask < lower - s.buffer:
                return self._skip("price already beyond the zone (stale)")
            else:
                price, order = ask, "MARKET"
        else:
            if bid < edge - s.buffer:
                price, order = edge, "LIMIT"
            elif bid > upper + s.buffer:
                return self._skip("price already beyond the zone (stale)")
            else:
                price, order = bid, "MARKET"
        dist, source, tp = self._stop(sig, buy, price)
        if dist is None:
            return self._skip(source)
        lots = self.lots_for(dist)
        eq = self.equity()
        budget = self.day_start * s.daily_loss_pct / 100.0
        if max(0.0, self.day_start - eq) + self.open_risk() + dist * lots * s.per_price_lot > budget + 1e-9:
            return self._skip("daily loss budget")
        sl = price - dist if buy else price + dist
        legs = [(lots, tp, "")]
        if self.v != "provider" and s.split:            # the EA's InpTelegramSplit
            half = int(lots / 2 / s.lot_step + 1e-9) * s.lot_step
            target = price + s.split_tp if buy else price - s.split_tp
            if half >= s.min_lot - 1e-9 and lots - half >= s.min_lot - 1e-9 and same + 2 <= s.max_per_direction:
                legs = [(round(half, 2), target, "A"), (round(lots - half, 2), None, "B")]
            else:
                legs = [(lots, target, "A")]
        self.orders += 1
        for leg_lots, leg_tp, leg in legs:
            q = Pos(sig.direction, leg_lots, price, sl, sl, leg_tp, source, t, order_type=order, leg=leg)
            if order == "LIMIT":
                q.expires = t + timedelta(minutes=s.expiry_minutes) if s.expiry_minutes > 0 else None
                self.pending.append(q)
            else:
                k = self.i                              # the bar holding the signal time is only partly after it
                if k < len(self.p.t) and self.p.t[k] < t:
                    k += 1
                q.open_time, q.from_bar = t, k
                self.open.append(q)

    def _stop(self, sig, buy: bool, price: float):
        """(distance, where it came from, target) - or (None, skip reason, None)."""
        s = self.s
        d = ((price - sig.sl) if buy else (sig.sl - price)) if sig.has_sl else 0.0
        if self.v == "fixed":
            return s.fixed_stop, "fixed $6", None
        if self.v == "signal":
            if sig.has_sl and s.signal_min <= d <= s.signal_max:
                return d, "signal", None
            return s.fixed_stop, "fixed $6 (signal stop missing or out of range)", None
        # provider: its own stop and first target, or not taken
        if not sig.has_sl or not (0 < d <= s.provider_max):
            return None, "provider version: no usable stop in the signal", None
        tps = [x for x in (sig.tps or self.targets) if (x > price if buy else x < price)]
        if not tps:
            return None, "provider version: no target beyond the entry", None
        return d, "signal", tps[0]

    def finish(self) -> None:
        self.advance_to(self.p.t[-1] + timedelta(minutes=2) if self.p.t else datetime.now(timezone.utc))
        if self.pending:
            self.skips["pending order never filled"] += len(self.pending)
        self.pending = []
        bid = self.p.c[-1] if self.p.c else None
        if bid is not None:
            for q in self.open:
                self._close(q, bid if q.direction == "buy" else bid + self.s.spread, "end of data",
                            self.p.t[-1] + timedelta(minutes=1))
        self.open = []


_TARGET_RE = re.compile(r"\b(?:TP|TARGET|TGT)\s*\d?\s*[:=@\-]?\s*(\d{3,6}(?:\.\d+)?)", re.I)


def targets_in(text: str) -> list:
    """The signal's targets for the "provider" reference version: TP1.. and
    also "Target 1: 4324.3" (the EA itself never uses targets)."""
    return [float(x) for x in _TARGET_RE.findall(text or "")]


def _fmt(t) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") if t else ""


def run(messages: list, prices: Prices, s: Settings, variants=tuple(VARIANTS)) -> dict:
    """{variant: Replay} after replaying every message (oldest first)."""
    out = {}
    for v in variants:
        r = Replay(v, prices, s)
        for m in messages:
            if m.get("media"):
                r.skips["media / photo (not read by the EA)"] += 1
                continue
            r.message(m["time"], m["text"])
        r.finish()
        out[v] = r
    return out


# --- report -------------------------------------------------------------------------

def summary_text(results: dict, s: Settings, messages: list, period: str, chat: str) -> str:
    import scorecard
    lines = [f"GoldTrader signal-history replay - {chat}", f"Period: {period} (UTC)",
             f"Messages: {len(messages)} | start equity ${s.start_equity:,.0f} | risk {s.risk_pct:g}% a trade | "
             f"spread ${s.spread:.2f} | M15/H1 filter {'on' if s.htf_filter else 'off'}", ""]
    for v, r in results.items():
        t = r.trades
        lines.append(f"== {v}: {VARIANTS[v]}")
        if not t:
            lines.append("   no trades")
        else:
            sm = scorecard.summarize(t, s.per_price_lot, s.fixed_stop)
            label, advice = scorecard.verdict(sm)
            wins = sum(1 for x in t if x["pnl"] > 0)
            pf = "n/a" if sm.get("profit_factor") is None else f"{sm['profit_factor']:.2f}"
            ret = (r.balance - s.start_equity) / s.start_equity * 100
            reasons = Counter(x["exit_reason"] for x in t)
            lines.append(f"   trades {len(t)} | win {wins / len(t) * 100:.0f}% | avg {sm['avg_r']:+.2f}R | "
                         f"total {sum(x['result_r'] for x in t):+.1f}R | net ${r.balance - s.start_equity:+,.0f} "
                         f"({ret:+.1f}%) | worst drawdown {r.max_dd * 100:.1f}% | profit factor "
                         f"{pf} | chance of no edge {sm.get('p_no_edge', 0) * 100:.0f}%")
            lines.append("   exits: " + ", ".join(f"{k} {n}" for k, n in reasons.most_common()))
            lines.append(f"   scorecard rule: {label} - {advice}")
        if r.skips:
            lines.append("   not traded: " + ", ".join(f"{k} {n}" for k, n in r.skips.most_common()))
        lines.append("")
    lines.append("Not modelled: news blackout, slippage beyond the spread, margin guard, edited messages. "
                 "M1 bars: a bar touching both the stop and the lock/target counts as the stop.")
    return "\n".join(lines)


def write_outputs(results: dict, summary: str, messages: list, bars: pd.DataFrame | None, cfg) -> list:
    os.makedirs(OUT_DIR, exist_ok=True)
    files = {}
    cols = ["version", "signal_time_utc", "direction", "order", "open_time_utc", "entry", "stop",
            "stop_distance", "stop_from", "target", "lots", "leg", "close_time_utc", "exit", "exit_reason", "move",
            "result_r", "pnl", "balance"]
    rows = [t for r in results.values() for t in r.trades]
    files["trades.csv"] = _csv(rows, cols)
    files["summary.txt"] = summary + "\n"
    files["messages.csv"] = _csv([{"time_utc": _fmt(m["time"]), "chat": m.get("chat", ""),
                                   "media": m.get("media", ""), "text": m["text"]} for m in messages],
                                 ["time_utc", "chat", "media", "text"])
    if bars is not None and len(bars):
        b = bars[["time", "open", "high", "low", "close"]].copy()
        b["time"] = pd.to_datetime(b["time"], utc=True).dt.strftime("%Y-%m-%d %H:%M")
        files["prices_M1.csv"] = b.to_csv(index=False)
    written = []
    for name, text in files.items():
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        written.append(path)
    try:
        import trade_journal
        drive = trade_journal.target_folder(cfg)
        if drive:
            for name, text in files.items():
                trade_journal.write_if_changed(os.path.join(drive, PREFIX + name), text)
            written.append(drive)
    except Exception as exc:                          # the local copy is what matters
        log.warning("Could not copy the replay to Google Drive (%s) - it is in %s", exc, OUT_DIR)
    return written


def _csv(rows: list, cols: list) -> str:
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


# --- inputs: Telegram history and MT5 prices ---------------------------------------

def load_messages_csv(path: str) -> list:
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            t = pd.Timestamp(r["time_utc"])
            t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
            out.append({"time": t.to_pydatetime(), "chat": r.get("chat", ""), "media": r.get("media", ""),
                        "text": r.get("text", "")})
    return sorted(out, key=lambda m: m["time"])


def load_prices_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def fetch_messages(chats: list, since: datetime) -> tuple[list, str]:
    """(messages oldest first, chat titles) with the relay's Telegram login."""
    from telethon import TelegramClient
    import telegram_relay_bridge as bridge

    api_id, api_hash = ask_api_credentials()
    session = os.path.join(paths.RELAY_DIR, os.environ.get("TELEGRAM_RELAY_SESSION", "tg_relay_bridge"))

    async def go():
        client = TelegramClient(session, int(api_id), api_hash)
        await client.start()          # asks for phone + code only if there is no saved login
        try:
            targets = list(chats)
            if not targets:
                dialogs = [d for d in await client.get_dialogs() if d.is_group or d.is_channel]
                print("\nYour Telegram groups and channels:")
                for n, d in enumerate(dialogs, 1):
                    print(f"  {n:3}. {d.name}  (id {bridge.bot_api_chat_id(d.entity)})")
                pick = input("\nNumber of the signal channel/group to replay: ").strip()
                targets = [dialogs[int(pick) - 1].entity]
            out, titles = [], []
            for chat in targets:
                entity = chat if not isinstance(chat, (str, int)) else await bridge.resolve_chat(client, chat)
                title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat)
                titles.append(title)
                n = 0
                async for m in client.iter_messages(entity):
                    if m.date < since:
                        break
                    media = message_filter.message_media_kind(m) or ""
                    out.append({"time": m.date.astimezone(timezone.utc), "chat": title, "media": media,
                                "text": m.message or ""})
                    n += 1
                log.info("Read %d messages from %s", n, title)
            return sorted(out, key=lambda x: x["time"]), ", ".join(titles)
        finally:
            await client.disconnect()

    import sqlite3
    try:
        return asyncio.run(go())
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc):
            raise SystemExit("The relay is using your Telegram login right now. Close the start.bat window, "
                             "run replay_signals.bat, then start start.bat again.")
        raise


def ask_api_credentials() -> tuple[str, str]:
    """TELEGRAM_API_ID / TELEGRAM_API_HASH from keys.txt - asked here and saved
    there when missing (without switching the relay on)."""
    import keys
    api_id = os.environ.get("TELEGRAM_API_ID", "").strip()
    api_hash = os.environ.get("TELEGRAM_API_HASH", "").strip()
    if api_id.isdigit() and api_hash:
        return api_id, api_hash
    print("\nReading Telegram history needs your personal Telegram API id (once):\n"
          "  https://my.telegram.org -> log in -> API development tools -> create an app\n"
          "  (any name) -> copy api_id and api_hash. They are saved in keys.txt only.\n")
    while not api_id.isdigit():
        api_id = input("api_id (a number): ").strip()
    while not api_hash:
        api_hash = input("api_hash: ").strip()
    keys.save("TELEGRAM_API_ID", api_id)
    keys.save("TELEGRAM_API_HASH", api_hash)
    return api_id, api_hash


def load_prices_mt5(symbol: str, start: datetime, end: datetime):
    import mt5_gateway as gw
    gw.connect()
    spec = gw.symbol_spec(symbol)
    gw.server_utc_offset_seconds(symbol)
    bars = gw.get_bars_range(symbol, "M1", start, end)
    if len(bars) == 0:
        raise SystemExit(f"MT5 returned no M1 prices for {symbol} from {start:%Y-%m-%d} - open an M1 chart "
                         f"and scroll back (Home key) so MT5 downloads the history, then run this again.")
    per_price = spec.tick_value / spec.tick_size if spec.tick_size > 0 else 100.0
    equity = None
    try:
        equity = gw.account_equity()
    except Exception:
        pass
    return bars, per_price, spec, equity


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--months", type=float, default=3.0, help="how far back to read (default 3)")
    ap.add_argument("--chat", action="append", help="@name or id of the signal channel (repeatable); "
                    "default TELEGRAM_SOURCE_CHANNELS from keys.txt, else pick from a list")
    ap.add_argument("--pick", action="store_true", help="always pick the chat from the list")
    ap.add_argument("--equity", type=float, help="starting equity (default: your MT5 account's)")
    ap.add_argument("--spread", type=float, default=0.30, help="spread in gold $ (default 0.30 = 30 points)")
    ap.add_argument("--no-htf", action="store_true", dest="no_htf", help="replay without the M15/H1 filter")
    ap.add_argument("--messages-file", help="replay a saved messages.csv instead of reading Telegram")
    ap.add_argument("--prices-file", help="use a saved prices_M1.csv instead of MT5")
    ap.add_argument("--symbol", default="XAUUSD")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    import keys
    keys.load()
    from config import AdvisorConfig
    cfg = AdvisorConfig()
    s = Settings(spread=args.spread, htf_filter=not args.no_htf,
                 signal_min=cfg.signal_sl_min_distance, signal_max=cfg.signal_sl_max_distance,
                 split=cfg.telegram_split, split_tp=cfg.telegram_tp1_dollars,
                 split_trail=cfg.telegram_trail_dollars,
                 risk_pct=cfg.risk_percent, daily_loss_pct=cfg.max_daily_loss_pct,
                 max_per_direction=cfg.max_open_positions_per_direction,
                 trade_hours=cfg.telegram_trade_windows, utc_offset_hours=cfg.telegram_utc_offset_hours,
                 weekdays_only=cfg.telegram_weekdays_only)

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=int(args.months * 30.5))
    if args.messages_file:
        messages, chat = load_messages_csv(args.messages_file), os.path.basename(args.messages_file)
    else:
        chats = [] if args.pick else (args.chat or [c.strip() for c in
                                                    os.environ.get("TELEGRAM_SOURCE_CHANNELS", "").split(",")
                                                    if c.strip()])
        messages, chat = fetch_messages(chats, since)
    if not messages:
        print("No messages found in that period.")
        return 1
    start = messages[0]["time"] - timedelta(days=5)          # warm-up for the M15/H1 filter
    end = min(now, messages[-1]["time"] + timedelta(days=4))
    if args.prices_file:
        bars = load_prices_csv(args.prices_file)
        equity = None
    else:
        bars, s.per_price_lot, _spec, equity = load_prices_mt5(args.symbol, start, end)
    s.start_equity = args.equity or equity or s.start_equity
    first_bar = pd.to_datetime(bars["time"], utc=True).min().to_pydatetime()
    if first_bar > messages[0]["time"] + timedelta(days=1):
        covered = sum(1 for m in messages if m["time"] >= first_bar)
        print(f"\nNOTE: MT5 gave M1 prices only from {first_bar:%Y-%m-%d}, so {len(messages) - covered} of "
              f"{len(messages)} messages have no prices and are not replayed. For the full period: MT5 -> "
              f"Tools -> Options -> Charts -> 'Max bars in chart' = Unlimited, restart MT5, open an XAUUSD M1 "
              f"chart and press Home a few times, then run this again (or use --months 1).\n")
        messages = [m for m in messages if m["time"] >= first_bar]
        if not messages:
            return 1
    log.info("Replaying %d messages over %d M1 bars...", len(messages), len(bars))
    results = run(messages, Prices(bars, htf=s.htf_filter), s)
    period = f"{messages[0]['time']:%Y-%m-%d} to {messages[-1]['time']:%Y-%m-%d}"
    text = summary_text(results, s, messages, period, chat)
    print("\n" + text)
    written = write_outputs(results, text, messages, bars, cfg)
    print("\nSaved: " + " | ".join(written))
    print("Ask Claude: \"analyse my signal replay\" - it reads the files from your Google Drive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

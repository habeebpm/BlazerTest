#!/usr/bin/env python3
"""
XTR micro-scalp signal engine for MQL5/Experts/ClaudeSignalEA.mq5.

Implements the "XTR - XAUUSD Micro-Scalp Signal Logic Specification"
(sections referenced below are that document's, see
CLAUDE_SIGNAL_PIPELINE.md for the full text) as deterministic Python rules,
not as something an LLM eyeballs from raw candles:

  - §2  directional determination (EMA9/EMA21, RSI, MACD histogram) per
        timeframe
  - §3  regime detection from M5 ADX
  - §4  the three setup types (RSI-extreme bounce, trend-continuation
        pullback with the MACD re-expansion gate, liquidity-sweep reversal)
  - §5  the M15/H1 conviction filter matrix
  - §7  stop-loss / take-profit sizing (ATR-clamped to the structural swing,
        1.5-2.0R target nudged to a round number or the opposite BB band)
  - §8  position sizing from account equity and risk %
  - §9  10-minute time-decay invalidation (best-effort: issues a CLOSE
        signal - the file protocol has no per-position selector)
  - §10 the two-loss standdown per setup type

Every number above - direction, SL, TP, lot size - is decided by this code.
Claude is called only for the discretionary overlay (§12: session timing,
round-number context, DXY/macro flags) and the narrative report (§13); it
never overrides the mechanical decision, and is not called at all on a
NO_TRADE cycle (saving the API call).

    python claude_signal_bot.py --selftest             # offline logic checks, no API key needed
    python claude_signal_bot.py --data-dir <path> --once --dry-run -v
    python claude_signal_bot.py --data-dir <path>       # run the loop, ANTHROPIC_API_KEY required

`--data-dir` should be the MT5 terminal's shared Common\\Files folder (the
one the EA writes to with FILE_COMMON) - typically, on Windows:
    %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files
See CLAUDE_SIGNAL_PIPELINE.md for the full setup and file-format reference.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRADE_LOG = LOG_DIR / "xtr_trades.csv"          # matches the §11 schema
TRADE_LOG_FIELDS = [
    "timestamp", "setup_type", "direction", "entry", "sl", "tp", "atr_at_entry",
    "regime_adx", "conviction", "outcome", "resolution_time", "notes",
]

log = logging.getLogger("claude_signal_bot")

DEFAULT_MODEL = "claude-sonnet-5"
HEADER_RE = re.compile(r"(\w+)=(\S+)")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
TWO_LOSS_THRESHOLD = 2
TIME_DECAY_SECONDS = 10 * 60


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "claude_signal_bot.log", encoding="utf-8"),
        ],
    )


@dataclass
class BotConfig:
    data_file: Path
    signal_file: Path
    ack_file: Path
    outcome_file: Path
    state_file: Path
    interval: float = 60.0
    model: str = DEFAULT_MODEL
    api_key: str = ""
    risk_percent: float = 2.0
    fallback_equity: float = 5000.0
    time_decay_seconds: float = TIME_DECAY_SECONDS
    dry_run: bool = False


@dataclass
class ChartSnapshot:
    symbol: str
    digits: int
    point: float
    tick_value: float
    tick_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    bid: float
    ask: float
    spread: int
    equity: float
    exported_at: str
    ind: dict            # {"M5": {...}, "M15": {...}, "H1": {...}}
    macd_hist_m5: list    # oldest -> newest
    bars: dict            # {"M1": df, "M5": df, "M15": df, "H1": df}


# --------------------------------------------------------------------------
# Chart-data file parsing (multi-section format written by the EA)
# --------------------------------------------------------------------------

def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current, buf = None, []
    for line in lines:
        line = line.rstrip("\n")
        if line.startswith("##"):
            if current is not None:
                sections[current] = buf
            current, buf = line[2:].strip(), []
        elif line.strip():
            buf.append(line)
    if current is not None:
        sections[current] = buf
    return sections


def _num(value: str) -> float | None:
    return None if value in ("NA", "", "nan") else float(value)


def parse_chart_file(path: Path) -> ChartSnapshot:
    if not path.exists():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        raise ValueError(f"{path}: empty file")

    meta = dict(HEADER_RE.findall(lines[0].lstrip("#").strip()))
    if "symbol" not in meta:
        raise ValueError(f"{path}: missing 'symbol' in header metadata")

    sections = _split_sections(lines[1:])

    ind: dict = {}
    if "INDICATORS" in sections:
        rows = sections["INDICATORS"]
        header = rows[0].split(",")
        for row in rows[1:]:
            vals = row.split(",")
            d = dict(zip(header, vals))
            tf = d.pop("tf")
            ind[tf] = {k: _num(v) for k, v in d.items()}
    for tf in ("M5", "M15", "H1"):
        if tf not in ind:
            raise ValueError(f"{path}: missing indicator row for {tf}")

    macd_hist_m5 = []
    if "MACD_HIST_M5" in sections:
        rows = sections["MACD_HIST_M5"]
        macd_hist_m5 = [float(r) for r in rows[1:]]   # skip the "value" header

    bars: dict = {}
    for tf in ("M1", "M5", "M15", "H1"):
        key = f"BARS_{tf}"
        if key not in sections:
            continue
        rows = sections[key]
        header = rows[0].split(",")
        data = [r.split(",") for r in rows[1:]]
        if not data:
            continue
        df = pd.DataFrame(data, columns=header)
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df["time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M")
        bars[tf] = df

    for tf in ("M1", "M5", "M15", "H1"):
        if tf not in bars:
            raise ValueError(f"{path}: missing bars for {tf}")

    last_close = float(bars["M5"]["close"].iloc[-1])
    return ChartSnapshot(
        symbol=meta["symbol"],
        digits=int(meta.get("digits", 2)),
        point=float(meta.get("point", 0.01)),
        tick_value=float(meta.get("tick_value", 1.0)),
        tick_size=float(meta.get("tick_size", 0.01)),
        volume_min=float(meta.get("volume_min", 0.01)),
        volume_max=float(meta.get("volume_max", 50.0)),
        volume_step=float(meta.get("volume_step", 0.01)),
        bid=float(meta.get("bid", last_close)),
        ask=float(meta.get("ask", last_close)),
        spread=int(meta.get("spread", 0)),
        equity=float(meta.get("equity", 0.0)),
        exported_at=meta.get("exported", ""),
        ind=ind,
        macd_hist_m5=macd_hist_m5,
        bars=bars,
    )


# --------------------------------------------------------------------------
# §2 - directional determination, §3 - regime
# --------------------------------------------------------------------------

def direction_for(tf_ind: dict) -> str:
    """BULLISH / BEARISH / MIXED for one timeframe's indicator snapshot."""
    ema9, ema21, rsi, macd_hist = tf_ind["ema9"], tf_ind["ema21"], tf_ind["rsi14"], tf_ind["macd_hist"]
    if None in (ema9, ema21, rsi, macd_hist):
        return "MIXED"
    if ema9 > ema21 and rsi > 50 and macd_hist > 0:
        return "BULLISH"
    if ema9 < ema21 and rsi < 50 and macd_hist < 0:
        return "BEARISH"
    return "MIXED"


def regime_for(m5_ind: dict) -> str:
    adx = m5_ind.get("adx14")
    return "TRENDING" if adx is not None and adx >= 25 else "RANGING"


# --------------------------------------------------------------------------
# §4 - setup detection
# --------------------------------------------------------------------------

def check_rsi_bounce(snap: ChartSnapshot) -> dict | None:
    """§4.1 RSI-Extreme Bounce (mean reversion, ranging regime)."""
    m5 = snap.ind["M5"]
    rsi, bb_upper, bb_lower = m5.get("rsi14"), m5.get("bb_upper"), m5.get("bb_lower")
    if rsi is None or bb_upper is None or bb_lower is None:
        return None
    close = float(snap.bars["M5"]["close"].iloc[-1])
    if rsi < 30 and close <= bb_lower:
        return {"setup_type": "4.1", "direction": "BUY"}
    if rsi > 70 and close >= bb_upper:
        return {"setup_type": "4.1", "direction": "SELL"}
    return None


def macd_reexpanding(hist: list[float], direction: str) -> bool:
    """The last 2 bars (h2, h1) grow in the trend's sign, immediately after
    a prior bar (h3) that was flat/decelerating relative to the one before
    it (h4) - the §4.2 gate that stops entries on the first EMA touch
    before momentum actually confirms (the trades 13/14 fix)."""
    if len(hist) < 4:
        return False
    sign = 1 if direction == "BULLISH" else -1
    h4, h3, h2, h1 = hist[-4], hist[-3], hist[-2], hist[-1]   # oldest -> newest
    if not (h1 * sign > 0 and h2 * sign > 0):
        return False
    decelerated_before = abs(h3) <= abs(h4)
    turning = abs(h2) > abs(h3)
    growing = abs(h1) > abs(h2)
    return decelerated_before and turning and growing


def check_trend_pullback(snap: ChartSnapshot, m5_direction: str) -> dict | None:
    """§4.2 Trend-Continuation Pullback (trending regime, gated)."""
    if m5_direction == "MIXED":
        return None
    m5 = snap.ind["M5"]
    ema9, atr = m5.get("ema9"), m5.get("atr14")
    if ema9 is None or atr is None or atr <= 0:
        return None
    close = float(snap.bars["M5"]["close"].iloc[-1])
    if abs(close - ema9) > 0.5 * atr:
        return None   # not pulled back to/toward EMA9
    if not macd_reexpanding(snap.macd_hist_m5, m5_direction):
        return None
    direction = "BUY" if m5_direction == "BULLISH" else "SELL"
    return {"setup_type": "4.2", "direction": direction}


def check_liquidity_sweep(snap: ChartSnapshot) -> dict | None:
    """§4.3 Liquidity-Sweep Reversal: a brief break of the recent M1 swing
    extreme followed by a close back inside within 1-3 M1 bars."""
    m1 = snap.bars["M1"]
    if len(m1) < 14:
        return None
    window = m1.iloc[-13:-3]     # the reference "10-minute swing" bars
    recent = m1.iloc[-3:]        # the 1-3 bars the sweep+reclaim must happen in
    if window.empty or recent.empty:
        return None

    swing_high, swing_low = window["high"].max(), window["low"].min()
    last_close = float(recent["close"].iloc[-1])

    if recent["high"].max() > swing_high and last_close < swing_high:
        return {"setup_type": "4.3", "direction": "SELL", "structural_level": float(swing_high)}
    if recent["low"].min() < swing_low and last_close > swing_low:
        return {"setup_type": "4.3", "direction": "BUY", "structural_level": float(swing_low)}
    return None


def select_setup(snap: ChartSnapshot, m5_direction: str, regime: str) -> dict | None:
    """§3/§4: pick the regime-favored setup, with the liquidity sweep as a
    confluence booster (or a standalone opportunistic entry if nothing else
    triggers)."""
    sweep = check_liquidity_sweep(snap)
    primary = check_rsi_bounce(snap) if regime == "RANGING" else check_trend_pullback(snap, m5_direction)

    if primary:
        candidate = dict(primary)
        candidate["confluence_sweep"] = bool(sweep and sweep["direction"] == primary["direction"])
        return candidate
    if sweep:
        candidate = dict(sweep)
        candidate["confluence_sweep"] = False
        return candidate
    return None


# --------------------------------------------------------------------------
# §5 - HTF conviction filter
# --------------------------------------------------------------------------

def htf_conviction(signal_direction: str, m15_direction: str, h1_direction: str) -> str:
    """Returns FULL, REDUCED or NO_TRADE per the §5 table."""
    want = "BULLISH" if signal_direction == "BUY" else "BEARISH"

    def state(tf_direction: str) -> str:
        if tf_direction == "MIXED":
            return "MIXED"
        return "MATCH" if tf_direction == want else "OPPOSE"

    m15_state, h1_state = state(m15_direction), state(h1_direction)
    if m15_state == "OPPOSE" or h1_state == "OPPOSE":
        return "NO_TRADE"
    if m15_state == "MIXED" and h1_state == "MIXED":
        return "NO_TRADE"
    if m15_state == "MATCH" and h1_state == "MATCH":
        return "FULL"
    return "REDUCED"


# --------------------------------------------------------------------------
# §7 - stop-loss / take-profit, §8 - position sizing
# --------------------------------------------------------------------------

def compute_stop_loss(snap: ChartSnapshot, direction: str) -> tuple[float, float]:
    """Returns (sl_price, stop_distance). See §7: the stop distance is the
    structural-swing distance (plus a small ATR buffer), clamped to
    [1.0, 1.5] x ATR14(M5)."""
    atr = snap.ind["M5"]["atr14"] or 0.0
    price = snap.ask if direction == "BUY" else snap.bid

    m1 = snap.bars["M1"]
    lookback = m1.iloc[-11:-1] if len(m1) >= 11 else m1.iloc[:-1] if len(m1) > 1 else m1
    if direction == "BUY":
        structural = float(lookback["low"].min())
        structural_distance = price - structural
    else:
        structural = float(lookback["high"].max())
        structural_distance = structural - price

    buffer = 0.05 * atr
    structural_distance = max(structural_distance, 0.0) + buffer

    if atr > 0:
        stop_distance = min(max(structural_distance, 1.0 * atr), 1.5 * atr)
    else:
        stop_distance = structural_distance

    sl = price - stop_distance if direction == "BUY" else price + stop_distance
    return sl, stop_distance


def _nearest_round_level(price: float) -> float:
    """Gold often reacts at whole-dollar handles; used only when it falls
    inside the allowed R:R band (see compute_take_profit)."""
    return round(price)


def compute_take_profit(snap: ChartSnapshot, direction: str, price: float, stop_distance: float) -> float:
    """§7: entry +/- (1.5 to 2.0 x stop distance), nudged to the nearest
    whole-dollar handle or the opposite Bollinger band when that still
    lands inside the allowed band; otherwise the 1.75R midpoint."""
    m5 = snap.ind["M5"]
    if direction == "BUY":
        lo, hi = price + 1.5 * stop_distance, price + 2.0 * stop_distance
        raw = price + 1.75 * stop_distance
        candidates = [_nearest_round_level(raw)]
        if m5.get("bb_upper") is not None:
            candidates.append(m5["bb_upper"])
    else:
        lo, hi = price - 2.0 * stop_distance, price - 1.5 * stop_distance
        raw = price - 1.75 * stop_distance
        candidates = [_nearest_round_level(raw)]
        if m5.get("bb_lower") is not None:
            candidates.append(m5["bb_lower"])

    in_range = [c for c in candidates if lo <= c <= hi]
    return min(in_range, key=lambda c: abs(c - raw)) if in_range else raw


def position_size(snap: ChartSnapshot, cfg: BotConfig, stop_distance: float) -> float:
    """§8: risk_amount / (stop_distance x value_per_price_unit_per_lot),
    rounded down to the broker's lot step."""
    equity = snap.equity if snap.equity > 0 else cfg.fallback_equity
    risk_amount = equity * (cfg.risk_percent / 100.0)
    value_per_unit = (snap.tick_value / snap.tick_size) if snap.tick_size else 0.0
    if value_per_unit <= 0 or stop_distance <= 0:
        return snap.volume_min

    lots = risk_amount / (stop_distance * value_per_unit)
    step = snap.volume_step or 0.01
    lots = math.floor(lots / step) * step
    lots = max(snap.volume_min, min(lots, snap.volume_max))
    return round(lots, 2)


# --------------------------------------------------------------------------
# §10 - two-loss standdown per setup type
# --------------------------------------------------------------------------

def parse_outcomes(path: Path, since_count: int) -> tuple[list[dict], int]:
    if not path.exists():
        return [], since_count
    lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    new_lines = lines[since_count:]
    records = []
    for line in new_lines:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        records.append({
            "timestamp": parts[0], "signal_id": parts[1], "setup_type": parts[2],
            "direction": parts[3], "profit": float(parts[4]), "outcome": parts[5],
        })
    return records, len(lines)


def update_standdown(state: dict, outcomes: list[dict], m5_direction: str) -> None:
    sd = state.setdefault("standdown", {})
    for rec in outcomes:
        st = rec["setup_type"]
        entry = sd.setdefault(st, {"consecutive_losses": 0, "active": False, "seen_mixed": False})
        if rec["outcome"] == "LOSS":
            entry["consecutive_losses"] += 1
            if entry["consecutive_losses"] >= TWO_LOSS_THRESHOLD:
                entry["active"] = True
                entry["seen_mixed"] = False
        else:
            entry["consecutive_losses"] = 0
            entry["active"] = False

    if m5_direction == "MIXED":
        for entry in sd.values():
            if entry.get("active"):
                entry["seen_mixed"] = True


def standdown_gate(state: dict, setup_type: str) -> bool:
    """Returns True if this setup type may trade this cycle (clearing a
    standdown that has seen M5 return to MIXED and now re-qualifies)."""
    entry = state.get("standdown", {}).get(setup_type)
    if not entry or not entry.get("active"):
        return True
    if entry.get("seen_mixed"):
        entry["active"] = False
        entry["consecutive_losses"] = 0
        entry["seen_mixed"] = False
        return True
    return False


# --------------------------------------------------------------------------
# §9 - time-decay invalidation (best-effort: the file protocol has no
# per-position selector, so an expiry closes every position this EA holds)
# --------------------------------------------------------------------------

def register_pending(state: dict, signal_id: int, setup_type: str) -> None:
    state.setdefault("pending", {})[str(signal_id)] = {
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "setup_type": setup_type,
    }


def resolve_pending(state: dict, outcomes: list[dict]) -> None:
    pending = state.get("pending", {})
    for rec in outcomes:
        pending.pop(str(rec["signal_id"]), None)


def expire_stale_pending(state: dict, decay_seconds: float) -> list[str]:
    pending = state.get("pending", {})
    now = datetime.now(timezone.utc)
    expired = []
    for sid, info in list(pending.items()):
        issued = datetime.fromisoformat(info["issued_at"])
        if (now - issued).total_seconds() >= decay_seconds:
            expired.append(sid)
            del pending[sid]
    return expired


# --------------------------------------------------------------------------
# §12 - discretionary overlay (deterministic parts; Claude adds the rest)
# --------------------------------------------------------------------------

def session_context(now: datetime | None = None) -> str:
    hour = (now or datetime.now(timezone.utc)).hour
    if 13 <= hour < 16:
        return "london_ny_overlap"
    if 8 <= hour < 16:
        return "london"
    if 13 <= hour < 21:
        return "new_york"
    if 0 <= hour < 8:
        return "asian"
    return "off_hours"


def round_number_context(price: float) -> dict:
    nearest = round(price / 50.0) * 50.0
    return {"nearest_50_handle": nearest, "distance": round(price - nearest, 2)}


def build_overlay_context(snap: ChartSnapshot) -> dict:
    return {
        "session": session_context(),
        "round_number": round_number_context(snap.bid),
        "dxy_correlation": "not evaluated - no DXY feed configured",
        "macro_news": "not evaluated - no economic-calendar feed configured",
    }


def call_claude_overlay(candidate: dict, snap: ChartSnapshot, scenario: dict, overlay: dict, cfg: BotConfig) -> dict:
    """Asks Claude for a one-paragraph qualitative note and an optional
    confidence caution flag on top of an ALREADY-DECIDED trade. Claude
    cannot change the direction, setup type, SL or TP - only annotate."""
    import anthropic

    system = (
        "You are a discretionary overlay on a fully mechanical XAUUSD "
        "micro-scalp signal. The direction, setup type, stop-loss and "
        "take-profit are already fixed by rule-based code and will NOT "
        "change based on your reply. Your only job is to note qualitative "
        "context that a human overseeing the bot would want to see: "
        "session liquidity, proximity to a round-number level, and any "
        "other risk you can infer from the bars provided. Reply with "
        "STRICT JSON only: "
        '{"caution": true|false, "note": "<one or two sentences>"}. '
        'Set "caution" true only if you see a clear reason to distrust '
        "this specific setup right now (e.g. a thin/illiquid session, "
        "price already deep into a round-number wall against the trade)."
    )
    user = (
        f"Setup: {candidate['setup_type']} {candidate['direction']} on {snap.symbol}\n"
        f"Conviction: {candidate['conviction']}\n"
        f"Entry/SL/TP: {candidate['entry']}/{candidate['sl']}/{candidate['tp']}\n"
        f"Scenario: {json.dumps(scenario)}\n"
        f"Overlay context: {json.dumps(overlay)}\n"
        f"Recent M5 bars:\n{snap.bars['M5'].tail(15).to_csv(index=False)}"
    )

    client = anthropic.Anthropic(api_key=cfg.api_key)
    resp = client.messages.create(
        model=cfg.model, max_tokens=250, system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    match = JSON_BLOCK_RE.search(raw)
    if not match:
        raise ValueError(f"no JSON object in Claude's overlay reply: {raw[:200]!r}")
    data = json.loads(match.group(0))
    return {"caution": bool(data.get("caution", False)), "note": str(data.get("note", ""))[:300]}


# --------------------------------------------------------------------------
# §13 - output format
# --------------------------------------------------------------------------

def format_report(snap: ChartSnapshot, m5_direction: str, regime: str,
                   m15_direction: str, h1_direction: str, decision: dict) -> str:
    lines = [
        f"1. M5 read: {m5_direction} "
        f"(ema9={snap.ind['M5']['ema9']} ema21={snap.ind['M5']['ema21']} "
        f"rsi={snap.ind['M5']['rsi14']} macd_hist={snap.ind['M5']['macd_hist']})",
        f"2. Regime: ADX={snap.ind['M5']['adx14']} -> {regime}",
        f"3. HTF filter: M15={m15_direction} H1={h1_direction} -> "
        f"{decision.get('conviction', 'NO_TRADE')}",
        f"4. M1 context: {decision.get('m1_note', 'no liquidity-sweep confluence')}",
    ]
    if decision["action"] == "NONE":
        lines.append("5. Signal: NO TRADE")
    else:
        lines.append(f"5. Signal: {decision['action']} - setup {decision['setup_type']} "
                      f"({decision['conviction']} conviction)")
        rr = abs(decision["tp"] - decision["entry"]) / abs(decision["entry"] - decision["sl"])
        lines.append(f"6. Execution: entry={decision['entry']} sl={decision['sl']} "
                      f"tp={decision['tp']} lot={decision['lot']} R:R=1:{rr:.2f}")
        lines.append(f"7. Time-decay: close/cancel if unresolved after "
                      f"{int(TIME_DECAY_SECONDS / 60)} minutes")
    if decision.get("overlay_note"):
        lines.append(f"Overlay: {decision['overlay_note']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Signal file I/O (atomic writes, matches the EA's parser)
# --------------------------------------------------------------------------

def write_signal(cfg: BotConfig, signal_id: int, symbol: str, action: str,
                  lot: float, sl: float, tp: float, setup_type: str, reason: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reason = reason.replace(",", ";").replace("\n", " ")
    line = f"{signal_id},{symbol},{action},{lot},{sl},{tp},{ts},{setup_type},{reason}\n"
    tmp = cfg.signal_file.with_suffix(cfg.signal_file.suffix + ".tmp")
    tmp.write_text(line, encoding="utf-8")
    os.replace(tmp, cfg.signal_file)


def record_trade_log(row: dict) -> None:
    is_new = not TRADE_LOG.exists()
    with TRADE_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in TRADE_LOG_FIELDS})


def load_state(cfg: BotConfig) -> dict:
    if cfg.state_file.exists():
        try:
            return json.loads(cfg.state_file.read_text(encoding="utf-8"))
        except Exception:
            log.warning("could not parse state file %s, starting fresh", cfg.state_file)
    return {"last_signal_id": 0, "last_bar_time": None, "outcome_lines_seen": 0,
            "standdown": {}, "pending": {}}


def save_state(cfg: BotConfig, state: dict) -> None:
    tmp = cfg.state_file.with_suffix(cfg.state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, cfg.state_file)


# --------------------------------------------------------------------------
# Main decision cycle
# --------------------------------------------------------------------------

def decide(snap: ChartSnapshot, state: dict) -> dict:
    """Runs §2-§10 and returns a decision dict. Never calls Claude."""
    m5_direction = direction_for(snap.ind["M5"])
    m15_direction = direction_for(snap.ind["M15"])
    h1_direction = direction_for(snap.ind["H1"])
    regime = regime_for(snap.ind["M5"])

    candidate = select_setup(snap, m5_direction, regime)
    if candidate is None:
        return {"action": "NONE", "m5_direction": m5_direction, "regime": regime,
                "m15_direction": m15_direction, "h1_direction": h1_direction}

    if not standdown_gate(state, candidate["setup_type"]):
        return {"action": "NONE", "m5_direction": m5_direction, "regime": regime,
                "m15_direction": m15_direction, "h1_direction": h1_direction,
                "m1_note": f"setup {candidate['setup_type']} suppressed by two-loss standdown"}

    conviction = htf_conviction(candidate["direction"], m15_direction, h1_direction)
    if conviction == "NO_TRADE":
        return {"action": "NONE", "m5_direction": m5_direction, "regime": regime,
                "m15_direction": m15_direction, "h1_direction": h1_direction}

    direction = candidate["direction"]
    sl, stop_distance = compute_stop_loss(snap, direction)
    price = snap.ask if direction == "BUY" else snap.bid
    tp = compute_take_profit(snap, direction, price, stop_distance)

    m1_note = "liquidity-sweep confluence" if candidate.get("confluence_sweep") else "no liquidity-sweep confluence"

    return {
        "action": direction, "setup_type": candidate["setup_type"], "conviction": conviction,
        "entry": price, "sl": sl, "tp": tp, "stop_distance": stop_distance,
        "m5_direction": m5_direction, "regime": regime,
        "m15_direction": m15_direction, "h1_direction": h1_direction, "m1_note": m1_note,
    }


def run_once(cfg: BotConfig, state: dict) -> dict:
    snap = parse_chart_file(cfg.data_file)

    outcomes, seen = parse_outcomes(cfg.outcome_file, state.get("outcome_lines_seen", 0))
    state["outcome_lines_seen"] = seen
    resolve_pending(state, outcomes)

    m5_direction_now = direction_for(snap.ind["M5"])
    update_standdown(state, outcomes, m5_direction_now)

    expired = expire_stale_pending(state, cfg.time_decay_seconds)
    if expired:
        state["last_signal_id"] = state.get("last_signal_id", 0) + 1
        expire_id = state["last_signal_id"]
        log.info("time-decay: %s signal(s) unresolved after %ds, issuing CLOSE (id=%d)",
                  expired, int(cfg.time_decay_seconds), expire_id)
        if not cfg.dry_run:
            write_signal(cfg, expire_id, snap.symbol, "CLOSE", 0.0, 0.0, 0.0, "timedecay", "10-minute time-decay")
        for sid in expired:
            record_trade_log({
                "timestamp": datetime.now(timezone.utc).isoformat(), "outcome": "EXPIRED_NO_FILL",
                "resolution_time": datetime.now(timezone.utc).isoformat(),
                "notes": f"original signal_id={sid}",
            })

    last_bar_time = snap.bars["M5"]["time"].iloc[-1].isoformat()
    if state.get("last_bar_time") == last_bar_time:
        log.debug("no new M5 bar since %s, skipping this cycle", last_bar_time)
        return state
    state["last_bar_time"] = last_bar_time

    decision = decide(snap, state)

    scenario = {
        "regime": decision["regime"], "m5_direction": decision["m5_direction"],
        "m15_direction": decision["m15_direction"], "h1_direction": decision["h1_direction"],
    }
    overlay = build_overlay_context(snap)

    if decision["action"] == "NONE":
        report = format_report(snap, decision["m5_direction"], decision["regime"],
                                decision["m15_direction"], decision["h1_direction"], decision)
        log.info("NO TRADE\n%s", report)
        return state

    lot = position_size(snap, cfg, decision["stop_distance"])
    decision["lot"] = lot

    overlay_note = ""
    if not cfg.dry_run and cfg.api_key:
        try:
            overlay_result = call_claude_overlay(decision, snap, scenario, overlay, cfg)
            overlay_note = overlay_result["note"]
            if overlay_result["caution"]:
                decision["conviction"] = "REDUCED"
                overlay_note = "[CAUTION] " + overlay_note
        except Exception as exc:
            log.warning("Claude overlay call failed, proceeding without it: %s", exc)
    decision["overlay_note"] = overlay_note

    report = format_report(snap, decision["m5_direction"], decision["regime"],
                            decision["m15_direction"], decision["h1_direction"], decision)
    log.info("SIGNAL\n%s", report)

    state["last_signal_id"] = state.get("last_signal_id", 0) + 1
    signal_id = state["last_signal_id"]
    register_pending(state, signal_id, decision["setup_type"])

    record_trade_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "setup_type": decision["setup_type"], "direction": decision["action"],
        "entry": decision["entry"], "sl": decision["sl"], "tp": decision["tp"],
        "atr_at_entry": snap.ind["M5"]["atr14"], "regime_adx": snap.ind["M5"]["adx14"],
        "conviction": decision["conviction"], "outcome": "PENDING", "notes": overlay_note,
    })

    if cfg.dry_run:
        log.info("[dry-run] would write signal id=%d", signal_id)
    else:
        write_signal(cfg, signal_id, snap.symbol, decision["action"], lot,
                     decision["sl"], decision["tp"], decision["setup_type"], overlay_note)
        log.info("wrote signal id=%d", signal_id)

    return state


def main_loop(cfg: BotConfig) -> None:
    state = load_state(cfg)
    log.info("watching %s every %.0fs (model=%s, dry_run=%s)", cfg.data_file, cfg.interval, cfg.model, cfg.dry_run)
    while True:
        try:
            state = run_once(cfg, state)
            save_state(cfg, state)
        except FileNotFoundError:
            log.warning("chart data file %s not found yet (has the EA run?), waiting", cfg.data_file)
        except Exception:
            log.exception("unexpected error in main loop, continuing")
        time.sleep(cfg.interval)


# --------------------------------------------------------------------------
# Selftest - runs anywhere, no API key or MT5 needed
# --------------------------------------------------------------------------

def _sample_chart_text(symbol: str = "XAUUSD", *, trending: bool = True) -> str:
    n_m1, n_m5, n_m15, n_h1 = 30, 30, 30, 30
    base = 2340.0

    def bar_lines(n: int, freq_minutes: int, drift: float, start_price: float) -> tuple[list[str], float]:
        rows, price, t0 = [], start_price, datetime(2026, 9, 16, 8, 0)
        for i in range(n):
            o = price
            price = price + drift + (0.15 if i % 3 == 0 else -0.1)
            h, l = max(o, price) + 0.2, min(o, price) - 0.2
            ts = (t0 + pd.Timedelta(minutes=i * freq_minutes)).strftime("%Y.%m.%d %H:%M")
            rows.append(f"{ts},{o:.2f},{h:.2f},{l:.2f},{price:.2f}")
        return rows, price

    drift = 0.35 if trending else 0.0
    m1_rows, m1_last = bar_lines(n_m1, 1, drift * 0.2, base)
    m5_rows, m5_last = bar_lines(n_m5, 5, drift, base)
    m15_rows, m15_last = bar_lines(n_m15, 15, drift, base)
    h1_rows, h1_last = bar_lines(n_h1, 60, drift, base)

    if trending:
        # ema9 within 0.5xATR(1.80) of the last close, so the §4.2 pullback
        # check ("price pulls back to/toward EMA9") actually triggers.
        ema9, ema21, rsi, macd_hist, adx = m5_last + 0.3, m5_last - 1.0, 62.0, 0.4, 30.0
        macd_hist_history = [0.10, 0.15, 0.12, 0.20, 0.32]   # decel then re-expand
    else:
        ema9, ema21, rsi, macd_hist, adx = m5_last, m5_last, 22.0, -0.1, 15.0
        macd_hist_history = [-0.05, -0.03, -0.02, -0.01, 0.00]

    bb_upper, bb_lower = m5_last + 5.0, m5_last - 5.0
    lines = [
        f"#symbol={symbol} digits=2 point=0.01 tick_value=1.00 tick_size=0.01 volume_min=0.01 "
        f"volume_max=50.00 volume_step=0.01 bid={m5_last - 0.1:.2f} ask={m5_last + 0.1:.2f} "
        f"spread=25 equity=5000.00 exported=2026.09.16T12:00:00",
        "##INDICATORS",
        "tf,ema9,ema21,rsi14,macd_hist,adx14,atr14,bb_upper,bb_lower",
        f"M5,{ema9:.2f},{ema21:.2f},{rsi:.2f},{macd_hist:.2f},{adx:.2f},1.80,{bb_upper:.2f},{bb_lower:.2f}",
        f"M15,{m15_last + 1:.2f},{m15_last - 1:.2f},58.00,0.20,NA,NA,NA,NA" if trending
        else "M15,2340.00,2340.00,50.00,0.00,NA,NA,NA,NA",
        f"H1,{h1_last + 1:.2f},{h1_last - 1:.2f},55.00,0.10,NA,NA,NA,NA" if trending
        else "H1,2340.00,2340.00,50.00,0.00,NA,NA,NA,NA",
        "##MACD_HIST_M5",
        "value",
        *[f"{v:.5f}" for v in macd_hist_history],
        "##BARS_M1", "time,open,high,low,close", *m1_rows,
        "##BARS_M5", "time,open,high,low,close", *m5_rows,
        "##BARS_M15", "time,open,high,low,close", *m15_rows,
        "##BARS_H1", "time,open,high,low,close", *h1_rows,
    ]
    return "\n".join(lines) + "\n"


def selftest() -> None:
    import tempfile

    print("claude_signal_bot (XTR) selftest")

    assert direction_for({"ema9": 10, "ema21": 9, "rsi14": 60, "macd_hist": 0.5}) == "BULLISH"
    assert direction_for({"ema9": 9, "ema21": 10, "rsi14": 40, "macd_hist": -0.5}) == "BEARISH"
    assert direction_for({"ema9": 10, "ema21": 9, "rsi14": 40, "macd_hist": 0.5}) == "MIXED"
    print("  direction_for: OK")

    assert regime_for({"adx14": 30}) == "TRENDING"
    assert regime_for({"adx14": 10}) == "RANGING"
    print("  regime_for: OK")

    assert macd_reexpanding([0.10, 0.15, 0.12, 0.20, 0.32], "BULLISH")
    assert not macd_reexpanding([0.10, 0.30, 0.20, 0.10], "BULLISH")            # still decelerating, not re-expanding
    assert not macd_reexpanding([-0.30, -0.20, -0.15, -0.10], "BULLISH")        # bearish re-expansion, wrong sign for BULLISH
    print("  macd_reexpanding: OK")

    assert htf_conviction("BUY", "BULLISH", "BULLISH") == "FULL"
    assert htf_conviction("BUY", "BULLISH", "MIXED") == "REDUCED"
    assert htf_conviction("BUY", "MIXED", "BULLISH") == "REDUCED"
    assert htf_conviction("BUY", "BEARISH", "BULLISH") == "NO_TRADE"
    assert htf_conviction("BUY", "BULLISH", "BEARISH") == "NO_TRADE"
    assert htf_conviction("BUY", "MIXED", "MIXED") == "NO_TRADE"
    print("  htf_conviction matrix: OK")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        trending_file = tmp_path / "chart_trending.txt"
        trending_file.write_text(_sample_chart_text(trending=True))
        snap = parse_chart_file(trending_file)
        assert snap.symbol == "XAUUSD"
        assert set(snap.bars) == {"M1", "M5", "M15", "H1"}
        assert len(snap.macd_hist_m5) == 5
        print(f"  parse_chart_file: OK (M5 bars={len(snap.bars['M5'])}, ind keys={list(snap.ind)})")

        m5_dir = direction_for(snap.ind["M5"])
        regime = regime_for(snap.ind["M5"])
        assert m5_dir == "BULLISH" and regime == "TRENDING"
        pullback = check_trend_pullback(snap, m5_dir)
        assert pullback is not None and pullback["direction"] == "BUY"
        print(f"  check_trend_pullback fires on a trending, re-expanding setup: OK ({pullback})")

        cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "signals.txt",
            ack_file=tmp_path / "ack.txt", outcome_file=tmp_path / "outcomes.txt",
            state_file=tmp_path / "state.json", dry_run=True,
        )
        state = load_state(cfg)
        decision = decide(snap, state)
        assert decision["action"] == "BUY" and decision["setup_type"] == "4.2"
        assert decision["sl"] < decision["entry"]
        print(f"  decide() end-to-end on a trending sample: OK (conviction={decision['conviction']})")

        sl, dist = compute_stop_loss(snap, "BUY")
        atr = snap.ind["M5"]["atr14"]
        assert 1.0 * atr - 1e-6 <= dist <= 1.5 * atr + 1e-6
        print(f"  compute_stop_loss clamps to [1.0, 1.5] x ATR: OK (dist={dist:.3f}, atr={atr})")

        tp = compute_take_profit(snap, "BUY", snap.ask, dist)
        rr = (tp - snap.ask) / dist
        assert 1.4 <= rr <= 2.1   # allow a little slack for the round-number nudge
        print(f"  compute_take_profit lands in the 1.5-2.0R band: OK (R={rr:.2f})")

        lots = position_size(snap, cfg, dist)
        assert snap.volume_min <= lots <= snap.volume_max
        print(f"  position_size respects broker limits: OK (lots={lots})")

        ranging_file = tmp_path / "chart_ranging.txt"
        ranging_file.write_text(_sample_chart_text(trending=False))
        snap2 = parse_chart_file(ranging_file)
        assert regime_for(snap2.ind["M5"]) == "RANGING"
        print("  ranging sample parses with RANGING regime: OK")

        # --- standdown (§10) ---
        state2 = load_state(cfg)
        outcomes = [
            {"signal_id": "1", "setup_type": "4.2", "direction": "BUY", "profit": -5.0, "outcome": "LOSS"},
            {"signal_id": "2", "setup_type": "4.2", "direction": "BUY", "profit": -5.0, "outcome": "LOSS"},
        ]
        update_standdown(state2, outcomes, "BULLISH")
        assert not standdown_gate(state2, "4.2")
        print("  two consecutive losses trigger a standdown: OK")
        update_standdown(state2, [], "MIXED")   # M5 returns to MIXED
        assert standdown_gate(state2, "4.2")    # now clears and re-arms
        assert not state2["standdown"]["4.2"]["active"]
        print("  standdown clears once M5 returns to MIXED: OK")

        # --- time-decay (§9) ---
        state3 = load_state(cfg)
        register_pending(state3, 7, "4.1")
        state3["pending"]["7"]["issued_at"] = "2000-01-01T00:00:00+00:00"   # force staleness
        expired = expire_stale_pending(state3, TIME_DECAY_SECONDS)
        assert expired == ["7"]
        assert "7" not in state3["pending"]
        print("  expire_stale_pending fires after the decay window: OK")

        # --- signal file write/round-trip ---
        write_signal(cfg, 1, "XAUUSD", "BUY", 0.01, snap.ask - 3.0, snap.ask + 5.0, "4.2", "test")
        content = cfg.signal_file.read_text().strip()
        assert content.startswith("1,XAUUSD,BUY,0.01,")
        print(f"  write_signal writes the 9-field format: OK ({content})")

        save_state(cfg, state3)
        reloaded = load_state(cfg)
        assert "7" not in reloaded.get("pending", {})
        print("  save_state/load_state round-trip: OK")

    print("ALL SELFTESTS PASSED")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_config_from_args(args: argparse.Namespace) -> BotConfig:
    data_dir = Path(args.data_dir) if args.data_dir else Path(".")
    return BotConfig(
        data_file=Path(args.data_file) if args.data_file else data_dir / "claude_chart_data.txt",
        signal_file=Path(args.signal_file) if args.signal_file else data_dir / "claude_trade_signals.txt",
        ack_file=Path(args.ack_file) if args.ack_file else data_dir / "claude_trade_ack.txt",
        outcome_file=Path(args.outcome_file) if args.outcome_file else data_dir / "claude_trade_outcomes.txt",
        state_file=Path(args.state_file) if args.state_file else LOG_DIR / "claude_signal_bot_state.json",
        interval=args.interval,
        model=args.model,
        api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        risk_percent=args.risk_percent,
        fallback_equity=args.fallback_equity,
        time_decay_seconds=args.time_decay_seconds,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true", help="run offline logic checks and exit")
    parser.add_argument("--once", action="store_true", help="run a single analysis cycle and exit")
    parser.add_argument("--data-dir", help="MT5 Common\\Files folder shared with ClaudeSignalEA")
    parser.add_argument("--data-file", help="override path to the chart-data file")
    parser.add_argument("--signal-file", help="override path to the signal file written for the EA")
    parser.add_argument("--ack-file", help="override path to the EA's execution-ack file")
    parser.add_argument("--outcome-file", help="override path to the EA's per-position outcome log")
    parser.add_argument("--state-file", help="path to this bot's own bookkeeping")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between analysis cycles")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None, help="defaults to $ANTHROPIC_API_KEY")
    parser.add_argument("--risk-percent", type=float, default=2.0, help="§8 risk per trade, as %% of equity")
    parser.add_argument("--fallback-equity", type=float, default=5000.0,
                         help="used for sizing if the EA's exported equity is 0 (e.g. testing)")
    parser.add_argument("--time-decay-seconds", type=float, default=TIME_DECAY_SECONDS,
                         help="§9: force-close if unresolved after this long")
    parser.add_argument("--dry-run", action="store_true", help="analyze and log but never write the signal file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.selftest:
        selftest()
        return 0

    cfg = build_config_from_args(args)
    if not cfg.dry_run and not cfg.api_key:
        log.warning("no Claude API key set - trades will still be decided mechanically, "
                    "but the §12 discretionary overlay note will be skipped")

    if args.once:
        state = load_state(cfg)
        state = run_once(cfg, state)
        save_state(cfg, state)
        return 0

    main_loop(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

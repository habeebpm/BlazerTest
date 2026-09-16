#!/usr/bin/env python3
"""
XTR-informed, Claude-analyzed signal engine for MQL5/Experts/ClaudeSignalEA.mq5.

Earlier versions of this bot ran the "XTR - XAUUSD Micro-Scalp Signal Logic
Specification" as deterministic if/else code: Python alone decided the
setup, the HTF filter, and the stop/target, and Claude only annotated an
already-fixed trade. This version inverts that: Claude is the analyst, doing
real-time judgment informed by the XTR setup definitions and general
professional trading principles (both embedded in its system prompt as
knowledge, not as hard gates) plus live multi-timeframe data. Claude decides
direction, setup rationale, confidence, stop and target every cycle.

What stays mechanical is risk *containment*, not signal *detection* - things
an autonomous live-trading bot should never leave to a single LLM call:

  - a sanity band on the proposed stop distance (vs. ATR14(M5)), and a
    same-side-of-price check, both applied to Claude's own numbers
  - a confidence floor
  - the §10 two-loss standdown per setup type (Claude is told which types
    are on standdown, and the gate is still enforced in code as a backstop)
  - §9 10-minute time-decay invalidation
  - §8 position sizing arithmetic from account equity and risk % (given
    Claude's stop distance, not Claude's own lot-size math)

Direction, setup rationale, entry timing color, and stop/target placement
are otherwise entirely Claude's call, made fresh every cycle against the
live indicator/bar data - not pattern-matched against a fixed rule table.

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
SETUP_TYPES = ("4.1", "4.2", "4.3", "discretionary")
VALID_ACTIONS = {"BUY", "SELL", "NONE"}

# Knowledge given to Claude as context to reason WITH, not a rule table to
# execute mechanically. It condenses the XTR spec's setup archetypes plus
# general, widely-accepted intraday trading principles, so Claude's
# real-time read is informed by both without being boxed into either.
TRADING_KNOWLEDGE = """\
XTR SETUP ARCHETYPES (apply with judgment, not as rigid triggers):
- 4.1 RSI-Extreme Bounce: mean reversion in a RANGING regime (M5 ADX<25).
  Classic trigger: M5 RSI<30 with price at/below the lower Bollinger band
  (long), or RSI>70 at/above the upper band (short). Historically the
  highest-conviction setup type in this system's own backtests.
- 4.2 Trend-Continuation Pullback: in a TRENDING regime (M5 ADX>=25), price
  pulls back toward the M5 EMA9 in the direction of a clean EMA9/EMA21/RSI/
  MACD alignment. Do NOT treat the first touch of EMA9 as sufficient -
  this system's live history (trades 13 & 14) lost specifically because of
  that. Require MACD histogram momentum to actually be re-expanding in the
  trend's direction (grew after decelerating/flattening), not just present.
- 4.3 Liquidity-Sweep Reversal: an M1 candle briefly breaks a recent swing
  high/low and closes back inside within 1-3 bars - a stop-hunt reclaim.
  Treat as a confluence booster for 4.1/4.2, or a smaller standalone entry.
- Higher-timeframe alignment (M15, H1 read the same way as M5: EMA9 vs
  EMA21, RSI vs 50, MACD histogram sign) raises conviction when it agrees
  and should make you materially more cautious - usually a hard pass -
  when it clearly opposes the M5 read. Two mixed/unclear HTFs is weak
  support, not a green light.

GENERAL PRINCIPLES TO WEIGH ALONGSIDE THE ABOVE:
- Trade with dominant momentum and structure; do not fight a strong,
  established trend without unusually strong counter-evidence.
- Require genuine confluence (multiple independent signals agreeing), not
  a single indicator crossing a threshold.
- Size the stop to current volatility (ATR) and real market structure (the
  nearest swing high/low), not an arbitrary fixed distance.
- Maintain a favorable risk:reward (roughly 1.5-2.0x the stop distance);
  a marginal setup with poor R:R is worse than no trade.
- Be more conservative in thin/illiquid sessions (Asian hours) and more
  willing to act during London/New York overlap.
- Respect round-number price levels (whole-dollar handles) as places price
  often reacts, both for stop placement and target selection.
- Never override an active two-loss standdown for a setup type - if that
  type just lost twice in a row, stand aside on it even if a fresh trigger
  looks tempting, until the underlying regime genuinely resets.
- When evidence is mixed, thin, or contradictory, the correct output is
  NONE. A skipped trade costs nothing; a bad one costs real money.
- You are producing a 10-minute-horizon scalp decision, not a long-term
  thesis - weight the freshest M1/M5 evidence most heavily, and use M15/H1
  as context and a conviction check, not the primary trigger.
"""


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
    min_confidence: float = 60.0
    min_atr_mult: float = 0.25   # sanity floor on the proposed stop distance
    max_atr_mult: float = 3.0    # sanity ceiling on the proposed stop distance
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
# Directional/regime labels and setup hints - all ADVISORY context handed to
# Claude, not a decision path of their own. Kept as plain functions so they
# stay independently testable.
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


def check_rsi_bounce(snap: ChartSnapshot) -> dict | None:
    """4.1-style RSI-extreme + Bollinger-band touch hint."""
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
    it (h4) - used as a hint for the 4.2-style re-expansion gate."""
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
    """4.2-style trend-continuation-pullback hint."""
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
    """4.3-style liquidity-sweep-reversal hint: a brief break of the recent
    M1 swing extreme followed by a close back inside within 1-3 M1 bars."""
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


def htf_conviction(signal_direction: str, m15_direction: str, h1_direction: str) -> str:
    """Advisory FULL/REDUCED/NO_TRADE grade handed to Claude as a hint - it
    is not used to gate anything in code."""
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


def compute_hints(snap: ChartSnapshot, m5_direction: str, regime: str) -> dict:
    """All three setup hints plus the advisory HTF grades for BUY and SELL,
    bundled for the prompt. Claude weighs these; none of them decide."""
    m15_direction = direction_for(snap.ind["M15"])
    h1_direction = direction_for(snap.ind["H1"])
    return {
        "rsi_extreme_bounce": check_rsi_bounce(snap),
        "trend_continuation_pullback": check_trend_pullback(snap, m5_direction),
        "liquidity_sweep_reversal": check_liquidity_sweep(snap),
        "htf_grade_if_buy": htf_conviction("BUY", m15_direction, h1_direction),
        "htf_grade_if_sell": htf_conviction("SELL", m15_direction, h1_direction),
    }


# --------------------------------------------------------------------------
# Position sizing (arithmetic, not a trading decision - kept mechanical)
# --------------------------------------------------------------------------

def position_size(snap: ChartSnapshot, cfg: BotConfig, stop_distance: float) -> float:
    """risk_amount / (stop_distance x value_per_price_unit_per_lot), rounded
    down to the broker's lot step."""
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
# Two-loss standdown per setup type (risk containment, kept mechanical)
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


def active_standdowns(state: dict) -> list[str]:
    return [st for st, e in state.get("standdown", {}).items() if e.get("active")]


# --------------------------------------------------------------------------
# Time-decay invalidation (risk containment, kept mechanical). Best-effort:
# the file protocol has no per-position selector, so an expiry closes every
# position this EA holds, not just the stale one.
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
# Session/round-number context (folded into the analysis prompt)
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


# --------------------------------------------------------------------------
# Claude analysis - the actual trading decision
# --------------------------------------------------------------------------

def build_analysis_prompt(snap: ChartSnapshot, m5_direction: str, m15_direction: str,
                           h1_direction: str, regime: str, hints: dict, overlay: dict,
                           standdown_active: list[str], recent_outcomes: list[dict]) -> tuple[str, str]:
    system = (
        f"You are a disciplined, real-time intraday trading analyst for {snap.symbol}, "
        "making a 10-minute-horizon scalp decision. You reason from the live data given "
        "below, informed by the setup archetypes and principles in the knowledge section "
        "- you do not mechanically apply them as a checklist, and you are free to decide "
        "NONE even when a hint looks superficially satisfied, or to trade on a read that "
        "does not neatly match one of the named archetypes (use setup_type "
        '"discretionary" in that case).\n\n'
        f"{TRADING_KNOWLEDGE}\n"
        "Setup types you may cite: 4.1 (RSI-extreme bounce), 4.2 (trend-continuation "
        "pullback), 4.3 (liquidity-sweep reversal), or discretionary.\n\n"
        "Hard constraints you must still respect (these are enforced in code as a "
        "backstop, but decide as if they are real): never propose a setup type listed "
        "as under an active two-loss standdown; sl/tp must be absolute prices on the "
        "correct side of the current bid/ask; keep the stop distance broadly consistent "
        "with current ATR (very roughly 0.25x-3x ATR14(M5)) - a stop far outside that is "
        "either noise-sized or unreasonably tight, not a considered choice.\n\n"
        "Reply with STRICT JSON only, no markdown fences, no text outside the object: "
        '{"action": "BUY"|"SELL"|"NONE", "setup_type": "4.1"|"4.2"|"4.3"|"discretionary"|null, '
        '"sl": <number or null>, "tp": <number or null>, "confidence": <integer 0-100>, '
        '"reasoning": "<a few sentences covering the M5 read, regime, HTF context, and '
        'why this stop/target>"}. sl/tp/setup_type are null when action is NONE.'
    )

    user = (
        f"Symbol: {snap.symbol}   Bid/Ask: {snap.bid}/{snap.ask}   Spread(points): {snap.spread}\n"
        f"Account equity: {snap.equity}\n\n"
        f"M5 indicators: {json.dumps(snap.ind['M5'])}\n"
        f"M15 indicators: {json.dumps(snap.ind['M15'])}\n"
        f"H1 indicators: {json.dumps(snap.ind['H1'])}\n"
        f"M5 direction (mechanical read): {m5_direction}   Regime (M5 ADX): {regime}\n"
        f"M15 direction (mechanical read): {m15_direction}   H1 direction (mechanical read): {h1_direction}\n"
        f"M5 MACD histogram, last {len(snap.macd_hist_m5)} bars (oldest->newest): {snap.macd_hist_m5}\n\n"
        f"Computed setup hints (advisory only, weigh them, don't just obey them): "
        f"{json.dumps(hints, default=str)}\n\n"
        f"Overlay context: {json.dumps(overlay)}\n"
        f"Setup types currently on two-loss standdown (do not propose these): {standdown_active}\n"
        f"Recent trade outcomes (most recent last): {json.dumps(recent_outcomes)}\n\n"
        f"Recent M1 bars (last 15, oldest first):\n{snap.bars['M1'].tail(15).to_csv(index=False)}\n"
        f"Recent M5 bars (last 20, oldest first):\n{snap.bars['M5'].tail(20).to_csv(index=False)}\n"
        f"Recent M15 bars (last 10, oldest first):\n{snap.bars['M15'].tail(10).to_csv(index=False)}\n"
        f"Recent H1 bars (last 10, oldest first):\n{snap.bars['H1'].tail(10).to_csv(index=False)}\n"
    )
    return system, user


def call_claude_analysis(system: str, user: str, cfg: BotConfig) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.api_key)
    resp = client.messages.create(
        model=cfg.model, max_tokens=700, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def parse_claude_decision(raw: str) -> dict:
    match = JSON_BLOCK_RE.search(raw)
    if not match:
        raise ValueError(f"no JSON object in Claude's reply: {raw[:200]!r}")
    data = json.loads(match.group(0))

    action = str(data.get("action", "NONE")).upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action {action!r}")

    setup_type = data.get("setup_type")
    if setup_type is not None and setup_type not in SETUP_TYPES:
        setup_type = "discretionary"

    return {
        "action": action,
        "setup_type": setup_type,
        "sl": data.get("sl"),
        "tp": data.get("tp"),
        "confidence": float(data.get("confidence", 0) or 0),
        "reasoning": str(data.get("reasoning", ""))[:600].replace(",", ";").replace("\n", " "),
    }


def validate_decision(decision: dict, snap: ChartSnapshot, state: dict, cfg: BotConfig) -> tuple[bool, str]:
    """Backstop safety checks on Claude's own proposal. Never invents or
    adjusts numbers - only accepts or rejects."""
    if decision["action"] == "NONE":
        return True, "no trade"

    setup_type = decision.get("setup_type") or "discretionary"
    if not standdown_gate(state, setup_type):
        return False, f"setup {setup_type} is on two-loss standdown"

    if decision["confidence"] < cfg.min_confidence:
        return False, f"confidence {decision['confidence']:.0f} below floor {cfg.min_confidence:.0f}"

    sl, tp = decision.get("sl"), decision.get("tp")
    if sl is None or tp is None:
        return False, "missing sl/tp"
    sl, tp = float(sl), float(tp)

    price = snap.ask if decision["action"] == "BUY" else snap.bid
    if decision["action"] == "BUY" and not (sl < price < tp):
        return False, "sl/tp not on the correct side of price for a BUY"
    if decision["action"] == "SELL" and not (tp < price < sl):
        return False, "sl/tp not on the correct side of price for a SELL"

    stop_distance = abs(price - sl)
    if stop_distance <= 0:
        return False, "zero stop distance"

    atr = snap.ind["M5"].get("atr14")
    if atr and atr > 0:
        lo, hi = cfg.min_atr_mult * atr, cfg.max_atr_mult * atr
        if not (lo <= stop_distance <= hi):
            return False, f"stop distance {stop_distance:.2f} outside sane band [{lo:.2f}, {hi:.2f}] (ATR={atr})"

    return True, "ok"


# --------------------------------------------------------------------------
# Output format - Claude's own reasoning is the narrative; the mechanical
# labels are kept alongside it for traceability/debugging.
# --------------------------------------------------------------------------

def format_report(snap: ChartSnapshot, m5_direction: str, regime: str, m15_direction: str,
                   h1_direction: str, hints: dict, decision: dict) -> str:
    lines = [
        f"Mechanical context: M5={m5_direction} regime={regime} (ADX={snap.ind['M5']['adx14']}) "
        f"M15={m15_direction} H1={h1_direction}",
        f"Hints: {json.dumps(hints, default=str)}",
    ]
    if decision["action"] == "NONE":
        lines.append(f"Decision: NO TRADE - {decision.get('reasoning', '')}")
    else:
        rr = abs(decision["tp"] - decision["entry"]) / abs(decision["entry"] - decision["sl"])
        lines.append(f"Decision: {decision['action']} - setup {decision['setup_type']} "
                      f"(confidence {decision['confidence']:.0f})")
        lines.append(f"Execution: entry={decision['entry']} sl={decision['sl']} "
                      f"tp={decision['tp']} lot={decision['lot']} R:R=1:{rr:.2f}")
        lines.append(f"Time-decay: close/cancel if unresolved after {int(TIME_DECAY_SECONDS / 60)} minutes")
        lines.append(f"Reasoning: {decision['reasoning']}")
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
            "standdown": {}, "pending": {}, "recent_outcomes": []}


def save_state(cfg: BotConfig, state: dict) -> None:
    tmp = cfg.state_file.with_suffix(cfg.state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, cfg.state_file)


# --------------------------------------------------------------------------
# Main decision cycle
# --------------------------------------------------------------------------

def analyze_with_claude(snap: ChartSnapshot, state: dict, cfg: BotConfig) -> dict:
    """Builds the prompt, calls Claude, and returns a raw (unvalidated)
    decision dict plus the mechanical context used to build the prompt."""
    m5_direction = direction_for(snap.ind["M5"])
    m15_direction = direction_for(snap.ind["M15"])
    h1_direction = direction_for(snap.ind["H1"])
    regime = regime_for(snap.ind["M5"])
    hints = compute_hints(snap, m5_direction, regime)
    overlay = build_overlay_context(snap)
    standdown_active = active_standdowns(state)
    recent_outcomes = state.get("recent_outcomes", [])[-5:]

    system, user = build_analysis_prompt(snap, m5_direction, m15_direction, h1_direction,
                                          regime, hints, overlay, standdown_active, recent_outcomes)
    raw = call_claude_analysis(system, user, cfg)
    decision = parse_claude_decision(raw)
    decision.update({
        "m5_direction": m5_direction, "m15_direction": m15_direction,
        "h1_direction": h1_direction, "regime": regime, "hints": hints,
    })
    return decision


def run_once(cfg: BotConfig, state: dict) -> dict:
    snap = parse_chart_file(cfg.data_file)

    outcomes, seen = parse_outcomes(cfg.outcome_file, state.get("outcome_lines_seen", 0))
    state["outcome_lines_seen"] = seen
    resolve_pending(state, outcomes)
    if outcomes:
        recent = state.setdefault("recent_outcomes", [])
        recent.extend(outcomes)
        state["recent_outcomes"] = recent[-20:]

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

    try:
        decision = analyze_with_claude(snap, state, cfg)
    except Exception:
        log.exception("Claude analysis failed, skipping this cycle without trading")
        return state

    ok, reason = validate_decision(decision, snap, state, cfg)
    if not ok:
        decision = {**decision, "action": "NONE", "reasoning": f"{decision.get('reasoning', '')} [REJECTED: {reason}]"}

    hints = decision["hints"]
    if decision["action"] == "NONE":
        report = format_report(snap, decision["m5_direction"], decision["regime"],
                                decision["m15_direction"], decision["h1_direction"], hints, decision)
        log.info("NO TRADE\n%s", report)
        return state

    price = snap.ask if decision["action"] == "BUY" else snap.bid
    stop_distance = abs(price - float(decision["sl"]))
    lot = position_size(snap, cfg, stop_distance)
    decision["entry"] = price
    decision["lot"] = lot

    report = format_report(snap, decision["m5_direction"], decision["regime"],
                            decision["m15_direction"], decision["h1_direction"], hints, decision)
    log.info("SIGNAL\n%s", report)

    state["last_signal_id"] = state.get("last_signal_id", 0) + 1
    signal_id = state["last_signal_id"]
    register_pending(state, signal_id, decision["setup_type"])

    record_trade_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "setup_type": decision["setup_type"], "direction": decision["action"],
        "entry": decision["entry"], "sl": decision["sl"], "tp": decision["tp"],
        "atr_at_entry": snap.ind["M5"]["atr14"], "regime_adx": snap.ind["M5"]["adx14"],
        "conviction": decision["confidence"], "outcome": "PENDING", "notes": decision["reasoning"],
    })

    if cfg.dry_run:
        log.info("[dry-run] would write signal id=%d", signal_id)
    else:
        write_signal(cfg, signal_id, snap.symbol, decision["action"], lot,
                     decision["sl"], decision["tp"], decision["setup_type"], decision["reasoning"])
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
# Selftest - runs anywhere, no API key or MT5 needed. The Claude call itself
# is monkeypatched so the full run_once path is exercised offline.
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
    from unittest.mock import patch

    print("claude_signal_bot selftest")

    assert direction_for({"ema9": 10, "ema21": 9, "rsi14": 60, "macd_hist": 0.5}) == "BULLISH"
    assert direction_for({"ema9": 9, "ema21": 10, "rsi14": 40, "macd_hist": -0.5}) == "BEARISH"
    assert direction_for({"ema9": 10, "ema21": 9, "rsi14": 40, "macd_hist": 0.5}) == "MIXED"
    print("  direction_for: OK")

    assert regime_for({"adx14": 30}) == "TRENDING"
    assert regime_for({"adx14": 10}) == "RANGING"
    print("  regime_for: OK")

    assert macd_reexpanding([0.10, 0.15, 0.12, 0.20, 0.32], "BULLISH")
    assert not macd_reexpanding([0.10, 0.30, 0.20, 0.10], "BULLISH")
    assert not macd_reexpanding([-0.30, -0.20, -0.15, -0.10], "BULLISH")
    print("  macd_reexpanding: OK")

    assert htf_conviction("BUY", "BULLISH", "BULLISH") == "FULL"
    assert htf_conviction("BUY", "BULLISH", "MIXED") == "REDUCED"
    assert htf_conviction("BUY", "BEARISH", "BULLISH") == "NO_TRADE"
    print("  htf_conviction (advisory) matrix: OK")

    parsed = parse_claude_decision(
        'Sure, here you go:\n```json\n'
        '{"action": "SELL", "setup_type": "4.1", "sl": 2350.0, "tp": 2330.0, '
        '"confidence": 72, "reasoning": "RSI extreme at the upper band"}\n```'
    )
    assert parsed["action"] == "SELL" and parsed["confidence"] == 72.0 and parsed["setup_type"] == "4.1"
    print("  parse_claude_decision extracts JSON from a fenced reply: OK")

    unknown_setup = parse_claude_decision('{"action": "BUY", "setup_type": "made_up", "confidence": 50}')
    assert unknown_setup["setup_type"] == "discretionary"
    print("  parse_claude_decision normalizes an unrecognized setup_type: OK")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        trending_file = tmp_path / "chart_trending.txt"
        trending_file.write_text(_sample_chart_text(trending=True))
        snap = parse_chart_file(trending_file)
        assert snap.symbol == "XAUUSD"
        assert set(snap.bars) == {"M1", "M5", "M15", "H1"}
        print(f"  parse_chart_file: OK (M5 bars={len(snap.bars['M5'])}, ind keys={list(snap.ind)})")

        m5_dir = direction_for(snap.ind["M5"])
        regime = regime_for(snap.ind["M5"])
        hints = compute_hints(snap, m5_dir, regime)
        assert hints["trend_continuation_pullback"] is not None
        print(f"  compute_hints surfaces the 4.2 pullback hint: OK ({hints['trend_continuation_pullback']})")

        cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "signals.txt",
            ack_file=tmp_path / "ack.txt", outcome_file=tmp_path / "outcomes.txt",
            state_file=tmp_path / "state.json", dry_run=True, api_key="test-key",
        )
        atr = snap.ind["M5"]["atr14"]

        good_decision = {
            "action": "BUY", "setup_type": "4.2", "sl": snap.ask - 1.5 * atr, "tp": snap.ask + 2.5 * atr,
            "confidence": 75.0, "reasoning": "trend pullback with re-expanding MACD",
        }
        state = load_state(cfg)
        ok, reason = validate_decision(good_decision, snap, state, cfg)
        assert ok, reason
        print("  validate_decision accepts a well-formed, sanely-sized BUY: OK")

        wrong_side = {**good_decision, "sl": snap.ask + 1.0}
        ok, reason = validate_decision(wrong_side, snap, state, cfg)
        assert not ok
        print(f"  validate_decision rejects sl on the wrong side: OK ({reason})")

        low_conf = {**good_decision, "confidence": 10.0}
        ok, reason = validate_decision(low_conf, snap, state, cfg)
        assert not ok
        print(f"  validate_decision rejects low confidence: OK ({reason})")

        oversized = {**good_decision, "sl": snap.ask - 10 * atr}
        ok, reason = validate_decision(oversized, snap, state, cfg)
        assert not ok
        print(f"  validate_decision rejects a stop far outside the ATR sanity band: OK ({reason})")

        standdown_state = load_state(cfg)
        update_standdown(standdown_state, [
            {"signal_id": "1", "setup_type": "4.2", "direction": "BUY", "profit": -5.0, "outcome": "LOSS"},
            {"signal_id": "2", "setup_type": "4.2", "direction": "BUY", "profit": -5.0, "outcome": "LOSS"},
        ], "BULLISH")
        ok, reason = validate_decision(good_decision, snap, standdown_state, cfg)
        assert not ok
        print(f"  validate_decision enforces the two-loss standdown as a backstop: OK ({reason})")
        update_standdown(standdown_state, [], "MIXED")
        assert standdown_gate(standdown_state, "4.2")
        print("  standdown clears once M5 returns to MIXED: OK")

        # --- end-to-end run_once with the Claude call monkeypatched ---
        fake_reply = json.dumps(good_decision)
        with patch(f"{__name__}.call_claude_analysis", return_value=fake_reply):
            e2e_state = load_state(cfg)
            e2e_state = run_once(cfg, e2e_state)
        assert e2e_state["last_signal_id"] == 1
        assert "1" in e2e_state["pending"]
        print("  run_once end-to-end (Claude mocked) registers a pending signal: OK")

        # --- time-decay (§9) ---
        state3 = load_state(cfg)
        register_pending(state3, 7, "4.1")
        state3["pending"]["7"]["issued_at"] = "2000-01-01T00:00:00+00:00"
        expired = expire_stale_pending(state3, TIME_DECAY_SECONDS)
        assert expired == ["7"]
        assert "7" not in state3["pending"]
        print("  expire_stale_pending fires after the decay window: OK")

        # --- position sizing ---
        lots = position_size(snap, cfg, 2.0 * atr)
        assert snap.volume_min <= lots <= snap.volume_max
        print(f"  position_size respects broker limits: OK (lots={lots})")

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
        min_confidence=args.min_confidence,
        min_atr_mult=args.min_atr_mult,
        max_atr_mult=args.max_atr_mult,
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
    parser.add_argument("--risk-percent", type=float, default=2.0, help="risk per trade, as %% of equity")
    parser.add_argument("--fallback-equity", type=float, default=5000.0,
                         help="used for sizing if the EA's exported equity is 0 (e.g. testing)")
    parser.add_argument("--time-decay-seconds", type=float, default=TIME_DECAY_SECONDS,
                         help="force-close if unresolved after this long")
    parser.add_argument("--min-confidence", type=float, default=60.0,
                         help="reject Claude's signal below this confidence")
    parser.add_argument("--min-atr-mult", type=float, default=0.25,
                         help="reject a proposed stop distance smaller than this x ATR14(M5)")
    parser.add_argument("--max-atr-mult", type=float, default=3.0,
                         help="reject a proposed stop distance larger than this x ATR14(M5)")
    parser.add_argument("--dry-run", action="store_true", help="analyze and log but never write the signal file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.selftest:
        selftest()
        return 0

    cfg = build_config_from_args(args)
    if not cfg.api_key:
        log.error("no Claude API key: set ANTHROPIC_API_KEY or pass --api-key - "
                  "Claude does the actual trading analysis now, so this bot cannot run without it")
        return 1

    if args.once:
        state = load_state(cfg)
        state = run_once(cfg, state)
        save_state(cfg, state)
        return 0

    main_loop(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

Claude is also shown its own recent decisions paired with what actually
happened to them (state["trade_history"], updated as outcomes arrive) and
asked to explicitly self-correct: if its own history shows a setup type or
reasoning pattern that's been losing, say so and adjust rather than
repeating it. It is also free to name a pattern that doesn't fit any of the
three XTR archetypes ("discretionary") rather than being forced into one -
this is meant to be a generative read of the market, not a classifier
choosing among three fixed labels.

    python claude_signal_bot.py --selftest             # offline logic checks, no API key needed
    python claude_signal_bot.py --data-dir <path> --once --dry-run -v
    python claude_signal_bot.py --data-dir <path>       # run the loop, ANTHROPIC_API_KEY required

`--data-dir` should be the MT5 terminal's shared Common\\Files folder (the
one the EA writes to with FILE_COMMON) - typically, on Windows:
    %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files
See CLAUDE_SIGNAL_PIPELINE.md for the full setup and file-format reference.

Optional Telegram fill notifications: set TELEGRAM_BOT_TOKEN and
TELEGRAM_CHAT_ID (or pass --telegram-bot-token/--telegram-chat-id) and a
message is sent the moment the EA's ack log reports a signal EXECUTED -
i.e. actually filled at market, not merely written. `--notify-test` sends
one test message and exits, to verify the bot/chat setup before relying on
it. Unset, notifications are silently skipped.
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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRADE_LOG = LOG_DIR / "xtr_trades.csv"          # matches the §11 schema
TRADE_LOG_FIELDS = [
    "timestamp", "setup_type", "direction", "entry", "sl", "tp", "atr_at_entry",
    "regime_adx", "conviction", "hints_fired", "outcome", "resolution_time", "notes",
]

log = logging.getLogger("claude_signal_bot")

DEFAULT_MODEL = "claude-sonnet-5"
HEADER_RE = re.compile(r"(\w+)=(\S+)")
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
  (long), or RSI>70 at/above the upper band (short). This system's own
  documented backtest assumption is that this is the primary, higher-
  conviction setup type - treat it as the default lean when both 4.1 and
  4.2 conditions are plausibly present, not as merely "historical."
- 4.2 Trend-Continuation Pullback: in a TRENDING regime (M5 ADX>=25), price
  pulls back toward the M5 EMA9 in the direction of a clean EMA9/EMA21/RSI/
  MACD alignment. Do NOT treat the first touch of EMA9 as sufficient -
  this system's live history (trades 13 & 14) lost specifically because of
  that. Require MACD histogram momentum to actually be re-expanding in the
  trend's direction (grew after decelerating/flattening), not just present.
  Documented as the weaker of the two backtested edges - treat it as
  opportunistic, requiring cleaner confluence and warranting a lower
  confidence than an equally clean 4.1, not a co-equal setup type.
- 4.3 Liquidity-Sweep Reversal: an M1 candle briefly breaks a recent swing
  high/low and closes back inside within 1-3 bars - a stop-hunt reclaim.
  M1 is entry-timing refinement, not an independent signal: use this only
  as a confluence booster for a 4.1 or 4.2 read that already stands on its
  own M5/HTF evidence, never as the sole basis for a trade by itself.
- Higher-timeframe alignment (M15, H1 read the same way as M5: EMA9 vs
  EMA21, RSI vs 50, MACD histogram sign) grades your conviction: FULL when
  both M15 and H1 clearly agree with the M5 direction, REDUCED when one
  clearly agrees and the other is mixed/unclear, and NO_TRADE when M5 is
  clearly opposed by either HTF or both are mixed/unclear - treat NO_TRADE
  conviction as a hard pass in practice, not merely "more cautious."

GENERAL PRINCIPLES TO WEIGH ALONGSIDE THE ABOVE:
- Trade with dominant momentum and structure; do not fight a strong,
  established trend without unusually strong counter-evidence.
- Require genuine confluence (multiple independent signals agreeing), not
  a single indicator crossing a threshold.
- Size the stop to current volatility and real market structure, not an
  arbitrary fixed distance: typically 1.0-1.5x M5 ATR14 as the working
  range, widened only when a genuine nearby swing high/low calls for more
  room, tightened only when structure sits closer than that. The stop
  distance is rejected outright if it falls far outside this (see the
  actual enforced band below) - 1.0-1.5x is the target to aim for within
  that band, not just the minimum that survives the check.
- Maintain a favorable risk:reward (roughly 1.5-2.0x the stop distance);
  a marginal setup with poor R:R is worse than no trade.
- Be more conservative in thin/illiquid sessions (Asian hours) and more
  willing to act during London/New York overlap.
- News check: gold is unusually macro-sensitive. If the overlay context
  below shows a HIGH-importance USD event (NFP, CPI, FOMC, PPI, and
  similar) due within roughly the next 15-20 minutes, stand fully aside
  regardless of how clean the technical setup looks - the move is about
  to be repriced, not traded. In the 15-20 minutes just after such an
  event fired, be materially more cautious: spreads widen and price can
  gap or whipsaw in ways that invalidate normal technical structure, so
  require unusually clean confluence before treating a post-release move
  as a genuine signal rather than release noise still settling. Absent
  any high-impact event nearby, this changes nothing.
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

SELF-CORRECTION (learn from the last, apply it to the present):
- You will be shown your own recent decisions paired with what actually
  happened to them. Read that history before deciding. If it shows a
  pattern - a setup type losing repeatedly, a reasoning style that keeps
  getting the regime wrong, ignoring HTF opposition and paying for it -
  say so explicitly and adjust this cycle instead of repeating it.
- This is not just the two-loss standdown (which is enforced regardless).
  It's about the texture of *why* recent calls went wrong, which the
  standdown counter alone doesn't capture - e.g. "the last two 4.2s both
  entered right as HTF flipped against them" is worth noticing even before
  a formal standdown triggers.
- Do not overcorrect on a small sample - one loss is not a pattern. Be
  specific about what you're adjusting and why, or say plainly that
  recent history shows nothing worth changing.
- You are not limited to the three named archetypes. If the live data
  shows a genuine, reasoned setup that doesn't match 4.1/4.2/4.3, describe
  it and use setup_type "discretionary" rather than forcing a label that
  doesn't fit or defaulting to NONE out of caution alone.
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
    fixed_lot: float = 0.05  # always use this lot size; 0 falls back to risk-based sizing instead
    time_decay_seconds: float = TIME_DECAY_SECONDS
    min_confidence: float = 60.0
    min_atr_mult: float = 0.25   # sanity floor on the proposed stop distance
    max_atr_mult: float = 3.0    # sanity ceiling on the proposed stop distance
    max_concurrent_signals: int = 1   # pending (unresolved) signals allowed at once
    require_htf_gate: bool = True     # skip the Claude call entirely when htf_align == "NONE"
    max_daily_loss_usd: float = 0.0   # 0 = disabled; halt new signals once today's realized pnl <= -this
    max_trades_per_day: int = 0       # 0 = disabled; halt new signals once today's EXECUTED count reaches this
    max_spread_mult: float = 0.0      # 0 = disabled; skip the cycle when spread > this x the recent rolling median
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_timeout: float = 10.0
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
    htf_align: str         # BUY/SELL/NONE - the EA's own mechanical 2-of-3 pre-filter
    htf_strength: int      # 2 or 3 - how many of {M5,M15,H1} agreed; 0 when htf_align is NONE
    ind: dict            # {"M5": {...}, "M15": {...}, "H1": {...}}
    macd_hist_m5: list    # oldest -> newest
    bars: dict            # {"M1": df, "M5": df, "M15": df, "H1": df}
    # News awareness (XTR's "News check" step), from the EA's Economic
    # Calendar query. None/"" means either "checked, nothing found in the
    # window" or "calendar unavailable on this broker's server" - the EA
    # can't tell those apart (see ClaudeSignalEA.mq5's own comment), so
    # neither can this. Defaulted so callers with no news data (the
    # backtest harness; an older export file) don't need to supply them.
    news_next_min: int | None = None
    news_next_name: str = ""
    news_recent_min: int | None = None
    news_recent_name: str = ""


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

    # News awareness: news_next_min/news_recent_min in the header are
    # authoritative (same pattern as htf_align/htf_strength) - -1, or the
    # fields missing entirely (an older export, or InpEnableNewsCheck off),
    # both mean "no news data"; see ChartSnapshot's own comment on why
    # that's ambiguous with "checked, found nothing" and left that way
    # rather than guessed at. ##NEWS (if present) supplies only the
    # human-readable event name the header can't hold (it may contain
    # spaces the header's key=value parsing can't handle).
    def _news_minutes(raw: str | None) -> int | None:
        if raw is None:
            return None
        value = int(raw)
        return value if value >= 0 else None

    news_next_min = _news_minutes(meta.get("news_next_min"))
    news_recent_min = _news_minutes(meta.get("news_recent_min"))
    news_next_name = news_recent_name = ""
    if "NEWS" in sections:
        rows = sections["NEWS"]
        header = rows[0].split(",")
        for row in rows[1:]:
            vals = row.split(",", len(header) - 1)
            d = dict(zip(header, vals))
            name = d.get("name", "NA")
            if d.get("when") == "NEXT" and news_next_min is not None:
                news_next_name = name
            elif d.get("when") == "RECENT" and news_recent_min is not None:
                news_recent_name = name

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

    htf_align = meta.get("htf_align")
    htf_strength_raw = meta.get("htf_strength")
    if htf_align not in ("BUY", "SELL", "NONE"):
        # Older export files / hand-built fixtures without the field: fall
        # back to computing it locally, via the identical rule, rather than
        # leaving the gate permanently open or closed by accident.
        m5_dir, m15_dir, h1_dir = direction_for(ind["M5"]), direction_for(ind["M15"]), direction_for(ind["H1"])
        htf_align = htf_gate_from_directions(m5_dir, m15_dir, h1_dir)
        htf_strength = htf_strength_from_directions(m5_dir, m15_dir, h1_dir)
    elif htf_strength_raw is not None:
        htf_strength = int(htf_strength_raw)
    else:
        # htf_align present but htf_strength missing (an export from before
        # the strength field existed): 2 is the only guarantee htf_align
        # itself makes when it's not NONE - not necessarily the true count.
        htf_strength = 0 if htf_align == "NONE" else 2

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
        htf_align=htf_align,
        htf_strength=htf_strength,
        ind=ind,
        macd_hist_m5=macd_hist_m5,
        bars=bars,
        news_next_min=news_next_min,
        news_next_name=news_next_name,
        news_recent_min=news_recent_min,
        news_recent_name=news_recent_name,
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


def htf_gate_from_directions(m5_dir: str, m15_dir: str, h1_dir: str) -> str:
    """The mechanical 'at least 2 of {M5,M15,H1} agree' cost pre-filter.
    Returns BUY/SELL/NONE. Mirrors ClaudeSignalEA.mq5's own HtfAlignment()
    exactly - the EA computes and exports this as the authoritative value
    (ChartSnapshot.htf_align), so live parsing never calls this; it exists
    for the one path with no EA to ask - the backtest harness - and as
    parse_chart_file's fallback for an export file missing the field."""
    bulls = sum(d == "BULLISH" for d in (m5_dir, m15_dir, h1_dir))
    bears = sum(d == "BEARISH" for d in (m5_dir, m15_dir, h1_dir))
    if bulls >= 2:
        return "BUY"
    if bears >= 2:
        return "SELL"
    return "NONE"


def htf_strength_from_directions(m5_dir: str, m15_dir: str, h1_dir: str) -> int:
    """Companion to htf_gate_from_directions: how many of the 3 agreed (2 or
    3), 0 when the gate is NONE. Mirrors ClaudeSignalEA.mq5's HtfAlignment
    `strength` out-param; same fallback-only usage as the gate itself."""
    bulls = sum(d == "BULLISH" for d in (m5_dir, m15_dir, h1_dir))
    bears = sum(d == "BEARISH" for d in (m5_dir, m15_dir, h1_dir))
    return max(bulls, bears) if (bulls >= 2 or bears >= 2) else 0


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
    down to the broker's lot step - unless cfg.fixed_lot is set (>0), in
    which case that lot is used directly instead of the risk-based
    calculation. Even then, the broker's own min/max/step still apply -
    that clamp is a mechanical safety check worth keeping regardless of
    who chose the number, the same way it always has for the computed
    value."""
    step = snap.volume_step or 0.01
    if cfg.fixed_lot > 0:
        lots = math.floor(cfg.fixed_lot / step) * step
        lots = max(snap.volume_min, min(lots, snap.volume_max))
        return round(lots, 2)

    equity = snap.equity if snap.equity > 0 else cfg.fallback_equity
    risk_amount = equity * (cfg.risk_percent / 100.0)
    value_per_unit = (snap.tick_value / snap.tick_size) if snap.tick_size else 0.0
    if value_per_unit <= 0 or stop_distance <= 0:
        return snap.volume_min

    lots = risk_amount / (stop_distance * value_per_unit)
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


# --------------------------------------------------------------------------
# Telegram fill notifications. The EA's ack log already reports EXECUTED
# the instant a signal fills at market (it never queues limit orders), so
# "filled" and "EXECUTED" are the same event here - no separate fill-vs-
# pending tracking is needed.
# --------------------------------------------------------------------------

def parse_acks(path: Path, since_count: int) -> tuple[list[dict], int]:
    if not path.exists():
        return [], since_count
    lines = [l for l in path.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    new_lines = lines[since_count:]
    records = []
    for line in new_lines:
        parts = line.split(",", 3)   # timestamp,id,status,detail - detail may itself be free text
        if len(parts) < 4:
            continue
        records.append({"timestamp": parts[0], "signal_id": parts[1], "status": parts[2], "detail": parts[3]})
    return records, len(lines)


def format_fill_notification(ack: dict, history_entry: dict | None, symbol: str) -> str:
    lines = [f"{symbol} signal FILLED (id {ack['signal_id']})"]
    if history_entry:
        lines.append(f"{history_entry['direction']} - setup {history_entry['setup_type']} "
                      f"- confidence {history_entry['confidence']:.0f}")
        lines.append(f"Entry {history_entry['entry']}  SL {history_entry['sl']}  TP {history_entry['tp']}")
        if history_entry.get("reasoning"):
            lines.append(f"Reasoning: {history_entry['reasoning']}")
    lines.append(f"Broker: {ack['detail']}")
    lines.append(f"Time: {ack['timestamp']}")
    return "\n".join(lines)


def send_telegram_message(cfg: BotConfig, text: str) -> None:
    """Plain text only (no parse_mode) - Claude's own reasoning ends up in
    these messages and may contain characters Telegram's Markdown/HTML
    parsers would choke on, so formatted markup isn't worth the failure
    mode. Raises on failure; callers decide whether that's fatal."""
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": cfg.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=cfg.telegram_timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Telegram API returned HTTP {resp.status}")


def notify_fills(state: dict, acks: list[dict], snap: ChartSnapshot, cfg: BotConfig) -> None:
    if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
        return
    for ack in acks:
        if ack["status"] != "EXECUTED":
            continue
        history_entry = state.get("trade_history", {}).get(ack["signal_id"])
        text = format_fill_notification(ack, history_entry, snap.symbol)
        try:
            send_telegram_message(cfg, text)
            log.info("Telegram notification sent for filled signal id=%s", ack["signal_id"])
        except (urllib.error.URLError, RuntimeError, OSError):
            log.exception("failed to send Telegram notification for signal id=%s", ack["signal_id"])


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
# Daily loss cap / trade-count circuit breaker (risk containment, kept
# mechanical). Unlike the per-setup-type standdown, this halts EVERYTHING
# for the rest of the UTC day once tripped - a blunt, deliberately simple
# backstop against a bad session compounding, independent of which setup
# types are involved. Gates the Claude call itself (like the HTF gate)
# rather than just rejecting afterward, so a tripped breaker costs nothing
# further in API spend either.
# --------------------------------------------------------------------------

def utc_date_str(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def update_daily_tracking(state: dict, now: datetime, acks: list[dict], outcomes: list[dict]) -> None:
    today = utc_date_str(now)
    daily = state.get("daily")
    if not daily or daily.get("date") != today:
        daily = {"date": today, "trade_count": 0, "pnl": 0.0}
        state["daily"] = daily
    daily["trade_count"] += sum(1 for a in acks if a["status"] == "EXECUTED")
    daily["pnl"] += sum(rec["profit"] for rec in outcomes)


def daily_breaker_reason(state: dict, cfg: BotConfig) -> str | None:
    """Returns why the breaker is tripped, or None if new signals may still
    be issued today. Only ever reads state["daily"] - update_daily_tracking
    must be called first each cycle so it reflects today's actual count."""
    daily = state.get("daily", {})
    if cfg.max_trades_per_day > 0 and daily.get("trade_count", 0) >= cfg.max_trades_per_day:
        return f"max trades/day reached ({daily['trade_count']}/{cfg.max_trades_per_day})"
    if cfg.max_daily_loss_usd > 0 and daily.get("pnl", 0.0) <= -cfg.max_daily_loss_usd:
        return f"daily loss cap reached (${daily['pnl']:.2f} <= -${cfg.max_daily_loss_usd:.2f})"
    return None


# --------------------------------------------------------------------------
# Mechanical spread-widening gate (cost/risk pre-filter, kept mechanical).
# The EA already exports the live spread every cycle; this just keeps a
# rolling window of recent readings in state (there's no live EA to ask for
# a "normal" spread the way there is for htf_align) and skips the Claude
# call when the current spread is abnormally wide relative to it - typically
# a sign of a thin/illiquid moment (rollover, a news spike) worth sitting
# out regardless of what the indicators say. Disabled by default
# (max_spread_mult=0) because "abnormal" is broker- and session-specific;
# enable it once you've observed your own broker's normal range.
# --------------------------------------------------------------------------

SPREAD_HISTORY_MAXLEN = 20
SPREAD_HISTORY_MIN_SAMPLES = 10   # don't gate on a cold/short history


def update_spread_history(state: dict, spread: int) -> None:
    hist = state.setdefault("spread_history", [])
    hist.append(spread)
    del hist[:-SPREAD_HISTORY_MAXLEN]


def spread_gate_reason(state: dict, spread: int, cfg: BotConfig) -> str | None:
    if cfg.max_spread_mult <= 0:
        return None
    hist = state.get("spread_history", [])
    if len(hist) < SPREAD_HISTORY_MIN_SAMPLES:
        return None
    baseline = sorted(hist)[len(hist) // 2]   # median, robust to a single earlier spike
    if baseline <= 0:
        return None
    if spread > cfg.max_spread_mult * baseline:
        return f"spread {spread} is {spread / baseline:.1f}x the recent median ({baseline}), over the {cfg.max_spread_mult}x limit"
    return None


# --------------------------------------------------------------------------
# Trade history: Claude's own past decisions paired with what happened to
# them - the "learn from the last" feedback loop. Kept in state.json (not
# just the append-only CSV) because we mutate an entry in place once its
# outcome arrives, which a plain audit log shouldn't do.
# --------------------------------------------------------------------------

MAX_TRADE_HISTORY = 50


def hints_fired_summary(hints: dict) -> str:
    """Which of the three mechanical setup hints actually fired this cycle
    (independent of the setup_type Claude chose to trade, if any) - e.g.
    "4.1,4.3" or "" if none fired. Lets later analysis tell apart Claude
    agreeing with a fired hint, trading discretionary with nothing firing,
    or going against what the hints show - each a different pattern to
    notice, not visible from setup_type alone."""
    fired = []
    if hints.get("rsi_extreme_bounce"):
        fired.append("4.1")
    if hints.get("trend_continuation_pullback"):
        fired.append("4.2")
    if hints.get("liquidity_sweep_reversal"):
        fired.append("4.3")
    return ",".join(fired)


def register_trade_history(state: dict, signal_id: int, decision: dict) -> None:
    history = state.setdefault("trade_history", {})
    history[str(signal_id)] = {
        "signal_id": signal_id, "setup_type": decision["setup_type"], "direction": decision["action"],
        "entry": decision["entry"], "sl": decision["sl"], "tp": decision["tp"],
        "confidence": decision["confidence"], "reasoning": decision["reasoning"],
        "self_correction": decision.get("self_correction", ""),
        "hints_fired": hints_fired_summary(decision.get("hints", {})),
        "outcome": "PENDING", "profit": None, "resolution_time": None,
    }
    if len(history) > MAX_TRADE_HISTORY:
        oldest = sorted(history, key=lambda k: int(k))[: len(history) - MAX_TRADE_HISTORY]
        for k in oldest:
            del history[k]


def _profit_stats(trades: list[dict]) -> dict:
    """wins/losses/pnl/avg_win/avg_loss/profit_factor over a list of
    resolved trade_history entries. profit_factor is gross win $ / gross
    loss $ (None when there are no losses yet to divide by - not 0, since
    an undefined ratio isn't the same claim as a bad one)."""
    wins = [t["profit"] for t in trades if t["outcome"] == "WIN"]
    losses = [t["profit"] for t in trades if t["outcome"] == "LOSS"]
    gross_win = sum(wins)
    gross_loss = -sum(losses)   # losses are negative profit; make this positive
    return {
        "wins": len(wins), "losses": len(losses),
        "pnl": round(gross_win - gross_loss, 2),
        "win_rate_pct": round(100.0 * len(wins) / (len(wins) + len(losses)), 1) if (wins or losses) else None,
        "avg_win": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
    }


def confidence_calibration(resolved: list[dict]) -> dict:
    """Buckets resolved trades by the confidence Claude reported at decision
    time and shows the actual win rate in each bucket - lets the
    self-correction loop notice if its own stated confidence is well
    calibrated (a claimed 80% should win close to 80% of the time) rather
    than just tracking whether it won or lost."""
    buckets = [(0, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    out: dict[str, dict] = {}
    for lo, hi in buckets:
        in_bucket = [t for t in resolved if lo <= t.get("confidence", 0) < hi]
        if not in_bucket:
            continue
        label = f"{lo}-{min(hi, 100)}"
        stats = _profit_stats(in_bucket)
        out[label] = {"n": len(in_bucket), "win_rate_pct": stats["win_rate_pct"]}
    return out


def summarize_recent_performance(state: dict, limit: int = 8) -> dict:
    """Recent resolved trades (with Claude's own past reasoning), a
    per-setup-type breakdown (win/loss counts, pnl, profit factor), overall
    stats, and a confidence-calibration table - the fuller picture the
    self-correction loop reasons from, not just a running tally."""
    history = state.get("trade_history", {})
    resolved = [t for t in history.values() if t.get("outcome") in ("WIN", "LOSS")]
    resolved.sort(key=lambda t: t.get("resolution_time") or "")
    recent = resolved[-limit:]

    by_setup: dict[str, dict] = {}
    for setup_type in {t["setup_type"] for t in resolved}:
        by_setup[setup_type] = _profit_stats([t for t in resolved if t["setup_type"] == setup_type])

    return {
        "recent_trades": recent,
        "by_setup_type": by_setup,
        "overall": _profit_stats(resolved) if resolved else None,
        "confidence_calibration": confidence_calibration(resolved),
    }


# --------------------------------------------------------------------------
# Time-decay invalidation (risk containment, kept mechanical). Targeted: the
# EA closes only the specific position tied to the expired signal id (via
# CLOSE_ID, resolved through its own signal-id -> position-ticket map), not
# every position it holds.
# --------------------------------------------------------------------------

def register_pending(state: dict, signal_id: int, setup_type: str) -> None:
    state.setdefault("pending", {})[str(signal_id)] = {
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "setup_type": setup_type,
    }


def resolve_pending(state: dict, outcomes: list[dict]) -> None:
    pending = state.get("pending", {})
    history = state.get("trade_history", {})
    for rec in outcomes:
        sid = str(rec["signal_id"])
        pending.pop(sid, None)
        if sid in history:
            history[sid]["outcome"] = rec["outcome"]
            history[sid]["profit"] = rec["profit"]
            history[sid]["resolution_time"] = datetime.now(timezone.utc).isoformat()


def expire_stale_pending(state: dict, decay_seconds: float, limit: int = 1) -> list[str]:
    """Returns up to `limit` expired signal ids, oldest first, and removes
    only those from `pending`. The signal file holds one message at a time
    (each write overwrites the last), so a single run_once cycle can only
    reliably deliver one CLOSE_ID - anything beyond `limit` stays in
    `pending` and is retried (still expired) on the next cycle rather than
    silently dropped."""
    pending = state.get("pending", {})
    now = datetime.now(timezone.utc)
    expired_all = [
        sid for sid, info in pending.items()
        if (now - datetime.fromisoformat(info["issued_at"])).total_seconds() >= decay_seconds
    ]
    expired_all.sort(key=lambda sid: pending[sid]["issued_at"])
    to_close = expired_all[:limit]
    for sid in to_close:
        del pending[sid]
    return to_close


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


def macro_news_context(snap: ChartSnapshot) -> str | dict:
    """The EA's Economic Calendar read (XTR's "News check" step), or an
    honest "not evaluated" string when there's nothing to report - either
    the EA has InpEnableNewsCheck off, this export predates the feature, or
    the broker's server doesn't populate the calendar at all. Those three
    cases are indistinguishable from the export alone (see ChartSnapshot's
    own comment) - reported as one "not evaluated", not guessed apart."""
    if snap.news_next_min is None and snap.news_recent_min is None:
        return "not evaluated - no economic-calendar data this cycle (feature off, older export, or broker doesn't populate it)"
    return {
        "next_high_impact_event": (
            {"name": snap.news_next_name, "minutes_until": snap.news_next_min}
            if snap.news_next_min is not None else None
        ),
        "recent_high_impact_event": (
            {"name": snap.news_recent_name, "minutes_since": snap.news_recent_min}
            if snap.news_recent_min is not None else None
        ),
    }


def build_overlay_context(snap: ChartSnapshot) -> dict:
    return {
        "session": session_context(),
        "round_number": round_number_context(snap.bid),
        "dxy_correlation": "not evaluated - no DXY feed configured",
        "macro_news": macro_news_context(snap),
    }


# --------------------------------------------------------------------------
# Claude analysis - the actual trading decision
# --------------------------------------------------------------------------

def build_analysis_prompt(snap: ChartSnapshot, m5_direction: str, m15_direction: str,
                           h1_direction: str, regime: str, hints: dict, overlay: dict,
                           standdown_active: list[str], performance: dict, pending: dict,
                           cfg: BotConfig) -> tuple[str, str]:
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
        "Hard constraints you must still respect (these are the ACTUAL values enforced in "
        "code as a backstop - decide as if they are real, not just decorative): never "
        "propose a setup type listed as under an active two-loss standdown; sl/tp must be "
        "absolute prices on the correct side of the current bid/ask; the stop distance "
        f"will be rejected outright if it falls outside {cfg.min_atr_mult}x-{cfg.max_atr_mult}x "
        "ATR14(M5) - a stop far outside that is either noise-sized or unreasonably tight, "
        f"not a considered choice; confidence below {cfg.min_confidence:.0f} will be rejected "
        "and traded as NONE regardless of what you propose, so don't report a confidence you "
        "don't actually mean; do not propose BUY/SELL if MAX CONCURRENT SIGNALS below shows "
        "the limit is already reached - a live position or pending signal already exists and "
        "adding another is not part of this system's design (10-minute single scalps, not "
        "pyramiding).\n\n"
        "Reply with STRICT JSON only, no markdown fences, no text outside the object: "
        '{"action": "BUY"|"SELL"|"NONE", "setup_type": "4.1"|"4.2"|"4.3"|"discretionary"|null, '
        '"sl": <number or null>, "tp": <number or null>, "confidence": <integer 0-100>, '
        '"self_correction": "<one sentence: what, if anything, you are adjusting based on '
        'RECENT PERFORMANCE HISTORY below, or \'nothing notable\' if there is no pattern yet>", '
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
        f"HTF gate alignment (live/forming-bar, cost pre-filter): {snap.htf_align} "
        f"(strength {snap.htf_strength}/3 - 3 means all of M5/M15/H1 agree, not just a bare majority)\n"
        f"M5 MACD histogram, last {len(snap.macd_hist_m5)} bars (oldest->newest): {snap.macd_hist_m5}\n\n"
        f"Computed setup hints (advisory only, weigh them, don't just obey them): "
        f"{json.dumps(hints, default=str)}\n\n"
        f"Overlay context: {json.dumps(overlay)}\n"
        f"Setup types currently on two-loss standdown (do not propose these): {standdown_active}\n"
        f"MAX CONCURRENT SIGNALS: {len(pending)}/{cfg.max_concurrent_signals} currently pending "
        f"{json.dumps(list(pending.values()))} - do not add a new BUY/SELL if this is already at "
        "the limit.\n\n"
        f"RECENT PERFORMANCE HISTORY (your own past decisions and reasoning, paired with what "
        f"actually happened - read this before deciding, per the SELF-CORRECTION guidance above):\n"
        f"Overall (all resolved trades): {json.dumps(performance['overall'])}\n"
        f"By setup type (wins/losses/pnl/profit_factor - profit_factor null means no losses yet to "
        f"divide by, not a bad ratio): {json.dumps(performance['by_setup_type'])}\n"
        f"Confidence calibration (win rate actually observed per confidence bucket you reported at "
        f"decision time - if a bucket's win rate runs well below its own range, you have been "
        f"overconfident there and should adjust, not just note the fact and repeat it): "
        f"{json.dumps(performance['confidence_calibration'])}\n"
        f"Last {len(performance['recent_trades'])} resolved trades (oldest first): "
        f"{json.dumps(performance['recent_trades'])}\n\n"
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


def _extract_json_object(text: str) -> str:
    """Finds the first balanced {...} object in text, tracking string
    literals so a brace inside a quoted value (e.g. Claude's own reasoning
    text) can't be mistaken for the object's end. More robust than a
    greedy regex against any stray commentary before/after the JSON."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no '{' found")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError("no balanced '}' found")


def parse_claude_decision(raw: str) -> dict:
    try:
        json_text = _extract_json_object(raw)
    except ValueError as exc:
        raise ValueError(f"no JSON object in Claude's reply: {raw[:200]!r}") from exc
    data = json.loads(json_text)

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
        "self_correction": str(data.get("self_correction", ""))[:300].replace(",", ";").replace("\n", " "),
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

    if len(state.get("pending", {})) >= cfg.max_concurrent_signals:
        return False, (f"already at max concurrent signals "
                        f"({len(state.get('pending', {}))}/{cfg.max_concurrent_signals} pending)")

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
    if not atr or atr <= 0:
        # No ATR to sanity-check against - fail safe rather than let an
        # unbounded stop distance through unvalidated.
        return False, "no ATR14(M5) available to sanity-check the stop distance"
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
        if decision.get("self_correction"):
            lines.append(f"Self-correction: {decision['self_correction']}")
    else:
        rr = abs(decision["tp"] - decision["entry"]) / abs(decision["entry"] - decision["sl"])
        lines.append(f"Decision: {decision['action']} - setup {decision['setup_type']} "
                      f"(confidence {decision['confidence']:.0f})")
        lines.append(f"Execution: entry={decision['entry']} sl={decision['sl']} "
                      f"tp={decision['tp']} lot={decision['lot']} R:R=1:{rr:.2f}")
        lines.append(f"Time-decay: close/cancel if unresolved after {int(TIME_DECAY_SECONDS / 60)} minutes")
        lines.append(f"Reasoning: {decision['reasoning']}")
        if decision.get("self_correction"):
            lines.append(f"Self-correction: {decision['self_correction']}")
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
            "ack_lines_seen": 0, "standdown": {}, "pending": {}, "trade_history": {},
            "htf_gate_skips": 0, "daily_breaker_skips": 0, "spread_gate_skips": 0,
            "daily": {}, "spread_history": []}


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
    performance = summarize_recent_performance(state)
    pending = state.get("pending", {})

    system, user = build_analysis_prompt(snap, m5_direction, m15_direction, h1_direction, regime,
                                          hints, overlay, standdown_active, performance, pending, cfg)
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
    resolve_pending(state, outcomes)   # also updates trade_history entries with their outcome

    acks, ack_seen = parse_acks(cfg.ack_file, state.get("ack_lines_seen", 0))
    state["ack_lines_seen"] = ack_seen
    notify_fills(state, acks, snap, cfg)   # Telegram, if configured; a no-op otherwise

    # Both trackers update every cycle (not just on a new M5 bar), so the
    # daily breaker's pnl/trade_count and the spread gate's rolling median
    # stay current even between bar closes.
    update_daily_tracking(state, datetime.now(timezone.utc), acks, outcomes)
    update_spread_history(state, snap.spread)

    m5_direction_now = direction_for(snap.ind["M5"])
    update_standdown(state, outcomes, m5_direction_now)

    # At most one expiry is actioned per cycle (see expire_stale_pending) -
    # the signal file can only carry one message, so a targeted CLOSE_ID
    # for signal A must not be overwritten by one for signal B in the same
    # write. Any further stale entries are picked up on the next cycle.
    expired = expire_stale_pending(state, cfg.time_decay_seconds)
    if expired:
        sid = expired[0]
        setup_type = state.get("trade_history", {}).get(sid, {}).get("setup_type", "unknown")
        state["last_signal_id"] = state.get("last_signal_id", 0) + 1
        expire_id = state["last_signal_id"]
        log.info("time-decay: signal id=%s unresolved after %ds, issuing targeted close (id=%d)",
                  sid, int(cfg.time_decay_seconds), expire_id)
        if not cfg.dry_run:
            write_signal(cfg, expire_id, snap.symbol, "CLOSE_ID", float(sid), 0.0, 0.0,
                         setup_type, "10-minute time-decay")
        record_trade_log({
            "timestamp": datetime.now(timezone.utc).isoformat(), "setup_type": setup_type,
            "outcome": "EXPIRED_NO_FILL", "resolution_time": datetime.now(timezone.utc).isoformat(),
            "notes": f"original signal_id={sid}",
        })

    last_bar_time = snap.bars["M5"]["time"].iloc[-1].isoformat()
    if state.get("last_bar_time") == last_bar_time:
        log.debug("no new M5 bar since %s, skipping this cycle", last_bar_time)
        return state
    state["last_bar_time"] = last_bar_time

    # Daily circuit breaker: a blunt, whole-account halt for the rest of
    # the UTC day once either limit trips, independent of setup type or
    # standdown state. Checked before spending an API call, same as the
    # HTF gate below.
    daily_reason = daily_breaker_reason(state, cfg)
    if daily_reason:
        state["daily_breaker_skips"] = state.get("daily_breaker_skips", 0) + 1
        log.info("daily circuit breaker tripped: %s - skipping the Claude call this cycle (%d skipped so far)",
                  daily_reason, state["daily_breaker_skips"])
        return state

    # Mechanical spread-widening gate: skip when the live spread is
    # abnormally wide vs. its own recent rolling median (disabled by
    # default - see max_spread_mult).
    spread_reason = spread_gate_reason(state, snap.spread, cfg)
    if spread_reason:
        state["spread_gate_skips"] = state.get("spread_gate_skips", 0) + 1
        log.info("mechanical spread gate tripped: %s - skipping the Claude call this cycle (%d skipped so far)",
                  spread_reason, state["spread_gate_skips"])
        return state

    # Mechanical cost pre-filter: skip the Claude call entirely (no tokens
    # spent) when the EA's own htf_align says fewer than 2 of {M5,M15,H1}
    # agree. This trades money for coverage - it also skips cycles a 4.1
    # (RSI-extreme bounce) or 4.3 (liquidity-sweep reversal) setup could
    # have fired on, since both are often contrarian to the higher
    # timeframes by design. See CLAUDE_SIGNAL_PIPELINE.md. Disable with
    # --no-htf-gate if that trade-off isn't the one you want.
    if cfg.require_htf_gate and snap.htf_align == "NONE":
        state["htf_gate_skips"] = state.get("htf_gate_skips", 0) + 1
        log.info("mechanical HTF gate: fewer than 2/3 timeframes aligned (M5=%s M15=%s H1=%s) - "
                  "skipping the Claude call this cycle (%d skipped so far)",
                  m5_direction_now, direction_for(snap.ind["M15"]), direction_for(snap.ind["H1"]),
                  state["htf_gate_skips"])
        return state

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
    register_trade_history(state, signal_id, decision)

    notes = decision["reasoning"]
    if decision.get("self_correction") and decision["self_correction"].lower() != "nothing notable":
        notes = f"[self-correction: {decision['self_correction']}] {notes}"

    record_trade_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "setup_type": decision["setup_type"], "direction": decision["action"],
        "entry": decision["entry"], "sl": decision["sl"], "tp": decision["tp"],
        "atr_at_entry": snap.ind["M5"]["atr14"], "regime_adx": snap.ind["M5"]["adx14"],
        "conviction": decision["confidence"], "hints_fired": hints_fired_summary(hints),
        "outcome": "PENDING", "notes": notes,
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

def _sample_chart_text(symbol: str = "XAUUSD", *, trending: bool = True,
                        force_htf_align: str | None = None,
                        news_next_min: int | None = None, news_next_name: str = "",
                        news_recent_min: int | None = None, news_recent_name: str = "") -> str:
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

    m5_dir, m15_dir, h1_dir, htf_align = ("BULLISH", "BULLISH", "BULLISH", "BUY") if trending \
        else ("MIXED", "MIXED", "MIXED", "NONE")
    if force_htf_align is not None:
        # Override the EA's exported gate independently of the bar/indicator
        # data above, so a test can simulate "M5 itself looks tradeable but
        # M15/H1 don't confirm" without perturbing the fixtures other tests
        # (e.g. the 4.2 pullback hint) rely on.
        htf_align = force_htf_align
        if htf_align == "NONE":
            m15_dir = h1_dir = "MIXED"

    htf_strength = htf_strength_from_directions(m5_dir, m15_dir, h1_dir)
    news_hdr_next = news_next_min if news_next_min is not None else -1
    news_hdr_recent = news_recent_min if news_recent_min is not None else -1
    lines = [
        f"#symbol={symbol} digits=2 point=0.01 tick_value=1.00 tick_size=0.01 volume_min=0.01 "
        f"volume_max=50.00 volume_step=0.01 bid={m5_last - 0.1:.2f} ask={m5_last + 0.1:.2f} "
        f"spread=25 equity=5000.00 m5_dir={m5_dir} m15_dir={m15_dir} h1_dir={h1_dir} "
        f"htf_align={htf_align} htf_strength={htf_strength} news_next_min={news_hdr_next} "
        f"news_recent_min={news_hdr_recent} exported=2026.09.16T12:00:00",
        "##INDICATORS",
        "tf,ema9,ema21,rsi14,macd_hist,adx14,atr14,bb_upper,bb_lower",
        f"M5,{ema9:.2f},{ema21:.2f},{rsi:.2f},{macd_hist:.2f},{adx:.2f},1.80,{bb_upper:.2f},{bb_lower:.2f}",
        f"M15,{m15_last + 1:.2f},{m15_last - 1:.2f},58.00,0.20,NA,NA,NA,NA" if trending
        else "M15,2340.00,2340.00,50.00,0.00,NA,NA,NA,NA",
        f"H1,{h1_last + 1:.2f},{h1_last - 1:.2f},55.00,0.10,NA,NA,NA,NA" if trending
        else "H1,2340.00,2340.00,50.00,0.00,NA,NA,NA,NA",
        "##NEWS",
        "when,minutes,currency,name",
        f"NEXT,{news_hdr_next},USD,{news_next_name or 'NA'}",
        f"RECENT,{news_hdr_recent},USD,{news_recent_name or 'NA'}",
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

    assert htf_gate_from_directions("BULLISH", "BULLISH", "MIXED") == "BUY"     # 2/3 bullish
    assert htf_gate_from_directions("BULLISH", "BEARISH", "MIXED") == "NONE"    # 1/3 each way
    assert htf_gate_from_directions("BEARISH", "BEARISH", "BEARISH") == "SELL"  # 3/3
    assert htf_gate_from_directions("MIXED", "MIXED", "MIXED") == "NONE"
    print("  htf_gate_from_directions (mechanical 2-of-3 pre-filter): OK")

    assert hints_fired_summary({"rsi_extreme_bounce": {"setup_type": "4.1"}, "trend_continuation_pullback": None,
                                 "liquidity_sweep_reversal": None}) == "4.1"
    assert hints_fired_summary({"rsi_extreme_bounce": {"setup_type": "4.1"}, "trend_continuation_pullback": None,
                                 "liquidity_sweep_reversal": {"setup_type": "4.3"}}) == "4.1,4.3"
    assert hints_fired_summary({"rsi_extreme_bounce": None, "trend_continuation_pullback": None,
                                 "liquidity_sweep_reversal": None}) == ""
    print("  hints_fired_summary lists which mechanical hints actually fired: OK")

    # --- daily circuit breaker: a state-level unit test, independent of run_once wiring ---
    daily_state: dict = {}
    now0 = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
    update_daily_tracking(daily_state, now0, [{"status": "EXECUTED"}, {"status": "REJECTED"}],
                           [{"profit": -5.0}])
    assert daily_state["daily"]["trade_count"] == 1, "only EXECUTED acks count as a trade opened"
    assert daily_state["daily"]["pnl"] == -5.0
    cap_cfg = BotConfig(data_file=Path("x"), signal_file=Path("x"), ack_file=Path("x"),
                         outcome_file=Path("x"), state_file=Path("x"), max_trades_per_day=1)
    assert daily_breaker_reason(daily_state, cap_cfg) is not None
    loss_cfg = BotConfig(data_file=Path("x"), signal_file=Path("x"), ack_file=Path("x"),
                          outcome_file=Path("x"), state_file=Path("x"), max_daily_loss_usd=5.0)
    assert daily_breaker_reason(daily_state, loss_cfg) is not None
    unlimited_cfg = BotConfig(data_file=Path("x"), signal_file=Path("x"), ack_file=Path("x"),
                               outcome_file=Path("x"), state_file=Path("x"))
    assert daily_breaker_reason(daily_state, unlimited_cfg) is None
    next_day = datetime(2026, 9, 17, 0, 5, tzinfo=timezone.utc)
    update_daily_tracking(daily_state, next_day, [], [])
    assert daily_state["daily"]["trade_count"] == 0 and daily_state["daily"]["pnl"] == 0.0, \
        "a new UTC day must reset the tracker"
    print("  daily_breaker_reason trips on trade-count and loss caps, and resets on a new UTC day: OK")

    # --- mechanical spread gate: a state-level unit test ---
    spread_cfg = BotConfig(data_file=Path("x"), signal_file=Path("x"), ack_file=Path("x"),
                            outcome_file=Path("x"), state_file=Path("x"), max_spread_mult=2.0)
    spread_state: dict = {}
    for _ in range(SPREAD_HISTORY_MIN_SAMPLES):
        update_spread_history(spread_state, 20)
    assert spread_gate_reason(spread_state, 20, spread_cfg) is None
    assert spread_gate_reason(spread_state, 50, spread_cfg) is not None   # 2.5x the median, over the 2.0x limit
    disabled_cfg = BotConfig(data_file=Path("x"), signal_file=Path("x"), ack_file=Path("x"),
                              outcome_file=Path("x"), state_file=Path("x"))   # max_spread_mult=0 (disabled)
    assert spread_gate_reason(spread_state, 999, disabled_cfg) is None
    cold_state: dict = {}
    update_spread_history(cold_state, 20)
    assert spread_gate_reason(cold_state, 999, spread_cfg) is None, "must not gate on a cold/short history"
    print("  spread_gate_reason trips on an abnormally wide spread vs. the rolling median: OK")

    parsed = parse_claude_decision(
        'Sure, here you go:\n```json\n'
        '{"action": "SELL", "setup_type": "4.1", "sl": 2350.0, "tp": 2330.0, '
        '"confidence": 72, "self_correction": "nothing notable", '
        '"reasoning": "RSI extreme at the upper band"}\n```'
    )
    assert parsed["action"] == "SELL" and parsed["confidence"] == 72.0 and parsed["setup_type"] == "4.1"
    print("  parse_claude_decision extracts JSON from a fenced reply: OK")

    unknown_setup = parse_claude_decision('{"action": "BUY", "setup_type": "made_up", "confidence": 50}')
    assert unknown_setup["setup_type"] == "discretionary"
    print("  parse_claude_decision normalizes an unrecognized setup_type: OK")

    # a brace inside a string field, and trailing commentary, must not confuse
    # extraction the way a naive greedy "first { to last }" regex would
    tricky = parse_claude_decision(
        '{"action": "NONE", "setup_type": null, "sl": null, "tp": null, "confidence": 40, '
        '"self_correction": "nothing notable", '
        '"reasoning": "price is consolidating near the {2340} handle, no edge"}\n\n'
        'Let me know if you would like a deeper structural read {like this}.'
    )
    assert tricky["action"] == "NONE" and "consolidating" in tricky["reasoning"]
    print("  parse_claude_decision handles a brace inside a string field and trailing text: OK")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        trending_file = tmp_path / "chart_trending.txt"
        trending_file.write_text(_sample_chart_text(trending=True))
        snap = parse_chart_file(trending_file)
        assert snap.symbol == "XAUUSD"
        assert set(snap.bars) == {"M1", "M5", "M15", "H1"}
        assert snap.htf_align == "BUY"
        assert snap.htf_strength == 3, "the trending fixture has all 3 timeframes BULLISH, unanimous"
        print(f"  parse_chart_file: OK (M5 bars={len(snap.bars['M5'])}, ind keys={list(snap.ind)}, "
              f"htf_align={snap.htf_align} htf_strength={snap.htf_strength})")

        assert htf_strength_from_directions("BULLISH", "BULLISH", "MIXED") == 2
        assert htf_strength_from_directions("BULLISH", "BULLISH", "BULLISH") == 3
        assert htf_strength_from_directions("BULLISH", "BEARISH", "MIXED") == 0
        print("  htf_strength_from_directions distinguishes a bare majority from unanimous: OK")

        # a header without htf_align at all (an older export file) must fall back to computing
        # it locally via the same rule, not silently gate everything open or closed
        no_field_text = _sample_chart_text(trending=True).replace(
            " m5_dir=BULLISH m15_dir=BULLISH h1_dir=BULLISH htf_align=BUY", "")
        no_field_file = tmp_path / "chart_no_htf_field.txt"
        no_field_file.write_text(no_field_text)
        snap_fallback = parse_chart_file(no_field_file)
        assert snap_fallback.htf_align == "BUY"
        assert snap_fallback.htf_strength == 3
        print("  parse_chart_file falls back to computing htf_align/htf_strength when absent: OK")

        m5_dir = direction_for(snap.ind["M5"])
        regime = regime_for(snap.ind["M5"])
        hints = compute_hints(snap, m5_dir, regime)
        assert hints["trend_continuation_pullback"] is not None
        print(f"  compute_hints surfaces the 4.2 pullback hint: OK ({hints['trend_continuation_pullback']})")

        # --- news awareness: header is authoritative, ##NEWS supplies the name ---
        assert snap.news_next_min is None and snap.news_recent_min is None
        assert macro_news_context(snap) == (
            "not evaluated - no economic-calendar data this cycle "
            "(feature off, older export, or broker doesn't populate it)"
        )
        print("  parse_chart_file/macro_news_context report 'not evaluated' when no news data is exported: OK")

        news_file = tmp_path / "chart_news.txt"
        news_file.write_text(_sample_chart_text(
            trending=True, news_next_min=12, news_next_name="Non-Farm Payrolls",
            news_recent_min=45, news_recent_name="CPI m/m",
        ))
        news_snap = parse_chart_file(news_file)
        assert news_snap.news_next_min == 12 and news_snap.news_next_name == "Non-Farm Payrolls"
        assert news_snap.news_recent_min == 45 and news_snap.news_recent_name == "CPI m/m"
        news_ctx = macro_news_context(news_snap)
        assert news_ctx["next_high_impact_event"] == {"name": "Non-Farm Payrolls", "minutes_until": 12}
        assert news_ctx["recent_high_impact_event"] == {"name": "CPI m/m", "minutes_since": 45}
        print(f"  parse_chart_file/macro_news_context surface a real upcoming/recent high-impact event: OK ({news_ctx})")

        # only "next" present - "recent" must independently report None, not borrow next's data
        next_only_file = tmp_path / "chart_news_next_only.txt"
        next_only_file.write_text(_sample_chart_text(trending=True, news_next_min=8, news_next_name="FOMC Statement"))
        next_only_snap = parse_chart_file(next_only_file)
        assert next_only_snap.news_next_min == 8 and next_only_snap.news_recent_min is None
        print("  news_next/news_recent are parsed independently, not conflated: OK")

        cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "signals.txt",
            ack_file=tmp_path / "ack.txt", outcome_file=tmp_path / "outcomes.txt",
            state_file=tmp_path / "state.json", dry_run=True, api_key="test-key",
        )
        atr = snap.ind["M5"]["atr14"]

        good_decision = {
            "action": "BUY", "setup_type": "4.2", "sl": snap.ask - 1.5 * atr, "tp": snap.ask + 2.5 * atr,
            "confidence": 75.0, "self_correction": "nothing notable",
            "reasoning": "trend pullback with re-expanding MACD",
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
        assert e2e_state["trade_history"]["1"]["outcome"] == "PENDING"
        assert e2e_state["trade_history"]["1"]["reasoning"] == good_decision["reasoning"]
        assert e2e_state["trade_history"]["1"]["self_correction"] == good_decision["self_correction"]
        print("  run_once end-to-end (Claude mocked) registers a pending signal + trade history: OK")

        # a second signal must be rejected outright while one is already pending -
        # force a "new bar" so run_once doesn't just skip the cycle as unchanged
        e2e_state["last_bar_time"] = None
        with patch(f"{__name__}.call_claude_analysis", return_value=fake_reply):
            e2e_state = run_once(cfg, e2e_state)
        assert e2e_state["last_signal_id"] == 1, "max-concurrent-signals backstop should have blocked a 2nd BUY"
        print("  validate_decision's max-concurrent-signals backstop blocks stacking a 2nd signal: OK")

        # --- mechanical HTF gate: skips the Claude call entirely when htf_align == NONE ---
        misaligned_file = tmp_path / "chart_misaligned.txt"
        misaligned_file.write_text(_sample_chart_text(trending=True, force_htf_align="NONE"))
        gated_cfg = BotConfig(
            data_file=misaligned_file, signal_file=tmp_path / "gated_signals.txt",
            ack_file=tmp_path / "gated_ack.txt", outcome_file=tmp_path / "gated_outcomes.txt",
            state_file=tmp_path / "gated_state.json", dry_run=True, api_key="test-key",
        )
        gated_snap = parse_chart_file(misaligned_file)
        assert gated_snap.htf_align == "NONE"
        gated_state = load_state(gated_cfg)
        with patch(f"{__name__}.call_claude_analysis") as mock_call:
            gated_state = run_once(gated_cfg, gated_state)
        mock_call.assert_not_called()
        assert gated_state["last_signal_id"] == 0
        assert gated_state["htf_gate_skips"] == 1
        print(f"  the mechanical HTF gate skips the Claude call when htf_align=NONE: OK "
              f"(skips={gated_state['htf_gate_skips']})")

        # --require-htf-gate=False (--no-htf-gate on the CLI) must bypass the gate and call Claude anyway
        nogate_cfg = BotConfig(
            data_file=misaligned_file, signal_file=tmp_path / "nogate_signals.txt",
            ack_file=tmp_path / "nogate_ack.txt", outcome_file=tmp_path / "nogate_outcomes.txt",
            state_file=tmp_path / "nogate_state.json", dry_run=True, api_key="test-key",
            require_htf_gate=False,
        )
        nogate_state = load_state(nogate_cfg)
        none_reply = json.dumps({**good_decision, "action": "NONE", "setup_type": None, "sl": None, "tp": None})
        with patch(f"{__name__}.call_claude_analysis", return_value=none_reply) as mock_call2:
            nogate_state = run_once(nogate_cfg, nogate_state)
        mock_call2.assert_called_once()
        assert nogate_state.get("htf_gate_skips", 0) == 0
        print("  --no-htf-gate (require_htf_gate=False) bypasses the gate and calls Claude anyway: OK")

        # --- daily circuit breaker wired into run_once: must skip the Claude call, not just the trade ---
        breaker_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "breaker_signals.txt",
            ack_file=tmp_path / "breaker_ack.txt", outcome_file=tmp_path / "breaker_outcomes.txt",
            state_file=tmp_path / "breaker_state.json", dry_run=True, api_key="test-key",
            max_trades_per_day=1,
        )
        breaker_state = load_state(breaker_cfg)
        breaker_state["daily"] = {"date": utc_date_str(), "trade_count": 1, "pnl": 0.0}
        with patch(f"{__name__}.call_claude_analysis") as mock_call3:
            breaker_state = run_once(breaker_cfg, breaker_state)
        mock_call3.assert_not_called()
        assert breaker_state["last_signal_id"] == 0
        assert breaker_state["daily_breaker_skips"] == 1
        print(f"  the daily circuit breaker skips the Claude call once max_trades_per_day is reached: OK "
              f"(skips={breaker_state['daily_breaker_skips']})")

        # --- mechanical spread gate wired into run_once ---
        spread_gate_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "spreadgate_signals.txt",
            ack_file=tmp_path / "spreadgate_ack.txt", outcome_file=tmp_path / "spreadgate_outcomes.txt",
            state_file=tmp_path / "spreadgate_state.json", dry_run=True, api_key="test-key",
            max_spread_mult=1.0,
        )
        spread_gate_state = load_state(spread_gate_cfg)
        spread_gate_state["spread_history"] = [5] * SPREAD_HISTORY_MIN_SAMPLES   # normal spread=25 in the fixture is 5x that
        with patch(f"{__name__}.call_claude_analysis") as mock_call4:
            spread_gate_state = run_once(spread_gate_cfg, spread_gate_state)
        mock_call4.assert_not_called()
        assert spread_gate_state["last_signal_id"] == 0
        assert spread_gate_state["spread_gate_skips"] == 1
        print(f"  the mechanical spread gate skips the Claude call on an abnormally wide spread: OK "
              f"(skips={spread_gate_state['spread_gate_skips']})")

        # --- self-correction feedback loop: outcome resolution feeds back into history ---
        resolve_pending(e2e_state, [
            {"signal_id": "1", "setup_type": "4.2", "direction": "BUY", "profit": -6.0, "outcome": "LOSS"},
        ])
        assert e2e_state["trade_history"]["1"]["outcome"] == "LOSS"
        assert "1" not in e2e_state["pending"]
        perf = summarize_recent_performance(e2e_state)
        assert perf["by_setup_type"]["4.2"]["losses"] == 1
        assert perf["recent_trades"][0]["reasoning"] == good_decision["reasoning"]
        print(f"  resolve_pending feeds outcomes back into trade_history for self-correction: OK ({perf['by_setup_type']})")

        # --- richer performance stats: overall totals + confidence calibration, not just win/loss counts ---
        richer_state = load_state(cfg)
        wins_losses = [
            (True, 6.0, 80.0), (True, 4.0, 85.0), (False, -5.0, 80.0),    # 80-90 bucket: 2W/1L
            (False, -3.0, 65.0), (False, -2.0, 65.0),                     # 60-70 bucket: 0W/2L
        ]
        for i, (is_win, profit, conf) in enumerate(wins_losses, start=1):
            register_trade_history(richer_state, i, {
                "setup_type": "4.1", "action": "BUY", "entry": 2340.0, "sl": 2338.0, "tp": 2344.0,
                "confidence": conf, "reasoning": "test", "self_correction": "nothing notable", "hints": {},
            })
            resolve_pending(richer_state, [{"signal_id": str(i), "setup_type": "4.1", "direction": "BUY",
                                             "profit": profit, "outcome": "WIN" if is_win else "LOSS"}])
        richer_perf = summarize_recent_performance(richer_state)
        assert richer_perf["overall"]["wins"] == 2 and richer_perf["overall"]["losses"] == 3
        assert richer_perf["overall"]["profit_factor"] == round(10.0 / 10.0, 2)
        assert richer_perf["by_setup_type"]["4.1"]["win_rate_pct"] == 40.0
        cal = richer_perf["confidence_calibration"]
        assert cal["80-90"]["n"] == 3 and cal["80-90"]["win_rate_pct"] == round(200 / 3, 1)
        assert cal["60-70"]["n"] == 2 and cal["60-70"]["win_rate_pct"] == 0.0
        print(f"  summarize_recent_performance reports overall profit factor + confidence calibration: OK "
              f"(overall={richer_perf['overall']}, calibration={cal})")

        # a trade with no losses yet must report profit_factor as null (undefined), not 0 or infinity
        only_wins_state = load_state(cfg)
        register_trade_history(only_wins_state, 1, {
            "setup_type": "4.2", "action": "BUY", "entry": 2340.0, "sl": 2338.0, "tp": 2344.0,
            "confidence": 90.0, "reasoning": "test", "self_correction": "nothing notable", "hints": {},
        })
        resolve_pending(only_wins_state, [{"signal_id": "1", "setup_type": "4.2", "direction": "BUY",
                                            "profit": 5.0, "outcome": "WIN"}])
        only_wins_perf = summarize_recent_performance(only_wins_state)
        assert only_wins_perf["by_setup_type"]["4.2"]["profit_factor"] is None
        print("  profit_factor is null (not 0/inf) when there are no losses yet to divide by: OK")

        # the prompt itself must actually surface that history to Claude, and must
        # reflect the ACTUAL configured cfg values, not a hardcoded placeholder
        custom_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "signals2.txt",
            ack_file=tmp_path / "ack2.txt", outcome_file=tmp_path / "outcomes2.txt",
            state_file=tmp_path / "state2.json", dry_run=True, api_key="test-key",
            min_atr_mult=0.5, max_atr_mult=2.0, min_confidence=72.0,
        )
        system, user = build_analysis_prompt(
            snap, m5_dir, "BULLISH", "BULLISH", regime, hints, build_overlay_context(snap),
            [], perf, {"3": {"setup_type": "4.1"}}, custom_cfg,
        )
        assert "SELF-CORRECTION" in system
        assert "trend pullback with re-expanding MACD" in user
        assert "0.5x-2.0x" in system, "prompt must reflect this run's actual ATR band, not a hardcoded one"
        assert "72" in system, "prompt must state the actual confidence floor"
        assert "1/1 currently pending" in user
        print("  build_analysis_prompt surfaces past reasoning + outcomes + the real cfg constraints: OK")

        # --- Telegram fill notifications (network itself is never touched here) ---
        telegram_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "signals.txt",
            ack_file=tmp_path / "tg_ack.txt", outcome_file=tmp_path / "outcomes.txt",
            state_file=tmp_path / "state.json", dry_run=True, api_key="test-key",
            telegram_bot_token="test-token", telegram_chat_id="12345",
        )
        tg_state = load_state(telegram_cfg)
        register_trade_history(tg_state, 1, {**good_decision, "entry": snap.ask})
        telegram_cfg.ack_file.write_text(
            "2026.09.16 12:01:00,1,EXECUTED,ticket=555 price=2350.10 lots=0.37 setup=4.2\n"
            "2026.09.16 12:01:05,2,REJECTED,missing sl/tp\n"
        )
        acks, _ = parse_acks(telegram_cfg.ack_file, 0)
        assert len(acks) == 2 and acks[0]["status"] == "EXECUTED" and acks[1]["status"] == "REJECTED"
        print("  parse_acks: OK")

        sent = []
        with patch(f"{__name__}.send_telegram_message", side_effect=lambda c, text: sent.append(text)):
            notify_fills(tg_state, acks, snap, telegram_cfg)
        assert len(sent) == 1 and "FILLED" in sent[0] and "4.2" in sent[0] and "ticket=555" in sent[0]
        print(f"  notify_fills sends exactly one message, only for the EXECUTED ack: OK ({sent[0].splitlines()[0]})")

        sent_unconfigured = []
        with patch(f"{__name__}.send_telegram_message", side_effect=lambda c, text: sent_unconfigured.append(text)):
            notify_fills(tg_state, acks, snap, cfg)   # cfg has no telegram token/chat id configured
        assert sent_unconfigured == []
        print("  notify_fills is a no-op without telegram_bot_token/chat_id: OK")

        # send_telegram_message itself: verify the request it builds, without touching the network
        class _FakeResponse:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with patch(f"{__name__}.urllib.request.urlopen", return_value=_FakeResponse()) as mock_urlopen:
            send_telegram_message(telegram_cfg, "hello from selftest")
        called_req = mock_urlopen.call_args[0][0]
        assert called_req.full_url == "https://api.telegram.org/bottest-token/sendMessage"
        body = urllib.parse.parse_qs(called_req.data.decode("utf-8"))
        assert body["chat_id"] == ["12345"] and body["text"] == ["hello from selftest"]
        print("  send_telegram_message builds the expected Bot API request: OK")

        # --- time-decay (§9): targeted close, not a blanket one ---
        state3 = load_state(cfg)
        register_pending(state3, 7, "4.1")
        state3["pending"]["7"]["issued_at"] = "2000-01-01T00:00:00+00:00"
        expired = expire_stale_pending(state3, TIME_DECAY_SECONDS)
        assert expired == ["7"]
        assert "7" not in state3["pending"]
        print("  expire_stale_pending fires after the decay window: OK")

        # two stale signals at once: only the oldest is closed this cycle, the
        # other is retried next cycle rather than silently dropped or blanket-closed
        state4 = load_state(cfg)
        register_pending(state4, 10, "4.1")
        register_pending(state4, 11, "4.2")
        state4["pending"]["10"]["issued_at"] = "2000-01-01T00:00:00+00:00"
        state4["pending"]["11"]["issued_at"] = "2000-01-01T00:00:01+00:00"   # 1s younger
        expired = expire_stale_pending(state4, TIME_DECAY_SECONDS)
        assert expired == ["10"], "must close only the oldest expired signal, not both at once"
        assert "11" in state4["pending"], "the non-selected expiry must remain pending for the next cycle"
        print(f"  expire_stale_pending processes one stale signal per cycle, oldest first: OK (kept {list(state4['pending'])})")

        # the write itself must target signal 10 specifically, not a blanket close
        write_signal(cfg, 99, "XAUUSD", "CLOSE_ID", float("10"), 0.0, 0.0, "4.1", "10-minute time-decay")
        assert cfg.signal_file.read_text().strip().startswith("99,XAUUSD,CLOSE_ID,10.0,")
        print("  time-decay writes a targeted CLOSE_ID (original signal id in the lot column): OK")

        # --- position sizing ---
        # cfg.fixed_lot defaults to 0.05 - always used regardless of stop distance,
        # unless explicitly disabled (fixed_lot=0, opting back into risk-based sizing).
        lots = position_size(snap, cfg, 2.0 * atr)
        assert lots == 0.05, f"fixed_lot=0.05 is the default and must always be used as-is: {lots}"
        assert position_size(snap, cfg, 50.0 * atr) == 0.05, "stop distance must not affect a fixed lot"
        print(f"  position_size defaults to a fixed 0.05 lot, ignoring stop distance/risk %: OK (lots={lots})")

        risk_based_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "riskbased_signals.txt",
            ack_file=tmp_path / "riskbased_ack.txt", outcome_file=tmp_path / "riskbased_outcomes.txt",
            state_file=tmp_path / "riskbased_state.json", dry_run=True, api_key="test-key",
            fixed_lot=0.0,   # opt back into risk-based sizing
        )
        risk_lots = position_size(snap, risk_based_cfg, 2.0 * atr)
        assert snap.volume_min <= risk_lots <= snap.volume_max
        bigger_stop_lots = position_size(snap, risk_based_cfg, 4.0 * atr)
        assert bigger_stop_lots < risk_lots, "a wider stop must size down under risk-based sizing (fixed_lot=0)"
        print(f"  fixed_lot=0 opts back into risk-based sizing, which still varies with stop distance: OK "
              f"(lots={risk_lots} at 2x ATR, {bigger_stop_lots} at 4x ATR)")

        # --- fixed_lot override: any chosen size is used as-is, not just the 0.05 default ---
        custom_fixed_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "fixedlot_signals.txt",
            ack_file=tmp_path / "fixedlot_ack.txt", outcome_file=tmp_path / "fixedlot_outcomes.txt",
            state_file=tmp_path / "fixedlot_state.json", dry_run=True, api_key="test-key",
            fixed_lot=0.10,
        )
        assert position_size(snap, custom_fixed_cfg, 2.0 * atr) == 0.10
        assert position_size(snap, custom_fixed_cfg, 50.0 * atr) == 0.10
        print("  fixed_lot honors any explicitly chosen size, not just the 0.05 default: OK")

        over_cfg = BotConfig(
            data_file=trending_file, signal_file=tmp_path / "overlot_signals.txt",
            ack_file=tmp_path / "overlot_ack.txt", outcome_file=tmp_path / "overlot_outcomes.txt",
            state_file=tmp_path / "overlot_state.json", dry_run=True, api_key="test-key",
            fixed_lot=1000.0,   # far above snap.volume_max
        )
        assert position_size(snap, over_cfg, 2.0 * atr) == snap.volume_max, \
            "fixed_lot must still be clamped to the broker's own volume_max, even when explicitly chosen"
        print("  fixed_lot is still clamped to the broker's volume_max (safety check applies regardless of who picked the size): OK")

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
        fixed_lot=args.fixed_lot,
        time_decay_seconds=args.time_decay_seconds,
        min_confidence=args.min_confidence,
        min_atr_mult=args.min_atr_mult,
        max_atr_mult=args.max_atr_mult,
        max_concurrent_signals=args.max_concurrent_signals,
        require_htf_gate=not args.no_htf_gate,
        max_daily_loss_usd=args.max_daily_loss_usd,
        max_trades_per_day=args.max_trades_per_day,
        max_spread_mult=args.max_spread_mult,
        telegram_bot_token=args.telegram_bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=args.telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""),
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
    parser.add_argument("--fixed-lot", type=float, default=0.05,
                         help="use this lot size for every trade instead of risk-based sizing "
                              "(default 0.05, always applied); pass 0 to fall back to risk-based "
                              "sizing instead - still clamped to the broker's min/max/step either way")
    parser.add_argument("--time-decay-seconds", type=float, default=TIME_DECAY_SECONDS,
                         help="force-close if unresolved after this long")
    parser.add_argument("--min-confidence", type=float, default=60.0,
                         help="reject Claude's signal below this confidence")
    parser.add_argument("--min-atr-mult", type=float, default=0.25,
                         help="reject a proposed stop distance smaller than this x ATR14(M5)")
    parser.add_argument("--max-atr-mult", type=float, default=3.0,
                         help="reject a proposed stop distance larger than this x ATR14(M5)")
    parser.add_argument("--max-concurrent-signals", type=int, default=1,
                         help="max pending (unresolved) signals at once; XTR is a single 10-min "
                              "scalp system by design, so this defaults conservatively to 1")
    parser.add_argument("--no-htf-gate", action="store_true",
                         help="disable the mechanical 2-of-3 HTF pre-filter (on by default) and call "
                              "Claude on every cycle regardless of htf_align - costs more, but doesn't "
                              "skip contrarian setups (RSI-extreme bounce, liquidity sweep) the gate would")
    parser.add_argument("--max-daily-loss-usd", type=float, default=0.0,
                         help="halt new signals for the rest of the UTC day once realized pnl today "
                              "reaches -this many dollars; 0 disables the cap (default)")
    parser.add_argument("--max-trades-per-day", type=int, default=0,
                         help="halt new signals for the rest of the UTC day once this many trades have "
                              "executed today; 0 disables the cap (default)")
    parser.add_argument("--max-spread-mult", type=float, default=0.0,
                         help="skip the cycle when the live spread exceeds this x the recent rolling "
                              "median spread; 0 disables the gate (default) - calibrate to your own "
                              "broker's normal range before enabling")
    parser.add_argument("--telegram-bot-token", default=None, help="defaults to $TELEGRAM_BOT_TOKEN")
    parser.add_argument("--telegram-chat-id", default=None, help="defaults to $TELEGRAM_CHAT_ID")
    parser.add_argument("--notify-test", action="store_true",
                         help="send a test Telegram message (verifies bot token/chat id) and exit")
    parser.add_argument("--dry-run", action="store_true", help="analyze and log but never write the signal file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.selftest:
        selftest()
        return 0

    if args.notify_test:
        cfg = build_config_from_args(args)
        if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
            log.error("--notify-test requires a bot token and chat id: set TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
                      "or pass --telegram-bot-token/--telegram-chat-id")
            return 1
        try:
            send_telegram_message(cfg, "ClaudeSignalEA: test notification - Telegram integration is working.")
        except Exception:
            log.exception("test notification failed to send")
            return 1
        log.info("test notification sent")
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

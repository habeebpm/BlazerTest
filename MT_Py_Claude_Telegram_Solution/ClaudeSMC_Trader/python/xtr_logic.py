"""
XTR gold scalping rules (M5 trigger, M15/H1 alignment) as mechanical gates on
top of Claude's verdict - see README.md "XTR alignment gate".

What is used from the XTR specification, and how:
  - Per-timeframe class (sec. 2): BULLISH / BEARISH only when EMA9 vs EMA21,
    RSI14 vs 50 and the MACD(12,26,9) histogram vs 0 ALL agree, else MIXED.
  - Conviction (sec. 4): an HTF that clearly OPPOSES the trade is a hard
    block ("the single most important rule"); both HTFs agreeing = FULL,
    one = REDUCED, none = UNALIGNED.
  - Setup type (sec. 5) and its momentum filter (sec. 6):
      BOUNCE_FAILURE_REVERSAL - M5 was classed against the trade within the
        last few bars (a countertrend bounce) and at least one HTF agrees:
        the M5 histogram must ALREADY be across zero in the trade's
        direction (6b).
      EXTENDED_CHASE - RSI beyond 65/35 or price outside the Bollinger band
        in the trade's direction: the histogram must still be accelerating
        bar-over-bar (6a).
      RSI_EXTREME_BOUNCE - ADX < 25 and a genuine extreme (RSI < 30 at the
        lower band for a buy, > 70 at the upper band for a sell) (5a).
      TREND_CONTINUATION - everything else.
    RSI beyond 30/70 at entry is flagged, never blocked (6c).
  - Regime (sec. 7): ADX14(M5) >= 25 trending, else ranging - reported
    only (log, alert, Claude's context); it never changes the lot.
  - Two-loss range stand-down (sec. 8): XtrStanddown below.

Deliberately NOT taken from the spec (the solution already has an
equivalent, or the rule conflicts with the fixed trading rules):
  - Entry/SL/TP (sec. 9): SL stays $6 at the reference lot and TP1 locks $6
    - i.e. TP1 = 1.0R, the spec's own recommended default. Breakeven at
    0.5 x M5 ATR plus the 15-minute decay window (sec. 10-11) already exist
    as exit_style="breakeven_r_decay" in both EAs.
  - Sizing (sec. 7, 12): lot size, SL and TP rules are never changed by
    this module - the 2% risk sizing and the $6 / $6 / $3 exits stay
    exactly as configured. It only decides whether an entry is allowed.

Pure functions over closed-bar DataFrames (no MT5 imports) - selftest.py
tests them offline.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

import pandas as pd

import market_intel as mi

log = logging.getLogger(__name__)

BULLISH, BEARISH, MIXED = "bullish", "bearish", "mixed"
FULL, REDUCED, UNALIGNED, OPPOSED = "full", "reduced", "unaligned", "opposed"
TREND_CONTINUATION = "trend_continuation"
EXTENDED_CHASE = "extended_chase"
RSI_EXTREME_BOUNCE = "rsi_extreme_bounce"
BOUNCE_FAILURE_REVERSAL = "bounce_failure_reversal"
TRENDING, RANGING = "trending", "ranging"

_CLASS_OF = {"buy": BULLISH, "sell": BEARISH}
_OPPOSITE = {"buy": "sell", "sell": "buy"}

MIN_BARS = 60   # EMA21 + MACD(26, 9) warm-up; 150+ recommended


@dataclass
class TfRead:
    close: float
    ema9: float
    ema21: float
    rsi14: float
    macd_hist: float
    macd_hist_prev: float
    bb_upper: float
    bb_lower: float
    adx14: float
    atr14: float
    cls: str


def classify(ema9: float, ema21: float, rsi14: float, hist: float) -> str:
    bull = (ema9 > ema21, rsi14 > 50.0, hist > 0.0)
    if all(bull):
        return BULLISH
    if not any(bull):
        return BEARISH
    return MIXED


def _classes(closed: pd.DataFrame) -> pd.Series:
    close = closed["close"]
    e9, e21 = mi.ema(close, 9), mi.ema(close, 21)
    r = mi.rsi(close)
    _, _, hist = mi.macd(close)
    return pd.Series([classify(a, b, c, d) for a, b, c, d in zip(e9, e21, r, hist)], index=closed.index)


def read_timeframe(closed: pd.DataFrame) -> TfRead:
    """Indicators on the last CLOSED bar of `closed`."""
    if len(closed) < MIN_BARS:
        raise ValueError(f"need at least {MIN_BARS} closed bars, got {len(closed)}")
    close = closed["close"]
    e9, e21 = mi.ema(close, 9), mi.ema(close, 21)
    r = mi.rsi(close)
    _, _, hist = mi.macd(close)
    _, upper, lower, _, _ = mi.bollinger(close)
    adx, _, _ = mi.adx_di(closed)
    atr = mi.atr(closed)
    last = lambda s: float(s.iloc[-1])  # noqa: E731
    return TfRead(close=last(close), ema9=last(e9), ema21=last(e21), rsi14=last(r),
                  macd_hist=last(hist), macd_hist_prev=float(hist.iloc[-2]),
                  bb_upper=last(upper), bb_lower=last(lower), adx14=last(adx), atr14=last(atr),
                  cls=classify(last(e9), last(e21), last(r), last(hist)))


@dataclass
class XtrAssessment:
    """One reading of M5/M15/H1 at the last closed M5 bar."""
    m5: TfRead
    m15_class: str
    h1_class: str
    m5_direction: str | None          # the sec. 3 all-three M5 trigger, or None
    bounce_direction: str | None      # the sec. 5a RSI-extreme bounce candidate, or None
    m5_recent_classes: list           # M5 classes of the few bars BEFORE the last one

    @property
    def regime(self) -> str:
        return TRENDING if self.m5.adx14 >= 25.0 else RANGING


@dataclass
class XtrDecision:
    direction: str
    conviction: str          # full / reduced / unaligned / opposed
    setup_type: str
    regime: str
    block_reason: str        # "" = allowed
    caution: str             # "" or the 6c RSI-extreme flag

    def summary(self) -> str:
        text = f"{self.conviction.upper()} conviction, {self.setup_type.replace('_', ' ')}, {self.regime}"
        if self.caution:
            text += f" ({self.caution})"
        return text


def m5_signal(m5: TfRead) -> str | None:
    if m5.cls == BULLISH:
        return "buy"
    if m5.cls == BEARISH:
        return "sell"
    return None


def bounce_signal(m5: TfRead) -> str | None:
    if m5.rsi14 < 30.0 and m5.close <= m5.bb_lower:
        return "buy"
    if m5.rsi14 > 70.0 and m5.close >= m5.bb_upper:
        return "sell"
    return None


def grade_conviction(direction: str, m15_class: str, h1_class: str) -> str:
    want, against = _CLASS_OF[direction], _CLASS_OF[_OPPOSITE[direction]]
    if against in (m15_class, h1_class):
        return OPPOSED
    agrees = (m15_class == want) + (h1_class == want)
    return FULL if agrees == 2 else REDUCED if agrees == 1 else UNALIGNED


def classify_setup(direction: str, a: XtrAssessment, conviction: str) -> str:
    m5 = a.m5
    against = _CLASS_OF[_OPPOSITE[direction]]
    # A countertrend bounce in the last few M5 bars that has now ENDED (the
    # last bar no longer reads against the trade) and an HTF agreeing: the
    # trend is resuming. While M5 still reads against it, it is not a
    # failed bounce yet (it may be an RSI-extreme bounce candidate instead).
    countertrend = against in a.m5_recent_classes and m5.cls != against
    if countertrend and conviction in (FULL, REDUCED):
        return BOUNCE_FAILURE_REVERSAL
    extended = ((direction == "buy" and (m5.rsi14 > 65.0 or m5.close > m5.bb_upper))
                or (direction == "sell" and (m5.rsi14 < 35.0 or m5.close < m5.bb_lower)))
    if extended:
        return EXTENDED_CHASE
    if m5.adx14 < 25.0 and a.bounce_direction == direction:
        return RSI_EXTREME_BOUNCE
    return TREND_CONTINUATION


def extended_entry_passes(direction: str, m5: TfRead) -> bool:
    return m5.macd_hist > m5.macd_hist_prev if direction == "buy" else m5.macd_hist < m5.macd_hist_prev


def reversal_confirmation_passes(direction: str, m5: TfRead) -> bool:
    return m5.macd_hist > 0.0 if direction == "buy" else m5.macd_hist < 0.0


def assess(gateway, symbol: str, bars: int = 200, recent: int = 6) -> XtrAssessment:
    """Reads M5/M15/H1 (closed bars only). Raises if any timeframe is
    unavailable or too short - callers treat that as "no XTR reading"."""
    frames = {}
    for tf in ("M5", "M15", "H1"):
        df = gateway.get_bars(symbol, tf, bars)
        frames[tf] = df.iloc[:-1].reset_index(drop=True)   # drop the forming bar
    m5 = read_timeframe(frames["M5"])
    classes = _classes(frames["M5"])
    return XtrAssessment(
        m5=m5, m15_class=read_timeframe(frames["M15"]).cls, h1_class=read_timeframe(frames["H1"]).cls,
        m5_direction=m5_signal(m5), bounce_direction=bounce_signal(m5),
        m5_recent_classes=list(classes.iloc[-1 - recent:-1]))


def evaluate(direction: str, a: XtrAssessment, cfg, standdown: "XtrStanddown | None" = None) -> XtrDecision:
    """Grades `direction` (Claude's call) against the XTR rules. cfg.xtr_gate:
    "block_opposed" (default) blocks an opposed HTF, a failed momentum filter
    and an active stand-down; "require_alignment" additionally requires the
    M5 trigger (or an RSI-extreme bounce) in the same direction and at least
    one agreeing HTF, exactly as the spec's own decision flow."""
    conviction = grade_conviction(direction, a.m15_class, a.h1_class)
    setup = classify_setup(direction, a, conviction)
    caution = (f"RSI {a.m5.rsi14:.0f} at an extreme - elevated reversal risk"
               if abs(a.m5.rsi14 - 50.0) > 20.0 else "")
    block = ""
    if conviction == OPPOSED:
        opposing = [tf for tf, c in (("M15", a.m15_class), ("H1", a.h1_class))
                    if c == _CLASS_OF[_OPPOSITE[direction]]]
        block = f"XTR: {'/'.join(opposing)} clearly {_CLASS_OF[_OPPOSITE[direction]]} - never trade against a clear HTF"
    elif setup == EXTENDED_CHASE and not extended_entry_passes(direction, a.m5):
        block = ("XTR: extended entry with M5 MACD histogram no longer accelerating "
                 f"({a.m5.macd_hist_prev:+.3f} -> {a.m5.macd_hist:+.3f}) - wait for a pullback")
    elif setup == BOUNCE_FAILURE_REVERSAL and not reversal_confirmation_passes(direction, a.m5):
        block = (f"XTR: bounce-failure entry before the M5 MACD histogram crossed zero "
                 f"({a.m5.macd_hist:+.3f}) - wait one more bar")
    elif standdown is not None and standdown.active(setup, direction):
        block = (f"XTR: stand-down on {direction} {setup.replace('_', ' ')} "
                 "after 2 losses in this range")
    elif cfg.xtr_gate == "require_alignment":
        triggered = a.m5_direction == direction or a.bounce_direction == direction
        if not triggered:
            block = "XTR: no clean M5 trigger (EMA9/21, RSI, MACD histogram) in this direction"
        elif conviction == UNALIGNED:
            block = "XTR: neither M15 nor H1 agrees (both mixed)"
    return XtrDecision(direction=direction, conviction=conviction, setup_type=setup, regime=a.regime,
                       block_reason=block, caution=caution)


def snapshot_context(a: XtrAssessment | None) -> dict | None:
    """What Claude sees under "xtr" in the market snapshot."""
    if a is None:
        return None
    m5 = a.m5
    return {
        "m5": {"class": m5.cls, "ema9": round(m5.ema9, 2), "ema21": round(m5.ema21, 2),
               "rsi14": round(m5.rsi14, 1), "macd_hist": round(m5.macd_hist, 4),
               "macd_hist_prev": round(m5.macd_hist_prev, 4), "adx14": round(m5.adx14, 1),
               "atr14": round(m5.atr14, 3)},
        "m15_class": a.m15_class, "h1_class": a.h1_class,
        "m5_trigger": a.m5_direction, "rsi_extreme_bounce_candidate": a.bounce_direction,
        "regime": a.regime,
        "buy": _grade_preview("buy", a), "sell": _grade_preview("sell", a),
    }


def _grade_preview(direction: str, a: XtrAssessment) -> dict:
    conviction = grade_conviction(direction, a.m15_class, a.h1_class)
    return {"conviction": conviction, "setup_type": classify_setup(direction, a, conviction)}


# ------------------------------------------------------------ stand-down

class XtrStanddown:
    """Sec. 8: after 2 consecutive losses on the same setup type with entries
    within ~1 ATR of each other, stand down on that setup type until
    (a) an M5 close beyond that range with ADX14(M5) >= 30, or
    (b) M15 or H1 turns clearly in favor of the losing direction where it
        was not at the last loss.
    Learns from real closed trades (MT5 history, joined by ticket), so it
    works across restarts; dry-run trades have no ticket and teach nothing.
    """

    MAX_OPEN = 200   # tickets awaiting their close; older ones are dropped

    def __init__(self, path: str | None):
        self.path = path
        self.open: dict = {}      # ticket -> {setup, entry, atr, direction, m15, h1}
        self.losses: dict = {}    # "setup|direction" -> {count, entry, atr, direction, m15, h1}
        self.seen: list = []      # tickets already counted
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    saved = json.load(f)
                # Only well-formed entries survive a load: one hand-edited
                # or truncated record must not raise on every bar.
                self.open = {str(k): v for k, v in dict(saved.get("open", {})).items()
                             if _valid_record(v)}
                self.losses = {k: v for k, v in dict(saved.get("losses", {})).items()
                               if "|" in str(k) and _valid_record(v)
                               and isinstance(v.get("count"), int)}
                self.seen = [str(t) for t in list(saved.get("seen", []))]
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                log.warning("Could not read %s (%s) - XTR stand-down state starts fresh.", path, exc)
                self.open, self.losses, self.seen = {}, {}, []

    @staticmethod
    def key(setup: str, direction: str) -> str:
        """Losses count per setup type AND direction: two failed buys never
        stand down sells of the same setup type, and a buy loss followed
        by a sell loss is not "two losses in a row" on one setup."""
        return f"{setup}|{direction}"

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"open": self.open, "losses": self.losses, "seen": self.seen[-500:]}, f)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Could not save %s (%s).", self.path, exc)

    def record_entry(self, ticket, decision: XtrDecision, entry: float, a: XtrAssessment) -> None:
        if not ticket:
            return
        self.open[str(ticket)] = {"setup": decision.setup_type, "entry": entry, "atr": a.m5.atr14,
                                  "direction": decision.direction, "m15": a.m15_class, "h1": a.h1_class}
        while len(self.open) > self.MAX_OPEN:   # a close never seen (history window) - oldest first
            self.open.pop(next(iter(self.open)))
        self.save()

    def update_from_closed(self, closed_trades: list) -> None:
        """closed_trades: mt5_gateway.recent_closed_trades() rows, any order."""
        changed = False
        for t in sorted(closed_trades, key=lambda r: r["time"]):
            key = str(t.get("ticket", ""))
            info = self.open.get(key)
            if info is None or key in self.seen:
                continue
            self.seen.append(key)
            self.open.pop(key, None)
            changed = True
            k = self.key(info["setup"], info["direction"])
            if t["pnl_dollars"] < 0:
                prev = self.losses.get(k)
                same_range = prev is not None and abs(info["entry"] - prev["entry"]) < max(info["atr"], 1e-9)
                count = prev["count"] + 1 if same_range else 1
                self.losses[k] = {**info, "count": count}
            else:
                self.losses.pop(k, None)
        if changed:
            self.save()

    def release_if_due(self, a: XtrAssessment) -> None:
        changed = False
        for k, info in list(self.losses.items()):
            if info["count"] < 2:
                continue
            breakout = abs(a.m5.close - info["entry"]) > info["atr"] and a.m5.adx14 >= 30.0
            want = _CLASS_OF[info["direction"]]
            htf_flip = ((a.m15_class == want and info["m15"] != want)
                        or (a.h1_class == want and info["h1"] != want))
            if breakout or htf_flip:
                log.info("XTR stand-down on %s lifted (%s).", k.replace("|", " "),
                         "decisive breakout with ADX >= 30" if breakout else "HTF turned in favor")
                self.losses.pop(k)
                changed = True
        if changed:
            self.save()

    def active(self, setup: str, direction: str) -> bool:
        info = self.losses.get(self.key(setup, direction))
        return bool(info) and info["count"] >= 2


def _valid_record(v) -> bool:
    """A saved open/loss record has every field the stand-down reads."""
    try:
        return (isinstance(v, dict) and v["direction"] in _CLASS_OF and isinstance(v["setup"], str)
                and float(v["entry"]) > 0 and float(v["atr"]) >= 0
                and isinstance(v["m15"], str) and isinstance(v["h1"], str))
    except (KeyError, TypeError, ValueError):
        return False


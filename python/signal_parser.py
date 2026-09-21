"""
Best-effort parser for free-text Telegram trading-signal messages.

Signal channels format calls inconsistently - different keywords, emoji,
line breaks, ranges instead of single prices - so this is deliberately
permissive rather than exact. It extracts what it can and records what it
couldn't in `ParsedSignal.errors`; it never guesses a missing price. Deciding
whether a parsed signal is actually safe to copy is SignalVerifier's job, not
this module's - a message can parse cleanly and still get rejected.

Recognised shapes, mixed and matched:
    BUY XAUUSD @ 2350.00
    SL: 2340.00
    TP1: 2360.00
    TP2: 2370.00

    (emoji) GOLD SELL NOW
    Entry 2355-2358
    Stop Loss 2365
    Take Profit 2345

    Buy Limit Gold 2340 sl 2330 tp 2360

    CLOSE ALL XAUUSD NOW
    Move SL to breakeven on GOLD
    CANCEL the gold sell order
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM = r"[-+]?\d{2,6}(?:\.\d{1,3})?"

_BUY_RE = re.compile(r"\bBUY\b|\bLONG\b", re.IGNORECASE)
_SELL_RE = re.compile(r"\bSELL\b|\bSHORT\b", re.IGNORECASE)
_PENDING_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_PENDING_STOP_RE = re.compile(r"\bSTOP\b(?![\s-]*LOSS)", re.IGNORECASE)
_NOW_RE = re.compile(r"\bNOW\b|\bMARKET\b", re.IGNORECASE)

_CLOSE_RE = re.compile(r"\bCLOSE\b|\bEXIT\b", re.IGNORECASE)
_CANCEL_RE = re.compile(r"\bCANCEL\b", re.IGNORECASE)
_BREAKEVEN_RE = re.compile(r"\bBREAK\s*EVEN\b|\bBE\b", re.IGNORECASE)
_SL_MENTION_RE = re.compile(r"\bSL\b|\bS/L\b|\bSTOP[\s-]*LOSS\b|\bSTOPLOSS\b", re.IGNORECASE)

_ENTRY_RE = re.compile(
    rf"\b(?:ENTRY|EP)\s*(?:PRICE|ZONE)?\s*[:=]?\s*({_NUM})(?:\s*(?:-|/|TO)\s*({_NUM}))?",
    re.IGNORECASE,
)
_AT_RE = re.compile(rf"@\s*({_NUM})(?:\s*(?:-|/|TO)\s*({_NUM}))?")
_SL_RE = re.compile(rf"\b(?:SL|S/L|STOP[\s-]*LOSS|STOPLOSS)\s*[:=]?\s*({_NUM})", re.IGNORECASE)
_TP_RE = re.compile(rf"\b(?:TP\d{{0,2}}|TAKE\s*PROFIT|TARGET)\s*[:=]?\s*({_NUM})",
                     re.IGNORECASE)

DEFAULT_SYMBOL_ALIASES = {
    "GOLD": "XAUUSD", "XAU/USD": "XAUUSD", "XAU-USD": "XAUUSD", "XAU USD": "XAUUSD",
    "XAUUSD": "XAUUSD", "GOLDUSD": "XAUUSD",
}


@dataclass
class ParsedSignal:
    raw_text: str
    action: str = "unknown"          # open | close | cancel | modify_sl | unknown
    symbol: str | None = None
    direction: str | None = None     # buy | sell
    order_type: str = "market"       # market | limit | stop
    entry: float | None = None
    entry_high: float | None = None
    sl: float | None = None
    tps: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def entry_mid(self) -> float | None:
        """Midpoint of an entry zone, or the single entry price, or None."""
        if self.entry is None:
            return None
        if self.entry_high is None:
            return self.entry
        return (self.entry + self.entry_high) / 2.0


def _find_symbol(text: str, aliases: dict) -> str | None:
    for token in sorted(aliases, key=len, reverse=True):
        if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):
            return aliases[token]
    return None


def _find_direction(text: str) -> tuple[str | None, str]:
    buy = _BUY_RE.search(text)
    sell = _SELL_RE.search(text)
    if buy and sell:
        # some messages hedge ("SELL if BUY fails") - the earlier keyword
        # is taken as the intended side
        direction = "buy" if buy.start() < sell.start() else "sell"
    elif buy:
        direction = "buy"
    elif sell:
        direction = "sell"
    else:
        return None, "market"

    order_type = "market"
    if _PENDING_LIMIT_RE.search(text):
        order_type = "limit"
    elif _PENDING_STOP_RE.search(text):
        order_type = "stop"
    return direction, order_type


def parse_signal(text: str, symbol_aliases: dict | None = None) -> ParsedSignal:
    """Turn a raw Telegram message into a structured, best-effort signal."""
    aliases = symbol_aliases or DEFAULT_SYMBOL_ALIASES
    clean = text.replace(",", "")
    sig = ParsedSignal(raw_text=text)
    sig.symbol = _find_symbol(clean, aliases)

    if _CANCEL_RE.search(clean):
        sig.action = "cancel"
        sig.direction, _ = _find_direction(clean)
        return sig

    direction, order_type = _find_direction(clean)

    # A breakeven mention only means "this whole message IS a breakeven
    # instruction" when there's no actual trade data alongside it - a
    # signal like "BUY GOLD 2350 SL 2340 TP 2360, move SL to breakeven
    # after TP1" is an open signal that happens to mention breakeven
    # management, not a standalone "move SL to breakeven" command, and
    # must not have its entry/SL/TP discarded.
    has_trade_data = bool(
        (_ENTRY_RE.search(clean) or _AT_RE.search(clean)) or _SL_RE.search(clean)
    )
    if _BREAKEVEN_RE.search(clean) and _SL_MENTION_RE.search(clean) and not (direction and has_trade_data):
        sig.action = "modify_sl"
        sig.direction = direction
        return sig

    if _CLOSE_RE.search(clean) and direction is None:
        sig.action = "close"
        return sig

    if direction is None:
        sig.action = "unknown"
        sig.errors.append("no BUY/SELL/LONG/SHORT keyword found")
        return sig

    sig.direction = direction
    sig.order_type = order_type
    if _NOW_RE.search(clean):
        sig.order_type = "market"

    entry_match = _ENTRY_RE.search(clean) or _AT_RE.search(clean)
    if entry_match:
        a, b = float(entry_match.group(1)), entry_match.group(2)
        if b is not None:
            b = float(b)
            sig.entry, sig.entry_high = (a, b) if a <= b else (b, a)
        else:
            sig.entry = a

    sl_match = _SL_RE.search(clean)
    if sl_match:
        sig.sl = float(sl_match.group(1))
    else:
        sig.errors.append("no stop-loss found")

    tp_matches = list(_TP_RE.finditer(clean))
    sig.tps = [float(m.group(1)) for m in tp_matches]
    if not sig.tps:
        sig.errors.append("no take-profit found")

    if sig.entry is None:
        # No explicit ENTRY/EP/@ label - fall back to the first bare number
        # that isn't part of an SL/TP match (covers "Buy Limit Gold 2340 sl
        # 2330 tp 2360", a common shorthand with no entry keyword at all).
        exclude = [sl_match.span()] if sl_match else []
        exclude += [m.span() for m in tp_matches]
        for m in re.finditer(_NUM, clean):
            if any(start <= m.start() < end for start, end in exclude):
                continue
            sig.entry = float(m.group(0))
            break

    sig.action = "open"
    return sig

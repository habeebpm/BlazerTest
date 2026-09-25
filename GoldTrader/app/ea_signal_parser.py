"""
UnifiedTrader_EA.mq5's ParseSignalText(), line for line in Python - so the
signal-history replay (signal_replay.py) reads every old message exactly the
way the EA reads a new one. Keep the two in sync: FindWholeWord,
ExtractNumberAt, FindTpLabel, FindSlLabel (incl. the bare "Stop 4342"
label), PrecededByWord, MentionsGold, ParseSignalText.

The message filter that runs BEFORE this (TsmcClassifyMessage) already has
its Python twin: relay/message_filter.py classify_message().
"""
from __future__ import annotations

from dataclasses import dataclass, field

OPEN, CLOSE, CANCEL, UNKNOWN = "OPEN", "CLOSE", "CANCEL", "UNKNOWN"
GOLD_ALIASES = ("XAUUSD", "XAU/USD", "XAU-USD", "XAUUSDT", "GOLD")


@dataclass
class Signal:
    action: str = UNKNOWN
    direction: str = ""          # "buy" / "sell" / ""
    symbol_ok: bool = False
    has_range: bool = False
    entry_a: float = 0.0
    entry_b: float = 0.0
    has_sl: bool = False
    sl: float = 0.0
    tps: list = field(default_factory=list)


def _digit(c: str) -> bool:
    return "0" <= c <= "9"


def _word_ch(c: str) -> bool:
    return _digit(c) or ("A" <= c <= "Z") or ("a" <= c <= "z")


def _letter(c: str) -> bool:
    return ("A" <= c <= "Z") or ("a" <= c <= "z")


def find_whole_word(text: str, word: str, start: int = 0) -> int:
    wlen, tlen, pos = len(word), len(text), start
    while pos <= tlen - wlen:
        idx = text.find(word, pos)
        if idx < 0:
            return -1
        left = idx == 0 or not _word_ch(text[idx - 1])
        right = idx + wlen >= tlen or not _word_ch(text[idx + wlen])
        if left and right:
            return idx
        pos = idx + 1
    return -1


def extract_number_at(text: str, from_pos: int, limit_pos: int, max_skip: int):
    """(value, end) or None - the EA's ExtractNumberAt."""
    tlen = min(limit_pos, len(text))
    i, skipped = max(0, from_pos), 0
    while i < tlen and skipped <= max_skip:
        if _digit(text[i]):
            break
        i += 1
        skipped += 1
    if i >= tlen or not _digit(text[i]):
        return None
    start, saw_dot = i, False
    while i < tlen:
        c = text[i]
        if _digit(c):
            i += 1
            continue
        if c == "." and not saw_dot:
            saw_dot = True
            i += 1
            continue
        break
    token = text[start:i]
    if len(token) < 2:
        return None
    try:
        return float(token.rstrip(".")) if token.rstrip(".") else None, i
    except ValueError:
        return None


def find_tp_label(text: str, from_pos: int):
    """(index, label_end) of "TP", "TP1".. or (-1, -1)."""
    tlen, pos = len(text), max(0, from_pos)
    while pos < tlen:
        idx = text.find("TP", pos)
        if idx < 0:
            return -1, -1
        if idx == 0 or not _letter(text[idx - 1]):
            j, digits = idx + 2, 0
            while j < tlen and _digit(text[j]) and digits < 2:
                j += 1
                digits += 1
            return idx, j
        pos = idx + 1
    return -1, -1


def preceded_by_word(text: str, idx: int, word: str) -> bool:
    j = idx - 1
    while j >= 0 and text[j] in " -":
        j -= 1
    start = j - len(word) + 1
    if start < 0 or text[start:start + len(word)] != word:
        return False
    return start == 0 or not _word_ch(text[start - 1])


def find_sl_label(text: str, from_pos: int):
    """(index, label_end) of the stop label or (-1, -1)."""
    for label in ("STOPLOSS", "STOP LOSS", "STOP-LOSS", "S/L"):
        idx = text.find(label, from_pos)
        if idx >= 0:
            return idx, idx + len(label)
    idx = find_whole_word(text, "SL", from_pos)
    if idx >= 0:
        return idx, idx + 2
    pos = from_pos
    while True:
        idx = find_whole_word(text, "STOP", pos)
        if idx < 0:
            return -1, -1
        num = extract_number_at(text, idx + 4, len(text), 10)
        if (not preceded_by_word(text, idx, "BUY") and not preceded_by_word(text, idx, "SELL")
                and num is not None and num[0] is not None and num[0] >= 100.0):
            return idx, idx + 4
        pos = idx + 4


def mentions_gold(upper: str) -> bool:
    return any(find_whole_word(upper, a) >= 0 for a in GOLD_ALIASES)


def parse(raw: str) -> Signal:
    """The EA's ParseSignalText()."""
    msg = Signal()
    upper = (raw or "").upper()
    tlen = len(upper)
    msg.symbol_ok = mentions_gold(upper)
    if find_whole_word(upper, "CANCEL") >= 0:
        msg.action = CANCEL
        return msg
    buy = find_whole_word(upper, "BUY")
    if buy < 0:
        buy = find_whole_word(upper, "LONG")
    sell = find_whole_word(upper, "SELL")
    if sell < 0:
        sell = find_whole_word(upper, "SHORT")
    direction = ""
    if buy >= 0 or sell >= 0:
        direction = "buy" if (buy >= 0 and (sell < 0 or buy < sell)) else "sell"
    close = find_whole_word(upper, "CLOSE")
    if close < 0:
        close = find_whole_word(upper, "EXIT")
    if close >= 0 and not direction:
        msg.action = CLOSE
        return msg
    if not direction:
        return msg
    msg.direction = direction
    dir_pos = buy if direction == "buy" else sell
    tp_pos, _ = find_tp_label(upper, dir_pos)
    sl_pos, sl_end = find_sl_label(upper, dir_pos)
    cutoff = tlen
    if sl_pos >= 0:
        cutoff = min(cutoff, sl_pos)
    if tp_pos >= 0:
        cutoff = min(cutoff, tp_pos)
    a = extract_number_at(upper, dir_pos + 3, cutoff, 30)
    if a is not None and a[0] is not None:
        msg.entry_a = a[0]
        b = extract_number_at(upper, a[1], cutoff, 8)
        if b is not None and b[0] is not None:
            msg.entry_b = b[0]
            msg.has_range = True
    if sl_pos >= 0:
        s = extract_number_at(upper, sl_end, tlen, 10)
        if s is not None and s[0] is not None:
            msg.sl, msg.has_sl = s[0], True
    scan, guard = dir_pos, 0
    while len(msg.tps) < 6 and guard < 50:
        guard += 1
        found, label_end = find_tp_label(upper, scan)
        if found < 0:
            break
        t = extract_number_at(upper, label_end, tlen, 6)
        if t is not None and t[0] is not None:
            msg.tps.append(t[0])
            scan = t[1]
        else:
            scan = label_end
    msg.action = OPEN
    return msg

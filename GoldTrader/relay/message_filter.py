"""
Trade-only message filter: decides, BEFORE any parsing, whether a Telegram
channel message is worth looking at as a trade message at all.

Signal channels post far more than signals - "Good morning traders ☀️",
motivational/mood posts, market commentary, promos, voice notes, videos,
long analysis threads. Those must be omitted, not parsed: the parser's
CLOSE/EXIT/CANCEL detection is keyword based, so an innocent "Good morning!
Close your charts and relax" would otherwise read as "close all positions".

A message is kept only if it is one of:
  - SIGNAL:  at most max_message_chars long, mentions a direction
             (BUY/SELL/LONG/SHORT) and a price-shaped number (decimals, or >= 100), and
             either the symbol (GOLD/XAU... by default) or a signal label
             (SL/TP/TP1/ENTRY/TARGET) - "Buy the dip 2650" is not a signal;
  - COMMAND: a short management message (at most max_command_chars) that
             contains a command (CLOSE/EXIT/CANCEL/BREAKEVEN/BE, or SL with
             MOVE/ENTRY) and
             consists ONLY of trading vocabulary - "Close all gold trades
             now" passes, "close your eyes and relax" does not.
Everything else - greetings, mood, commentary, emoji-only, too long - is
SKIPPED. Media (video, audio, voice, sticker, GIF, document, poll, ...) and
service messages (pins, joins) are skipped by message_media_kind() before
the text is even read; a photo's caption is used only when
accept_photo_captions is on.

MQL5/Include/TelegramSMC_Common.mqh's TsmcClassifyMessage() implements the
exact same rules for UnifiedTrader_EA.mq5 - keep
the two in sync (same vocabulary, same thresholds).

Pure functions, no Telegram/MT5 imports - tested in relay_selftest.py.
"""
from __future__ import annotations

import re

SKIP, SIGNAL, COMMAND = "skip", "signal", "command"

MAX_CHANNELS = 3              # at most this many source channels per copier/bridge
DEFAULT_MAX_MESSAGE_CHARS = 400
DEFAULT_MAX_COMMAND_CHARS = 60

DIRECTION_WORDS = {"BUY", "SELL", "LONG", "SHORT"}
COMMAND_WORDS = {"CLOSE", "EXIT", "CANCEL", "BREAKEVEN", "BE"}
SIGNAL_LABELS = {"SL", "TP", "ENTRY", "STOPLOSS", "TARGET"}
GOLD_WORDS = {"GOLD", "XAU", "XAUUSD"}

# Every word a management command may contain. A message holding a command
# word plus ANY other word ("your", "eyes", "relax", "morning") is ordinary
# chat, not a command. Numbers and TP1/TP2... are always allowed.
COMMAND_VOCAB = {
    "CLOSE", "EXIT", "CANCEL", "BREAKEVEN", "BREAK", "EVEN", "BE", "MOVE", "SET", "PUT",
    "SL", "S", "L", "STOP", "LOSS", "STOPLOSS", "RISK", "FREE", "TO", "ENTRY", "AT",
    "ALL", "NOW", "HERE", "IT", "THIS", "THAT", "THE", "A", "AN", "REST", "REMAINING",
    "TRADE", "TRADES", "POSITION", "POSITIONS", "ORDER", "ORDERS", "PENDING", "LIMIT",
    "LIMITS", "SIGNAL", "SIGNALS", "RUNNING", "OPEN",
    "GOLD", "XAUUSD", "XAU", "USD",
    "BUY", "BUYS", "SELL", "SELLS", "LONG", "LONGS", "SHORT", "SHORTS",
    "TP", "TAKE", "PROFIT", "PROFITS", "SECURE", "BOOK", "HALF", "PARTIAL", "PARTIALS",
    "PARTIALLY", "FULL", "FULLY", "MARKET", "PRICE", "PIPS", "PIP", "POINTS", "PTS",
    "IN", "ON", "OF", "FOR", "WITH", "AND", "OR", "YOUR", "OUR", "MY",
    "GUYS", "PLEASE", "PLS", "EVERYONE", "TEAM", "MANUALLY", "EARLY", "QUICK", "QUICKLY",
    "IMMEDIATELY", "ASAP", "VIP", "UPDATE", "ALERT",
}

_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:\.[0-9]+)?")
_NUMBER_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_TP_RE = re.compile(r"^TP[0-9]{1,2}$")

# Telethon Message attributes, most specific first (a video is also a
# "document" to Telegram, so the specific kinds must be checked before it).
_MEDIA_ATTRS = ("video_note", "video", "voice", "audio", "gif", "sticker", "poll", "dice",
                "game", "geo", "venue", "contact", "document", "invoice")


def words_of(text: str) -> list:
    return _TOKEN_RE.findall(text.upper())


def _is_price(word: str) -> bool:
    """A price-shaped number: has decimals (1.0850, 2650.5) or is >= 100
    (2650) - so "3 reasons to buy gold" has no price."""
    return bool(_NUMBER_RE.match(word)) and ("." in word or float(word) >= 100.0)


def classify_message(text: str | None, max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
                     max_command_chars: int = DEFAULT_MAX_COMMAND_CHARS,
                     symbol_words: set | None = None) -> tuple[str, str]:
    """(SIGNAL | COMMAND | SKIP, reason). Never raises. `symbol_words`
    (uppercase) are the words that name the traded symbol - gold by
    default; any word starting with XAU also counts."""
    t = (text or "").strip()
    if not t:
        return SKIP, "empty (media without text, or a service message)"
    if max_message_chars > 0 and len(t) > max_message_chars:
        return SKIP, f"long message ({len(t)} chars > {max_message_chars})"
    words = words_of(t)
    if not words:
        return SKIP, "no words (emoji/sticker-style message)"
    wset = set(words)
    has_direction = bool(wset & DIRECTION_WORDS)
    symbols = symbol_words or GOLD_WORDS
    has_symbol = any(w in symbols or w.startswith("XAU") for w in words)
    has_label = bool(wset & SIGNAL_LABELS) or any(_TP_RE.match(w) for w in words)
    has_price = any(_is_price(w) for w in words)
    if has_direction and has_price and (has_symbol or has_label):
        return SIGNAL, "buy/sell + price + symbol or SL/TP"
    has_command = (bool(wset & COMMAND_WORDS) or ("BREAK" in wset and "EVEN" in wset)
                   or ("SL" in wset and ("MOVE" in wset or "ENTRY" in wset)))
    if has_command:
        if max_command_chars > 0 and len(t) > max_command_chars:
            return SKIP, f"command word inside a long message ({len(t)} chars > {max_command_chars})"
        stray = [w for w in words
                 if w not in COMMAND_VOCAB and not _NUMBER_RE.match(w) and not _TP_RE.match(w)]
        if stray:
            return SKIP, f"command word in ordinary chat (e.g. {stray[0].lower()!r})"
        return COMMAND, "short trading command"
    if has_direction:
        return SKIP, "mentions buy/sell without a price and a symbol or SL/TP"
    return SKIP, "not a trade message (greeting, mood, commentary, promo...)"


def message_media_kind(message, accept_photo_captions: bool = False) -> str | None:
    """None when `message` (a Telethon Message, duck-typed) is a plain text
    post that may be read; otherwise a short description of why it is
    omitted: service action (pin/join), video, voice, audio, sticker, ...
    A link preview is NOT media - only real attachments count."""
    if getattr(message, "action", None) is not None:
        return "service message (pin/join/title change)"
    for attr in _MEDIA_ATTRS:
        if getattr(message, attr, None):
            return attr.replace("_", " ")
    if getattr(message, "photo", None):
        if accept_photo_captions and (getattr(message, "message", "") or "").strip():
            return None
        return "photo" if not accept_photo_captions else "photo without caption"
    return None


def check_channel_limit(chats: list, what: str = "channels") -> None:
    """Raises ValueError when more than MAX_CHANNELS are configured."""
    if len(chats) > MAX_CHANNELS:
        raise ValueError(f"At most {MAX_CHANNELS} {what} are allowed, got {len(chats)}: {chats}")

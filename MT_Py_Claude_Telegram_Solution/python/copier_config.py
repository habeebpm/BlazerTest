"""
Configuration for the Telegram signal copier + verifier.

Kept separate from config.TradeConfig (the confluence bot's settings) because
the two tools size and gate trades very differently - the confluence bot
derives its own SL/TP from indicators, while the copier's SL/TP come from
someone else's message and therefore need a whole extra layer of sanity
checks (SignalVerifier) before anything is sent to the broker.

`symbol`, `magic`, `deviation_points` and `comment` are named the same as on
TradeConfig on purpose: mt5_client's connect(), get_positions(),
close_position() and open_signal_position() are written against those
attribute names so both configs can be passed to the same functions.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import message_filter
from signal_parser import DEFAULT_SYMBOL_ALIASES


@dataclass
class CopierConfig:
    symbol: str = "XAUUSD"
    symbol_aliases: dict = field(default_factory=lambda: dict(DEFAULT_SYMBOL_ALIASES))

    # ---------------- source ----------------
    # Chat ids or @usernames allowed to trigger trades - at most 3
    # (message_filter.MAX_CHANNELS). Empty = accept any source Telethon is
    # subscribed to - dry-run only; --live refuses to start that way.
    allowed_chats: list = field(default_factory=list)

    # ---------------- trade-only message filter (see message_filter.py) ----
    # Only trade messages are read: a signal (buy/sell + price + symbol or
    # SL/TP, at most max_message_chars) or a short command made only of
    # trading words (at most max_command_chars). Greetings, mood posts,
    # commentary, long messages, videos, audio, voice notes, stickers,
    # documents and service messages are omitted before parsing.
    trade_messages_only: bool = True
    max_message_chars: int = message_filter.DEFAULT_MAX_MESSAGE_CHARS
    max_command_chars: int = message_filter.DEFAULT_MAX_COMMAND_CHARS
    accept_photo_captions: bool = False   # True = a photo's caption may carry a signal

    # ---------------- sizing ----------------
    lots: float = 0.01
    use_risk_percent: bool = False
    risk_percent: float = 0.2
    max_lot_size: float = 5.0

    # ---------------- verification thresholds ----------------
    distance_unit: str = "pip"          # "point" | "pip" | "usd", see TradeConfig
    min_sl_units: float = 10.0          # reject a stop tighter than this
    max_sl_units: float = 150.0         # reject a stop wider than this (fat-finger guard)
    min_risk_reward: float = 0.0        # nearest TP / SL distance; 0 = not enforced
    max_price_deviation_units: float = 50.0   # market has moved too far from the signaled entry
    max_signal_age_seconds: float = 120.0     # reject a signal relayed/queued too long
    dedupe_window_seconds: float = 60.0       # suppress the same signal repeated in this window
    allow_missing_sl_fallback: bool = False   # reject signals with no SL unless this is set
    default_sl_units: float = 60.0            # used only when the fallback above is enabled
    max_open_positions: int = 4
    max_trades_per_day: int = 0        # 0 = unlimited

    # ---------------- post-trade verification ----------------
    # After a copy is sent, the fill is checked against what was requested;
    # a gap wider than this (in distance_unit units) is logged as a warning.
    post_trade_tolerance_units: float = 5.0

    # ---------------- execution ----------------
    magic: int = 20260920
    deviation_points: int = 30
    comment: str = "Telegram_Sig"

    # ---------------- connection (never hardcode credentials) ----------------
    login: int | None = None
    password: str | None = None
    server: str | None = None
    terminal_path: str | None = None

    # ---------------- telegram ----------------
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session: str = "tg_copier"

    def __post_init__(self):
        message_filter.check_channel_limit(self.allowed_chats, "signal channels")

    def symbol_words(self) -> set:
        """Words that name the traded symbol, for message_filter."""
        words = {self.symbol.upper()}
        for alias in self.symbol_aliases:
            words.update(message_filter.words_of(alias))
        return words

    def unit_size(self, point: float) -> float:
        if self.distance_unit == "point":
            return point
        if self.distance_unit == "pip":
            return point * 10.0
        if self.distance_unit == "usd":
            return 1.0
        raise ValueError(f"distance_unit must be point|pip|usd, got {self.distance_unit!r}")

    @classmethod
    def from_env(cls, **overrides) -> "CopierConfig":
        """Build config, taking credentials and MT5_*/TELEGRAM_* overrides from the env."""
        env_map = {
            "symbol": ("MT5_SYMBOL", str),
            "lots": ("TG_LOTS", float),
            "login": ("MT5_LOGIN", int),
            "password": ("MT5_PASSWORD", str),
            "server": ("MT5_SERVER", str),
            "terminal_path": ("MT5_PATH", str),
            "telegram_api_id": ("TELEGRAM_API_ID", int),
            "telegram_api_hash": ("TELEGRAM_API_HASH", str),
            "telegram_session": ("TELEGRAM_SESSION", str),
            "max_message_chars": ("TG_MAX_MESSAGE_CHARS", int),
            "max_command_chars": ("TG_MAX_COMMAND_CHARS", int),
        }
        kwargs: dict = {}
        for field_name, (env_name, caster) in env_map.items():
            raw = os.environ.get(env_name)
            if raw not in (None, ""):
                kwargs[field_name] = caster(raw)
        channels_raw = os.environ.get("TELEGRAM_CHANNELS")
        if channels_raw:
            kwargs["allowed_chats"] = [c.strip() for c in channels_raw.split(",") if c.strip()]
        kwargs.update(overrides)
        return cls(**kwargs)

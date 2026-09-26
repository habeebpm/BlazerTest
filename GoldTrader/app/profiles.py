"""
Instrument profiles: one program, one set of rules per market.

    python goldtrader.py start                   # gold (XAUUSD) - the default, unchanged
    python goldtrader.py start --profile btc     # Bitcoin (BTCUSD) - start_btc.bat

The BTC profile keeps gold's tested SHAPE - a stop of about one M15 ATR,
the profit locked at +1R, then trailed 0.5R behind - but sizes it from
BTC's own volatility instead of gold's fixed dollars, so it holds at any
BTC price: the stop is 1.0 x ATR14 on M15 (between 0.20% and 2.0% of the
price), the lock and trail are multiples of each trade's own stop
(BTCTrader_EA manages them: InpLockR / InpTrailR). Bitcoin trades around
the clock, so there are no trading hours or Friday cutoff; a spread limit
in % of price keeps it out of thin weekend / news spreads instead.

Everything else is the same brain: the 2-of-3 pre-screen, Claude's
"full" verdict, SMC structure, M15/H1 alignment, the economic calendar
blackout, the breaking-news check (crypto feeds), the ML advisor, the
daily cap and budget, the margin guard, the scorecard and the journal -
in their own files (logs\\btc\\, BTC_ journal files, own magic number and
pause switch), so the gold instance never sees BTC and the other way round.

Profile values are applied BEFORE any start-up option, so start_btc.bat can
still change one (e.g. --symbol BTCUSDm for a broker with a suffix).
"""
from __future__ import annotations

import os

import paths

BTC_MAGIC = 20260931

BTC_NEWS_FEEDS = [
    # Google News searches (Reuters, Bloomberg, CoinDesk, The Block, ...)
    "https://news.google.com/rss/search?q=bitcoin+OR+BTC+OR+crypto+OR+%22bitcoin+ETF%22+OR+SEC"
    "+OR+%22Federal+Reserve%22+OR+Powell+when:1d&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=war+OR+missile+OR+sanctions+OR+tariff+OR+ceasefire"
    "+OR+%22central+bank%22+OR+%22emergency%22+when:1d&hl=en-US&gl=US&ceid=US:en",
    # Crypto newsrooms
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    # CNBC - Economy
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
]

BTC_NEWS_KEYWORDS = [
    "bitcoin", "btc", "crypto", "etf", "sec", "binance", "coinbase", "stablecoin", "tether", "usdt",
    "liquidat", "hack", "exploit", "halving", "miner", "blackrock", "microstrategy", "strategy",
    "regulat", "ban", "lawsuit", "fed", "powell", "fomc", "rate", "inflation", "cpi", "payroll",
    "dollar", "treasury", "yield", "nasdaq", "stocks", "tariff", "sanction", "war", "missile",
    "strike", "attack", "ceasefire", "emergency", "crisis", "bank", "recession", "china",
]

PROFILES = {
    "gold": {},
    "btc": {
        "instrument": "btc",
        "symbol": "BTCUSD",
        "magic": BTC_MAGIC,
        "shared_cap_magic_numbers": [],
        # stop: 1 x M15 ATR14, 0.20%-2.0% of price; lock +1R, trail 0.5R
        "sl_mode": "atr",
        "sl_atr_mult": 1.0,
        "sl_atr_period": 14,
        "sl_atr_timeframe": "M15",
        "sl_pct_min": 0.20,
        "sl_pct_max": 2.0,
        "lock_mode": "r",
        "lock_r": 1.0,
        "trail_r": 0.5,
        # risk: 2% a trade like gold; its own 5% daily cap and 3 per direction,
        # so gold (10%) + BTC (5%) can never lose more than 15% in one day
        "risk_percent": 2.0,
        "max_daily_loss_pct": 5.0,
        "max_open_positions_per_direction": 3,
        # 24/7 market: no hours, no Friday cutoff; spread limit in % of price
        "trade_windows": "",
        "trade_days": "Mon-Sun",
        "friday_cutoff_ny": "",
        "max_spread_points": 0,
        "max_spread_pct": 0.06,
        "news_feeds": BTC_NEWS_FEEDS,
        "news_keywords": BTC_NEWS_KEYWORDS,
        # its own files and switches
        "claude_pause_filename": "claudesmc_btc_pause.txt",
        "last_verdict_filename": "claudesmc_btc_last_verdict.txt",
        "journal_prefix": "BTC_",
        "run_companions": False,
    },
}


def names() -> list:
    return list(PROFILES)


def apply(cfg, name: str):
    """Sets the profile's values on `cfg` (an AdvisorConfig) and returns it.
    Raises ValueError on an unknown profile."""
    key = (name or "gold").strip().lower()
    if key not in PROFILES:
        raise ValueError(f"unknown profile {name!r} - use one of: {', '.join(PROFILES)}")
    for field_name, value in PROFILES[key].items():
        if not hasattr(cfg, field_name):
            raise AttributeError(f"profile {key}: AdvisorConfig has no field {field_name!r}")
        setattr(cfg, field_name, list(value) if isinstance(value, list) else value)
    if key != "gold":
        cfg.log_dir = os.path.join(paths.LOG_DIR, key)
    return cfg

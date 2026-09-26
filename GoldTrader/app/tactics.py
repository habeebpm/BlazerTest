"""
Entry tactics - WHEN a Claude entry may be taken. Entry filters only: they
never touch the lot, the stop-loss, TP1, the trail or the position cap.

Four checks, each switchable in config.py (see docs/BACKTEST_REPORT.md for
the one-year test behind the defaults):

  * trading hours  - entries only inside trade_windows on trade_days, in
                     trade_timezone (default 06:00-23:00 Oman time, Monday
                     to Friday - UnifiedTrader_EA applies the same window to
                     Telegram entries). Any IANA time zone works, daylight
                     saving included.
  * Friday cutoff  - no new entry after friday_cutoff_ny (New York time): a
                     $6 stop cannot protect a position held over the weekend
                     gap.
  * spread guard   - no entry while the spread is above max_spread_points
                     (daily reopen, news spikes, thin liquidity). With a $6
                     stop, a 50-point spread is already 8% of the risk.
  * trend strength - no entry while M15 ADX14 < min_adx: continuation
                     entries with a fixed stop need a trending market.

The time and spread checks run before the market snapshot and the paid
Claude call (executor.verdict_independent_block), the ADX check right after
the snapshot, still before the Claude call - a blocked bar costs nothing.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")      # gold's own clock: daily break, trading day, Friday cutoff

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ZONE_LABELS = {"Asia/Muscat": "Oman", "America/New_York": "New York", "Europe/London": "London",
               "Asia/Dubai": "UAE", "UTC": "UTC"}


def zone(cfg) -> ZoneInfo:
    try:
        return ZoneInfo((cfg.trade_timezone or "UTC").strip())
    except Exception:
        raise ValueError(f"unknown time zone {cfg.trade_timezone!r} (use e.g. Asia/Muscat)") from None


def zone_label(cfg) -> str:
    """"Oman" for Asia/Muscat - how the hours are written for people."""
    name = (cfg.trade_timezone or "UTC").strip()
    return ZONE_LABELS.get(name, name)


def parse_days(spec: str) -> set:
    """"Mon-Fri" -> {0..4}; "Mon,Wed,Fri"; "" / "any" -> every day (0=Monday).
    Raises ValueError on a typo, so it is caught at start-up."""
    spec = (spec or "").strip().lower()
    if spec in ("", "any", "all"):
        return set(range(7))
    days = set()
    for part in spec.split(","):
        part = part.strip()
        ends = [p.strip()[:3] for p in part.split("-")]
        if len(ends) > 2 or any(e not in DAY_NAMES for e in ends):
            raise ValueError(f"trading days not understood: {spec!r} (use e.g. Mon-Fri)")
        a, b = DAY_NAMES.index(ends[0]), DAY_NAMES.index(ends[-1])
        days.update(range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(0, b + 1)))
    return days


def _minutes(hhmm: str) -> int:
    try:
        h, m = (int(x) for x in hhmm.strip().split(":"))
    except ValueError:
        raise ValueError(f"not a time of day: {hhmm.strip()!r} (use HH:MM, e.g. 08:00)") from None
    if not (0 <= h <= 24 and 0 <= m < 60) or h * 60 + m > 24 * 60:
        raise ValueError(f"not a time of day: {hhmm.strip()!r} (use HH:MM, e.g. 08:00)")
    return h * 60 + m


def parse_windows(spec: str) -> list:
    """"08:00-16:45,18:15-20:00" -> [(480, 1005), (1095, 1200)] (minutes
    after midnight). "" = no restriction. A window may wrap midnight
    ("20:00-02:00"). Raises ValueError on a malformed spec, so a typo is
    caught at start-up rather than silently allowing every hour."""
    windows = []
    for part in (spec or "").split(","):
        if not part.strip():
            continue
        if "-" not in part:
            raise ValueError(f"trading window needs start-end, got {part!r}")
        start, end = part.split("-", 1)
        windows.append((_minutes(start), _minutes(end)))
    return windows


def _as_utc(now) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    ts = now.to_pydatetime()  # pandas Timestamp
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def in_windows(minute_of_day: int, windows: list) -> bool:
    for start, end in windows:
        if start <= end:
            if start <= minute_of_day < end:
                return True
        elif minute_of_day >= start or minute_of_day < end:
            return True
    return False


def time_block(cfg, now) -> str:
    """Trading days/hours (trade_timezone) and Friday-cutoff (New York)
    check for `now` (UTC)."""
    utc = _as_utc(now)
    ny = utc.astimezone(NEW_YORK)
    cutoff = (cfg.friday_cutoff_ny or "").strip()
    if cutoff and ny.weekday() == 4 and ny.hour * 60 + ny.minute >= _minutes(cutoff):
        return f"Friday after {cutoff} New York - no new entry before the weekend"
    local, label = utc.astimezone(zone(cfg)), zone_label(cfg)
    if local.weekday() not in parse_days(cfg.trade_days):
        return f"not a trading day ({local:%A} {label} time; entries only {cfg.trade_days})"
    windows = parse_windows(cfg.trade_windows)
    if windows and not in_windows(local.hour * 60 + local.minute, windows):
        return (f"outside trading hours ({local:%H:%M} {label} time; entries only "
                f"{cfg.trade_windows} {label} time)")
    return ""


def spread_block(cfg, spread_points: float) -> str:
    if cfg.max_spread_points and spread_points > cfg.max_spread_points:
        return f"spread {spread_points:.0f} points is above the {cfg.max_spread_points}-point limit"
    return ""


def current_spread_pct(gateway, cfg) -> float | None:
    """Live spread as % of the mid price (BTC's spread limit); None when unavailable."""
    try:
        tick = gateway.get_tick(cfg.symbol)
        mid = (tick.ask + tick.bid) / 2.0
        return (tick.ask - tick.bid) / mid * 100.0 if mid > 0 else None
    except Exception:
        return None


def current_spread_points(gateway, cfg, spec=None) -> float | None:
    """Live spread in points from the current tick; None when unavailable."""
    try:
        spec = spec or gateway.symbol_spec(cfg.symbol)
        tick = gateway.get_tick(cfg.symbol)
        if not spec.point:
            return None
        return (tick.ask - tick.bid) / spec.point
    except Exception:
        return None


def regime_block(cfg, features: dict) -> str:
    """Trend-strength check on the market snapshot (before the Claude call)."""
    if not cfg.min_adx:
        return ""
    adx = (features.get("primary_indicators") or {}).get("adx14")
    if adx is not None and adx < cfg.min_adx:
        return f"no trend: {cfg.primary_timeframe} ADX14 {adx:.1f} is below {cfg.min_adx:g}"
    return ""


def vote_legs(features: dict) -> dict:
    """The three confluence legs exactly as claude_advisor.SYSTEM_PROMPT defines
    them, on the snapshot's own numbers: {"trend"|"momentum"|"strength":
    (direction "buy"/"sell"/"neutral", confirmed)}. Shared by the Claude
    pre-screen and backtest.mechanical_verdict, so the two never drift."""
    ind = features["primary_indicators"]
    trend_bias = features["trend_bias"]
    trend_up = trend_bias["close"] > trend_bias["ema200"] and ind["ema20"] > ind["ema50"]
    trend_down = trend_bias["close"] < trend_bias["ema200"] and ind["ema20"] < ind["ema50"]
    trend_confirmed = abs(ind["ema20"] - ind["ema50"]) >= 0.25 * ind["atr14"]
    momentum_up = ind["macd_line"] > ind["macd_signal"] and 50 <= ind["rsi14"] <= 70
    momentum_down = ind["macd_line"] < ind["macd_signal"] and 30 <= ind["rsi14"] <= 50
    momentum_confirmed = ((ind["macd_hist"] > ind["macd_hist_prev"] and ind["rsi14"] >= 55) or
                          (ind["macd_hist"] < ind["macd_hist_prev"] and ind["rsi14"] <= 45))
    strength_up = ind["adx14"] >= 22 and ind["plus_di"] > ind["minus_di"]
    strength_down = ind["adx14"] >= 22 and ind["minus_di"] > ind["plus_di"]
    strength_confirmed = ind["adx14"] >= 28 and abs(ind["plus_di"] - ind["minus_di"]) >= 8

    def side(up, down):
        return "buy" if up else "sell" if down else "neutral"
    return {"trend": (side(trend_up, trend_down), trend_confirmed),
            "momentum": (side(momentum_up, momentum_down), momentum_confirmed),
            "strength": (side(strength_up, strength_down), strength_confirmed)}


def prescreen_block(cfg, features: dict) -> str:
    """Before the paid Claude call: "" if the legs could still reach the
    minimum an entry needs (cfg.min_confluence_count legs agreeing, one of
    them confirmed when full conviction is required), else why not - the
    rules forbid a trade then whatever Claude answers, so the call is saved."""
    if not getattr(cfg, "claude_prescreen", False):
        return ""
    try:
        legs = vote_legs(features).values()
    except (KeyError, TypeError):
        return ""          # numbers missing - never block on that, let Claude judge
    need_confirmed = 1 if cfg.require_full_conviction else 0
    for d in ("buy", "sell"):
        agree = [c for side, c in legs if side == d]
        if len(agree) >= cfg.min_confluence_count and sum(agree) >= need_confirmed:
            return ""
    return (f"pre-screen: fewer than {cfg.min_confluence_count} agreeing legs"
            + (" with one confirmed" if need_confirmed else "") + " - no trade possible, Claude not asked")


def validate(cfg) -> None:
    """Raises ValueError on a malformed time setting."""
    parse_windows(cfg.trade_windows)
    parse_days(cfg.trade_days)
    zone(cfg)
    if (cfg.friday_cutoff_ny or "").strip():
        _minutes(cfg.friday_cutoff_ny)


def windows_in(cfg, display_zone: str, now) -> str:
    """cfg.trade_windows as clock times in display_zone on `now`'s date, e.g.
    "16:00-00:45, 02:15-04:00" for New York hours shown in Oman time in
    summer (they shift an hour when New York changes its clocks)."""
    windows = parse_windows(cfg.trade_windows)
    if not windows:
        return "any hour"
    src, dst = zone(cfg), ZoneInfo(display_zone)
    day = _as_utc(now).astimezone(src).date()
    out = []
    for start, end in windows:
        a = datetime(day.year, day.month, day.day, tzinfo=src) + timedelta(minutes=start)
        b = datetime(day.year, day.month, day.day, tzinfo=src) + timedelta(minutes=end)
        out.append(f"{a.astimezone(dst):%H:%M}-{b.astimezone(dst):%H:%M}")
    return ", ".join(out)


def trading_day(now, server_offset_seconds: int | None = None):
    """The trading day `now` (UTC) belongs to - the day the EA's daily cap
    uses (broker server midnight). With the broker's offset known that is
    exact; without it, gold's own day is used, which rolls at 17:00 New
    York - the same moment as server midnight at the usual GMT+2/+3
    (New York close) gold brokers."""
    utc = _as_utc(now)
    if server_offset_seconds is not None:
        return (utc + timedelta(seconds=server_offset_seconds)).date()
    return (utc.astimezone(NEW_YORK) + timedelta(hours=7)).date()


GOLD_CLOSE_NY, GOLD_REOPEN_NY = 17 * 60, 18 * 60     # Friday close / Sunday reopen, New York


def gold_market_closed(now) -> bool:
    """True from gold's Friday 17:00 close to its Sunday 18:00 reopen (New
    York; Saturday 01:00 - Monday 02:00 Oman time)."""
    ny = _as_utc(now).astimezone(NEW_YORK)
    minute, day = ny.hour * 60 + ny.minute, ny.weekday()
    return (day == 4 and minute >= GOLD_CLOSE_NY) or day == 5 or (day == 6 and minute < GOLD_REOPEN_NY)


def gold_reopen_after(now) -> datetime:
    """The next Sunday 18:00 New York (gold's weekly reopen) after `now`, in UTC."""
    ny = _as_utc(now).astimezone(NEW_YORK)
    days = (6 - ny.weekday()) % 7
    reopen = (ny + timedelta(days=days)).replace(hour=GOLD_REOPEN_NY // 60, minute=GOLD_REOPEN_NY % 60,
                                                 second=0, microsecond=0)
    if reopen <= ny:
        reopen = reopen + timedelta(days=7)
    return reopen.astimezone(timezone.utc)


def market_closed_for(cfg, now) -> bool:
    """The gold instance has nothing to poll while gold's market is closed
    for the weekend; Bitcoin never closes."""
    return getattr(cfg, "instrument", "gold") == "gold" and gold_market_closed(now)


def weekend_mode(cfg, now) -> bool:
    """The BTC weekend allowance is in force: gold is closed and the profile
    sets a weekend daily cap or position limit."""
    return bool((cfg.weekend_max_daily_loss_pct > 0 or cfg.weekend_max_positions_per_direction > 0)
                and gold_market_closed(now))


def effective_limits(cfg, now):
    """`cfg` with the weekend daily cap and positions per direction applied
    while gold is closed (BTC: the allowance gold uses on weekdays - the
    account trades only Bitcoin then); `cfg` itself otherwise. Risk per
    trade, stop, lock and trail never change."""
    if not weekend_mode(cfg, now):
        return cfg
    changes = {}
    if cfg.weekend_max_daily_loss_pct > 0:
        changes["max_daily_loss_pct"] = cfg.weekend_max_daily_loss_pct
    if cfg.weekend_max_positions_per_direction > 0:
        changes["max_open_positions_per_direction"] = cfg.weekend_max_positions_per_direction
    return dataclasses.replace(cfg, **changes)


def describe(cfg) -> str:
    """One line for the start-up log."""
    parts = [f"hours {cfg.trade_windows} {zone_label(cfg)} time" if cfg.trade_windows else "any hour"]
    if parse_days(cfg.trade_days) != set(range(7)):
        parts.append(cfg.trade_days)
    if cfg.friday_cutoff_ny:
        parts.append(f"Friday cutoff {cfg.friday_cutoff_ny}")
    if cfg.max_spread_points:
        parts.append(f"spread <= {cfg.max_spread_points} points")
    if getattr(cfg, "max_spread_pct", 0):
        parts.append(f"spread <= {cfg.max_spread_pct:g}% of price")
    if cfg.min_adx:
        parts.append(f"ADX >= {cfg.min_adx:g}")
    if cfg.weekend_max_daily_loss_pct > 0 or cfg.weekend_max_positions_per_direction > 0:
        parts.append(f"while gold is closed: {cfg.weekend_max_daily_loss_pct or cfg.max_daily_loss_pct:g}% "
                     f"daily cap, {cfg.weekend_max_positions_per_direction or cfg.max_open_positions_per_direction}"
                     f" per direction")
    return ", ".join(parts)

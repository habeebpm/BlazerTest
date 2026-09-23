"""
Entry tactics - WHEN a Claude entry may be taken. Entry filters only: they
never touch the lot, the stop-loss, TP1, the trail or the position cap.

Four checks, each switchable in config.py (see docs/BACKTEST_REPORT.md for
the one-year test behind the defaults):

  * trading hours  - entries only inside trade_windows_ny (New York time, so
                     US/UK daylight-saving shifts are followed automatically).
                     Gold's Asian session is a range that turns momentum
                     entries into false breakouts, and the London morning
                     sweeps tight stops; both lost over the tested year.
  * Friday cutoff  - no new entry after friday_cutoff_ny: a $6 stop cannot
                     protect a position held over the weekend gap.
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

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


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
    """Trading-hours and Friday-cutoff check for `now` (UTC)."""
    ny = _as_utc(now).astimezone(NEW_YORK)
    minute = ny.hour * 60 + ny.minute
    cutoff = (cfg.friday_cutoff_ny or "").strip()
    if cutoff and ny.weekday() == 4 and minute >= _minutes(cutoff):
        return f"Friday after {cutoff} New York - no new entry before the weekend"
    windows = parse_windows(cfg.trade_windows_ny)
    if windows and not in_windows(minute, windows):
        return (f"outside trading hours ({ny:%H:%M} New York; entries only "
                f"{cfg.trade_windows_ny} New York)")
    return ""


def spread_block(cfg, spread_points: float) -> str:
    if cfg.max_spread_points and spread_points > cfg.max_spread_points:
        return f"spread {spread_points:.0f} points is above the {cfg.max_spread_points}-point limit"
    return ""


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


def validate(cfg) -> None:
    """Raises ValueError on a malformed time setting."""
    parse_windows(cfg.trade_windows_ny)
    if (cfg.friday_cutoff_ny or "").strip():
        _minutes(cfg.friday_cutoff_ny)


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


def describe(cfg) -> str:
    """One line for the start-up log."""
    parts = [f"hours {cfg.trade_windows_ny} New York" if cfg.trade_windows_ny else "any hour"]
    if cfg.friday_cutoff_ny:
        parts.append(f"Friday cutoff {cfg.friday_cutoff_ny}")
    if cfg.max_spread_points:
        parts.append(f"spread <= {cfg.max_spread_points} points")
    if cfg.min_adx:
        parts.append(f"ADX >= {cfg.min_adx:g}")
    return ", ".join(parts)

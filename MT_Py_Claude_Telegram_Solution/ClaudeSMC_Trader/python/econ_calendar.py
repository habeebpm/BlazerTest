"""
Economic calendar for the Claude side - reads the CSV that the MQL5 EAs
(UnifiedTrader_EA.mq5 / ClaudeSMC_TradeManager.mq5, via
../../MQL5/Include/EconCalendar.mqh) export from MT5's built-in calendar into
the shared Common\\Files folder. No API key, no extra data provider: the
Python MetaTrader5 package has no calendar access of its own, so the EA does
the reading and hands it over, the same way the Why button's verdict file
works in the other direction.

Two uses:
  - blackout_reason(): executor.gate() refuses new entries within
    news_block_before/after_minutes of an event at least
    news_min_importance for news_currencies (on by default).
  - calendar_context(): what Claude sees - upcoming events and recent
    releases with actual vs forecast and what the surprise usually means
    for gold (USD-positive = bearish for gold, USD-negative = bullish).

Everything degrades to "no calendar" (no blackout, null context) when the
file doesn't exist yet, can't be read, or the gateway has no
read_common_file (backtest.HistoricalGateway, test fakes) - never raises.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from config import AdvisorConfig

log = logging.getLogger(__name__)

IMPORTANCE_RANK = {"none": 0, "low": 1, "moderate": 2, "high": 3}
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class EconEvent:
    time_utc: datetime
    currency: str
    importance: str
    event: str
    actual: float | None
    forecast: float | None
    previous: float | None
    impact: str   # effect of actual vs forecast on the currency: positive / negative / na


def _num(text: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_calendar_csv(text: str) -> tuple[list[EconEvent], datetime | None]:
    """(events oldest first, exported_at_utc or None). Malformed rows are
    skipped, never raised on - one bad row must not blank the calendar."""
    exported_at = None
    data_lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            if "exported_at_utc=" in line:
                try:
                    exported_at = datetime.strptime(line.split("=", 1)[1].strip(),
                                                    _TIME_FORMAT).replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
            continue
        data_lines.append(line)
    events = []
    for row in csv.DictReader(io.StringIO("\n".join(data_lines))):
        try:
            events.append(EconEvent(
                time_utc=datetime.strptime(row["time_utc"].strip(), _TIME_FORMAT).replace(tzinfo=timezone.utc),
                currency=row["currency"].strip().upper(),
                importance=row["importance"].strip().lower(),
                event=row["event"].strip(),
                actual=_num(row.get("actual")),
                forecast=_num(row.get("forecast")),
                previous=_num(row.get("previous")),
                impact=(row.get("impact") or "na").strip().lower(),
            ))
        except (KeyError, ValueError, AttributeError):
            continue
    events.sort(key=lambda e: e.time_utc)
    return events, exported_at


def load_events(gateway, cfg: AdvisorConfig) -> tuple[list[EconEvent], datetime | None] | None:
    """The exported calendar, or None when there isn't one to use (feature
    off, gateway can't read shared files, file missing or unreadable)."""
    if not cfg.econ_calendar_filename:
        return None
    reader = getattr(gateway, "read_common_file", None)
    if reader is None:
        return None
    try:
        text = reader(cfg.econ_calendar_filename)
    except Exception as exc:
        log.warning("Could not read the economic calendar file %r (%s) - continuing without it.",
                    cfg.econ_calendar_filename, exc)
        return None
    if text is None:
        return None
    return parse_calendar_csv(text)


def _watched(event: EconEvent, cfg: AdvisorConfig) -> bool:
    return event.currency in {c.upper() for c in cfg.news_currencies}


def gold_impact(event: EconEvent) -> str:
    """Gold is priced in USD: a USD-positive surprise usually weighs on gold,
    a USD-negative one usually supports it. Only USD is mapped."""
    if event.currency != "USD":
        return ""
    if event.impact == "positive":
        return "USD-positive surprise: usually bearish for gold"
    if event.impact == "negative":
        return "USD-negative surprise: usually bullish for gold"
    return ""


def min_importance_rank(cfg: AdvisorConfig) -> int:
    """news_min_importance as a rank - a typo raises instead of silently
    meaning something else, like every other invalid setting here."""
    rank = IMPORTANCE_RANK.get(str(cfg.news_min_importance).lower())
    if rank is None or rank == 0:
        raise ValueError(f"Unrecognized news_min_importance {cfg.news_min_importance!r} - must be "
                         "'low', 'moderate' or 'high'.")
    return rank


def blackout_reason(events: list[EconEvent], now: datetime, cfg: AdvisorConfig) -> str:
    """"" or why a new entry right now is inside a news blackout."""
    if not cfg.news_auto_blackout:
        return ""
    min_rank = min_importance_rank(cfg)
    before = timedelta(minutes=cfg.news_block_before_minutes)
    after = timedelta(minutes=cfg.news_block_after_minutes)
    for e in events:
        if not _watched(e, cfg) or IMPORTANCE_RANK.get(e.importance, 0) < min_rank:
            continue
        if e.time_utc - before <= now <= e.time_utc + after:
            return (f"news blackout: {e.currency} {e.event} ({e.importance} impact) at "
                    f"{e.time_utc.strftime('%H:%M')} UTC")
    return ""


def calendar_context(events: list[EconEvent], exported_at: datetime | None, now: datetime,
                     cfg: AdvisorConfig) -> dict:
    """What Claude sees: moderate+ events for the watched currencies,
    released in the last 12h (with the surprise and its usual gold impact)
    and upcoming in the next 24h, plus the minutes to the next event that
    would trigger the blackout."""
    recent, upcoming = [], []
    next_blocking = None
    min_rank = min_importance_rank(cfg)
    for e in events:
        rank = IMPORTANCE_RANK.get(e.importance, 0)
        if not _watched(e, cfg) or rank < 2:
            continue
        minutes = round((e.time_utc - now).total_seconds() / 60.0)
        if e.time_utc <= now:
            if e.actual is None or minutes < -12 * 60:
                continue
            item = {"time_utc": e.time_utc.strftime("%Y-%m-%d %H:%M"), "currency": e.currency,
                    "event": e.event, "importance": e.importance, "minutes_ago": -minutes,
                    "actual": e.actual, "forecast": e.forecast, "previous": e.previous}
            if e.forecast is not None:
                item["surprise"] = round(e.actual - e.forecast, 6)
            impact = gold_impact(e)
            if impact:
                item["gold_impact"] = impact
            recent.append(item)
        elif minutes <= 24 * 60:
            upcoming.append({"time_utc": e.time_utc.strftime("%Y-%m-%d %H:%M"),
                             "currency": e.currency, "event": e.event,
                             "importance": e.importance, "minutes_until": minutes,
                             "forecast": e.forecast, "previous": e.previous})
            if rank >= min_rank and next_blocking is None:
                next_blocking = minutes
    context = {
        "recent_releases": recent[-8:],
        "upcoming_24h": upcoming[:8],
        "minutes_to_next_blackout_event": next_blocking,
        "blackout_window_minutes": [cfg.news_block_before_minutes, cfg.news_block_after_minutes],
    }
    if exported_at is not None:
        context["data_age_minutes"] = round((now - exported_at).total_seconds() / 60.0)
    return context

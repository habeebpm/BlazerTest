"""
Pre-trade breaking-news check: right before a full-conviction entry is sent,
look for SURPRISE, UNSCHEDULED news (a military strike, an emergency Fed
move, a tariff shock, a bank failure, ...) that could move gold or the US
dollar hard, and refuse the entry if such news points against it.

Scheduled releases (NFP, CPI, FOMC) are already handled by the economic
calendar blackout (econ_calendar.py) - this covers what no calendar lists.

Two sources, both optional, combined into ONE Claude call:
  - Claude's server-side web search tool (news_check_web_search): Claude
    searches the live web for the latest gold/dollar/Fed/geopolitical news.
  - Free RSS headlines (news_feeds, Google News search feeds by default),
    filtered to the last news_check_lookback_minutes and to news_keywords.
If web search is refused (not enabled for the API organization, or any
other error), the call is retried once with the headlines alone.

Cost: this only runs for a verdict that already cleared every other gate
(full conviction, confluence, position cap, daily budget) - a few times a
day at most - never on ordinary 15-minute cycles.

If no check can be made at all (Claude unreachable, no headlines and no web
search), news_check_fail_closed decides: False (default) trades anyway and
says so in the log and the Telegram alert; True refuses the entry.

Everything takes its client/fetcher as a parameter (same dependency
injection as claude_advisor.get_verdict()) so selftest.py runs it offline.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from config import AdvisorConfig

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are the risk officer of an automated XAUUSD (gold) trading system. An \
entry is about to be sent; the details are in the user message. Your ONLY \
job is to look for SURPRISE, UNSCHEDULED breaking news from roughly the \
last {lookback} minutes that could move gold or the US dollar sharply, and \
say whether it makes this entry unsafe.

What counts: geopolitical shocks (military strikes, war escalation, a \
ceasefire or peace deal), unscheduled central-bank action or remarks \
(emergency rate moves, surprise Fed/Powell comments, FX intervention, a \
central bank buying or selling gold in size), tariff or sanctions \
announcements, a bank or financial-system failure, a US political or \
fiscal shock, a market-wide crash or trading halt. Scheduled economic \
releases (NFP, CPI, FOMC at its scheduled time) are filtered separately - \
mention one only if its result was a big surprise.

If the web_search tool is available, use it for the latest news on gold, \
the US dollar, the Fed and major geopolitical events, and prefer results \
from the last few hours. Also read the RSS headlines provided (each with \
minutes_ago). Ignore stories older than the window that the market has \
already absorbed, opinion pieces, price recaps and routine commentary. \
Never invent news: if you find nothing specific, say so.

Usual direction of impact: gold tends to RISE on risk-off/safe-haven shocks, \
a weaker dollar, dovish surprises and falling yields; it tends to FALL on a \
stronger dollar, hawkish surprises, rising yields and de-escalation that \
removes safe-haven demand.

Set block_trade to true when a FRESH (about the last 60 minutes) medium or \
high severity surprise points AGAINST this entry's direction, or when a \
fresh high severity shock makes the next minutes too violent to trade \
either way. Otherwise false.

Finish your answer with ONLY this JSON object (no markdown fences):
{{"surprise_news": true|false, "severity": "none"|"low"|"medium"|"high", \
"impact_on_trade": "supports"|"against"|"neutral"|"unclear", \
"block_trade": true|false, "headline": "<the single most important \
story, or empty>", "summary": "<one or two sentences a trader can act on>"}}
"""


class NewsVerdict(BaseModel):
    surprise_news: bool
    severity: Literal["none", "low", "medium", "high"]
    impact_on_trade: Literal["supports", "against", "neutral", "unclear"]
    block_trade: bool
    headline: str = ""
    summary: str = ""


@dataclass
class Headline:
    title: str
    source: str = ""
    published_utc: datetime | None = None


@dataclass
class NewsCheckResult:
    ran: bool                      # a verdict was actually obtained
    block_reason: str = ""         # "" = clear to trade
    note: str = ""                 # one line for the log and the Telegram alert
    used_web_search: bool = False
    headline_count: int = 0
    verdict: NewsVerdict | None = None
    errors: list = field(default_factory=list)


# ---------------------------------------------------------------- headlines

def _http_get(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ClaudeSMC_Trader"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)          # RSS 2.0: RFC 822
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))  # Atom: ISO 8601
        except ValueError:
            return None
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def parse_feed(xml_text: str) -> list[Headline]:
    """RSS 2.0 <item> or Atom <entry> elements -> Headlines. Unparseable
    XML returns [] rather than raising."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title, source, when = "", "", None
        for child in el:
            name = _local(child.tag)
            if name == "title":
                title = (child.text or "").strip()
            elif name == "source":
                source = (child.text or "").strip()
            elif name in ("pubDate", "published", "updated") and when is None:
                when = _parse_time(child.text)
        if title:
            out.append(Headline(title=title, source=source, published_utc=when))
    return out


def fetch_headlines(cfg: AdvisorConfig, now: datetime, fetcher=_http_get,
                    errors: list | None = None) -> list[Headline]:
    """Recent, relevant headlines from every feed, newest first, deduplicated.
    Never raises - a dead feed is recorded in `errors` and skipped."""
    cutoff = now - timedelta(minutes=cfg.news_check_lookback_minutes)
    keywords = [k.lower() for k in cfg.news_keywords]
    seen, result = set(), []
    for url in cfg.news_feeds:
        try:
            items = parse_feed(fetcher(url))
        except Exception as exc:
            if errors is not None:
                errors.append(f"feed {url[:60]}: {exc}")
            log.info("News feed unavailable (%s): %s", url[:80], exc)
            continue
        for h in items:
            if h.published_utc is None or h.published_utc < cutoff or h.published_utc > now + timedelta(minutes=5):
                continue
            low = h.title.lower()
            if keywords and not any(re.search(r"\b" + re.escape(k), low) for k in keywords):
                continue
            key = re.sub(r"\W+", " ", low).strip()
            if key in seen:
                continue
            seen.add(key)
            result.append(h)
    result.sort(key=lambda h: h.published_utc, reverse=True)
    return result[:cfg.news_max_headlines]


# ------------------------------------------------------------ Claude call

def extract_json_object(text: str) -> dict | None:
    """The LAST JSON object in `text` that has a block_trade key - Claude
    writes its reasoning (and citations) first and the verdict last."""
    decoder = json.JSONDecoder()
    found = None
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and "block_trade" in obj:
            found = obj
    return found


def _response_text(response) -> str:
    return "".join(getattr(b, "text", "") or "" for b in getattr(response, "content", [])
                   if getattr(b, "type", "") == "text")


def _web_searches_used(response) -> int:
    usage = getattr(response, "usage", None)
    stu = getattr(usage, "server_tool_use", None) if usage is not None else None
    n = getattr(stu, "web_search_requests", None) if stu is not None else None
    if isinstance(n, int):
        return n
    return sum(1 for b in getattr(response, "content", [])
               if getattr(b, "type", "") == "server_tool_use")


def _ask_claude(client, cfg: AdvisorConfig, user_text: str, web_search: bool):
    """One Claude request (continuing up to 3 times on pause_turn, which a
    long server-side web-search turn can return). Returns (text, searches)."""
    kwargs = dict(
        model=cfg.news_check_model or cfg.claude_model,
        max_tokens=cfg.news_check_max_tokens,
        system=SYSTEM_PROMPT.format(lookback=cfg.news_check_lookback_minutes),
    )
    if web_search:
        kwargs["tools"] = [{"type": cfg.news_web_search_tool, "name": "web_search",
                            "max_uses": cfg.news_web_search_max_uses}]
    messages = [{"role": "user", "content": user_text}]
    text, searches = "", 0
    for _ in range(4):
        response = client.messages.create(messages=messages, **kwargs)
        text += _response_text(response)
        searches += _web_searches_used(response)
        if getattr(response, "stop_reason", "") != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": response.content}]
    return text, searches


def _decide(v: NewsVerdict) -> str:
    against = v.impact_on_trade in ("against", "unclear")
    if v.block_trade or (v.surprise_news and v.severity == "high" and against):
        what = v.headline or v.summary or "surprise news"
        return f"breaking news ({v.severity}, {v.impact_on_trade}): {what}"
    return ""


def check_before_trade(client, cfg: AdvisorConfig, direction: str, price: float,
                       now: datetime | None = None, fetcher=_http_get) -> NewsCheckResult:
    """Never raises. Returns a NewsCheckResult whose block_reason is "" when
    the entry may go ahead."""
    if not cfg.breaking_news_check:
        return NewsCheckResult(ran=False, note="news check off")
    now = now or datetime.now(timezone.utc)
    errors: list = []
    headlines = fetch_headlines(cfg, now, fetcher, errors) if cfg.news_feeds else []
    payload = {
        "symbol": cfg.symbol,
        "entry_direction": direction,
        "entry_price": round(price, 2),
        "now_utc": now.strftime("%Y-%m-%d %H:%M"),
        "lookback_minutes": cfg.news_check_lookback_minutes,
        "rss_headlines": [
            {"minutes_ago": int((now - h.published_utc).total_seconds() // 60),
             "title": h.title, "source": h.source}
            for h in headlines
        ],
    }
    user_text = json.dumps(payload, indent=1)

    text, searches, used_web = "", 0, False
    attempts = [True, False] if cfg.news_check_web_search else [False]
    for web in attempts:
        if not web and not headlines:
            errors.append("no headlines to judge without web search")
            break
        try:
            text, searches = _ask_claude(client, cfg, user_text, web)
            used_web = web and searches > 0
            break
        except Exception as exc:
            errors.append(f"{'web search' if web else 'headline-only'} call failed: {exc}")
            log.warning("Breaking-news check %s call failed: %s",
                        "web-search" if web else "headline-only", exc)

    verdict = None
    obj = extract_json_object(text) if text else None
    if obj is not None:
        try:
            verdict = NewsVerdict.model_validate(obj)
        except ValidationError as exc:
            errors.append(f"unreadable verdict: {exc.errors()[0].get('msg', exc)}")
    elif text:
        errors.append("no verdict JSON in Claude's answer")

    parts = []
    if used_web:
        parts.append(f"{searches} web search{'es' if searches != 1 else ''}")
    if headlines:
        parts.append(f"{len(headlines)} headlines")
    sources = " + ".join(parts)
    if verdict is None or (not used_web and not headlines):
        why = "; ".join(errors[-2:]) or "no news source available"
        if cfg.news_check_fail_closed:
            return NewsCheckResult(ran=False, block_reason=f"breaking-news check unavailable ({why})",
                                   note=f"UNAVAILABLE - entry refused ({why})",
                                   used_web_search=used_web, headline_count=len(headlines),
                                   errors=errors)
        return NewsCheckResult(ran=False, note=f"unavailable - traded without it ({why})",
                               used_web_search=used_web, headline_count=len(headlines),
                               errors=errors)

    block = _decide(verdict)
    if block:
        note = f"BLOCKED - {verdict.summary or verdict.headline} [{sources}]"
    elif verdict.surprise_news:
        note = (f"{verdict.severity} surprise, {verdict.impact_on_trade} this trade: "
                f"{verdict.summary or verdict.headline} [{sources}]")
    else:
        note = f"clear - no surprise news [{sources}]"
    log.info("Breaking-news check for %s @ %.2f: %s", direction.upper(), price, note)
    return NewsCheckResult(ran=True, block_reason=block, note=note, used_web_search=used_web,
                           headline_count=len(headlines), verdict=verdict, errors=errors)

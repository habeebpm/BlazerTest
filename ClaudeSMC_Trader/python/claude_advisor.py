"""
Asks Claude to validate the three-confluence framework (Trend / Momentum /
Strength - see ../../python/README.md, this repo's existing mechanical
XAUUSD bot) against a live market snapshot, and to only call "full
conviction" when it would actually act on the trade itself.

The pass/confirm thresholds in SYSTEM_PROMPT are copied verbatim from that
existing bot's documented and measured rule table, so this is the same
proven framework - just with an LLM doing the judgment call instead of a
hard-coded if-statement, per the user's explicit request. Claude also gets
the SMC (liquidity sweep / premium-discount) context and raw candles, so it
can down-weight a mechanically-passing setup that's structurally ugly (e.g.
a "confirmed" trend leg right into an unswept liquidity pool), which is the
whole point of asking a model to validate rather than just compute.

get_verdict() takes the Anthropic client as a parameter (dependency
injection, same philosophy as verifier.py in the Telegram copier stack) so
it's testable with a fake client - see selftest.py - without hitting the
network or requiring the `anthropic` package to be installed at all.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from config import AdvisorConfig

SYSTEM_PROMPT = """\
You are a disciplined XAUUSD (gold) trading analyst. You will be given a JSON \
snapshot of the current chart: indicator values, SMC structure, recent \
candles, session info. Validate the three-confluence framework below against \
it and decide whether this is a trade worth taking RIGHT NOW at market.

THE THREE CONFLUENCES (evaluate each independently, then combine):

1. TREND - passes when the trend-timeframe close sits on the trade's side of \
   its EMA200 (above for a buy, below for a sell) AND the primary timeframe's \
   EMA20 is on the same side of EMA50 (above for a buy, below for a sell). \
   CONFIRMED when |EMA20 - EMA50| on the primary timeframe is at least 0.25x \
   the primary ATR14 (a decisively separated cross, not a graze).

2. MOMENTUM - passes when MACD line is on the trade's side of its signal line \
   (above for a buy, below for a sell) AND RSI14 is between 50-70 for a buy \
   or 30-50 for a sell. CONFIRMED when the MACD histogram is expanding in the \
   trade's favor (compare macd_hist to macd_hist_prev) AND RSI14 >= 55 for a \
   buy or <= 45 for a sell.

3. STRENGTH - passes when ADX14 >= 22 AND +DI > -DI for a buy (or -DI > +DI \
   for a sell). CONFIRMED when ADX14 >= 28 AND the DI gap (|+DI - -DI|) >= 8.

A leg's `direction` is whichever side it actually leans (buy/sell/neutral) - \
evaluate it honestly even if that's the opposite of the other two legs.

CONFLUENCE COUNT: how many of the three legs point the SAME direction as your \
overall `direction` call (0-3). The existing mechanical version of this bot \
requires >= 2 of 3 with >= 1 confirmed before it would even consider a trade \
- treat that as your floor, not your bar for "full" conviction.

SMC CONTEXT (corroborating evidence, not a fourth mechanical gate): a \
liquidity sweep in your trade's favor (price swept the opposite side's stops \
and reclaimed) plus an entry sitting in the discount zone for a buy (premium \
for a sell) meaningfully strengthens a mechanically-passing setup. Price \
sitting in the WRONG zone, or an unswept liquidity pool still hanging directly \
above a buy / below a sell, should pull you toward "partial" even when the \
three legs mechanically pass - that unswept pool is exactly where price tends \
to go next.

CONVICTION - this is the field that actually gates execution, so be honest \
and conservative:
  - "full":    >=2 of 3 legs agree on direction, >=1 of those is CONFIRMED, \
               AND the SMC context does not contradict the trade, AND there \
               is no other obvious red flag (session dead/Asian range chop, \
               spread wide relative to ATR, price right into a level that \
               isn't in the data but the candle pattern itself warns of \
               exhaustion, etc).
  - "partial": the mechanical vote passes but something above gives you pause \
               - SMC contradicts, momentum and trend disagree, low confirmed \
               count, indecisive candle pattern.
  - "none":    fewer than 2 legs agree, or the setup is genuinely unclear.

direction is "none" only when you would not take either side.

Respond with the structured verdict. `reasoning` should be 2-4 sentences a \
trader could actually use to understand your call - name the specific \
numbers that drove it, don't just restate the rule text.
"""


class ConfluenceLeg(BaseModel):
    direction: Literal["buy", "sell", "neutral"]
    passes: bool
    confirmed: bool
    note: str


class ConfluenceVerdict(BaseModel):
    trend: ConfluenceLeg
    momentum: ConfluenceLeg
    strength: ConfluenceLeg
    confluence_count: int
    direction: Literal["buy", "sell", "none"]
    conviction: Literal["none", "partial", "full"]
    smc_alignment: str
    reasoning: str


def build_client():
    """Lazy import, same convention as mt5_gateway.mt5() - lets every other
    module in this solution stay importable without the `anthropic` package.
    Credentials resolve from the environment (ANTHROPIC_API_KEY or an
    `ant auth login` profile) - never hardcode a key here.
    """
    import anthropic
    return anthropic.Anthropic()


def get_verdict(client, cfg: AdvisorConfig, features: dict) -> ConfluenceVerdict:
    response = client.messages.parse(
        model=cfg.claude_model,
        max_tokens=cfg.claude_max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(features, indent=2, default=str)}],
        output_format=ConfluenceVerdict,
    )
    return response.parsed_output

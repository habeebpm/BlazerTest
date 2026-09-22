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

Any failure of the Claude API call itself (no credits, bad key, rate limit,
network down, ...) is re-raised as ClaudeUnavailableError with a specific,
classified reason - see _wrap_api_error() - so main.py's poll loop can tell
"Claude isn't available right now" apart from a genuine bug and just skip
the cycle instead of crashing. See ClaudeUnavailableError's own docstring
for how this relates to running the Telegram copier stack independently.
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

EXTENDED-ENTRY MOMENTUM CHECK - if price already looks stretched in your \
trade's direction (noticeably extended from its EMAs, RSI14 already \
elevated toward the 70/30 block zone rather than freshly crossing into \
confluence) you are looking at a CHASE, not a fresh entry, and momentum \
needs to still be building, not just present. `primary_indicators.\
macd_hist_shape` gives you the last several histogram bars and whether the \
current one is `declining_from_peak` (already retreating from its recent \
high/low, even though it may still be on the trade's side of zero and still \
pass the mechanical MOMENTUM test above). For an entry you judge as \
extended/chasing specifically: if `declining_from_peak` is true, treat that \
as a NO TRADE - direction "none", or "partial" at best, never "full" - and \
say so in your reasoning (wait for a pullback/reset rather than chase \
decelerating momentum). This was learned the hard way: comparing a losing \
chase entry against winning ones, the winners all had momentum still \
accelerating at entry, not just direction agreeing. It does NOT apply to a \
fresh, early-stage move that isn't extended - only to entries you'd \
already call a chase on their own merits.

CONFLUENCE COUNT: how many of the three legs point the SAME direction as your \
overall `direction` call (0-3). The existing mechanical version of this bot \
requires >= 2 of 3 with >= 1 confirmed before it would even consider a trade \
- treat that as your floor, not your bar for "full" conviction.

SMC CONTEXT (corroborating evidence, not a fourth mechanical gate) - `smc` \
in the snapshot has five parts, each weighing on conviction, not gating it \
mechanically:

  - liquidity_sweep: a sweep in your trade's favor (price swept the opposite \
    side's stops and reclaimed) strengthens the setup. An unswept liquidity \
    pool still hanging directly above a buy / below a sell is exactly where \
    price tends to go next - pulls you toward "partial".
  - premium_discount: an entry in the discount zone for a buy (premium for a \
    sell) strengthens it; the wrong zone pulls toward "partial".
  - market_structure: `trend` (bullish/bearish/transitional) and `last_event` \
    - a CHoCH (Change of Character - price just broke structure AGAINST the \
    prevailing trend) in your trade's direction is a strong, fresh reversal \
    signal worth real weight; a CHoCH AGAINST your trade's direction is a \
    serious red flag even if the three mechanical legs pass - structure just \
    turned against you. A BOS (Break of Structure) in your direction confirms \
    the existing trend is still intact and extending.
  - order_blocks: `bullish_order_block`/`bearish_order_block` (null if none \
    found) - the last opposing candle before a strong displacement move, i.e. \
    the market's own footprint of where size was likely transacted. An entry \
    with `price_inside_zone: true` on the order block matching your trade's \
    direction is a classic, well-regarded entry trigger; being deep inside \
    the OPPOSITE-direction order block argues against the trade.
  - fair_value_gaps: still-open 3-candle imbalances, most recent first. An \
    unfilled gap in your trade's direction between current price and your \
    stop is a magnet price often returns to before continuing - normal, not \
    a red flag on its own. A wide, fresh, opposite-direction gap sitting \
    right in the trade's path is worth noting as a likely pause/reversal \
    point.

`daily_weekly_levels` (prev_day_high/low, prev_week_high/low) are classic \
levels where stops cluster and reversals often start - an entry priced right \
through one of these, or a stop placed just beyond one where a lot of other \
stops likely sit too, is worth a mention in your reasoning either way.

CONVICTION - this is the field that actually gates execution, so be honest \
and conservative:
  - "full":    >=2 of 3 legs agree on direction, >=1 of those is CONFIRMED, \
               AND the SMC context (sweep, zone, structure, order blocks, \
               FVGs) does not contradict the trade, AND there is no other \
               obvious red flag (session dead/Asian range chop, spread wide \
               relative to ATR, price right into a level that isn't in the \
               data but the candle pattern itself warns of exhaustion, etc).
  - "partial": the mechanical vote passes but something above gives you pause \
               - SMC contradicts (wrong zone, unswept pool ahead, a CHoCH \
               against you, price sitting inside the opposite order block), \
               momentum and trend disagree, low confirmed count, indecisive \
               candle pattern.
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


class ClaudeUnavailableError(RuntimeError):
    """The Claude API call itself failed - bad key, no credits, rate limited,
    network down, an overloaded model, ... - as opposed to a bug in how this
    solution built the request. main.py's poll loop catches this separately
    from other errors: it logs why and skips the evaluation cycle rather than
    crashing the process, and retries on the next poll.

    This is the "run only Telegram if Claude is out of credits" behavior:
    this solution's own trading only pauses (no new signals get evaluated
    until Claude is reachable again), while the wholly independent Telegram
    copier stack (../../python/, ../../MQL5/) never calls Claude at all and
    keeps running unaffected, and ClaudeSMC_TradeManager.mq5 keeps managing
    whatever positions are already open with no Claude dependency of its
    own. See the top-level README's "Running with only one side available"
    section for the reverse case (Telegram/Claude API down, MT5 side up).
    """
    def __init__(self, message: str, *, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def _wrap_api_error(exc: Exception) -> ClaudeUnavailableError:
    """Classifies whatever the Anthropic SDK (or the network layer under it)
    raised into a ClaudeUnavailableError with a specific, actionable reason -
    using the typed exception hierarchy and the `.type` field documented at
    https://docs.anthropic.com/en/api/errors (402 -> billing_error is the
    "out of credits" case; it has no dedicated exception subclass, so `.type`
    is checked before falling back to the raw status code). If `anthropic`
    itself isn't importable, `exc` can only have come from a test double (a
    real client always requires the package - see build_client()), so this
    falls back to a generic message rather than guessing at a classification.
    """
    try:
        import anthropic
    except ImportError:
        return ClaudeUnavailableError(f"Claude API call failed: {exc}", retryable=False)

    if isinstance(exc, anthropic.APIStatusError):
        error_type = getattr(exc, "type", None)
        status = exc.status_code
        if error_type == "billing_error" or status == 402:
            return ClaudeUnavailableError(
                f"Claude API billing error (HTTP 402) - the account is almost certainly out "
                f"of credits. Add credits at https://console.anthropic.com/ or stop this "
                f"process (Ctrl+C, or stop the service) and keep running the Telegram copier "
                f"stack on its own until it's resolved: {exc}", retryable=False)
        if error_type == "authentication_error" or status == 401:
            return ClaudeUnavailableError(
                f"Claude API rejected the API key (HTTP 401) - check ANTHROPIC_API_KEY: {exc}",
                retryable=False)
        if error_type == "permission_error" or status == 403:
            return ClaudeUnavailableError(
                f"Claude API denied this request (HTTP 403): {exc}", retryable=False)
        if error_type == "rate_limit_error" or status == 429:
            return ClaudeUnavailableError(
                f"Claude API rate-limited this request (HTTP 429) - will retry next poll: {exc}",
                retryable=True)
        if status is not None and status >= 500:
            return ClaudeUnavailableError(
                f"Claude API service issue (HTTP {status}) - will retry next poll: {exc}",
                retryable=True)
        return ClaudeUnavailableError(f"Claude API request failed (HTTP {status}): {exc}",
                                      retryable=False)
    if isinstance(exc, anthropic.APIConnectionError):
        return ClaudeUnavailableError(
            f"Could not reach the Claude API (network issue) - will retry next poll: {exc}",
            retryable=True)
    return ClaudeUnavailableError(f"Claude API call failed unexpectedly: {exc}", retryable=False)


def get_verdict(client, cfg: AdvisorConfig, features: dict) -> ConfluenceVerdict:
    try:
        response = client.messages.parse(
            model=cfg.claude_model,
            max_tokens=cfg.claude_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(features, indent=2, default=str)}],
            output_format=ConfluenceVerdict,
        )
    except Exception as exc:
        raise _wrap_api_error(exc) from exc
    return response.parsed_output

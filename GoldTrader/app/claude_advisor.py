"""
Asks Claude to validate the three-confluence framework (Trend / Momentum /
Strength - the rules in SYSTEM_PROMPT below) against a live market snapshot, and to only call "full
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
import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from config import AdvisorConfig

log = logging.getLogger(__name__)

# Server-side refusal fallback: if Claude's safety classifiers decline a
# request, the API re-runs it on Anthropic's recommended fallback model in
# the same call instead of returning a refusal. The "default" scalar form
# needs exactly this beta header.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_fallbacks_rejected = {"value": False}

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

`dxy` (US Dollar Index context, null if not configured for this account - \
ignore it entirely when null) gives `vs_ema20` (above/below) and \
`change_pct_last_10_bars` for the dollar index. XAUUSD is usually (not \
always) inversely correlated with dollar strength: DXY below its EMA20 and/\
or falling corroborates a gold BUY (and argues against a SELL); DXY above \
its EMA20 and/or rising corroborates a SELL (and argues against a BUY). \
Treat a contradiction (e.g. DXY rising hard while you're evaluating a gold \
BUY) as a reason to lean toward "partial" rather than "full", not an \
automatic veto - the correlation breaks down often enough (risk-off moves, \
central bank divergence) that it's context, not a rule.

`consensus` (null if not configured for this account - ignore it entirely \
when null) counts another trading system's currently open positions on this \
same symbol (e.g. a Telegram-signal-driven EA sharing this account). \
Agreement (its open positions lean the same direction you're evaluating) is \
mild corroborating evidence; active disagreement (it's holding a meaningful \
number of positions the OPPOSITE direction) is worth a mention and a reason \
for extra caution, but never an automatic veto - it may simply be trading a \
different, unrelated strategy.

`economic_calendar` (null when no calendar is available - ignore it then) \
comes from MT5's built-in economic calendar for the watched currencies \
(USD by default). `upcoming_24h` lists scheduled moderate/high-impact \
releases with `minutes_until`; `minutes_to_next_blackout_event` is how long \
until the next event that blocks new entries (entries inside the blackout \
window are refused mechanically, so you never need to veto for that). A \
trade opened shortly before a high-impact release can be stopped out by the \
release spike before the setup plays out - if one is due within the next \
hour or so, say so and lean toward "partial" unless the setup is \
exceptional. `recent_releases` shows actual vs forecast (`surprise`) with \
`gold_impact`: a USD-positive surprise usually pressures gold, a \
USD-negative one usually supports it. Treat a fresh, large surprise that \
agrees with your direction as corroboration, and one against it as a \
reason for caution - the market may already have priced it in, so it is \
context, not a rule.

`xtr` (null when unavailable) is a mechanical M5/M15/H1 alignment read from \
a separately forward-tested gold scalping rulebook. Each timeframe is \
"bullish"/"bearish" only when EMA9 vs EMA21, RSI14 vs 50 and the MACD \
histogram ALL agree, else "mixed". `buy`/`sell` preview the conviction \
(full = M15 and H1 both agree, reduced = one agrees, unaligned = both \
mixed, opposed = one clearly against) and the setup type. `gate` says what \
is enforced after your verdict: "off" = nothing, the reading is evidence for \
you to weigh (an "opposed" preview is a reason for caution, not an automatic \
no - pullbacks against a clear M15/H1 do sometimes work); "block_opposed" \
= an entry against a clearly opposed M15 or H1 is refused, as are an \
extended chase whose M5 histogram has stopped accelerating and a \
bounce-failure entry before the histogram crosses zero; \
"require_alignment" = additionally an M5 trigger and one agreeing HTF are \
required. Do not call "full" for a direction the active gate would refuse. \
In a ranging regime (M5 ADX < 25) an RSI-extreme bounce at the Bollinger \
band is the rulebook's best-performing setup.

`recent_performance` summarizes this system's own last several closed trades \
(win/loss count, win rate, net P&L) - context only, never a mechanical gate: \
it does not change what counts as a valid setup. Use it the way a disciplined \
trader would use their own recent track record: a cold streak is a reason to \
demand a cleaner setup before calling "full" (tighten toward "partial" on a \
borderline call), never a reason to size up or chase to "get it back", and a \
hot streak is never itself a reason to lower your bar. If `trade_count` is 0 \
or the field is otherwise sparse, ignore it - there is nothing to learn from \
yet.

`ml_win_probability` (null until a local model has been trained - see \
ml_advisor.py and train_ml_model.py; ignore it entirely when null) is a \
locally-trained statistical estimate of how trades that looked like this \
one actually did historically, drawn only from THIS system's own past \
trades - `win_probability_pct_buy` and `win_probability_pct_sell` (the \
SAME current market state scored once per candidate direction, since which \
side you'll call hasn't been decided yet) plus `trained_on_n_trades` (how \
much history it's actually based on) and `cv_accuracy_pct` vs \
`base_rate_pct` (the model's out-of-sample accuracy vs always guessing the \
more common outcome). Read whichever probability matches the direction \
you're actually leaning toward. If `cv_accuracy_pct` is not clearly above \
`base_rate_pct`, the model has shown no real skill - ignore it. Treat a low \
sample count (well under 100) as a weak signal barely worth a mention; \
only let it meaningfully move your call once `trained_on_n_trades` is \
reasonably large and it has shown skill. Like `recent_performance`, this is pattern-matching against \
history, not a rule: a low win probability on an otherwise clean setup is \
a reason to lean toward "partial" rather than "full", never an automatic \
veto, and a high one is never by itself a reason to call "full" on a setup \
that doesn't otherwise earn it.

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

TAKE-PROFIT TARGETS - when direction is buy or sell, list in \
`take_profit_targets` up to 3 price levels in the trade's direction, nearest \
first, where price is likely to react: unswept liquidity pools, \
prev_day/prev_week highs and lows, the opposite order block, an \
opposite-direction FVG. Use real levels from the snapshot, not round-number \
guesses. They are shown to the trader in the entry notification only - the \
exit itself is managed mechanically (stop locked at TP1, then trailed) - so \
they never change whether or how the trade executes. Empty list when \
direction is "none".

Respond with the structured verdict. `reasoning` should be 2-4 sentences a \
trader could actually use to understand your call - name the specific \
numbers that drove it, don't just restate the rule text.
"""


def _btc_prompt(gold: str) -> str:
    """The same analyst rules, written for BTCUSD: every gold-specific
    passage of SYSTEM_PROMPT replaced (each must exist - the self-test
    fails loudly if the gold prompt changes under it)."""
    def swap(text: str, old: str, new: str) -> str:
        if text.count(old) != 1:
            raise RuntimeError(f"BTC prompt: gold passage not found once: {old[:60]!r}")
        return text.replace(old, new)

    out = swap(gold, "You are a disciplined XAUUSD (gold) trading analyst.",
               "You are a disciplined BTCUSD (Bitcoin) trading analyst.")
    start = out.index("`dxy` (US Dollar Index context")
    end = out.index("`consensus` (null")
    out = out[:start] + (
        "BITCOIN MARKET NOTES: BTCUSD trades around the clock, weekends included - weekend and "
        "late-US-evening liquidity is thin, so moves there travel further on less volume and "
        "fake breakouts are common; the US cash session (and the London/US overlap) carries the "
        "real volume. Stop hunts and liquidation cascades are routine: price often sweeps a "
        "round number (every $1,000, especially $5,000/$10,000 marks) or the previous "
        "day/week high/low, then snaps back - a sweep-and-reclaim in your direction is strong "
        "evidence, a buy sitting just under an unswept round-number high (or a sell just above "
        "an unswept low) pulls toward \"partial\". Moves are large relative to the stop, so "
        "a clean structure break (BOS/CHoCH) with an order-block or FVG entry matters more "
        "than a marginal oscillator reading.\n\n"
        "`dxy` (US Dollar Index context, null if not configured for this account - ignore it "
        "entirely when null) gives `vs_ema20` and `change_pct_last_10_bars` for the dollar "
        "index. Bitcoin behaves mostly like a high-beta risk asset (it tends to move with "
        "Nasdaq and liquidity) with a loose, unstable inverse link to the dollar: a dollar "
        "rising hard argues against a BUY and supports a SELL, a falling dollar the other way "
        "round. Treat a contradiction as a reason to lean toward \"partial\", never an "
        "automatic veto - the link breaks often (crypto-specific news, ETF flows, exchange "
        "events dominate on many days).\n\n") + out[end:]
    out = swap(out, "`gold_impact`: a USD-positive surprise usually pressures gold, a "
                    "USD-negative one usually supports it.",
               "`btc_impact`: a USD-positive (hawkish) surprise usually pressures Bitcoin, a "
               "USD-negative (dovish) one usually supports it.")
    out = swap(out, "a separately forward-tested gold scalping rulebook.",
               "a mechanical rulebook first built for gold scalping, used here as a plain "
               "M5/M15/H1 trend-alignment read for BTCUSD.")
    return out


BTC_SYSTEM_PROMPT = _btc_prompt(SYSTEM_PROMPT)


def system_prompt(cfg) -> str:
    """The analyst prompt for this instance's market (gold unchanged)."""
    return BTC_SYSTEM_PROMPT if getattr(cfg, "instrument", "gold") == "btc" else SYSTEM_PROMPT


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
    # Defaulted in Python (mechanical backtest verdicts and tests build this
    # model without it) but REQUIRED in the schema sent to Claude, so a real
    # verdict always carries it.
    take_profit_targets: list[float] = Field(default_factory=list)

    model_config = ConfigDict(json_schema_extra=lambda schema, _model: schema.setdefault(
        "required", []).append("take_profit_targets"))


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
    Claude-sourced trading only pauses (no new signals get evaluated until
    Claude is reachable again), while UnifiedTrader_EA keeps copying
    Telegram signals and managing whatever positions are already open - it
    never calls Claude.
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
        if error_type == "not_found_error" or status == 404:
            return ClaudeUnavailableError(
                f"Claude API does not know this model (HTTP 404) - set a current model with "
                f"--model in start.bat: {exc}", retryable=False)
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
    if "Could not resolve authentication method" in str(exc):
        return ClaudeUnavailableError(
            "no Anthropic API key - put ANTHROPIC_API_KEY in keys.txt (or run settings.bat)",
            retryable=False)
    return ClaudeUnavailableError(f"Claude API call failed unexpectedly: {exc}", retryable=False)


def _is_fallback_rejection(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 400 and "fallback" in str(exc).lower()


def call_with_fallbacks(client, cfg: AdvisorConfig, method: str, **kwargs):
    """client.beta.messages.<method>(**kwargs) with refusal fallbacks on
    (cfg.claude_refusal_fallbacks), else client.messages.<method>. If the
    API rejects the fallbacks option itself (a 400 naming it - e.g. an
    account or platform without the beta), the call is retried once
    without it and fallbacks stay off for the rest of the run."""
    if cfg.claude_refusal_fallbacks and not _fallbacks_rejected["value"]:
        try:
            return getattr(client.beta.messages, method)(
                betas=[REFUSAL_FALLBACK_BETA], fallbacks="default", **kwargs)
        except Exception as exc:
            if not _is_fallback_rejection(exc):
                raise
            _fallbacks_rejected["value"] = True
            log.warning("Claude API rejected refusal fallbacks (%s) - continuing without them.", exc)
    return getattr(client.messages, method)(**kwargs)


def neutral_verdict(reason: str) -> ConfluenceVerdict:
    """A "no trade" verdict for a cycle where Claude gave no usable answer
    (declined, or ran out of max_tokens) - gate() then rejects it as "no
    actionable direction", so nothing trades and nothing is retried."""
    leg = ConfluenceLeg(direction="neutral", passes=False, confirmed=False, note=reason)
    return ConfluenceVerdict(trend=leg, momentum=leg, strength=leg, confluence_count=0,
                             direction="none", conviction="none", smc_alignment="n/a",
                             reasoning=f"No verdict this cycle: {reason}.")


def get_verdict(client, cfg: AdvisorConfig, features: dict) -> ConfluenceVerdict:
    try:
        response = call_with_fallbacks(
            client, cfg, "parse",
            model=cfg.claude_model,
            max_tokens=cfg.claude_max_tokens,
            system=system_prompt(cfg),
            messages=[{"role": "user", "content": json.dumps(features, indent=2, default=str)}],
            output_format=ConfluenceVerdict,
        )
    except Exception as exc:
        raise _wrap_api_error(exc) from exc
    usage = getattr(response, "usage", None)
    if usage is not None:
        # Real cost per call - compare with docs/REFERENCE.md "Small accounts".
        log.info("Claude call: %s input + %s output tokens", getattr(usage, "input_tokens", "?"),
                 getattr(usage, "output_tokens", "?"))
    stop = getattr(response, "stop_reason", None)
    parsed = getattr(response, "parsed_output", None)
    if stop == "refusal" or parsed is None:
        details = getattr(response, "stop_details", None)
        reason = ("Claude declined the request" + (f" ({details.category})" if getattr(details, "category", None)
                                                   else "")
                  if stop == "refusal" else
                  f"Claude's answer was cut off or unreadable (stop_reason={stop}) - raise claude_max_tokens"
                  if stop == "max_tokens" else f"no structured verdict (stop_reason={stop})")
        log.warning("%s - treating this cycle as no trade.", reason)
        return neutral_verdict(reason)
    return parsed

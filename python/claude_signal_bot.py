#!/usr/bin/env python3
"""
Claude-driven signal bridge for MQL5/Experts/ClaudeSignalEA.mq5.

Every `--interval` seconds (60s by default, matching the EA's export cadence):

  1. Reads the plain-text chart-data file the EA exports (OHLCV bars plus a
     metadata header: symbol, digits, point, tick value/size, bid/ask).
  2. Derives a compact "scenario" summary from it - trend (EMA20/50/200),
     momentum (RSI/MACD), trend-strength (ADX/DMI), volatility (ATR,
     Bollinger width) and an extended-range/breakout read (price's position
     inside its recent 20-bar range) - alongside the raw bars.
  3. Sends both to Claude and asks for a strict-JSON BUY/SELL/NONE decision
     with absolute SL/TP prices.
  4. Validates the response defensively (correct side of price, confidence
     floor, a sanity cap on stop distance) and writes it to the signal file
     the EA polls, as a new, monotonically increasing signal id.

    python claude_signal_bot.py --selftest             # offline logic checks, no API key needed
    python claude_signal_bot.py --data-dir <path> --once --dry-run -v
    python claude_signal_bot.py --data-dir <path>       # run the loop, ANTHROPIC_API_KEY required

`--data-dir` should be the MT5 terminal's shared Common\\Files folder (the
one the EA writes to with FILE_COMMON) - typically, on Windows:
    %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files
See CLAUDE_SIGNAL_PIPELINE.md for the full setup and file-format reference.

NOTE ON "XTR": the task that produced this bridge asked for analysis "based
on XTR and other advanced scenarios". XTR is not a standard technical-
analysis term, so this implements it as an eXtended Trend/Range read (the
`range_scenario` field below - is price breaking out of, or sitting inside,
its recent range) combined with the usual trend/momentum/volatility
confluences, all handed to Claude as pre-computed context rather than left
for the model to eyeball from raw candles alone. Adjust
`build_scenario_summary` if a more specific meaning was intended.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

import indicators as ind

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
SIGNAL_LOG = LOG_DIR / "claude_signals.csv"

log = logging.getLogger("claude_signal_bot")

DEFAULT_MODEL = "claude-sonnet-5"
VALID_ACTIONS = {"BUY", "SELL", "NONE"}
HEADER_RE = re.compile(r"(\w+)=(\S+)")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_DIR / "claude_signal_bot.log", encoding="utf-8"),
        ],
    )


@dataclass
class BotConfig:
    data_file: Path
    signal_file: Path
    ack_file: Path
    state_file: Path
    interval: float = 60.0
    model: str = DEFAULT_MODEL
    api_key: str = ""
    min_confidence: float = 60.0
    max_sl_distance: float | None = None
    lot: float = 0.01
    bars_in_prompt: int = 120
    dry_run: bool = False


@dataclass
class ChartSnapshot:
    symbol: str
    digits: int
    point: float
    tick_value: float
    tick_size: float
    bid: float
    ask: float
    spread: int
    timeframe: str
    exported_at: str
    bars: pd.DataFrame   # columns: time, open, high, low, close, tick_volume, spread


# --------------------------------------------------------------------------
# Chart-data file parsing
# --------------------------------------------------------------------------

def parse_chart_file(path: Path) -> ChartSnapshot:
    if not path.exists():
        raise FileNotFoundError(path)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 3:
        raise ValueError(f"{path}: expected a header, a CSV header row and at least one bar")

    meta = dict(HEADER_RE.findall(lines[0].lstrip("#").strip()))
    if "symbol" not in meta:
        raise ValueError(f"{path}: missing 'symbol' in header metadata")

    rows = []
    for line in lines[2:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 7:
            continue
        rows.append(parts[:7])

    if not rows:
        raise ValueError(f"{path}: no bar rows found")

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "tick_volume", "spread"])
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    df["tick_volume"] = df["tick_volume"].astype(int)
    df["spread"] = df["spread"].astype(int)
    df["time"] = pd.to_datetime(df["time"], format="%Y.%m.%d %H:%M")

    last_close = float(df["close"].iloc[-1])
    return ChartSnapshot(
        symbol=meta["symbol"],
        digits=int(meta.get("digits", 2)),
        point=float(meta.get("point", 0.01)),
        tick_value=float(meta.get("tick_value", 1.0)),
        tick_size=float(meta.get("tick_size", 0.01)),
        bid=float(meta.get("bid", last_close)),
        ask=float(meta.get("ask", last_close)),
        spread=int(meta.get("spread", 0)),
        timeframe=meta.get("timeframe", "M1"),
        exported_at=meta.get("exported", ""),
        bars=df,
    )


# --------------------------------------------------------------------------
# Scenario summary - the "XTR and other advanced scenarios" context
# --------------------------------------------------------------------------

def build_scenario_summary(snap: ChartSnapshot) -> dict:
    df = snap.bars
    close, high, low = df["close"], df["high"], df["low"]

    ema20 = ind.ema(close, 20)
    ema50 = ind.ema(close, 50)
    ema200 = ind.ema(close, 200) if len(close) >= 200 else pd.Series([float("nan")] * len(close))
    rsi14 = ind.rsi(close, 14)
    _, _, macd_hist = ind.macd(close)
    atr14 = ind.atr(high, low, close, 14)
    adx14, plus_di, minus_di = ind.adx(high, low, close, 14)
    bb_upper, _, bb_lower = ind.bollinger(close, 20, 2.0)

    recent_high = float(high.tail(20).max())
    recent_low = float(low.tail(20).min())
    last_close = float(close.iloc[-1])

    trend = "up" if ema20.iloc[-1] > ema50.iloc[-1] else "down"
    if pd.isna(ema200.iloc[-1]):
        trend_vs_macro = "unknown"
    else:
        trend_vs_macro = "aligned" if (trend == "up") == (last_close > ema200.iloc[-1]) else "against"

    # eXtended Trend/Range read: is price pressing a recent extreme
    # (breakout scenario) or oscillating inside it (range scenario)?
    span = max(recent_high - recent_low, 1e-9)
    range_pos = (last_close - recent_low) / span
    if range_pos > 0.9:
        range_scenario = "pressing_recent_high"
    elif range_pos < 0.1:
        range_scenario = "pressing_recent_low"
    else:
        range_scenario = "inside_range"

    bb_width = None
    if not pd.isna(bb_upper.iloc[-1]) and not pd.isna(bb_lower.iloc[-1]):
        bb_width = float(bb_upper.iloc[-1] - bb_lower.iloc[-1])

    return {
        "close": last_close,
        "ema20": float(ema20.iloc[-1]),
        "ema50": float(ema50.iloc[-1]),
        "ema200": None if pd.isna(ema200.iloc[-1]) else float(ema200.iloc[-1]),
        "rsi14": float(rsi14.iloc[-1]),
        "macd_hist": float(macd_hist.iloc[-1]),
        "adx14": float(adx14.iloc[-1]),
        "plus_di": float(plus_di.iloc[-1]),
        "minus_di": float(minus_di.iloc[-1]),
        "atr14": float(atr14.iloc[-1]),
        "bollinger_width": bb_width,
        "recent_high_20": recent_high,
        "recent_low_20": recent_low,
        "trend": trend,
        "trend_vs_macro": trend_vs_macro,
        "range_scenario": range_scenario,
    }


# --------------------------------------------------------------------------
# Claude call
# --------------------------------------------------------------------------

def build_prompt(snap: ChartSnapshot, scenario: dict, cfg: BotConfig) -> tuple[str, str]:
    system = (
        f"You are a disciplined intraday trading analyst for {snap.symbol}. "
        "You are given recent bars plus pre-computed technical scenario "
        "statistics (trend, momentum, trend-strength, volatility and an "
        "extended trend/range breakout-vs-range read). Decide whether the "
        "evidence supports a new BUY, a new SELL, or NONE. Be conservative: "
        "only signal a trade when multiple independent pieces of evidence "
        "agree; default to NONE when the evidence is mixed or thin. "
        "Reply with STRICT JSON only - no markdown fences, no commentary "
        "outside the object - matching exactly this shape: "
        '{"action": "BUY"|"SELL"|"NONE", "sl": <number or null>, '
        '"tp": <number or null>, "confidence": <integer 0-100>, '
        '"reasoning": "<one sentence>"}. '
        f"sl/tp must be absolute prices on the correct side of the current "
        f"bid/ask ({snap.bid}/{snap.ask}) when action is BUY or SELL, and "
        "null when action is NONE."
    )
    recent = snap.bars.tail(cfg.bars_in_prompt)
    user = (
        f"Symbol: {snap.symbol}\n"
        f"Bid/Ask: {snap.bid}/{snap.ask}  Spread(points): {snap.spread}\n"
        f"Timeframe: {snap.timeframe}  Exported: {snap.exported_at}\n\n"
        f"Scenario summary:\n{json.dumps(scenario, indent=2)}\n\n"
        f"Last {len(recent)} bars (oldest first):\n{recent.to_csv(index=False)}"
    )
    return system, user


def call_claude(system: str, user: str, cfg: BotConfig) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=cfg.api_key)
    resp = client.messages.create(
        model=cfg.model,
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


def parse_claude_response(raw: str) -> dict:
    match = JSON_BLOCK_RE.search(raw)
    if not match:
        raise ValueError(f"no JSON object found in Claude's response: {raw[:200]!r}")
    data = json.loads(match.group(0))

    action = str(data.get("action", "NONE")).upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action {action!r}")

    return {
        "action": action,
        "sl": data.get("sl"),
        "tp": data.get("tp"),
        "confidence": float(data.get("confidence", 0) or 0),
        "reasoning": str(data.get("reasoning", ""))[:200].replace(",", ";").replace("\n", " "),
    }


def validate_signal(data: dict, snap: ChartSnapshot, cfg: BotConfig) -> tuple[bool, str]:
    """Defensively re-checks Claude's decision before it ever reaches the EA."""
    if data["action"] == "NONE":
        return True, "no trade"

    if data["confidence"] < cfg.min_confidence:
        return False, f"confidence {data['confidence']:.0f} below floor {cfg.min_confidence:.0f}"

    sl, tp = data.get("sl"), data.get("tp")
    if sl is None or tp is None:
        return False, "missing sl/tp"
    sl, tp = float(sl), float(tp)

    price = snap.ask if data["action"] == "BUY" else snap.bid
    if data["action"] == "BUY" and not (sl < price < tp):
        return False, "sl/tp not on the correct side of price for a BUY"
    if data["action"] == "SELL" and not (tp < price < sl):
        return False, "sl/tp not on the correct side of price for a SELL"

    sl_distance = abs(price - sl)
    if sl_distance <= 0:
        return False, "zero sl distance"
    if cfg.max_sl_distance is not None and sl_distance > cfg.max_sl_distance:
        return False, f"sl distance {sl_distance:.2f} exceeds sanity cap {cfg.max_sl_distance:.2f}"

    return True, "ok"


# --------------------------------------------------------------------------
# Signal file I/O (atomic writes, matches the EA's parser)
# --------------------------------------------------------------------------

def write_signal(cfg: BotConfig, signal_id: int, symbol: str, data: dict) -> None:
    action = data["action"]
    sl = data.get("sl") or 0.0
    tp = data.get("tp") or 0.0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    reason = data.get("reasoning", "").replace(",", ";").replace("\n", " ")
    line = f"{signal_id},{symbol},{action},{cfg.lot},{sl},{tp},{ts},{reason}\n"

    tmp = cfg.signal_file.with_suffix(cfg.signal_file.suffix + ".tmp")
    tmp.write_text(line, encoding="utf-8")
    os.replace(tmp, cfg.signal_file)


def record_signal(row: dict) -> None:
    is_new = not SIGNAL_LOG.exists()
    with SIGNAL_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def load_state(cfg: BotConfig) -> dict:
    if cfg.state_file.exists():
        try:
            return json.loads(cfg.state_file.read_text(encoding="utf-8"))
        except Exception:
            log.warning("could not parse state file %s, starting fresh", cfg.state_file)
    return {"last_signal_id": 0, "last_bar_time": None}


def save_state(cfg: BotConfig, state: dict) -> None:
    tmp = cfg.state_file.with_suffix(cfg.state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, cfg.state_file)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def run_once(cfg: BotConfig, state: dict) -> dict:
    snap = parse_chart_file(cfg.data_file)
    last_bar_time = snap.bars["time"].iloc[-1].isoformat()
    if state.get("last_bar_time") == last_bar_time:
        log.debug("no new bar since %s, skipping this cycle", last_bar_time)
        return state

    scenario = build_scenario_summary(snap)
    system, user = build_prompt(snap, scenario, cfg)

    try:
        raw = call_claude(system, user, cfg)
        data = parse_claude_response(raw)
    except Exception as exc:
        log.error("Claude analysis failed, skipping this cycle: %s", exc)
        return state

    ok, reason = validate_signal(data, snap, cfg)
    if not ok:
        log.info("signal rejected (%s): %s", reason, data)
        data = {**data, "action": "NONE", "reasoning": f"rejected: {reason}"}

    state = dict(state)
    state["last_signal_id"] = state.get("last_signal_id", 0) + 1
    state["last_bar_time"] = last_bar_time
    signal_id = state["last_signal_id"]

    record_signal({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_id": signal_id,
        "bar_time": last_bar_time,
        "action": data["action"],
        "sl": data.get("sl"),
        "tp": data.get("tp"),
        "confidence": data.get("confidence"),
        "reasoning": data.get("reasoning"),
    })

    if cfg.dry_run:
        log.info("[dry-run] would write id=%d action=%s sl=%s tp=%s conf=%s",
                  signal_id, data["action"], data.get("sl"), data.get("tp"), data.get("confidence"))
    else:
        write_signal(cfg, signal_id, snap.symbol, data)
        log.info("wrote signal id=%d action=%s sl=%s tp=%s conf=%s",
                  signal_id, data["action"], data.get("sl"), data.get("tp"), data.get("confidence"))

    return state


def main_loop(cfg: BotConfig) -> None:
    state = load_state(cfg)
    log.info("watching %s every %.0fs (model=%s, dry_run=%s)", cfg.data_file, cfg.interval, cfg.model, cfg.dry_run)
    while True:
        try:
            state = run_once(cfg, state)
            save_state(cfg, state)
        except FileNotFoundError:
            log.warning("chart data file %s not found yet (has the EA run?), waiting", cfg.data_file)
        except Exception:
            log.exception("unexpected error in main loop, continuing")
        time.sleep(cfg.interval)


# --------------------------------------------------------------------------
# Selftest - runs anywhere, no API key or MT5 needed
# --------------------------------------------------------------------------

def _sample_chart_text(symbol: str = "XAUUSD") -> str:
    import numpy as np

    rng = np.random.default_rng(42)
    n = 220
    closes = 2340.0 + np.cumsum(rng.normal(0.05, 0.6, n))
    t0 = datetime(2026, 9, 16, 8, 0)

    lines = [
        f"#symbol={symbol} digits=2 point=0.01 tick_value=1.00 tick_size=0.01 "
        f"bid={closes[-1] - 0.1:.2f} ask={closes[-1] + 0.1:.2f} spread=25 "
        f"timeframe=PERIOD_M1 exported=2026.09.16T12:00:00",
        "time,open,high,low,close,tick_volume,spread",
    ]
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        h, l = max(o, c) + 0.3, min(o, c) - 0.3
        ts = (t0 + pd.Timedelta(minutes=i)).strftime("%Y.%m.%d %H:%M")
        lines.append(f"{ts},{o:.2f},{h:.2f},{l:.2f},{c:.2f},100,25")
    return "\n".join(lines) + "\n"


def selftest() -> None:
    import tempfile

    print("claude_signal_bot selftest")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        data_file = tmp_path / "chart.txt"
        data_file.write_text(_sample_chart_text())

        snap = parse_chart_file(data_file)
        assert snap.symbol == "XAUUSD"
        assert len(snap.bars) > 100
        print(f"  parse_chart_file: OK ({len(snap.bars)} bars)")

        scenario = build_scenario_summary(snap)
        assert "trend" in scenario and "rsi14" in scenario and "range_scenario" in scenario
        print(f"  build_scenario_summary: OK (trend={scenario['trend']}, rsi14={scenario['rsi14']:.1f})")

        cfg = BotConfig(
            data_file=data_file,
            signal_file=tmp_path / "signals.txt",
            ack_file=tmp_path / "ack.txt",
            state_file=tmp_path / "state.json",
            min_confidence=60.0,
            max_sl_distance=20.0,
        )

        good = {"action": "BUY", "sl": snap.ask - 5.0, "tp": snap.ask + 10.0, "confidence": 80.0, "reasoning": "test"}
        ok, reason = validate_signal(good, snap, cfg)
        assert ok, reason
        print("  validate_signal accepts a well-formed BUY: OK")

        wrong_side = {**good, "sl": snap.ask + 5.0}
        ok, reason = validate_signal(wrong_side, snap, cfg)
        assert not ok
        print(f"  validate_signal rejects sl on the wrong side: OK ({reason})")

        low_conf = {**good, "confidence": 10.0}
        ok, reason = validate_signal(low_conf, snap, cfg)
        assert not ok
        print(f"  validate_signal rejects low confidence: OK ({reason})")

        too_wide = {**good, "sl": snap.ask - 50.0}
        ok, reason = validate_signal(too_wide, snap, cfg)
        assert not ok
        print(f"  validate_signal rejects an oversized sl distance: OK ({reason})")

        parsed = parse_claude_response(
            'Sure, here you go:\n```json\n'
            '{"action": "SELL", "sl": 2350.0, "tp": 2330.0, "confidence": 72, "reasoning": "trend down"}\n'
            '```'
        )
        assert parsed["action"] == "SELL" and parsed["confidence"] == 72.0
        print("  parse_claude_response extracts JSON from a fenced reply: OK")

        state = {"last_signal_id": 0, "last_bar_time": None}
        state["last_signal_id"] += 1
        write_signal(cfg, state["last_signal_id"], snap.symbol, good)
        assert cfg.signal_file.exists()
        content = cfg.signal_file.read_text().strip()
        assert content.startswith("1,XAUUSD,BUY,")
        print(f"  write_signal writes atomically: OK ({content})")

        save_state(cfg, state)
        reloaded = load_state(cfg)
        assert reloaded["last_signal_id"] == 1
        print("  save_state/load_state round-trip: OK")

    print("ALL SELFTESTS PASSED")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_config_from_args(args: argparse.Namespace) -> BotConfig:
    data_dir = Path(args.data_dir) if args.data_dir else Path(".")
    return BotConfig(
        data_file=Path(args.data_file) if args.data_file else data_dir / "claude_chart_data.txt",
        signal_file=Path(args.signal_file) if args.signal_file else data_dir / "claude_trade_signals.txt",
        ack_file=Path(args.ack_file) if args.ack_file else data_dir / "claude_trade_ack.txt",
        state_file=Path(args.state_file) if args.state_file else LOG_DIR / "claude_signal_bot_state.json",
        interval=args.interval,
        model=args.model,
        api_key=args.api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
        min_confidence=args.min_confidence,
        max_sl_distance=args.max_sl_distance,
        lot=args.lot,
        bars_in_prompt=args.bars_in_prompt,
        dry_run=args.dry_run,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--selftest", action="store_true", help="run offline logic checks and exit")
    parser.add_argument("--once", action="store_true", help="run a single analysis cycle and exit")
    parser.add_argument("--data-dir", help="MT5 Common\\Files folder shared with ClaudeSignalEA")
    parser.add_argument("--data-file", help="override path to the chart-data file")
    parser.add_argument("--signal-file", help="override path to the signal file written for the EA")
    parser.add_argument("--ack-file", help="override path to the EA's execution-ack file")
    parser.add_argument("--state-file", help="path to this bot's own bookkeeping (last bar/signal id)")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between analysis cycles")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", default=None, help="defaults to $ANTHROPIC_API_KEY")
    parser.add_argument("--min-confidence", type=float, default=60.0)
    parser.add_argument("--max-sl-distance", type=float, default=None,
                         help="reject signals whose stop distance (in price units) exceeds this")
    parser.add_argument("--lot", type=float, default=0.01, help="lot size written into each signal")
    parser.add_argument("--bars-in-prompt", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true", help="analyze and log but never write the signal file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)

    if args.selftest:
        selftest()
        return 0

    cfg = build_config_from_args(args)
    if not cfg.dry_run and not cfg.api_key:
        log.error("no Claude API key: set ANTHROPIC_API_KEY, pass --api-key, or run with --dry-run")
        return 1

    if args.once:
        state = load_state(cfg)
        state = run_once(cfg, state)
        save_state(cfg, state)
        return 0

    main_loop(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Thin wrapper around the MetaTrader5 Python package.

The MetaTrader5 package ships Windows-only wheels and talks to a local
MetaTrader 5 terminal over Windows IPC, so it cannot be installed on macOS or
Linux. It is therefore imported lazily: every other module in this project
(indicators, strategy, config, the test suites) stays importable and runnable
on any platform - only live trading needs Windows.

On a Mac, see the "Running on a Mac" section of README.md; the short version
is that the MQL5 Expert Advisor runs natively inside MT5 for macOS and needs
no Python at all.
"""
from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from config import TradeConfig

log = logging.getLogger(__name__)

_mt5 = None


def mt5():
    """Import MetaTrader5 on first use, with a clear error if unavailable."""
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5 as m
        except ImportError as exc:  # pragma: no cover - platform dependent
            system = platform.system()
            if system == "Darwin":
                raise RuntimeError(
                    "The MetaTrader5 Python package does not run on macOS - it ships "
                    "Windows-only wheels and talks to the terminal over Windows IPC.\n"
                    "  Options, easiest first:\n"
                    "   1. Run the MQL5 Expert Advisor instead "
                    "(MQL5/Experts/XAUUSD_Confluence_EA.mq5). MT5 for macOS runs it "
                    "natively, same strategy, no Python needed. Recommended.\n"
                    "   2. Run this bot inside a Windows VM (Parallels / VMware Fusion "
                    "/ UTM) with MT5 and Python installed in the VM.\n"
                    "   3. Run it on a Windows VPS, which also keeps it going when your "
                    "Mac is asleep.\n"
                    "   4. Install Windows Python into the same Wine/CrossOver bottle as "
                    "MT5 and pip install MetaTrader5 there.\n"
                    "  The strategy logic itself does run on your Mac: try "
                    "'python trader.py --selftest' and 'python test_integration.py'."
                ) from exc
            raise RuntimeError(
                f"The MetaTrader5 package is not available on this platform ({system}). "
                "It requires Windows with the MetaTrader 5 terminal installed: "
                "pip install MetaTrader5"
            ) from exc
        _mt5 = m
    return _mt5


TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1",
}


def timeframe(name: str):
    try:
        return getattr(mt5(), TIMEFRAMES[name.upper()])
    except KeyError as exc:
        raise ValueError(f"Unsupported timeframe {name!r}. Use one of {list(TIMEFRAMES)}") from exc


@dataclass
class SymbolSpec:
    name: str
    point: float
    digits: int
    stops_level_points: int
    spread_points: int
    volume_min: float
    volume_max: float
    volume_step: float
    tick_value: float
    tick_size: float
    filling_mode: int


# --------------------------------------------------------------------------- #
# connection
# --------------------------------------------------------------------------- #
def connect(cfg: TradeConfig) -> None:
    m = mt5()
    kwargs = {}
    if cfg.terminal_path:
        kwargs["path"] = cfg.terminal_path
    if cfg.login and cfg.password and cfg.server:
        kwargs.update(login=int(cfg.login), password=cfg.password, server=cfg.server)

    if not m.initialize(**kwargs):
        raise RuntimeError(f"MT5 initialize() failed: {m.last_error()}")

    account = m.account_info()
    if account is None:
        raise RuntimeError(f"MT5 account_info() failed: {m.last_error()}")

    if not m.symbol_select(cfg.symbol, True):
        raise RuntimeError(
            f"Could not select {cfg.symbol} in Market Watch: {m.last_error()}. "
            "Check the exact symbol name your broker uses for gold."
        )

    log.info(
        "Connected: account=%s server=%s balance=%.2f %s trade_allowed=%s",
        account.login, account.server, account.balance, account.currency,
        account.trade_allowed,
    )
    if not account.trade_allowed:
        log.warning("Algo trading is DISABLED for this account/terminal "
                    "(enable the AlgoTrading button in MT5).")


def disconnect() -> None:
    if _mt5 is not None:
        _mt5.shutdown()


def get_symbol_spec(cfg: TradeConfig) -> SymbolSpec:
    m = mt5()
    info = m.symbol_info(cfg.symbol)
    if info is None:
        raise RuntimeError(f"symbol_info({cfg.symbol}) returned None: {m.last_error()}")
    return SymbolSpec(
        name=info.name,
        point=info.point,
        digits=info.digits,
        stops_level_points=getattr(info, "trade_stops_level", 0),
        spread_points=info.spread,
        volume_min=info.volume_min,
        volume_max=info.volume_max,
        volume_step=info.volume_step,
        tick_value=info.trade_tick_value,
        tick_size=info.trade_tick_size,
        filling_mode=info.filling_mode,
    )


# --------------------------------------------------------------------------- #
# market open / closed
# --------------------------------------------------------------------------- #
class QuoteMonitor:
    """Decides whether quotes are live by watching the tick timestamp advance.

    Deliberately avoids comparing MT5's server-time timestamps against the
    local clock: the broker's server sits in its own timezone (often GMT+2/+3),
    so "tick.time is 3 hours old" usually means a timezone gap, not a closed
    market. A frozen timestamp, measured with a monotonic wall clock, is
    unambiguous.
    """

    def __init__(self, stale_after: float = 90.0):
        self.stale_after = stale_after
        self._last_quote_time = None
        self._last_change = time.monotonic()

    def update(self, tick) -> bool:
        """Feed the latest tick; returns True while quotes still look live."""
        qt = getattr(tick, "time_msc", None) or getattr(tick, "time", None)
        if qt != self._last_quote_time:
            self._last_quote_time = qt
            self._last_change = time.monotonic()
            return True
        return (time.monotonic() - self._last_change) < self.stale_after

    @property
    def frozen_for(self) -> float:
        return time.monotonic() - self._last_change


def trade_mode_problem(cfg: TradeConfig) -> str | None:
    """Return a reason string if the symbol itself is not fully tradable."""
    m = mt5()
    info = m.symbol_info(cfg.symbol)
    if info is None:
        return f"symbol_info({cfg.symbol}) unavailable"
    mode = info.trade_mode
    names = {
        getattr(m, "SYMBOL_TRADE_MODE_DISABLED", 0): "trading disabled for this symbol",
        getattr(m, "SYMBOL_TRADE_MODE_LONGONLY", 1): "long-only mode",
        getattr(m, "SYMBOL_TRADE_MODE_SHORTONLY", 2): "short-only mode",
        getattr(m, "SYMBOL_TRADE_MODE_CLOSEONLY", 3): "close-only mode (no new positions)",
    }
    return names.get(mode)


def market_status(cfg: TradeConfig, samples: int = 2, gap: float = 2.0) -> tuple[bool, str]:
    """One-shot check: is this symbol actually tradable right now?

    Samples the quote timestamp `samples` times `gap` seconds apart. XAUUSD
    ticks several times a second while its session is open, so a timestamp that
    does not move across the samples means the session is shut.
    """
    problem = trade_mode_problem(cfg)
    if problem:
        return False, problem

    monitor = QuoteMonitor(stale_after=0.0)   # any repeat sample counts as frozen
    monitor.update(get_tick(cfg.symbol))
    moved = False
    for _ in range(max(1, samples - 1)):
        time.sleep(gap)
        if monitor.update(get_tick(cfg.symbol)):
            moved = True

    if moved:
        return True, "quotes are live"
    last = get_tick(cfg.symbol)
    stamp = datetime.fromtimestamp(last.time).strftime("%Y-%m-%d %H:%M:%S")
    return False, (f"quotes frozen across {samples} samples {gap:.0f}s apart "
                   f"(last quote {stamp} server time) - market is closed")


# --------------------------------------------------------------------------- #
# preflight: the check that stops a too-tight gold stop from bleeding money
# --------------------------------------------------------------------------- #
def preflight_check(cfg: TradeConfig, spec: SymbolSpec,
                    market_open: bool = True) -> list[str]:
    """Validate the configured distances against real broker constraints.

    Returns a list of blocking problems (empty list == good to trade). While
    the market is closed the reported spread is stale or artificially padded,
    so spread-based complaints are downgraded to warnings instead of blocking.
    """
    problems: list[str] = []
    point = spec.point
    sl_dist = cfg.sl_distance(point)
    trail_dist = cfg.trail_distance(point)
    sl_points = sl_dist / point
    spread_price = spec.spread_points * point
    min_stop_price = spec.stops_level_points * point

    log.info(
        "%s spec: point=%g digits=%d spread=%d pts (%.2f) stops_level=%d pts (%.2f)",
        spec.name, point, spec.digits, spec.spread_points, spread_price,
        spec.stops_level_points, min_stop_price,
    )
    log.info(
        "Configured distances (%s units): SL=%.2f units = %.2f price = %.0f broker points; "
        "trail=%.2f price",
        cfg.distance_unit, cfg.stop_loss_units, sl_dist, sl_points, trail_dist,
    )

    def spread_issue(msg: str) -> None:
        """Block on a spread problem when live; only warn when the market is shut."""
        if market_open:
            problems.append(msg)
        else:
            log.warning("%s (market closed - spread reading is unreliable, "
                        "re-check when the session opens)", msg)

    if spec.stops_level_points and sl_dist < min_stop_price:
        problems.append(
            f"Stop-loss distance {sl_dist:.2f} is inside the broker's minimum stop "
            f"distance of {min_stop_price:.2f} ({spec.stops_level_points} points). "
            f"Orders will be rejected with 'Invalid stops'."
        )
    if sl_dist <= spread_price:
        spread_issue(
            f"Stop-loss distance {sl_dist:.2f} is smaller than the current spread "
            f"{spread_price:.2f} ({spec.spread_points} points). A buy would be stopped "
            f"out the instant it opens."
        )
    elif sl_dist < 2 * spread_price:
        log.warning(
            "Stop-loss (%.2f) is less than 2x the spread (%.2f) - expect frequent "
            "spread-driven stop-outs.", sl_dist, spread_price
        )
    if trail_dist <= spread_price:
        spread_issue(
            f"Trailing distance {trail_dist:.2f} is smaller than the spread "
            f"{spread_price:.2f}; the trailing stop would sit on top of the price."
        )
    if cfg.lots < spec.volume_min:
        problems.append(
            f"Lot size {cfg.lots} is below the broker minimum {spec.volume_min}."
        )

    if problems:
        log.error("PREFLIGHT FAILED - refusing to trade with these distances:")
        for p in problems:
            log.error("  * %s", p)
        log.error(
            "  Fix: widen the distances or switch distance_unit (currently %r). "
            "See the docstring in config.py.", cfg.distance_unit,
        )
    return problems


# --------------------------------------------------------------------------- #
# market data
# --------------------------------------------------------------------------- #
def get_rates(symbol: str, tf_name: str, count: int) -> pd.DataFrame:
    m = mt5()
    rates = m.copy_rates_from_pos(symbol, timeframe(tf_name), 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"copy_rates_from_pos({symbol},{tf_name}) failed: {m.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.set_index("time").sort_index()
    return df[["open", "high", "low", "close", "tick_volume", "spread"]]


def get_closed_bars(symbol: str, tf_name: str, count: int) -> pd.DataFrame:
    """Rates with the still-forming final bar dropped - no repainting."""
    return get_rates(symbol, tf_name, count + 1).iloc[:-1]


def get_tick(symbol: str):
    m = mt5()
    tick = m.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick({symbol}) failed: {m.last_error()}")
    return tick


# --------------------------------------------------------------------------- #
# orders and positions
# --------------------------------------------------------------------------- #
def _pick_filling_mode(spec: SymbolSpec):
    m = mt5()
    # SYMBOL_FILLING_FOK = 1, SYMBOL_FILLING_IOC = 2 (bit flags)
    if spec.filling_mode & 1:
        return m.ORDER_FILLING_FOK
    if spec.filling_mode & 2:
        return m.ORDER_FILLING_IOC
    return m.ORDER_FILLING_RETURN


def position_size_for(spec: SymbolSpec, sl_distance: float, *, lots: float,
                       use_risk_percent: bool = False, risk_percent: float = 0.2,
                       max_lot_size: float = 5.0, equity: float = 0.0) -> float:
    """Pure lot-sizing math: fixed `lots`, or a size derived from `equity`.

    Takes equity as a parameter (rather than calling account_equity() itself)
    so it can be unit-tested, and reused by callers such as the Telegram
    copier, without an MT5 connection. Risk-percent sizing grows the lot with
    the account and shrinks it after a drawdown, which is what keeps risk per
    trade a constant fraction of the daily loss cap as the balance changes.
    """
    if not use_risk_percent:
        return float(lots)
    if equity <= 0 or spec.tick_size <= 0 or spec.tick_value <= 0 or sl_distance <= 0:
        return float(lots)
    loss_per_lot = (sl_distance / spec.tick_size) * spec.tick_value
    calc = (equity * risk_percent / 100.0) / loss_per_lot
    step = spec.volume_step or 0.01
    calc = (calc // step) * step
    calc = max(spec.volume_min, min(calc, spec.volume_max, max_lot_size))
    return round(calc, 2)


def position_size(cfg: TradeConfig, spec: SymbolSpec, sl_distance: float) -> float:
    """Fixed lots, or a size derived from equity when use_risk_percent is set."""
    equity = account_equity() if cfg.use_risk_percent else 0.0
    return position_size_for(
        spec, sl_distance, lots=cfg.lots, use_risk_percent=cfg.use_risk_percent,
        risk_percent=cfg.risk_percent, max_lot_size=cfg.max_lot_size, equity=equity,
    )


def open_position(cfg: TradeConfig, spec: SymbolSpec, direction: str, dry_run: bool):
    """Send a market order with the configured SL (and optional TP)."""
    m = mt5()
    tick = get_tick(cfg.symbol)
    point = spec.point

    is_buy = direction == "buy"
    price = tick.ask if is_buy else tick.bid
    sl_dist = cfg.sl_distance(point)
    lots = position_size(cfg, spec, sl_dist)
    tp_dist = cfg.tp_distance(point)

    sl = round(price - sl_dist if is_buy else price + sl_dist, spec.digits)
    tp = 0.0
    if tp_dist > 0:
        tp = round(price + tp_dist if is_buy else price - tp_dist, spec.digits)

    request = {
        "action": m.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": float(lots),
        "type": m.ORDER_TYPE_BUY if is_buy else m.ORDER_TYPE_SELL,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": cfg.deviation_points,
        "magic": cfg.magic,
        "comment": cfg.comment,
        "type_time": m.ORDER_TIME_GTC,
        "type_filling": _pick_filling_mode(spec),
    }

    if dry_run:
        log.info(
            "[DRY-RUN] would send %s %.2f %s @ %.*f sl=%.*f tp=%s",
            direction.upper(), lots, cfg.symbol, spec.digits, price,
            spec.digits, sl, f"{tp:.{spec.digits}f}" if tp else "none",
        )
        return None

    result = m.order_send(request)
    if result is None:
        log.error("order_send returned None: %s", m.last_error())
        return None
    # A partial fill (DONE_PARTIAL) opened a real position - not a rejection.
    if result.retcode not in (m.TRADE_RETCODE_DONE, getattr(m, "TRADE_RETCODE_DONE_PARTIAL", 10010)):
        market_closed = getattr(m, "TRADE_RETCODE_MARKET_CLOSED", 10018)
        if result.retcode == market_closed:
            log.warning("Order not placed: the market is closed. The signal stands; "
                        "the bot will keep evaluating and can trade when it reopens.")
        else:
            log.error("Order rejected: retcode=%s comment=%s", result.retcode, result.comment)
        return result

    log.info(
        "OPENED %s %.2f %s @ %.*f sl=%.*f ticket=%s",
        direction.upper(), result.volume, cfg.symbol, spec.digits, result.price,
        spec.digits, sl, result.order,
    )
    return result


def open_signal_position(cfg, spec: SymbolSpec, direction: str, volume: float,
                          sl: float, tp: float, dry_run: bool):
    """Like open_position, but volume/SL/TP come from an already-verified copied
    signal instead of being derived from a TradeConfig's distance settings.

    `cfg` only needs `.symbol`, `.magic`, `.deviation_points` and `.comment` -
    CopierConfig carries the same names so it can be passed here directly.
    Always re-fetches the current tick rather than trusting a stale signal
    price, exactly like open_position.
    """
    m = mt5()
    tick = get_tick(cfg.symbol)
    is_buy = direction == "buy"
    price = tick.ask if is_buy else tick.bid

    request = {
        "action": m.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": float(volume),
        "type": m.ORDER_TYPE_BUY if is_buy else m.ORDER_TYPE_SELL,
        "price": price,
        "sl": round(sl, spec.digits) if sl else 0.0,
        "tp": round(tp, spec.digits) if tp else 0.0,
        "deviation": cfg.deviation_points,
        "magic": cfg.magic,
        "comment": cfg.comment,
        "type_time": m.ORDER_TIME_GTC,
        "type_filling": _pick_filling_mode(spec),
    }

    if dry_run:
        log.info(
            "[DRY-RUN] would copy %s %.2f %s @ %.*f sl=%s tp=%s",
            direction.upper(), volume, cfg.symbol, spec.digits, price,
            f"{request['sl']:.{spec.digits}f}" if request["sl"] else "none",
            f"{request['tp']:.{spec.digits}f}" if request["tp"] else "none",
        )
        return None

    result = m.order_send(request)
    if result is None:
        log.error("order_send returned None: %s", m.last_error())
        return None
    # A partial fill (DONE_PARTIAL) opened a real position - not a rejection.
    if result.retcode not in (m.TRADE_RETCODE_DONE, getattr(m, "TRADE_RETCODE_DONE_PARTIAL", 10010)):
        market_closed = getattr(m, "TRADE_RETCODE_MARKET_CLOSED", 10018)
        if result.retcode == market_closed:
            log.warning("Copied order not placed: the market is closed.")
        else:
            log.error("Copied order rejected: retcode=%s comment=%s",
                       result.retcode, result.comment)
        return result

    log.info(
        "COPIED %s %.2f %s @ %.*f sl=%s tp=%s ticket=%s",
        direction.upper(), result.volume, cfg.symbol, spec.digits, result.price,
        f"{request['sl']:.{spec.digits}f}" if request["sl"] else "none",
        f"{request['tp']:.{spec.digits}f}" if request["tp"] else "none",
        result.order,
    )
    return result


def get_positions(cfg: TradeConfig) -> list:
    m = mt5()
    positions = m.positions_get(symbol=cfg.symbol)
    if positions is None:
        return []
    return [p for p in positions if p.magic == cfg.magic]


def modify_stop(cfg: TradeConfig, position, new_sl: float, digits: int, dry_run: bool) -> bool:
    m = mt5()
    new_sl = round(new_sl, digits)
    if dry_run:
        log.info("[DRY-RUN] would trail ticket %s sl %.*f -> %.*f",
                 position.ticket, digits, position.sl, digits, new_sl)
        return True

    request = {
        "action": m.TRADE_ACTION_SLTP,
        "symbol": position.symbol,
        "position": position.ticket,
        "sl": new_sl,
        "tp": position.tp,
        "magic": cfg.magic,
    }
    result = m.order_send(request)
    if result is None or result.retcode != m.TRADE_RETCODE_DONE:
        log.warning("Trail modify failed for %s: %s", position.ticket,
                    getattr(result, "comment", m.last_error()))
        return False
    log.info("TRAILED ticket %s sl -> %.*f", position.ticket, digits, new_sl)
    return True


def close_position(cfg: TradeConfig, spec: SymbolSpec, position, dry_run: bool) -> bool:
    m = mt5()
    tick = get_tick(cfg.symbol)
    is_buy = position.type == m.POSITION_TYPE_BUY
    request = {
        "action": m.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": m.ORDER_TYPE_SELL if is_buy else m.ORDER_TYPE_BUY,
        "position": position.ticket,
        "price": tick.bid if is_buy else tick.ask,
        "deviation": cfg.deviation_points,
        "magic": cfg.magic,
        "comment": "close " + cfg.comment,
        "type_time": m.ORDER_TIME_GTC,
        "type_filling": _pick_filling_mode(spec),
    }
    if dry_run:
        log.info("[DRY-RUN] would close ticket %s", position.ticket)
        return True
    result = m.order_send(request)
    ok = result is not None and result.retcode == m.TRADE_RETCODE_DONE
    log.info("CLOSED ticket %s: %s", position.ticket, "ok" if ok else "FAILED")
    return ok


def account_equity() -> float:
    info = mt5().account_info()
    return float(info.equity) if info else 0.0

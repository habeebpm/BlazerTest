"""
Thin wrapper around the MetaTrader5 Python package.

The MetaTrader5 package only runs on Windows with a MetaTrader 5 terminal
installed, so it is imported lazily: every other module in this project stays
importable (and testable) on Linux/macOS.
"""
from __future__ import annotations

import logging
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
            raise RuntimeError(
                "The MetaTrader5 package is not available. It requires Windows "
                "with the MetaTrader 5 terminal installed:  pip install MetaTrader5"
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
# preflight: the check that stops a 6-point gold stop from bleeding money
# --------------------------------------------------------------------------- #
def preflight_check(cfg: TradeConfig, spec: SymbolSpec) -> list[str]:
    """Validate the configured distances against real broker constraints.

    Returns a list of blocking problems (empty list == good to trade).
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

    if spec.stops_level_points and sl_dist < min_stop_price:
        problems.append(
            f"Stop-loss distance {sl_dist:.2f} is inside the broker's minimum stop "
            f"distance of {min_stop_price:.2f} ({spec.stops_level_points} points). "
            f"Orders will be rejected with 'Invalid stops'."
        )
    if sl_dist <= spread_price:
        problems.append(
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
        problems.append(
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
        if cfg.distance_unit == "point":
            log.error(
                "  Fix: set distance_unit='usd' (SL $%.2f, trail $%.2f) or raise the "
                "unit counts. See the docstring in config.py.",
                cfg.stop_loss_units, cfg.trailing_stop_units,
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


def open_position(cfg: TradeConfig, spec: SymbolSpec, direction: str, dry_run: bool):
    """Send a market order with the configured SL (and optional TP)."""
    m = mt5()
    tick = get_tick(cfg.symbol)
    point = spec.point

    is_buy = direction == "buy"
    price = tick.ask if is_buy else tick.bid
    sl_dist = cfg.sl_distance(point)
    tp_dist = cfg.tp_distance(point)

    sl = round(price - sl_dist if is_buy else price + sl_dist, spec.digits)
    tp = 0.0
    if tp_dist > 0:
        tp = round(price + tp_dist if is_buy else price - tp_dist, spec.digits)

    request = {
        "action": m.TRADE_ACTION_DEAL,
        "symbol": cfg.symbol,
        "volume": float(cfg.lots),
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
            direction.upper(), cfg.lots, cfg.symbol, spec.digits, price,
            spec.digits, sl, f"{tp:.{spec.digits}f}" if tp else "none",
        )
        return None

    result = m.order_send(request)
    if result is None:
        log.error("order_send returned None: %s", m.last_error())
        return None
    if result.retcode != m.TRADE_RETCODE_DONE:
        log.error("Order rejected: retcode=%s comment=%s", result.retcode, result.comment)
        return result

    log.info(
        "OPENED %s %.2f %s @ %.*f sl=%.*f ticket=%s",
        direction.upper(), result.volume, cfg.symbol, spec.digits, result.price,
        spec.digits, sl, result.order,
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

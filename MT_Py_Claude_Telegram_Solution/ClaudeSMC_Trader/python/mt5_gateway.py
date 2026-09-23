"""
Self-contained MetaTrader 5 wrapper for this solution.

Deliberately independent of the sibling ../../python/ package (the Telegram
copier stack) - this is a standalone solution, so it doesn't reach across
directories for a config type built for a different bot. The MetaTrader5
package is Windows-only and imported lazily, same convention as the rest of
this repo: everything except live MT5 calls stays importable/testable on
any platform (see selftest.py, which mocks this module entirely).
"""
from __future__ import annotations

import logging
import os
import platform
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

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
            raise RuntimeError(
                f"The MetaTrader5 package is not available on this platform ({system}). "
                "It requires Windows with the MetaTrader 5 terminal installed: "
                "pip install MetaTrader5. The feature/indicator code and selftest.py "
                "run fine without it - only main.py's live loop needs it."
            ) from exc
        _mt5 = m
    return _mt5


TIMEFRAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1",
}


def timeframe_const(name: str):
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


def connect(login: int | None = None, password: str | None = None,
            server: str | None = None, terminal_path: str | None = None) -> None:
    m = mt5()
    kwargs = {}
    if terminal_path:
        kwargs["path"] = terminal_path
    if login:
        kwargs.update(login=login, password=password, server=server)
    if not m.initialize(**kwargs):
        raise RuntimeError(f"MetaTrader5 initialize() failed: {m.last_error()}")


def symbol_spec(symbol: str) -> SymbolSpec:
    m = mt5()
    info = m.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"symbol_info({symbol}) returned None: {m.last_error()}")
    if not info.visible and not m.symbol_select(symbol, True):
        raise RuntimeError(f"Could not add {symbol} to Market Watch: {m.last_error()}")
    return SymbolSpec(
        name=symbol, point=info.point, digits=info.digits,
        stops_level_points=info.trade_stops_level, spread_points=info.spread,
        volume_min=info.volume_min, volume_max=info.volume_max, volume_step=info.volume_step,
        tick_value=info.trade_tick_value, tick_size=info.trade_tick_size,
    )


def get_bars(symbol: str, timeframe_name: str, count: int) -> pd.DataFrame:
    """Closed + currently-forming bars, oldest first. Callers that need only
    CLOSED bars should drop the last row (see market_intel.wait_for_new_bar).
    """
    m = mt5()
    rates = m.copy_rates_from_pos(symbol, timeframe_const(timeframe_name), 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"copy_rates_from_pos({symbol}, {timeframe_name}) failed: {m.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.rename(columns={"tick_volume": "volume"})


def get_bars_range(symbol: str, timeframe_name: str, start, end) -> pd.DataFrame:
    """Every bar between `start` and `end` (both timezone-aware datetimes,
    or anything pandas.Timestamp accepts) - for backtest.py, which needs a
    whole historical window rather than "the most recent N bars". Unlike
    get_bars(), every returned row is a genuinely CLOSED historical bar -
    there's no still-forming bar to drop.
    """
    m = mt5()
    rates = m.copy_rates_range(symbol, timeframe_const(timeframe_name),
                                pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime())
    if rates is None:
        raise RuntimeError(f"copy_rates_range({symbol}, {timeframe_name}) failed: {m.last_error()}")
    df = pd.DataFrame(rates)
    if len(df) == 0:
        return df.assign(time=pd.Series(dtype="datetime64[ns, UTC]"))
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.rename(columns={"tick_volume": "volume"})


def get_tick(symbol: str):
    m = mt5()
    tick = m.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick({symbol}) failed: {m.last_error()}")
    return tick


def now() -> datetime:
    """The gateway's own notion of "now" - real wall-clock time here, but
    backtest.HistoricalGateway overrides this with its simulated replay
    clock instead. executor.gate() calls THIS (via `gateway.now()`) rather
    than datetime.now() directly, specifically so a backtest replaying
    historical data checks config.py's news_blackout_windows against the
    bar being evaluated, not against the real date the backtest happens to
    be run on - the same no-lookahead principle every other gateway call
    here already follows (get_bars/get_tick never leak future/real-time
    data into a simulated evaluation).
    """
    return datetime.now(timezone.utc)


def account_equity() -> float:
    m = mt5()
    info = m.account_info()
    if info is None:
        raise RuntimeError(f"account_info() failed: {m.last_error()}")
    return float(info.equity)


def write_common_file(filename: str, text: str) -> None:
    """Writes `text` into MT5's shared Common\\Files folder - the ONE
    location both this Python process and a running MQL5 EA's FileOpen(...,
    FILE_COMMON) can both reach (MQL5's file sandbox otherwise only sees
    each EA's own MQL5/Files directory, never anything Python writes).
    Used by main.py to hand UnifiedTrader_EA.mq5's "Why" Telegram command
    the latest Claude verdict text - see that EA's ReadLastVerdictFile()
    and InpLastVerdictFilename (must match config.py's
    last_verdict_filename). Plain text only - MQL5 reads it with
    FILE_TXT|FILE_ANSI, so anything outside that encoding would come back
    mangled on the EA side.
    """
    m = mt5()
    info = m.terminal_info()
    if info is None:
        raise RuntimeError(f"terminal_info() failed: {m.last_error()}")
    files_dir = os.path.join(info.commondata_path, "Files")
    os.makedirs(files_dir, exist_ok=True)
    with open(os.path.join(files_dir, filename), "w", encoding="ascii", errors="replace") as f:
        f.write(text)


def open_positions(symbol: str, magic: int) -> list:
    """This system's own open positions (filtered by magic number), as plain
    dicts - never touches a position opened by anything else on the account.
    """
    m = mt5()
    positions = m.positions_get(symbol=symbol)
    if positions is None:
        return []
    out = []
    for p in positions:
        if p.magic != magic:
            continue
        out.append({
            "ticket": p.ticket,
            "direction": "buy" if p.type == m.POSITION_TYPE_BUY else "sell",
            "volume": p.volume,
            "price_open": p.price_open,
            "sl": p.sl,
            "tp": p.tp,
        })
    return out


def count_same_direction(symbol: str, magic: int, direction: str, additional_magics=()) -> int:
    """Same-direction open positions under `magic`, plus (if given) any of
    `additional_magics` too - so a shared position cap (see AdvisorConfig.
    shared_cap_magic_numbers) can count another system's positions on this
    account without this system needing to know anything about that system
    beyond its magic number. Built on open_positions() (one positions_get()
    call per magic, rather than one filtered by a magic set) so the two
    never drift apart on what counts as "this magic's position" or how a
    position's direction is derived.
    """
    return sum(1 for magic_id in {magic, *additional_magics}
              for p in open_positions(symbol, magic_id) if p["direction"] == direction)


def recent_closed_trades(symbol: str, magic: int, count: int = 10,
                          lookback_days: int = 14) -> list[dict]:
    """This system's own closed trades (filtered by magic number), newest
    first - built from MT5's own deal history rather than logs/trades.csv,
    so it reflects real broker fills (including anything the MQL5 trade
    manager closed) whether or not this Python process was running at the
    time. Each closed position produces one DEAL_ENTRY_OUT deal; its
    profit+swap+commission is that trade's net P&L. Used by market_intel.
    recent_performance_summary() to give Claude qualitative context on
    recent performance - never touches trading decisions on its own.
    """
    m = mt5()
    now = datetime.now(timezone.utc)
    deals = m.history_deals_get(now - timedelta(days=lookback_days), now)
    if deals is None:
        return []
    out = []
    for d in deals:
        if d.symbol != symbol or d.magic != magic or d.entry != m.DEAL_ENTRY_OUT:
            continue
        out.append({
            "time": datetime.fromtimestamp(d.time, tz=timezone.utc),
            # The CLOSING deal's type is the opposite of the position's own
            # direction (closing a buy position is a sell deal, and vice
            # versa) - flipped here so the direction reported is the
            # position's, not the deal's.
            "direction": "buy" if d.type == m.DEAL_TYPE_SELL else "sell",
            "pnl_dollars": float(d.profit + d.swap + d.commission),
            "ticket": d.position_id,
        })
    out.sort(key=lambda r: r["time"], reverse=True)
    return out[:count]


def price_distance_for_dollars(spec: SymbolSpec, dollars: float, lots: float) -> float:
    """Price distance that turns into exactly `dollars` of account P&L at
    `lots` volume, derived from the broker's own tick value/size rather than
    assuming a contract size - the same formula the Telegram copier's
    position_size_for() inverts for lot sizing (see ../../python/mt5_client.py).
    """
    if spec.tick_size <= 0 or spec.tick_value <= 0 or lots <= 0:
        raise ValueError("tick_size, tick_value and lots must all be positive")
    return dollars * spec.tick_size / (spec.tick_value * lots)


def place_market_order(spec: SymbolSpec, direction: str, lots: float, sl_price: float,
                        tp_price: float, magic: int, comment: str, deviation_points: int,
                        dry_run: bool):
    """Returns the MT5 order_send result, or None in dry-run."""
    m = mt5()
    tick = get_tick(spec.name)
    price = tick.ask if direction == "buy" else tick.bid
    order_type = m.ORDER_TYPE_BUY if direction == "buy" else m.ORDER_TYPE_SELL

    if dry_run:
        log.info("[DRY-RUN] would open %s %.2f lots %s @ %.2f sl=%.2f tp=%.2f",
                  direction.upper(), lots, spec.name, price, sl_price, tp_price)
        return None

    request = {
        "action": m.TRADE_ACTION_DEAL,
        "symbol": spec.name,
        "volume": lots,
        "type": order_type,
        "price": price,
        "sl": round(sl_price, spec.digits),
        "tp": round(tp_price, spec.digits),
        "deviation": deviation_points,
        "magic": magic,
        "comment": comment,
        "type_time": m.ORDER_TIME_GTC,
        "type_filling": m.ORDER_FILLING_IOC,
    }
    result = m.order_send(request)
    if result is None:
        log.error("order_send returned None: %s", m.last_error())
    return result

"""
Minimal, self-contained MetaTrader 5 wrapper for the Drive price export - deliberately
NOT importing app/mt5_gateway.py or any other folder
mt5_client.py, matching this repo's convention of independent solutions
that don't reach across directories for code built for a different tool
(see mt5_gateway.py's own docstring). This one only needs three things:
connect, read the symbol spec (for quote precision), and read bars - no
order placement, no trailing, no position management.

The MetaTrader5 package is Windows-only and imported lazily, same
convention as the rest of this repo, so this module (and exporter.py,
which only depends on this file's return types) stays importable and
testable on any platform - only the live export loop needs a real
terminal.
"""
from __future__ import annotations

import platform
import time
from dataclasses import dataclass

import pandas as pd

_mt5 = None


def mt5():
    global _mt5
    if _mt5 is None:
        try:
            import MetaTrader5 as m
        except ImportError as exc:  # pragma: no cover - platform dependent
            system = platform.system()
            raise RuntimeError(
                f"The MetaTrader5 package is not available on this platform ({system}). "
                "It requires Windows with the MetaTrader 5 terminal installed: "
                "pip install MetaTrader5. exporter.py's formatting logic and selftest.py "
                "run fine without it - only xtr_export.py's live loop needs it."
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
    digits: int


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
    return SymbolSpec(name=symbol, digits=info.digits)


def get_bars(symbol: str, timeframe_name: str, count: int) -> pd.DataFrame:
    """Closed + currently-forming bars, oldest first, `time` as raw broker-
    server epoch seconds (NOT necessarily UTC - see exporter.py's
    detect_broker_utc_offset_seconds, which converts this to genuine UTC).
    Callers that need only closed bars must drop the last row - the same
    convention as app/mt5_gateway.get_bars().
    """
    m = mt5()
    rates = m.copy_rates_from_pos(symbol, timeframe_const(timeframe_name), 0, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"copy_rates_from_pos({symbol}, {timeframe_name}) failed: {m.last_error()}")
    df = pd.DataFrame(rates)
    return df.rename(columns={"tick_volume": "volume"})


def broker_server_epoch_now(symbol: str) -> int:
    """The broker server's own current time (epoch seconds), read from the
    live tick rather than any bar - used to detect the broker's UTC offset.
    """
    m = mt5()
    tick = m.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"symbol_info_tick({symbol}) failed: {m.last_error()}")
    return int(tick.time)


def local_utc_epoch_now() -> int:
    """This machine's own real UTC time - assumes the machine's clock is
    correct (NTP-synced), which is what detect_broker_utc_offset_seconds
    relies on to back out the broker's offset from real UTC.
    """
    return int(time.time())

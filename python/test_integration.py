"""
Integration test: drives trader.py against a fake MetaTrader5 module.

Runs anywhere (no Windows, no terminal, no broker) and exercises the real
code paths - preflight, signal evaluation, order construction, the trailing
stop and the entry guards - by injecting a stub into sys.modules.

    python test_integration.py
"""
import pathlib
import sys, types, time
from datetime import datetime, timedelta
import numpy as np, pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# ---------------- build a fake MetaTrader5 module ----------------
m = types.ModuleType("MetaTrader5")
for i, name in enumerate(["TIMEFRAME_M1","TIMEFRAME_M5","TIMEFRAME_M15","TIMEFRAME_M30",
                          "TIMEFRAME_H1","TIMEFRAME_H4","TIMEFRAME_D1","TIMEFRAME_W1"]):
    setattr(m, name, i+1)
m.TRADE_ACTION_DEAL, m.TRADE_ACTION_SLTP = 1, 2
m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 0, 1
m.POSITION_TYPE_BUY, m.POSITION_TYPE_SELL = 0, 1
m.ORDER_TIME_GTC = 0
m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN = 0, 1, 2
m.TRADE_RETCODE_DONE = 10009

SPREAD_POINTS = 25
STOPS_LEVEL = 0
sent = []

def realistic(n, start, drift, sigma, seed):
    rng = np.random.default_rng(seed)
    return start + np.cumsum(drift + rng.normal(0.0, sigma, n))

PRICES_M15 = realistic(900, 2000.0, 0.20, 1.5, 17)

def _rates(count, closes, minutes):
    closes = closes[-count:]
    end = datetime(2026, 9, 11, 15, 0)
    times = [(end - timedelta(minutes=minutes*(len(closes)-1-i))).timestamp() for i in range(len(closes))]
    rng = np.random.default_rng(1)
    noise = np.abs(rng.normal(0, 0.4, len(closes)))
    return np.array(
        [(t, c-0.1, c+nz, c-nz, c, 100, SPREAD_POINTS, 0)
         for t, c, nz in zip(times, closes, noise)],
        dtype=[("time","<i8"),("open","<f8"),("high","<f8"),("low","<f8"),
               ("close","<f8"),("tick_volume","<i8"),("spread","<i4"),("real_volume","<i8")])

m.initialize = lambda **kw: True
m.shutdown = lambda: None
m.last_error = lambda: (0, "ok")
m.symbol_select = lambda s, on: True
m.account_info = lambda: types.SimpleNamespace(login=123, server="Mock-Demo", balance=10000.0,
                                               equity=10000.0, currency="USD", trade_allowed=True)
m.symbol_info = lambda s: types.SimpleNamespace(
    name=s, point=0.01, digits=2, trade_stops_level=STOPS_LEVEL, spread=SPREAD_POINTS,
    volume_min=0.01, volume_max=100.0, volume_step=0.01,
    trade_tick_value=1.0, trade_tick_size=0.01, filling_mode=1)
m.symbol_info_tick = lambda s: types.SimpleNamespace(
    bid=PRICES_M15[-1], ask=PRICES_M15[-1] + SPREAD_POINTS*0.01, time=0)

def copy_rates_from_pos(symbol, tf, start, count):
    return _rates(count, PRICES_M15, 15) if tf == m.TIMEFRAME_M15 else _rates(count, PRICES_M15[::16], 240)
m.copy_rates_from_pos = copy_rates_from_pos

POSITIONS = []
m.positions_get = lambda **kw: list(POSITIONS)
def order_send(req):
    sent.append(req)
    return types.SimpleNamespace(retcode=m.TRADE_RETCODE_DONE, order=555, price=req.get("price", 0.0),
                                 volume=req.get("volume", 0.0), comment="done")
m.order_send = order_send
sys.modules["MetaTrader5"] = m

# ---------------- exercise the bot ----------------
import mt5_client as mc, trader
from config import TradeConfig
trader.setup_logging()

print("\n=== 1. preflight with the literal 6-point config ===")
cfg = TradeConfig()
bot = trader.Bot(cfg, dry_run=True)
problems = bot.start()
print("problems:", len(problems))
for p in problems: print("   *", p)
assert problems, "preflight MUST reject a 6-point stop against a 25-point spread"

print("\n=== 2. preflight with distance_unit='usd' ===")
cfg2 = TradeConfig(distance_unit="usd")
bot2 = trader.Bot(cfg2, dry_run=True)
problems2 = bot2.start()
print("problems:", problems2)
assert not problems2, "a $6 stop should pass preflight"

print("\n=== 3. signal evaluation against mock bars ===")
sig = bot2.latest_signal()
print("  ", sig.summary())

print("\n=== 4. dry-run entry (forced signal) ===")
import strategy as st
bot2.last_bar_time = None
real_eval = bot2.latest_signal
def forced():
    s = real_eval()
    s.direction = "buy"
    s.buy.trend = s.buy.momentum = s.buy.strength = True
    return s
bot2.latest_signal = forced
bot2.check_for_entry()
print("   orders sent in dry-run:", len(sent), "(expected 0)")
assert len(sent) == 0

print("\n=== 5. LIVE entry path ===")
live = trader.Bot(cfg2, dry_run=False)
live.start(); live.latest_signal = forced; live.last_bar_time = None
live.check_for_entry()
assert len(sent) == 1, sent
req = sent[0]
entry = req["price"]
print(f"   order: {'BUY' if req['type']==0 else 'SELL'} {req['volume']} lots @ {entry:.2f} "
      f"sl={req['sl']:.2f} (distance ${entry-req['sl']:.2f}) magic={req['magic']}")
# SL is normalized to the symbol digits (2), so allow half a cent of rounding
assert abs((entry - req["sl"]) - 6.00) <= 0.005 + 1e-9, "SL must be $6.00 below entry"
assert req["volume"] == 0.02, "lot size must be 0.02"

print("\n=== 6. trailing stop on a live position ===")
POSITIONS.append(types.SimpleNamespace(ticket=555, symbol="XAUUSD", type=m.POSITION_TYPE_BUY,
                                       volume=0.02, price_open=entry, sl=req["sl"], tp=0.0, magic=cfg2.magic))
sent.clear()
for bump in (1.0, 3.0, 5.0, 4.0, 9.0):
    m.symbol_info_tick = (lambda b: (lambda s: types.SimpleNamespace(
        bid=entry + b, ask=entry + b + SPREAD_POINTS*0.01, time=0)))(bump)
    live.manage_trailing()
    if sent:
        new_sl = sent[-1]["sl"]
        POSITIONS[0].sl = new_sl
    print(f"   price +${bump:.2f} -> sl {POSITIONS[0].sl:.2f} "
          f"({'trailed' if sent else 'unchanged'}, locked {POSITIONS[0].sl-entry:+.2f})")
    sent.clear()
assert abs(POSITIONS[0].sl - (entry + 9.0 - 3.0)) <= 0.005 + 1e-9, POSITIONS[0].sl
print("   final stop is $3.00 behind the high -> $6.00 profit locked")

print("\n=== 7. guards ===")
print("   entry_blocked with 1 open position:", live.entry_blocked())
assert live.entry_blocked() is not None

print("\nALL MOCK-MT5 INTEGRATION CHECKS PASSED")

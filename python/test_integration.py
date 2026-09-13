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
m.TRADE_RETCODE_MARKET_CLOSED = 10018
m.SYMBOL_TRADE_MODE_DISABLED, m.SYMBOL_TRADE_MODE_LONGONLY = 0, 1
m.SYMBOL_TRADE_MODE_SHORTONLY, m.SYMBOL_TRADE_MODE_CLOSEONLY = 2, 3
m.SYMBOL_TRADE_MODE_FULL = 4

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
    trade_tick_value=1.0, trade_tick_size=0.01, filling_mode=1,
    trade_mode=m.SYMBOL_TRADE_MODE_FULL)

# MARKET_OPEN drives whether the quote timestamp advances, which is exactly how
# the bot decides the session is shut.
MARKET_OPEN = True
_frozen_stamp = time.time()

def _tick(sym):
    stamp = time.time() if MARKET_OPEN else _frozen_stamp
    return types.SimpleNamespace(bid=PRICES_M15[-1],
                                 ask=PRICES_M15[-1] + SPREAD_POINTS*0.01,
                                 time=int(stamp), time_msc=int(stamp*1000))
m.symbol_info_tick = lambda s: _tick(s)

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

print("\n=== 1. preflight rejects a too-tight stop (6 broker points) ===")
tight = TradeConfig(distance_unit="point", stop_loss_units=6.0, trailing_stop_units=3.0)
bot = trader.Bot(tight, dry_run=True)
problems = bot.start(probe_market=False)
print("problems:", len(problems))
for p in problems: print("   *", p)
assert problems, "preflight MUST reject a 6-point stop against a 25-point spread"

print("\n=== 2. preflight accepts the shipped default (60 pips / 30 pips) ===")
cfg2 = TradeConfig()
print(f"   SL {cfg2.stop_loss_units:g} {cfg2.distance_unit} = ${cfg2.sl_distance(0.01):.2f}, "
      f"trail {cfg2.trailing_stop_units:g} {cfg2.distance_unit} = ${cfg2.trail_distance(0.01):.2f}")
bot2 = trader.Bot(cfg2, dry_run=True)
problems2 = bot2.start(probe_market=False)
print("problems:", problems2)
assert not problems2, "60 pips / 30 pips should pass preflight"

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
live.start(probe_market=False); live.latest_signal = forced; live.last_bar_time = None
live.check_for_entry()
assert len(sent) == 1, sent
req = sent[0]
entry = req["price"]
print(f"   order: {'BUY' if req['type']==0 else 'SELL'} {req['volume']} lots @ {entry:.2f} "
      f"sl={req['sl']:.2f} (distance ${entry-req['sl']:.2f}) magic={req['magic']}")
# 60 pips = $6.00; SL is normalized to the symbol digits (2), so allow
# half a cent of rounding
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
m.symbol_info_tick = lambda s: _tick(s)   # restore the MARKET_OPEN-driven tick source

print("\n=== 7. guards ===")
print("   entry_blocked with 1 open position:", live.entry_blocked())
assert live.entry_blocked() is not None

print("\n=== 8. market closed (quotes frozen) ===")
import mt5_client as _mc
MARKET_OPEN = False
closed_bot = trader.Bot(cfg2, dry_run=False)
open_flag, reason = _mc.market_status(cfg2, samples=2, gap=0.05)
print("   market_status ->", open_flag, "|", reason.split(" (")[0])
assert open_flag is False, "frozen quotes must read as a closed market"

closed_bot.start(probe_market=False)
closed_bot.market_open = True          # pretend we started while it was open
closed_bot.quotes = _mc.QuoteMonitor(stale_after=0.0)
closed_bot.refresh_market_state()      # first sample
closed_bot.refresh_market_state()      # repeat sample -> frozen
print("   after frozen quotes, bot.market_open =", closed_bot.market_open)
assert closed_bot.market_open is False
print("   entry_blocked:", closed_bot.entry_blocked())
assert closed_bot.entry_blocked() == "market is closed"

POSITIONS.append(types.SimpleNamespace(ticket=777, symbol="XAUUSD", type=m.POSITION_TYPE_BUY,
                                       volume=0.02, price_open=2000.0, sl=1994.0, tp=0.0,
                                       magic=cfg2.magic))
sent.clear()
closed_bot.manage_trailing()
print("   trailing modifications attempted while closed:", len(sent), "(expected 0)")
assert len(sent) == 0

print("\n=== 9. preflight is advisory about spread while closed ===")
tight_closed = trader.Bot(tight, dry_run=True)
probs_closed = _mc.preflight_check(tight, tight_closed_spec := _mc.get_symbol_spec(tight),
                                   market_open=False)
print("   blocking problems while closed:", len(probs_closed), "(spread complaints demoted)")
assert len(probs_closed) < len(problems), "spread issues should not block while closed"

print("\n=== 10. quotes resume -> trading re-enables ===")
assert closed_bot.market_open is False, "should still be closed going in"
MARKET_OPEN = True
time.sleep(1.1)                       # let the mock clock advance a second
closed_bot.refresh_market_state()     # same monitor that was frozen
print("   bot.market_open =", closed_bot.market_open)
assert closed_bot.market_open is True, "moving quotes must re-enable trading"
print("   entry_blocked now:", closed_bot.entry_blocked())

print("\nALL MOCK-MT5 INTEGRATION CHECKS PASSED")

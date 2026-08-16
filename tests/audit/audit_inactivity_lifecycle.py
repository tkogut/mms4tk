#!/usr/bin/env python3
"""AUDIT 4/4 — Cykl zycia pozycji: TP na przeciwnej bandzie, negacja impulsu 50%,
stagnacja/drabinka wyjscia, Break-Even Escape. READ-ONLY, mocki."""
import asyncio
import sys
from unittest.mock import MagicMock, AsyncMock

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.analytics import Candle, SignalResult
from mms4tk.daemon import MMS4Daemon

# ── Neutralizacja efektow ubocznych: zadnych webhookow ani Telegramu ─────────
import mms4tk.hermes_webhook as _hw
class _NoopWH:
    def __init__(self, *a, **k): pass
    def __getattr__(self, n): return lambda *a, **k: None
    @staticmethod
    def send_telegram(*a, **k): return None
_hw.HermesWebhookClient = _NoopWH

R = []
def check(name, cond, info=""):
    R.append((name, bool(cond), info))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  :: {info}" if info else ""))

print("=" * 78)
print("AUDIT 4/4 — INACTIVITY MANAGER / CYKL ZYCIA POZYCJI")
print("=" * 78)

ATR = 100.0

# ── SCENARIUSZ B: impuls > 0.3*ATR + cofka > 50% ────────────────────────────
print("\n[B] Negacja impulsu 50% (LONG, entry=10000, ATR=100, min_impulse=30)")
m = MMS4InactivityManager()
m.track_position("SYM", 10000.0, 0.0, "long", ATR)
seq = [(10020.0, "impuls 20 < 30 -> brak reakcji"),
       (10050.0, "szczyt 10050, impuls 50 > 30"),
       (10030.0, "cofka do 10030; poziom negacji = 10025"),
       (10020.0, "ponizej 10025 -> negacja")]
acts = []
for px, note in seq:
    a = m.update_and_check("SYM", px, ATR, new_candle_closed=True)
    acts.append(a)
    print(f"   px={px:>9,.1f}  ->  {a.value:<15} ({note})")
check("T4.1 Negacja impulsu wyzwala SPLIT_50 przy zniesieniu >50%",
      StaleAction.SPLIT_50 in acts, f"akcje={[a.value for a in acts]}")
a2 = m.update_and_check("SYM", 10010.0, ATR, True)
check("T4.2 Po splicie kolejna negacja daje FORCE_CLOSE", a2 == StaleAction.FORCE_CLOSE, f"akcja={a2.value}")

# ── T4.2b Ile razy z rzedu emitowany jest SPLIT_50 z reguly negacji impulsu ──
rep = [m.update_and_check("SYM", 10015.0, ATR, True).value for _ in range(10)]
tr = m.get_tracker("SYM")
print(f"   10 kolejnych cykli po negacji: {rep}")
print(f"   tracker.is_split = {tr.is_split} (ustawiane TYLKO w galezi stagnacji, l.230)")
check("T4.2b [P0] Regula negacji impulsu nie zapetla zlecen redukcji",
      rep.count("SPLIT_50") <= 1,
      f"SPLIT_50 wyemitowany {rep.count('SPLIT_50')}/10 razy z rzedu; _check_impulse_negation NIGDY nie ustawia "
      "tracker.is_split=True, wiec daemon wysyla reduce_position(50%) w KAZDYM cyklu (co 15s) az do wyzerowania pozycji")

# ── T4.3 max_observed liczony z KAZDEGO tick-u, nie z High swiecy ──────────
m2 = MMS4InactivityManager()
m2.track_position("S2", 10000.0, 0.0, "long", ATR)
m2.update_and_check("S2", 10040.0, ATR, True)   # przelotny szczyt
a = m2.update_and_check("S2", 10015.0, ATR, True)  # zwykla korekta 25 pkt
m2b = MMS4InactivityManager(); m2b.set_symbol_config("S2b", {"impulse_min_atr": 0.8})
m2b.track_position("S2b", 10000.0, 0.0, "long", ATR)
m2b.update_and_check("S2b", 10040.0, ATR, True)
a_tuned = m2b.update_and_check("S2b", 10015.0, ATR, True)
check("T4.3 [P2] Prog impulsu jest konfigurowalny per symbol (0.3xATR bywa w szumie)",
      a == StaleAction.SPLIT_50 and a_tuned == StaleAction.HOLD,
      f"prog 0.3xATR -> {a.value}, prog 0.8xATR -> {a_tuned.value} (klucz impulse_min_atr / MMS4_IMPULSE_MIN_ATR)")

# ── T4.4 Zatrucie trackera przez signal.price = 0.0 ────────────────────────
m3 = MMS4InactivityManager()
m3.track_position("S3", 10000.0, 0.0, "short", ATR)
a0 = m3.update_and_check("S3", 0.0, ATR, True)       # degeneracyjny cykl (<2 swiec -> price=0.0)
t = m3.get_tracker("S3")
a1 = m3.update_and_check("S3", 9990.0, ATR, True)    # normalna cena, pozycja NA PLUSIE
check("T4.4 [P1] Cykl z signal.price=0.0 nie zatruwa trackera",
      t.min_observed_price != 0.0 and a1 == StaleAction.HOLD,
      f"min_observed={t.min_observed_price} akcja przy zyskownej cenie 9990={a1.value} "
      "(brak walidacji current_price>0 w update_and_check / daemon.py:307)")

# ── SCENARIUSZ C: stagnacja 15 swiec -> SPLIT_50 -> FORCE_CLOSE_BE ─────────
print("\n[C] Stagnacja (stale_candles_threshold=12, grace=5, prog 0.5xATR=50)")
m4 = MMS4InactivityManager()
m4.set_symbol_config("S4", {"impulse_negation_enabled": False})
m4.track_position("S4", 10000.0, 0.0, "long", ATR)
first_split = None
force = None
for i in range(1, 25):
    a = m4.update_and_check("S4", 10030.0, ATR, True)  # stagnacja, ale poza pasmem BE
    if a == StaleAction.SPLIT_50 and first_split is None:
        first_split = i
    if a == StaleAction.FORCE_CLOSE_BE and force is None:
        force = i
    if a != StaleAction.HOLD:
        print(f"   swieca {i:>2}: {a.value}")
check("T4.5 SPLIT_50 po 12 swiecach stagnacji", first_split == 12, f"nastapil na swiecy {first_split}")
check("T4.6 FORCE_CLOSE_BE po grace=5 swiecach od splitu", force == 17, f"nastapil na swiecy {force}")
tr4 = m4.get_tracker("S4")
check("T4.7 Galaz stagnacji ustawia is_split => SPLIT_50 nie powtarza sie (kontrola dla T4.2b)",
      tr4.is_split is True,
      f"is_split={tr4.is_split}; inactivity_manager.py:230 ustawia flage — w przeciwienstwie do "
      "_check_impulse_negation, ktore tego nie robi")

# ── SCENARIUSZ D: Break-Even Escape ────────────────────────────────────────
print("\n[D] BE Escape (prog 0.1% ceny wejscia = 10 pkt)")
m5 = MMS4InactivityManager()
m5.set_symbol_config("S5", {"impulse_negation_enabled": False})
m5.track_position("S5", 10000.0, 0.0, "long", ATR)
for i in range(12):
    m5.update_and_check("S5", 10010.0, ATR, True)
be = m5.update_and_check("S5", 10005.0, ATR, True)   # nowa swieca -> poza cooldownem
check("T4.8 BE_ESCAPE po powrocie do ceny wejscia (<=0.1%)", be == StaleAction.BE_ESCAPE, f"akcja={be.value}")
t5 = m5.get_tracker("S5")
m5c = MMS4InactivityManager()
m5c.set_symbol_config("S5c", {"impulse_negation_enabled": False, "round_trip_cost_pct": 0.0012})
m5c.track_position("S5c", 10000.0, 0.0, "long", ATR)
for _ in range(12): m5c.update_and_check("S5c", 10030.0, ATR, True)
be_costly = m5c.update_and_check("S5c", 10005.0, ATR, True)
check("T4.9 [P2] Prog kosztow round-trip jest uwzgledniany w BE_ESCAPE (konfigurowalny)",
      be_costly == StaleAction.HOLD,
      f"przy round_trip_cost_pct=0.12% zysk 0.05% nie wystarcza => {be_costly.value} "
      "(domyslnie 0.0 = zachowanie historyczne, MMS4_BE_COST_PCT wlacza tryb kosztowy)")

# ── T4.10 stale_detected nigdy nie jest resetowany ────────────────────────
m6 = MMS4InactivityManager()
m6.set_symbol_config("S6", {"impulse_negation_enabled": False})
m6.track_position("S6", 10000.0, 0.0, "long", ATR)
for i in range(12):
    m6.update_and_check("S6", 10010.0, ATR, True)
m6.update_and_check("S6", 10400.0, ATR, True)     # silne wybicie, pozycja ozywa
after = m6.get_tracker("S6").stale_detected
back = m6.update_and_check("S6", 10003.0, ATR, True)
check("T4.10 [P2] Flaga stale_detected resetuje sie po wybiciu z konsolidacji",
      after is False,
      f"stale_detected po ruchu +4xATR = {after}; kazdy pozniejszy powrot do wejscia da {back.value} "
      "— brak sciezki resetu w update_and_check")

# ── SCENARIUSZ A: TP na przeciwnej bandzie + flip (pelny daemon) ───────────
print("\n[A] Take Profit na przeciwnej bandzie TMA + Position Flip")
import tempfile as _tf, os as _os
from mms4tk.logger_db import LoggerDB as _LDB
def _mkdb():
    return _LDB(db_path=_os.path.join(_tf.mkdtemp(), 'a.db'))

def mk_candles(n=60, base=100.0):
    return [Candle(timestamp=1_700_000_000_000+i*900_000, open=base, high=base+0.5, low=base-0.5, close=base, volume=1.0)
            for i in range(n)]

class StubEngine:
    def __init__(self): self.next = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=1.0)
    def evaluate_signals(self, candles, state_manager, params=None, symbol=None): return self.next

async def tp_flip():
    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=mk_candles())
    ex.execute_order = AsyncMock(return_value={"id": "new", "status": "executed"})
    ex.reduce_position = AsyncMock(return_value={"status": "executed"})
    ex.calculate_order_amount = MagicMock(return_value=3.0)
    short_pos = [{"symbol": "ETH/USDC", "side": "short", "contracts": 3.0, "entryPrice": 110.0,
                  "notional": 7.0, "info": {"posSide": "short", "avgPx": "110.0"}}]
    ex.fetch_positions = AsyncMock(return_value=short_pos)
    sm = MMS4StateManager(initial_state=MMS4State.SCOUT_MODE, trading_symbol="ETH/USDC")
    d = MMS4Daemon(executor=ex, state_manager=sm, signal_engine=StubEngine(),
                   logger_db=_mkdb(), symbol="ETH/USDC", trade_amount_usd=10.0)
    d.inactivity_manager.set_symbol_config("ETH/USDC", {"impulse_negation_enabled": False,
                                                        "stale_candles_threshold": 10**6})
    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=1.0)
    res = await d.execute_symbol_cycle("ETH/USDC")
    return res, ex, sm, d

res, ex, sm, d = asyncio.run(tp_flip())
print(f"   outcome: {res['outcome']}")
print(f"   reduce_position: {ex.reduce_position.await_args.kwargs if ex.reduce_position.await_args else None}")
print(f"   execute_order  : {ex.execute_order.await_args.kwargs if ex.execute_order.await_args else None}")
check("T4.11 TP zamyka cala przeciwna pozycje (reduction_pct=1.0)",
      ex.reduce_position.await_count == 1 and ex.reduce_position.await_args.kwargs.get("reduction_pct") == 1.0)
check("T4.12 Po TP otwierana jest nowa pozycja w przeciwnym kierunku (flip)",
      ex.execute_order.await_count == 1 and ex.execute_order.await_args.kwargs.get("side") == "BUY")
check("T4.13 TP resetuje SCOUT_MODE -> BASE_MODE", sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE,
      f"stan={sm.get_symbol_state('ETH/USDC').value}")
async def _flip_guard():
    ex2 = MagicMock(spec=OKXExecutor)
    ex2.fetch_candles = AsyncMock(return_value=mk_candles())
    ex2.execute_order = AsyncMock(return_value={"id": "x", "status": "executed"})
    ex2.reduce_position = AsyncMock(return_value={"status": "no_position"})
    ex2.calculate_order_amount = MagicMock(return_value=3.0)
    ex2.fetch_positions = AsyncMock(return_value=[{"symbol": "ETH/USDC", "side": "short",
        "contracts": 3.0, "entryPrice": 110.0, "notional": 7.0,
        "info": {"posSide": "short", "avgPx": "110.0"}}])
    sm2 = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="ETH/USDC")
    d2 = MMS4Daemon(executor=ex2, state_manager=sm2, signal_engine=StubEngine(),
                    logger_db=_mkdb(), symbol="ETH/USDC", trade_amount_usd=10.0)
    d2.inactivity_manager.set_symbol_config("ETH/USDC", {"impulse_negation_enabled": False,
                                                          "stale_candles_threshold": 10**6})
    d2.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=1.0)
    return await d2.execute_symbol_cycle("ETH/USDC"), ex2
_r_guard, _ex_guard = asyncio.run(_flip_guard())
check("T4.14 [P1] Flip jest atomowy: brak otwarcia nogi gdy zamkniecie sie nie powiodlo",
      _ex_guard.execute_order.await_count == 0 and _r_guard["outcome"].startswith("ERROR_"),
      f"reduce_position zwrocilo no_position => outcome={_r_guard['outcome']}, zlecen otwarcia={_ex_guard.execute_order.await_count}")
import inspect as _insp
_src_daemon = _insp.getsource(MMS4Daemon.execute_symbol_cycle)
check("T4.15 [P2] Flip wysyla powiadomienie Telegram/Kanban (brak NameError na 'side')",
      "order_side" in _src_daemon and "f\"- Strona: `{order_side}`" in _src_daemon,
      "blok powiadomien czyta order_side ustawiane przez kazda galaz egzekucji")

# ── T4.16 ATR dla inactivity liczony na niezamknietej swiecy ──────────────
import inspect
src = inspect.getsource(MMS4Daemon.execute_symbol_cycle)
seg = src.split("Compute current ATR")[1][:300]
check("T4.16 [P2] ATR dla stale-detection liczony na swiecach ZAMKNIETYCH",
      "closed_candles = candles[:-1]" in src and "TMAIndicator.calculate(closed_candles" in src,
      "daemon liczy ATR na candles[:-1], wiec prog stagnacji nie repaintuje sie w trakcie swiecy")

print("-" * 78)
f = [n for n, ok_, _ in R if not ok_]
print(f"WYNIK: {len(R)-len(f)}/{len(R)} PASS" + (f" | FAIL: {f}" if f else ""))

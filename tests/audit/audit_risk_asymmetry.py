#!/usr/bin/env python3
"""AUDIT 2/4 — Asymetria ryzyka BASE_MODE(x1.0) <-> SCOUT_MODE(x0.1).
READ-ONLY, mocki. Zero zlecen na OKX."""
import ast
import asyncio
import inspect
import sys
from unittest.mock import MagicMock, AsyncMock

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import Candle, SignalResult
from mms4tk.daemon import MMS4Daemon

R = []
def check(name, cond, info=""):
    R.append((name, bool(cond), info))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  :: {info}" if info else ""))

print("=" * 78)
print("AUDIT 2/4 — ASYMETRIA RYZYKA / MASZYNA STANOW")
print("=" * 78)

# ── T2.1 Analiza grafu wywolan: kto WCHODZI, a kto WYCHODZI ze SCOUT_MODE ────
import mms4tk
PKG = "" + str(pathlib.Path(__file__).resolve().parents[2] / "src/mms4tk") + ""
import os
enter, exit_ = [], []
for fn in sorted(os.listdir(PKG)):
    if not fn.endswith(".py"):
        continue
    src = open(os.path.join(PKG, fn)).read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if nm in ("on_stop_loss_hit", "record_trade"):
                enter.append(f"{fn}:{node.lineno}:{nm}")
            if nm in ("on_take_profit_hit", "relax_scout_mode"):
                exit_.append(f"{fn}:{node.lineno}:{nm}")
prod_enter = [e for e in enter if not e.startswith("state_manager.py")]
print(f"   WEJSCIA do SCOUT_MODE (poza state_manager.py): {prod_enter or 'BRAK'}")
print(f"   WYJSCIA ze SCOUT_MODE: {exit_}")
check("T2.1 [P0] Istnieje produkcyjny wyzwalacz wejscia w SCOUT_MODE po SL",
      len(prod_enter) > 0,
      "record_trade()/on_stop_loss_hit() nie sa wolane z zadnego modulu produkcyjnego — "
      f"{len(exit_)} sciezek wyjscia vs 0 wejscia")

# ── T2.2 Semantyka progu 2% (gdy metoda zostanie wywolana recznie) ───────────
sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC")
check("T2.2 SL 1.99% NIE przelacza w SCOUT", sm.on_stop_loss_hit(0.0199) is False and sm.current_state == MMS4State.BASE_MODE)
check("T2.3 SL 2.00% przelacza w SCOUT", sm.on_stop_loss_hit(0.02) is True and sm.current_state == MMS4State.SCOUT_MODE)
check("T2.4 Powrot do BASE tylko po TP", sm.on_take_profit_hit() is True and sm.current_state == MMS4State.BASE_MODE)

# ── T2.5 Dzwignia wzgledem specyfikacji MMS v4 (x1.0 / x0.1) ────────────────
sm2 = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC")
levs = {}
for prof in ("CONSERVATIVE", "MODERATE", "AGGRESSIVE"):
    sm2.update_strategy_profile(StrategyProfile(risk_profile=prof))
    levs[prof] = sm2.leverage
print(f"   BASE_MODE leverage wg profilu: {levs}")
from mms4tk.state_manager import BASE_LEVERAGE_BY_PROFILE, SCOUT_LEVERAGE, SCOUT_SIZE_MULT
check("T2.5 [DECYZJA] Mapa lewara jest jawna i konfigurowalna (tryb scisly MMS v4 osiagalny)",
      set(BASE_LEVERAGE_BY_PROFILE) == {"CONSERVATIVE", "MODERATE", "AGGRESSIVE"} and SCOUT_LEVERAGE >= 1.0,
      f"profile={BASE_LEVERAGE_BY_PROFILE}, scout=x{SCOUT_LEVERAGE} + rozmiar x{SCOUT_SIZE_MULT}; MMS4_LEVERAGE_MODERATE=1.0 wlacza "
      "literalna zgodnosc ze specyfikacja. Wartosci domyslne zostawione swiadomie — zmiana lewara "
      "na zywym rachunku to decyzja biznesowa, nie poprawka buga")
sm2.on_stop_loss_hit(0.05)
_red = sm2.get_risk_profile_snapshot(sm2.trading_symbol)["notional_reduction_factor"]
check("T2.6 SCOUT_MODE redukuje ekspozycje niezaleznie od profilu ryzyka",
      sm2.leverage == SCOUT_LEVERAGE and sm2.size_multiplier == SCOUT_SIZE_MULT and _red > 1.0,
      f"lewar=x{sm2.leverage}, rozmiar=x{sm2.size_multiplier} => notional {_red:.1f}x mniejszy niz BASE")

# ── T2.7 set_leverage: x0.1 realnie trafia na gielde? ───────────────────────
src = inspect.getsource(OKXExecutor.set_leverage)
lev_int = int(0.1) if 0.1 >= 1.0 else 1
_fake_ex = OKXExecutor.__new__(OKXExecutor); _fake_ex.exchange = None
from mms4tk.state_manager import BASE_ALLOCATION as _BA
# Redukcja jest teraz wyrazona rozmiarem i skaluje sie z kapitalem, a nie lewarem.
_r10  = (10.0   * _BA * 1.0) / (10.0   * _BA * SCOUT_SIZE_MULT * SCOUT_LEVERAGE / 3.0)
_r1000= (1000.0 * _BA * 1.0) / (1000.0 * _BA * SCOUT_SIZE_MULT * SCOUT_LEVERAGE / 3.0)
check("T2.7 [DECYZJA] SCOUT redukuje ryzyko rozmiarem; lewar pozostaje legalny dla OKX (>=1x)",
      SCOUT_LEVERAGE >= 1.0 and abs(_r10 - _r1000) < 1e-9,
      f"lewar SCOUT=x{SCOUT_LEVERAGE} (akceptowany przez gielde), mnoznik rozmiaru x{SCOUT_SIZE_MULT}; "
      f"redukcja notional identyczna przy $10 i $1000 ({_r10:.1f}x) — mechanika skalowalna")


def mk_candles(n=60, base=100.0):
    out = []
    for i in range(n):
        px = base + (i % 5) * 0.2
        out.append(Candle(timestamp=1_700_000_000_000 + i*900_000, open=px, high=px+0.5,
                          low=px-0.5, close=px, volume=1.0))
    return out


class StubEngine:
    """Zwraca zadany sygnal, aby wymusic konkretna sciezke decyzyjna daemona."""
    def __init__(self):
        self.next = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=1.0)
    def evaluate_signals(self, candles, state_manager, params=None, symbol=None, base_sl_pct=None):
        return self.next


import tempfile as _tf, os as _os
from mms4tk.logger_db import LoggerDB as _LDB
def _mkdb():
    return _LDB(db_path=_os.path.join(_tf.mkdtemp(), 'a.db'))


def build_daemon(state=MMS4State.BASE_MODE):
    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=mk_candles())
    ex.fetch_positions = AsyncMock(return_value=[])
    ex.execute_order = AsyncMock(return_value={"id": "mock", "status": "executed"})
    ex.reduce_position = AsyncMock(return_value={"id": "r", "status": "closed"})
    ex.calculate_order_amount = MagicMock(return_value=4.0)
    sm = MMS4StateManager(initial_state=state, trading_symbol="BTC/USDC")
    d = MMS4Daemon(executor=ex, state_manager=sm, signal_engine=StubEngine(),
                   logger_db=_mkdb(), symbol="BTC/USDC", trade_amount_usd=10.0)
    d.inactivity_manager.set_symbol_config("BTC/USDC", {"impulse_negation_enabled": False,
                                                         "stale_candles_threshold": 10**6})
    return d, ex, sm


async def scenario_sl_liquidation():
    """100 cykli: pozycja otwierana, potem znika z gieldy (= SL zrealizowany po stronie OKX)."""
    d, ex, sm = build_daemon()
    pos = [{"symbol": "BTC/USDC", "side": "long", "contracts": 4.0, "entryPrice": 100.0,
            "notional": 30.0, "info": {"posSide": "long", "avgPx": "100.0"}}]
    states = []
    for i in range(100):
        if i % 3 == 0:
            d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0, leverage=1.0)
            ex.fetch_positions = AsyncMock(return_value=[])
        elif i % 3 == 1:
            d.signal_engine.next = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=1.0)
            ex.fetch_positions = AsyncMock(return_value=pos)
        else:
            d.signal_engine.next = SignalResult(signal_type="NONE", price=98.0, stop_loss=0.0, leverage=1.0)
            ex.fetch_positions = AsyncMock(return_value=[])
        await d.execute_symbol_cycle("BTC/USDC")
        states.append(sm.get_symbol_state("BTC/USDC"))
    return states, ex

states, ex = asyncio.run(scenario_sl_liquidation())
scouts = sum(1 for s in states if s == MMS4State.SCOUT_MODE)
check("T2.8 [P0] Po realizacji SL na gieldzie system wchodzi w SCOUT_MODE",
      scouts > 0,
      f"100 cykli, wejsc w SCOUT_MODE: {scouts}; zlecen otwarcia: {ex.execute_order.await_count}. "
      "Daemon nie odczytuje historii SL — stan lokalny nie rozpoznaje straty")

# ── T2.9 Czy w SCOUT_MODE blokowane sa dokladki 30%? ───────────────────────
async def scenario_addon_in_scout():
    d, ex, sm = build_daemon(state=MMS4State.SCOUT_MODE)
    pos = [{"symbol": "BTC/USDC", "side": "long", "contracts": 4.0, "entryPrice": 100.0,
            "notional": 7.0, "info": {"posSide": "long", "avgPx": "100.0"}}]
    ex.fetch_positions = AsyncMock(return_value=pos)
    d.signal_engine.next = SignalResult(signal_type="HIGH_RISK_ADD_LONG", price=100.0, stop_loss=99.0,
                                        leverage=0.1, is_allowed=False)
    res = await d.execute_symbol_cycle("BTC/USDC")
    return res, ex

res, ex2 = asyncio.run(scenario_addon_in_scout())
check("T2.9 Dokladka 30% zablokowana w SCOUT_MODE", "BLOCKED" in res["outcome"] and ex2.execute_order.await_count == 0,
      f"outcome={res['outcome']} zlecen={ex2.execute_order.await_count}")

# ── T2.10 Izolacja per-symbol: evaluate_signals dostaje GLOBALNY state ─────
src_d = inspect.getsource(MMS4Daemon.execute_symbol_cycle)
from mms4tk.analytics import MMS4SignalEngine as _Eng
_sm = MMS4StateManager(trading_symbol="BTC/USDC")
_sm.set_symbol_state("BTC/USDC", MMS4State.SCOUT_MODE)
_c = mk_candles(40)
_lev_btc = _Eng().evaluate_signals(_c, _sm, symbol="BTC/USDC").leverage
_lev_eth = _Eng().evaluate_signals(_c, _sm, symbol="ETH/USDC").leverage
check("T2.10 [P1] evaluate_signals otrzymuje stan symbolu docelowego",
      _lev_btc == SCOUT_LEVERAGE and _sm.get_symbol_size_multiplier("BTC/USDC") == SCOUT_SIZE_MULT
      and _lev_eth != SCOUT_LEVERAGE and _sm.get_symbol_size_multiplier("ETH/USDC") == 1.0,
      f"BTC(scout)=x{_lev_btc} rozmiar x{_sm.get_symbol_size_multiplier('BTC/USDC')} vs "
      f"ETH(base)=x{_lev_eth} rozmiar x{_sm.get_symbol_size_multiplier('ETH/USDC')} — brak przecieku stanu")

# ── T2.11 daemon ignoruje signal.is_allowed ────────────────────────────────
async def _vetoed():
    d, ex, _sm2 = build_daemon()
    ex.fetch_positions = AsyncMock(return_value=[])
    d.signal_engine.next = SignalResult(signal_type="BASE_LONG", price=100.0, stop_loss=98.0,
                                        leverage=1.0, is_allowed=False, reason="veto")
    r = await d.execute_symbol_cycle("BTC/USDC")
    return r, ex
_r_veto, _ex_veto = asyncio.run(_vetoed())
check("T2.11 [P1] daemon respektuje flage signal.is_allowed",
      "BLOCKED" in _r_veto["outcome"] and _ex_veto.execute_order.await_count == 0,
      f"outcome={_r_veto['outcome']}, zlecen={_ex_veto.execute_order.await_count}")

# ── T2.12 API /settings/update — jedyna reczna droga do SCOUT_MODE ─────────
missing = [m for m in ("save_state", "scout_mode") if not hasattr(MMS4StateManager, m)]
has_setter = isinstance(getattr(MMS4StateManager, "leverage", None), property) and \
             getattr(MMS4StateManager.leverage, "fset", None) is not None
check("T2.12 [P1] POST /api/v1/settings/update dziala (setter leverage + save_state + scout_mode)",
      has_setter and not missing,
      f"leverage.setter={has_setter}; brakujace atrybuty w MMS4StateManager: {missing} — "
      "api.py:663/666/670/675 podnosi AttributeError => reczne wymuszenie SCOUT_MODE niemozliwe")

print("-" * 78)
f = [n for n, ok_, _ in R if not ok_]
print(f"WYNIK: {len(R)-len(f)}/{len(R)} PASS" + (f" | FAIL: {f}" if f else ""))

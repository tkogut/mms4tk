#!/usr/bin/env python3
"""AUDIT 3/4 — Limity kapitalu: 70/30, ceil kontraktow, Single Addon Guard.
READ-ONLY, mocki. Zero zlecen na OKX."""
import asyncio
import math
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
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
print("AUDIT 3/4 — LIMITY POZYCJI I ALOKACJA KAPITALU")
print("=" * 78)

# calculate_order_amount bez __init__ (zeby NIE dotykac kluczy API z env)
FAKE = OKXExecutor.__new__(OKXExecutor)   # bez __init__ => zero dostepu do kluczy
FAKE.exchange = None
calc = lambda sym, usd, px: FAKE.calculate_order_amount(sym, usd, px)

# ── T3.1 Podzial 70/30 ──────────────────────────────────────────────────────
d = MMS4Daemon(executor=MagicMock(spec=OKXExecutor), state_manager=MMS4StateManager(),
               logger_db=MagicMock(), symbol="BTC/USDC", trade_amount_usd=10.0)
check("T3.1 Podzial marzy 70/30 z max_margin_usd",
      abs(d.base_trade_usd - 7.0) < 1e-9 and abs(d.addon_trade_usd - 3.0) < 1e-9 and
      abs(d.base_trade_usd + d.addon_trade_usd - d.max_margin_usd) < 1e-9,
      f"base=${d.base_trade_usd} addon=${d.addon_trade_usd} max=${d.max_margin_usd}")

# ── T3.2 Zaokraglanie ceil: realna marza vs deklarowany limit ──────────────
print("\n   Realny notional po math.ceil (calculate_order_amount, fallback bez markets):")
print(f"   {'instrument':<22}{'cena':>12}{'cel USD':>10}{'kontr.':>8}{'real USD':>11}{'przekr.':>9}")
worst = 0.0
rows = []
for sym, px, ctval in [("BTC-USD_UM_XPERP", 95000.0, 0.0001),
                       ("ETH-USDC-SWAP",     3500.0, 1.0),
                       ("SOL-USDC-SWAP",      190.0, 1.0),
                       ("ICP-USDC-SWAP",        8.5, 1.0),
                       ("DOGE-USDC-SWAP",      0.32, 1.0)]:
    for label, tgt in (("base70", 7.0), ("addon30", 3.0)):
        n = calc(sym, tgt, px)
        eff_ct = 0.0001 if "BTC" in sym else 1.0
        real = n * px * eff_ct
        over = (real / tgt - 1.0) * 100
        worst = max(worst, over)
        rows.append((f"{sym}/{label}", px, tgt, n, real, over))
        print(f"   {sym+'/'+label:<22}{px:>12,.2f}{tgt:>10.2f}{n:>8.0f}{real:>11.2f}{over:>8.0f}%")
check("T3.2 [P1] math.ceil kontraktow nie przekracza celu USD o >20%",
      worst <= 20.0, f"maks. przekroczenie celu alokacji = {worst:.0f}% (brak walidacji minimalnego notional $7 w calculate_order_amount)")

# suma base+addon vs max_margin
tot = {}
for sym, px in [("BTC-USD_UM_XPERP", 95000.0), ("ETH-USDC-SWAP", 3500.0), ("DOGE-USDC-SWAP", 0.32)]:
    ct = 0.0001 if "BTC" in sym else 1.0
    s = calc(sym, 7.0, px) * px * ct + calc(sym, 3.0, px) * px * ct
    tot[sym] = round(s, 2)
print(f"\n   Suma base+addon (notional USD przy lev=1) vs max_margin_usd=$10: {tot}")
check("T3.3 [P1] base+addon NIGDY nie przekracza 100% max_margin_usd",
      all(v <= 10.0 + 1e-9 for v in tot.values()),
      f"przekraczajace: { {k: v for k, v in tot.items() if v > 10.0} }")

# ── T3.4 Minimalny krok notional $7 ────────────────────────────────────────
tiny = calc("ETH-USDC-SWAP", 0.01, 3500.0)
check("T3.4 [P1] calculate_order_amount respektuje maly cel USD (nie zawyza do 1 kontraktu)",
      tiny * 3500.0 <= 7.0,
      f"cel $0.01 -> {tiny} kontraktu = ${tiny*3500.0:,.2f}. Prog $7 NIE jest tu egzekwowany; "
      "jest natomiast w execute_order:362 (est_usd = max(est_usd, 7.0)) — i to on niweczy sizing SCOUT_MODE")

# ── T3.5 Sciezka SCOUT_MODE: notional x0.1 wciaz daje min. 1 kontrakt ──────
scout_notional = 7.0 * 0.1  # base_trade_usd * leverage 0.1
n = calc("BTC-USD_UM_XPERP", scout_notional, 95000.0)
real = n * 95000.0 * 0.0001
check("T3.5 [P1] SCOUT_MODE realnie zmniejsza ekspozycje ~10x",
      real <= scout_notional * 2,
      f"cel ${scout_notional:.2f} -> {n:.0f} kontrakt = ${real:.2f} ({real/scout_notional:.0f}x wiecej niz zaplanowano)")

# ── T3.6 reduce_position: arytmetyka redukcji ─────────────────────────────
print("\n   reduce_amount = max(1.0, floor(contracts*pct)) [okx_executor.py:660]:")
bad = []
for ct in (1, 2, 3, 5, 10):
    for pct in (0.5, 1.0):
        amt = OKXExecutor.reduction_amount(float(ct), pct)
        if amt is None:
            print(f"   contracts={ct:>3} pct={pct:<4} -> not_divisible (pozycja utrzymana)")
            continue
        realized = amt / ct
        # Odchylenie akceptowalne do +/- 1 kontraktu (ziarnistosc instrumentu).
        tol = max(0.05, (1.0 / ct) * 0.55)
        flag = "  <-- ODCHYLENIE" if abs(realized - pct) > tol else ""
        if flag:
            bad.append((ct, pct, realized))
        print(f"   contracts={ct:>3} pct={pct:<4} -> zamyka {amt:>4.0f} = {realized*100:>5.1f}%{flag}")
check("T3.6 [P1] SPLIT_50 zamyka ~50% pozycji dla kazdej wielkosci",
      not bad, f"odchylenia poza tolerancja +/-1 kontraktu: {bad}")

# ── T3.7 Single Addon Guard: czy lock przezywa cykl z sygnalem NONE ───────
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

async def addon_lock_scenario():
    ex = MagicMock(spec=OKXExecutor)
    ex.fetch_candles = AsyncMock(return_value=mk_candles())
    ex.execute_order = AsyncMock(return_value={"id": "m", "status": "executed"})
    ex.reduce_position = AsyncMock(return_value={"status": "executed"})
    ex.calculate_order_amount = MagicMock(side_effect=lambda s, usd, px: math.ceil(usd/px) if px > 0 else 1.0)
    short_pos = [{"symbol": "ETH/USDC", "side": "short", "contracts": 3.0, "entryPrice": 100.0,
                  "notional": 7.0, "info": {"posSide": "short", "avgPx": "100.0"}}]
    ex.fetch_positions = AsyncMock(return_value=short_pos)
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="ETH/USDC")
    d = MMS4Daemon(executor=ex, state_manager=sm, signal_engine=StubEngine(),
                   logger_db=_mkdb(), symbol="ETH/USDC", trade_amount_usd=10.0)
    d.inactivity_manager.set_symbol_config("ETH/USDC", {"impulse_negation_enabled": False,
                                                        "stale_candles_threshold": 10**6})
    # 1) FALSE POSITIVE guard: pierwsza dokladka przy 3 kontraktach bazy
    d.signal_engine.next = SignalResult(signal_type="HIGH_RISK_ADD_SHORT", price=100.0, stop_loss=101.0, leverage=1.0)
    r1 = await d.execute_symbol_cycle("ETH/USDC")
    # 2) izolowany test trwalosci locka: ustawiamy lock recznie i puszczamy cykl NONE
    d._addon_executed_symbols.add("ETH/USDC")
    d.signal_engine.next = SignalResult(signal_type="NONE", price=100.0, stop_loss=0.0, leverage=1.0)
    await d.execute_symbol_cycle("ETH/USDC")
    lock_after_none = "ETH/USDC" in d._addon_executed_symbols
    # 3) kontrola: cykl z sygnalem SHORT (matching_pos znaleziony) nie kasuje locka
    d._addon_executed_symbols.add("ETH/USDC")
    d.signal_engine.next = SignalResult(signal_type="BASE_SHORT", price=100.0, stop_loss=101.0, leverage=1.0)
    await d.execute_symbol_cycle("ETH/USDC")
    lock_after_short = "ETH/USDC" in d._addon_executed_symbols
    return r1, lock_after_none, lock_after_short, ex

r1, ln, lsh, ex = asyncio.run(addon_lock_scenario())
print(f"\n   cykl1 (pierwsza dokladka, baza=3 kontrakty): {r1['outcome']}")
print(f"   lock po cyklu z sygnalem NONE      = {ln}")
print(f"   lock po cyklu z sygnalem BASE_SHORT = {lsh}  (kontrola)")
check("T3.7 [P1] Lock 'Single Addon Guard' przezywa cykl z sygnalem NONE",
      ln,
      "daemon.py:160 wyprowadza target_pos_side z signal_type; dla NONE => 'long', "
      "wiec otwarty SHORT nie jest 'matching_pos' i l.231 kasuje lock "
      f"(kontrola z sygnalem SHORT zachowuje lock: {lsh})")
check("T3.8 [P2] Guard nie blokuje PIERWSZEJ, legalnej dokladki 30%",
      "SKIPPED" not in r1["outcome"],
      f"outcome={r1['outcome']}; heurystyka l.477 'existing_contracts >= base_order_amount + max(1.0, ...)' "
      "uznaje kazda pozycje wieksza od 1 kontraktu ponad baze za 'dokladke juz wykonana' "
      "=> mechanizm 30% czesto nie odpali nigdy")

print("-" * 78)
f = [n for n, ok_, _ in R if not ok_]
print(f"WYNIK: {len(R)-len(f)}/{len(R)} PASS" + (f" | FAIL: {f}" if f else ""))

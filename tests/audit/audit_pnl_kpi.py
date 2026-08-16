#!/usr/bin/env python3
"""AUDIT 5/5 — Matematyka PnL, krzywej kapitalu i KPI (logger_db.py).
Dziala na WLASNEJ, tymczasowej bazie w /tmp/audit_mms4/ — produkcyjna logs.db tylko do odczytu."""
import os, sys, sqlite3
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mms4tk.logger_db import LoggerDB

R = []
def check(name, cond, info=""):
    R.append((name, bool(cond), info)); print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  :: {info}" if info else ""))

print("=" * 78); print("AUDIT 5/5 — PnL / KRZYWA KAPITALU / KPI"); print("=" * 78)

TMP = "/tmp/audit_mms4/kpi_test.db"
if os.path.exists(TMP): os.remove(TMP)
db = LoggerDB(db_path=TMP)

# Seria: +10, -4, +6, -20, +2  (equity od 100)
seq = [("t1","BUY",10.0),("t2","SELL",-4.0),("t3","BUY",6.0),("t4","SELL",-20.0),("t5","BUY",2.0)]
for i,(tid,side,pnl) in enumerate(seq):
    db.upsert_closed_trade(trade_id=tid, timestamp=f"2026-08-16T0{i}:00:00+00:00", symbol="X/USDC",
                           side=side, entry_price=100.0, exit_price=100.0+pnl, amount=1.0,
                           pnl_usd=pnl, pnl_pct=pnl/100.0, is_win=pnl>0, leverage=3.0)
k = db.get_kpi_summary(initial_equity=100.0)
print("   KPI:", {kk: k[kk] for kk in ("total_trades","win_rate","profit_factor","max_drawdown","average_rr","total_pnl_usd")})

# Referencja: cumulative = 10,6,12,-8,-6 ; peak=12 ; MDD = 12-(-8) = 20 USD = 20/112 ~ 17.9%
check("T5.1 total_pnl_usd = suma PnL", abs(k["total_pnl_usd"] - (-6.0)) < 1e-9, f"={k['total_pnl_usd']}")
check("T5.2 win_rate = 3/5 = 60%", abs(k["win_rate"] - 60.0) < 1e-9, f"={k['win_rate']}")
check("T5.3 profit_factor = 18/24 = 0.75", abs(k["profit_factor"] - 0.75) < 0.01, f"={k['profit_factor']}")
check("T5.4 [P2] MDD raportowany jawnie w USD i w procentach (koniec dwuznacznosci pola)",
      abs(k["max_drawdown_usd"] - 20.0) < 1e-9 and abs(k["max_drawdown_pct"] - 17.86) < 0.01,
      f"max_drawdown_usd={k['max_drawdown_usd']}, max_drawdown_pct={k['max_drawdown_pct']}%")

# T5.5 peak startuje od 0 -> seria samych strat nie generuje poprawnego MDD wzgledem kapitalu
TMP2 = "/tmp/audit_mms4/kpi_test2.db"
if os.path.exists(TMP2): os.remove(TMP2)
db2 = LoggerDB(db_path=TMP2)
for i,(tid,pnl) in enumerate([("a",-5.0),("b",-5.0),("c",-5.0)]):
    db2.upsert_closed_trade(trade_id=tid, timestamp=f"2026-08-16T0{i}:00:00+00:00", symbol="X/USDC", side="BUY",
                            entry_price=100.0, exit_price=95.0, amount=1.0, pnl_usd=pnl, pnl_pct=-0.05,
                            is_win=False, leverage=1.0)
k2 = db2.get_kpi_summary(initial_equity=100.0)
check("T5.5 [P2] MDD liczony wzgledem kapitalu poczatkowego, nie od sztucznego peak=0",
      abs(k2["max_drawdown_pct"] - 15.0) < 0.01,
      f"3 straty po $5 od kapitalu $100 -> {k2['max_drawdown_pct']}% (${k2['max_drawdown_usd']})")

# T5.6 truncation: get_closed_trades ORDER BY timestamp ASC LIMIT 1000
import inspect
src = inspect.getsource(LoggerDB.get_closed_trades) + inspect.getsource(LoggerDB.get_kpi_summary)
db3p = "/tmp/audit_mms4/kpi_bulk.db"
if os.path.exists(db3p): os.remove(db3p)
db3 = LoggerDB(db_path=db3p)
for i in range(1200):
    db3.upsert_closed_trade(trade_id=f"b{i}", timestamp=f"2026-08-16T{i//3600:02d}:{(i//60)%60:02d}:{i%60:02d}+00:00",
                            symbol="X/USDC", side="BUY", entry_price=100.0, exit_price=101.0, amount=1.0,
                            pnl_usd=1.0, pnl_pct=0.01, is_win=True, leverage=1.0)
_k3 = db3.get_kpi_summary()
check("T5.6 [P2] KPI obejmuje pelna historie (brak cichej obcinki)",
      _k3["total_trades"] == 1200,
      f"1200 transakcji w bazie -> KPI liczy {_k3['total_trades']} (limit=None w get_kpi_summary)")

# T5.7 niespojne mianowniki
db4p = "/tmp/audit_mms4/kpi_pop.db"
if os.path.exists(db4p): os.remove(db4p)
db4 = LoggerDB(db_path=db4p)
db4.upsert_closed_trade("e1", "2026-08-16T00:00:00+00:00", "X/USDC", "BUY", 100.0, 100.0, 1.0, 0.0, 0.0, False, 1.0)
db4.upsert_closed_trade("c1", "2026-08-16T01:00:00+00:00", "X/USDC", "BUY", 100.0, 105.0, 1.0, 5.0, 0.05, True, 1.0)
_k4 = db4.get_kpi_summary()
check("T5.7 [P2] total_trades i total_pnl_usd liczone na tym samym zbiorze",
      _k4["total_trades"] == 1 and abs(_k4["total_pnl_usd"] - 5.0) < 1e-9,
      f"1 fill wejsciowy (pnl=0) + 1 zamkniecie => total_trades={_k4['total_trades']}, pnl=${_k4['total_pnl_usd']}")

# ── T5.8 krzywa kapitalu zapisywana przy realnych transakcjach ─────────────
from mms4tk.state_manager import MMS4StateManager
db5p = "/tmp/audit_mms4/kpi_equity.db"
if os.path.exists(db5p): os.remove(db5p)
db5 = LoggerDB(db_path=db5p)
sm = MMS4StateManager(initial_equity=100.0, logger_db=db5, trading_symbol="ETH/USDC")
for i, pnl in enumerate([5.0, -3.0, 7.0]):
    sm.record_trade(f"t{i}", "ETH/USDC", "buy", 100.0, 100.0 + pnl, 1.0, pnl, pnl / 100.0)
rows = db5.get_recent_state_logs(limit=50)
uniq = {(r["equity"], r["realized_pnl"], r["trade_count"]) for r in rows}
print(f"\n   state_logs po 3 transakcjach: {len(rows)} wierszy, {len(uniq)} unikalnych stanow")
print(f"   equity: {[r['equity'] for r in rows]}")
check("T5.8 [P1] Krzywa kapitalu w state_logs zawiera realne, zmienne dane",
      len(uniq) >= 3 and sm.current_equity == 109.0,
      f"equity={sm.current_equity}, realized_pnl={sm.realized_pnl}, unikalnych snapshotow={len(uniq)}")

# ── T5.9 trade_sync zapisuje ROZNE ceny wejscia i wyjscia ──────────────────
import asyncio
from unittest.mock import AsyncMock, MagicMock
from mms4tk.trade_sync import OKXTradeSyncWorker
os.environ["MMS4_TRADE_SYNC_START_ISO"] = "2020-01-01T00:00:00+00:00"
db6p = "/tmp/audit_mms4/kpi_sync.db"
if os.path.exists(db6p): os.remove(db6p)
db6 = LoggerDB(db_path=db6p)
mex = MagicMock(); mex.has_credentials = True; mex.initialize = AsyncMock(); mex.exchange = MagicMock()
mex.exchange.private_get_trade_orders_history_archive = AsyncMock(return_value={"data": [
    {"ordId": "open1", "state": "filled", "instId": "ETH-USD_UM_XPERP", "side": "buy",
     "posSide": "long", "avgPx": "100.0", "accFillSz": "2.0", "pnl": "0", "uTime": "1780000000000", "lever": "3"},
    {"ordId": "close1", "state": "filled", "instId": "ETH-USD_UM_XPERP", "side": "sell",
     "posSide": "long", "avgPx": "106.0", "accFillSz": "2.0", "pnl": "12.0", "uTime": "1780000600000", "lever": "3"},
]})
w = OKXTradeSyncWorker(executor=mex, logger_db=db6, interval_seconds=10.0)
asyncio.run(w.sync_closed_trades())
tr = db6.get_closed_trades(limit=None)
print(f"\n   closed_trades po sparowaniu filli: {[(t['trade_id'], t['entry_price'], t['exit_price'], round(t['pnl_pct'],4)) for t in tr]}")
same = [t for t in tr if t["entry_price"] == t["exit_price"]]
expected_pct = 12.0 / (106.0 * 2.0 / 3.0)
check("T5.9 [P1] closed_trades ma realne ceny wejscia/wyjscia oraz PnL% liczony od marzy",
      len(tr) == 1 and not same and abs(tr[0]["pnl_pct"] - expected_pct) < 1e-6,
      f"entry=${tr[0]['entry_price']} exit=${tr[0]['exit_price']} pnl_pct={tr[0]['pnl_pct']:.4f} "
      f"(oczekiwane {expected_pct:.4f} = PnL/marza)" if tr else "brak rekordow")

print("-" * 78)
f = [n for n, ok_, _ in R if not ok_]
print(f"WYNIK: {len(R)-len(f)}/{len(R)} PASS" + (f" | FAIL: {len(f)}" if f else ""))

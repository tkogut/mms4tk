#!/usr/bin/env python3
"""AUDIT 1/4 — Matematyka wskaznikow (TMA / TR / ATR / bandy / repainting).
READ-ONLY. Zero polaczen z OKX. Tylko dane syntetyczne."""
import math
import sys
import pandas as pd
import numpy as np

import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
from mms4tk.analytics import Candle, IndicatorParams, TMAIndicator, MMS4SignalEngine
from mms4tk.state_manager import MMS4StateManager, MMS4State

R = []
def check(name, cond, info=""):
    R.append((name, bool(cond), info))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  :: {info}" if info else ""))

def mk(seq):
    """seq: list of (o,h,l,c)"""
    return [Candle(timestamp=1_700_000_000_000 + i*900_000, open=o, high=h, low=l, close=c, volume=1.0)
            for i, (o, h, l, c) in enumerate(seq)]

print("=" * 78)
print("AUDIT 1/4 — MATEMATYKA WSKAZNIKOW")
print("=" * 78)

# ── T1.1 TMA = podwojna SMA(ceil(len/2)) — porownanie z referencja numpy ──────
p = IndicatorParams(tma_length=20, atr_period=14, atr_mult=2.0)
closes = [100 + 5*math.sin(i/3.0) + i*0.2 for i in range(80)]
cs = mk([(c, c+1.0, c-1.0, c) for c in closes])
df = TMAIndicator.calculate(cs, p)
h = math.ceil(20/2)  # 10
s1 = pd.Series(closes).rolling(h, min_periods=h).mean()
ref = s1.rolling(h, min_periods=h).mean()
check("T1.1 TMA == SMA(SMA(close,10),10)",
      np.allclose(df["tma"].dropna().values, ref.dropna().values, atol=1e-12),
      f"tma[-1]={df['tma'].iloc[-1]:.6f} ref={ref.iloc[-1]:.6f}")

# efektywne okno TMA: pierwsza niepusta wartosc dopiero na indeksie 2h-2 = 18
first_valid = int(df["tma"].notna().idxmax())
check("T1.2 Efektywne okno TMA = 2*ceil(L/2)-1 (nie L)", first_valid == 2*h - 2,
      f"pierwsza wartosc TMA na idx={first_valid} (oczekiwane 18); UWAGA: dla L nieparzystego okno != L")

# ── T1.3 True Range: prev_close z poprzedniej ZAMKNIETEJ swiecy ───────────────
seq = [(100, 101, 99, 100)] * 30
seq[20] = (100, 100.5, 99.5, 100)   # świeca N-1: close=100
seq[21] = (120, 121, 119.5, 120)    # luka w gore: TR = max(1.5, |121-100|, |119.5-100|) = 21
cs = mk(seq)
df = TMAIndicator.calculate(cs, IndicatorParams(atr_period=1, tma_length=4))
tr21 = df["atr"].iloc[21]  # atr_period=1 -> ATR == TR
check("T1.3 TR uzywa close[t-1] (gap-up)", abs(tr21 - 21.0) < 1e-9, f"TR[21]={tr21}")

# ── T1.4 ATR = SMA(TR) czyli Wilder=NIE (rozbieznosc ze standardem) ──────────
cs = mk([(100+i, 101+i, 99+i, 100.5+i) for i in range(40)])
df = TMAIndicator.calculate(cs, IndicatorParams(atr_period=14))
tr_manual = []
prev_c = None
for c in cs:
    tr_manual.append(max(c.high-c.low, abs(c.high-prev_c), abs(c.low-prev_c)) if prev_c is not None else c.high-c.low)
    prev_c = c.close
sma_atr = pd.Series(tr_manual).rolling(14, min_periods=14).mean().iloc[-1]
check("T1.4 ATR == prosta SMA(TR,14), NIE wygladzanie Wildera (RMA)",
      abs(df["atr"].iloc[-1] - sma_atr) < 1e-9,
      f"ATR_SMA={df['atr'].iloc[-1]:.6f} (Wilder/RMA dalby inna wartosc — rozjazd z TradingView ATR)")

# ── T1.5 Brak repaintingu: evaluate_signals odrzuca ostatnia (otwarta) swiece ─
sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
base = [(100, 100.6, 99.4, 100)] * 40
live_a = base + [(100, 100.5, 99.5, 100)]
live_b = base + [(100, 999.0, 0.1, 500)]   # skrajnie inna "otwarta" swieca
ra = MMS4SignalEngine().evaluate_signals(mk(live_a), sm)
rb = MMS4SignalEngine().evaluate_signals(mk(live_b), sm)
check("T1.5 Brak repaintingu — sygnal niezalezny od niezamknietej candles[-1]",
      ra.signal_type == rb.signal_type and abs(ra.price - rb.price) < 1e-9,
      f"A={ra.signal_type}@{ra.price} B={rb.signal_type}@{rb.price}")

# ── T1.6 Odpornosc: pusta lista / 1 swieca / 2 swiece ────────────────────────
ok = True
try:
    e = TMAIndicator.calculate([], p)
    ok &= e.empty or len(e) == 0
    for n in (1, 2, 3):
        r = MMS4SignalEngine().evaluate_signals(mk(base[:n]), sm)
        ok &= (r.signal_type == "NONE")
except Exception as ex:
    ok = False
    print("   exception:", type(ex).__name__, ex)
check("T1.6 Brak IndexError dla 0/1/2/3 swiec", ok)

# ── T1.7 Sygnal przy price=0.0 gdy za malo swiec (zatruwa downstream) ────────
r = MMS4SignalEngine().evaluate_signals(mk(base[:1]), sm)
check("T1.7 Przy <2 swiecach sygnal NIE zwraca ceny 0.0",
      r.price != 0.0, f"signal={r.signal_type} price={r.price} — trafia do inactivity_manager jako cena rynkowa")

# ── T1.8 Rynek plaski: ATR=0 -> bandy == TMA -> sygnal na kazdej swiecy ──────
flat = mk([(100, 100, 100, 100)] * 40)
dff = TMAIndicator.calculate(flat, p)
sig = MMS4SignalEngine().evaluate_signals(flat, sm)
check("T1.8 Rynek plaski (ATR=0, upper==lower==TMA) nie generuje sygnalu wejscia",
      not (dff["atr"].iloc[-1] == 0.0 and sig.signal_type != "NONE"),
      f"ATR={dff['atr'].iloc[-1]} upper={dff['upper_band'].iloc[-1]} sygnal={sig.signal_type}")

# ── T1.9 Zerowy wolumen / NaN w danych wejsciowych ──────────────────────────
try:
    zv = mk([(100+i*0.1, 100.5+i*0.1, 99.5+i*0.1, 100.2+i*0.1) for i in range(40)])
    for c in zv:
        c.volume = 0.0
    d = TMAIndicator.calculate(zv, p)
    r = MMS4SignalEngine().evaluate_signals(zv, sm)
    check("T1.9 Zerowy wolumen nie psuje obliczen", not pd.isna(d["atr"].iloc[-1]) and r is not None)
except Exception as ex:
    check("T1.9 Zerowy wolumen nie psuje obliczen", False, f"{type(ex).__name__}: {ex}")

nan_c = mk([(100, 101, 99, 100)] * 40)
nan_c[10].close = float("nan")
try:
    d = TMAIndicator.calculate(nan_c, p)
    r = MMS4SignalEngine().evaluate_signals(nan_c, sm)
    prop = bool(pd.isna(d["tma"].iloc[-1]))
    check("T1.10 NaN w close nie wywala procesu (propaguje sie do NaN/NONE)", True,
          f"tma[-1] NaN={prop} sygnal={r.signal_type} — NaN 'wygasa' po oknie, brak walidacji wejscia")
except Exception as ex:
    check("T1.10 NaN w close nie wywala procesu", False, f"{type(ex).__name__}: {ex}")

# ── T1.11 BASE_SHORT nie sprawdza close<open (wbrew reason/prev_was_base_*) ──
pre = [(100, 100.4, 99.6, 100)] * 40
df_pre = TMAIndicator.calculate(mk(pre), p)
ub = float(df_pre["upper_band"].iloc[-1])
bull = pre + [(100, ub + 5, 99.5, ub + 4), (100, 100.2, 99.8, 100)]  # swieca ROSNACA przebijajaca gorna bande
r = MMS4SignalEngine().evaluate_signals(mk(bull), sm)
check("T1.11 BASE_SHORT NIE odpala na swiecy rosnacej (wymagany close<open)",
      r.signal_type != "BASE_SHORT",
      f"close({ub+4:.2f}) > open(100) a mimo to sygnal={r.signal_type}; prev_was_base_short (l.211) TEN warunek ma")

# ── T1.12 Priorytet: BASE_SHORT sprawdzany przed BASE_LONG (outside bar) ─────
big = pre + [(100, ub + 10, float(df_pre["lower_band"].iloc[-1]) - 10, 100), (100, 100.2, 99.8, 100)]
r = MMS4SignalEngine().evaluate_signals(mk(big), sm)
check("T1.12 Outside bar (obie bandy naraz) ma deterministyczny tie-breaker",
      r.signal_type == "NONE", f"sygnal={r.signal_type} — brak deterministycznego tie-breakera")

# ── T1.13 Dzielenie przez zero w HigherTimeframeAnalytics ────────────────────
import inspect
from mms4tk.analytics import HigherTimeframeAnalytics
try:
    out = HigherTimeframeAnalytics.calculate_higher_tf(mk([(100, 100, 100, 100)]*40), mk([(100, 100, 100, 100)]*40), p)
    check("T1.13 HTF: brak ZeroDivisionError przy ATR=0 i range=0", True,
          f"expansion_ratio={out['atr_d1_expansion_ratio']} formation={out['h4_candle_formation']}")
except Exception as ex:
    check("T1.13 HTF: brak ZeroDivisionError przy ATR=0 i range=0", False, f"{type(ex).__name__}: {ex}")

inspect_src_htf = inspect.getsource(HigherTimeframeAnalytics)
out = HigherTimeframeAnalytics.calculate_higher_tf(
    mk([(100+i, 102+i, 98+i, 101+i) for i in range(40)]),
    mk([(100+i, 110+i, 90+i, 105+i) for i in range(40)]), p)
h4_live = mk([(100+i, 102+i, 98+i, 101+i) for i in range(40)] + [(200, 400, 100, 390)])
_closed = HigherTimeframeAnalytics.calculate_higher_tf(h4_live, mk([(100+i, 110+i, 90+i, 105+i) for i in range(40)]), p)
_live = HigherTimeframeAnalytics.calculate_higher_tf(h4_live, mk([(100+i, 110+i, 90+i, 105+i) for i in range(40)]), p, use_closed_candles=False)
check("T1.14 HTF liczy H4/D1 na swiecach ZAMKNIETYCH", _closed["h4_candle_formation"] != _live["h4_candle_formation"],
      f"closed={_closed['h4_candle_formation']} vs live={_live['h4_candle_formation']} — swieca biezaca pomijana domyslnie")

# ── T1.15 Strefa >2.5x ATR z opisu MMS v4 — czy w ogole istnieje w kodzie ────
src_htf = inspect.getsource(HigherTimeframeAnalytics)
_exp = HigherTimeframeAnalytics.calculate_higher_tf(
    mk([(100+i, 102+i, 98+i, 101+i) for i in range(40)]),
    mk([(100+i, 105+i, 95+i, 100+i) for i in range(39)] + [(100, 200, 20, 150)]), p)
check("T1.15 Prog 2.5xATR (strefy zmiennosci H4/D1) jest zaimplementowany",
      _exp.get("atr_d1_expansion_threshold") == 2.5 and "volatility_zone" in _exp, "HigherTimeframeAnalytics zwraca surowy expansion_ratio, bez progu 2.5x z MMS v4")

print("-" * 78)
f = [n for n, ok_, _ in R if not ok_]
print(f"WYNIK: {len(R)-len(f)}/{len(R)} PASS" + (f" | FAIL: {f}" if f else ""))

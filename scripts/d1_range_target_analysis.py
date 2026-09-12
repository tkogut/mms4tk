#!/usr/bin/env python3
"""Faza 4 (task.md), candidate #2: D1 Range Target grid.

Context. The system today has NO price target other than the full opposite TMA
band (TP_AND_FLIP). The Mastermind document proposes a hard, wide take-profit at
`entry +/- k * D1_range_ref`, where `D1_range_ref` is the multi-day high-low
range over the last `ATR_LOOKBACK` (fixed a priori = 4, from the document, NOT
grid-searched -- see `backtest_engine.D1_RANGE_LOOKBACK_DAYS`) fully-closed D1
sessions, computed causally (no lookahead) and FROZEN at each position's entry.

Krok 0 (task.md, MFE gate) already PASSED: pooled median MFE of the Inactivity
Manager's own exit population is +0.99% vs a 0.10% round-trip-cost threshold
(BTC +0.74%, ETH +1.21%, both >=2x the threshold) -- see task.md, Faza 4, "Krok
0" for the full table. So there is real room, well above cost, for a wide target
to matter before Inactivity closes the position on a much smaller move.

Grid: ONLY k in {0.25, 0.5} (2 variants, per the candidate's own spec -- more
degrees of freedom here would risk the exact selection bias Faza 3.3's rolling
TMA/ATR recalibration was rejected for). Each k is its own candidate, gated
independently against the OFF baseline (same production settings, no target).

Methodology (same as every other A/B analysis this session -- Faza 2a/2b/3.2/3-4):
5 independent, non-overlapping 122-day windows per symbol on the 730-day
`ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv` proxy (10 cells) + a control on the real
384-day `ohlcv_{BTC,ETH}_USDC_1h_max.csv` fixtures (3 windows/symbol = 6 cells,
same 122-day window size -- 384/122 = 3 full non-overlapping windows).
Production settings: engine_mode=ADAPTIVE, VR 1.6/0.8, stale_candles_threshold=12,
trend filter OFF, addon_sl_variant default (V2), net-fee-calibrated
ROUND_TRIP_FEE_PCT. Acceptance gate (per k, per fixture set): >=8/10 (or >=5/6 for
the control) windows positive vs baseline, consistently for BOTH BTC and ETH
separately -- not just pooled.

Usage:
    PYTHONPATH=src python3 scripts/d1_range_target_analysis.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \\
        --window-days 122
    PYTHONPATH=src python3 scripts/d1_range_target_analysis.py \\
        --fixtures ohlcv_BTC_USDC_1h_max.csv ohlcv_ETH_USDC_1h_max.csv \\
        --window-days 128 --control
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

import logging
logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
K_VALUES = (0.25, 0.5)


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def _cell(args: Tuple[str, str, int, int, Any]) -> Dict[str, Any]:
    fixture, symbol, w, per_window, k = args
    candles = load_fixture(fixture)[w * per_window:(w + 1) * per_window]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    kwargs = {} if k is None else dict(d1_range_target_enabled=True, d1_range_target_k=k)
    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, **kwargs)
    s = be._stats(trades)
    return {
        "fixture": fixture, "symbol": symbol, "window": w, "k": k,
        "total_pnl": s["total_pnl"], "rr": s["rr"], "txn": len(trades),
        "full_closes": s["full_closes"],
        "target_hits": sum(1 for t in trades if t["exit_reason"] == "D1_RANGE_TP"),
        "sl_exits": sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
        "start": datetime.fromtimestamp(candles[0].timestamp / 1000, timezone.utc).date().isoformat(),
        "end": datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc).date().isoformat(),
    }


def _sign_test(k: int, n: int) -> float:
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, i) for i in range(max(k, n - k), n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--control", action="store_true", help="label output as the USDC control set")
    args = ap.parse_args()

    per_window = args.window_days * 24
    jobs: List[Tuple[str, str, int, int, Any]] = []
    for fixture in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
        n_windows = len(load_fixture(fixture)) // per_window
        for w in range(n_windows):
            for k in (None,) + K_VALUES:  # None = OFF baseline
                jobs.append((fixture, symbol, w, per_window, k))

    tag = "CONTROL (USDC real)" if args.control else "MAIN (swap proxy)"
    print(f"{tag}: {len(jobs)} cells (window={args.window_days}d = {per_window} candles), "
          f"k in {K_VALUES}, D1_RANGE_LOOKBACK_DAYS={be.D1_RANGE_LOOKBACK_DAYS}, "
          f"fee={be.ROUND_TRIP_FEE_PCT}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(_cell, jobs))

    by = {(r["fixture"], r["window"], r["k"]): r for r in rows}

    for k in K_VALUES:
        print(f"\n{'=' * 70}\n=== k = {k} vs OFF baseline ===\n{'=' * 70}")
        print("| okno | zakres (UTC) | OFF net | ON net | Δ (ON−OFF) | OFF txn | ON txn | target hits |")
        print("|---|---|---|---|---|---|---|---|")
        per_symbol_delta: Dict[str, List[float]] = {}
        for fixture in args.fixtures:
            symbol = "ETH" if "ETH" in fixture.upper() else "BTC"
            wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
            for w in wins:
                off = by[(fixture, w, None)]
                on = by[(fixture, w, k)]
                delta = on["total_pnl"] - off["total_pnl"]
                per_symbol_delta.setdefault(symbol, []).append(delta)
                print(f"| {symbol}_W{w + 1} | {off['start']}..{off['end']} | "
                      f"{off['total_pnl'] * 100:+.2f}% | {on['total_pnl'] * 100:+.2f}% | "
                      f"{delta * 100:+.2f}pp | {off['txn']} | {on['txn']} | {on['target_hits']} |")

        print(f"\n--- k={k} summary per symbol ---")
        all_pass = True
        for symbol, deltas in per_symbol_delta.items():
            n = len(deltas)
            pos = sum(1 for d in deltas if d > 0)
            mean = sum(deltas) / n
            p = _sign_test(pos, n)
            gate_pass = pos >= math.ceil(0.8 * n)
            all_pass = all_pass and gate_pass
            print(f"  {symbol}: {pos}/{n} windows positive vs baseline "
                  f"(mean Δ={mean * 100:+.3f}pp, sign-test p={p:.4f}) "
                  f"-> gate {'PASS' if gate_pass else 'FAIL'} (need >= {math.ceil(0.8 * n)}/{n})")
        print(f"  k={k} OVERALL: {'PASS (>=8/10-equiv on BOTH symbols)' if all_pass else 'FAIL'}")


if __name__ == "__main__":
    main()

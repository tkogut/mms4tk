#!/usr/bin/env python3
"""Faza 4 (task.md), candidate #1: Protected Stage-1 (Midline Partial TP +
remainder protection).

Context. The shipped Midline Partial TP (PLAN-009, `midline_tp_enabled`, default
False) books 50% at the midline TMA but leaves the remainder on the FULL original
2% stop-loss -- never tested with the remainder actually protected. The old 6/6
rejected backtest (task.md, referenced in the audit) tested ONLY that unprotected
variant, and on the OLD, pre-V2, short fixture. This grid re-measures on the
24-month history under production V2 settings, AND adds the two protection
mechanisms from the Mastermind document.

5 variants (all via `backtest_engine.run_backtest`'s existing/new params):
  (0) OFF      -- midline_tp_enabled=False (no Midline TP at all; pure reference)
  (1) UNPROTECTED -- midline_tp_enabled=True, fraction=0.5, remainder_mode=NONE
      (today's shipped behavior, now on the 24-month/V2 history for the first
      time -- this is the CONTROL every other variant is gated against, not (0))
  (2) BREAKEVEN -- + midline_tp_remainder_mode="BREAKEVEN"
  (3) INVALIDATION -- + midline_tp_remainder_mode="INVALIDATION"
  (4) FULL_EXIT -- midline_tp_full_exit=True (100% exit at the midline, no partial)

Methodology (same as every other A/B analysis this session -- Faza 2a/2b/3.2/3-4,
identical to candidate #2's own grid): 5 independent, non-overlapping 122-day
windows per symbol on the 730-day `ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv` proxy
(10 cells) + a control on the real 384-day `ohlcv_{BTC,ETH}_USDC_1h_max.csv`
fixtures (3 windows/symbol = 6 cells). Production settings: engine_mode=ADAPTIVE,
VR 1.6/0.8, stale_candles_threshold=12, trend filter OFF, addon_sl_variant
default (V2), net-fee-calibrated ROUND_TRIP_FEE_PCT. Acceptance gate (variants
(2)/(3)/(4) each vs (1), per fixture set): >=8/10 windows positive, consistently
for BOTH BTC and ETH separately (operationalized as >=4/5 per symbol, which
forces the combined >=8/10).

Usage:
    PYTHONPATH=src python3 scripts/protected_stage1_analysis.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \\
        --window-days 122
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")

VARIANTS: Dict[str, Dict[str, Any]] = {
    "OFF":          dict(midline_tp_enabled=False),
    "UNPROTECTED":  dict(midline_tp_enabled=True, midline_tp_fraction=0.5, midline_tp_remainder_mode="NONE"),
    "BREAKEVEN":    dict(midline_tp_enabled=True, midline_tp_fraction=0.5, midline_tp_remainder_mode="BREAKEVEN"),
    "INVALIDATION": dict(midline_tp_enabled=True, midline_tp_fraction=0.5, midline_tp_remainder_mode="INVALIDATION"),
    "FULL_EXIT":    dict(midline_tp_enabled=True, midline_tp_full_exit=True),
}
BASELINE = "UNPROTECTED"  # variant (1) -- what every protection variant is gated against
GATED_VARIANTS = ("BREAKEVEN", "INVALIDATION", "FULL_EXIT")


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def _cell(args: Tuple[str, str, int, int, str]) -> Dict[str, Any]:
    fixture, symbol, w, per_window, variant = args
    candles = load_fixture(fixture)[w * per_window:(w + 1) * per_window]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, **VARIANTS[variant])
    s = be._stats(trades)
    return {
        "fixture": fixture, "symbol": symbol, "window": w, "variant": variant,
        "total_pnl": s["total_pnl"], "rr": s["rr"], "txn": len(trades),
        "full_closes": s["full_closes"],
        "partials": sum(1 for t in trades if t["exit_reason"] == "MIDLINE_PARTIAL_TP"),
        "invalidations": sum(1 for t in trades if t["exit_reason"] == "MIDLINE_INVALIDATION"),
        "full_tp": sum(1 for t in trades if t["exit_reason"] == "MIDLINE_FULL_TP"),
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
    jobs: List[Tuple[str, str, int, int, str]] = []
    for fixture in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
        n_windows = len(load_fixture(fixture)) // per_window
        for w in range(n_windows):
            for variant in VARIANTS:
                jobs.append((fixture, symbol, w, per_window, variant))

    tag = "CONTROL (USDC real)" if args.control else "MAIN (swap proxy)"
    print(f"{tag}: {len(jobs)} cells (window={args.window_days}d = {per_window} candles), "
          f"variants={list(VARIANTS)}, fee={be.ROUND_TRIP_FEE_PCT}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(_cell, jobs))

    by = {(r["fixture"], r["window"], r["variant"]): r for r in rows}

    # Full table, all 5 variants side by side.
    print("\n| okno | zakres | OFF | UNPROTECTED(1) | BREAKEVEN(2) | INVALIDATION(3) | FULL_EXIT(4) |")
    print("|---|---|---|---|---|---|---|")
    for fixture in args.fixtures:
        symbol = "ETH" if "ETH" in fixture.upper() else "BTC"
        wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
        for w in wins:
            cells = {v: by[(fixture, w, v)] for v in VARIANTS}
            print(f"| {symbol}_W{w + 1} | {cells['OFF']['start']}..{cells['OFF']['end']} | "
                  + " | ".join(f"{cells[v]['total_pnl'] * 100:+.2f}%" for v in VARIANTS) + " |")

    for variant in GATED_VARIANTS:
        print(f"\n{'=' * 70}\n=== {variant} vs {BASELINE} baseline ===\n{'=' * 70}")
        per_symbol_delta: Dict[str, List[float]] = {}
        for fixture in args.fixtures:
            symbol = "ETH" if "ETH" in fixture.upper() else "BTC"
            wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
            for w in wins:
                base = by[(fixture, w, BASELINE)]
                on = by[(fixture, w, variant)]
                delta = on["total_pnl"] - base["total_pnl"]
                per_symbol_delta.setdefault(symbol, []).append(delta)
                print(f"  {symbol}_W{w + 1} {base['start']}..{base['end']}: "
                      f"{BASELINE}={base['total_pnl'] * 100:+.2f}% {variant}={on['total_pnl'] * 100:+.2f}% "
                      f"delta={delta * 100:+.2f}pp")

        print(f"  --- {variant} summary per symbol ---")
        all_pass = True
        for symbol, deltas in per_symbol_delta.items():
            n = len(deltas)
            pos = sum(1 for d in deltas if d > 0)
            mean = sum(deltas) / n
            p = _sign_test(pos, n)
            gate_pass = pos >= math.ceil(0.8 * n)
            all_pass = all_pass and gate_pass
            print(f"    {symbol}: {pos}/{n} positive vs {BASELINE} "
                  f"(mean Δ={mean * 100:+.3f}pp, sign-test p={p:.4f}) "
                  f"-> gate {'PASS' if gate_pass else 'FAIL'} (need >= {math.ceil(0.8 * n)}/{n})")
        print(f"  {variant} OVERALL vs {BASELINE}: {'PASS' if all_pass else 'FAIL'}")


if __name__ == "__main__":
    main()

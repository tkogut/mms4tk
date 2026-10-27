#!/usr/bin/env python3
"""Faza 4, krok 0 (task.md): MFE (Max Favorable Excursion) gate for candidate #2
("D1 Range Target"), BEFORE building any grid for it.

Context. Candidate #2 proposes a hard take-profit at `entry +/- k * D1_range_ref`
(a wide, multi-day price target). Before spending any grid-search budget on it,
this measures whether there is even room for such a target to matter: for every
position `run_backtest()` opens, how far did price move IN THE POSITION'S FAVOR
between `entry_idx` and `exit_idx` (MFE, as a % of entry), grouped by
`exit_reason` -- particularly the population closed by the Inactivity Manager
(STALE_SPLIT_50 / FORCE_CLOSE / FORCE_CLOSE_BE / BE_ESCAPE), which prior
attribution in this session found to be ~75% of all exit events.

Gate (task.md, Faza 4, candidate #2 spec): if the MEDIAN MFE for the Inactivity
population does not significantly exceed 0.10% of entry (~`ROUND_TRIP_FEE_PCT`,
i.e. the move barely covers the round-trip cost before any profit) -- candidate
#2 dies HERE. No grid is built for it regardless of the result (a clear death is
just as valid and complete an outcome as a pass).

MFE definition (task.md): for a long, `max(high)` over `[entry_idx, exit_idx]`
minus `entry_price`, as a fraction of `entry_price`; for a short, `entry_price`
minus `min(low)` over the same window, as a fraction of `entry_price`. Uses each
trade record's own `entry_idx`/`exit_idx` (added to `run_backtest()`'s trade
dicts for exactly this measurement -- see backtest_engine.py) against the ORIGINAL
candle list `run_backtest()` was given, so the window always matches what that
specific trade record actually experienced (a partial's window ends at its own
booking index, not at the position's eventual final close).

Windowing: this engine's `evaluate_signals()` recomputes TMA/ATR over the WHOLE
candle window it is given on every step (see analytics.py -- `df =
TMAIndicator.calculate(closed_candles, params)` inside the per-step call), so a
single continuous run over the full ~24-month fixture is O(n^2) and, per
task.md's Faza 3.3 measurement (2928 candles/122d ~3min, growing worse than
linearly), would take on the order of HOURS for the full ~730-day fixture. This
script therefore reuses the SAME methodology as every other A/B analysis this
session (Faza 2a/2b/3.2/3-4): 5 independent, non-overlapping 122-day windows per
symbol (10 cells total) -- each window is short enough to run in seconds, and
pooling MFE observations across all 10 independent windows is a a strictly
BETTER-conditioned population estimate than one long continuous run would be
(more independent samples of "what does a real Inactivity exit's favorable
excursion look like", not fewer).

Production settings: engine_mode=ADAPTIVE, VR 1.6/0.8, stale_candles_threshold=12,
trend filter OFF, addon_sl_variant default (V2), net-fee-calibrated
ROUND_TRIP_FEE_PCT.

Usage:
    PYTHONPATH=src python3 scripts/mfe_inactivity_gate_analysis.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
INACTIVITY_REASONS = ("STALE_SPLIT_50", "FORCE_CLOSE", "FORCE_CLOSE_BE", "BE_ESCAPE")


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def mfe_pct(candles: List[Candle], trade: Dict[str, Any]) -> float:
    """MFE, as a fraction of entry_price (not yet *100), over
    [entry_idx, exit_idx] inclusive against the ORIGINAL (window-local) candle list."""
    lo = trade["entry_idx"]
    hi = trade["exit_idx"]
    window = candles[lo:hi + 1]
    entry = trade["entry_price"]
    if trade["side"] == "long":
        return (max(c.high for c in window) - entry) / entry
    return (entry - min(c.low for c in window)) / entry


def _sign_test_ge(values: List[float], threshold: float) -> int:
    return sum(1 for v in values if v > threshold)


def _cell(args: Tuple[str, str, int, int]) -> Dict[str, Any]:
    fixture, symbol, w, per_window = args
    candles = load_fixture(fixture)[w * per_window:(w + 1) * per_window]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False)  # addon_sl_variant left at default (V2)
    by_reason: Dict[str, List[float]] = {}
    for t in trades:
        by_reason.setdefault(t["exit_reason"], []).append(mfe_pct(candles, t))
    return {"fixture": fixture, "symbol": symbol, "window": w, "n_trades": len(trades), "by_reason": by_reason}


def measure(fixture: str, window_days: int, workers: int) -> Dict[str, Any]:
    symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
    per_window = window_days * 24
    n_windows = len(load_fixture(fixture)) // per_window
    jobs = [(fixture, symbol, w, per_window) for w in range(n_windows)]
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
        cells = list(ex.map(_cell, jobs))
    by_reason: Dict[str, List[float]] = {}
    n_trades = 0
    for cell in cells:
        n_trades += cell["n_trades"]
        for reason, values in cell["by_reason"].items():
            by_reason.setdefault(reason, []).extend(values)
    return {"fixture": fixture, "symbol": symbol, "n_trades": n_trades, "n_windows": len(cells),
            "by_reason": by_reason}


def _stats_line(label: str, values: List[float], threshold: float) -> str:
    if not values:
        return f"    {label:<16} n=0"
    med = statistics.median(values)
    mean = statistics.mean(values)
    p25 = statistics.quantiles(values, n=4)[0] if len(values) >= 4 else min(values)
    p75 = statistics.quantiles(values, n=4)[2] if len(values) >= 4 else max(values)
    above = _sign_test_ge(values, threshold)
    return (f"    {label:<16} n={len(values):>4d}  median={med*100:+6.3f}%  mean={mean*100:+6.3f}%  "
            f"p25={p25*100:+6.3f}%  p75={p75*100:+6.3f}%  "
            f"above_{threshold*100:.2f}%_thr={above}/{len(values)} ({above/len(values)*100:.1f}%)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()

    threshold = be.ROUND_TRIP_FEE_PCT  # 0.10003% -- "covers round-trip cost" reference line
    print(f"fee threshold = {threshold*100:.4f}% (ROUND_TRIP_FEE_PCT), window={args.window_days}d\n")

    all_inactivity: List[float] = []
    per_symbol_inactivity: Dict[str, List[float]] = {}
    for fixture in args.fixtures:
        result = measure(fixture, args.window_days, args.workers)
        print(f"=== {fixture} ({result['symbol']}, {result['n_windows']} windows, "
              f"n={result['n_trades']} trade records) ===")
        reasons = sorted(result["by_reason"].keys(),
                         key=lambda r: (r not in INACTIVITY_REASONS, r))
        inactivity_values: List[float] = []
        for reason in reasons:
            values = result["by_reason"][reason]
            tag = " [INACTIVITY]" if reason in INACTIVITY_REASONS else ""
            print(_stats_line(reason + tag, values, threshold))
            if reason in INACTIVITY_REASONS:
                inactivity_values.extend(values)
        print(_stats_line("INACTIVITY(all)", inactivity_values, threshold))
        pct_inactivity = len(inactivity_values) / result["n_trades"] * 100 if result["n_trades"] else 0.0
        print(f"    -> inactivity share of all exit events: {len(inactivity_values)}/{result['n_trades']} "
              f"({pct_inactivity:.1f}%)\n")
        all_inactivity.extend(inactivity_values)
        per_symbol_inactivity[result["symbol"]] = inactivity_values

    print("=== POOLED across all fixtures ===")
    print(_stats_line("INACTIVITY(all)", all_inactivity, threshold))
    median_all = statistics.median(all_inactivity) if all_inactivity else float("nan")

    print("\n=== GATE VERDICT ===")
    # "Significantly exceeds" operationalized as: pooled median clears the fee
    # threshold by a wide margin (>= 2x, i.e. there is room for a profit beyond
    # just covering the round trip) AND this holds per-symbol too (not an
    # artifact of one symbol's distribution dominating the pool).
    per_symbol_medians = {sym: statistics.median(v) if v else float("nan")
                          for sym, v in per_symbol_inactivity.items()}
    for sym, med in per_symbol_medians.items():
        print(f"  {sym}: median MFE (inactivity population) = {med*100:+.4f}%")
    print(f"  POOLED: median MFE (inactivity population) = {median_all*100:+.4f}%  "
          f"vs threshold {threshold*100:.4f}%")

    passes = (median_all >= 2 * threshold
             and all(med >= 2 * threshold for med in per_symbol_medians.values()))
    if passes:
        print("  PASS: median MFE clears >=2x the round-trip fee threshold, pooled AND per-symbol.")
        print("  -> candidate #2 (D1 Range Target) proceeds to the grid.")
    else:
        print("  FAIL: median MFE does NOT clear >=2x the round-trip fee threshold "
              "(pooled and/or per-symbol) -- it barely covers cost, if at all.")
        print("  -> candidate #2 (D1 Range Target) DIES HERE. No grid built.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Phase 3.3: rolling TMA/ATR recalibration vs the static production parameter set.

Request (verbatim, from the user, task.md Faza 3): "Zastąpmy statyczny zestaw
parametrów rolowanym oknem kroczącym. Wprowadźmy kroczącą rekalibrację
parametrów TMA/ATR co 7-14 dni, wykorzystując przesuwające się w czasie okna
testowe, zamiast uśredniania jednego zestawu dla całego okresu 610 dni."

Context this measurement answers (Faza 2a/2b, task.md "Sanity-check głównego
znaleziska Fazy 2"): on the 610-day / 5x122d-window `_swap_24m` proxy, EVERY
combination of window x engine_mode x base_sl_pct is deeply negative (median
window total_pnl ~= -23% net), and the worst window (BTC_W1,
2024-09-10..2025-01-10) is not a data artifact -- it is a +64.4% BTC rally
(mean-reversion strategy vs a strong trend = its worst possible regime). The
open question this script measures: does letting `tma_length`/`atr_period`
adapt on a rolling basis (instead of one static pair fixed for the whole
period) recover any of that, or is the loss structural (regime, not
parameterization)?

THIS IS NOT A TRAIN/TEST SPLIT. The user's request is explicit about that:
recalibrate on a window and apply the recalibrated parameters TO SIGNALS
GENERATED IN THAT SAME WINDOW ("w locie"), not to a held-out future window.
That is a deliberate, named departure from the train/test methodology
`backtest_engine.train_test_validate` uses elsewhere in this codebase, and it
means every number this script reports is in-sample for its own window -- see
"Known limitation" below before reading the results as an OOS performance
claim.

Recalibration method (brute-force grid, not ATR-derived): for every
calibration window, run the REAL `backtest_engine.run_backtest()` under all 9
cells of `TMA_GRID x ATR_GRID` = {20,34,50} x {10,14,21} (the literal grid the
task specified) and keep whichever cell produced the best NET total_pnl on
that window. Rationale for grid search over a volatility-derived formula
(e.g. `atr_period` scaled by realized vol, `tma_length` scaled by
autocorrelation half-life): the strategy's edge (or lack of one, per Faza 2)
comes from where price sits relative to a TMA+-ATR band, not from a
closed-form relationship between window volatility and the optimal band
width -- no such formula exists in this codebase or the audit docs, and
inventing one here would be an untested new hypothesis layered on top of an
already-untested one. A small, explicit, brute-force grid is the same
technique `backtest_engine._grid_search_train` already uses for the
production trend-filter recalibration engine (P7), so this reuses an
already-reviewed pattern instead of adding a new one.

Known limitation (in-sample selection bias): choosing the best-of-9 cell on
the SAME data it is scored on will, on pure noise, produce a positive-looking
number even with zero real edge -- more so the shorter the window (7d) and
the larger the grid. This script does not correct for that (the user's own
spec says this measurement is deliberately not OOS); the mitigation used
instead is (a) reporting parameter-stability across windows (Faza 3 point 4)
-- a rolling recalibration that is actually tracking structure should pick
similar parameters window-to-window, one chasing noise should not -- and (b)
a windowed-static control (see below) that isolates the recalibration effect
from the window-chopping-itself effect.

Three arms are computed per test window, not two, because window-chopping
alone changes results (a fresh `run_backtest()` call resets the state
machine -- SCOUT_MODE, Post-SL Cooldown, Single Addon Guard, any position open
at a window boundary is simply dropped -- so five independent 7-day windows
do NOT behave like one continuous 35-day run):

  1. "rolling"          -- best-of-9-grid cell chosen PER calibration window
                            (7d or 14d sub-windows), state reset at every
                            sub-window boundary. This is the hypothesis under
                            test.
  2. "windowed_static"   -- the (20, 14) grid cell ONLY (today's production
                            default), computed on the SAME sub-windows as
                            (1), same state resets. This isolates "does
                            recalibrating help" from "does chopping into
                            small windows help/hurt on its own" -- (1) vs (2)
                            is the fair comparison the task asks for, not (1)
                            vs (3).
  3. "continuous_static" -- the (20, 14) grid cell run ONCE over an entire,
                            unchopped test window (122d) or the full 610d
                            span, no resets. This reproduces what today's
                            production parameterization actually measures as
                            (and is a cross-check against the already-
                            published Faza 2a ADAPTIVE column -- same fixture,
                            same 122d windows, same production settings, same
                            V2 SL mechanism).

Test windows: the SAME 5 non-overlapping 122-day windows Faza 2a/2b used
(BTC_W1..W5 / ETH_W1..W5, 2024-09-10..2026-05-13 on `_swap_24m`) -- so this
script's per-window numbers slot directly next to that table, including the
BTC_W1 +64.4% rally window. Calibration windows (7d/14d) are cut INSIDE each
122-day test window (dropping a small remainder: 2928 // 168 = 17 x 7d
windows, 2928 // 336 = 8 x 14d windows), so both configurations stay aligned
to the same regime boundaries Faza 2a already established.

Production settings held fixed throughout (identical to Faza 2a/2b):
engine_mode=ADAPTIVE, adaptive_vr_high/low=1.6/0.8, stale_candles_threshold=12,
trend filter OFF, addon_sl_variant=default (V2), net PnL under the A1-calibrated
fee/funding model (`backtest_engine.ROUND_TRIP_FEE_PCT`/`FUNDING_RATE_PCT_PER_INTERVAL`
module defaults, not overridden).

Usage:

    # headline measurement: both 7d and 14d configs, all 5 test windows, both symbols
    PYTHONPATH=src python3 scripts/rolling_tma_atr_recalibration.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv

    # quick smoke check on a small slice (what the unit test exercises)
    PYTHONPATH=src python3 scripts/rolling_tma_atr_recalibration.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv --max-test-windows 1 \\
        --test-window-days 10 --calib-days 7
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")

# The literal grid from the task spec -- 9 cells, not a wider/tuned search.
TMA_GRID = [20, 34, 50]
ATR_PERIOD_GRID = [10, 14, 21]
STATIC_DEFAULT = (20, 14)  # today's production IndicatorParams() default

PRODUCTION_KW = dict(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def symbol_for(fixture_name: str) -> str:
    return "ETH/USDC" if "ETH" in fixture_name.upper() else "BTC/USDC"


def _candle_date(c: Candle) -> str:
    return datetime.fromtimestamp(c.timestamp / 1000, timezone.utc).date().isoformat()


def _run_one(symbol: str, candles: List[Candle], tma_length: int, atr_period: int) -> Dict[str, Any]:
    """One `run_backtest()` call + `_stats()`. Pure function, safe to run in a worker
    process -- no shared/module-level mutation (unlike `sl_grid_analysis.py`'s
    `BASE_SL_PCT` patch, `tma_length`/`atr_period` are ordinary `IndicatorParams`
    fields, not process-global state)."""
    params = IndicatorParams(tma_length=tma_length, atr_period=atr_period, **PRODUCTION_KW)
    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False)
    stats = be._stats(trades)
    return {
        "tma_length": tma_length, "atr_period": atr_period,
        "total_pnl": stats["total_pnl"], "rr": stats["rr"], "trades": len(trades),
        "sl_exits": sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
    }


def _calibrate_subwindow(job: Tuple[str, List[Candle], int, int, int]) -> Dict[str, Any]:
    """Grid-search one calibration sub-window: run all 9 (tma_length, atr_period)
    cells, pick the best net total_pnl ("rolling"), and separately report the
    (20, 14) cell ("windowed_static") -- both computed under IDENTICAL state resets
    (one `run_backtest()` call each, same candles), so their difference isolates
    the recalibration effect from the window-chopping effect. Runs in its own
    process (one sub-window per job)."""
    symbol, candles, calib_days, test_window_idx, sub_idx = job
    grid: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for t in TMA_GRID:
        for a in ATR_PERIOD_GRID:
            grid[(t, a)] = _run_one(symbol, candles, t, a)

    best_key = max(grid, key=lambda k: grid[k]["total_pnl"])
    rolling = grid[best_key]
    static = grid[STATIC_DEFAULT]
    return {
        "symbol": symbol, "calib_days": calib_days, "test_window": test_window_idx,
        "sub_window": sub_idx,
        "start": _candle_date(candles[0]), "end": _candle_date(candles[-1]),
        "chosen_tma_length": best_key[0], "chosen_atr_period": best_key[1],
        "rolling_total_pnl": rolling["total_pnl"], "rolling_rr": rolling["rr"],
        "rolling_trades": rolling["trades"], "rolling_sl_exits": rolling["sl_exits"],
        "static_total_pnl": static["total_pnl"], "static_rr": static["rr"],
        "static_trades": static["trades"], "static_sl_exits": static["sl_exits"],
    }


def build_subwindow_jobs(
    candles: List[Candle], symbol: str, calib_days_list: List[int],
    test_window_days: int, max_test_windows: Optional[int],
) -> List[Tuple[str, List[Candle], int, int, int]]:
    """Slice `candles` into the same non-overlapping `test_window_days`-long test
    windows Faza 2a/2b used (dropping any tail remainder, and capped at
    `max_test_windows` so this defaults to exactly the 5x122d = 610d headline
    window), then further slice each test window into `calib_days`-long
    calibration sub-windows (dropping a small remainder inside each test window)."""
    per_test_window = test_window_days * 24
    n_test_windows = len(candles) // per_test_window
    if max_test_windows is not None:
        n_test_windows = min(n_test_windows, max_test_windows)

    jobs: List[Tuple[str, List[Candle], int, int, int]] = []
    for w in range(n_test_windows):
        window_candles = candles[w * per_test_window:(w + 1) * per_test_window]
        for calib_days in calib_days_list:
            per_sub = calib_days * 24
            n_sub = len(window_candles) // per_sub
            for s in range(n_sub):
                sub = window_candles[s * per_sub:(s + 1) * per_sub]
                jobs.append((symbol, sub, calib_days, w, s))
    return jobs


def continuous_baseline(
    candles: List[Candle], symbol: str, test_window_days: int, max_test_windows: Optional[int],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """The (20, 14) cell run ONCE per test window (no sub-window chopping -- this
    is what Faza 2a's ADAPTIVE column already measured, kept here as a
    cross-check) and ONCE over the entire capped span (the "one static set
    averaged over the whole 610-day period" baseline the task asks to compare
    against)."""
    per_test_window = test_window_days * 24
    n_test_windows = len(candles) // per_test_window
    if max_test_windows is not None:
        n_test_windows = min(n_test_windows, max_test_windows)
    per_window_rows = []
    for w in range(n_test_windows):
        window_candles = candles[w * per_test_window:(w + 1) * per_test_window]
        r = _run_one(symbol, window_candles, *STATIC_DEFAULT)
        r.update(symbol=symbol, test_window=w, start=_candle_date(window_candles[0]),
                 end=_candle_date(window_candles[-1]))
        per_window_rows.append(r)

    full_span = candles[:n_test_windows * per_test_window]
    full_row = _run_one(symbol, full_span, *STATIC_DEFAULT) if full_span else {"total_pnl": 0.0, "rr": float("nan"), "trades": 0}
    full_row.update(symbol=symbol, candles=len(full_span))
    return per_window_rows, full_row


def _stability(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Parameter stability across chronologically-ordered calibration windows:
    how often each (tma_length, atr_period) cell wins, and how often the winner
    CHANGES from the immediately preceding window (a rolling recalibration
    tracking real structure should settle near one combo; one chasing noise
    should switch often)."""
    ordered = sorted(rows, key=lambda r: (r["test_window"], r["sub_window"]))
    combos = [(r["chosen_tma_length"], r["chosen_atr_period"]) for r in ordered]
    counts = Counter(combos)
    n = len(combos)
    switches = sum(1 for i in range(1, n) if combos[i] != combos[i - 1])
    mode_combo, mode_count = (counts.most_common(1)[0] if counts else ((None, None), 0))
    return {
        "n_windows": n,
        "mode_combo": mode_combo,
        "mode_share": (mode_count / n) if n else 0.0,
        "switch_rate": (switches / (n - 1)) if n > 1 else 0.0,
        "distribution": dict(counts),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--calib-days", nargs="+", type=int, default=[7, 14],
                    help="calibration window length(s) in days -- 7 and 14 are the "
                         "literal range from the user request, tested as separate "
                         "configs, not averaged together")
    ap.add_argument("--test-window-days", type=int, default=122,
                    help="must match Faza 2a/2b's window length to keep the "
                         "per-window comparison valid")
    ap.add_argument("--max-test-windows", type=int, default=5,
                    help="caps at the 610d/5-window Faza 2a headline span; pass a "
                         "larger number to include the ~120d tail Faza 2a excluded")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--csv-out", default=None, help="write the raw per-calibration-window rows here")
    ap.add_argument("--include-continuous", action="store_true", default=False,
                    help="also compute the (20,14) CONTINUOUS baseline (one run_backtest() "
                         "call per unchopped 122d test window, no sub-window resets) via "
                         "`continuous_baseline()`. Off by default: `evaluate_signals()` "
                         "re-derives TMA/ATR over the whole growing window on every step, "
                         "so cost grows faster than linearly with window size, and a 122d "
                         "(2928-candle) continuous run costs noticeably more than the ~25 "
                         "small 7d/14d calibration windows it would be compared against "
                         "combined. Faza 2a (task.md) already published this EXACT number "
                         "(same fixture, same 122d windows, same production settings, "
                         "IndicatorParams() defaults 20/14) as its ADAPTIVE column -- this "
                         "script cites those instead of recomputing them by default. Pass "
                         "this flag only to reproduce/verify that cross-check yourself.")
    args = ap.parse_args()

    all_rows: List[Dict[str, Any]] = []
    continuous_rows: List[Dict[str, Any]] = []
    continuous_full: List[Dict[str, Any]] = []

    for fixture in args.fixtures:
        symbol = symbol_for(fixture)
        candles = load_fixture(fixture)
        jobs = build_subwindow_jobs(candles, symbol, args.calib_days,
                                     args.test_window_days, args.max_test_windows)
        print(f"{fixture}: {len(candles)} candles, {len(jobs)} calibration sub-windows "
              f"({args.calib_days} d configs) x 9 grid cells each", flush=True)
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            done = 0
            futures = [ex.submit(_calibrate_subwindow, j) for j in jobs]
            for fut in futures:
                all_rows.append(fut.result())
                done += 1
                if done % 25 == 0 or done == len(jobs):
                    print(f"  ... {done}/{len(jobs)} calibration sub-windows done", flush=True)

        if args.include_continuous:
            per_win, full_row = continuous_baseline(candles, symbol, args.test_window_days,
                                                      args.max_test_windows)
            continuous_rows.extend(per_win)
            continuous_full.append(full_row)

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            wr.writeheader()
            wr.writerows(all_rows)

    # ── Per-test-window table: rolling vs windowed_static vs continuous_static ──
    for fixture in args.fixtures:
        symbol = symbol_for(fixture)
        print(f"\n=== {fixture} ({symbol}) ===")
        cont_by_w = {r["test_window"]: r for r in continuous_rows if r["symbol"] == symbol}
        for calib_days in args.calib_days:
            print(f"\n  -- calib_days={calib_days} --")
            print(f"  {'W':<4} | {'range':<23} | {'rolling':>9} | {'win_static':>10} | "
                  f"{'cont_static':>11} | {'roll-win_static':>15}")
            sym_rows = [r for r in all_rows if r["symbol"] == symbol and r["calib_days"] == calib_days]
            windows = sorted({r["test_window"] for r in sym_rows})
            roll_sum = win_static_sum = 0.0
            for w in windows:
                w_rows = [r for r in sym_rows if r["test_window"] == w]
                roll = sum(r["rolling_total_pnl"] for r in w_rows)
                win_static = sum(r["static_total_pnl"] for r in w_rows)
                roll_sum += roll
                win_static_sum += win_static
                cont = cont_by_w.get(w, {}).get("total_pnl", float("nan"))
                rng = f"{w_rows[0]['start']}..{w_rows[-1]['end']}"
                print(f"  W{w + 1:<3} | {rng:<23} | {roll * 100:+8.2f}% | {win_static * 100:+9.2f}% | "
                      f"{cont * 100:+10.2f}% | {(roll - win_static) * 100:+14.2f}pp")
            if cont_by_w:
                cont_sum_s = f"{sum(cont_by_w[w]['total_pnl'] for w in windows if w in cont_by_w) * 100:+10.2f}%"
            else:
                cont_sum_s = f"{'see Faza 2a':>11}"
            print(f"  {'SUM':<4} | {'':<23} | {roll_sum * 100:+8.2f}% | {win_static_sum * 100:+9.2f}% | "
                  f"{cont_sum_s} | {(roll_sum - win_static_sum) * 100:+14.2f}pp")

            stab = _stability(sym_rows)
            print(f"  stability: mode combo (tma={stab['mode_combo'][0]}, atr={stab['mode_combo'][1]}) "
                  f"picked in {stab['mode_share'] * 100:.1f}% of {stab['n_windows']} windows; "
                  f"switch rate window-to-window = {stab['switch_rate'] * 100:.1f}%")
            print(f"  distribution: {stab['distribution']}")

        full = next((r for r in continuous_full if r["symbol"] == symbol), None)
        if full is not None:
            print(f"\n  continuous_static, single run over full capped span "
                  f"({full['candles']} candles, tma=20/atr=14): total_pnl={full['total_pnl'] * 100:+.2f}%  "
                  f"rr={full['rr']:.4f}  trades={full['trades']}")
        else:
            print("\n  continuous_static over the full capped span not computed "
                  "(pass --include-continuous) -- cite Faza 2a's published ADAPTIVE "
                  "column sum for this symbol instead (task.md).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""P4 variant #2 re-run: the `base_sl_pct` grid on the LONG history, under V2.

Why this exists (2026-09-11, Phase 2b). The strategic report (task.md, Etap 5,
"P4 wariant #2") swept `base_sl_pct` in {1.5, 2.0, 2.5, 3.0, 3.5}% and concluded
the hypothesis was falsified — widening the stop monotonically hurt ETH net PnL in
both OOS halves, so `base_sl_pct=2.0%` stays. That measurement is invalidated by
two changes that landed AFTER it:

  (a) `run_backtest()` now defaults to `addon_sl_variant="V2"` (= live production
      since P3 V2, v0.44.4+). The old grid ran under the retired V0 mechanism
      (composite 1.25% stop off the addon-averaged avgPx).
  (b) Longer fixtures exist: `ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv` (730 d, a
      USDT-swap PROXY for the production USDC series — OKX has no 24 months of
      USDC history) and `ohlcv_{BTC,ETH}_USDC_1h_max.csv` (384 d, real USDC).

Conceptually V2 also changes WHAT the grid measures: under V2 the addon carries no
stop of its own, so `base_sl_pct` is the single stop in the whole system. The old
grid measured a compromise between two thresholds (base 2% vs composite 1.25%);
this one measures one decision variable.

Method: chop each fixture into consecutive, NON-OVERLAPPING `--window-days`
windows (default 122 d ~ 4 months, the granularity `fixture_power_analysis.py`
uses, so the tables stay comparable), and for every (window x symbol x SL) cell run
the real `backtest_engine.run_backtest()` with production parameters
(engine_mode=ADAPTIVE, VR 1.6/0.8, stale_candles_threshold=12, trend filter off,
default addon_sl_variant=V2) and the P2 net-PnL model with the A1-calibrated
`ROUND_TRIP_FEE_PCT`.

`base_sl_pct` is NOT a `run_backtest()` argument — it is the module-level constant
`backtest_engine.BASE_SL_PCT`, read at call time by both the SL check and V2's own
`sl_pct_override` assignment. It is therefore swept by patching that module global
inside each worker process (one process per cell, so no cross-contamination).

KNOWN COUPLING, deliberately left production-faithful: `state_manager.on_stop_loss_hit`
enters SCOUT_MODE only for `sl_percent >= 0.02`, a hardcoded threshold. At
`base_sl_pct=1.5%` no stop-out can ever trigger SCOUT_MODE, so that row differs
from the others by more than the stop distance alone. That is exactly what
shipping a 1.5% stop to production would do today, so it is modeled, not removed.

Usage:

    # the headline measurement: 5 independent 122d windows on the 730d proxy
    PYTHONPATH=src python3 scripts/sl_grid_analysis.py \
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \
        --window-days 122

    # the V0-vs-V2 isolation on the ORIGINAL 90d fixture the first grid used:
    # --window-days 90 == that report's "full", --window-days 45 == its H1/H2.
    # With --variant V0 this reproduces the original table cell for cell.
    PYTHONPATH=src python3 scripts/sl_grid_analysis.py --variant V0 --window-days 45 \
        --fixtures ohlcv_BTC_USDC_1h.csv ohlcv_ETH_USDC_1h.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
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
GRID = [0.015, 0.020, 0.025, 0.030, 0.035]


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def _cell(args: Tuple[str, str, int, int, float, str]) -> Dict[str, Any]:
    """One (fixture, symbol, window, sl) backtest. Runs in its own process, so the
    `BASE_SL_PCT` patch cannot leak into any other cell."""
    fixture, symbol, w, per_window, sl, variant = args
    candles = load_fixture(fixture)[w * per_window:(w + 1) * per_window]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    be.BASE_SL_PCT = sl
    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, addon_sl_variant=variant)
    stats = be._stats(trades)
    return {
        "fixture": fixture, "symbol": symbol, "window": w, "sl": sl, "variant": variant,
        "total_pnl": stats["total_pnl"], "rr": stats["rr"],
        "trades": len(trades), "full_closes": stats["full_closes"],
        "sl_exits": sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
        "tp_flips": sum(1 for t in trades if t["exit_reason"] == "TP_AND_FLIP"),
        "addon_trades": sum(1 for t in trades if t.get("addon_applied")),
        "start": datetime.fromtimestamp(candles[0].timestamp / 1000, timezone.utc).date().isoformat(),
        "end": datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc).date().isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--csv-out", default=None, help="write the raw per-cell rows here")
    ap.add_argument("--variant", choices=("V0", "V1", "V2"), default="V2",
                    help="addon_sl_variant; V2 is production (and run_backtest's default). "
                         "V0 reproduces the retired pre-P3 mechanism the original P4 grid "
                         "was measured under.")
    args = ap.parse_args()

    per_window = args.window_days * 24
    jobs: List[Tuple[str, str, int, int, float, str]] = []
    for fixture in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
        n_windows = len(load_fixture(fixture)) // per_window
        for w in range(n_windows):
            for sl in GRID:
                jobs.append((fixture, symbol, w, per_window, sl, args.variant))

    print(f"{len(jobs)} cells (window={args.window_days}d = {per_window} candles), "
          f"variant={args.variant}, fee={be.ROUND_TRIP_FEE_PCT}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(_cell, jobs))

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)

    by = {(r["fixture"], r["window"], r["sl"]): r for r in rows}
    for fixture in args.fixtures:
        wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
        print(f"\n=== {fixture} ===")
        header = " | ".join(f"W{w + 1}" for w in wins)
        print(f"  {'SL%':<6} | {header} | SUM")
        for sl in GRID:
            cells = [by[(fixture, w, sl)] for w in wins]
            body = " | ".join(f"{c['total_pnl'] * 100:+6.2f}%" for c in cells)
            print(f"  {sl * 100:<6.1f} | {body} | {sum(c['total_pnl'] for c in cells) * 100:+7.2f}%")
        print(f"  --- R:R / SL-exits / trades ---")
        for sl in GRID:
            cells = [by[(fixture, w, sl)] for w in wins]
            rr = " ".join(f"{c['rr']:.3f}" for c in cells)
            sle = " ".join(f"{c['sl_exits']:>3d}" for c in cells)
            txn = " ".join(f"{c['trades']:>3d}" for c in cells)
            print(f"  {sl * 100:<6.1f} | rr [{rr}] | sl [{sle}] (tot {sum(c['sl_exits'] for c in cells)})"
                  f" | txn [{txn}]")
        # Per-window monotonicity of the widening direction: does SL 3.5% beat 2.0%?
        print("  --- direction per window (net pnl at 2.0% vs wider) ---")
        for w in wins:
            base = by[(fixture, w, 0.020)]["total_pnl"]
            deltas = [(sl, by[(fixture, w, sl)]["total_pnl"] - base) for sl in GRID if sl != 0.020]
            worse_when_wider = all(d < 0 for sl, d in deltas if sl > 0.020)
            print(f"  W{w + 1} {by[(fixture, w, 0.020)]['start']}..{by[(fixture, w, 0.020)]['end']}: "
                  + " ".join(f"{sl * 100:.1f}%:{d * 100:+6.2f}pp" for sl, d in deltas)
                  + f" -> widening {'HURTS' if worse_when_wider else 'does NOT uniformly hurt'}")


if __name__ == "__main__":
    main()

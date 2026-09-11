#!/usr/bin/env python3
"""Phase 3-4 (task.md): D1 ATR exhaustion filter, ON vs OFF, on the long history.

Context. `analytics.HigherTimeframeAnalytics.calculate_higher_tf` already computes
`atr_d1_expansion_ratio` / `is_volatility_expansion` (threshold
`htf_expansion_threshold=2.5`) and `api.py` exposes it at
`/api/v1/analytics/higher_tf` — but nothing in `daemon.py` reads it. It is a
read-only data point for the external Hermes agent today, not an execution guard.
This measures the hypothesis from `.agents/specs/knowledge/Architektura Systemowa
mms4tk...md` sec. 3 and the audit's P5 (ii): does blocking HIGH_RISK_ADD on days
whose D1 range already exceeds `2.5 x ATR_D1` reduce net losses from addons pushed
into a strong trend (worst case: BTC W1, the +64% rally, task.md's Phase 2 sanity
check) — WITHOUT just trading less for no per-trade benefit?

`backtest_engine.run_backtest(..., d1_exhaustion_filter_enabled=True/False)` is the
new optional param (default False = today's production behavior, unchanged). The
filter is causal/intraday (see `_compute_d1_exhaustion_flags`'s docstring) and
touches ONLY the HIGH_RISK_ADD branch — base entries are untouched.

Method: same as `scripts/sl_grid_analysis.py` / `ab_engine_mode_analysis.py` —
5 consecutive, non-overlapping 122-day windows per symbol on the 730-day
`ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv` proxy fixtures, production config
(engine_mode=ADAPTIVE, VR 1.6/0.8, stale_candles_threshold=12, trend filter off,
addon_sl_variant=V2 default), net PnL with the A1-calibrated ROUND_TRIP_FEE_PCT.

Usage:
    PYTHONPATH=src python3 scripts/d1_exhaustion_filter_analysis.py --window-days 122 \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
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


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def _cell(args: Tuple[str, str, int, int, bool]) -> Dict[str, Any]:
    fixture, symbol, w, per_window, filter_enabled = args
    candles = load_fixture(fixture)[w * per_window:(w + 1) * per_window]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, d1_exhaustion_filter_enabled=filter_enabled)
    stats = be._stats(trades)
    addon_trades = [t for t in trades if t.get("addon_applied")]
    addon_closes_net = sum((t["pnl_pct"] - t.get("fee_pct", 0.0) - t.get("funding_pct", 0.0)) * t["weight"]
                           for t in addon_trades)
    return {
        "fixture": fixture, "symbol": symbol, "window": w, "filter": filter_enabled,
        "total_pnl": stats["total_pnl"], "rr": stats["rr"],
        "trades": len(trades), "full_closes": stats["full_closes"],
        "sl_exits": sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
        "addon_count": len(addon_trades),
        "addon_closes_net_pnl": addon_closes_net,
        "start": datetime.fromtimestamp(candles[0].timestamp / 1000, timezone.utc).date().isoformat(),
        "end": datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc).date().isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--csv-out", default=None)
    args = ap.parse_args()

    per_window = args.window_days * 24
    jobs: List[Tuple[str, str, int, int, bool]] = []
    for fixture in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
        n_windows = len(load_fixture(fixture)) // per_window
        for w in range(n_windows):
            for filter_enabled in (False, True):
                jobs.append((fixture, symbol, w, per_window, filter_enabled))

    print(f"{len(jobs)} cells (window={args.window_days}d = {per_window} candles), "
          f"fee={be.ROUND_TRIP_FEE_PCT}, threshold={IndicatorParams().htf_expansion_threshold}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(_cell, jobs))

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)

    by = {(r["fixture"], r["window"], r["filter"]): r for r in rows}
    grand_off = grand_on = 0.0
    wins_on = 0
    total_windows = 0
    for fixture in args.fixtures:
        wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
        print(f"\n=== {fixture} ({by[(fixture, wins[0], False)]['symbol']}) ===")
        for w in wins:
            off = by[(fixture, w, False)]
            on = by[(fixture, w, True)]
            grand_off += off["total_pnl"]
            grand_on += on["total_pnl"]
            total_windows += 1
            if on["total_pnl"] > off["total_pnl"]:
                wins_on += 1
            print(
                f"  W{w + 1} {off['start']}..{off['end']}: "
                f"OFF net={off['total_pnl']*100:+7.2f}% rr={off['rr']:.3f} txn={off['trades']:>3d} "
                f"sl={off['sl_exits']:>2d} addons={off['addon_count']:>2d} addon_pnl={off['addon_closes_net_pnl']*100:+6.2f}pp"
                f"  ||  ON net={on['total_pnl']*100:+7.2f}% rr={on['rr']:.3f} txn={on['trades']:>3d} "
                f"sl={on['sl_exits']:>2d} addons={on['addon_count']:>2d} addon_pnl={on['addon_closes_net_pnl']*100:+6.2f}pp"
                f"  =>  delta(ON-OFF)={(on['total_pnl']-off['total_pnl'])*100:+7.2f}pp"
            )

    print(f"\n=== SUMMARY across {total_windows} independent windows ===")
    print(f"  ON beats OFF (net pnl) in {wins_on}/{total_windows} windows")
    print(f"  SUM OFF = {grand_off*100:+7.2f}%   SUM ON = {grand_on*100:+7.2f}%   "
          f"delta = {(grand_on-grand_off)*100:+7.2f}pp")


if __name__ == "__main__":
    main()

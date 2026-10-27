#!/usr/bin/env python3
"""Faza 5 (2026-09-12): maker-first (postOnly) execution fee measurement.

Last hypothesis of the Profitability Workstream (task.md): re-measure the SAME
24-month trade population (production settings, engine_mode=ADAPTIVE, VR 1.6/0.8,
stale_candles_threshold=12, trend_filter_enabled=False, addon_sl_variant default
V2) under `MAKER_ROUND_TRIP_FEE_PCT` (both legs maker/postOnly) instead of the
default `ROUND_TRIP_FEE_PCT` (both legs taker) -- purely a cost-model swap via
`round_trip_fee_pct_override`, NOT a new signal/filter. Entries/exits/signal logic
are completely unchanged between the two columns; only `fee_pct` on each trade
differs, so any delta is the fee saving alone.

Same methodology as Faza 2a/2b/3.2/4 (`scripts/htf_directional_gate_analysis.py`,
`scripts/mfe_inactivity_gate_analysis.py`): chop each fixture into consecutive,
NON-OVERLAPPING `--window-days` windows (default 122d), one row per window, sum
and per-window sign test.

Fixtures:
  proxy (5+5 non-overlapping 122d windows):
    ohlcv_BTC_USDT_1h_swap_24m.csv, ohlcv_ETH_USDT_1h_swap_24m.csv
  control (real USDC data, however many 122d windows fit in 384 days -> 3+3):
    ohlcv_BTC_USDC_1h_max.csv, ohlcv_ETH_USDC_1h_max.csv

CAVEAT (required by task.md, Faza 5): this assumes 100% fills as maker
(postOnly) -- it does NOT model the real-execution risk of an order NOT filling
at the maker price (fill-rate). This is a measurement of the theoretical
ceiling of fee savings, not a guaranteed, deployed result. `okx_executor.py` is
untouched by this script -- backtest-harness measurement only.

Reproduce:

    PYTHONPATH=src python3 scripts/maker_first_fee_analysis.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \\
        --tag proxy
    PYTHONPATH=src python3 scripts/maker_first_fee_analysis.py \\
        --fixtures ohlcv_BTC_USDC_1h_max.csv ohlcv_ETH_USDC_1h_max.csv \\
        --tag control
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
MODES = ("TAKER", "MAKER")


def load_fixture(name: str):
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def one_run(symbol: str, chunk, fee_override) -> dict:
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    # addon_sl_variant deliberately NOT passed -- use run_backtest's production-tracking default (V2).
    trades = be.run_backtest(symbol, chunk, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, round_trip_fee_pct_override=fee_override)
    s = be._stats(trades)
    return {
        "total_pnl": s["total_pnl"],
        "rr": s["rr"],
        "win_rate": s["win_rate"],
        "txn": len(trades),
        "full_closes": s["full_closes"],
    }


def measure(fixtures, window_days: int, windows, tag: str) -> list:
    results = []
    for name in fixtures:
        symbol = "ETH/USDC" if "ETH" in name.upper() else "BTC/USDC"
        sym = "ETH" if "ETH" in name.upper() else "BTC"
        candles = load_fixture(name)
        per_window = window_days * 24
        n_windows = len(candles) // per_window
        print(f"\n=== {name}: {len(candles)} candles -> {n_windows} x {window_days}d windows ===",
              flush=True)
        sel = range(n_windows) if not windows else [i - 1 for i in windows]
        for w in sel:
            chunk = candles[w * per_window:(w + 1) * per_window]
            row = {
                "window": f"{tag}_{sym}_W{w + 1}",
                "symbol": sym,
                "tag": tag,
                "fixture": name,
                "start": str(datetime.fromtimestamp(chunk[0].timestamp / 1000, timezone.utc).date()),
                "end": str(datetime.fromtimestamp(chunk[-1].timestamp / 1000, timezone.utc).date()),
                "candles": len(chunk),
            }
            row["TAKER"] = one_run(symbol, chunk, None)
            row["MAKER"] = one_run(symbol, chunk, be.MAKER_ROUND_TRIP_FEE_PCT)
            results.append(row)
            t, m = row["TAKER"], row["MAKER"]
            print(f"  {row['window']} {row['start']}..{row['end']}: "
                  f"TAKER pnl={t['total_pnl']:+.4f} rr={t['rr']:.4f} txn={t['txn']} | "
                  f"MAKER pnl={m['total_pnl']:+.4f} rr={m['rr']:.4f} txn={m['txn']} | "
                  f"delta={m['total_pnl'] - t['total_pnl']:+.4f}", flush=True)
    return results


def _sign_test(k: int, n: int) -> float:
    """Exact two-sided binomial sign test p-value for k successes out of n."""
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, i) for i in range(max(k, n - k), n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def report(rows) -> None:
    rows.sort(key=lambda r: (r["tag"], {"BTC": 0, "ETH": 1}.get(r["symbol"], 9), r["window"]))
    print("| okno | zakres (UTC) | TAKER (baseline) | MAKER | Δ (MAKER-TAKER) pp | txn |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        t, m = r["TAKER"], r["MAKER"]
        print(f"| {r['window']} | {r['start']}..{r['end']} | {t['total_pnl'] * 100:+.2f}% | "
              f"{m['total_pnl'] * 100:+.2f}% | {(m['total_pnl'] - t['total_pnl']) * 100:+.2f} | "
              f"{t['txn']} |")

    for tag in sorted(set(r["tag"] for r in rows)):
        for sym in ("BTC", "ETH", None):
            sub = [r for r in rows if r["tag"] == tag and (sym is None or r["symbol"] == sym)]
            if not sub:
                continue
            n = len(sub)
            d = [r["MAKER"]["total_pnl"] - r["TAKER"]["total_pnl"] for r in sub]
            pos = sum(x > 0 for x in d)
            zero = sum(x == 0 for x in d)
            mean = sum(d) / n
            sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1)) if n > 1 else float("nan")
            print(f"\n[{tag}] {sym or 'BTC+ETH'}: n={n} windows | MAKER>TAKER {pos}/{n} (ties {zero})")
            print(f"  pnl mean Δ = {mean * 100:+.4f} pp, sd = {sd * 100:.4f} pp, "
                  f"sign test {pos}/{n - zero} -> p = {_sign_test(pos, n - zero):.4f}")
            print(f"  pooled (sum of windows): TAKER {sum(r['TAKER']['total_pnl'] for r in sub) * 100:+.2f}% "
                  f"vs MAKER {sum(r['MAKER']['total_pnl'] for r in sub) * 100:+.2f}% "
                  f"(Δ {sum(r['MAKER']['total_pnl'] - r['TAKER']['total_pnl'] for r in sub) * 100:+.2f} pp)")

    print("\nCAVEAT: MAKER column assumes 100%% postOnly fills -- fill-rate risk NOT modeled. "
          "Theoretical ceiling of fee savings, not a guaranteed deployed result.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", help="fixture CSV names under tests/fixtures/")
    ap.add_argument("--tag", default="proxy", help="label for this fixture group (proxy/control)")
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--windows", nargs="*", type=int, default=None, help="1-based window indices")
    ap.add_argument("--json", default=None, help="write raw per-window results here")
    ap.add_argument("--report", nargs="*", default=None,
                    help="print the table/sign tests from previously written --json files")
    args = ap.parse_args()

    if args.report is not None:
        rows = []
        for p in args.report:
            with open(p, encoding="utf-8") as f:
                rows.extend(json.load(f))
        report(rows)
        return

    if not args.fixtures:
        ap.error("--fixtures is required unless --report is used")
    rows = measure(args.fixtures, args.window_days, args.windows, args.tag)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        print(f"\nwrote {args.json}")
    print()
    report(rows)


if __name__ == "__main__":
    main()

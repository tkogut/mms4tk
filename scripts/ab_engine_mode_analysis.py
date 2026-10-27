#!/usr/bin/env python3
"""A/B `engine_mode` (CLASSIC vs ADAPTIVE) over independent out-of-sample windows.

Phase 2a (2026-09-11). The Etap 5 strategic report closed this A/B as "measured,
no conclusive signal" on SIX windows carved out of ONE 90-day fixture, under the
harness default `addon_sl_variant="V0"` — a stop-loss mechanism production retired
on 2026-09-10 (P3 V2). Two things invalidated that measurement:

  * the harness default is now "V2" (matches live production after P3), and
  * `tests/fixtures/ohlcv_{BTC,ETH}_USDT_1h_swap_24m.csv` gives 730 days of real
    history instead of 90 (session A3).

This script re-runs the comparison on any fixture, chopping it into consecutive,
NON-OVERLAPPING `--window-days` windows exactly the way
`scripts/fixture_power_analysis.py` does (same default, 122d ~ 4 months), so the
output is directly comparable with the tables already in `task.md`.

Both modes run with production parameters (VR 1.6/0.8, `stale_candles_threshold=12`,
trend filter off) and — deliberately — WITHOUT passing `addon_sl_variant`, so the
run picks up whatever the production-tracking default is at the time. PnL is net of
the P2 fee/funding model (`ROUND_TRIP_FEE_PCT` calibrated from production in A1).

Reproduce:

    PYTHONPATH=src python3 scripts/ab_engine_mode_analysis.py \
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv

`--windows N [N ...]` runs only the listed (1-based) windows, so a long fixture can
be split across several processes; `--json PATH` dumps the raw per-window numbers
for `--report`, which prints the markdown table and the sign tests from one or more
such dumps without re-running any backtest.
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
MODES = ("CLASSIC", "ADAPTIVE")


def load_fixture(name: str):
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def one_run(symbol: str, chunk, mode: str) -> dict:
    params = IndicatorParams(engine_mode=mode, adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    # addon_sl_variant deliberately NOT passed — use run_backtest's production-tracking default.
    trades = be.run_backtest(symbol, chunk, params,
                             stale_candles_threshold=12, trend_filter_enabled=False)
    s = be._stats(trades)
    return {
        "total_pnl": s["total_pnl"],
        "rr": s["rr"],
        "win_rate": s["win_rate"],
        "txn": len(trades),
        "full_closes": s["full_closes"],
        "sl_exits": len([t for t in trades if t["exit_reason"] == "STOP_LOSS"]),
        "addons": len([t for t in trades if t.get("addon_applied")]),
    }


def measure(fixtures, window_days: int, windows) -> list:
    results = []
    for name in fixtures:
        symbol = "ETH/USDC" if "ETH" in name.upper() else "BTC/USDC"
        tag = "ETH" if "ETH" in name.upper() else "BTC"
        candles = load_fixture(name)
        per_window = window_days * 24
        n_windows = len(candles) // per_window
        print(f"\n=== {name}: {len(candles)} candles -> {n_windows} x {window_days}d windows ===",
              flush=True)
        sel = range(n_windows) if not windows else [i - 1 for i in windows]
        for w in sel:
            chunk = candles[w * per_window:(w + 1) * per_window]
            row = {
                "window": f"{tag}_W{w + 1}",
                "symbol": tag,
                "fixture": name,
                "start": str(datetime.fromtimestamp(chunk[0].timestamp / 1000, timezone.utc).date()),
                "end": str(datetime.fromtimestamp(chunk[-1].timestamp / 1000, timezone.utc).date()),
                "candles": len(chunk),
            }
            for mode in MODES:
                row[mode] = one_run(symbol, chunk, mode)
            results.append(row)
            c, a = row["CLASSIC"], row["ADAPTIVE"]
            print(f"  {row['window']} {row['start']}..{row['end']}: "
                  f"C pnl={c['total_pnl']:+.4f} rr={c['rr']:.4f} txn={c['txn']} sl={c['sl_exits']} | "
                  f"A pnl={a['total_pnl']:+.4f} rr={a['rr']:.4f} txn={a['txn']} sl={a['sl_exits']} | "
                  f"delta={c['total_pnl'] - a['total_pnl']:+.4f}", flush=True)
    return results


def _sign_test(k: int, n: int) -> float:
    """Exact two-sided binomial sign test p-value for k successes out of n."""
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, i) for i in range(max(k, n - k), n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def report(rows) -> None:
    rows.sort(key=lambda r: ({"BTC": 0, "ETH": 1}.get(r["symbol"], 9), r["window"]))
    print("| okno | zakres (UTC) | CLASSIC | ADAPTIVE | Δ (C−A) pp | C R:R | A R:R | txn C→A | SL C→A |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        c, a = r["CLASSIC"], r["ADAPTIVE"]
        print(f"| {r['window']} | {r['start']}..{r['end']} | {c['total_pnl'] * 100:+.2f}% | "
              f"{a['total_pnl'] * 100:+.2f}% | {(c['total_pnl'] - a['total_pnl']) * 100:+.2f} | "
              f"{c['rr']:.4f} | {a['rr']:.4f} | {c['txn']}→{a['txn']} | {c['sl_exits']}→{a['sl_exits']} |")

    for sym in ("BTC", "ETH", None):
        sub = [r for r in rows if sym is None or r["symbol"] == sym]
        if not sub:
            continue
        n = len(sub)
        d = [r["CLASSIC"]["total_pnl"] - r["ADAPTIVE"]["total_pnl"] for r in sub]
        drr = [r["CLASSIC"]["rr"] - r["ADAPTIVE"]["rr"] for r in sub]
        pos, zero, posrr = sum(x > 0 for x in d), sum(x == 0 for x in d), sum(x > 0 for x in drr)
        mean = sum(d) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1)) if n > 1 else float("nan")
        se = sd / math.sqrt(n) if n > 1 else float("nan")
        mrr = sum(drr) / n
        sdrr = math.sqrt(sum((x - mrr) ** 2 for x in drr) / (n - 1)) if n > 1 else float("nan")
        serr = sdrr / math.sqrt(n) if n > 1 else float("nan")
        print(f"\n{sym or 'BTC+ETH'}: n={n} windows | C>A pnl {pos}/{n} (ties {zero}) | C>A R:R {posrr}/{n}")
        print(f"  pnl  mean Δ = {mean * 100:+.3f} pp, sd = {sd * 100:.3f} pp, t = {mean / se:+.3f}, "
              f"sign test {pos}/{n - zero} -> p = {_sign_test(pos, n - zero):.4f}")
        print(f"  R:R  mean Δ = {mrr:+.4f}, sd = {sdrr:.4f}, t = {mrr / serr:+.3f}, "
              f"sign test {posrr}/{n} -> p = {_sign_test(posrr, n):.4f}")
        print(f"  pooled (sum of windows): CLASSIC {sum(r['CLASSIC']['total_pnl'] for r in sub) * 100:+.2f}% "
              f"vs ADAPTIVE {sum(r['ADAPTIVE']['total_pnl'] for r in sub) * 100:+.2f}%")
        tx_c, tx_a = (sum(r[m]["txn"] for r in sub) for m in MODES)
        sl_c, sl_a = (sum(r[m]["sl_exits"] for r in sub) for m in MODES)
        print(f"  txn {tx_c}→{tx_a} ({(tx_a / tx_c - 1) * 100:+.1f}%), "
              f"SL-exits {sl_c}→{sl_a} ({(sl_a / sl_c - 1) * 100:+.1f}%)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", help="fixture CSV names under tests/fixtures/")
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
    rows = measure(args.fixtures, args.window_days, args.windows)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        print(f"\nwrote {args.json}")
    print()
    report(rows)


if __name__ == "__main__":
    main()

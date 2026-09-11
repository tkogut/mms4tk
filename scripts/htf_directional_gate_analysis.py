#!/usr/bin/env python3
"""Faza 3.2: optional H4 directional-bias gate, GATE OFF vs GATE ON.

Hypothesis (task.md, "Sanity-check głównego znalezienia Fazy 2" + Hermes
"asynchronous supervision" model): forcing `directional_bias=LONG_ONLY` when H4
closes strongly above its own TMA upper band (and `SHORT_ONLY` symmetrically below)
should block counter-trend entries during sustained trends -- exactly the regime
(BTC_W1, +64% rally, 2024-09-10..2025-01-10) where the mean-reversion strategy
bleeds worst.

Same methodology as Faza 2a/2b (`scripts/ab_engine_mode_analysis.py`,
`scripts/sl_grid_analysis.py`): chop a fixture into consecutive, NON-OVERLAPPING
`--window-days` windows (default 122d ~ 4 months), run production parameters
(engine_mode=ADAPTIVE, VR 1.6/0.8, `stale_candles_threshold=12`, trend filter OFF
-- that is the SEPARATE, already-decided `tma_trend_filter_enabled`/same-interval
slope filter, untouched here), `addon_sl_variant` left at the run_backtest default
(V2, production), net PnL under the A1-calibrated `ROUND_TRIP_FEE_PCT` + funding.

Reproduce:

    PYTHONPATH=src python3 scripts/htf_directional_gate_analysis.py \
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
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
MODES = ("GATE_OFF", "GATE_ON")


def load_fixture(name: str):
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def one_run(symbol: str, chunk, gate_enabled: bool) -> dict:
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    # addon_sl_variant deliberately NOT passed -- use run_backtest's production-tracking default (V2).
    trades = be.run_backtest(symbol, chunk, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, htf_directional_gate_enabled=gate_enabled)
    s = be._stats(trades)
    return {
        "total_pnl": s["total_pnl"],
        "rr": s["rr"],
        "win_rate": s["win_rate"],
        "txn": len(trades),
        "full_closes": s["full_closes"],
        "sl_exits": len([t for t in trades if t["exit_reason"] == "STOP_LOSS"]),
        "shorts": len([t for t in trades if t.get("side") == "short"]),
        "longs": len([t for t in trades if t.get("side") == "long"]),
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
            row["GATE_OFF"] = one_run(symbol, chunk, False)
            row["GATE_ON"] = one_run(symbol, chunk, True)
            results.append(row)
            o, n_ = row["GATE_OFF"], row["GATE_ON"]
            print(f"  {row['window']} {row['start']}..{row['end']}: "
                  f"OFF pnl={o['total_pnl']:+.4f} rr={o['rr']:.4f} txn={o['txn']} sl={o['sl_exits']} "
                  f"L/S={o['longs']}/{o['shorts']} | "
                  f"ON pnl={n_['total_pnl']:+.4f} rr={n_['rr']:.4f} txn={n_['txn']} sl={n_['sl_exits']} "
                  f"L/S={n_['longs']}/{n_['shorts']} | "
                  f"delta={n_['total_pnl'] - o['total_pnl']:+.4f}", flush=True)
    return results


def _sign_test(k: int, n: int) -> float:
    """Exact two-sided binomial sign test p-value for k successes out of n."""
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, i) for i in range(max(k, n - k), n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def report(rows) -> None:
    rows.sort(key=lambda r: ({"BTC": 0, "ETH": 1}.get(r["symbol"], 9), r["window"]))
    print("| okno | zakres (UTC) | OFF | ON | Δ (ON−OFF) pp | OFF R:R | ON R:R | txn OFF→ON | SL OFF→ON | L/S OFF→ON |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        o, n_ = r["GATE_OFF"], r["GATE_ON"]
        print(f"| {r['window']} | {r['start']}..{r['end']} | {o['total_pnl'] * 100:+.2f}% | "
              f"{n_['total_pnl'] * 100:+.2f}% | {(n_['total_pnl'] - o['total_pnl']) * 100:+.2f} | "
              f"{o['rr']:.4f} | {n_['rr']:.4f} | {o['txn']}→{n_['txn']} | {o['sl_exits']}→{n_['sl_exits']} | "
              f"{o['longs']}/{o['shorts']}→{n_['longs']}/{n_['shorts']} |")

    for sym in ("BTC", "ETH", None):
        sub = [r for r in rows if sym is None or r["symbol"] == sym]
        if not sub:
            continue
        n = len(sub)
        d = [r["GATE_ON"]["total_pnl"] - r["GATE_OFF"]["total_pnl"] for r in sub]
        drr = [r["GATE_ON"]["rr"] - r["GATE_OFF"]["rr"] for r in sub]
        pos, zero, posrr = sum(x > 0 for x in d), sum(x == 0 for x in d), sum(x > 0 for x in drr)
        mean = sum(d) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1)) if n > 1 else float("nan")
        se = sd / math.sqrt(n) if n > 1 else float("nan")
        mrr = sum(drr) / n
        sdrr = math.sqrt(sum((x - mrr) ** 2 for x in drr) / (n - 1)) if n > 1 else float("nan")
        serr = sdrr / math.sqrt(n) if n > 1 else float("nan")
        print(f"\n{sym or 'BTC+ETH'}: n={n} windows | ON>OFF pnl {pos}/{n} (ties {zero}) | ON>OFF R:R {posrr}/{n}")
        print(f"  pnl  mean Δ = {mean * 100:+.3f} pp, sd = {sd * 100:.3f} pp, "
              f"t = {(mean / se) if se == se and se != 0 else float('nan'):+.3f}, "
              f"sign test {pos}/{n - zero} -> p = {_sign_test(pos, n - zero):.4f}")
        print(f"  R:R  mean Δ = {mrr:+.4f}, sd = {sdrr:.4f}, "
              f"t = {(mrr / serr) if serr == serr and serr != 0 else float('nan'):+.3f}, "
              f"sign test {posrr}/{n} -> p = {_sign_test(posrr, n):.4f}")
        print(f"  pooled (sum of windows): OFF {sum(r['GATE_OFF']['total_pnl'] for r in sub) * 100:+.2f}% "
              f"vs ON {sum(r['GATE_ON']['total_pnl'] for r in sub) * 100:+.2f}%")
        tx_o, tx_n = (sum(r[m]["txn"] for r in sub) for m in MODES)
        sl_o, sl_n = (sum(r[m]["sl_exits"] for r in sub) for m in MODES)
        sh_o, sh_n = (sum(r[m]["shorts"] for r in sub) for m in MODES)
        lo_o, lo_n = (sum(r[m]["longs"] for r in sub) for m in MODES)
        print(f"  txn {tx_o}→{tx_n} ({(tx_n / tx_o - 1) * 100:+.1f}%), "
              f"SL-exits {sl_o}→{sl_n} ({(sl_n / sl_o - 1) * 100:+.1f}%), "
              f"shorts {sh_o}→{sh_n}, longs {lo_o}→{lo_n}")


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

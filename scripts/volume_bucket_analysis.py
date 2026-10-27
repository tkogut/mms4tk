#!/usr/bin/env python3
"""Issue #80 step 2: does the SIGNAL CANDLE's volume, relative to its own recent
history, differentiate trade outcome -- measured per symbol (volume means something
different on each market), NOT applied as a filter (that's step 4, explicitly out of
scope -- see the issue's STOP CONDITION).

Hypothesis origin: the 2026-09-27 XAG/USDC incident (entry on a near-empty weekend
candle, ATR=$0.06) plus the external "EMA Alerts" strategy (requires breakout-candle
volume >= 1.2x its own 20-candle average + a minimum turnover floor). This is
DIFFERENT from issue #70's already-rejected "degenerate weekend candles" hypothesis
(day-of-week / ATR==0 collapse) -- #70 never measured the volume/20-candle-average
RATIO directly, only the day-of-week pattern and the ATR==0 edge case.

Research only -- nothing in `src/` is modified for this step. Uses the unmodified
production harness (`backtest_engine.run_backtest`) exactly as
`scripts/htf_directional_gate_analysis.py` / `scripts/ema_cascade_gate_analysis.py`
do: consecutive NON-OVERLAPPING `--window-days` windows (default 122d), production
parameters (engine_mode=ADAPTIVE, VR 1.6/0.8, stale_candles_threshold=12, trend
filter OFF, HTF gate OFF, addon_sl_variant default V2), net PnL via `_stats`'s own
per-trade fee+funding model.

Volume ratio definition (mirrors the daemon.py #80-step-1 instrumentation exactly):
for a signal candle at index i (the CLOSED candle analytics.evaluate_signals fires
on -- `run_backtest`'s own `position["entry_idx"]`), vol_ratio = candles[i].volume /
mean(candles[i-20:i].volume) -- the 20 candles immediately BEFORE the signal candle,
excluding it. Buckets (from the issue, and from the external strategy's own 1.2x
threshold): LOW <0.5, MID 0.5-1.2, HIGH >=1.2.

A position can generate more than one `trades` row (STALE_SPLIT_50 / MIDLINE_PARTIAL_TP
partials followed by a final close) that all share the same `entry_idx` -- those are
grouped back into ONE bucketed observation per opened position, summing their
weighted net pnl, so a position isn't counted 2-3x under its own entry candle's
volume ratio.

Fixtures: use the *_USDC_1h_max series for BTC/ETH (NOT the 24-month *_USDT_swap
fixtures -- per this issue, only the USDC series has realistic zeros/H==L, matching
the live-traded market). No USDC (or any) fixture exists for XAU/XAG -- OKX doesn't
list them as USDC markets at all (production reads the XPERP instrument for those
symbols, see analytics.py / okx_executor.py). This script's companion fetch
(one-off, not checked in as a script) pulled full available 1h history directly from
the live XAU-USD_UM_XPERP-310502 / XAG-USD_UM_XPERP-310509 instruments via OKX's
public EEA history-candles endpoint and wrote
tests/fixtures/ohlcv_XAU_USD_XPERP_1h.csv / ohlcv_XAG_USD_XPERP_1h.csv. Both
instruments listed 2026-04-28/2026-04-30 -- only ~150 days of history exists AT ALL,
so each yields exactly one full 122d window plus a short (~30d) leftover tail
(reported separately, excluded from the cross-window verdict as underpowered).

Reproduce:

    PYTHONPATH=src python3 scripts/volume_bucket_analysis.py \\
        --fixtures ohlcv_BTC_USDC_1h_max.csv ohlcv_ETH_USDC_1h_max.csv \\
                   ohlcv_XAU_USD_XPERP_1h.csv ohlcv_XAG_USD_XPERP_1h.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
BUCKETS = ("LOW", "MID", "HIGH")
VOL_LOOKBACK = 20
LOW_HI = 0.5
MID_HI = 1.2

SYMBOL_TAGS = {
    "BTC": "BTC/USDC",
    "ETH": "ETH/USDC",
    "XAU": "XAU/USD",
    "XAG": "XAG/USD",
}


def load_fixture(name: str):
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def _tag_for(name: str) -> str:
    u = name.upper()
    for tag in ("BTC", "ETH", "XAU", "XAG"):
        if tag in u:
            return tag
    raise ValueError(f"cannot infer symbol tag from fixture name: {name}")


def vol_ratio_at(candles, idx: int):
    """Signal-candle volume / mean volume of the VOL_LOOKBACK candles immediately
    before it (excluding the signal candle itself) -- identical formula to the
    daemon.py step-1 instrumentation. None if there isn't enough history."""
    if idx < VOL_LOOKBACK:
        return None
    window = candles[idx - VOL_LOOKBACK:idx]
    avg = sum(c.volume for c in window) / VOL_LOOKBACK
    if avg <= 0:
        return None
    return float(candles[idx].volume) / avg


def bucket_of(ratio: float) -> str:
    if ratio < LOW_HI:
        return "LOW"
    if ratio < MID_HI:
        return "MID"
    return "HIGH"


def one_run(symbol: str, chunk, htf_gate_enabled: bool):
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    trades = be.run_backtest(symbol, chunk, params, stale_candles_threshold=12,
                             trend_filter_enabled=False,
                             htf_directional_gate_enabled=htf_gate_enabled)

    # Group all trade rows (partials + final close) sharing the same entry_idx back
    # into one observation per OPENED position -- see module docstring.
    by_entry: dict = defaultdict(list)
    for t in trades:
        by_entry[t["entry_idx"]].append(t)

    observations = []
    for entry_idx, rows in by_entry.items():
        ratio = vol_ratio_at(chunk, entry_idx)
        if ratio is None:
            continue
        net = sum((r["pnl_pct"] - r.get("fee_pct", 0.0) - r.get("funding_pct", 0.0)) * r.get("weight", 1.0)
                  for r in rows)
        observations.append({
            "entry_idx": entry_idx,
            "vol_ratio": ratio,
            "bucket": bucket_of(ratio),
            "net_pnl": net,
            "side": rows[0]["side"],
        })
    return observations


def bucket_stats(obs) -> dict:
    out = {}
    for b in BUCKETS:
        sub = [o for o in obs if o["bucket"] == b]
        n = len(sub)
        total = sum(o["net_pnl"] for o in sub)
        wins = sum(1 for o in sub if o["net_pnl"] > 0)
        out[b] = {
            "n": n,
            "total_pnl": total,
            "avg_pnl": (total / n) if n else float("nan"),
            "win_rate": (wins / n * 100) if n else float("nan"),
        }
    return out


def measure(fixtures, window_days: int, windows, htf_gate_enabled: bool) -> list:
    results = []
    for name in fixtures:
        tag = _tag_for(name)
        symbol = SYMBOL_TAGS[tag]
        candles = load_fixture(name)
        per_window = window_days * 24
        n_windows = len(candles) // per_window
        leftover = len(candles) - n_windows * per_window
        print(f"\n=== {name}: {len(candles)} candles -> {n_windows} x {window_days}d windows "
              f"(+{leftover} leftover candles, ~{leftover / 24:.0f}d)===", flush=True)
        sel = range(n_windows) if not windows else [i - 1 for i in windows]
        for w in sel:
            chunk = candles[w * per_window:(w + 1) * per_window]
            _emit_window(results, tag, symbol, name, f"{tag}_W{w + 1}", chunk, partial=False,
                        htf_gate_enabled=htf_gate_enabled)
        # Short leftover tail (relevant for XAU/XAG, which have < 2 full windows of
        # history at all) -- reported separately, excluded from the cross-window
        # verdict (too short to be an independent data point).
        if not windows and leftover >= 24 * 14:  # at least 2 weeks, else not worth reporting
            chunk = candles[n_windows * per_window:]
            _emit_window(results, tag, symbol, name, f"{tag}_PARTIAL", chunk, partial=True,
                        htf_gate_enabled=htf_gate_enabled)
    return results


def _emit_window(results, tag, symbol, name, label, chunk, partial: bool, htf_gate_enabled: bool):
    if len(chunk) < 100:
        return
    obs = one_run(symbol, chunk, htf_gate_enabled)
    stats = bucket_stats(obs)
    row = {
        "window": label,
        "symbol": tag,
        "fixture": name,
        "partial": partial,
        "start": str(datetime.fromtimestamp(chunk[0].timestamp / 1000, timezone.utc).date()),
        "end": str(datetime.fromtimestamp(chunk[-1].timestamp / 1000, timezone.utc).date()),
        "candles": len(chunk),
        "buckets": stats,
    }
    results.append(row)
    parts = " | ".join(
        f"{b} n={stats[b]['n']} pnl={stats[b]['total_pnl']*100:+.2f}% "
        f"avg={stats[b]['avg_pnl']*100:+.3f}% wr={stats[b]['win_rate']:.0f}%"
        for b in BUCKETS
    )
    flag = " [PARTIAL/underpowered]" if partial else ""
    print(f"  {label} {row['start']}..{row['end']}{flag}: {parts}", flush=True)


def _sign_test(k: int, n: int) -> float:
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, i) for i in range(max(k, n - k), n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def report(rows) -> None:
    full_rows = [r for r in rows if not r.get("partial")]
    partial_rows = [r for r in rows if r.get("partial")]

    print("\n" + "=" * 100)
    print("PER-WINDOW BUCKET TABLE (net PnL is the sum across the bucket's positions, "
          "not per-trade average)")
    print("=" * 100)
    print("| window | zakres (UTC) | LOW n/pnl/avg/wr | MID n/pnl/avg/wr | HIGH n/pnl/avg/wr |")
    print("|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["symbol"], r["window"])):
        b = r["buckets"]
        def fmt(x):
            return f"{x['n']}/{x['total_pnl']*100:+.2f}%/{x['avg_pnl']*100:+.3f}%/{x['win_rate']:.0f}%"
        tag = " (partial)" if r.get("partial") else ""
        print(f"| {r['window']}{tag} | {r['start']}..{r['end']} | {fmt(b['LOW'])} | {fmt(b['MID'])} | {fmt(b['HIGH'])} |")

    print("\n" + "=" * 100)
    print("STEP 3 CRITERION: is LOW consistently worse than MID+HIGH combined, "
          "in a majority of INDEPENDENT full windows, on the symbols that HAVE "
          "multiple independent windows (BTC, ETH)? XAU/XAG have only ONE full")
    print("122d window each (their instruments were only listed ~150 days ago) -- "
          "reported separately as single-window, supplementary, non-decisive evidence.")
    print("(partial/leftover windows excluded from this verdict -- see table above for their raw numbers)")
    print("=" * 100)

    def _symbol_delta_summary(sym):
        sub = [r for r in full_rows if r["symbol"] == sym]
        n = len(sub)
        deltas = []  # LOW avg_pnl - combined(MID+HIGH) avg_pnl, per window
        low_worse = 0
        low_has_data = 0
        for r in sub:
            b = r["buckets"]
            low_n, low_pnl = b["LOW"]["n"], b["LOW"]["total_pnl"]
            combined_n = b["MID"]["n"] + b["HIGH"]["n"]
            combined_pnl = b["MID"]["total_pnl"] + b["HIGH"]["total_pnl"]
            if low_n == 0 or combined_n == 0:
                continue
            low_has_data += 1
            low_avg = low_pnl / low_n
            combined_avg = combined_pnl / combined_n
            deltas.append(low_avg - combined_avg)
            if low_avg < combined_avg:
                low_worse += 1
        return n, low_has_data, low_worse, deltas

    MULTI_WINDOW_SYMBOLS = ("BTC", "ETH")
    primary_verdict = {}
    print("\n-- PRIMARY (multi-window: BTC, ETH) --")
    for sym in MULTI_WINDOW_SYMBOLS:
        if sym not in {r["symbol"] for r in full_rows}:
            continue
        n, low_has_data, low_worse, deltas = _symbol_delta_summary(sym)
        print(f"\n{sym}: {n} full window(s), {low_has_data} with LOW-bucket data")
        if low_has_data == 0:
            print("  -- no window had any LOW-volume-bucket trades; cannot evaluate")
            primary_verdict[sym] = None
            continue
        p = _sign_test(low_worse, low_has_data)
        mean_delta = sum(deltas) / len(deltas)
        print(f"  LOW worse than MID+HIGH in {low_worse}/{low_has_data} windows "
              f"(sign test p={p:.4f}, NOT significant with this few windows regardless of outcome)")
        print(f"  mean(LOW avg - combined avg) per window = {mean_delta*100:+.4f} pp "
              f"(per-window deltas: {[f'{d*100:+.3f}pp' for d in deltas]})")
        primary_verdict[sym] = (low_worse, low_has_data, mean_delta, p)

    print("\n-- SUPPLEMENTARY (single window only: XAU, XAG) --")
    supplementary_verdict = {}
    for sym in ("XAU", "XAG"):
        if sym not in {r["symbol"] for r in full_rows}:
            continue
        n, low_has_data, low_worse, deltas = _symbol_delta_summary(sym)
        if low_has_data == 0 or n == 0:
            print(f"{sym}: no usable window -- skipped")
            continue
        mean_delta = deltas[0]
        direction = "supports" if mean_delta < 0 else "contradicts"
        print(f"{sym}: 1 full window ({n} total) -- LOW vs MID+HIGH delta = {mean_delta*100:+.3f}pp "
              f"({direction} the hypothesis). Single window -- NOT decisive either way.")
        supplementary_verdict[sym] = mean_delta

    print("\n" + "-" * 100)
    majority_symbols = [
        s for s, v in primary_verdict.items()
        if v is not None and v[0] > v[1] / 2
    ]
    all_p_significant = all(
        v is not None and v[3] < 0.05 for v in primary_verdict.values()
    )
    print(f"Primary symbols (BTC/ETH) where LOW is worse in a bare majority of windows: {majority_symbols}")
    print(f"Any primary symbol individually statistically significant (p<0.05)? {all_p_significant}")
    if len(majority_symbols) == len([v for v in primary_verdict.values() if v is not None]) and majority_symbols:
        deltas_str = ", ".join(
            f"{s} {v[2]*100:+.3f}pp ({v[0]}/{v[1]})" for s, v in primary_verdict.items() if v is not None
        )
        supp_str = ", ".join(
            f"{s} {d*100:+.3f}pp ({'supports' if d < 0 else 'contradicts'})"
            for s, d in supplementary_verdict.items()
        ) or "no usable data"
        print(f"VERDICT: directionally CONSISTENT on BTC/ETH (LOW worse in a bare majority of windows in "
              f"both: {deltas_str}), but NOT statistically significant at n=3 windows/symbol (3/3 would only "
              f"reach sign-test p=0.25; 2/3 never reaches significance). XAU/XAG (single window each, "
              f"underpowered) do not agree with each other: {supp_str}.")
        print("RECOMMENDATION: inconclusive -- a mild, non-significant directional tilt on crypto, no usable "
              "signal yet on metals (need more elapsed history before a second independent XAU/XAG window "
              "exists). Do NOT implement a filter on this evidence. Re-run with more windows once more "
              "history accumulates (BTC/ETH: rerun periodically as fixtures extend; XAU/XAG: wait for a "
              "second 122d window, ~4 more months from instrument listing).")
    else:
        print("VERDICT: criterion NOT met -- LOW-volume bucket is not consistently worse across the "
              "available symbols/windows. Do not implement a filter on this evidence.")

    if partial_rows:
        print("\n(partial/underpowered windows, excluded above, raw numbers only:)")
        for r in partial_rows:
            b = r["buckets"]
            print(f"  {r['window']} {r['start']}..{r['end']}: "
                  f"LOW n={b['LOW']['n']} pnl={b['LOW']['total_pnl']*100:+.2f}% | "
                  f"MID n={b['MID']['n']} pnl={b['MID']['total_pnl']*100:+.2f}% | "
                  f"HIGH n={b['HIGH']['n']} pnl={b['HIGH']['total_pnl']*100:+.2f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", help="fixture CSV names under tests/fixtures/")
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--windows", nargs="*", type=int, default=None, help="1-based window indices")
    ap.add_argument("--json", default=None, help="write raw per-window results here")
    ap.add_argument("--report", nargs="*", default=None,
                    help="print the table/verdict from previously written --json files")
    ap.add_argument("--htf-gate", action="store_true",
                    help="run with htf_directional_gate_enabled=True, matching the production "
                         "config (DECISIONS.md: on for all 4 active symbols since 2026-09-13/14). "
                         "Default False is the sensitivity/baseline run.")
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
    rows = measure(args.fixtures, args.window_days, args.windows, args.htf_gate)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        print(f"\nwrote {args.json}")
    report(rows)


if __name__ == "__main__":
    main()

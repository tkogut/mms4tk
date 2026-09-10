#!/usr/bin/env python3
"""How much statistical power does a given OHLCV fixture actually buy us?

Session A3 (2026-09-10/11). Every parameter verdict so far (P1-P4, the A/B tests)
was decided on ONE 90-day window, ~2160 candles/symbol -- roughly 180 trades, which
puts a ~+-20pp 90% confidence band around any win-rate style comparison. Two audit
tickets are explicitly blocked on sample size:

  * P5 (per-volatility-zone parameters) needs ~19 trades PER ZONE (3 zones).
  * P8 (session analysis)              needs ~30 trades PER CELL  (4 UTC sessions).

This script chops a fixture into consecutive, non-overlapping windows of
`--window-days` (default 122d ~ 4 months, the granularity the previous strategic
report suggested for independent out-of-sample windows), runs the REAL
`backtest_engine.run_backtest()` on each with production parameters, and reports the
realized trade counts per window, per volatility zone and per UTC session -- so the
"is the data enough?" question is answered with measured counts, not an assumed
trades-per-day rate.

Zone/session are bucketed at the trade's EXIT candle (`exit_idx` is what
`run_backtest` returns); positions are short-lived at 1h granularity, so this is a
close proxy for the entry bucket, not an exact reproduction of what P5/P8 would log
in production.

Usage:

    PYTHONPATH=src python3 scripts/fixture_power_analysis.py \
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams, TMAIndicator  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")

# Audit thresholds these counts are judged against.
P5_MIN_PER_ZONE = 19
P5_ZONES = 3
P8_MIN_PER_CELL = 30

SESSIONS = [("ASIA", 0, 8), ("EU", 8, 16), ("US", 16, 24)]


def load_fixture(name: str):
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    out = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                              low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"])))
    return out


def session_of(ts_ms: int) -> str:
    hour = datetime.fromtimestamp(ts_ms / 1000, timezone.utc).hour
    for name, lo, hi in SESSIONS:
        if lo <= hour < hi:
            return name
    return "?"


def zone_of(vr: float, params: IndicatorParams) -> str:
    if vr > params.adaptive_vr_high:
        return "HIGH"
    if vr < params.adaptive_vr_low:
        return "LOW"
    return "NORMAL"


def analyze(name: str, window_days: int, symbol: str) -> None:
    candles = load_fixture(name)
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    per_window = window_days * 24

    print(f"\n=== {name} ({len(candles)} candles, window={window_days}d = {per_window} candles) ===")
    n_windows = len(candles) // per_window
    if n_windows == 0:
        print("  fixture shorter than one window")
        return

    totals = Counter()
    zone_min, sess_min = [], []
    for w in range(n_windows):
        chunk = candles[w * per_window:(w + 1) * per_window]
        df = TMAIndicator.calculate(chunk, params)
        vr = df["vr"].to_numpy() if "vr" in df else None
        trades = be.run_backtest(symbol, chunk, params, stale_candles_threshold=12, trend_filter_enabled=False)

        zones, sessions = Counter(), Counter()
        for t in trades:
            idx = t.get("exit_idx", 0)
            sessions[session_of(chunk[idx].timestamp)] += 1
            if vr is not None:
                zones[zone_of(float(vr[idx]), params)] += 1
        full = [t for t in trades if not t.get("partial")]
        totals["trades"] += len(trades)
        totals["full"] += len(full)
        zone_min.append(min(zones.get(z, 0) for z in ("HIGH", "NORMAL", "LOW")))
        sess_min.append(min(sessions.get(s[0], 0) for s in SESSIONS))

        start = datetime.fromtimestamp(chunk[0].timestamp / 1000, timezone.utc).date()
        end = datetime.fromtimestamp(chunk[-1].timestamp / 1000, timezone.utc).date()
        print(f"  W{w + 1} {start}..{end}: trades={len(trades)} (full={len(full)}) "
              f"zones={{HIGH:{zones['HIGH']}, NORMAL:{zones['NORMAL']}, LOW:{zones['LOW']}}} "
              f"sessions={{" + ", ".join(f"{s}:{sessions[s]}" for s, _, _ in SESSIONS) + "}")

    print(f"  TOTAL over {n_windows} window(s): trades={totals['trades']} (full={totals['full']}), "
          f"~{totals['trades'] / (n_windows * window_days):.2f} trades/day")
    print(f"  P5 gate (>={P5_MIN_PER_ZONE}/zone x {P5_ZONES} zones, per window): "
          f"worst zone per window = {zone_min} -> "
          f"{'PASS' if all(m >= P5_MIN_PER_ZONE for m in zone_min) else 'FAIL in some window'}")
    print(f"  P8 gate (>={P8_MIN_PER_CELL}/session cell, per window): "
          f"worst session per window = {sess_min} -> "
          f"{'PASS' if all(m >= P8_MIN_PER_CELL for m in sess_min) else 'FAIL in some window'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--window-days", type=int, default=122)
    args = ap.parse_args()
    for name in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in name.upper() else "BTC/USDC"
        analyze(name, args.window_days, symbol)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Issue #80 step 2: fetch the full available 1h history for the XAU/XAG XPERP
instruments directly from OKX's EEA public history-candles endpoint.

Why this exists: no USDC (or any) fixture exists for XAU/XAG anywhere in this repo
-- OKX doesn't list them as USDC markets at all (production reads the XPERP
instrument for those symbols; see analytics.py / okx_executor.py's EEA X-Perp
discovery). The repo's existing `fetch_ohlcv_fixture.py` can't reach them either:
it calls `OKXExecutor.fetch_candles_paginated(symbol, ...)`, which calls
`ex.fetch_ohlcv(symbol, ...)` directly with no XPERP discovery/fallback (that
fallback only exists in `fetch_candles()`, the non-paginated method) -- exactly
the gap issue #80's Analysis section calls out.

This script instead talks to the OKX EEA REST API directly with the real XPERP
instId (found via `GET /api/v5/public/instruments?instType=FUTURES` against
eea.okx.com, filtering `ruleType == "xperp"` -- the same discovery logic as
`OKXExecutor.discover_eea_xperp_symbol`, just resolved once by hand here since
this is a one-off fixture build, not a production code path), paginating
backwards with the `after` cursor until an empty page (pre-listing) is hit.

Both instruments were only listed ~150 days before this was run (2026-09-30):
    XAU-USD_UM_XPERP-310502  listed 2026-04-28  (listTime=1777359600497)
    XAG-USD_UM_XPERP-310509  listed 2026-04-30  (listTime=1777881600396)
so this yields ~3700/~3580 1h candles each -- one full 122-day backtest window
plus a short (~30d) leftover tail, not the multi-window depth BTC/ETH have.

Usage (from the repo root):

    PYTHONPATH=src python3 scripts/fetch_xperp_fixture.py
    PYTHONPATH=src python3 scripts/fetch_xperp_fixture.py --force  # overwrite
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures")
BASE_URL = "https://eea.okx.com/api/v5/market/history-candles"

INSTRUMENTS = [
    ("XAU-USD_UM_XPERP-310502", "ohlcv_XAU_USD_XPERP_1h.csv"),
    ("XAG-USD_UM_XPERP-310509", "ohlcv_XAG_USD_XPERP_1h.csv"),
]


def fetch_all(inst_id: str) -> list:
    """Page backwards through OKX's history-candles endpoint until an empty
    page is returned (pre-listing). Drops the unconfirmed in-progress bar
    (confirm == "0") -- normally only ever the single most recent candle."""
    all_rows: dict = {}
    after = None
    while True:
        url = f"{BASE_URL}?instId={inst_id}&bar=1H&limit=100"
        if after:
            url += f"&after={after}"
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
        rows = data.get("data", [])
        if not rows:
            break
        for r in rows:
            if len(r) > 8 and r[8] == "0":
                continue  # unconfirmed/in-progress candle
            ts = int(r[0])
            all_rows[ts] = (ts, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        oldest_ts = min(int(r[0]) for r in rows)
        if after == oldest_ts:
            break
        after = oldest_ts
        time.sleep(0.15)
        if len(rows) < 100:
            break
    return sorted(all_rows.values(), key=lambda x: x[0])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="overwrite an existing fixture file")
    args = ap.parse_args()

    for inst_id, out_name in INSTRUMENTS:
        out_path = os.path.join(FIXTURES_DIR, out_name)
        if os.path.exists(out_path) and not args.force:
            print(f"[SKIP] {out_path} already exists (use --force to overwrite)")
            continue
        rows = fetch_all(inst_id)
        if not rows:
            raise SystemExit(f"[FAIL] no candles returned for {inst_id}")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for row in rows:
                w.writerow(row)
        span_days = (rows[-1][0] - rows[0][0]) / 86_400_000
        print(f"[OK] {inst_id}: {len(rows)} candles -> {os.path.relpath(out_path, REPO_ROOT)}")
        print(f"     range: {rows[0][0]} .. {rows[-1][0]} ({span_days:.1f} days)")


if __name__ == "__main__":
    main()

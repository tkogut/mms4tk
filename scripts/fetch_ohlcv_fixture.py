#!/usr/bin/env python3
"""Fetch a long OHLCV history from OKX and write it as a test fixture CSV.

Why this exists (session A3, 2026-09-10): every parameter validation done so far
(P1-P4, A/B tests) ran on a SINGLE 90-day window (`tests/fixtures/ohlcv_*_1h.csv`,
~2160 candles/symbol). With that few candles the 90% confidence interval on a
win-rate style metric is on the order of +-20 percentage points -- wide enough that
no A/B verdict from those fixtures is actually decidable. This script rebuilds the
same kind of fixture over a 24-month window so that multiple independent
out-of-sample windows become available.

It deliberately reuses the PRODUCTION fetch path
(`OKXExecutor.fetch_candles_paginated`, PLAN-010 4.1a) rather than re-implementing
pagination, so the fixture data is produced by the exact code the recalibration
engine uses. No API credentials are required -- OKX OHLCV is public market data
(the executor happily runs in "Mock Mode" for this path).

Usage (from the repo root):

    PYTHONPATH=src python3 scripts/fetch_ohlcv_fixture.py \
        --symbols BTC/USDC ETH/USDC --days 730 --suffix _24m

Output format matches the existing fixtures exactly:
`timestamp,open,high,low,close,volume` with the timestamp in milliseconds.

The script never overwrites an existing file unless --force is given: the 90-day
fixtures are load-bearing for the current test suite and must stay untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timezone
from typing import List

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mms4tk.okx_executor import OKXExecutor  # noqa: E402

HOUR_MS = 3600 * 1000
MAX_GAP_MS = 6 * HOUR_MS


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat()


def _fixture_path(symbol: str, timeframe: str, suffix: str) -> str:
    # "BTC/USDT:USDT" (ccxt swap notation) -> "BTC_USDT"; the suffix disambiguates
    # spot from swap in the filename (e.g. "_swap_24m").
    slug = symbol.split(":")[0].replace("/", "_")
    return os.path.join(REPO_ROOT, "tests", "fixtures", f"ohlcv_{slug}_{timeframe}{suffix}.csv")


def write_csv(path: str, candles: List) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for c in candles:
            w.writerow([c.timestamp, c.open, c.high, c.low, c.close, c.volume])


def report_gaps(candles: List, max_gap_ms: int = MAX_GAP_MS) -> List[tuple]:
    """Return [(prev_ts, next_ts, gap_ms)] for every consecutive pair further apart
    than `max_gap_ms`. An empty list means the series has no hole worth worrying
    about for backtesting purposes."""
    gaps = []
    for prev, cur in zip(candles, candles[1:]):
        delta = cur.timestamp - prev.timestamp
        if delta > max_gap_ms:
            gaps.append((prev.timestamp, cur.timestamp, delta))
    return gaps


async def _clamp_days_to_listing(executor: OKXExecutor, symbol: str, days: int) -> int:
    """Shrink `days` so the first page of the pagination loop lands inside the
    instrument's real history.

    `fetch_candles_paginated` walks forward from `now - days` in 300-candle pages and
    stops at the first EMPTY page. If the requested window starts before the
    instrument was listed, that very first page is empty and the whole fetch returns
    nothing (OKX serves no pre-listing candles). Reading `listTime` from the market
    metadata and clamping up front turns that silent zero-row result into an
    explicit, reported truncation.
    """
    ex = executor.exchange
    if ex is None:
        return days
    try:
        if not ex.markets:
            await ex.load_markets()
        market = ex.markets.get(symbol) or {}
        list_time = (market.get("info") or {}).get("listTime")
        if not list_time:
            return days
        listed_ms = int(list_time)
    except Exception as e:  # pragma: no cover - network/metadata best effort
        print(f"     [warn] could not read listTime for {symbol}: {type(e).__name__}: {e}")
        return days

    available_days = (datetime.now(timezone.utc).timestamp() * 1000 - listed_ms) / 86_400_000
    if available_days >= days:
        return days
    clamped = max(1, int(available_days) - 1)
    print(f"[NOTE] {symbol} was listed {_iso(listed_ms)} -> only ~{available_days:.1f} days exist; "
          f"requested {days}, clamping to {clamped}")
    return clamped


async def fetch_one(symbol: str, timeframe: str, days: int, suffix: str, force: bool) -> None:
    path = _fixture_path(symbol, timeframe, suffix)
    if os.path.exists(path) and not force:
        print(f"[SKIP] {path} already exists (use --force to overwrite)")
        return

    executor = OKXExecutor()
    try:
        days = await _clamp_days_to_listing(executor, symbol, days)
        candles = await executor.fetch_candles_paginated(symbol, timeframe, days=days, logger_db=None)
    finally:
        if executor.exchange is not None:
            await executor.exchange.close()

    if not candles:
        raise SystemExit(f"[FAIL] no candles returned for {symbol}")

    write_csv(path, candles)

    span_days = (candles[-1].timestamp - candles[0].timestamp) / 86_400_000
    print(f"[OK] {symbol}: {len(candles)} candles -> {os.path.relpath(path, REPO_ROOT)}")
    print(f"     range: {_iso(candles[0].timestamp)} .. {_iso(candles[-1].timestamp)} ({span_days:.1f} days)")
    print(f"     expected for {days}d at {timeframe}: ~{days * 24} candles")

    gaps = report_gaps(candles)
    if gaps:
        print(f"     [WARN] {len(gaps)} gap(s) > 6h:")
        for prev_ts, next_ts, delta in gaps[:20]:
            print(f"       {_iso(prev_ts)} -> {_iso(next_ts)}  ({delta / HOUR_MS:.1f}h)")
        if len(gaps) > 20:
            print(f"       ... and {len(gaps) - 20} more")
    else:
        print("     no gap > 6h")

    dupes = len(candles) - len({c.timestamp for c in candles})
    if dupes:
        print(f"     [WARN] {dupes} duplicate timestamp(s)")
    if any(b.timestamp <= a.timestamp for a, b in zip(candles, candles[1:])):
        print("     [WARN] timestamps are not strictly increasing")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", nargs="+", default=["BTC/USDC", "ETH/USDC"])
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--suffix", default="_24m", help="appended to the fixture filename stem")
    ap.add_argument("--force", action="store_true", help="overwrite an existing fixture file")
    args = ap.parse_args()

    for symbol in args.symbols:
        await fetch_one(symbol, args.timeframe, args.days, args.suffix, args.force)


if __name__ == "__main__":
    asyncio.run(main())

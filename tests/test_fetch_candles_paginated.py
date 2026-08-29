"""PLAN-010 §4.1a: paginated OHLCV fetch + local cache, without hitting the real
OKX API (mocked ccxt calls only)."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor


@pytest.fixture
def db(tmp_path):
    return LoggerDB(db_path=str(tmp_path / "ohlcv_cache_test.db"))


@pytest.fixture
def ex():
    e = OKXExecutor()
    e.exchange.fetch_ohlcv = AsyncMock()
    e.exchange.rateLimit = 1  # avoid real sleeps in tests
    e.exchange.close = AsyncMock()
    return e


def _candle_row(ts, price=80000.0):
    return [ts, price, price, price, price, 1.0]


def test_fetch_candles_paginated_stops_when_batch_exhausted(db, ex):
    hour_ms = 3_600_000
    base_ts = int(time.time() * 1000) - 3 * hour_ms
    batch = [_candle_row(base_ts + i * hour_ms) for i in range(3)]
    ex.exchange.fetch_ohlcv.side_effect = [batch, []]

    candles = asyncio.run(ex.fetch_candles_paginated("BTC/USDC", timeframe="1h", days=1, logger_db=db))

    assert len(candles) == 3
    assert candles[0].timestamp == base_ts


def test_fetch_candles_paginated_writes_to_cache(db, ex):
    hour_ms = 3_600_000
    base_ts = int(time.time() * 1000) - 2 * hour_ms
    batch = [_candle_row(base_ts + i * hour_ms) for i in range(2)]
    ex.exchange.fetch_ohlcv.side_effect = [batch, []]

    asyncio.run(ex.fetch_candles_paginated("BTC/USDC", timeframe="1h", days=1, logger_db=db))

    cached = db.get_cached_ohlcv("BTC/USDC", "1h")
    assert len(cached) == 2


def test_fetch_candles_paginated_only_fetches_missing_tail(db, ex):
    """A cache that already covers the requested window (from its earliest point)
    must only trigger a fetch for candles NEWER than the last cached one — not a
    full re-fetch of the whole window on every call."""
    hour_ms = 3_600_000
    now_ms = int(time.time() * 1000)
    want_since = now_ms - 1 * 86_400_000  # days=1 below
    cache_start = want_since - 2 * hour_ms  # cache starts BEFORE the requested window
    cache_end = now_ms - hour_ms  # ... and runs up to nearly now

    n = int((cache_end - cache_start) / hour_ms)
    db.upsert_ohlcv_batch("BTC/USDC", "1h", [
        {"timestamp": cache_start + i * hour_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for i in range(n)
    ])
    ex.exchange.fetch_ohlcv.side_effect = [[]]  # nothing new beyond the cached tail

    candles = asyncio.run(ex.fetch_candles_paginated("BTC/USDC", timeframe="1h", days=1, logger_db=db))

    assert len(candles) > 0
    called_since = ex.exchange.fetch_ohlcv.call_args_list[0].kwargs["since"]
    assert called_since > want_since, "should only ask for candles past the cached tail, not the whole window"


def test_fetch_candles_paginated_falls_back_to_cache_on_fetch_failure(db, ex):
    """A transient OKX API failure must not throw away already-cached history —
    return what the cache has rather than raising, when there IS a cache to fall
    back on."""
    hour_ms = 3_600_000
    now_ms = int(time.time() * 1000)
    db.upsert_ohlcv_batch("BTC/USDC", "1h", [
        {"timestamp": now_ms - hour_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ])
    ex.exchange.fetch_ohlcv.side_effect = Exception("OKX unreachable")

    candles = asyncio.run(ex.fetch_candles_paginated("BTC/USDC", timeframe="1h", days=1, logger_db=db))

    assert len(candles) == 1


def test_fetch_candles_paginated_raises_on_failure_with_no_cache(db, ex):
    ex.exchange.fetch_ohlcv.side_effect = Exception("OKX unreachable")

    with pytest.raises(Exception):
        asyncio.run(ex.fetch_candles_paginated("BTC/USDC", timeframe="1h", days=1, logger_db=db))

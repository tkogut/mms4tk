"""Audit Phase 3.2-followup (2026-09-11, real bug found and fixed): before this
fix, `OKXExecutor.fetch_candles` ALWAYS swallowed a real fetch failure and
returned deterministic mock candles instead of raising -- so any caller whose
own fail-safe assumed "an exception means the fetch failed" (e.g.
`daemon._refresh_htf_directional_gate`) never actually saw a failure, even
during a genuine OKX outage. `allow_mock_fallback=False` lets a caller opt into
"tell me the truth" instead of "give me something anyway".

These tests exercise the REAL `OKXExecutor.fetch_candles` (not the global
`tests/conftest.py` class-level fake, which never fails) -- `allow_real_okx_network`
opts out of that autouse patch. No real network call is made: `self.exchange`
is replaced by a fake object whose `fetch_ohlcv` always raises, deterministically
and offline, standing in for "OKX is unreachable". No `pytest-asyncio` plugin is
configured in this repo -- the established pattern (test_daemon.py,
test_htf_directional_gate_production.py) is a plain `asyncio.run()` wrapper.
"""
import asyncio

import pytest

from mms4tk.okx_executor import OKXExecutor, _generate_mock_candles


class _AlwaysFailsExchange:
    async def fetch_ohlcv(self, *args, **kwargs):
        raise ConnectionError("simulated OKX outage")


class _WorksExchange:
    async def fetch_ohlcv(self, *args, **kwargs):
        return [[1_700_000_000_000, 100.0, 101.0, 99.0, 100.5, 5.0]]


@pytest.mark.allow_real_okx_network
def test_fetch_candles_default_still_falls_back_to_mock_on_failure():
    """Regression guard: `allow_mock_fallback` defaults to True, so every
    pre-existing caller (none of which pass the new kwarg) keeps today's exact
    behavior -- a real failure returns mock candles instead of raising."""
    ex = OKXExecutor()
    ex.exchange = _AlwaysFailsExchange()

    candles = asyncio.run(ex.fetch_candles(symbol="BTC/USDC", timeframe="1h", limit=10))

    assert len(candles) == 10
    mock = _generate_mock_candles(count=10)
    assert [c.close for c in candles] == [c.close for c in mock]


@pytest.mark.allow_real_okx_network
def test_fetch_candles_allow_mock_fallback_false_raises_on_failure():
    """The actual fix: a caller that opts out of the mock fallback sees the
    real exception instead of silently getting fabricated data."""
    ex = OKXExecutor()
    ex.exchange = _AlwaysFailsExchange()

    with pytest.raises(ConnectionError, match="simulated OKX outage"):
        asyncio.run(ex.fetch_candles(symbol="BTC/USDC", timeframe="4h", limit=50, allow_mock_fallback=False))


@pytest.mark.allow_real_okx_network
def test_fetch_candles_allow_mock_fallback_false_still_returns_on_success():
    """`allow_mock_fallback=False` must not change behavior on the happy path --
    it only changes what happens when every real attempt fails."""
    ex = OKXExecutor()
    ex.exchange = _WorksExchange()

    candles = asyncio.run(ex.fetch_candles(symbol="BTC/USDC", timeframe="4h", limit=50, allow_mock_fallback=False))

    assert len(candles) == 1
    assert candles[0].close == 100.5

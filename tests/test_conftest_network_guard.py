"""
Regression guard for the global OKX network block in tests/conftest.py.

These two tests exist to catch the specific failure mode that motivated the class-level
patch: an OKXExecutor instance that no file-specific fixture happens to cover, quietly
reaching the real OKX REST API and hanging the suite. They assert both halves of the
problem — a freshly constructed instance, and the module-level singleton that already
existed before any fixture could run.
"""
import asyncio
import time

from mms4tk.api import executor as shared_executor
from mms4tk.okx_executor import OKXExecutor


def test_fresh_executor_is_patched_and_returns_instantly():
    """A fresh OKXExecutor(), never touched by any file-specific fixture, must still be
    blocked from real network access by the global fixture in conftest.py."""
    ex = OKXExecutor()
    start = time.monotonic()
    asyncio.run(ex.initialize())
    candles = asyncio.run(ex.fetch_candles("BTC/USDC", limit=10))
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, "fetch_candles/initialize touched the real network"
    assert len(candles) == 10


def test_shared_singleton_is_also_patched():
    """The executor singleton built by mms4tk.api at module import — i.e. BEFORE any
    pytest fixture could run — must be covered by the same class-level patch."""
    start = time.monotonic()
    result = asyncio.run(shared_executor.verify_symbol_with_okx("BTC/USDC"))
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert result["okx_verified"] is True
    assert result["reason"].startswith("stubbed")

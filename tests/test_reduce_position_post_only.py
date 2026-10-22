"""2026-10-22: reduce_position()'s opt-in post-only (maker) execution path.

Two independent production samples (.ai/analysis/2026-10-14-sl-loss-attribution.md
and a 2026-10-21 follow-up over 97 fresh closes) showed STALE_SPLIT_50 and the
impulse-negation STALE_FORCE_CLOSE's thin gross edge is now smaller than the OKX
taker/maker fee gap -- the freshest week flipped these exits net-negative. These
tests exercise the REAL `OKXExecutor.reduce_position`/`_attempt_post_only_reduce`
(not the global `tests/conftest.py` class-level fake, which never fails) --
`allow_real_okx_network` opts out of that autouse patch. No real network call is
made: `self.exchange` is a fake object with deterministic async methods, standing
in for OKX. Same `asyncio.run()` convention as `test_okx_executor_fetch_candles.py`.
"""
import asyncio

import pytest

from mms4tk.okx_executor import OKXExecutor


class _FakePosition(dict):
    """Minimal stand-in for a ccxt position dict."""


def _make_executor_with_position(fake_exchange, contracts=10.0, side="long"):
    ex = OKXExecutor()
    ex.exchange = fake_exchange
    ex.has_credentials = True

    async def _fetch_positions():
        return [_FakePosition(symbol="BTC/USDC", side=side, contracts=contracts, info={})]

    ex.fetch_positions = _fetch_positions
    return ex


class _FillsImmediatelyExchange:
    """postOnly order fills on the first fetch_order poll -- no market fallback."""

    # Empty (not missing) so discover_eea_xperp_symbol's "markets loaded?" check
    # short-circuits to "no match" instead of raising on an unrelated symbol.
    markets = {}

    def __init__(self):
        self.market_orders = []
        self.limit_orders = []
        self.cancelled = []

    async def fetch_ticker(self, symbol):
        return {"bid": 99.0, "ask": 100.0}

    async def create_order(self, symbol, type, side, amount, params, price=None):
        if type == "limit":
            self.limit_orders.append(
                {"symbol": symbol, "side": side, "amount": amount, "price": price, "params": params}
            )
            return {"id": "maker-1", "status": "open"}
        self.market_orders.append(
            {"symbol": symbol, "side": side, "amount": amount, "params": params}
        )
        return {"id": "market-1", "status": "closed"}

    async def fetch_order(self, order_id, symbol):
        return {"id": order_id, "status": "closed", "symbol": symbol}

    async def cancel_order(self, order_id, symbol):
        self.cancelled.append(order_id)


class _NeverFillsExchange:
    """postOnly order never fills -- times out, gets cancelled, falls back to market."""

    markets = {}

    def __init__(self):
        self.market_orders = []
        self.limit_orders = []
        self.cancelled = []

    async def fetch_ticker(self, symbol):
        return {"bid": 99.0, "ask": 100.0}

    async def create_order(self, symbol, type, side, amount, params, price=None):
        if type == "limit":
            self.limit_orders.append(
                {"symbol": symbol, "side": side, "amount": amount, "price": price, "params": params}
            )
            return {"id": "maker-2", "status": "open"}
        self.market_orders.append(
            {"symbol": symbol, "side": side, "amount": amount, "params": params}
        )
        return {"id": "market-2", "status": "closed"}

    async def fetch_order(self, order_id, symbol):
        return {"id": order_id, "status": "open"}  # never reports filled

    async def cancel_order(self, order_id, symbol):
        self.cancelled.append(order_id)


class _CancelFailsExchange(_NeverFillsExchange):
    """Same as never-fills, but cancel_order itself raises -- must still fall back."""

    async def cancel_order(self, order_id, symbol):
        self.cancelled.append(order_id)
        raise ConnectionError("simulated cancel failure")


@pytest.mark.allow_real_okx_network
def test_post_only_fill_skips_market_fallback():
    """A maker fill before the timeout returns that order directly -- no market
    order is ever placed."""
    fake = _FillsImmediatelyExchange()
    ex = _make_executor_with_position(fake)

    result = asyncio.run(
        ex.reduce_position(
            symbol="BTC/USDC", side="long", reduction_pct=0.50,
            reason="STALE_SPLIT_50", use_post_only=True, post_only_timeout_seconds=3.0,
        )
    )

    assert result["id"] == "maker-1"
    assert len(fake.limit_orders) == 1
    assert fake.limit_orders[0]["params"]["postOnly"] is True
    assert fake.limit_orders[0]["price"] == 100.0  # selling (closing a long) rests at the ask
    assert fake.market_orders == []


@pytest.mark.allow_real_okx_network
def test_post_only_timeout_cancels_and_falls_back_to_market():
    """An unfilled post-only order is cancelled, then the existing guaranteed
    market order still executes -- the exit is never silently skipped."""
    fake = _NeverFillsExchange()
    ex = _make_executor_with_position(fake)

    result = asyncio.run(
        ex.reduce_position(
            symbol="BTC/USDC", side="long", reduction_pct=0.50,
            reason="STALE_FORCE_CLOSE", use_post_only=True, post_only_timeout_seconds=1.2,
        )
    )

    assert result["id"] == "market-2"
    assert len(fake.limit_orders) == 1
    assert fake.cancelled == ["maker-2"]
    assert len(fake.market_orders) == 1
    assert fake.market_orders[0]["params"].get("postOnly") is None


@pytest.mark.allow_real_okx_network
def test_post_only_cancel_failure_still_falls_back_to_market():
    """Even if cancelling the stale maker order itself errors, the position
    still gets closed via the market fallback -- this must never raise up and
    leave a stale position fully unprotected."""
    fake = _CancelFailsExchange()
    ex = _make_executor_with_position(fake)

    result = asyncio.run(
        ex.reduce_position(
            symbol="BTC/USDC", side="long", reduction_pct=0.50,
            reason="STALE_FORCE_CLOSE", use_post_only=True, post_only_timeout_seconds=1.2,
        )
    )

    assert result["id"] == "market-2"
    assert fake.cancelled == ["maker-2"]
    assert len(fake.market_orders) == 1


@pytest.mark.allow_real_okx_network
def test_existing_callers_without_the_flag_are_unaffected():
    """Regression guard: every pre-existing caller (MIDLINE_PARTIAL_TP,
    TAKE_PROFIT_*, FORCE_CLOSE_BE, BE_ESCAPE) omits use_post_only, so it
    defaults to False and must issue the exact same plain market order as
    before -- no ticker fetch, no limit order, ever."""
    fake = _FillsImmediatelyExchange()
    ex = _make_executor_with_position(fake)

    result = asyncio.run(
        ex.reduce_position(symbol="BTC/USDC", side="long", reduction_pct=1.0, reason="FORCE_CLOSE_BE")
    )

    assert result["id"] == "market-1"
    assert fake.limit_orders == []
    assert len(fake.market_orders) == 1
    assert fake.market_orders[0]["params"] == {
        "tdMode": "cross",
        "posSide": "long",
        "reduceOnly": True,
    }


@pytest.mark.allow_real_okx_network
def test_post_only_skipped_when_no_usable_quote():
    """A zero/missing bid-ask (e.g. a thin or halted book) must not place a
    limit order at a nonsense price -- fall straight back to market."""

    class _NoQuoteExchange(_FillsImmediatelyExchange):
        async def fetch_ticker(self, symbol):
            return {"bid": 0.0, "ask": 0.0}

    fake = _NoQuoteExchange()
    ex = _make_executor_with_position(fake)

    result = asyncio.run(
        ex.reduce_position(
            symbol="BTC/USDC", side="long", reduction_pct=0.50,
            reason="STALE_SPLIT_50", use_post_only=True, post_only_timeout_seconds=1.0,
        )
    )

    assert result["id"] == "market-1"
    assert fake.limit_orders == []

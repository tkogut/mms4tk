"""
Tests for position sizing feature (MMS4_MAX_MARGIN_USD / 70% Base Order).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from mms4tk.daemon import MMS4Daemon
from mms4tk.analytics import SignalResult

# These tests size from the daemon's own `trade_amount_usd`, not from symbols.json (#109).
pytestmark = pytest.mark.usefixtures("empty_symbols_store")


def _build_daemon():
    """A BTC/USDC daemon with a $10 allocation, a BASE_LONG signal at $64,800 and mocked I/O."""
    executor = MagicMock()
    executor.initialize = AsyncMock()
    executor.close = AsyncMock()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.execute_order = AsyncMock(return_value={"id": "order-123"})

    state_manager = MagicMock()
    state_manager.leverage = 1.0
    state_manager.current_state.value = "BASE_MODE"
    state_manager.get_summary.return_value = {
        "current_state": "BASE_MODE",
        "leverage": 1.0,
        "current_equity": 30.0,
        "realized_pnl": 0.0,
        "total_trades": 0
    }

    signal_engine = MagicMock()
    # Signal at price $64,800.0 USD
    signal_engine.evaluate_signals.return_value = SignalResult(
        signal_type="BASE_LONG",
        price=64800.0,
        stop_loss=63504.0,
        leverage=1.0,
        is_allowed=True,
        reason="Base LONG signal"
    )

    logger_db = MagicMock()

    # Create daemon with $10 USD trade allocation
    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_manager,
        signal_engine=signal_engine,
        logger_db=logger_db,
        symbol="BTC/USDC",
        trade_amount_usd=10.0
    )
    return daemon, executor


def test_daemon_position_sizing_calculation():
    """Verify order_amount is dynamically calculated from 70% base_trade_usd / price."""
    daemon, executor = _build_daemon()

    result = asyncio.run(daemon.execute_cycle())

    # Expected base units = round(7.0 / 64800.0, 6) = 0.000108 BTC
    expected_amount = round(7.0 / 64800.0, 6)
    executor.execute_order.assert_called_once_with(
        symbol="BTC/USDC",
        side="BUY",
        amount=expected_amount,
        stop_loss_pct=0.02,
        leverage=1.0,
        price=64800.0
    )
    assert daemon.max_margin_usd == 10.0
    assert daemon.base_trade_usd == 7.0
    assert daemon.addon_trade_usd == 3.0


def test_per_symbol_allocation_overrides_trade_amount_usd(empty_symbols_store):
    """Intended behavior, pinned (#109): a configured per-symbol `allocation_usd` AND `base_ratio`
    win over the constructor's `trade_amount_usd` and the default 70/30 split. With
    allocation_usd=100 and base_ratio=60 the base leg is 60 USD at 1x, i.e. not the 7 USD the $10
    constructor default would give, and not the 70 USD a 70% ratio would give."""
    empty_symbols_store["BTC/USDC"] = {"is_active": True, "allocation_usd": 100.0, "base_ratio": 60, "add_ratio": 40}
    daemon, executor = _build_daemon()

    asyncio.run(daemon.execute_cycle())

    executor.execute_order.assert_called_once_with(
        symbol="BTC/USDC",
        side="BUY",
        amount=round(60.0 / 64800.0, 6),
        stop_loss_pct=0.02,
        leverage=1.0,
        price=64800.0,
    )

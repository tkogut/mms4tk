"""
Tests for position sizing feature (MMS4_TRADE_AMOUNT_USD).
"""

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mms4tk.daemon import MMS4Daemon
from mms4tk.analytics import SignalResult


def test_daemon_position_sizing_calculation():
    """Verify order_amount is dynamically calculated from trade_amount_usd / price."""
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

    result = asyncio.run(daemon.execute_cycle())

    # Expected units = round(10.0 / 64800.0, 6) = 0.000154 BTC
    expected_amount = round(10.0 / 64800.0, 6)
    executor.execute_order.assert_called_once_with(
        symbol="BTC/USDC",
        side="BUY",
        amount=expected_amount,
        stop_loss_pct=0.02,
        leverage=1.0
    )
    assert daemon.trade_amount_usd == 10.0
    assert daemon.get_status()["trade_amount_usd"] == 10.0

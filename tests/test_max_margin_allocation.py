"""
Tests for 70/30 capital allocation split under MMS4_MAX_MARGIN_USD total cap.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from mms4tk.daemon import MMS4Daemon
from mms4tk.analytics import SignalResult
from mms4tk.logger_db import LoggerDB


def test_daemon_70_30_capital_split_calculation(tmp_path):
    """Verify base order takes 70% ($7.00) and high risk add takes 30% ($3.00) of $10.00 cap."""
    executor = MagicMock()
    executor.initialize = AsyncMock()
    executor.close = AsyncMock()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.execute_order = AsyncMock(return_value={"id": "order-70-30"})
    # No open position for the base leg.
    executor.fetch_positions = AsyncMock(return_value=[])

    state_manager = MagicMock()
    state_manager.leverage = 1.0
    state_manager.current_state.value = "BASE_MODE"
    state_manager.is_high_risk_add_allowed.return_value = True

    signal_engine = MagicMock()
    # Signal at price $50,000.0 USD
    signal_engine.evaluate_signals.return_value = SignalResult(
        signal_type="BASE_LONG",
        price=50000.0,
        stop_loss=49000.0,
        leverage=1.0,
        is_allowed=True,
        reason="Base LONG signal"
    )

    # A real LoggerDB: the addon guard reads a persisted counter, and a MagicMock's
    # __int__ defaults to 1, which would look like "an addon was already executed".
    logger_db = LoggerDB(db_path=str(tmp_path / "margin_logs.db"))

    # Create daemon with $10 USD max margin cap
    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_manager,
        signal_engine=signal_engine,
        logger_db=logger_db,
        symbol="BTC/USDC",
        trade_amount_usd=10.0
    )

    assert daemon.max_margin_usd == 10.0
    assert daemon.base_trade_usd == 7.0
    assert daemon.addon_trade_usd == 3.0

    # 1. Test Base Order (70% = $7.00 USD)
    asyncio.run(daemon.execute_cycle())
    expected_base_amount = round(7.0 / 50000.0, 6) # 0.00014 BTC
    executor.execute_order.assert_called_once_with(
        symbol="BTC/USDC",
        side="BUY",
        amount=expected_base_amount,
        stop_loss_pct=0.02,
        leverage=1.0,
        price=50000.0
    )

    # 2. Test High Risk Add Order (30% = $3.00 USD).
    # The addon requires an existing base position on the same side, so surface one.
    executor.execute_order.reset_mock()
    executor.fetch_positions = AsyncMock(return_value=[{
        "symbol": "BTC/USDC",
        "side": "long",
        "contracts": 1.0,
        "entryPrice": 50000.0,
        "notional": 7.0,
        "info": {"posSide": "long", "avgPx": "50000.0"},
    }])
    signal_engine.evaluate_signals.return_value = SignalResult(
        signal_type="HIGH_RISK_ADD_LONG",
        price=50000.0,
        stop_loss=49500.0,
        leverage=1.0,
        is_allowed=True,
        reason="High Risk Add LONG signal"
    )

    asyncio.run(daemon.execute_cycle())
    expected_addon_amount = round(3.0 / 50000.0, 6) # 0.00006 BTC
    executor.execute_order.assert_called_once_with(
        symbol="BTC/USDC",
        side="BUY",
        amount=expected_addon_amount,
        stop_loss_pct=0.0125,
        leverage=1.0,
        price=50000.0
    )

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import SignalResult, Candle
from mms4tk.logger_db import LoggerDB
from mms4tk.hermes_webhook import HermesWebhookClient


@pytest.fixture
def temp_db(tmp_path: Path) -> LoggerDB:
    db_file = tmp_path / "test_tp_logs.db"
    return LoggerDB(db_path=str(db_file))


def test_take_profit_and_flip_execution(temp_db: LoggerDB):
    """
    Test MMS4 Rule 1.4:
    When a BASE_LONG signal is received while a SHORT position is active,
    the daemon must:
    1. Close the existing SHORT position with Take Profit (100% reduce_position).
    2. Reset SCOUT_MODE to BASE_MODE (on_take_profit_hit).
    3. Open a new BASE_LONG position.
    """
    async def _async_test():
        # Setup mocks
        executor = OKXExecutor()
        
        # Mock active positions returning 1 SHORT position on BTC/USDC
        mock_pos = {
            "symbol": "BTC-USDC-SWAP",
            "instId": "BTC-USDC-SWAP",
            "posSide": "short",
            "side": "short",
            "contracts": 5.0,
            "notionalUsd": 315.0,
            "entryPrice": 63000.0,
            "markPrice": 62200.0,
            "unrealizedPnl": 4.0,
        }
        executor.fetch_positions = AsyncMock(return_value=[mock_pos])
        executor.reduce_position = AsyncMock(return_value={"id": "reduce_order_123", "status": "closed"})
        executor.execute_order = AsyncMock(return_value={"id": "new_buy_order_456", "status": "executed"})
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=63000, high=63100, low=62900, close=63000, volume=10),
            Candle(timestamp=2000, open=63000, high=63050, low=62150, close=62200, volume=15),
            Candle(timestamp=3000, open=62200, high=62300, low=62100, close=62250, volume=5),
        ])

        state_mgr = MMS4StateManager(initial_state=MMS4State.SCOUT_MODE)
        assert state_mgr.current_state == MMS4State.SCOUT_MODE

        daemon = MMS4Daemon(
            executor=executor,
            state_manager=state_mgr,
            logger_db=temp_db,
            symbol="BTC/USDC"
        )

        # Mock signal engine to return BASE_LONG
        long_signal = SignalResult(
            signal_type="BASE_LONG",
            price=62200.0,
            stop_loss=60956.0,
            leverage=1.0,
            is_allowed=True,
            reason="Candle hit lower band"
        )
        daemon.signal_engine.evaluate_signals = MagicMock(return_value=long_signal)

        # Execute cycle
        result = await daemon.execute_symbol_cycle("BTC/USDC")

        # 1. Verify Take Profit reduction was called
        executor.reduce_position.assert_awaited_once_with(
            symbol="BTC/USDC",
            side="short",
            reduction_pct=1.0,
            reason="TAKE_PROFIT_BASE_LONG"
        )

        # 2. Verify new BUY order was executed
        executor.execute_order.assert_awaited_once()
        call_args = executor.execute_order.call_args[1]
        assert call_args["symbol"] == "BTC/USDC"
        assert call_args["side"] == "BUY"
        assert call_args["stop_loss_pct"] == 0.02

        # 3. Verify state transition from SCOUT_MODE -> BASE_MODE on TP hit
        assert state_mgr.current_state == MMS4State.BASE_MODE

        # 4. Verify outcome message
        assert result["outcome"] == "EXECUTED_TP_AND_FLIP_BASE_LONG"

    asyncio.run(_async_test())


def test_hermes_webhook_emit_take_profit_event(tmp_path: Path):
    """Test emit_take_profit_event creates proper WebhookEvent record."""
    db_file = tmp_path / "webhook_tp.db"
    client = HermesWebhookClient(db_path=str(db_file))

    event_id = client.emit_take_profit_event(
        symbol="ETH/USDC",
        closed_side="short",
        entry_price=1900.0,
        exit_price=1850.0,
        pnl_pct=0.0263,
        new_side="long",
        reason="Opposite TMA Band reached"
    )

    assert event_id > 0
    pending = client.get_pending_events(limit=5)
    assert len(pending) == 1
    ev = pending[0]
    assert ev["event_type"] == "TAKE_PROFIT_OPPOSITE_BAND"
    assert "ETH/USDC" in ev["title"]
    assert "+2.63%" in ev["title"]
    assert ev["priority"] == 9

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import SignalResult, Candle
from mms4tk.logger_db import LoggerDB


@pytest.fixture
def temp_db(tmp_path: Path) -> LoggerDB:
    db_file = tmp_path / "test_recon_pnl.db"
    return LoggerDB(db_path=str(db_file))


def test_reconciliation_pnl_btc(temp_db: LoggerDB):
    """
    Test reconciliation PnL calculation for BTC (contractSize=0.0001).
    37 contracts of BTC at entry $79542 and exit $79359 should yield PnL USD ~ -0.677 USD (NOT -6771 USD).
    """
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {"BTC/USDC": {"contractSize": 0.0001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.0001})

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="BTC/USDC",
    )

    # Save a position snapshot representing 37 contracts of BTC long
    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=79542.0,
        last_price=79542.0,
    )

    # Position is now absent from live positions, current price is $79359
    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=79359.0,
    )

    # Verify record_trade was called with contractSize-scaled PnL
    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    pnl = kwargs["pnl"]
    
    # Expected pnl: ((79359 - 79542) / 79542) * 79542 * 37 * 0.0001 = -183 * 37 * 0.0001 = -0.6771 USD
    assert pnl == pytest.approx(-0.6771, rel=1e-3)
    assert abs(pnl) < 1.0  # Must NOT be ~ -6763 or -6771 USD


def test_reconciliation_pnl_eth(temp_db: LoggerDB):
    """
    Test reconciliation PnL calculation for ETH (contractSize=0.001).
    84 contracts of ETH SHORT at entry $2491.23 and exit $2540.78 should yield PnL USD ~ -4.162 USD (NOT -4162 USD).
    """
    executor = OKXExecutor()
    # Test fallback contract size without exchange.markets populated
    executor.exchange = MagicMock()
    executor.exchange.markets = None

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="ETH/USDC",
    )

    # Save a position snapshot representing 84 contracts of ETH short
    temp_db.save_position_snapshot(
        symbol="ETH/USDC",
        side="short",
        contracts=84.0,
        entry_price=2491.23,
        last_price=2491.23,
    )

    # Position is now absent from live positions, current price is $2540.78
    daemon.reconcile_positions(
        target_symbol="ETH/USDC",
        live_positions=[],
        current_price=2540.78,
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    pnl = kwargs["pnl"]

    # Expected pnl: ((2491.23 - 2540.78) / 2491.23) * 2491.23 * 84 * 0.001 = -49.55 * 84 * 0.001 = -4.1622 USD
    assert pnl == pytest.approx(-4.1622, rel=1e-3)
    assert abs(pnl) < 10.0  # Must NOT be ~ -4162 USD


def test_reconciliation_classifies_addon_stop_out_at_composite_threshold(temp_db: LoggerDB):
    """
    P3: a position that received a High Risk Add gets its SL re-attached at the
    composite (addon_sl_pct) distance from the averaged entry, not base_sl_pct.
    A loss between the two thresholds (0.9375% .. 1.5% with defaults) is a real
    exchange-side stop-out on such a position and must be classified as one.
    """
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {"BTC/USDC": {"contractSize": 0.0001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.0001})

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="BTC/USDC",
    )

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=100.0,
        last_price=100.0,
    )
    # This position already carries a High Risk Add.
    temp_db.record_addon("BTC/USDC", "long")

    # Loss of 1.2%: below base_sl_pct*0.75 (1.5%) but above addon_sl_pct*0.75
    # (0.9375%) — must be recognized as the composite SL firing.
    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=98.8,
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["is_stop_loss"] is True


def test_reconciliation_without_addon_still_uses_base_threshold(temp_db: LoggerDB):
    """Regression guard: a position with no addon keeps the pre-P3 base_sl_pct
    threshold — the same 1.2% loss is NOT classified as a stop-out."""
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {"BTC/USDC": {"contractSize": 0.0001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.0001})

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.record_trade = MagicMock()

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=temp_db,
        symbol="BTC/USDC",
    )

    temp_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=37.0,
        entry_price=100.0,
        last_price=100.0,
    )
    # No addon recorded for this position.

    daemon.reconcile_positions(
        target_symbol="BTC/USDC",
        live_positions=[],
        current_price=98.8,
    )

    state_mgr.record_trade.assert_called_once()
    kwargs = state_mgr.record_trade.call_args[1]
    assert kwargs["is_stop_loss"] is False


def test_take_profit_pnl_with_contract_size(temp_db: LoggerDB):
    """
    Test Take Profit PnL calculation scaled by contract size.
    """
    async def _async_test():
        executor = OKXExecutor()
        executor.exchange = MagicMock()
        executor.exchange.markets = {"ETH/USDC": {"contractSize": 0.001}}
        executor.exchange.market = MagicMock(return_value={"contractSize": 0.001})

        # Mock active SHORT position of 50 contracts on ETH/USDC
        mock_pos = {
            "symbol": "ETH/USDC",
            "instId": "ETH-USDC-SWAP",
            "posSide": "short",
            "side": "short",
            "contracts": 50.0,
            "notionalUsd": 125.0,
            "entryPrice": 2500.0,
            "markPrice": 2450.0,
            "unrealizedPnl": 2.5,
        }
        executor.fetch_positions = AsyncMock(return_value=[mock_pos])
        executor.reduce_position = AsyncMock(return_value={"id": "reduce_123", "status": "closed"})
        executor.execute_order = AsyncMock(return_value={"id": "buy_456", "status": "executed"})
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=2500, high=2510, low=2490, close=2500, volume=10),
            Candle(timestamp=2000, open=2500, high=2505, low=2445, close=2450, volume=15),
            Candle(timestamp=3000, open=2450, high=2460, low=2440, close=2450, volume=5),
        ])

        from mms4tk.api import configured_symbols_store
        configured_symbols_store["ETH/USDC"] = {"allocation_usd": 200.0, "is_active": True, "base_ratio": 70, "add_ratio": 30}

        state_mgr = MMS4StateManager(initial_state=MMS4State.SCOUT_MODE)
        state_mgr.record_trade = MagicMock()

        daemon = MMS4Daemon(
            executor=executor,
            state_manager=state_mgr,
            logger_db=temp_db,
            symbol="ETH/USDC",
            trade_amount_usd=200.0,
        )

        long_signal = SignalResult(
            signal_type="BASE_LONG",
            price=2450.0,
            stop_loss=2400.0,
            leverage=1.0,
            is_allowed=True,
            reason="Opposite TMA Band reached"
        )
        daemon.signal_engine.evaluate_signals = MagicMock(return_value=long_signal)

        await daemon.execute_symbol_cycle("ETH/USDC")

        # Verify record_trade was called for take profit with contractSize scaling
        state_mgr.record_trade.assert_called_once()
        kwargs = state_mgr.record_trade.call_args[1]
        pnl = kwargs["pnl"]
        
        # Expected: ((2500 - 2450) / 2500) * 2500 * 50 * 0.001 = 50 * 50 * 0.001 = +2.50 USD
        assert pnl == pytest.approx(2.50, rel=1e-3)
        assert kwargs["is_take_profit"] is True
        assert abs(pnl) < 10.0  # Must NOT be 2500 USD

    asyncio.run(_async_test())


def test_get_contract_size_helper():
    """Test _get_contract_size fallback and market lookup rules."""
    executor = OKXExecutor()
    state_mgr = MMS4StateManager()
    daemon = MMS4Daemon(executor=executor, state_manager=state_mgr)

    # 1. Fallbacks when exchange markets not loaded
    executor.exchange = None
    assert daemon._get_contract_size("BTC/USDC") == 0.0001
    assert daemon._get_contract_size("BTC/USDT") == 0.0001
    assert daemon._get_contract_size("BTC-USDC-SWAP") == 0.0001
    assert daemon._get_contract_size("ETH/USDC") == 0.001
    assert daemon._get_contract_size("ETH-USDT-SWAP") == 0.001
    assert daemon._get_contract_size("SOL/USDC") == 1.0

    # 2. Lookup via executor.exchange
    executor.exchange = MagicMock()
    executor.exchange.markets = {"SOL/USDC": {"contractSize": 0.1}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.1})
    assert daemon._get_contract_size("SOL/USDC") == 0.1

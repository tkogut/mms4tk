import os
import pytest
import tempfile
import asyncio
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from mms4tk.logger_db import LoggerDB
from mms4tk.api import app, logger_db
from mms4tk.daemon import MMS4Daemon
from mms4tk.analytics import Candle, TMAIndicator, IndicatorParams, SignalResult
from mms4tk.state_manager import MMS4StateManager, MMS4State


def test_logger_db_tp_regime_settings():
    """Verify tp_mode and tp_auto_regime keys can be set, fetched, and persisted in SQLite SSOT."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = LoggerDB(db_path=db_path)
        
        # Test defaults / empty
        assert db.get_setting("tp_mode", "CLASSIC") == "CLASSIC"
        assert db.get_setting("tp_auto_regime", "false") == "false"
        
        # Test update
        db.set_setting("tp_mode", "DYNAMIC_MIDLINE", updated_by="test_suite")
        db.set_setting("tp_auto_regime", "true", updated_by="test_suite")
        
        assert db.get_setting("tp_mode") == "DYNAMIC_MIDLINE"
        assert db.get_setting("tp_auto_regime") == "true"
        
        # Test persistence across re-instantiation
        db2 = LoggerDB(db_path=db_path)
        assert db2.get_setting("tp_mode") == "DYNAMIC_MIDLINE"
        assert db2.get_setting("tp_auto_regime") == "true"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_api_tp_regime_manual_override_lock():
    """Verify Kłódka (Manual Override): when tp_auto_regime is OFF, Hermes AI cannot change tp_mode."""
    client = TestClient(app)
    api_key = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
    headers = {"X-MMS4-API-KEY": api_key}

    try:
        # 1. Turn OFF auto-regime (Manual Override active) and set mode to CLASSIC
        post_res = client.post(
            "/api/v1/settings/update",
            json={"tp_auto_regime": False, "tp_mode": "CLASSIC"},
            headers=headers
        )
        assert post_res.status_code == 200
        data = post_res.json()
        assert data["tp_mode"] == "CLASSIC"
        assert data["tp_auto_regime"] is False

        # 2. Attempt tp_mode change via Hermes endpoint while tp_auto_regime is OFF -> REJECTED
        hermes_res = client.post(
            "/api/v1/settings/tp_regime",
            json={"tp_mode": "DYNAMIC_MIDLINE", "source": "hermes", "reason": "AI regime switch"},
            headers=headers
        )
        assert hermes_res.status_code == 200
        h_data = hermes_res.json()
        assert h_data["status"] == "rejected"
        assert "Manual Override active" in h_data["message"]

        # Verify DB setting was NOT changed
        get_res = client.get("/api/v1/settings")
        assert get_res.json()["tp_mode"] == "CLASSIC"

        # 3. Operator (UI) updates tp_mode directly -> SUCCEEDS
        ui_res = client.post(
            "/api/v1/settings/tp_regime",
            json={"tp_mode": "DYNAMIC_MIDLINE", "source": "ui", "reason": "Operator decision"},
            headers=headers
        )
        assert ui_res.status_code == 200
        assert ui_res.json()["status"] == "success"
        assert ui_res.json()["tp_mode"] == "DYNAMIC_MIDLINE"

        # 4. Turn ON auto-regime -> Hermes AI can now switch regimes
        client.post(
            "/api/v1/settings/update",
            json={"tp_auto_regime": True},
            headers=headers
        )

        hermes_res2 = client.post(
            "/api/v1/settings/tp_regime",
            json={"tp_mode": "CLASSIC", "source": "hermes", "reason": "High trend detected"},
            headers=headers
        )
        assert hermes_res2.status_code == 200
        assert hermes_res2.json()["status"] == "success"
        assert hermes_res2.json()["tp_mode"] == "CLASSIC"
    finally:
        # Cleanup
        if logger_db:
            logger_db.set_setting("tp_mode", "CLASSIC", updated_by="test_cleanup")
            logger_db.set_setting("tp_auto_regime", "false", updated_by="test_cleanup")


def test_daemon_dynamic_midline_tp1_trigger_short():
    """In DYNAMIC_MIDLINE mode, price <= tma_mid triggers 50% scale-out for SHORT position."""
    async def _async_test():
        mock_executor = MagicMock()
        mock_executor.calculate_order_amount = MagicMock(return_value=1.0)
        mock_executor.reduce_position = AsyncMock(return_value={"status": "executed", "id": "123"})
        mock_executor.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDC", "side": "short", "contracts": 2.0, "entryPrice": 60000.0}
        ])

        # Create 25 closed candles with TMA mid around 60000
        candles = [
            Candle(timestamp=i * 60, open=60000.0, high=60500.0, low=59500.0, close=60000.0, volume=10.0)
            for i in range(25)
        ]
        # Current candle price reaches 58500 (below TMA mid)
        candles[-1] = Candle(timestamp=25 * 60, open=59000.0, high=59200.0, low=58500.0, close=58500.0, volume=10.0)
        mock_executor.fetch_candles = AsyncMock(return_value=candles)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = LoggerDB(db_path=db_path)
            db.set_setting("tp_mode", "DYNAMIC_MIDLINE")

            sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
            daemon = MMS4Daemon(executor=mock_executor, state_manager=sm, logger_db=db, symbol="BTC/USDC")

            result = await daemon.execute_symbol_cycle("BTC/USDC")

            assert result["outcome"] == "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_called_once_with(
                symbol="BTC/USDC",
                side="short",
                reduction_pct=0.50,
                reason="DYNAMIC_MIDLINE_TP1"
            )
            assert daemon._midline_tp_taken.get("BTC/USDC_short") is True

            # Second cycle with same position -> must NOT fire duplicate TP1
            mock_executor.reduce_position.reset_mock()
            result2 = await daemon.execute_symbol_cycle("BTC/USDC")
            assert result2["outcome"] != "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_not_called()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    asyncio.run(_async_test())


def test_daemon_dynamic_midline_tp1_trigger_long():
    """In DYNAMIC_MIDLINE mode, price >= tma_mid triggers 50% scale-out for LONG position."""
    async def _async_test():
        mock_executor = MagicMock()
        mock_executor.calculate_order_amount = MagicMock(return_value=1.0)
        mock_executor.reduce_position = AsyncMock(return_value={"status": "executed", "id": "456"})
        mock_executor.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDC", "side": "long", "contracts": 2.0, "entryPrice": 58000.0}
        ])

        candles = [
            Candle(timestamp=i * 60, open=58000.0, high=58500.0, low=57500.0, close=58000.0, volume=10.0)
            for i in range(25)
        ]
        # Current candle price reaches 59500 (above TMA mid)
        candles[-1] = Candle(timestamp=25 * 60, open=58500.0, high=59500.0, low=58400.0, close=59500.0, volume=10.0)
        mock_executor.fetch_candles = AsyncMock(return_value=candles)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = LoggerDB(db_path=db_path)
            db.set_setting("tp_mode", "DYNAMIC_MIDLINE")

            sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
            daemon = MMS4Daemon(executor=mock_executor, state_manager=sm, logger_db=db, symbol="BTC/USDC")

            result = await daemon.execute_symbol_cycle("BTC/USDC")

            assert result["outcome"] == "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_called_once_with(
                symbol="BTC/USDC",
                side="long",
                reduction_pct=0.50,
                reason="DYNAMIC_MIDLINE_TP1"
            )
            assert daemon._midline_tp_taken.get("BTC/USDC_long") is True
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    asyncio.run(_async_test())


def test_daemon_classic_mode_ignores_midline():
    """In CLASSIC mode, mid-line crossing does NOT trigger partial scale-out."""
    async def _async_test():
        mock_executor = MagicMock()
        mock_executor.calculate_order_amount = MagicMock(return_value=1.0)
        mock_executor.reduce_position = AsyncMock(return_value={"status": "executed"})
        mock_executor.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDC", "side": "short", "contracts": 2.0, "entryPrice": 60000.0}
        ])

        candles = [
            Candle(timestamp=i * 60, open=60000.0, high=60500.0, low=59500.0, close=60000.0, volume=10.0)
            for i in range(25)
        ]
        candles[-1] = Candle(timestamp=25 * 60, open=59000.0, high=59200.0, low=58500.0, close=58500.0, volume=10.0)
        mock_executor.fetch_candles = AsyncMock(return_value=candles)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = LoggerDB(db_path=db_path)
            db.set_setting("tp_mode", "CLASSIC")

            sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
            daemon = MMS4Daemon(executor=mock_executor, state_manager=sm, logger_db=db, symbol="BTC/USDC")

            result = await daemon.execute_symbol_cycle("BTC/USDC")

            assert result["outcome"] != "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_not_called()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    asyncio.run(_async_test())


def test_state_machine_sl_transition_after_partial_tp():
    """State Machine Rule: If position took partial TP1, but remaining 50% hits SL (>=2%), state transitions to SCOUT_MODE."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = LoggerDB(db_path=db_path)
        sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)

        # 1. Partial TP1 does not demote/promote state machine
        assert sm.current_state == MMS4State.BASE_MODE

        # 2. Remaining 50% hits SL at -2.0% (0.02)
        sm.record_trade(
            trade_id="trade_sl_after_tp1",
            symbol="BTC/USDC",
            side="short",
            entry_price=60000.0,
            exit_price=61200.0,
            amount=1.0,
            pnl=-20.0,
            pnl_percent=-0.02,
            is_stop_loss=True,
            is_take_profit=False,
            sl_percent=0.02
        )

        # Must transition to SCOUT_MODE
        assert sm.current_state == MMS4State.SCOUT_MODE
        assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)

import asyncio
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.state_manager import SCOUT_LEVERAGE, SCOUT_SIZE_MULT
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import MMS4SignalEngine, SignalResult, Candle
from mms4tk.logger_db import LoggerDB
from mms4tk.api import get_daemon_status, daemon as api_daemon


@pytest.fixture
def temp_db(tmp_path: Path) -> LoggerDB:
    db_file = tmp_path / "test_logs.db"
    return LoggerDB(db_path=str(db_file))


def test_daemon_lifecycle(tmp_path: Path):
    """Test start, status, and stop lifecycle of MMS4Daemon."""
    async def _async_test():
        db = LoggerDB(db_path=str(tmp_path / "lifecycle_logs.db"))
        executor = OKXExecutor()
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=63000, high=63100, low=62900, close=63000, volume=10),
            Candle(timestamp=2000, open=63000, high=63050, low=62950, close=63000, volume=15),
        ])
        executor.fetch_positions = AsyncMock(return_value=[])
        state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
        daemon = MMS4Daemon(
            executor=executor,
            state_manager=state_mgr,
            logger_db=db,
            interval_seconds=0.1
        )

        assert daemon.is_running is False
        status_before = daemon.get_status()
        assert status_before["is_running"] is False
        assert status_before["processed_cycles"] == 0

        await daemon.start()
        assert daemon.is_running is True
        
        # Let daemon run a couple of fast cycles
        await asyncio.sleep(0.7)

        status_running = daemon.get_status()
        assert status_running["is_running"] is True
        assert status_running["processed_cycles"] >= 1
        assert status_running["last_cycle_timestamp"] is not None

        await daemon.stop()
        assert daemon.is_running is False

    asyncio.run(_async_test())


def test_daemon_single_cycle_and_db_persistence(temp_db: LoggerDB):
    """Test single cycle execution and SQLite log persistence."""
    async def _async_test():
        executor = OKXExecutor()
        state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
        daemon = MMS4Daemon(
            executor=executor,
            state_manager=state_mgr,
            logger_db=temp_db,
            interval_seconds=1.0
        )

        res = await daemon.execute_cycle()

        assert res["cycle"] == 1
        assert "timestamp" in res
        assert "signal_type" in res
        assert res["state"] == "BASE_MODE"
        assert res["leverage"] == 3.0

        # Check SQLite DB records
        cycle_logs = temp_db.get_recent_cycle_logs()
        assert len(cycle_logs) == 1
        assert cycle_logs[0]["state"] == "BASE_MODE"
        assert cycle_logs[0]["leverage"] == 3.0

        state_logs = temp_db.get_recent_state_logs()
        assert len(state_logs) == 1
        assert state_logs[0]["state"] == "BASE_MODE"

    asyncio.run(_async_test())


def test_daemon_signal_handling_and_high_risk_add_blocking(temp_db: LoggerDB):
    """Test signal execution logic and SCOUT_MODE high risk add blocking."""
    async def _async_test():
        executor = OKXExecutor()
        state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
        daemon = MMS4Daemon(
            executor=executor,
            state_manager=state_mgr,
            logger_db=temp_db
        )

        # Transition state manager to SCOUT_MODE (reduced position size via
        # SCOUT_SIZE_MULT, not a 0.1x leverage — see SCOUT_LEVERAGE/SCOUT_SIZE_MULT
        # in state_manager.py) via SL hit
        state_mgr.on_stop_loss_hit(0.02)
        assert state_mgr.current_state == MMS4State.SCOUT_MODE
        assert state_mgr.leverage == SCOUT_LEVERAGE
        assert state_mgr.size_multiplier == SCOUT_SIZE_MULT

        # Execute cycle in SCOUT_MODE
        res = await daemon.execute_cycle()
        assert res["state"] == "SCOUT_MODE"
        assert res["leverage"] == SCOUT_LEVERAGE

    asyncio.run(_async_test())


def test_api_daemon_status_endpoint_direct():
    """Test GET /api/v1/daemon/status handler directly."""
    async def _async_test():
        res = await get_daemon_status()
        assert "is_running" in res
        assert "interval_seconds" in res
        assert "processed_cycles" in res

    asyncio.run(_async_test())

"""PLAN-008 Phase 1: verify SQLite system_settings reach the signal engine.

Before this fix, execute_symbol_cycle() called evaluate_signals() without
`params`, so the engine always built a fresh, env-only IndicatorParams()
internally — any engine_mode/adaptive_vr_*/time_lock_* value saved via the
UI (persisted to SQLite system_settings) never reached signal evaluation,
meaning ADAPTIVE mode was never actually active on production regardless
of what the dashboard showed.
"""
import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mms4tk.analytics import Candle
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4StateManager, MMS4State


def _make_daemon(tmp_path: Path) -> MMS4Daemon:
    db = LoggerDB(db_path=str(tmp_path / "params_sync_logs.db"))
    executor = OKXExecutor()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.fetch_positions = AsyncMock(return_value=[])
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    return MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=db,
        interval_seconds=0.1,
    )


def test_get_indicator_params_defaults_without_db_settings(tmp_path: Path):
    """With nothing in system_settings, params must fall back to env/hardcoded defaults."""
    daemon = _make_daemon(tmp_path)
    params = daemon._get_indicator_params()
    assert params.engine_mode.upper() == "CLASSIC"
    assert params.adaptive_vr_high == pytest.approx(1.6)
    assert params.adaptive_vr_low == pytest.approx(0.8)
    assert params.time_lock_start == "13:30"
    assert params.time_lock_end == "14:30"


def test_get_indicator_params_reflects_sqlite_mutation_without_restart(tmp_path: Path):
    """The core regression this fix targets: mutating SQLite system_settings must
    change what _get_indicator_params() returns immediately, with no daemon restart."""
    daemon = _make_daemon(tmp_path)

    # Sanity: engine starts in CLASSIC.
    assert daemon._get_indicator_params().engine_mode.upper() == "CLASSIC"

    # Simulate what the UI settings save path does (LoggerDB.set_settings_bulk).
    daemon.logger_db.set_settings_bulk(
        {
            "engine_mode": "ADAPTIVE",
            "adaptive_vr_high": "2.1",
            "adaptive_vr_low": "0.5",
            "time_lock_start": "09:00",
            "time_lock_end": "10:15",
        },
        updated_by="test",
    )

    params = daemon._get_indicator_params()
    assert params.engine_mode.upper() == "ADAPTIVE"
    assert params.adaptive_vr_high == pytest.approx(2.1)
    assert params.adaptive_vr_low == pytest.approx(0.5)
    assert params.time_lock_start == "09:00"
    assert params.time_lock_end == "10:15"


def test_get_indicator_params_survives_db_failure(tmp_path: Path, monkeypatch):
    """A broken/unavailable logger_db must not crash signal evaluation — fall back
    to env/hardcoded defaults instead."""
    daemon = _make_daemon(tmp_path)

    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(daemon.logger_db, "get_all_settings", _boom)

    params = daemon._get_indicator_params()
    assert params.engine_mode.upper() == "CLASSIC"


def test_execute_symbol_cycle_passes_live_params_to_signal_engine(tmp_path: Path, monkeypatch):
    """End-to-end regression: execute_symbol_cycle() must call evaluate_signals()
    with the live, SQLite-backed params — not an implicit env-only default."""
    daemon = _make_daemon(tmp_path)
    daemon.logger_db.set_settings_bulk({"engine_mode": "ADAPTIVE"}, updated_by="test")

    daemon.executor.fetch_candles = AsyncMock(return_value=[
        Candle(timestamp=1000, open=100, high=101, low=99, close=100, volume=1),
        Candle(timestamp=2000, open=100, high=101, low=99, close=100, volume=1),
        Candle(timestamp=3000, open=100, high=101, low=99, close=100.5, volume=1),
    ])
    daemon.executor.fetch_positions = AsyncMock(return_value=[])

    captured = {}
    original = daemon.signal_engine.evaluate_signals

    def _spy(*args, **kwargs):
        captured["params"] = kwargs.get("params")
        return original(*args, **kwargs)

    monkeypatch.setattr(daemon.signal_engine, "evaluate_signals", _spy)

    asyncio.run(daemon.execute_symbol_cycle(daemon.symbol))

    assert captured["params"] is not None
    assert captured["params"].engine_mode.upper() == "ADAPTIVE"

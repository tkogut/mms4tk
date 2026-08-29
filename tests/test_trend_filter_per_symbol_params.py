"""PLAN-008 Phase 3: per-symbol persistence of the TMA Slope Trend Filter settings.

Regression target: `symbol_configurations` (SQLite) has a fixed schema with no column
for these fields, so writing them only into `configured_symbols_store` would silently
reset to defaults on the next `load_symbols_config()` call (it prefers SQLite whenever
any row exists — which it always does once a symbol has been added). They must be
readable through `system_settings` under a per-symbol-qualified key.
"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager


def _make_daemon(tmp_path: Path) -> MMS4Daemon:
    db = LoggerDB(db_path=str(tmp_path / "trend_filter_params.db"))
    executor = OKXExecutor()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.fetch_positions = AsyncMock(return_value=[])
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    return MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=db, interval_seconds=0.1)


def test_defaults_disabled_when_no_override(tmp_path: Path):
    daemon = _make_daemon(tmp_path)
    params = daemon._get_indicator_params("BTC/USDC")
    assert params.tma_trend_filter_enabled is False
    assert params.tma_slope_lookback == 5
    assert params.tma_slope_threshold == pytest.approx(0.002)


def test_per_symbol_override_via_system_settings_survives_reload(tmp_path: Path):
    """Simulates the calibrated Phase 3 decision: BTC/USDC=ON, ETH/USDC=OFF, persisted
    via the qualified system_settings keys used by add_or_update_trading_symbol()."""
    daemon = _make_daemon(tmp_path)
    daemon.logger_db.set_settings_bulk(
        {
            "tma_trend_filter_enabled:BTC/USDC": True,
            "tma_slope_lookback:BTC/USDC": 3,
            "tma_slope_threshold:BTC/USDC": 0.002,
        },
        updated_by="test",
    )

    btc_params = daemon._get_indicator_params("BTC/USDC")
    assert btc_params.tma_trend_filter_enabled is True
    assert btc_params.tma_slope_lookback == 3
    assert btc_params.tma_slope_threshold == pytest.approx(0.002)

    # A DIFFERENT symbol with no override must stay disabled — no cross-symbol leakage.
    eth_params = daemon._get_indicator_params("ETH/USDC")
    assert eth_params.tma_trend_filter_enabled is False

    # Re-reading via a fresh LoggerDB handle onto the SAME db file simulates a daemon
    # restart: the setting must still be there (this is exactly the persistence gap
    # a configured_symbols_store-only implementation would have failed).
    reloaded_db = LoggerDB(db_path=daemon.logger_db.db_path)
    daemon.logger_db = reloaded_db
    btc_params_after_reload = daemon._get_indicator_params("BTC/USDC")
    assert btc_params_after_reload.tma_trend_filter_enabled is True


def test_no_symbol_falls_back_to_defaults(tmp_path: Path):
    """Calling without a symbol (should not normally happen from execute_symbol_cycle,
    but must not crash) must not blow up looking up a None-qualified key."""
    daemon = _make_daemon(tmp_path)
    params = daemon._get_indicator_params(None)
    assert params.tma_trend_filter_enabled is False

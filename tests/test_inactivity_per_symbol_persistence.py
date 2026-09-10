"""Audit P6.1 (2026-09-08): per-symbol persistence of the 3 Inactivity Manager
settings (stale_candles_threshold / stale_atr_threshold / impulse_negation_enabled).

Regression target: `symbol_configurations` (SQLite) has a fixed schema with no column
for these fields, so writing them only into `configured_symbols_store` silently
resets to hardcoded defaults (12 / 0.5 / True) on the next `load_symbols_config()`
call (it prefers SQLite whenever any row exists — which it always does once a symbol
has been added). They must be readable through `system_settings` under a
per-symbol-qualified key, exactly like the trend-filter fields (see
tests/test_trend_filter_per_symbol_params.py, the sibling regression this mirrors).
"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mms4tk.daemon import MMS4Daemon
from mms4tk.inactivity_manager import MMS4InactivityManager
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager


def _make_daemon(tmp_path: Path) -> MMS4Daemon:
    db = LoggerDB(db_path=str(tmp_path / "inactivity_params.db"))
    executor = OKXExecutor()
    executor.fetch_candles = AsyncMock(return_value=[])
    executor.fetch_positions = AsyncMock(return_value=[])
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    return MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=db, interval_seconds=0.1)


def test_no_override_returns_empty_dict(tmp_path: Path):
    """Nothing configured anywhere -> empty overrides, matching the previous
    behavior of not calling set_symbol_config() at all (MMS4InactivityManager's own
    hardcoded defaults apply)."""
    daemon = _make_daemon(tmp_path)
    assert daemon._get_inactivity_overrides("BTC/USDC") == {}


def test_per_symbol_override_via_system_settings_survives_reload(tmp_path: Path):
    """Simulates an operator setting BTC/USDC=6 candles / 0.3 ATR / negation off via
    the dashboard, persisted via the qualified system_settings keys used by
    add_or_update_trading_symbol()."""
    daemon = _make_daemon(tmp_path)
    daemon.logger_db.set_settings_bulk(
        {
            "stale_candles_threshold:BTC/USDC": 6,
            "stale_atr_threshold:BTC/USDC": 0.3,
            "impulse_negation_enabled:BTC/USDC": False,
        },
        updated_by="test",
    )

    btc = daemon._get_inactivity_overrides("BTC/USDC")
    assert btc["stale_candles_threshold"] == 6
    assert btc["stale_atr_threshold"] == pytest.approx(0.3)
    assert btc["impulse_negation_enabled"] is False

    # A DIFFERENT symbol with no override must stay unconfigured — no cross-symbol
    # leakage.
    assert daemon._get_inactivity_overrides("ETH/USDC") == {}

    # Re-reading via a fresh LoggerDB handle onto the SAME db file simulates a daemon
    # restart: the setting must still be there — this is exactly the persistence gap
    # a configured_symbols_store-only implementation would have failed (the bug this
    # ticket fixes).
    reloaded_db = LoggerDB(db_path=daemon.logger_db.db_path)
    daemon.logger_db = reloaded_db
    btc_after_reload = daemon._get_inactivity_overrides("BTC/USDC")
    assert btc_after_reload["stale_candles_threshold"] == 6
    assert btc_after_reload["impulse_negation_enabled"] is False


def test_system_settings_wins_over_configured_symbols_store(tmp_path: Path, monkeypatch):
    """system_settings is the SSOT — matches _get_indicator_params' precedence."""
    daemon = _make_daemon(tmp_path)
    daemon.logger_db.set_settings_bulk(
        {"stale_candles_threshold:BTC/USDC": 6}, updated_by="test",
    )

    import mms4tk.api as api_module
    monkeypatch.setitem(api_module.configured_symbols_store, "BTC/USDC", {"stale_candles_threshold": 999})

    overrides = daemon._get_inactivity_overrides("BTC/USDC")
    assert overrides["stale_candles_threshold"] == 6  # system_settings wins, not 999


def test_configured_symbols_store_fallback_when_no_system_settings(tmp_path: Path, monkeypatch):
    """Before any system_settings write ever happens for a symbol (e.g. mid-migration,
    or a value only ever set through symbols.json), the in-memory store is still a
    valid fallback — this ticket adds a source, it doesn't remove one."""
    daemon = _make_daemon(tmp_path)

    import mms4tk.api as api_module
    monkeypatch.setitem(api_module.configured_symbols_store, "ETH/USDC", {"impulse_negation_enabled": False})

    overrides = daemon._get_inactivity_overrides("ETH/USDC")
    assert overrides == {"impulse_negation_enabled": False}


def test_no_symbol_falls_back_to_defaults(tmp_path: Path):
    """Must not crash looking up a None-qualified key."""
    daemon = _make_daemon(tmp_path)
    assert daemon._get_inactivity_overrides(None) == {}


def test_get_inactivity_overrides_matches_manager_default_types(tmp_path: Path):
    """Sanity: the fallback default VALUES this helper's callers rely on
    (MMS4InactivityManager's own class constants) haven't drifted from what this
    ticket's docstrings assume."""
    assert MMS4InactivityManager.DEFAULT_MAX_STALE_CANDLES == 12
    assert MMS4InactivityManager.DEFAULT_VOL_COMPRESSION_THRESHOLD == pytest.approx(0.5)
    assert MMS4InactivityManager.DEFAULT_IMPULSE_NEGATION_ENABLED is True

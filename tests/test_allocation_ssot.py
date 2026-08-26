"""
Tests for SSOT allocation hierarchy, per-symbol overrides, and fallbacks (PLAN-003).
"""

import pytest
from unittest.mock import MagicMock
from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.logger_db import LoggerDB
from mms4tk.api import configured_symbols_store


@pytest.fixture(autouse=True)
def clean_configured_symbols():
    """Ensure clean configured_symbols_store state before and after each test."""
    original = dict(configured_symbols_store)
    configured_symbols_store.clear()
    yield
    configured_symbols_store.clear()
    configured_symbols_store.update(original)


def create_test_daemon(max_margin_usd: float = 20.0, tmp_path=None) -> MMS4Daemon:
    """Helper to construct a lightweight test daemon."""
    executor = MagicMock()
    state_manager = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC")
    signal_engine = MagicMock()
    logger_db = LoggerDB(db_path=str(tmp_path / "test.db")) if tmp_path else LoggerDB(db_path=":memory:")
    
    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_manager,
        signal_engine=signal_engine,
        logger_db=logger_db,
        symbol="BTC/USDC",
        trade_amount_usd=max_margin_usd,
    )
    daemon.max_margin_usd = max_margin_usd
    daemon.base_allocation = 0.70
    daemon.addon_allocation = 0.30
    return daemon


def test_per_symbol_allocation_overrides_global(tmp_path):
    """Per-symbol allocation_usd (e.g. 100 USD BTC) overrides global max_margin_usd (20 USD)."""
    daemon = create_test_daemon(max_margin_usd=20.0, tmp_path=tmp_path)
    
    configured_symbols_store["BTC/USDC"] = {
        "allocation_usd": 100.0,
        "base_ratio": 70,
        "add_ratio": 30,
        "is_active": True,
    }

    plan = daemon.plan_allocation("BTC/USDC")
    
    assert plan["margin_cap_usd"] == 100.0
    assert plan["margin_base_usd"] == 70.0
    assert plan["margin_addon_usd"] == 30.0


def test_global_fallback_when_no_per_symbol(tmp_path):
    """When no per-symbol configuration exists, fall back to global max_margin_usd."""
    daemon = create_test_daemon(max_margin_usd=25.0, tmp_path=tmp_path)
    
    # Empty symbol store or symbol not present
    plan = daemon.plan_allocation("SOL/USDC")
    
    assert plan["margin_cap_usd"] == 25.0
    assert plan["margin_base_usd"] == 17.5
    assert plan["margin_addon_usd"] == 7.5


def test_allocation_survives_margin_change(tmp_path):
    """Dynamic change of global max_margin_usd does not break per-symbol allocation."""
    daemon = create_test_daemon(max_margin_usd=20.0, tmp_path=tmp_path)
    
    configured_symbols_store["BTC/USDC"] = {
        "allocation_usd": 100.0,
        "base_ratio": 70,
        "add_ratio": 30,
        "is_active": True,
    }

    plan1 = daemon.plan_allocation("BTC/USDC")
    assert plan1["margin_cap_usd"] == 100.0

    # Dynamically update global max_margin_usd to 50.0
    daemon.max_margin_usd = 50.0

    plan2 = daemon.plan_allocation("BTC/USDC")
    assert plan2["margin_cap_usd"] == 100.0
    assert plan2["margin_base_usd"] == 70.0
    assert plan2["margin_addon_usd"] == 30.0


def test_allocation_zero_guard(tmp_path):
    """allocation_usd=0 or <= 0 guards against invalid values and falls back to global max_margin_usd."""
    daemon = create_test_daemon(max_margin_usd=30.0, tmp_path=tmp_path)
    
    configured_symbols_store["ETH/USDC"] = {
        "allocation_usd": 0.0,
        "base_ratio": 70,
        "add_ratio": 30,
        "is_active": True,
    }

    plan = daemon.plan_allocation("ETH/USDC")
    
    assert plan["margin_cap_usd"] == 30.0
    assert plan["margin_base_usd"] == 21.0
    assert plan["margin_addon_usd"] == 9.0


def test_budget_usd_fallback(tmp_path):
    """budget_usd works as fallback when allocation_usd is missing."""
    daemon = create_test_daemon(max_margin_usd=10.0, tmp_path=tmp_path)
    
    configured_symbols_store["DOGE/USDC"] = {
        "budget_usd": 50.0,
        "base_ratio": 70,
        "add_ratio": 30,
        "is_active": True,
    }

    plan = daemon.plan_allocation("DOGE/USDC")
    
    assert plan["margin_cap_usd"] == 50.0
    assert plan["margin_base_usd"] == 35.0
    assert plan["margin_addon_usd"] == 15.0

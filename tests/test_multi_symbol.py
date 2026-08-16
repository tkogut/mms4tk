import pytest
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.state_manager import SCOUT_LEVERAGE, SCOUT_SIZE_MULT
from mms4tk.api import app, configured_symbols_store
from fastapi.testclient import TestClient

client = TestClient(app)


def test_per_symbol_state_isolation():
    """Verify that Stop-Loss hit on one symbol transitions ONLY that symbol into SCOUT_MODE."""
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC")
    sm.set_symbol_state("ETH/USDC", MMS4State.BASE_MODE)
    sm.set_symbol_state("SOL/USDC", MMS4State.BASE_MODE)

    # Initial states all BASE_MODE
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE
    assert sm.get_symbol_state("SOL/USDC") == MMS4State.BASE_MODE

    # Trigger 2.5% Stop-Loss ONLY on ETH/USDC
    transitioned = sm.on_stop_loss_hit(sl_percent=0.025, sl_price=3000.0, symbol="ETH/USDC")
    assert transitioned is True

    # ETH/USDC MUST be in SCOUT_MODE, while BTC/USDC & SOL/USDC stay in BASE_MODE
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE
    assert sm.get_symbol_state("SOL/USDC") == MMS4State.BASE_MODE

    # Leverage isolation
    assert sm.get_symbol_leverage("ETH/USDC") == SCOUT_LEVERAGE
    assert sm.get_symbol_size_multiplier("ETH/USDC") == SCOUT_SIZE_MULT
    assert sm.get_symbol_leverage("BTC/USDC") == 3.0  # Default MODERATE leverage

    # Take-Profit on ETH/USDC restores BASE_MODE for ETH/USDC
    tp_transitioned = sm.on_take_profit_hit(symbol="ETH/USDC")
    assert tp_transitioned is True
    assert sm.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE


def test_get_trading_symbols_api():
    """Test GET /api/v1/symbols endpoint."""
    res = client.get("/api/v1/symbols")
    assert res.status_code == 200
    data = res.json()
    assert "symbols" in data
    assert "total" in data
    symbols_list = [s["symbol"] for s in data["symbols"]]
    assert "BTC/USDC" in symbols_list
    assert "ETH/USDC" in symbols_list


def test_add_remove_trading_symbol_api():
    """Test POST /api/v1/symbols/add and DELETE /api/v1/symbols/{symbol} endpoints."""
    headers = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}

    # Add DOGE/USDC
    add_res = client.post(
        "/api/v1/symbols/add",
        json={"symbol": "DOGE/USDC"},
        headers=headers
    )
    assert add_res.status_code == 200
    assert add_res.json()["symbol"] == "DOGE/USDC"

    # Verify added
    get_res = client.get("/api/v1/symbols")
    syms = [s["symbol"] for s in get_res.json()["symbols"] if s["is_active"]]
    assert "DOGE/USDC" in syms

    # Delete (Pause) DOGE/USDC
    del_res = client.delete("/api/v1/symbols/DOGE/USDC", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["paused"] == "DOGE/USDC"

    # Verify paused
    get_res2 = client.get("/api/v1/symbols")
    active_syms = [s["symbol"] for s in get_res2.json()["symbols"] if s["is_active"]]
    assert "DOGE/USDC" not in active_syms


def test_multi_symbol_daemon_cycle():
    """Test MMS4Daemon.execute_multi_symbol_cycle for parallel async execution."""
    import asyncio
    from mms4tk.daemon import MMS4Daemon
    daemon = MMS4Daemon(symbol="BTC/USDC")
    results = asyncio.run(daemon.execute_multi_symbol_cycle(["BTC/USDC", "ETH/USDC", "SOL/USDC"]))

    assert len(results) == 3
    symbols_evaluated = [r["symbol"] for r in results]
    assert "BTC/USDC" in symbols_evaluated
    assert "ETH/USDC" in symbols_evaluated
    assert "SOL/USDC" in symbols_evaluated


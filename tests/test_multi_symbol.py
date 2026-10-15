import pytest
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.state_manager import SCOUT_LEVERAGE, SCOUT_SIZE_MULT
from mms4tk.api import app, configured_symbols_store, state_manager, save_symbols_config
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


def test_get_trading_symbols_api_htf_gate_visibility():
    """GET /api/v1/symbols surfaces all 3 directional-bias layers (audit gap fix,
    2026-09-13): raw manual `directional_bias`, the automatic HTF gate reading
    (`htf_bias`), and what evaluate_signals actually enforces
    (`effective_directional_bias`) -- see state_manager.get_symbol_htf_bias /
    get_effective_directional_bias.
    """
    assert state_manager.strategy_profile.directional_bias == "BOTH"
    try:
        # Simulate the H4 gate having fired for BTC/USDC (what daemon.py's
        # _refresh_htf_directional_gate does when htf_directional_gate_enabled
        # is on for that symbol), while ETH/USDC's gate has never set anything.
        state_manager.set_symbol_htf_bias("BTC/USDC", "LONG_ONLY")

        res = client.get("/api/v1/symbols")
        assert res.status_code == 200
        by_symbol = {s["symbol"]: s for s in res.json()["symbols"]}

        gated = by_symbol["BTC/USDC"]
        assert gated["htf_bias"] == "LONG_ONLY"
        assert gated["effective_directional_bias"] == "LONG_ONLY"
        assert gated["directional_bias"] == "BOTH"  # raw manual field untouched

        ungated = by_symbol["ETH/USDC"]
        assert ungated["htf_bias"] == "BOTH"
        assert ungated["effective_directional_bias"] == "BOTH"
        assert ungated["directional_bias"] == "BOTH"
    finally:
        # Don't leak gate state into other tests sharing this module-level
        # state_manager singleton.
        state_manager.set_symbol_htf_bias("BTC/USDC", "BOTH")


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

    # Delete DOGE/USDC — the endpoint performs a hard removal from the symbols store
    # (its own docstring/message say "permanently removed"), not a pause. The response
    # has no "paused" key; asserting one always raised KeyError before this fix.
    del_res = client.delete("/api/v1/symbols/DOGE/USDC", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["removed"] == "DOGE/USDC"

    # Verify it's gone entirely, not merely deactivated
    get_res2 = client.get("/api/v1/symbols")
    all_syms = [s["symbol"] for s in get_res2.json()["symbols"]]
    assert "DOGE/USDC" not in all_syms


def test_add_rejects_already_configured_symbol_without_resetting_it():
    """2026-10-15 incident: POST /api/v1/symbols/add and /update share one handler
    that overwrites every omitted AddSymbolRequest field with its schema default.
    /update is always called with the full current config (SymbolsTab.jsx), but
    /add is not — a stray "Dodaj Nową Parę" click on an already-configured symbol
    silently reset ALL of its settings to defaults in production, undoing the
    PR #136 decision to keep htf_directional_gate_enabled on for BTC/USDC. /add
    must now reject an existing symbol (409) instead of clobbering it; /update
    remains the only path that can change an existing symbol's settings."""
    headers = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}
    symbol = "BTC/USDC"

    before = client.get("/api/v1/symbols").json()["symbols"]
    before_row = next(s for s in before if s["symbol"] == symbol)

    add_res = client.post(
        "/api/v1/symbols/add",
        json={"symbol": symbol},
        headers=headers,
    )
    assert add_res.status_code == 409
    assert symbol in add_res.json()["detail"]

    after = client.get("/api/v1/symbols").json()["symbols"]
    after_row = next(s for s in after if s["symbol"] == symbol)
    assert after_row["allocation_usd"] == before_row["allocation_usd"]
    assert after_row["htf_directional_gate_enabled"] == before_row["htf_directional_gate_enabled"]
    assert after_row["stale_candles_threshold"] == before_row["stale_candles_threshold"]


def test_update_still_allowed_on_already_configured_symbol():
    """The /add 409 guard must be scoped to the /add route only — /update (the
    dashboard's save/toggle path) must keep working on an existing symbol.

    POST /update writes through save_symbols_config() to symbols.json AND the
    SQLite symbol_configurations row, not just the in-memory
    configured_symbols_store — the autouse _isolate_system_settings fixture
    only restores the in-memory dict, not those two on-disk/SQLite writes (see
    tests/test_inactivity_api_persistence.py's docstring, which documents this
    exact gap from three prior reviews). Explicit try/finally here restores all
    three places this endpoint writes."""
    headers = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}
    symbol = "ETH/USDC"

    before_full_config = configured_symbols_store.get(symbol)
    assert before_full_config is not None, "ETH/USDC must already be configured for this test"

    try:
        res = client.post(
            "/api/v1/symbols/update",
            json={"symbol": symbol, "allocation_usd": 42.0},
            headers=headers,
        )
        assert res.status_code == 200

        get_res = client.get("/api/v1/symbols")
        row = next(s for s in get_res.json()["symbols"] if s["symbol"] == symbol)
        assert row["allocation_usd"] == 42.0
    finally:
        configured_symbols_store[symbol] = dict(before_full_config)
        save_symbols_config(configured_symbols_store)


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


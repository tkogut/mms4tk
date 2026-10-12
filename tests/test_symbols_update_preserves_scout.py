"""POST /api/v1/symbols/update must not touch a symbol's risk state.

It used to force BASE_MODE unconditionally, silently wiping an active SCOUT_MODE (even
right after a real stop-out). Cleanup mirrors tests/test_inactivity_api_persistence.py:
the endpoint writes configured_symbols_store + the symbol_configurations row, and
tests/conftest.py snapshots/restores system_settings.
"""
import pytest
from fastapi.testclient import TestClient

import mms4tk.api as api
from mms4tk.state_manager import MMS4State, MMS4StateManager

client = TestClient(api.app)
AUTH_HEADERS = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}
SYMBOL = "ZZZ/USDC"  # never a real configured pair


@pytest.fixture
def isolated_state_manager(monkeypatch):
    sm = MMS4StateManager(trading_symbol="BTC/USDC")
    monkeypatch.setattr(api, "state_manager", sm)
    yield sm
    api.configured_symbols_store.pop(SYMBOL, None)
    api.logger_db.delete_symbol_config(SYMBOL)
    api.save_symbols_config(api.configured_symbols_store)


def _update():
    return client.post("/api/v1/symbols/update", json={"symbol": SYMBOL}, headers=AUTH_HEADERS)


def test_pair_config_update_keeps_active_scout_mode(isolated_state_manager):
    sm = isolated_state_manager
    sm.set_symbol_state(SYMBOL, MMS4State.SCOUT_MODE, reason="Stop Loss hit (2.1%)")

    assert _update().status_code == 200
    assert sm.get_symbol_state(SYMBOL) == MMS4State.SCOUT_MODE


def test_pair_config_update_leaves_new_symbol_in_base_mode(isolated_state_manager):
    sm = isolated_state_manager

    assert _update().status_code == 200
    assert sm.get_symbol_state(SYMBOL) == MMS4State.BASE_MODE
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE

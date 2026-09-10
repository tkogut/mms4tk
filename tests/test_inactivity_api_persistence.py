"""Audit P6.1 (2026-09-08): API-level coverage of per-symbol Inactivity Manager
settings persistence — the write side (POST /api/v1/symbols/update) and the read
side (GET /api/v1/symbols), both of which must go through system_settings (SSOT),
not just the in-memory configured_symbols_store. See
tests/test_inactivity_per_symbol_persistence.py for the daemon-level coverage of the
same fix.
"""
from fastapi.testclient import TestClient

from mms4tk.api import app, configured_symbols_store, logger_db

client = TestClient(app)
AUTH_HEADERS = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}


def test_symbols_update_persists_inactivity_fields_to_system_settings():
    """POST /api/v1/symbols/update must write the 3 Inactivity Manager fields into
    system_settings under a per-symbol-qualified key — previously only
    tma_trend_filter_enabled/tma_slope_*/high_vol_base_filter_enabled were persisted
    there; stale_candles_threshold/stale_atr_threshold/impulse_negation_enabled were
    silently left configured_symbols_store-only."""
    res = client.post(
        "/api/v1/symbols/update",
        json={
            "symbol": "ETH/USDC",
            "stale_candles_threshold": 7,
            "stale_atr_threshold": 0.35,
            "impulse_negation_enabled": False,
        },
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200

    assert logger_db.get_setting("stale_candles_threshold:ETH/USDC") == "7"
    assert logger_db.get_setting("stale_atr_threshold:ETH/USDC") == "0.35"
    assert logger_db.get_setting("impulse_negation_enabled:ETH/USDC") == "False"


def test_get_symbols_reads_inactivity_fields_from_system_settings_not_stale_in_memory():
    """Simulates the exact bug this ticket fixes: system_settings has the operator's
    real setting, but configured_symbols_store (the in-memory store rebuilt fresh on
    every process restart) does NOT — GET /api/v1/symbols must still report the
    system_settings value, not a stale/default in-memory one."""
    symbol = "BTC/USDC"
    logger_db.set_setting(f"stale_candles_threshold:{symbol}", "9", updated_by="test")

    # Simulate "after restart": the in-memory store has no override for this key at
    # all (as load_symbols_config() would produce, since symbol_configurations has no
    # column for it).
    original = dict(configured_symbols_store.get(symbol, {}))
    configured_symbols_store[symbol] = {k: v for k, v in original.items() if k != "stale_candles_threshold"}
    try:
        res = client.get("/api/v1/symbols")
        assert res.status_code == 200
        row = next(s for s in res.json()["symbols"] if s["symbol"] == symbol)
        assert row["stale_candles_threshold"] == 9  # NOT the hardcoded default of 12
    finally:
        configured_symbols_store[symbol] = original

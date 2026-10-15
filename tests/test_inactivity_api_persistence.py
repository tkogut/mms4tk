"""Audit P6.1 (2026-09-08): API-level coverage of per-symbol Inactivity Manager
settings persistence — the write side (POST /api/v1/symbols/update) and the read
side (GET /api/v1/symbols), both of which must go through system_settings (SSOT),
not just the in-memory configured_symbols_store. See
tests/test_inactivity_per_symbol_persistence.py for the daemon-level coverage of the
same fix.

Both tests explicitly clean up the system_settings keys they write (belt), on top
of tests/conftest.py's own full-table snapshot/restore fixture (suspenders) — an
independent review of this ticket found the FIRST version of this file left
non-default values (including impulse_negation_enabled:ETH/USDC=False) in
.agents/swarm/logs.db with no cleanup at all, which docker-compose mounts into the
live daemon. Explicit try/finally here makes each test's side effects visible and
self-contained rather than relying solely on the global fixture.
"""
from fastapi.testclient import TestClient

from mms4tk.api import app, configured_symbols_store, logger_db, save_symbols_config

client = TestClient(app)
AUTH_HEADERS = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}


def test_symbols_update_persists_inactivity_fields_to_system_settings():
    """POST /api/v1/symbols/update must write the 3 Inactivity Manager fields into
    system_settings under a per-symbol-qualified key — previously only
    tma_trend_filter_enabled/tma_slope_*/high_vol_base_filter_enabled were persisted
    there; stale_candles_threshold/stale_atr_threshold/impulse_negation_enabled were
    silently left configured_symbols_store-only.

    Cleanup restores THREE places this endpoint writes, not just system_settings:
    it also calls save_symbols_config() (rewrites symbols.json AND the SQLite
    symbol_configurations row via configured_symbols_store) with AddSymbolRequest's
    OWN defaults (allocation_usd=10.0/base_ratio=70/timeframe="15m") for every field
    this request doesn't set — a second independent review found the first version
    of this cleanup restored only the 3 inactivity keys, leaving an operator's real
    allocation_usd/base_ratio/timeframe silently reset to those defaults in the
    live-mounted db/file if they ever differed. Today harmless only by coincidence
    (the live table already holds those exact defaults for ETH/USDC). NOTE: the
    restored symbols.json entry is the 5-field shape load_symbols_config() itself
    normalizes to (is_active/allocation_usd/base_ratio/add_ratio/timeframe) — it
    does NOT restore any stale_*/impulse_* keys that may have been sitting in that
    file, since load_symbols_config() never reads those keys back out of JSON
    either (SQLite's fixed schema wins whenever it has rows). That's a dead write
    being restored to a different dead write, not a config loss.

    THIRD independent review (2026-09-10) caught a real defect here: `before_full_
    config` was truthiness-checked (`if before_full_config:`), so a symbol that did
    NOT exist in configured_symbols_store before this test (empty dict, falsy) skipped
    restore entirely — this endpoint creates symbols unconditionally
    (add_or_update_trading_symbol), so running this test against an unconfigured
    symbol would PERMANENTLY leave it configured (active, default allocation) in the
    live-mounted store. Fixed: sentinel on `is not None`, and the "didn't exist
    before" branch now removes it via delete_symbol_config(), not just skips."""
    symbol = "ETH/USDC"
    keys = [
        f"stale_candles_threshold:{symbol}",
        f"stale_atr_threshold:{symbol}",
        f"impulse_negation_enabled:{symbol}",
    ]
    before_settings = {k: logger_db.get_setting(k) for k in keys}
    before_full_config = configured_symbols_store.get(symbol)  # None if never configured
    try:
        res = client.post(
            "/api/v1/symbols/update",
            json={
                "symbol": symbol,
                "stale_candles_threshold": 7,
                "stale_atr_threshold": 0.35,
                "impulse_negation_enabled": False,
            },
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200

        assert logger_db.get_setting(keys[0]) == "7"
        assert logger_db.get_setting(keys[1]) == "0.35"
        assert logger_db.get_setting(keys[2]) == "False"
    finally:
        for k in keys:
            if before_settings[k] is None:
                logger_db.delete_setting(k)
            else:
                logger_db.set_setting(k, before_settings[k], updated_by="test_cleanup")
        # Restore symbol_configurations (SQLite) + symbols.json to their pre-test
        # state too — the endpoint writes both via save_symbols_config() (which
        # itself writes to both places), not just system_settings. Distinguish
        # "existed before, restore it" from "did NOT exist before, remove it
        # entirely" — see docstring for why the truthiness check this replaces was
        # a real bug.
        if before_full_config is not None:
            configured_symbols_store[symbol] = dict(before_full_config)
            save_symbols_config(configured_symbols_store)
        else:
            configured_symbols_store.pop(symbol, None)
            logger_db.delete_symbol_config(symbol)
            save_symbols_config(configured_symbols_store)


def test_symbols_update_persists_mid_vol_and_addon_gate_fields():
    """Issue #134/#137: POST /api/v1/symbols/update must persist
    mid_vol_sweep_filter_enabled and addon_requires_favorable_price_enabled into
    system_settings, the same per-symbol-qualified pattern as the sibling filters
    — and GET /api/v1/symbols must read them back, not a stale in-memory default."""
    symbol = "ETH/USDC"
    keys = [
        f"mid_vol_sweep_filter_enabled:{symbol}",
        f"addon_requires_favorable_price_enabled:{symbol}",
    ]
    before_settings = {k: logger_db.get_setting(k) for k in keys}
    before_full_config = configured_symbols_store.get(symbol)
    try:
        res = client.post(
            "/api/v1/symbols/update",
            json={
                "symbol": symbol,
                "mid_vol_sweep_filter_enabled": True,
                "addon_requires_favorable_price_enabled": True,
            },
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200
        assert logger_db.get_setting(keys[0]) == "True"
        assert logger_db.get_setting(keys[1]) == "True"

        get_res = client.get("/api/v1/symbols")
        row = next(s for s in get_res.json()["symbols"] if s["symbol"] == symbol)
        assert row["mid_vol_sweep_filter_enabled"] is True
        assert row["addon_requires_favorable_price_enabled"] is True
    finally:
        for k in keys:
            if before_settings[k] is None:
                logger_db.delete_setting(k)
            else:
                logger_db.set_setting(k, before_settings[k], updated_by="test_cleanup")
        if before_full_config is not None:
            configured_symbols_store[symbol] = dict(before_full_config)
            save_symbols_config(configured_symbols_store)
        else:
            configured_symbols_store.pop(symbol, None)
            logger_db.delete_symbol_config(symbol)
            save_symbols_config(configured_symbols_store)


def test_symbols_update_defaults_mid_vol_and_addon_gate_fields_to_false():
    """Omitting the two new fields entirely must leave them at False (the safe,
    unvalidated-by-default convention every filter in this file follows), not
    raise or silently inherit some other value."""
    symbol = "ETH/USDC"
    keys = [
        f"mid_vol_sweep_filter_enabled:{symbol}",
        f"addon_requires_favorable_price_enabled:{symbol}",
    ]
    before_settings = {k: logger_db.get_setting(k) for k in keys}
    before_full_config = configured_symbols_store.get(symbol)
    try:
        res = client.post(
            "/api/v1/symbols/update",
            json={"symbol": symbol},
            headers=AUTH_HEADERS,
        )
        assert res.status_code == 200
        assert logger_db.get_setting(keys[0]) == "False"
        assert logger_db.get_setting(keys[1]) == "False"
    finally:
        for k in keys:
            if before_settings[k] is None:
                logger_db.delete_setting(k)
            else:
                logger_db.set_setting(k, before_settings[k], updated_by="test_cleanup")
        if before_full_config is not None:
            configured_symbols_store[symbol] = dict(before_full_config)
            save_symbols_config(configured_symbols_store)
        else:
            configured_symbols_store.pop(symbol, None)
            logger_db.delete_symbol_config(symbol)
            save_symbols_config(configured_symbols_store)


def test_get_symbols_reads_inactivity_fields_from_system_settings_not_stale_in_memory():
    """Simulates the exact bug this ticket fixes: system_settings has the operator's
    real setting, but configured_symbols_store (the in-memory store rebuilt fresh on
    every process restart) does NOT — GET /api/v1/symbols must still report the
    system_settings value, not a stale/default in-memory one."""
    symbol = "BTC/USDC"
    key = f"stale_candles_threshold:{symbol}"
    before = logger_db.get_setting(key)
    logger_db.set_setting(key, "9", updated_by="test")

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
        if before is None:
            logger_db.delete_setting(key)
        else:
            logger_db.set_setting(key, before, updated_by="test_cleanup")

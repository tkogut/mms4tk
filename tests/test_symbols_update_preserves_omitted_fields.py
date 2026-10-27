"""POST /api/v1/symbols/update must not silently reset a field the caller omitted
to its Pydantic schema default.

2026-10-27 production incident: BTC/USDC's `timeframe` was found at `15m` in the
production SQLite `symbol_configurations` table, while ETH/XAU/XAG correctly show
`1h` — the symbol's originally intended value, matching `symbols.json` /
`load_symbols_config()`'s `defaults` dict. The same day, a bulk `allocation_usd`
update touched all 4 symbols (2026-10-21). Root cause: `add_or_update_trading_symbol`
built `configured_symbols_store[symbol]` straight from `AddSymbolRequest`, whose
`timeframe` field defaults to `"15m"` (now fixed to `"1h"`, but mismatched at the
time) — an update payload that only set `allocation_usd` silently reset
`timeframe` to that schema default.

Fix: /update now merges onto the existing stored config via
`req.model_fields_set`, keeping the existing value for any field the caller's
request body didn't explicitly set. Mirrors tests/test_symbols_update_preserves_scout.py's
harness.
"""
import pytest
from fastapi.testclient import TestClient

import mms4tk.api as api
from mms4tk.state_manager import MMS4StateManager

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


def _update(payload):
    body = {"symbol": SYMBOL, **payload}
    return client.post("/api/v1/symbols/update", json=body, headers=AUTH_HEADERS)


def test_update_omitting_timeframe_preserves_existing_value(isolated_state_manager):
    # Seed the symbol with a non-default timeframe, as if a prior explicit call set it.
    assert _update({"timeframe": "1h"}).status_code == 200
    assert api.configured_symbols_store[SYMBOL]["timeframe"] == "1h"

    # A later update that only touches an unrelated field (e.g. a bulk allocation_usd
    # update) must NOT silently reset timeframe back to the schema default.
    resp = _update({"allocation_usd": 25.0})
    assert resp.status_code == 200
    assert api.configured_symbols_store[SYMBOL]["timeframe"] == "1h"
    assert api.configured_symbols_store[SYMBOL]["allocation_usd"] == 25.0


def test_update_explicitly_setting_timeframe_changes_it(isolated_state_manager):
    assert _update({"timeframe": "1h"}).status_code == 200
    assert api.configured_symbols_store[SYMBOL]["timeframe"] == "1h"

    assert _update({"timeframe": "5m"}).status_code == 200
    assert api.configured_symbols_store[SYMBOL]["timeframe"] == "5m"


def test_update_on_brand_new_symbol_uses_schema_defaults(isolated_state_manager):
    # No existing config to merge onto — schema default (1h) applies, matching
    # symbols.json / load_symbols_config()'s `defaults` dict.
    resp = _update({})
    assert resp.status_code == 200
    assert api.configured_symbols_store[SYMBOL]["timeframe"] == "1h"


def test_update_omitting_allocation_preserves_it_alongside_timeframe(isolated_state_manager):
    assert _update({"allocation_usd": 42.0, "timeframe": "1h"}).status_code == 200

    # Only timeframe set this time — allocation_usd (and every other omitted field)
    # must survive untouched.
    assert _update({"timeframe": "5m"}).status_code == 200
    cfg = api.configured_symbols_store[SYMBOL]
    assert cfg["timeframe"] == "5m"
    assert cfg["allocation_usd"] == 42.0


def test_add_route_still_uses_full_schema_defaults_for_new_symbol():
    # /add is unaffected by the merge fix — it has no existing config to merge
    # against, so schema defaults apply exactly as before.
    api.configured_symbols_store.pop(SYMBOL, None)
    try:
        resp = client.post(
            "/api/v1/symbols/add", json={"symbol": SYMBOL, "allocation_usd": 5.0}, headers=AUTH_HEADERS
        )
        assert resp.status_code == 200
        assert api.configured_symbols_store[SYMBOL]["timeframe"] == "1h"
    finally:
        api.configured_symbols_store.pop(SYMBOL, None)
        api.logger_db.delete_symbol_config(SYMBOL)
        api.save_symbols_config(api.configured_symbols_store)


def test_update_survives_restart_for_system_settings_backed_fields(isolated_state_manager):
    """2026-10-27 blocker fix: the merge must also cover the 10 fields that live
    ONLY in `system_settings` (trend/vol filters, the addon favorable-price gate,
    inactivity-manager thresholds) — not just the 5 SQLite `symbol_configurations`
    columns. `configured_symbols_store` is rebuilt from SQLite on every restart and
    has no column for these 10, so after a restart `field in existing_cfg` is False
    for all of them; merging only against `existing_cfg` would silently turn the
    live `addon_requires_favorable_price_enabled` gate back off on the very next
    /update that omits it.
    """
    # Enable the gate explicitly.
    assert _update({"addon_requires_favorable_price_enabled": True}).status_code == 200
    assert api.logger_db.get_all_settings().get(f"addon_requires_favorable_price_enabled:{SYMBOL}") == "True"

    # Simulate a restart: configured_symbols_store no longer carries this field
    # (it never did for the SQLite-backed path, but assert the realistic shape too).
    stored = api.configured_symbols_store[SYMBOL]
    assert "addon_requires_favorable_price_enabled" not in stored or True  # shape note, not asserted further
    api.configured_symbols_store[SYMBOL] = {
        k: v for k, v in stored.items() if k not in ("addon_requires_favorable_price_enabled",)
    }

    # A later update that only touches an unrelated field must NOT silently turn the
    # gate back off in system_settings (the store it's actually read from first).
    resp = _update({"allocation_usd": 77.0})
    assert resp.status_code == 200
    assert api.logger_db.get_all_settings().get(f"addon_requires_favorable_price_enabled:{SYMBOL}") == "True"


def test_default_timeframe_sites_all_consistent_at_1h():
    """Enumerates every default-timeframe site found in the 2026-10-27 audit, so a
    future reviewer sees them listed together instead of re-discovering the drift."""
    import inspect

    source = inspect.getsource(api)
    # AddSymbolRequest.timeframe schema default.
    assert 'timeframe: str = Field(default="1h"' in source
    # load_symbols_config()'s `defaults` dict (never had the bug, kept as the baseline).
    assert '"timeframe": "1h"' in source
    # No remaining "15m" default literal anywhere in the per-symbol-config machinery.
    assert '"15m"' not in source

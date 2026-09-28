import os
import pytest
from fastapi.testclient import TestClient
from mms4tk.api import app

client = TestClient(app)

VALID_KEY = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
INVALID_KEY = "invalid-secret-key-12345"


def test_public_get_endpoints_unauthenticated():
    """Verify GET endpoints remain read-only accessible without API key header."""
    res_health = client.get("/api/v1/health")
    assert res_health.status_code == 200
    assert res_health.json()["status"] == "ok"

    res_state = client.get("/api/v1/state")
    assert res_state.status_code == 200
    assert "state" in res_state.json()

    res_pnl = client.get("/api/v1/stats/pnl")
    assert res_pnl.status_code == 200

    res_signals = client.get("/api/v1/analytics/signals")
    assert res_signals.status_code == 200


def test_mutating_endpoints_missing_header_returns_401():
    """Verify mutating endpoints return 401 Unauthorized when X-MMS4-API-KEY header is missing."""
    res_killall = client.post("/api/v1/emergency/killall")
    assert res_killall.status_code == 401
    assert "Unauthorized" in res_killall.json()["detail"]

    res_reverse = client.post("/api/v1/emergency/reverse")
    assert res_reverse.status_code == 401
    assert "Unauthorized" in res_reverse.json()["detail"]

    res_high_risk = client.post("/api/v1/trade/high_risk_add", json={"side": "LONG", "size": 1.0})
    assert res_high_risk.status_code == 401
    assert "Unauthorized" in res_high_risk.json()["detail"]


def test_mutating_endpoints_invalid_header_returns_401():
    """Verify mutating endpoints return 401 Unauthorized when X-MMS4-API-KEY header is invalid."""
    headers = {"X-MMS4-API-KEY": INVALID_KEY}

    res_killall = client.post("/api/v1/emergency/killall", headers=headers)
    assert res_killall.status_code == 401

    res_reverse = client.post("/api/v1/emergency/reverse", headers=headers)
    assert res_reverse.status_code == 401

    res_high_risk = client.post("/api/v1/trade/high_risk_add", json={"side": "LONG", "size": 1.0}, headers=headers)
    assert res_high_risk.status_code == 401


def test_mutating_endpoints_valid_header_returns_200():
    """Verify mutating endpoints return 200 OK when valid X-MMS4-API-KEY header is provided."""
    headers = {"X-MMS4-API-KEY": VALID_KEY}

    # killall now really engages Maintenance Mode (issue #67 — it used to be a
    # no-op) — disable it again in `finally` so this doesn't leak into later
    # tests in the same pytest process that assume trading is unblocked.
    try:
        res_killall = client.post("/api/v1/emergency/killall", headers=headers)
        assert res_killall.status_code == 200
        body = res_killall.json()
        assert body["status"] == "success"
        assert body["maintenance_mode_engaged"] is True
        assert body["positions_closed"] == []
        assert body["positions_failed"] == []
    finally:
        from mms4tk.okx_executor import set_maintenance_lock
        set_maintenance_lock(False)

    # Audit P9.3 (2026-09-08): fail-closed — auth passes (verify_api_key runs before
    # the handler body), but the endpoint itself is not yet implemented and always
    # refuses with 501 rather than reporting a fake success.
    res_reverse = client.post("/api/v1/emergency/reverse", headers=headers)
    assert res_reverse.status_code == 501

    # Audit P9.7 (2026-09-08): high_risk_add now enforces the same persisted Single
    # Addon Guard as the daemon's automatic path — a repeat call against the same
    # (symbol, side, entry_price) is correctly "rejected", not "success" a second
    # time, so this assertion tolerates either rather than requiring "success".
    res_high_risk = client.post("/api/v1/trade/high_risk_add", json={"side": "LONG", "size": 1.0}, headers=headers)
    assert res_high_risk.status_code == 200
    assert res_high_risk.json()["status"] in ("success", "rejected")


def test_killall_actually_closes_every_open_position(monkeypatch):
    """
    Regression test for issue #67: killall used to only call cancel_all_orders()
    (which the bot rarely has anything queued for) and unconditionally claim
    "position close triggered" without closing anything. It must now call
    reduce_position(reduction_pct=1.0) for every open position and report the
    real per-position outcome.
    """
    import mms4tk.api as api_module

    fake_positions = [
        {"symbol": "BTC/USDC", "side": "long", "contracts": 3.0},
        {"symbol": "ETH/USDC", "side": "short", "contracts": 5.0},
    ]

    closed_calls = []

    async def _fake_fetch_positions(*args, **kwargs):
        return fake_positions

    async def _fake_reduce_position(symbol, side, reduction_pct=0.5, reason="STALE_SPLIT"):
        closed_calls.append((symbol, side, reduction_pct, reason))
        return {"status": "executed", "symbol": symbol, "side": side}

    async def _fake_cancel_all_orders(*args, **kwargs):
        return []

    monkeypatch.setattr(api_module.executor, "fetch_positions", _fake_fetch_positions)
    monkeypatch.setattr(api_module.executor, "reduce_position", _fake_reduce_position)
    monkeypatch.setattr(api_module.executor, "cancel_all_orders", _fake_cancel_all_orders)

    headers = {"X-MMS4-API-KEY": VALID_KEY}
    try:
        res = client.post("/api/v1/emergency/killall", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "success"
        assert body["maintenance_mode_engaged"] is True
        assert len(body["positions_closed"]) == 2
        assert body["positions_failed"] == []
        assert ("BTC/USDC", "long", 1.0, "EMERGENCY_KILLALL") in closed_calls
        assert ("ETH/USDC", "short", 1.0, "EMERGENCY_KILLALL") in closed_calls
    finally:
        from mms4tk.okx_executor import set_maintenance_lock
        set_maintenance_lock(False)


def test_killall_reports_failed_closes_without_lying(monkeypatch):
    """A position that fails to close must show up in positions_failed, and the
    endpoint must not claim overall success."""
    import mms4tk.api as api_module

    fake_positions = [{"symbol": "BTC/USDC", "side": "long", "contracts": 3.0}]

    async def _fake_fetch_positions(*args, **kwargs):
        return fake_positions

    async def _fake_reduce_position(symbol, side, reduction_pct=0.5, reason="STALE_SPLIT"):
        return {"status": "no_position", "symbol": symbol, "side": side}

    async def _fake_cancel_all_orders(*args, **kwargs):
        return []

    monkeypatch.setattr(api_module.executor, "fetch_positions", _fake_fetch_positions)
    monkeypatch.setattr(api_module.executor, "reduce_position", _fake_reduce_position)
    monkeypatch.setattr(api_module.executor, "cancel_all_orders", _fake_cancel_all_orders)

    headers = {"X-MMS4-API-KEY": VALID_KEY}
    try:
        res = client.post("/api/v1/emergency/killall", headers=headers)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "partial"
        assert body["positions_closed"] == []
        assert len(body["positions_failed"]) == 1
        assert body["positions_failed"][0]["symbol"] == "BTC/USDC"
    finally:
        from mms4tk.okx_executor import set_maintenance_lock
        set_maintenance_lock(False)

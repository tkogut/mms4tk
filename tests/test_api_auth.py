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

    res_killall = client.post("/api/v1/emergency/killall", headers=headers)
    assert res_killall.status_code == 200
    assert res_killall.json()["status"] == "success"

    # Audit P9.3 (2026-09-08): fail-closed — auth passes (verify_api_key runs before
    # the handler body), but the endpoint itself is not yet implemented and always
    # refuses with 501 rather than reporting a fake success.
    res_reverse = client.post("/api/v1/emergency/reverse", headers=headers)
    assert res_reverse.status_code == 501

    res_high_risk = client.post("/api/v1/trade/high_risk_add", json={"side": "LONG", "size": 1.0}, headers=headers)
    assert res_high_risk.status_code == 200
    assert res_high_risk.json()["status"] == "success"

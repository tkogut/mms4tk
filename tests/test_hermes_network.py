"""
Hermes Network Integration Tests (tests/test_hermes_network.py)

Validates HTTP routes under /api/v1/ accessible to Hermes agent over Traefik proxy network:
- GET /api/v1/health
- GET /api/v1/state
- GET /api/v1/stats/pnl
- GET /api/v1/analytics/signals
- POST /api/v1/emergency/killall
- POST /api/v1/emergency/reverse
- POST /api/v1/trade/high_risk_add
"""

import os
import pytest
from fastapi.testclient import TestClient
from mms4tk.api import app

API_KEY = os.getenv("MMS4_API_KEY", "test-api-auth-key-unit-test")
AUTH_HEADERS = {"X-MMS4-API-KEY": API_KEY}


@pytest.fixture
def client():
    """
    FastAPI TestClient fixture for Hermes network route testing.

    Using TestClient as a context manager fires the real ASGI lifespan, which starts
    the shared daemon's background loop against the shared `executor` singleton. Real
    OKX network calls from that singleton are blocked globally by the autouse
    _no_real_okx_network fixture in tests/conftest.py, so this file tests HTTP route
    wiring (the point of these tests, per the module docstring) rather than live
    exchange connectivity.
    """
    with TestClient(app) as test_client:
        yield test_client


def test_get_health_endpoint(client: TestClient):
    """Validate GET /api/v1/health returns 200 OK with status and version."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    from mms4tk import __version__
    assert data["version"] == __version__


def test_get_state_endpoint(client: TestClient):
    """Validate GET /api/v1/state returns 200 OK with MMS4 strategy state metrics."""
    response = client.get("/api/v1/state")
    assert response.status_code == 200
    data = response.json()
    assert "state" in data
    assert "leverage" in data
    assert "current_equity" in data
    assert "realized_pnl" in data
    assert "total_trades" in data
    assert isinstance(data["leverage"], (int, float))


def test_get_balance_endpoint(client: TestClient):
    """Validate GET /api/v1/balance returns 200 OK with free and total account balances."""
    response = client.get("/api/v1/balance")
    assert response.status_code == 200
    data = response.json()
    assert "free" in data
    assert "total" in data


def test_get_stats_pnl_endpoint(client: TestClient):
    """Validate GET /api/v1/stats/pnl returns 200 OK with PnL and trade statistics."""
    response = client.get("/api/v1/stats/pnl")
    assert response.status_code == 200
    data = response.json()
    assert "daily_roe" in data
    assert "realized_pnl" in data
    assert "unrealized_pnl" in data
    assert "total_trades" in data
    assert "win_rate" in data


def test_get_analytics_signals_endpoint(client: TestClient):
    """Validate GET /api/v1/analytics/signals returns 200 OK with latest SignalResult."""
    response = client.get("/api/v1/analytics/signals")
    assert response.status_code == 200
    data = response.json()
    assert "signal_type" in data
    assert "price" in data
    assert "stop_loss" in data
    assert "leverage" in data
    assert "is_allowed" in data
    assert "reason" in data


def test_post_emergency_killall_endpoint(client: TestClient):
    """Validate POST /api/v1/emergency/killall executes emergency stop sequence."""
    response = client.post("/api/v1/emergency/killall", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "message" in data
    assert "action_taken" in data
    assert "cancelled" in data["action_taken"].lower() or "close" in data["action_taken"].lower()


def test_post_emergency_reverse_endpoint(client: TestClient):
    """Validate POST /api/v1/emergency/reverse executes emergency position reversal."""
    response = client.post("/api/v1/emergency/reverse", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "message" in data
    assert "action_taken" in data
    assert "reversed" in data["action_taken"].lower() or "closed" in data["action_taken"].lower()


def test_post_trade_high_risk_add_endpoint(client: TestClient):
    """Validate POST /api/v1/trade/high_risk_add handles high-risk position addition."""
    payload = {
        "side": "LONG",
        "size": 1.5,
        "stop_loss_pct": 0.01
    }
    response = client.post("/api/v1/trade/high_risk_add", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("success", "rejected")
    assert "executed" in data
    assert "reason" in data
    assert "details" in data


def test_post_trade_high_risk_add_short(client: TestClient):
    """Validate POST /api/v1/trade/high_risk_add with SHORT side."""
    payload = {
        "side": "SHORT",
        "size": 2.0,
        "stop_loss_pct": 0.015
    }
    response = client.post("/api/v1/trade/high_risk_add", json=payload, headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("success", "rejected")
    assert isinstance(data["executed"], bool)

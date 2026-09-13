"""Faza 6 C.3.2: GET /api/v1/diagnostics/bias-sync -- per-symbol directional_bias
layer visibility + suppression flag."""
import pytest
from fastapi.testclient import TestClient

from mms4tk.api import app, state_manager
from mms4tk.state_manager import StrategyProfile


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_profile():
    state_manager.update_strategy_profile(StrategyProfile())
    yield
    state_manager.update_strategy_profile(StrategyProfile())


def test_bias_sync_no_auth_required(client):
    """Deliberately unauthenticated -- read-only diagnostics."""
    resp = client.get("/api/v1/diagnostics/bias-sync")
    assert resp.status_code == 200


def test_bias_sync_reports_suppressed_true_with_manual_override(client):
    state_manager.update_strategy_profile(
        StrategyProfile(directional_bias="LONG_ONLY", reason="Manual override test")
    )
    resp = client.get("/api/v1/diagnostics/bias-sync")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["symbols"]) > 0
    for sym_info in data["symbols"]:
        assert sym_info["directional_bias"] == "LONG_ONLY"
        assert sym_info["suppressed"] is True
        assert sym_info["effective_directional_bias"] == "LONG_ONLY"
    assert data["symbols"][0]["last_strategy_profile_reason"] == "Manual override test"
    assert data["symbols"][0]["last_strategy_profile_age_seconds"] >= 0.0


def test_bias_sync_reports_suppressed_false_without_override(client):
    state_manager.update_strategy_profile(StrategyProfile(directional_bias="BOTH"))
    resp = client.get("/api/v1/diagnostics/bias-sync")
    assert resp.status_code == 200
    data = resp.json()
    for sym_info in data["symbols"]:
        assert sym_info["directional_bias"] == "BOTH"
        assert sym_info["suppressed"] is False

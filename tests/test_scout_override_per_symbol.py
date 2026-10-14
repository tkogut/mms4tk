"""scout_override / HITL SWEEP_WICK must be able to relax a NON-primary symbol.

`relax_scout_mode()` was called without a symbol from both endpoints, so it only ever
acted on the primary trading symbol: a stopped-out XAG could not be released by Hermes.
Omitting `symbol` keeps the previous behavior (primary symbol).
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import mms4tk.api as api
from mms4tk.state_manager import MMS4State, MMS4StateManager, SCOUT_OVERRIDE_MIN_HOURS

client = TestClient(api.app)
AUTH_HEADERS = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}
BTC, XAG = "BTC/USDC", "XAG/USDC"


@pytest.fixture
def sm(monkeypatch):
    manager = MMS4StateManager(trading_symbol=BTC)
    monkeypatch.setattr(api, "state_manager", manager)
    manager.set_symbol_state(BTC, MMS4State.SCOUT_MODE, reason="test")
    manager.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
    # Issue #129: scout_override only relaxes SCOUT_MODE past SCOUT_OVERRIDE_MIN_HOURS;
    # backdate so this suite exercises "override after the cooldown", not the
    # (separately tested) cooldown-blocked case.
    backdated = datetime.now(timezone.utc) - timedelta(hours=SCOUT_OVERRIDE_MIN_HOURS + 1)
    manager._scout_since[BTC] = backdated
    manager._scout_since[XAG] = backdated
    return manager


def _hitl(**extra):
    return client.post(
        "/api/v1/hermes/hitl_resolve",
        json={"decision": "SWEEP_WICK", **extra},
        headers=AUTH_HEADERS,
    )


def test_hitl_with_symbol_relaxes_only_that_symbol(sm):
    res = _hitl(symbol=XAG)
    assert res.status_code == 200
    assert res.json()["scout_mode_relaxed"] is True
    assert res.json()["symbol"] == XAG
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    assert sm.get_symbol_state(BTC) == MMS4State.SCOUT_MODE


def test_hitl_symbol_is_normalized(sm):
    assert _hitl(symbol="xag").json()["symbol"] == XAG
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE


def test_hitl_without_symbol_still_relaxes_primary_only(sm):
    res = _hitl()
    assert res.json()["symbol"] == BTC
    assert sm.get_symbol_state(BTC) == MMS4State.BASE_MODE
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_hitl_marubozu_leaves_scout_untouched(sm):
    res = client.post(
        "/api/v1/hermes/hitl_resolve",
        json={"decision": "MARUBOZU_BREAKOUT", "symbol": XAG},
        headers=AUTH_HEADERS,
    )
    assert res.json()["scout_mode_relaxed"] is False
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_hitl_for_symbol_not_in_scout_reports_not_relaxed(sm):
    assert _hitl(symbol="ETH/USDC").json()["scout_mode_relaxed"] is False


def test_strategy_profile_scout_override_with_symbol(sm):
    res = client.post(
        "/api/v1/strategy/profile",
        json={"scout_override": True, "symbol": XAG},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    assert sm.get_symbol_state(BTC) == MMS4State.SCOUT_MODE


def test_strategy_profile_scout_override_without_symbol_is_primary_only(sm):
    client.post("/api/v1/strategy/profile", json={"scout_override": True}, headers=AUTH_HEADERS)
    assert sm.get_symbol_state(BTC) == MMS4State.BASE_MODE
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


@pytest.mark.parametrize("bad", ["", "   ", "XAG:USDC", "X" * 40, "XAG USDC", "XAG/USDC/EXTRA"])
def test_invalid_symbol_is_rejected_not_a_silent_noop(sm, bad):
    assert _hitl(symbol=bad).status_code == 422
    res = client.post("/api/v1/strategy/profile", json={"scout_override": True, "symbol": bad}, headers=AUTH_HEADERS)
    assert res.status_code == 422
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE

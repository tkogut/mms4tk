"""#119: SCOUT_STALE alert, SCOUT visibility in plan_allocation, per-symbol/one-shot
manual SCOUT controls (hitl_resolve, /settings/update, /strategy/profile)."""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import mms4tk.api as api
from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import (
    SCOUT_EXIT_K,
    SCOUT_STALE_ALERT_HOURS,
    MMS4State,
    MMS4StateManager,
    StrategyProfile,
)

BTC, XAG = "BTC/USDC", "XAG/USDC"
SEND = "mms4tk.hermes_webhook.HermesWebhookClient.send_telegram"
client = TestClient(api.app)
AUTH_HEADERS = {"X-MMS4-API-KEY": "test-api-auth-key-unit-test"}


def _ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _make(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "scout_stale.db"))
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db, trading_symbol=BTC)
    daemon = MMS4Daemon(executor=OKXExecutor(), state_manager=sm, logger_db=db, interval_seconds=0.1)
    return db, sm, daemon


def _backdate_rows(db: LoggerDB, symbol: str, state: str, hours_by_id: dict):
    with sqlite3.connect(db.db_path) as conn:
        for row_id, hours in hours_by_id.items():
            conn.execute("UPDATE state_history SET timestamp = ? WHERE id = ?", (_ago(hours).isoformat(), row_id))
        conn.commit()


# ── scout_since tracking ────────────────────────────────────────────────────

def test_scout_since_set_on_entry_and_cleared_on_exit():
    sm = MMS4StateManager(trading_symbol=BTC)
    assert sm.get_scout_since(XAG) is None
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="t")
    since = sm.get_scout_since(XAG)
    assert since is not None and (datetime.now(timezone.utc) - since).total_seconds() < 5
    assert sm.scout_symbols() == [XAG]
    sm.set_symbol_state(XAG, MMS4State.BASE_MODE, reason="t")
    assert sm.get_scout_since(XAG) is None
    assert sm.scout_symbols() == []


def test_scout_since_not_reset_by_repeated_scout_writes():
    sm = MMS4StateManager(trading_symbol=BTC)
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="t")
    sm._scout_since[XAG] = _ago(30)
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="again")
    assert sm.get_scout_since(XAG) < _ago(29)


def test_scout_since_restored_from_start_of_current_run(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "restore.db"))
    sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="old run")   # id 1
    sm.set_symbol_state(XAG, MMS4State.BASE_MODE, reason="exit")       # id 2
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="new run")   # id 3
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="rewrite")   # id 4
    _backdate_rows(db, XAG, "", {1: 100, 2: 90, 3: 40, 4: 5})

    restarted = MMS4StateManager(logger_db=db, trading_symbol=BTC)
    since = restarted.get_scout_since(XAG)
    assert since is not None
    # start of the CURRENT run (40 h ago), not the old run and not the latest rewrite
    assert _ago(41) < since < _ago(39)


def test_scout_since_none_when_latest_row_not_scout(tmp_path: Path):
    db = LoggerDB(db_path=str(tmp_path / "none.db"))
    sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="t")
    sm.set_symbol_state(XAG, MMS4State.BASE_MODE, reason="t")
    assert db.get_scout_since(XAG) is None
    assert db.get_scout_since("NOPE/USDC") is None


# ── alert ───────────────────────────────────────────────────────────────────

def _scout_for(sm: MMS4StateManager, symbol: str, hours: float) -> None:
    sm.set_symbol_state(symbol, MMS4State.SCOUT_MODE, reason="t")
    sm._scout_since[symbol] = _ago(hours)


def test_no_alert_below_threshold(tmp_path: Path):
    _, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS - 1)
    with patch(SEND, return_value=True) as send:
        daemon._check_scout_stale_alert()
    send.assert_not_called()


def test_alert_fires_once_then_repeats_after_threshold(tmp_path: Path):
    db, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS + 1)
    with patch(SEND, return_value=True) as send:
        daemon._check_scout_stale_alert()
        daemon._check_scout_stale_alert()  # next cycle: marker suppresses
    assert send.call_count == 1
    kwargs = send.call_args.kwargs
    assert "SCOUT_STALE" in kwargs["title"] and XAG in kwargs["title"]
    assert f"0/{SCOUT_EXIT_K}" in kwargs["body"]

    key = f"scout_stale_alerted:{XAG}"
    db.set_setting(key, _ago(SCOUT_STALE_ALERT_HOURS + 1).isoformat())  # last alert is old
    with patch(SEND, return_value=True) as send:
        daemon._check_scout_stale_alert()
    assert send.call_count == 1


def test_alert_marker_survives_restart(tmp_path: Path):
    db, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS + 1)
    with patch(SEND, return_value=True):
        daemon._check_scout_stale_alert()
    sm2 = MMS4StateManager(logger_db=db, trading_symbol=BTC)
    sm2._scout_since[XAG] = _ago(SCOUT_STALE_ALERT_HOURS + 2)
    daemon2 = MMS4Daemon(executor=OKXExecutor(), state_manager=sm2, logger_db=db, interval_seconds=0.1)
    with patch(SEND, return_value=True) as send:
        daemon2._check_scout_stale_alert()
    send.assert_not_called()


def test_failed_send_is_retried_next_cycle(tmp_path: Path):
    _, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS + 1)
    with patch(SEND, return_value=False) as send:
        daemon._check_scout_stale_alert()
        daemon._check_scout_stale_alert()
    assert send.call_count == 2


def test_leaving_scout_clears_marker_and_alert(tmp_path: Path):
    db, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS + 1)
    with patch(SEND, return_value=True):
        daemon._check_scout_stale_alert()
    assert db.get_setting(f"scout_stale_alerted:{XAG}")
    sm.set_symbol_state(XAG, MMS4State.BASE_MODE, reason="t")
    assert db.get_setting(f"scout_stale_alerted:{XAG}") is None
    with patch(SEND, return_value=True) as send:
        daemon._check_scout_stale_alert()
    send.assert_not_called()


def test_alert_only_for_stale_symbol(tmp_path: Path):
    _, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS + 1)
    _scout_for(sm, BTC, 1)
    with patch(SEND, return_value=True) as send:
        daemon._check_scout_stale_alert()
    assert send.call_count == 1
    assert XAG in send.call_args.kwargs["title"]


# ── plan_allocation visibility ──────────────────────────────────────────────

def test_plan_allocation_exposes_scout_fields(tmp_path: Path, empty_symbols_store):
    _, sm, daemon = _make(tmp_path)
    plan = daemon.plan_allocation(XAG)
    assert plan["scout_since"] is None and plan["scout_age_hours"] is None
    assert plan["scout_ok"] == 0 and plan["scout_exit_k"] == SCOUT_EXIT_K

    _scout_for(sm, XAG, 26)
    sm._set_scout_ok(XAG, 1)
    plan = daemon.plan_allocation(XAG)
    assert plan["scout_since"] is not None
    assert 25.9 < plan["scout_age_hours"] < 26.1
    assert plan["scout_ok"] == 1


# ── E4 tail: hitl_resolve / strategy profile / settings ─────────────────────

@pytest.fixture
def sm(monkeypatch):
    manager = MMS4StateManager(trading_symbol=BTC)
    monkeypatch.setattr(api, "state_manager", manager)
    manager.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
    return manager


def test_hitl_without_fields_keeps_current_profile(sm):
    sm.update_strategy_profile(StrategyProfile(directional_bias="SHORT_ONLY", risk_profile="AGGRESSIVE"))
    res = client.post(
        "/api/v1/hermes/hitl_resolve",
        json={"decision": "SWEEP_WICK", "symbol": XAG},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 200
    profile = res.json()["profile"]
    assert profile["directional_bias"] == "SHORT_ONLY"
    assert profile["risk_profile"] == "AGGRESSIVE"


def test_hitl_explicit_fields_still_override(sm):
    res = client.post(
        "/api/v1/hermes/hitl_resolve",
        json={"decision": "MARUBOZU_BREAKOUT", "directional_bias": "LONG_ONLY", "risk_profile": "CONSERVATIVE"},
        headers=AUTH_HEADERS,
    )
    assert res.json()["profile"]["directional_bias"] == "LONG_ONLY"
    assert res.json()["profile"]["risk_profile"] == "CONSERVATIVE"


def test_scout_override_is_one_shot(sm):
    client.post("/api/v1/strategy/profile", json={"scout_override": True, "symbol": XAG}, headers=AUTH_HEADERS)
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="new stop-out")
    res = client.post("/api/v1/strategy/profile", json={"directional_bias": "LONG_ONLY", "symbol": XAG}, headers=AUTH_HEADERS)
    assert res.json()["scout_override"] is False
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_settings_scout_mode_with_symbol(sm):
    res = client.post("/api/v1/settings/update", json={"scout_mode": False, "symbol": "xag"}, headers=AUTH_HEADERS)
    assert res.status_code == 200
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE
    res = client.post("/api/v1/settings/update", json={"scout_mode": True, "symbol": XAG}, headers=AUTH_HEADERS)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
    assert sm.get_symbol_state(BTC) == MMS4State.BASE_MODE


def test_settings_scout_mode_without_symbol_is_primary(sm):
    client.post("/api/v1/settings/update", json={"scout_mode": True}, headers=AUTH_HEADERS)
    assert sm.get_symbol_state(BTC) == MMS4State.SCOUT_MODE


def test_settings_invalid_symbol_rejected(sm):
    res = client.post("/api/v1/settings/update", json={"scout_mode": False, "symbol": "XAG:USDC"}, headers=AUTH_HEADERS)
    assert res.status_code == 422
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_scout_override_not_stored_true(sm):
    res = client.post("/api/v1/strategy/profile", json={"scout_override": True, "symbol": XAG}, headers=AUTH_HEADERS)
    assert res.json()["scout_override"] is True          # response echoes the request
    assert sm.strategy_profile.scout_override is False   # but nothing stays armed
    hitl = client.post(
        "/api/v1/hermes/hitl_resolve", json={"decision": "SWEEP_WICK", "symbol": XAG}, headers=AUTH_HEADERS
    )
    assert hitl.json()["scout_override"] is True
    assert sm.strategy_profile.scout_override is False


def test_marker_write_failure_does_not_block_other_symbols(tmp_path: Path):
    db, sm, daemon = _make(tmp_path)
    _scout_for(sm, XAG, SCOUT_STALE_ALERT_HOURS + 1)
    _scout_for(sm, "ETH/USDC", SCOUT_STALE_ALERT_HOURS + 1)
    with patch(SEND, return_value=True) as send, patch.object(db, "set_setting", side_effect=RuntimeError("db")):
        daemon._check_scout_stale_alert()
    assert send.call_count == 2


def test_settings_scout_mode_mirrors_when_symbol_is_primary(sm):
    client.post("/api/v1/settings/update", json={"scout_mode": True, "symbol": BTC}, headers=AUTH_HEADERS)
    assert sm.get_symbol_state(BTC) == MMS4State.SCOUT_MODE

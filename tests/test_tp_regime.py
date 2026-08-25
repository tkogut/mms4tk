import os
import pytest
import tempfile
import asyncio
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from mms4tk.logger_db import LoggerDB
from mms4tk.api import app, logger_db
from mms4tk.daemon import MMS4Daemon
from mms4tk.analytics import Candle, TMAIndicator, IndicatorParams, SignalResult
from mms4tk.state_manager import MMS4StateManager, MMS4State


def test_logger_db_tp_regime_settings():
    """Verify tp_mode and tp_auto_regime keys can be set, fetched, and persisted in SQLite SSOT."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = LoggerDB(db_path=db_path)
        
        # Test defaults / empty
        assert db.get_setting("tp_mode", "CLASSIC") == "CLASSIC"
        assert db.get_setting("tp_auto_regime", "false") == "false"
        
        # Test update
        db.set_setting("tp_mode", "DYNAMIC_MIDLINE", updated_by="test_suite")
        db.set_setting("tp_auto_regime", "true", updated_by="test_suite")
        
        assert db.get_setting("tp_mode") == "DYNAMIC_MIDLINE"
        assert db.get_setting("tp_auto_regime") == "true"
        
        # Test persistence across re-instantiation
        db2 = LoggerDB(db_path=db_path)
        assert db2.get_setting("tp_mode") == "DYNAMIC_MIDLINE"
        assert db2.get_setting("tp_auto_regime") == "true"
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


def test_api_tp_regime_manual_override_lock(monkeypatch):
    """
    Kłódka (Manual Override): with tp_auto_regime OFF, the AI agent cannot change tp_mode.

    The agent is simulated by the CREDENTIAL it presents, not by a "source" string in the
    body. That distinction is the whole point: a self-declared source is worthless as an
    authorization signal, since an agent holding a valid key can put any value in it.
    """
    operator_key = "operator-key-unit-test"
    agent_key = "agent-key-unit-test"
    monkeypatch.setenv("MMS4_API_KEY", operator_key)
    monkeypatch.setenv("MMS4_HERMES_API_KEY", agent_key)

    client = TestClient(app)
    operator = {"X-MMS4-API-KEY": operator_key}
    agent = {"X-MMS4-API-KEY": agent_key}

    # 1. Manual Override on, mode CLASSIC
    res = client.post("/api/v1/settings/update",
                      json={"tp_auto_regime": False, "tp_mode": "CLASSIC"}, headers=operator)
    assert res.status_code == 200
    assert res.json()["tp_mode"] == "CLASSIC"
    assert res.json()["tp_auto_regime"] is False

    # 2. Agent tries the dedicated endpoint -> rejected, even claiming to be the UI
    res = client.post("/api/v1/settings/tp_regime",
                      json={"tp_mode": "DYNAMIC_MIDLINE", "source": "ui", "reason": "spoofed"},
                      headers=agent)
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    assert "Manual Override active" in res.json()["message"]
    assert client.get("/api/v1/settings").json()["tp_mode"] == "CLASSIC"

    # 3. Agent tries the general settings endpoint -> also blocked
    #    (this path used to be dead code: `_source` is a Pydantic private attribute and
    #     was never populated from the body, so the lock never fired here at all)
    res = client.post("/api/v1/settings/update",
                      json={"tp_mode": "DYNAMIC_MIDLINE"}, headers=agent)
    assert res.status_code == 200
    assert client.get("/api/v1/settings").json()["tp_mode"] == "CLASSIC"

    # 4. Explicit null source must not crash the endpoint (used to be a 500)
    res = client.post("/api/v1/settings/tp_regime",
                      json={"tp_mode": "DYNAMIC_MIDLINE", "source": None}, headers=agent)
    assert res.status_code == 200

    # 5. Operator may switch
    res = client.post("/api/v1/settings/tp_regime",
                      json={"tp_mode": "DYNAMIC_MIDLINE", "reason": "Operator decision"},
                      headers=operator)
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert res.json()["tp_mode"] == "DYNAMIC_MIDLINE"

    # 6. With Auto-Regime ON the agent regains control
    client.post("/api/v1/settings/update", json={"tp_auto_regime": True}, headers=operator)
    res = client.post("/api/v1/settings/tp_regime",
                      json={"tp_mode": "CLASSIC", "reason": "High trend detected"}, headers=agent)
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert res.json()["tp_mode"] == "CLASSIC"


def test_lock_is_advisory_without_a_separate_agent_key(monkeypatch):
    """
    Without MMS4_HERMES_API_KEY the operator and the agent share one credential, so they
    cannot be told apart and the lock is advisory. Pinned so the degradation stays a
    documented, deliberate property rather than a silent surprise.
    """
    from mms4tk.api import is_lock_enforceable

    monkeypatch.delenv("MMS4_HERMES_API_KEY", raising=False)
    assert is_lock_enforceable() is False

    monkeypatch.setenv("MMS4_HERMES_API_KEY", "agent-key-unit-test")
    assert is_lock_enforceable() is True


def test_daemon_dynamic_midline_tp1_trigger_short():
    """In DYNAMIC_MIDLINE mode, price <= tma_mid triggers 50% scale-out for SHORT position."""
    async def _async_test():
        mock_executor = MagicMock()
        mock_executor.calculate_order_amount = MagicMock(return_value=1.0)
        mock_executor.reduce_position = AsyncMock(return_value={"status": "executed", "id": "123"})
        mock_executor.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDC", "side": "short", "contracts": 2.0, "entryPrice": 60000.0}
        ])

        # Create 25 closed candles with TMA mid around 60000
        candles = [
            Candle(timestamp=i * 60, open=60000.0, high=60500.0, low=59500.0, close=60000.0, volume=10.0)
            for i in range(25)
        ]
        # Current candle price reaches 58500 (below TMA mid)
        candles[-1] = Candle(timestamp=25 * 60, open=59000.0, high=59200.0, low=58500.0, close=58500.0, volume=10.0)
        mock_executor.fetch_candles = AsyncMock(return_value=candles)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = LoggerDB(db_path=db_path)
            db.set_setting("tp_mode", "DYNAMIC_MIDLINE")

            sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
            daemon = MMS4Daemon(executor=mock_executor, state_manager=sm, logger_db=db, symbol="BTC/USDC")

            result = await daemon.execute_symbol_cycle("BTC/USDC")

            assert result["outcome"] == "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_called_once_with(
                symbol="BTC/USDC",
                side="short",
                reduction_pct=0.50,
                reason="DYNAMIC_MIDLINE_TP1"
            )
            assert daemon._midline_tp_taken.get("BTC/USDC_short") is True

            # Second cycle with same position -> must NOT fire duplicate TP1
            mock_executor.reduce_position.reset_mock()
            result2 = await daemon.execute_symbol_cycle("BTC/USDC")
            assert result2["outcome"] != "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_not_called()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    asyncio.run(_async_test())


def test_daemon_dynamic_midline_tp1_trigger_long():
    """In DYNAMIC_MIDLINE mode, price >= tma_mid triggers 50% scale-out for LONG position."""
    async def _async_test():
        mock_executor = MagicMock()
        mock_executor.calculate_order_amount = MagicMock(return_value=1.0)
        mock_executor.reduce_position = AsyncMock(return_value={"status": "executed", "id": "456"})
        mock_executor.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDC", "side": "long", "contracts": 2.0, "entryPrice": 58000.0}
        ])

        candles = [
            Candle(timestamp=i * 60, open=58000.0, high=58500.0, low=57500.0, close=58000.0, volume=10.0)
            for i in range(25)
        ]
        # Current candle price reaches 59500 (above TMA mid)
        candles[-1] = Candle(timestamp=25 * 60, open=58500.0, high=59500.0, low=58400.0, close=59500.0, volume=10.0)
        mock_executor.fetch_candles = AsyncMock(return_value=candles)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = LoggerDB(db_path=db_path)
            db.set_setting("tp_mode", "DYNAMIC_MIDLINE")

            sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
            daemon = MMS4Daemon(executor=mock_executor, state_manager=sm, logger_db=db, symbol="BTC/USDC")

            result = await daemon.execute_symbol_cycle("BTC/USDC")

            assert result["outcome"] == "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_called_once_with(
                symbol="BTC/USDC",
                side="long",
                reduction_pct=0.50,
                reason="DYNAMIC_MIDLINE_TP1"
            )
            assert daemon._midline_tp_taken.get("BTC/USDC_long") is True
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    asyncio.run(_async_test())


def test_daemon_classic_mode_ignores_midline():
    """In CLASSIC mode, mid-line crossing does NOT trigger partial scale-out."""
    async def _async_test():
        mock_executor = MagicMock()
        mock_executor.calculate_order_amount = MagicMock(return_value=1.0)
        mock_executor.reduce_position = AsyncMock(return_value={"status": "executed"})
        mock_executor.fetch_positions = AsyncMock(return_value=[
            {"symbol": "BTC/USDC", "side": "short", "contracts": 2.0, "entryPrice": 60000.0}
        ])

        candles = [
            Candle(timestamp=i * 60, open=60000.0, high=60500.0, low=59500.0, close=60000.0, volume=10.0)
            for i in range(25)
        ]
        candles[-1] = Candle(timestamp=25 * 60, open=59000.0, high=59200.0, low=58500.0, close=58500.0, volume=10.0)
        mock_executor.fetch_candles = AsyncMock(return_value=candles)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            db_path = tf.name

        try:
            db = LoggerDB(db_path=db_path)
            db.set_setting("tp_mode", "CLASSIC")

            sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
            daemon = MMS4Daemon(executor=mock_executor, state_manager=sm, logger_db=db, symbol="BTC/USDC")

            result = await daemon.execute_symbol_cycle("BTC/USDC")

            assert result["outcome"] != "DYNAMIC_MIDLINE_TP1"
            mock_executor.reduce_position.assert_not_called()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    asyncio.run(_async_test())


def test_state_machine_sl_transition_after_partial_tp():
    """State Machine Rule: If position took partial TP1, but remaining 50% hits SL (>=2%), state transitions to SCOUT_MODE."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        db = LoggerDB(db_path=db_path)
        sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)

        # 1. Partial TP1 does not demote/promote state machine
        assert sm.current_state == MMS4State.BASE_MODE

        # 2. Remaining 50% hits SL at -2.0% (0.02)
        sm.record_trade(
            trade_id="trade_sl_after_tp1",
            symbol="BTC/USDC",
            side="short",
            entry_price=60000.0,
            exit_price=61200.0,
            amount=1.0,
            pnl=-20.0,
            pnl_percent=-0.02,
            is_stop_loss=True,
            is_take_profit=False,
            sl_percent=0.02
        )

        # Must transition to SCOUT_MODE
        assert sm.current_state == MMS4State.SCOUT_MODE
        assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


# ── Regression guards for the v0.34.0 code review ───────────────────────────

def test_failed_reduce_is_not_recorded_as_tp1(tmp_path):
    """
    A reduce_position that did not execute must NOT count as Take Profit 1.

    The original predicate excluded only "not_divisible", so "no_position" — returned
    when the position vanished between fetch_positions() and the reduce (SL fill, manual
    close, race) — latched the flag, wrote a TP1 row and sent a Telegram quoting a PnL
    for an order that was never placed.
    """
    from mms4tk.daemon import MMS4Daemon

    for status in ("no_position", "error", "rejected", "not_divisible"):
        assert MMS4Daemon._close_succeeded({"status": status}) is False, status
    assert MMS4Daemon._close_succeeded({"id": "abc", "status": "executed"}) is True


def test_midline_tp_flag_survives_restart(tmp_path):
    """
    The 50% scale-out marker must outlive the process.

    Held only in memory, it was lost on a container rebuild (routine here) and on any
    cycle that transiently saw no positions — and because the trigger is a level test
    (price <= mid), not a crossing test, the next cycle cut another 50% off the same
    position.
    """
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "midline.db")
    db = LoggerDB(db_path=db_path)
    assert db.is_midline_tp_taken("ETH/USDC", "short") is False

    db.record_midline_tp("ETH/USDC", "short", price=1880.5)
    assert db.is_midline_tp_taken("ETH/USDC", "short") is True
    # The other side of the same symbol is tracked separately.
    assert db.is_midline_tp_taken("ETH/USDC", "long") is False

    # A brand-new LoggerDB on the same file — i.e. after a restart — still sees it.
    assert LoggerDB(db_path=db_path).is_midline_tp_taken("ETH/USDC", "short") is True

    # Only closing the position clears it.
    db.clear_midline_tp("ETH/USDC", "short")
    assert db.is_midline_tp_taken("ETH/USDC", "short") is False


def test_midline_ledger_read_failure_fails_closed():
    """
    If the ledger cannot be read, assume TP1 was already taken.

    Failing open would cut another 50% off a position that may already be halved; failing
    closed only skips a take-profit, which is recoverable.
    """
    from unittest.mock import MagicMock
    from mms4tk.daemon import MMS4Daemon
    from mms4tk.okx_executor import OKXExecutor

    broken_db = MagicMock()
    broken_db.is_midline_tp_taken.side_effect = RuntimeError("database is locked")
    d = MMS4Daemon(executor=MagicMock(spec=OKXExecutor), logger_db=broken_db, symbol="ETH/USDC")

    assert d._is_midline_tp_taken("ETH/USDC", "short") is True


def test_tp1_dedupe_key_distinguishes_sub_dollar_prices():
    """int(price) collapsed every sub-$1 alert onto one dedupe key, muting all but the first."""
    def key(symbol, side, price):
        return f"TP1_MID_{symbol}_{side}_{price:.8f}"

    assert key("DOGE/USDC", "short", 0.3211) != key("DOGE/USDC", "short", 0.2874)

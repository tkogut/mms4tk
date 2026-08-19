"""
Tests for Hermes Webhook, Event Dispatch, and HITL Integration (Task 09).
"""

import json
import os
import sqlite3
import tempfile
import pytest
from unittest.mock import MagicMock, patch

from mms4tk.hermes_webhook import HermesWebhookClient, HermesWebhookEvent
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.logger_db import LoggerDB


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database for testing."""
    db_path = str(tmp_path / "test_logs.db")
    return db_path


@pytest.fixture
def webhook_client(tmp_db):
    """Create a HermesWebhookClient with temporary database."""
    return HermesWebhookClient(db_path=tmp_db)


@pytest.fixture
def logger_db(tmp_db):
    """Create a LoggerDB instance with temporary database."""
    return LoggerDB(db_path=tmp_db)


class TestHermesWebhookClient:
    """Tests for HermesWebhookClient event queuing."""

    def test_webhook_table_created(self, webhook_client, tmp_db):
        """Verify webhook_events table is created on init."""
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_events'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_emit_event(self, webhook_client):
        """Verify event is written to database."""
        event = HermesWebhookEvent(
            event_type="TEST_EVENT",
            title="Test Card Title",
            body="Test card body content",
            metadata={"symbol": "BTC/USDC", "price": 65000.0},
            priority=7,
        )
        event_id = webhook_client.emit_event(event)
        assert event_id > 0

    def test_emit_scout_mode_event(self, webhook_client):
        """Verify SCOUT_MODE event generates correct Kanban card data."""
        event_id = webhook_client.emit_scout_mode_event(
            symbol="BTC/USDC",
            sl_price=64800.0,
            sl_percent=0.02,
            reason="Stop Loss hit (2.0%)",
        )
        assert event_id > 0

        # Verify event data
        events = webhook_client.get_pending_events()
        assert len(events) == 1
        event = events[0]
        assert event["event_type"] == "SCOUT_MODE_ACTIVATED"
        assert "H4 Liquidity Sweep Analysis" in event["title"]
        assert "BTC/USDC" in event["title"]
        assert event["priority"] == 9
        assert event["dispatched"] == 0

    def test_get_pending_events(self, webhook_client):
        """Verify pending events retrieval."""
        # Emit multiple events
        webhook_client.emit_scout_mode_event("BTC/USDC", 64800.0, 0.02)
        webhook_client.emit_scout_mode_event("ETH/USDC", 3200.0, 0.025)

        events = webhook_client.get_pending_events()
        assert len(events) == 2
        assert events[0]["id"] < events[1]["id"]  # Ordered by ID ASC

    def test_mark_dispatched(self, webhook_client):
        """Verify event is marked as dispatched."""
        event_id = webhook_client.emit_scout_mode_event("BTC/USDC", 64800.0, 0.02)
        webhook_client.mark_dispatched(event_id, "kanban-task-abc123")

        # Should no longer appear in pending
        pending = webhook_client.get_pending_events()
        assert len(pending) == 0

    def test_mark_error(self, webhook_client):
        """Verify error recording on event."""
        event_id = webhook_client.emit_scout_mode_event("BTC/USDC", 64800.0, 0.02)
        webhook_client.mark_error(event_id, "Connection refused")

        # Event should still be pending (error doesn't mark dispatched)
        pending = webhook_client.get_pending_events()
        assert len(pending) == 1
        assert pending[0]["error"] == "Connection refused"


class TestStateManagerWebhookIntegration:
    """Tests for state_manager webhook emission on SCOUT_MODE transition."""

    def test_on_stop_loss_emits_webhook(self, logger_db, tmp_db):
        """Verify SL >= 2% triggers webhook event emission."""
        webhook = HermesWebhookClient(db_path=tmp_db)
        sm = MMS4StateManager(
            initial_state=MMS4State.BASE_MODE,
            logger_db=logger_db,
            hermes_webhook=webhook,
            trading_symbol="BTC/USDC",
        )

        transitioned = sm.on_stop_loss_hit(sl_percent=0.02, sl_price=64800.0)
        assert transitioned is True
        assert sm.current_state == MMS4State.SCOUT_MODE

        # Verify webhook event was emitted
        events = webhook.get_pending_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "SCOUT_MODE_ACTIVATED"
        assert "BTC/USDC" in events[0]["title"]

    def test_small_sl_no_webhook(self, logger_db, tmp_db):
        """Verify SL < 2% does NOT trigger webhook."""
        webhook = HermesWebhookClient(db_path=tmp_db)
        sm = MMS4StateManager(
            initial_state=MMS4State.BASE_MODE,
            logger_db=logger_db,
            hermes_webhook=webhook,
        )

        transitioned = sm.on_stop_loss_hit(sl_percent=0.01, sl_price=64900.0)
        assert transitioned is False
        assert sm.current_state == MMS4State.BASE_MODE

        events = webhook.get_pending_events()
        assert len(events) == 0

    def test_scout_mode_sl_no_double_webhook(self, logger_db, tmp_db):
        """Verify SL in SCOUT_MODE doesn't emit another webhook."""
        webhook = HermesWebhookClient(db_path=tmp_db)
        sm = MMS4StateManager(
            initial_state=MMS4State.SCOUT_MODE,
            logger_db=logger_db,
            hermes_webhook=webhook,
        )

        transitioned = sm.on_stop_loss_hit(sl_percent=0.03, sl_price=64700.0)
        assert transitioned is False  # Already in SCOUT_MODE

        events = webhook.get_pending_events()
        assert len(events) == 0

    def test_no_webhook_client_graceful(self, logger_db):
        """Verify state_manager works without webhook client."""
        sm = MMS4StateManager(
            initial_state=MMS4State.BASE_MODE,
            logger_db=logger_db,
            hermes_webhook=None,
        )

        transitioned = sm.on_stop_loss_hit(sl_percent=0.02, sl_price=64800.0)
        assert transitioned is True
        assert sm.current_state == MMS4State.SCOUT_MODE


class TestHITLResolveEndpoint:
    """Tests for HITL resolve endpoint via FastAPI TestClient."""

    @pytest.fixture
    def client(self, tmp_db):
        """Create FastAPI TestClient with temporary database."""
        # We need to patch the global instances
        from fastapi.testclient import TestClient
        from mms4tk import api

        # Save originals
        orig_logger_db = api.logger_db
        orig_state_manager = api.state_manager
        orig_webhook = api.hermes_webhook

        # Create test instances
        test_logger_db = LoggerDB(db_path=tmp_db)
        test_webhook = HermesWebhookClient(db_path=tmp_db)
        test_state_manager = MMS4StateManager(
            initial_state=MMS4State.SCOUT_MODE,
            logger_db=test_logger_db,
            hermes_webhook=test_webhook,
        )

        api.logger_db = test_logger_db
        api.state_manager = test_state_manager
        api.hermes_webhook = test_webhook

        yield TestClient(api.app, raise_server_exceptions=False)

        # Restore originals
        api.logger_db = orig_logger_db
        api.state_manager = orig_state_manager
        api.hermes_webhook = orig_webhook

    def test_hitl_resolve_sweep_wick(self, client):
        """SWEEP_WICK decision should relax SCOUT_MODE."""
        response = client.post(
            "/api/v1/hermes/hitl_resolve",
            json={
                "decision": "SWEEP_WICK",
                "directional_bias": "BOTH",
                "risk_profile": "MODERATE",
                "reason": "H4 wick clearly reversed into TMA band",
            },
            headers={"X-MMS4-API-KEY": "test-api-auth-key-unit-test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "SWEEP_WICK"
        assert data["scout_override"] is True
        assert data["scout_mode_relaxed"] is True

    def test_hitl_resolve_marubozu_breakout(self, client):
        """MARUBOZU_BREAKOUT decision should maintain SCOUT_MODE."""
        response = client.post(
            "/api/v1/hermes/hitl_resolve",
            json={
                "decision": "MARUBOZU_BREAKOUT",
                "directional_bias": "SHORT_ONLY",
                "risk_profile": "CONSERVATIVE",
                "reason": "H4 full-body breakout below TMA",
            },
            headers={"X-MMS4-API-KEY": "test-api-auth-key-unit-test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["decision"] == "MARUBOZU_BREAKOUT"
        assert data["scout_override"] is False
        assert data["scout_mode_relaxed"] is False

    def test_hitl_resolve_unauthorized(self, client):
        """HITL resolve without API key should return 401."""
        response = client.post(
            "/api/v1/hermes/hitl_resolve",
            json={"decision": "SWEEP_WICK"},
        )
        assert response.status_code == 401

    def test_get_pending_events_endpoint(self, client):
        """Verify GET /api/v1/hermes/events returns empty list initially."""
        response = client.get("/api/v1/hermes/events")
        assert response.status_code == 200
        assert response.json() == []

    def test_mark_dispatched_endpoint(self, client, tmp_db):
        """Verify POST /api/v1/hermes/events/dispatched marks event."""
        # First emit an event
        webhook = HermesWebhookClient(db_path=tmp_db)
        event_id = webhook.emit_scout_mode_event("BTC/USDC", 64800.0, 0.02)

        response = client.post(
            "/api/v1/hermes/events/dispatched",
            json={"event_id": event_id, "kanban_task_id": "test-task-123"},
            headers={"X-MMS4-API-KEY": "test-api-auth-key-unit-test"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

"""Faza 6 C.3.1: `hermes_tool_invocation_logger` middleware + widened
`emergency_action_log` (now covering all 12 Hermes-tool-facing endpoints, not
just the 3 emergency ones), with the new `client_label` column carrying the
caller-supplied `X-MMS4-CLIENT` header.
"""
import os

import pytest
from fastapi.testclient import TestClient

from mms4tk.api import app, logger_db as api_logger_db


@pytest.fixture
def client():
    return TestClient(app)


def test_tool_call_with_client_header_logs_client_label(client):
    os.environ["MMS4_API_KEY"] = "test-api-auth-key-unit-test"
    resp = client.get(
        "/api/v1/analytics/higher_tf",
        headers={"X-MMS4-CLIENT": "h4-cron"},
    )
    assert resp.status_code == 200

    rows = api_logger_db.get_recent_tool_invocations(limit=5)
    matches = [r for r in rows if r["endpoint"] == "/api/v1/analytics/higher_tf"]
    assert matches, "expected a logged row for /api/v1/analytics/higher_tf"
    assert matches[0]["client_label"] == "h4-cron"
    assert matches[0]["outcome"] == "success"


def test_tool_call_without_client_header_logs_null(client):
    resp = client.get("/api/v1/analytics/higher_tf")
    assert resp.status_code == 200

    rows = api_logger_db.get_recent_tool_invocations(limit=5)
    matches = [r for r in rows if r["endpoint"] == "/api/v1/analytics/higher_tf"]
    assert matches
    assert matches[0]["client_label"] is None


def test_api_key_never_logged_in_request_params(client):
    """The only secret in play (X-MMS4-API-KEY) travels as a header, never in
    query params or body -- this asserts the logged request_params never
    contain the key's value, whether or not the caller sent a valid one."""
    api_key = "test-api-auth-key-unit-test"
    os.environ["MMS4_API_KEY"] = api_key

    resp = client.post(
        "/api/v1/strategy/profile",
        headers={"X-MMS4-API-KEY": api_key, "X-MMS4-CLIENT": "hermes-agent"},
        json={"reason": "C.3.1 secret-scrub test"},
    )
    assert resp.status_code == 200

    rows = api_logger_db.get_recent_tool_invocations(limit=5)
    matches = [r for r in rows if r["endpoint"] == "/api/v1/strategy/profile"]
    assert matches
    assert api_key not in matches[0]["request_params"]
    assert matches[0]["client_label"] == "hermes-agent"


def test_emergency_reverse_call_is_logged_with_501_outcome(client):
    """Faza 6 C.3.1: the manual per-handler logging call inside
    emergency_reverse was removed in favor of the uniform middleware -- this
    confirms the call is still captured (now via the middleware) with an
    outcome reflecting the 501 status."""
    api_key = "test-api-auth-key-unit-test"
    os.environ["MMS4_API_KEY"] = api_key
    resp = client.post(
        "/api/v1/emergency/reverse",
        headers={"X-MMS4-API-KEY": api_key},
    )
    assert resp.status_code == 501

    rows = api_logger_db.get_recent_tool_invocations(limit=5)
    matches = [r for r in rows if r["endpoint"] == "/api/v1/emergency/reverse"]
    assert matches
    assert matches[0]["outcome"] == "error_501"

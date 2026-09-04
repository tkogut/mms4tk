"""2026-09-04 fix: Telegram dispatch previously ONLY shelled out to an external
"hermes" CLI, which is structurally unreachable from inside the antigravity_core
container (no `docker` binary, no docker socket mounted, no such binary present) —
confirmed live: both an entry and a stop-loss alert had silently failed the same
day. `_send_now()` now tries the direct Telegram Bot API first; the old CLI
candidates remain only as a fallback.
"""
from unittest.mock import MagicMock, patch

import pytest

from mms4tk.telegram_queue import TelegramQueueManager


@pytest.fixture
def queue_manager():
    """A manager whose worker thread we never start — tests call `_send_now`/
    `_send_via_bot_api` directly, synchronously, to avoid timing flakiness."""
    mgr = TelegramQueueManager.__new__(TelegramQueueManager)
    mgr.min_interval = 3.0
    mgr.dedupe_ttl = 60.0
    mgr._last_send_time = 0.0
    return mgr


def test_send_via_bot_api_missing_credentials_returns_false(queue_manager, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("chat_id", raising=False)
    assert queue_manager._send_via_bot_api("test message") is False


def test_send_via_bot_api_success(queue_manager, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"ok": True}
    with patch("mms4tk.telegram_queue.httpx.post", return_value=fake_response) as mock_post:
        assert queue_manager._send_via_bot_api("hello") is True
        call = mock_post.call_args
        assert "api.telegram.org/bot" in call.args[0]
        assert call.kwargs["json"]["chat_id"] == "12345"
        assert call.kwargs["json"]["text"] == "hello"


def test_send_via_bot_api_falls_back_on_lowercase_chat_id_env_var(queue_manager, monkeypatch):
    """The user's first attempt at setting this env var used lowercase `chat_id` —
    support it too so a stray leftover value doesn't silently do nothing."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-test")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("chat_id", "67890")

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"ok": True}
    with patch("mms4tk.telegram_queue.httpx.post", return_value=fake_response) as mock_post:
        assert queue_manager._send_via_bot_api("hello") is True
        assert mock_post.call_args.kwargs["json"]["chat_id"] == "67890"


def test_send_via_bot_api_handles_telegram_error_response(queue_manager, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    fake_response = MagicMock(status_code=400, text="Bad Request: chat not found")
    fake_response.json.return_value = {"ok": False}
    with patch("mms4tk.telegram_queue.httpx.post", return_value=fake_response):
        assert queue_manager._send_via_bot_api("hello") is False


def test_send_via_bot_api_handles_network_exception(queue_manager, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    with patch("mms4tk.telegram_queue.httpx.post", side_effect=ConnectionError("network down")):
        assert queue_manager._send_via_bot_api("hello") is False


def test_send_now_uses_bot_api_and_never_reaches_the_cli_fallback(queue_manager, monkeypatch):
    """The concrete regression guard: with valid credentials, dispatch must succeed
    via the Bot API alone — subprocess (the old, structurally-broken CLI path)
    must never even be invoked."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    fake_response = MagicMock(status_code=200)
    fake_response.json.return_value = {"ok": True}
    with patch("mms4tk.telegram_queue.httpx.post", return_value=fake_response), \
         patch("mms4tk.telegram_queue.subprocess.run") as mock_subprocess:
        assert queue_manager._send_now("Title", "Body") is True
        mock_subprocess.assert_not_called()


def test_send_now_falls_back_to_cli_when_bot_api_unavailable(queue_manager, monkeypatch):
    """Without credentials, dispatch still attempts the legacy CLI path — proves
    the fallback chain wasn't accidentally removed, only reordered."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("chat_id", raising=False)

    fake_result = MagicMock(returncode=1, stderr="not found")
    with patch("mms4tk.telegram_queue.subprocess.run", return_value=fake_result) as mock_subprocess:
        assert queue_manager._send_now("Title", "Body") is False
        assert mock_subprocess.called

"""Faza 6 C.3.3: MMS4Daemon._check_stale_directional_bias_alert -- Telegram
alert when the global manual directional_bias has been stuck at something
other than "BOTH" for longer than the configured threshold, without a new
strategy_profile write, deduped so it doesn't spam every cycle."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.okx_executor import OKXExecutor
from mms4tk.logger_db import LoggerDB


def _make_daemon(tmp_path: Path) -> MMS4Daemon:
    db = LoggerDB(db_path=str(tmp_path / "stale_bias.db"))
    executor = OKXExecutor()
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=db)
    return MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=db, interval_seconds=0.1)


def _seed_old_profile(daemon: MMS4Daemon, bias: str, hours_old: float) -> None:
    """Directly insert a strategy_profile row with a manufactured old timestamp
    (save_strategy_profile always stamps "now", so this bypasses it to simulate
    a write from `hours_old` hours in the past)."""
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
    import sqlite3
    with sqlite3.connect(daemon.logger_db.db_path) as conn:
        conn.execute(
            "INSERT INTO strategy_profile (timestamp, directional_bias, risk_profile, scout_override, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (old_ts, bias, "MODERATE", 0, "Old manual override"),
        )
        conn.commit()
    daemon.state_manager.update_strategy_profile(StrategyProfile(directional_bias=bias, reason="Old manual override"))
    # update_strategy_profile just wrote a FRESH row with "now" -- overwrite that
    # row's timestamp back to the old one so the age computation sees it as stale.
    with sqlite3.connect(daemon.logger_db.db_path) as conn:
        conn.execute(
            "UPDATE strategy_profile SET timestamp = ? WHERE id = (SELECT MAX(id) FROM strategy_profile)",
            (old_ts,),
        )
        conn.commit()


def test_alert_fires_once_when_bias_stale_beyond_threshold(tmp_path: Path):
    """Two simulated daemon cycles against the SAME stale row must result in
    exactly one message actually reaching the Telegram queue -- the second
    call to send_telegram still happens (cheap, fire-and-forget every cycle),
    but the queue's own dedupe_key + ttl_override (real, not mocked here)
    must suppress the second enqueue."""
    daemon = _make_daemon(tmp_path)
    _seed_old_profile(daemon, bias="LONG_ONLY", hours_old=13.0)

    from mms4tk.telegram_queue import TelegramQueueManager
    real_queue = TelegramQueueManager(min_interval_seconds=0.0, dedupe_ttl_seconds=60.0)
    try:
        with patch("mms4tk.telegram_queue.get_telegram_queue", return_value=real_queue):
            daemon._check_stale_directional_bias_alert()
            daemon._check_stale_directional_bias_alert()  # simulate a second cycle, same stale row

        # Exactly one message actually made it past dedupe -- the real
        # dedupe_key+ttl suppressed the second, identical alert. Checking the
        # dedupe hash table (not queue size) since the background worker
        # thread may have already dequeued the first message by now.
        assert len(real_queue._recent_hashes) == 1
    finally:
        real_queue._is_running = False


def test_no_alert_when_bias_is_both(tmp_path: Path):
    daemon = _make_daemon(tmp_path)
    _seed_old_profile(daemon, bias="BOTH", hours_old=48.0)

    with patch("mms4tk.hermes_webhook.HermesWebhookClient.send_telegram") as mock_send:
        daemon._check_stale_directional_bias_alert()
        mock_send.assert_not_called()


def test_no_alert_when_below_threshold(tmp_path: Path):
    daemon = _make_daemon(tmp_path)
    _seed_old_profile(daemon, bias="SHORT_ONLY", hours_old=1.0)

    with patch("mms4tk.hermes_webhook.HermesWebhookClient.send_telegram") as mock_send:
        daemon._check_stale_directional_bias_alert()
        mock_send.assert_not_called()


def test_configurable_threshold_via_system_settings(tmp_path: Path):
    daemon = _make_daemon(tmp_path)
    daemon.logger_db.set_setting("stale_directional_bias_alert_hours", "2.0")
    _seed_old_profile(daemon, bias="FLAT", hours_old=3.0)

    with patch("mms4tk.hermes_webhook.HermesWebhookClient.send_telegram") as mock_send:
        daemon._check_stale_directional_bias_alert()
        mock_send.assert_called_once()

"""
Tests for scripts/hermes/mms4tk_event_dispatcher.py's Telegram-routing logic.

Context: this dispatcher is a Hermes-side cron script (runs every 1 minute,
outside the antigravity_core container) that polls mms4tk's webhook_events
table and, for some event types, ALSO pushes a Telegram alert via
`hermes send --to telegram`. On 2026-09-04 it was discovered that two event
types (POSITION_OPENED, STALE_POSITION_FORCE_CLOSE) were being sent twice:
once immediately by mms4tk's own TelegramQueueManager (direct Bot API dispatch,
fixed the same day) and once again by this dispatcher's cron poll. These tests
lock in the fixed routing table so the duplication cannot silently regress.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "hermes" / "mms4tk_event_dispatcher.py"
)
spec = importlib.util.spec_from_file_location("mms4tk_event_dispatcher", MODULE_PATH)
dispatcher = importlib.util.module_from_spec(spec)
sys.modules["mms4tk_event_dispatcher"] = dispatcher
spec.loader.exec_module(dispatcher)


class TestIsActionableEvent:
    @pytest.mark.parametrize(
        "event_type,initial_status,expected",
        [
            ("SCOUT_MODE_ACTIVATED", "todo", True),
            ("HITL_REVIEW", "blocked", True),
            ("RISK_ALERT", "todo", True),
            ("STALE_POSITION_SPLIT_50", "todo", True),  # generic initial_status fallback
            ("STALE_POSITION_SPLIT_50", "in_progress", False),
            ("POSITION_OPENED", "todo", False),  # explicitly excluded even if status matches
            ("MULTI_SYMBOL_DIGEST", "todo", False),  # explicitly excluded
            ("STALE_POSITION_FORCE_CLOSE", "in_progress", False),
            ("TAKE_PROFIT_OPPOSITE_BAND", "done", False),
            ("MIDLINE_PARTIAL_TP", "done", False),
        ],
    )
    def test_actionability_matrix(self, event_type, initial_status, expected):
        assert dispatcher.is_actionable_event(event_type, initial_status) == expected


class TestShouldSendTelegramDirectly:
    """These two event types have their OWN direct Telegram channel now
    (TelegramQueueManager's Bot API dispatch) — the dispatcher must NOT also
    send them, or every trade entry / forced close alerts twice."""

    def test_position_opened_not_sent_by_dispatcher(self):
        is_actionable = dispatcher.is_actionable_event("POSITION_OPENED", "in_progress")
        assert dispatcher.should_send_telegram_directly("POSITION_OPENED", is_actionable) is False

    def test_stale_position_force_close_not_sent_by_dispatcher(self):
        is_actionable = dispatcher.is_actionable_event("STALE_POSITION_FORCE_CLOSE", "in_progress")
        assert (
            dispatcher.should_send_telegram_directly("STALE_POSITION_FORCE_CLOSE", is_actionable)
            is False
        )

    @pytest.mark.parametrize("event_type", ["SCOUT_MODE_ACTIVATED", "HITL_REVIEW", "RISK_ALERT"])
    def test_sole_channel_events_still_sent(self, event_type):
        # These have no other Telegram path — the dispatcher must remain their
        # sole channel regardless of is_actionable's own value.
        assert dispatcher.should_send_telegram_directly(event_type, is_actionable=False) is True

    def test_is_actionable_fallback_still_triggers_send(self):
        # A blocked/todo event of an otherwise-unlisted type should still alert
        # (HITL-style events not enumerated by literal name).
        assert dispatcher.should_send_telegram_directly("SOME_OTHER_TYPE", is_actionable=True) is True

    @pytest.mark.parametrize(
        "event_type",
        [
            "STALE_POSITION_SPLIT_50",
            "STALE_POSITION_BE_ESCAPE",
            "TAKE_PROFIT_OPPOSITE_BAND",
            "MIDLINE_PARTIAL_TP",
            "MULTI_SYMBOL_DIGEST",
        ],
    )
    def test_non_actionable_non_listed_events_not_sent(self, event_type):
        # Unchanged pre-existing behavior: these already have their own direct
        # send elsewhere (or, for MULTI_SYMBOL_DIGEST, their own digest cron) and
        # are not actionable, so the dispatcher correctly stays silent.
        assert dispatcher.should_send_telegram_directly(event_type, is_actionable=False) is False

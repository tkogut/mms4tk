"""
Regression test: get_addon_events() surfaced skipped/blocked add-on attempts as
if they were real fills. The old query matched any cycle_logs row where
`outcome LIKE '%ADD%' OR signal_type LIKE '%ADD%'` — since signal_type is
"HIGH_RISK_ADD_SHORT" on every re-evaluation of the same signal regardless of
outcome, a single add-on attempt that got re-checked N times per candle (and
skipped/blocked each time) produced N chart markers instead of 0 or 1.

Confirmed live in production: XAU/USDC showed 822 CANDLE_GATE_SKIP_HIGH_RISK_ADD_SHORT
and 436 BLOCKED_HIGH_RISK_ADD_LONG rows against only 3 EXECUTED_HIGH_RISK_ADD_SHORT
fills — the dashboard rendered dozens of "+ADD" markers for a single real add.

Fix: only rows whose `outcome` actually starts with "EXECUTED" qualify.
"""
import os

from mms4tk.logger_db import LoggerDB


def test_get_addon_events_excludes_skipped_and_blocked_attempts(tmp_path):
    db_file = os.path.join(tmp_path, "test_logs.db")
    db = LoggerDB(db_path=db_file)

    db.log_cycle(symbol="XAU/USDC", signal_type="HIGH_RISK_ADD_SHORT", state="BASE_MODE",
                 leverage=3.0, price=4355.0, outcome="CANDLE_GATE_SKIP_HIGH_RISK_ADD_SHORT")
    db.log_cycle(symbol="XAU/USDC", signal_type="HIGH_RISK_ADD_LONG", state="BASE_MODE",
                 leverage=3.0, price=4355.0, outcome="BLOCKED_HIGH_RISK_ADD_LONG")
    db.log_cycle(symbol="XAU/USDC", signal_type="HIGH_RISK_ADD_SHORT", state="BASE_MODE",
                 leverage=3.0, price=4355.0, outcome="SKIPPED_HIGH_RISK_ADD_SHORT")
    db.log_cycle(symbol="XAU/USDC", signal_type="HIGH_RISK_ADD_SHORT", state="BASE_MODE",
                 leverage=3.0, price=4363.5, outcome="EXECUTED_HIGH_RISK_ADD_SHORT")

    events = db.get_addon_events(symbol="XAU/USDC")

    assert len(events) == 1
    assert events[0]["price"] == 4363.5
    assert events[0]["side"] == "short"


def test_get_addon_events_returns_multiple_real_fills_in_recency_order(tmp_path):
    db_file = os.path.join(tmp_path, "test_logs.db")
    db = LoggerDB(db_path=db_file)

    db.log_cycle(symbol="XAU/USDC", signal_type="HIGH_RISK_ADD_SHORT", state="BASE_MODE",
                 leverage=3.0, price=4350.0, outcome="EXECUTED_HIGH_RISK_ADD_SHORT")
    db.log_cycle(symbol="XAU/USDC", signal_type="HIGH_RISK_ADD_SHORT", state="BASE_MODE",
                 leverage=3.0, price=4357.7, outcome="EXECUTED_HIGH_RISK_ADD_SHORT")

    events = db.get_addon_events(symbol="XAU/USDC")

    assert [e["price"] for e in events] == [4357.7, 4350.0]

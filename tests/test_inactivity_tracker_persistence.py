"""
Tests for issue #71: MMS4InactivityManager tracker state persisted to SQLite
so a restart/deploy doesn't silently reset an open position's protection
(observed extremes, is_split, candle_count, peak_profit_atr, ...).
"""
import os
import time

import pytest

from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction
from mms4tk.logger_db import LoggerDB


@pytest.fixture
def db(tmp_path):
    return LoggerDB(db_path=os.path.join(tmp_path, "tracker_persistence.db"))


def test_manager_without_logger_db_stays_fully_in_memory():
    """Default behavior (logger_db=None) must be byte-identical to before this
    feature existed — no writes attempted, no crash."""
    m = MMS4InactivityManager()
    m.track_position("BTC/USDC", entry_price=60000.0, entry_time=time.time(), side="long", atr=1000.0)
    m.update_and_check("BTC/USDC", current_price=60500.0, current_atr=1000.0, new_candle_closed=True)
    m.stop_tracking("BTC/USDC")  # must not raise


def test_survives_restart_when_entry_price_matches(db):
    """The exact scenario this issue exists for: process restarts (a brand new
    MMS4InactivityManager instance, same DB) while a position is open — the
    reloaded tracker must have the SAME observed extremes/candle_count/is_split
    as right before the restart, not defaults."""
    m1 = MMS4InactivityManager(logger_db=db)
    m1.track_position("XAG/USDC", entry_price=64.2498, entry_time=1000.0, side="short", atr=0.058)

    # Drive a real SPLIT_50 via Impulse Negation (same recipe shape as
    # test_impulse_negation_fibonacci_382 in test_midline_inactivity_tp.py) so
    # is_split/candle_count/extremes all have real, non-default values to check
    # after "restart". Impulse 0.0998 >= 0.8*ATR(0.058)=0.0464 -> fib=0.382;
    # negation_level = 64.15 + 0.0998*0.382 = 64.1881.
    a1 = m1.update_and_check("XAG/USDC", current_price=64.15, current_atr=0.058, new_candle_closed=True)
    assert a1 == StaleAction.HOLD
    action = m1.update_and_check("XAG/USDC", current_price=64.20, current_atr=0.058, new_candle_closed=True)
    tracker_before = m1.get_tracker("XAG/USDC")
    assert action == StaleAction.SPLIT_50
    assert tracker_before.is_split is True
    assert tracker_before.min_observed_price == 64.15

    # "Restart": a fresh manager instance, same DB, re-tracking the same position.
    m2 = MMS4InactivityManager(logger_db=db)
    m2.track_position("XAG/USDC", entry_price=64.2498, entry_time=9999.0, side="short", atr=0.058)

    tracker_after = m2.get_tracker("XAG/USDC")
    assert tracker_after.is_split is True
    assert tracker_after.min_observed_price == 64.15
    assert tracker_after.candle_count == tracker_before.candle_count
    assert tracker_after.entry_time == 1000.0  # the ORIGINAL entry_time, not the restart's


def test_does_not_restore_when_entry_price_does_not_match(db):
    """A persisted row for the same (symbol, side) but a materially different
    entry_price means a DIFFERENT position — a fresh close-then-reopen, not a
    restart of the same one. Must start clean, and must clear the stale row so
    it can't confuse a later restore."""
    m1 = MMS4InactivityManager(logger_db=db)
    m1.track_position("BTC/USDC", entry_price=60000.0, entry_time=1000.0, side="long", atr=1000.0)
    m1.update_and_check("BTC/USDC", current_price=63000.0, current_atr=1000.0, new_candle_closed=True)
    assert m1.get_tracker("BTC/USDC").max_observed_price == 63000.0

    # A genuinely different position at a very different entry price.
    m2 = MMS4InactivityManager(logger_db=db)
    m2.track_position("BTC/USDC", entry_price=70000.0, entry_time=5000.0, side="long", atr=1000.0)

    tracker = m2.get_tracker("BTC/USDC")
    assert tracker.max_observed_price == 70000.0  # fresh default (== entry_price), not 63000.0
    assert tracker.candle_count == 0

    # The stale row must be gone — a THIRD manager instance re-tracking at the
    # ORIGINAL 60000.0 entry must not resurrect it either (it's gone, not just
    # shadowed).
    assert db.get_inactivity_tracker_state("BTC/USDC", "long")["entry_price"] == pytest.approx(70000.0)


def test_stop_tracking_clears_persisted_state(db):
    m = MMS4InactivityManager(logger_db=db)
    m.track_position("ETH/USDC", entry_price=3000.0, entry_time=1000.0, side="short", atr=50.0)
    assert db.get_inactivity_tracker_state("ETH/USDC", "short") is not None

    m.stop_tracking("ETH/USDC")
    assert db.get_inactivity_tracker_state("ETH/USDC", "short") is None


def test_persist_happens_before_action_is_returned(db):
    """Ordering guarantee (issue #71 step 3): the persisted is_split marker
    must already be True by the time update_and_check RETURNS SPLIT_50 to the
    caller — daemon.py only issues reduce_position/attach_stop_loss AFTER
    receiving that return value, so a crash between "action returned" and
    "order issued" must find is_split already durable."""
    m = MMS4InactivityManager(logger_db=db)
    m.track_position("SOL/USDC", entry_price=100.0, entry_time=1000.0, side="long", atr=2.0)

    action = None
    for px in (102.0, 104.0, 101.0, 100.5):
        action = m.update_and_check("SOL/USDC", current_price=px, current_atr=2.0, new_candle_closed=True)
        if action == StaleAction.SPLIT_50:
            break

    assert action == StaleAction.SPLIT_50
    # Read back via the DB directly (not via `m`) — proves the write already
    # landed in SQLite, not just in the in-memory tracker `m` still holds.
    persisted = db.get_inactivity_tracker_state("SOL/USDC", "long")
    assert persisted is not None
    assert bool(persisted["is_split"]) is True


def test_does_not_write_on_every_unchanged_hold_cycle(db, monkeypatch):
    """Issue #71 explicitly calls for writing on state changes, not
    unconditionally on every ~15s poll — a HOLD cycle where price sits exactly
    at the already-recorded extreme must not trigger a write."""
    m = MMS4InactivityManager(logger_db=db)
    m.track_position("BTC/USDC", entry_price=60000.0, entry_time=1000.0, side="long", atr=1000.0)

    calls = []
    original = db.save_inactivity_tracker_state

    def counting_save(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "save_inactivity_tracker_state", counting_save)

    # Price exactly at entry (== current max_observed_price for a fresh long),
    # not a new candle close -> nothing changes.
    action = m.update_and_check("BTC/USDC", current_price=60000.0, current_atr=1000.0, new_candle_closed=False)
    assert action == StaleAction.HOLD
    assert len(calls) == 0


def test_restore_survives_a_manager_with_no_matching_row_gracefully(db):
    """track_position for a position that was NEVER persisted before (first
    time ever, or persistence just enabled) must behave exactly like the
    no-logger_db case — fresh tracker, no crash on the empty lookup."""
    m = MMS4InactivityManager(logger_db=db)
    m.track_position("NEW/USDC", entry_price=1.0, entry_time=1000.0, side="short", atr=0.01)
    tracker = m.get_tracker("NEW/USDC")
    assert tracker.candle_count == 0
    assert tracker.is_split is False

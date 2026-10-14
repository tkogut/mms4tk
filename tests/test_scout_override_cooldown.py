"""Issue #129: scout_override must not instantly re-arm full risk after a stop-loss.

Production evidence (.ai/analysis/2026-10-14-sl-loss-attribution.md §4, §6): Hermes classified a
stop-loss as SWEEP_WICK and called scout_override ~14h after entering SCOUT_MODE; three days
later BOTH BTC and ETH hit a second full-size stop-loss within two minutes of each other. A
cooldown on scout_override must block an early relax while leaving the other two exit paths
(a genuine Take-Profit, and SCOUT_EXIT_K consecutive clean closes) completely unaffected --
those bypass relax_scout_mode() entirely and must keep exiting immediately.
"""
from datetime import datetime, timedelta, timezone

import pytest

from mms4tk.state_manager import (
    SCOUT_EXIT_K,
    SCOUT_MIN_GROSS_PNL_PCT,
    SCOUT_OVERRIDE_MIN_HOURS,
    MMS4State,
    MMS4StateManager,
)

BTC, ETH = "BTC/USDC", "ETH/USDC"


def _ago(hours: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _scout_sm(symbol=ETH, entered_hours_ago: float = 0.0) -> MMS4StateManager:
    sm = MMS4StateManager(logger_db=None, trading_symbol=BTC)
    sm.set_symbol_state(symbol, MMS4State.SCOUT_MODE, reason="test: prior stop")
    if entered_hours_ago:
        sm._scout_since[symbol] = _ago(entered_hours_ago)
    return sm


def test_defaults_match_the_agreed_parameter():
    assert SCOUT_OVERRIDE_MIN_HOURS == 48.0


def test_scout_override_blocked_inside_cooldown_window():
    sm = _scout_sm(entered_hours_ago=14.0)  # the real 2026-09-18 -> 09-19 gap
    assert sm.relax_scout_mode(ETH) is False
    assert sm.get_symbol_state(ETH) == MMS4State.SCOUT_MODE


def test_scout_override_allowed_once_cooldown_elapses():
    sm = _scout_sm(entered_hours_ago=SCOUT_OVERRIDE_MIN_HOURS + 1.0)
    assert sm.relax_scout_mode(ETH) is True
    assert sm.get_symbol_state(ETH) == MMS4State.BASE_MODE


def test_scout_override_blocked_right_at_entry():
    sm = _scout_sm()  # just entered, scout_since ~= now
    assert sm.relax_scout_mode(ETH) is False
    assert sm.get_symbol_state(ETH) == MMS4State.SCOUT_MODE


def test_cooldown_bypassed_when_respect_cooldown_false():
    sm = _scout_sm(entered_hours_ago=1.0)
    assert sm.relax_scout_mode(ETH, respect_cooldown=False) is True
    assert sm.get_symbol_state(ETH) == MMS4State.BASE_MODE


def test_manual_dashboard_toggle_bypasses_cooldown():
    sm = _scout_sm(entered_hours_ago=1.0)
    sm.set_scout_mode(False, ETH)
    assert sm.get_symbol_state(ETH) == MMS4State.BASE_MODE


def test_env_zero_disables_cooldown(monkeypatch):
    # MMS4_SCOUT_OVERRIDE_MIN_HOURS=0 must behave exactly like before Issue #129.
    import importlib

    monkeypatch.setenv("MMS4_SCOUT_OVERRIDE_MIN_HOURS", "0")
    import mms4tk.state_manager as sm_mod

    importlib.reload(sm_mod)
    try:
        assert sm_mod.SCOUT_OVERRIDE_MIN_HOURS == 0.0
        sm = sm_mod.MMS4StateManager(logger_db=None, trading_symbol=BTC)
        sm.set_symbol_state(ETH, sm_mod.MMS4State.SCOUT_MODE, reason="test")
        assert sm.relax_scout_mode(ETH) is True
    finally:
        monkeypatch.delenv("MMS4_SCOUT_OVERRIDE_MIN_HOURS", raising=False)
        importlib.reload(sm_mod)


def test_take_profit_exit_ignores_cooldown_entirely():
    """on_take_profit_hit() calls set_symbol_state() directly -- it must never be gated."""
    sm = _scout_sm()  # scout_since ~= now, well inside the cooldown window
    sm.on_take_profit_hit(symbol=ETH)
    assert sm.get_symbol_state(ETH) == MMS4State.BASE_MODE


def test_missing_scout_since_fails_closed():
    """A missing _scout_since entry (shouldn't happen in production -- always set by
    set_symbol_state()/the DB restore) must block, not silently allow, the relax."""
    sm = MMS4StateManager(logger_db=None, trading_symbol=BTC)
    sm._symbol_states[ETH] = MMS4State.SCOUT_MODE  # bypass set_symbol_state on purpose
    assert ETH not in sm._scout_since
    assert sm.relax_scout_mode(ETH) is False
    assert sm.get_symbol_state(ETH) == MMS4State.SCOUT_MODE


def test_scout_exit_k_ignores_cooldown_entirely():
    """The K-consecutive-clean-closes path also bypasses relax_scout_mode() -- must stay immediate."""
    sm = _scout_sm()  # scout_since ~= now, well inside the cooldown window
    clean = SCOUT_MIN_GROSS_PNL_PCT * 2
    for _ in range(SCOUT_EXIT_K):
        sm.finalize_close(
            symbol=ETH, side="short", reason="FORCE_CLOSE", entry_price=100.0,
            exit_price=100.0 * (1 - clean), amount=1.0, pnl=clean, pnl_percent=clean, sl_percent=0.0,
        )
    assert sm.get_symbol_state(ETH) == MMS4State.BASE_MODE

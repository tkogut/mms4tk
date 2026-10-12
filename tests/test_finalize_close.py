"""`finalize_close` is the single entry point for booking a position close.

The reason -> (SL, TP, partial) table is shared by the daemon and the backtest, and an
unknown reason must raise so a new close path can never silently skip the state machine.
"""
import pytest

from mms4tk.state_manager import (
    CLOSE_REASON_FLAGS,
    MMS4State,
    MMS4StateManager,
    close_reason_flags,
)

BTC, XAG = "BTC/USDC", "XAG/USDC"


def _sm():
    return MMS4StateManager(trading_symbol=BTC)


def _close(sm, reason, symbol=XAG, pnl=1.0, sl_percent=0.0):
    return sm.finalize_close(
        symbol=symbol, side="long", reason=reason, entry_price=100.0, exit_price=101.0,
        amount=1.0, pnl=pnl, pnl_percent=0.01, sl_percent=sl_percent,
    )


def test_table_matches_the_documented_state_machine_contract():
    expected = {
        "STOP_LOSS": (True, False, False),
        "TP_AND_FLIP": (False, True, False),
        "STALE_SPLIT_50": (False, True, True),
        "MIDLINE_PARTIAL_TP": (False, True, True),
        "FORCE_CLOSE": (False, False, False),
        "FORCE_CLOSE_BE": (False, False, False),
        "BE_ESCAPE": (False, False, False),
        "D1_RANGE_TP": (False, True, False),
        "MIDLINE_FULL_TP": (False, True, False),
        "MIDLINE_INVALIDATION": (False, False, False),
        "CLOSED_EXTERNAL_TP": (False, True, False),
        "CLOSED_EXTERNAL": (False, False, False),
    }
    assert {k: (v["is_stop_loss"], v["is_take_profit"], v["is_partial"]) for k, v in CLOSE_REASON_FLAGS.items()} == expected


def test_unknown_reason_raises():
    with pytest.raises(ValueError, match="Unknown close reason"):
        close_reason_flags("SOMETHING_NEW")
    with pytest.raises(ValueError):
        _close(_sm(), "SOMETHING_NEW")


def test_flags_are_returned_as_a_copy():
    close_reason_flags("STOP_LOSS")["is_stop_loss"] = False
    assert CLOSE_REASON_FLAGS["STOP_LOSS"]["is_stop_loss"] is True


def test_stop_loss_enters_scout_for_that_symbol_only():
    sm = _sm()
    _close(sm, "STOP_LOSS", pnl=-5.0, sl_percent=0.021)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
    assert sm.get_symbol_state(BTC) == MMS4State.BASE_MODE


def test_full_take_profit_exits_scout():
    for reason in ("TP_AND_FLIP", "D1_RANGE_TP", "MIDLINE_FULL_TP", "CLOSED_EXTERNAL_TP"):
        sm = _sm()
        sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
        _close(sm, reason)
        assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE, reason


@pytest.mark.parametrize("reason", [
    "STALE_SPLIT_50", "MIDLINE_PARTIAL_TP",            # partial TP preserves SCOUT
    "FORCE_CLOSE", "FORCE_CLOSE_BE", "BE_ESCAPE",       # E2 keeps these "neither" (E3 changes it)
    "MIDLINE_INVALIDATION", "CLOSED_EXTERNAL",
])
def test_these_closes_do_not_exit_scout(reason):
    sm = _sm()
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
    _close(sm, reason, pnl=2.0)  # even on a profit
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_every_close_is_booked_in_the_ledger():
    sm = _sm()
    rec = _close(sm, "FORCE_CLOSE", pnl=0.5)
    assert sm.trade_history == [rec]
    assert rec.is_stop_loss is False and rec.is_take_profit is False
    assert sm.get_summary()["total_trades"] == 1
    assert sm.get_summary()["realized_pnl"] == 0.5


def test_default_trade_id_is_unique_per_reason_symbol_and_call():
    sm = _sm()
    a = _close(sm, "FORCE_CLOSE")
    b = _close(sm, "STALE_SPLIT_50")
    assert a.trade_id != b.trade_id


def test_full_take_profit_still_restores_scout_when_bookkeeping_fails(monkeypatch):
    """Issue #99: the exchange-side close already happened, so a failing ledger write must
    not leave the symbol stuck in SCOUT_MODE. The error still propagates."""
    sm = _sm()
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
    monkeypatch.setattr(sm, "record_trade", lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))

    with pytest.raises(RuntimeError, match="db down"):
        _close(sm, "TP_AND_FLIP")
    assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE


def test_failed_bookkeeping_does_not_restore_scout_for_non_full_tp(monkeypatch):
    sm = _sm()
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test")
    monkeypatch.setattr(sm, "record_trade", lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")))

    for reason in ("STALE_SPLIT_50", "FORCE_CLOSE", "STOP_LOSS"):
        with pytest.raises(RuntimeError):
            _close(sm, reason)
        assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE, reason

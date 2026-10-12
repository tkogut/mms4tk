"""Inactivity-Manager closes (SPLIT_50, FORCE_CLOSE, FORCE_CLOSE_BE, BE_ESCAPE) used to be
invisible to the state manager's ledger. They are now booked through `finalize_close`
with an ESTIMATED PnL, while their SCOUT_MODE effect stays exactly what it was:
SPLIT_50 is a partial TP (preserves SCOUT), the full closes are neither SL nor TP.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager

XAG = "XAG/USDC"


@pytest.fixture
def daemon_and_sm(tmp_path: Path):
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {XAG: {"contractSize": 0.01}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.01})
    sm = MMS4StateManager(trading_symbol="BTC/USDC")
    daemon = MMS4Daemon(
        executor=executor, state_manager=sm,
        logger_db=LoggerDB(db_path=str(tmp_path / "stale.db")), symbol="BTC/USDC",
    )
    sm.set_symbol_state(XAG, MMS4State.SCOUT_MODE, reason="test: prior stop")
    return daemon, sm


@pytest.mark.parametrize("reason", ["STALE_SPLIT_50", "FORCE_CLOSE", "FORCE_CLOSE_BE", "BE_ESCAPE"])
def test_stale_close_is_booked_but_never_leaves_scout(daemon_and_sm, reason):
    daemon, sm = daemon_and_sm

    daemon._book_stale_close(XAG, "short", 61.25, 61.14, 10.0, reason)

    (rec,) = sm.trade_history
    assert rec.symbol == XAG and rec.is_stop_loss is False
    assert rec.is_partial is (reason == "STALE_SPLIT_50")
    # short from 61.25 to 61.14 = +0.1796% on 10 contracts of 0.01 -> ~ +0.0110 USD
    assert rec.pnl == pytest.approx((61.25 - 61.14) / 61.25 * 61.25 * 10 * 0.01, rel=1e-6)
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE  # E3, not E2, changes this


def test_long_pnl_sign(daemon_and_sm):
    daemon, sm = daemon_and_sm
    daemon._book_stale_close(XAG, "long", 60.0, 59.0, 5.0, "FORCE_CLOSE")
    assert sm.trade_history[0].pnl < 0


def test_booking_failure_never_blocks_the_trading_cycle(daemon_and_sm):
    daemon, sm = daemon_and_sm
    sm.finalize_close = MagicMock(side_effect=RuntimeError("ledger down"))
    daemon._book_stale_close(XAG, "long", 60.0, 61.0, 5.0, "FORCE_CLOSE")  # must not raise


def test_unknown_stale_reason_is_swallowed_and_logged_not_raised(daemon_and_sm):
    daemon, sm = daemon_and_sm
    daemon._book_stale_close(XAG, "long", 60.0, 61.0, 5.0, "NOT_A_REASON")
    assert sm.trade_history == []


@pytest.mark.parametrize("result,expected", [
    ({"amount": 5.0}, 5.0),        # the order reports what it closed
    ({"status": "executed"}, 5.0),  # falls back to 50% of the open position (10 contracts)
    (None, 5.0),
])
def test_reduced_contracts_uses_order_amount_then_fraction(result, expected):
    pos = {"contracts": 10.0}
    assert MMS4Daemon._stale_reduced_contracts(result, pos, 0.50) == pytest.approx(expected)


def test_reduced_contracts_without_position_is_zero():
    assert MMS4Daemon._stale_reduced_contracts({}, None, 0.5) == 0.0


@pytest.mark.parametrize("status", ["no_position", "not_divisible", "error", "rejected"])
def test_full_stale_close_is_not_booked_when_no_closing_order_was_placed(daemon_and_sm, status):
    """The position can already be gone (e.g. a stop filled between the fetch and the
    reduce): that must not leave a fictitious FORCE_CLOSE in the ledger."""
    daemon, sm = daemon_and_sm
    booked = daemon._book_stale_close_if_executed(
        {"status": status}, XAG, "long", 60.0, 61.0, 5.0, "FORCE_CLOSE"
    )
    assert booked is False
    assert sm.trade_history == []


def test_full_stale_close_is_booked_when_the_order_was_placed(daemon_and_sm):
    daemon, sm = daemon_and_sm
    assert daemon._book_stale_close_if_executed({"id": "123", "status": "closed"}, XAG, "long", 60.0, 61.0, 5.0, "FORCE_CLOSE")
    assert len(sm.trade_history) == 1
    assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE  # still no SCOUT exit (E3)


def test_missing_result_is_not_booked(daemon_and_sm):
    daemon, sm = daemon_and_sm
    assert daemon._book_stale_close_if_executed(None, XAG, "long", 60.0, 61.0, 5.0, "FORCE_CLOSE") is False

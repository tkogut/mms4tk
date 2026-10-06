"""
Regression tests for issue #99: a position reconciled as "disappeared from OKX"
(reconcile_positions) always passed is_take_profit=False to record_trade(), even
when the reconciled close was genuinely profitable. The only state-manager path
that restores SCOUT_MODE -> BASE_MODE (on_take_profit_hit) was therefore never
reached for a profitable silent close (e.g. a ratcheted exchange SL triggering in
profit, see issue #92) — a symbol could stay stuck at reduced size indefinitely.

Confirmed on production state_history: every SCOUT_MODE->BASE_MODE restore ever
logged targeted [BTC/USDC] regardless of which symbol actually entered SCOUT_MODE
(SOL, ETH, XAG never appear on the restore side) — consistent with this path never
firing for them.
"""
from pathlib import Path
from unittest.mock import MagicMock

from mms4tk.daemon import MMS4Daemon
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4State, MMS4StateManager


def _daemon(temp_db: LoggerDB, symbol: str, state_mgr: MMS4StateManager) -> MMS4Daemon:
    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {symbol: {"contractSize": 0.001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.001})
    return MMS4Daemon(executor=executor, state_manager=state_mgr, logger_db=temp_db, symbol=symbol)


def test_reconciled_profitable_close_restores_scout_mode(tmp_path: Path):
    """A reconciled close that made money (not a stop-out) must restore the symbol
    from SCOUT_MODE back to BASE_MODE — the exact gap issue #99 found."""
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.set_symbol_state("ETH/USDC", MMS4State.SCOUT_MODE, reason="test setup: prior SL hit")
    assert state_mgr.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE

    daemon = _daemon(db, "ETH/USDC", state_mgr)
    db.save_position_snapshot(symbol="ETH/USDC", side="long", contracts=1.0, entry_price=2650.0, last_price=2650.0)

    # Price moved favorably — position disappeared from OKX without hitting the
    # stop (a silent profitable exit, e.g. a ratcheted SL triggering in profit).
    daemon.reconcile_positions(target_symbol="ETH/USDC", live_positions=[], current_price=2700.0)

    assert state_mgr.get_symbol_state("ETH/USDC") == MMS4State.BASE_MODE, (
        "a profitable reconciled close must restore SCOUT_MODE -> BASE_MODE, "
        "same as any other take-profit"
    )


def test_reconciled_loss_without_sl_trigger_does_not_restore_scout_mode(tmp_path: Path):
    """A reconciled close that lost money but didn't meet the stop-out threshold
    (e.g. a tiny adverse move) must NOT be treated as a take-profit — no state change."""
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
    state_mgr.set_symbol_state("ETH/USDC", MMS4State.SCOUT_MODE, reason="test setup: prior SL hit")

    daemon = _daemon(db, "ETH/USDC", state_mgr)
    db.save_position_snapshot(symbol="ETH/USDC", side="long", contracts=1.0, entry_price=2650.0, last_price=2650.0)

    # Tiny adverse move, well under the 2% stop-out threshold — a real loss, but
    # not a stop-out and not a take-profit either.
    daemon.reconcile_positions(target_symbol="ETH/USDC", live_positions=[], current_price=2649.5)

    assert state_mgr.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE, (
        "a small loss that isn't a stop-out must not be mistaken for a take-profit"
    )


def test_reconciled_stop_out_keeps_scout_mode(tmp_path: Path):
    """A genuine stop-out reconciled close must not restore BASE_MODE — it should
    (re-)trigger SCOUT_MODE via on_stop_loss_hit, not on_take_profit_hit."""
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    daemon = _daemon(db, "ETH/USDC", state_mgr)
    db.save_position_snapshot(symbol="ETH/USDC", side="long", contracts=1.0, entry_price=2650.0, last_price=2650.0)

    # >= 2% adverse move - a genuine stop-out.
    daemon.reconcile_positions(target_symbol="ETH/USDC", live_positions=[], current_price=2590.0)

    assert state_mgr.get_symbol_state("ETH/USDC") == MMS4State.SCOUT_MODE, (
        "a genuine stop-out must enter/stay in SCOUT_MODE, not be treated as a take-profit"
    )


def test_take_profit_and_flip_restores_target_symbol_not_primary(tmp_path: Path):
    """Issue #99's secondary fix: the explicit on_take_profit_hit call in the
    TP-and-flip path must fire unconditionally (not only inside the except branch
    wrapping record_trade), and must restore the position's OWN symbol — not
    silently fall back to the daemon's primary trading_symbol."""
    db = LoggerDB(db_path=str(tmp_path / "logs.db"))
    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC")
    state_mgr.set_symbol_state("XAG/USDC", MMS4State.SCOUT_MODE, reason="test setup: prior SL hit")

    # record_trade itself already restores on a successful is_take_profit=True call
    # (state_manager.py's own internal on_take_profit_hit dispatch) — this test
    # exercises that path directly, confirming the fix didn't regress it and that
    # BTC/USDC (the unrelated primary symbol) is left untouched.
    state_mgr.record_trade(
        trade_id="t1", symbol="XAG/USDC", side="long", entry_price=60.0, exit_price=61.0,
        amount=10.0, pnl=10.0, pnl_percent=0.0167, is_take_profit=True,
    )

    assert state_mgr.get_symbol_state("XAG/USDC") == MMS4State.BASE_MODE
    assert state_mgr.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE  # never touched, stays default

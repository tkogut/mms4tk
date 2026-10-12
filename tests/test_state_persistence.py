import os
import tempfile
import pytest
from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.state_manager import SCOUT_LEVERAGE, SCOUT_SIZE_MULT


def test_sqlite_state_save_and_restore():
    """Verify state transition is persisted in SQLite and restored 1:1 upon manager re-init."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_logs.db")
        logger_db = LoggerDB(db_path=db_path)

        # Initial state manager should default to BASE_MODE
        sm1 = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=logger_db)
        assert sm1.current_state == MMS4State.BASE_MODE
        assert sm1.leverage == 3.0

        # Trigger Stop Loss >= 2% -> transitions to SCOUT_MODE and saves to DB
        transitioned = sm1.on_stop_loss_hit(0.025)
        assert transitioned is True
        assert sm1.current_state == MMS4State.SCOUT_MODE
        # SCOUT cuts risk through SIZE now; leverage stays exchange-legal (>= 1x).
        assert sm1.leverage == SCOUT_LEVERAGE
        assert sm1.size_multiplier == SCOUT_SIZE_MULT

        # Verify DB has latest state as SCOUT_MODE
        latest_saved = logger_db.get_latest_state()
        assert latest_saved is not None
        assert latest_saved["state"] == "SCOUT_MODE"
        assert latest_saved["leverage"] == SCOUT_LEVERAGE

        # Re-initialize state manager with same logger_db -> should restore SCOUT_MODE
        sm2 = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=logger_db)
        assert sm2.current_state == MMS4State.SCOUT_MODE
        assert sm2.leverage == SCOUT_LEVERAGE
        assert sm2.size_multiplier == SCOUT_SIZE_MULT

        # Trigger Take Profit -> transitions back to BASE_MODE and saves to DB
        restored = sm2.on_take_profit_hit()
        assert restored is True
        assert sm2.current_state == MMS4State.BASE_MODE
        assert sm2.leverage == 3.0

        # Re-initialize third state manager instance -> should restore BASE_MODE
        sm3 = MMS4StateManager(initial_state=MMS4State.SCOUT_MODE, logger_db=logger_db)
        assert sm3.current_state == MMS4State.BASE_MODE
        assert sm3.leverage == 3.0


def test_set_logger_db_restores_state():
    """Verify set_logger_db attaches DB and restores saved state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_logs.db")
        logger_db = LoggerDB(db_path=db_path)
        logger_db.save_state("SCOUT_MODE", 0.1, "Manual test save")

        sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
        assert sm.current_state == MMS4State.BASE_MODE

        sm.set_logger_db(logger_db)
        assert sm.current_state == MMS4State.SCOUT_MODE
        assert sm.leverage == SCOUT_LEVERAGE
        assert sm.size_multiplier == SCOUT_SIZE_MULT


BTC, XAG = "BTC/USDC", "XAG/USDC"


def _new_db(tmpdir):
    return LoggerDB(db_path=os.path.join(tmpdir, "test_logs.db"))


def test_restart_keeps_non_primary_symbol_in_scout_without_leaking_to_primary():
    """XAG stopped out -> SCOUT. After a restart XAG must still be SCOUT and the primary
    symbol (BTC) must stay BASE. Previously only the latest row was restored and it was
    assigned to the PRIMARY symbol: XAG silently returned to BASE, BTC went to SCOUT."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _new_db(tmpdir)
        sm1 = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        assert sm1.on_stop_loss_hit(0.021, symbol=XAG) is True

        sm2 = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        assert sm2.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
        assert sm2.get_symbol_state(BTC) == MMS4State.BASE_MODE
        assert sm2.current_state == MMS4State.BASE_MODE
        assert sm2.get_symbol_size_multiplier(XAG) == SCOUT_SIZE_MULT


def test_restart_restores_latest_state_of_each_symbol_independently():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _new_db(tmpdir)
        sm1 = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        sm1.on_stop_loss_hit(0.021, symbol=BTC)
        sm1.on_stop_loss_hit(0.021, symbol=XAG)
        sm1.on_take_profit_hit(symbol=BTC)  # BTC back to BASE, XAG stays SCOUT

        sm2 = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        assert sm2.get_symbol_state(BTC) == MMS4State.BASE_MODE
        assert sm2.get_symbol_state(XAG) == MMS4State.SCOUT_MODE


def test_set_logger_db_restores_per_symbol_states():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _new_db(tmpdir)
        MMS4StateManager(logger_db=db, trading_symbol=BTC).on_stop_loss_hit(0.021, symbol=XAG)

        sm = MMS4StateManager(trading_symbol=BTC)
        sm.set_logger_db(db)
        assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
        assert sm.get_symbol_state(BTC) == MMS4State.BASE_MODE


def test_legacy_prefix_rows_are_backfilled_into_symbol_column():
    """Rows written before the `symbol` column existed carry the symbol only in the
    "[SYM]" reason prefix; opening the DB must backfill them (idempotently)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _new_db(tmpdir)
        db.save_state("SCOUT_MODE", 1.0, "[XAG/USDC] Stop Loss hit (2.1%)")  # legacy: untagged
        db = LoggerDB(db_path=os.path.join(tmpdir, "test_logs.db"))  # re-open => migration runs
        db = LoggerDB(db_path=os.path.join(tmpdir, "test_logs.db"))  # and is idempotent

        assert db.get_latest_symbol_states()[XAG]["state"] == "SCOUT_MODE"
        sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE
        assert sm.get_symbol_state(BTC) == MMS4State.BASE_MODE


def test_untagged_legacy_row_applies_to_primary_symbol_only_when_it_has_no_tagged_row():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _new_db(tmpdir)
        db.save_state("SCOUT_MODE", 1.0, "no symbol anywhere")
        db.save_state("SCOUT_MODE", 1.0, "[XAG/USDC] x", symbol=XAG)  # latest row overall is XAG's

        sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        assert sm.get_symbol_state(BTC) == MMS4State.SCOUT_MODE  # untagged legacy -> primary
        assert sm.get_symbol_state(XAG) == MMS4State.SCOUT_MODE

        db2 = _new_db(tempfile.mkdtemp())
        db2.save_state("SCOUT_MODE", 1.0, "no symbol anywhere")
        db2.save_state("BASE_MODE", 3.0, "[BTC/USDC] tp", symbol=BTC)
        sm2 = MMS4StateManager(logger_db=db2, trading_symbol=BTC)
        assert sm2.get_symbol_state(BTC) == MMS4State.BASE_MODE  # tagged row wins


def test_unknown_persisted_state_is_ignored_not_fatal():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = _new_db(tmpdir)
        db.save_state("NOT_A_STATE", 1.0, "[XAG/USDC] corrupt", symbol=XAG)
        sm = MMS4StateManager(logger_db=db, trading_symbol=BTC)
        assert sm.get_symbol_state(XAG) == MMS4State.BASE_MODE

import os
import tempfile
import pytest
from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import MMS4StateManager, MMS4State


def test_sqlite_state_save_and_restore():
    """Verify state transition is persisted in SQLite and restored 1:1 upon manager re-init."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_logs.db")
        logger_db = LoggerDB(db_path=db_path)

        # Initial state manager should default to BASE_MODE
        sm1 = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=logger_db)
        assert sm1.current_state == MMS4State.BASE_MODE
        assert sm1.leverage == 1.0

        # Trigger Stop Loss >= 2% -> transitions to SCOUT_MODE and saves to DB
        transitioned = sm1.on_stop_loss_hit(0.025)
        assert transitioned is True
        assert sm1.current_state == MMS4State.SCOUT_MODE
        assert sm1.leverage == 0.1

        # Verify DB has latest state as SCOUT_MODE
        latest_saved = logger_db.get_latest_state()
        assert latest_saved is not None
        assert latest_saved["state"] == "SCOUT_MODE"
        assert latest_saved["leverage"] == 0.1

        # Re-initialize state manager with same logger_db -> should restore SCOUT_MODE
        sm2 = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=logger_db)
        assert sm2.current_state == MMS4State.SCOUT_MODE
        assert sm2.leverage == 0.1

        # Trigger Take Profit -> transitions back to BASE_MODE and saves to DB
        restored = sm2.on_take_profit_hit()
        assert restored is True
        assert sm2.current_state == MMS4State.BASE_MODE
        assert sm2.leverage == 1.0

        # Re-initialize third state manager instance -> should restore BASE_MODE
        sm3 = MMS4StateManager(initial_state=MMS4State.SCOUT_MODE, logger_db=logger_db)
        assert sm3.current_state == MMS4State.BASE_MODE
        assert sm3.leverage == 1.0


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
        assert sm.leverage == 0.1

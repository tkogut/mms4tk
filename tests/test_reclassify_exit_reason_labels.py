"""
Tests for scripts/reclassify_exit_reason_labels.py — the migration that clears
cached BREAK-EVEN/STOP-LOSS `reason` values so LoggerDB.get_closed_trades() can
reclassify them with the 2026-10-26 fix (EXECUTED_TP_AND_FLIP mapping + price-move-
based STOP-LOSS fallback, see test_exit_reason_classification_fixes.py).
"""
import importlib.util
import os
import sqlite3
import sys

from mms4tk.logger_db import LoggerDB

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "reclassify_exit_reason_labels.py")
_spec = importlib.util.spec_from_file_location("reclassify_exit_reason_labels", _SCRIPT_PATH)
reclassify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reclassify)


def _run(argv):
    argv_backup = sys.argv
    try:
        sys.argv = ["reclassify_exit_reason_labels.py"] + argv
        return reclassify.main()
    finally:
        sys.argv = argv_backup


def _insert_cycle_log(db_file, timestamp, symbol, signal_type, outcome):
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO cycle_logs (timestamp, symbol, signal_type, state, leverage, price, outcome, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp, symbol, signal_type, "BASE_MODE", 3.0, 0.0, outcome, ""),
        )
        conn.commit()


def test_dry_run_reports_but_does_not_write(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.upsert_closed_trade(
        trade_id="t1", timestamp="2026-10-20T06:00:00+00:00", symbol="BTC-USD_UM_XPERP-310404",
        side="LONG", entry_price=80000.0, exit_price=79600.0, amount=1.0,
        pnl_usd=-1.2, pnl_pct=-0.015, is_win=False, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=-0.01, reason="STOP-LOSS",
    )

    rc = _run(["--db-path", db_file])

    assert rc == 0
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT reason FROM closed_trades WHERE trade_id='t1'").fetchone()
    assert row[0] == "STOP-LOSS", "dry-run must not modify reason"


def test_apply_reclassifies_a_wrongly_tagged_stop_loss_to_break_even(tmp_path):
    """A row mislabeled STOP-LOSS by the old margin-based fallback (0.5% price move,
    3x leverage) must become BREAK-EVEN after the migration."""
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.upsert_closed_trade(
        trade_id="small_move", timestamp="2026-10-20T06:00:00+00:00", symbol="BTC-USD_UM_XPERP-310404",
        side="LONG", entry_price=80000.0, exit_price=79600.0, amount=1.0,
        pnl_usd=-1.2, pnl_pct=-0.015, is_win=False, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=-0.01, reason="STOP-LOSS",
    )

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT reason FROM closed_trades WHERE trade_id='small_move'").fetchone()
    assert row[0] == "BREAK-EVEN"


def test_apply_reclassifies_a_hidden_tp_and_flip_from_break_even(tmp_path):
    """A real EXECUTED_TP_AND_FLIP close mislabeled BREAK-EVEN must become
    TAKE-PROFIT after the migration."""
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    trade_ts = "2026-10-20T06:00:10+00:00"
    db.upsert_closed_trade(
        trade_id="hidden_tp", timestamp=trade_ts, symbol="ETH-USD_UM_XPERP-310404",
        side="SHORT", entry_price=2500.0, exit_price=2480.0, amount=10.0,
        pnl_usd=0.20, pnl_pct=0.008, is_win=True, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=0.03, reason="BREAK-EVEN",
    )
    _insert_cycle_log(db_file, trade_ts, "ETH/USDC", "BASE_LONG", "EXECUTED_TP_AND_FLIP_BASE_LONG")

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT reason FROM closed_trades WHERE trade_id='hidden_tp'").fetchone()
    assert row[0] == "TAKE-PROFIT"


def test_apply_leaves_a_correctly_tagged_stale_close_untouched(tmp_path):
    """A row tagged by an unrelated, correctly-matched mechanism (not the PnL
    fallback) is out of scope for this migration's candidate filter and must be
    skipped entirely."""
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.upsert_closed_trade(
        trade_id="split_trade", timestamp="2026-10-20T06:00:00+00:00", symbol="ETH-USD_UM_XPERP-310404",
        side="SHORT", entry_price=2700.0, exit_price=2699.0, amount=1.0,
        pnl_usd=0.05, pnl_pct=0.0003, is_win=True, leverage=3.0,
        fee_usd=0.0, pnl_net_usd=0.05, reason="STALE_SPLIT_50",
    )

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0
    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT reason FROM closed_trades WHERE trade_id='split_trade'").fetchone()
    assert row[0] == "STALE_SPLIT_50"

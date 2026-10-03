"""
Tests for scripts/reclassify_split_50_reason.py — the one-time migration that
clears the cached `reason` on STALE_SPLIT_50/MIDLINE_PARTIAL_TP rows so
LoggerDB.get_closed_trades() reclassifies them with the fixed matcher (PR #93,
issue #91) instead of keeping a stale pre-fix value forever.
"""
import importlib.util
import os
import sqlite3
import sys

from mms4tk.logger_db import LoggerDB

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "reclassify_split_50_reason.py")
_spec = importlib.util.spec_from_file_location("reclassify_split_50_reason", _SCRIPT_PATH)
reclassify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reclassify)


def _insert_cycle_log(db_file, timestamp, symbol, signal_type, outcome, details=""):
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO cycle_logs (timestamp, symbol, signal_type, state, leverage, price, outcome, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp, symbol, signal_type, "BASE_MODE", 3.0, 0.0, outcome, details),
        )
        conn.commit()


def test_dry_run_reports_but_does_not_write(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.upsert_closed_trade(
        trade_id="leg_1", timestamp="2026-10-02T14:11:00+00:00", symbol="XAU-USD_UM_XPERP-310502",
        side="SHORT", entry_price=4192.0, exit_price=4188.70, amount=25.0,
        pnl_usd=0.08, pnl_pct=-0.0006, is_win=False, leverage=3.0,
        fee_usd=0.10, pnl_net_usd=-0.02, reason="STALE_SPLIT_50",
    )

    argv_backup = sys.argv
    try:
        sys.argv = ["reclassify_split_50_reason.py", "--db-path", db_file]
        reclassify.main()
    finally:
        sys.argv = argv_backup

    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT reason FROM closed_trades WHERE trade_id='leg_1'").fetchone()
    assert row[0] == "STALE_SPLIT_50", "dry-run must not modify reason"


def test_apply_reclassifies_misattributed_leg_and_keeps_genuine_split(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)

    # The real production incident shape (issue #91): a STALE_SPLIT_50 cycle_logs
    # row at 14:11:02, and two closed_trades rows — the split's own fill (correctly
    # tagged) and an unrelated SL execution 2m21s later (incorrectly cached with the
    # same stale reason, pre-fix).
    _insert_cycle_log(
        db_file, "2026-10-02T14:11:02+00:00", "XAU/USDC", "STALE_SPLIT_50", "STALE_SPLIT_50",
        "Inactivity Manager: Split 50% of SHORT position for XAU/USDC after 1 candles stagnation.",
    )
    db.upsert_closed_trade(
        trade_id="leg_1", timestamp="2026-10-02T14:11:00+00:00", symbol="XAU-USD_UM_XPERP-310502",
        side="SHORT", entry_price=4192.0, exit_price=4188.70, amount=25.0,
        pnl_usd=0.0825, pnl_pct=-0.0006, is_win=False, leverage=3.0,
        fee_usd=0.1047, pnl_net_usd=-0.0222, reason="STALE_SPLIT_50",
    )
    db.upsert_closed_trade(
        trade_id="leg_2", timestamp="2026-10-02T14:13:23+00:00", symbol="XAU-USD_UM_XPERP-310502",
        side="SHORT", entry_price=4192.0, exit_price=4189.296, amount=25.0,
        pnl_usd=0.0676, pnl_pct=-0.0011, is_win=False, leverage=3.0,
        fee_usd=0.1047, pnl_net_usd=-0.0372, reason="STALE_SPLIT_50",
    )

    # A different symbol's genuine, correctly-matched STALE_SPLIT_50 must be left
    # as STALE_SPLIT_50 after reclassification (it has its own close cycle_logs row).
    _insert_cycle_log(
        db_file, "2026-09-30T14:02:58+00:00", "ETH/USDC", "STALE_SPLIT_50", "STALE_SPLIT_50",
        "Inactivity Manager: Split 50% of SHORT position for ETH/USDC after 1 candles stagnation.",
    )
    db.upsert_closed_trade(
        trade_id="eth_split", timestamp="2026-09-30T14:03:00+00:00", symbol="ETH-USD_UM_XPERP-310404",
        side="SHORT", entry_price=2700.0, exit_price=2699.0, amount=1.0,
        pnl_usd=0.05, pnl_pct=0.0003, is_win=True, leverage=3.0,
        fee_usd=0.0, pnl_net_usd=0.05, reason="STALE_SPLIT_50",
    )

    argv_backup = sys.argv
    try:
        sys.argv = ["reclassify_split_50_reason.py", "--db-path", db_file, "--apply"]
        reclassify.main()
    finally:
        sys.argv = argv_backup

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["trade_id"]: r for r in conn.execute("SELECT trade_id, reason FROM closed_trades")}

    assert rows["leg_1"]["reason"] == "STALE_SPLIT_50", "the split's own fill keeps its correct reason"
    assert rows["leg_2"]["reason"] != "STALE_SPLIT_50", (
        "the unrelated SL execution 2m21s later must no longer inherit the split's reason"
    )
    assert rows["eth_split"]["reason"] == "STALE_SPLIT_50", "a genuinely-matched split must be unaffected"

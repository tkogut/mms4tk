"""
Tests for scripts/reclassify_break_even_reason.py — the one-time migration that
recomputes pnl_pct (ctVal-scaled) for historical closed_trades rows persisted
before the trade_sync.py fix, and reclassifies mislabeled "BREAK-EVEN" rows.
"""
import importlib.util
import os
import sqlite3
import sys

import pytest

from mms4tk.logger_db import LoggerDB

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "reclassify_break_even_reason.py")
_spec = importlib.util.spec_from_file_location("reclassify_break_even_reason", _SCRIPT_PATH)
reclassify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reclassify)


def _seed(db: LoggerDB, trade_id: str, symbol: str, side: str, entry: float, exit_: float,
          amount: float, pnl_net: float, old_pnl_pct: float, leverage: float = 3.0) -> None:
    db.upsert_closed_trade(
        trade_id=trade_id, timestamp="2026-09-20T14:00:00+00:00", symbol=symbol, side=side,
        entry_price=entry, exit_price=exit_, amount=amount, pnl_usd=pnl_net, pnl_pct=old_pnl_pct,
        is_win=pnl_net > 0, leverage=leverage, fee_usd=0.02, pnl_net_usd=pnl_net, reason="BREAK-EVEN",
    )


def test_get_contract_size_matches_daemon_fallback_table():
    assert reclassify.get_contract_size("BTC-USD_UM_XPERP-310404") == 0.0001
    assert reclassify.get_contract_size("ETH/USDC") == 0.001
    assert reclassify.get_contract_size("XAU/USDC") == 0.001
    assert reclassify.get_contract_size("XAG/USDC") == 0.01
    assert reclassify.get_contract_size("SOL/USDC") == 1.0


def test_dry_run_reports_but_does_not_write(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    _seed(db, "ord_btc_sl", "BTC-USD_UM_XPERP-310404", "SHORT", 77429.0, 78984.6, 19.0, -3.02, -0.000006)

    argv_backup = sys.argv
    try:
        sys.argv = ["reclassify_break_even_reason.py", "--db-path", db_file]
        reclassify.main()
    finally:
        sys.argv = argv_backup

    with sqlite3.connect(db_file) as conn:
        row = conn.execute("SELECT pnl_pct, reason FROM closed_trades WHERE trade_id='ord_btc_sl'").fetchone()
    assert row[1] == "BREAK-EVEN", "dry-run must not modify reason"
    assert row[0] == pytest.approx(-0.000006, rel=1e-3), "dry-run must not modify pnl_pct"


def test_apply_reclassifies_real_stop_loss_and_leaves_genuine_break_even(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    _seed(db, "ord_btc_sl", "BTC-USD_UM_XPERP-310404", "SHORT", 77429.0, 78984.6, 19.0, -3.02, -0.000006)
    _seed(db, "ord_genuine_be", "ETH-USD_UM_XPERP-310404", "LONG", 2650.0, 2650.3, 1.0, 0.01, 0.000001)
    # A TAKE-PROFIT row must never be touched — only reason='BREAK-EVEN' rows are selected.
    db.upsert_closed_trade(
        trade_id="ord_tp", timestamp="2026-09-20T14:00:00+00:00", symbol="XAU/USDC", side="LONG",
        entry_price=3000.0, exit_price=3010.0, amount=1.0, pnl_usd=0.5, pnl_pct=0.1,
        is_win=True, leverage=3.0, fee_usd=0.0, pnl_net_usd=0.5, reason="TAKE-PROFIT",
    )

    argv_backup = sys.argv
    try:
        sys.argv = ["reclassify_break_even_reason.py", "--db-path", db_file, "--apply"]
        reclassify.main()
    finally:
        sys.argv = argv_backup

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["trade_id"]: r for r in conn.execute("SELECT trade_id, pnl_pct, reason FROM closed_trades")}

    assert rows["ord_btc_sl"]["reason"] == "STOP-LOSS"
    assert rows["ord_btc_sl"]["pnl_pct"] < -0.015
    assert rows["ord_genuine_be"]["reason"] == "BREAK-EVEN"
    assert rows["ord_tp"]["reason"] == "TAKE-PROFIT"
    assert rows["ord_tp"]["pnl_pct"] == pytest.approx(0.1), "non-BREAK-EVEN rows must be untouched"

"""Tests for scripts/fix_btc_timeframe.py — the one-time correction for the
2026-10-27 production incident (BTC/USDC's `symbol_configurations.timeframe`
silently reset to `15m`), mirroring tests/test_bump_data_collection_epoch.py's
harness."""
import importlib.util
import os
import sys

from mms4tk.logger_db import LoggerDB

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "fix_btc_timeframe.py")
_spec = importlib.util.spec_from_file_location("fix_btc_timeframe", _SCRIPT_PATH)
fix_btc_timeframe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fix_btc_timeframe)


def _run(argv):
    argv_backup = sys.argv
    try:
        sys.argv = ["fix_btc_timeframe.py"] + argv
        return fix_btc_timeframe.main()
    finally:
        sys.argv = argv_backup


def test_dry_run_reports_but_does_not_write(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.save_symbol_config(
        symbol="BTC/USDC", is_active=True, allocation_usd=50.0,
        base_ratio=70, add_ratio=30, timeframe="15m",
    )

    rc = _run(["--db-path", db_file])

    assert rc == 0
    cfg = db.get_all_symbol_configs()["BTC/USDC"]
    assert cfg["timeframe"] == "15m"  # unchanged — dry run only


def test_apply_writes_the_correction(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.save_symbol_config(
        symbol="BTC/USDC", is_active=True, allocation_usd=50.0,
        base_ratio=70, add_ratio=30, timeframe="15m",
    )

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0
    cfg = db.get_all_symbol_configs()["BTC/USDC"]
    assert cfg["timeframe"] == "1h"
    # Every other field preserved untouched.
    assert cfg["is_active"] is True
    assert cfg["allocation_usd"] == 50.0
    assert cfg["base_ratio"] == 70
    assert cfg["add_ratio"] == 30


def test_already_correct_is_a_no_op(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.save_symbol_config(
        symbol="BTC/USDC", is_active=True, allocation_usd=50.0,
        base_ratio=70, add_ratio=30, timeframe="1h",
    )

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0
    cfg = db.get_all_symbol_configs()["BTC/USDC"]
    assert cfg["timeframe"] == "1h"


def test_other_symbols_are_never_touched(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.save_symbol_config(
        symbol="BTC/USDC", is_active=True, allocation_usd=50.0,
        base_ratio=70, add_ratio=30, timeframe="15m",
    )
    db.save_symbol_config(
        symbol="ETH/USDC", is_active=True, allocation_usd=50.0,
        base_ratio=70, add_ratio=30, timeframe="15m",  # deliberately also "wrong" — must stay untouched
    )

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0
    configs = db.get_all_symbol_configs()
    assert configs["BTC/USDC"]["timeframe"] == "1h"
    assert configs["ETH/USDC"]["timeframe"] == "15m"  # untouched — script is BTC/USDC-only


def test_missing_row_is_a_clean_no_op(tmp_path):
    db_file = str(tmp_path / "logs.db")
    LoggerDB(db_path=db_file)

    rc = _run(["--db-path", db_file, "--apply"])

    assert rc == 0

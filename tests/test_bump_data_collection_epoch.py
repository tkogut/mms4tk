"""Tests for scripts/bump_data_collection_epoch.py — the auditable replacement for
ad hoc `UPDATE system_settings ...` SQL used for past `data_collection_epoch_start`
bumps (PLAN-008/PLAN-010 history, `.agents/MEMORY.md`)."""
import importlib.util
import os
import sys

import pytest

from mms4tk.logger_db import LoggerDB

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "bump_data_collection_epoch.py")
_spec = importlib.util.spec_from_file_location("bump_data_collection_epoch", _SCRIPT_PATH)
bump_epoch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_epoch)


def _run(argv):
    argv_backup = sys.argv
    try:
        sys.argv = ["bump_data_collection_epoch.py"] + argv
        return bump_epoch.main()
    finally:
        sys.argv = argv_backup


def test_dry_run_reports_but_does_not_write(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    db.set_setting("data_collection_epoch_start:BTC/USDC", "2026-09-28T20:39:00+00:00")

    rc = _run(["--db-path", db_file, "--symbol", "BTC/USDC", "--at", "2026-10-25T21:00:00+00:00"])

    assert rc == 0
    assert db.get_setting("data_collection_epoch_start:BTC/USDC") == "2026-09-28T20:39:00+00:00"


def test_apply_sets_the_new_epoch_for_every_symbol(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)

    rc = _run([
        "--db-path", db_file,
        "--symbol", "BTC/USDC", "--symbol", "ETH/USDC",
        "--at", "2026-10-25T21:00:00+00:00",
        "--apply", "--updated-by", "test_suite",
    ])

    assert rc == 0
    assert db.get_setting("data_collection_epoch_start:BTC/USDC") == "2026-10-25T21:00:00+00:00"
    assert db.get_setting("data_collection_epoch_start:ETH/USDC") == "2026-10-25T21:00:00+00:00"
    assert db.count_clean_closed_trades("BTC/USDC") == 0


def test_defaults_to_now_when_at_is_omitted(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)

    rc = _run(["--db-path", db_file, "--symbol", "XAU/USDC", "--apply"])

    assert rc == 0
    value = db.get_setting("data_collection_epoch_start:XAU/USDC")
    assert value is not None and "T" in value  # ISO8601 date/time separator


def test_rejects_an_invalid_timestamp(tmp_path, capsys):
    db_file = str(tmp_path / "logs.db")
    LoggerDB(db_path=db_file)

    rc = _run(["--db-path", db_file, "--symbol", "BTC/USDC", "--at", "not-a-timestamp"])

    assert rc == 2
    assert "not a valid ISO8601" in capsys.readouterr().err

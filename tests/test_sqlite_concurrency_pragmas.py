"""
Issue #72: every connection to logs.db must enable WAL mode and set a real
busy_timeout, since the file is opened concurrently by LoggerDB
(antigravity_core), hermes_webhook.py, and ui_dashboard/app.py.
"""
import os

from mms4tk.logger_db import LoggerDB
from mms4tk.hermes_webhook import _connect as hermes_webhook_connect


def test_logger_db_connection_uses_wal_and_busy_timeout(tmp_path):
    db = LoggerDB(db_path=os.path.join(tmp_path, "pragmas.db"))
    with db._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert journal_mode.lower() == "wal"
    assert busy_timeout == LoggerDB._BUSY_TIMEOUT_MS


def test_hermes_webhook_connect_uses_wal_and_busy_timeout(tmp_path):
    db_path = os.path.join(tmp_path, "webhook_pragmas.db")
    with hermes_webhook_connect(db_path) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert journal_mode.lower() == "wal"
    assert busy_timeout == 5000


def test_wal_mode_persists_for_the_db_file_across_connections(tmp_path):
    """journal_mode=WAL is a property of the DATABASE FILE, not the
    connection — once set, a later connection (even one that doesn't
    explicitly ask) should still see it, but every connection should still be
    setting busy_timeout for ITSELF (that one IS per-connection)."""
    db_path = os.path.join(tmp_path, "persist.db")
    db1 = LoggerDB(db_path=db_path)
    with db1._get_connection() as conn:
        pass  # _init_db already ran in __init__, connection already set WAL

    import sqlite3
    plain_conn = sqlite3.connect(db_path)
    try:
        journal_mode = plain_conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
    finally:
        plain_conn.close()

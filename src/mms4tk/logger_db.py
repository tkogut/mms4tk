import os
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("mms4tk.logger_db")

DEFAULT_DB_PATH = ".agents/swarm/logs.db"


class LoggerDB:
    """
    SQLite database logger for MMS4 strategy cycle events and state metrics.
    Persists data to .agents/swarm/logs.db for auditability and R-SEC-01 compliance.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize database schema if tables do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cycle_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    leverage REAL NOT NULL,
                    price REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    details TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS state_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    state TEXT NOT NULL,
                    leverage REAL NOT NULL,
                    equity REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    trade_count INTEGER NOT NULL
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    state TEXT NOT NULL,
                    leverage REAL NOT NULL,
                    reason TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS strategy_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    directional_bias TEXT NOT NULL,
                    risk_profile TEXT NOT NULL,
                    scout_override INTEGER NOT NULL,
                    reason TEXT
                )
            """)
            conn.commit()

    def log_cycle(
        self,
        symbol: str,
        signal_type: str,
        state: str,
        leverage: float,
        price: float,
        outcome: str,
        details: str = ""
    ) -> int:
        """Log outcome of a daemon execution cycle."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO cycle_logs (timestamp, symbol, signal_type, state, leverage, price, outcome, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (timestamp, symbol, signal_type, state, leverage, price, outcome, details)
            )
            conn.commit()
            return cursor.lastrowid or 0

    def log_state_metrics(
        self,
        state: str,
        leverage: float,
        equity: float,
        realized_pnl: float,
        trade_count: int
    ) -> int:
        """Log state machine equity & leverage metrics."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO state_logs (timestamp, state, leverage, equity, realized_pnl, trade_count)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (timestamp, state, leverage, equity, realized_pnl, trade_count)
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_recent_cycle_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent cycle log entries."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM cycle_logs ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def get_recent_state_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve recent state metric log entries."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM state_logs ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    def save_state(self, state: str, leverage: float, reason: str = "") -> int:
        """Save state transition or snapshot to state_history table."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO state_history (timestamp, state, leverage, reason)
                VALUES (?, ?, ?, ?)
                """,
                (timestamp, state, leverage, reason)
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_latest_state(self) -> Optional[Dict[str, Any]]:
        """Retrieve the latest state record from state_history table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM state_history ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def save_strategy_profile(
        self,
        directional_bias: str,
        risk_profile: str,
        scout_override: bool,
        reason: str = ""
    ) -> int:
        """Save strategy profile record to strategy_profile table."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO strategy_profile (timestamp, directional_bias, risk_profile, scout_override, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, directional_bias, risk_profile, 1 if scout_override else 0, reason)
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_latest_strategy_profile(self) -> Optional[Dict[str, Any]]:
        """Retrieve the latest strategy profile record from strategy_profile table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM strategy_profile ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["scout_override"] = bool(d["scout_override"])
                return d
            return None


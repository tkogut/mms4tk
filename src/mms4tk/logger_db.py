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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS closed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT UNIQUE NOT NULL,
                    timestamp TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    amount REAL NOT NULL,
                    pnl_usd REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    is_win INTEGER NOT NULL,
                    leverage REAL NOT NULL DEFAULT 1.0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS symbol_configurations (
                    symbol TEXT PRIMARY KEY,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    allocation_usd REAL NOT NULL DEFAULT 10.0,
                    base_ratio INTEGER NOT NULL DEFAULT 70,
                    add_ratio INTEGER NOT NULL DEFAULT 30,
                    timeframe TEXT NOT NULL DEFAULT '15m',
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def upsert_closed_trade(
        self,
        trade_id: str,
        timestamp: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        amount: float,
        pnl_usd: float,
        pnl_pct: float,
        is_win: bool,
        leverage: float = 1.0
    ) -> None:
        """Upsert a closed trade into closed_trades table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO closed_trades (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, is_win, leverage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    exit_price=excluded.exit_price,
                    pnl_usd=excluded.pnl_usd,
                    pnl_pct=excluded.pnl_pct,
                    is_win=excluded.is_win
                """,
                (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, 1 if is_win else 0, leverage)
            )
            conn.commit()

    def get_closed_trades(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Fetch historical closed trades sorted by timestamp."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM closed_trades ORDER BY timestamp ASC LIMIT ?", (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_kpi_summary(self) -> Dict[str, Any]:
        """Compute KPI summary (Win Rate, Profit Factor, MDD %, Average R:R) from closed_trades."""
        trades = self.get_closed_trades(limit=1000)
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "win_rate_long": 0.0,
                "win_rate_short": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "average_rr": 0.0,
                "total_pnl_usd": 0.0,
            }

        # Filter out zero-PnL entry fills to get actual closed round-trip trades with realized PnL
        closed_pnl_trades = [t for t in trades if abs(t.get("pnl_usd", 0.0)) > 0.0001]
        total_pnl_count = len(closed_pnl_trades)

        wins = [t for t in closed_pnl_trades if t.get("is_win") == 1 or t.get("pnl_usd", 0.0) > 0]
        losses = [t for t in closed_pnl_trades if t.get("is_win") == 0 and t.get("pnl_usd", 0.0) < 0]
        
        longs = [t for t in closed_pnl_trades if t["side"].upper() in ["BUY", "LONG"]]
        shorts = [t for t in closed_pnl_trades if t["side"].upper() in ["SELL", "SHORT"]]

        win_rate = len(wins) / total_pnl_count if total_pnl_count > 0 else 0.0
        
        long_wins = [t for t in longs if t.get("is_win") == 1 or t.get("pnl_usd", 0.0) > 0]
        short_wins = [t for t in shorts if t.get("is_win") == 1 or t.get("pnl_usd", 0.0) > 0]

        win_rate_long = len(long_wins) / len(longs) if longs else 0.0
        win_rate_short = len(short_wins) / len(shorts) if shorts else 0.0

        gross_profit = sum(t["pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["pnl_usd"] for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0
        average_rr = round(avg_win / avg_loss, 2) if avg_loss > 0 else (99.0 if avg_win > 0 else 0.0)

        # Calculate Max Drawdown % (Peak-to-Trough)
        cumulative_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            cumulative_pnl += t["pnl_usd"]
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

        return {
            "total_trades": total_pnl_count,
            "win_rate": round(win_rate * 100, 1),
            "win_rate_long": round(win_rate_long * 100, 1),
            "win_rate_short": round(win_rate_short * 100, 1),
            "profit_factor": profit_factor,
            "max_drawdown": round(max_dd, 2),
            "average_rr": average_rr,
            "total_pnl_usd": round(sum(t["pnl_usd"] for t in trades), 2),
        }

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

    def save_symbol_config(
        self,
        symbol: str,
        is_active: bool,
        allocation_usd: float,
        base_ratio: int,
        add_ratio: int,
        timeframe: str
    ) -> None:
        """Upsert per-symbol configuration into SQLite symbol_configurations table."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO symbol_configurations (symbol, is_active, allocation_usd, base_ratio, add_ratio, timeframe, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    is_active = excluded.is_active,
                    allocation_usd = excluded.allocation_usd,
                    base_ratio = excluded.base_ratio,
                    add_ratio = excluded.add_ratio,
                    timeframe = excluded.timeframe,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    1 if is_active else 0,
                    float(allocation_usd),
                    int(base_ratio),
                    int(add_ratio),
                    str(timeframe),
                    timestamp
                )
            )
            conn.commit()

    def get_all_symbol_configs(self) -> Dict[str, Dict[str, Any]]:
        """Retrieve all per-symbol configurations from SQLite symbol_configurations table."""
        result = {}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM symbol_configurations ORDER BY symbol ASC")
            for row in cursor.fetchall():
                d = dict(row)
                result[d["symbol"]] = {
                    "is_active": bool(d["is_active"]),
                    "allocation_usd": float(d["allocation_usd"]),
                    "base_ratio": int(d["base_ratio"]),
                    "add_ratio": int(d["add_ratio"]),
                    "timeframe": str(d["timeframe"])
                }
        return result


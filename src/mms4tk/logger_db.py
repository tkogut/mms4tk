import json
import os
import sqlite3
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("mms4tk.logger_db")

def _default_db_path() -> str:
    """
    Resolve the logs.db location.

    Explicit MMS4_DB_PATH wins. Otherwise anchor on the package installation root
    rather than the process CWD, so the daemon and the API always open the same file
    no matter where they were started from (audit finding P2-32).
    """
    env_path = os.getenv("MMS4_DB_PATH")
    if env_path:
        return env_path
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(pkg_root, ".agents", "swarm", "logs.db")


DEFAULT_DB_PATH = _default_db_path()


def _timestamp_at_or_after(timestamp: Any, since: str) -> bool:
    """True when `timestamp` is at or after `since` (both ISO8601).

    Parsed as datetimes rather than compared as strings, because the two sides come
    from different writers and are not guaranteed to share a format: `closed_trades`
    rows carry microseconds and an explicit offset ("...T08:00:14.769000+00:00")
    while an operator-set `data_collection_epoch_start` is typically minute-precision
    ("...T16:00:00+00:00"), and a bare-Z or naive value is possible from either.
    A naive value is read as UTC — every writer in this system records UTC.

    Falls back to lexicographic comparison (the convention used by
    `count_clean_closed_trades`) if either side is unparseable, so a malformed row
    degrades to the old behaviour rather than raising inside a KPI read.
    """
    raw = "" if timestamp is None else str(timestamp)
    try:
        left = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return raw >= str(since)
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return left >= right


class LoggerDB:
    """
    SQLite database logger for MMS4 strategy cycle events and state metrics.
    Persists data to .agents/swarm/logs.db for auditability and R-SEC-01 compliance.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._last_state_metrics: Optional[tuple] = None
        self._last_state_metrics_ts: float = 0.0
        self._ensure_dir()
        self._init_db()

    def _ensure_dir(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    # Issue #72: default SQLite journal mode allows only one writer at a time
    # and has no wait window of its own — under concurrent access (this class
    # from antigravity_core, plus ui_dashboard/app.py opening the same file
    # directly) that surfaces as "database is locked" instead of a short,
    # automatic wait. WAL lets readers and the writer proceed concurrently;
    # busy_timeout gives any operation that still contends a real wait window
    # before erroring out.
    _BUSY_TIMEOUT_MS = 5000

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
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
            # SCOUT_MODE is per-symbol, but state_history only ever carried the symbol
            # inside the free-text `reason` ("[XAG/USDC] ..."), so a restart could only
            # rebuild ONE state and assigned it to the primary symbol. A real column makes
            # the latest state of every symbol recoverable. Legacy rows are backfilled
            # from that "[SYM]" prefix; rows with no prefix stay NULL (= primary symbol).
            # The backfill runs ONLY when this call adds the column: LoggerDB() is built on
            # every Streamlit rerun, and an unindexed full-table UPDATE (plus its write
            # lock) on each of those would contend with the daemon for nothing.
            cursor.execute("PRAGMA table_info(state_history)")
            if "symbol" not in {row[1] for row in cursor.fetchall()}:
                cursor.execute("ALTER TABLE state_history ADD COLUMN symbol TEXT")
                # Only a "[BASE/QUOTE]" prefix counts (a "/" inside the brackets) so a
                # non-symbol tag such as "[HITL] ..." is never mistaken for a symbol.
                cursor.execute("""
                    UPDATE state_history
                    SET symbol = substr(reason, 2, instr(reason, ']') - 2)
                    WHERE reason LIKE '[%/%]%'
                      AND instr(reason, ']') > 2
                      AND instr(reason, '/') BETWEEN 3 AND instr(reason, ']') - 2
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
                    leverage REAL NOT NULL DEFAULT 1.0,
                    fee_usd REAL NOT NULL DEFAULT 0.0,
                    pnl_net_usd REAL NOT NULL DEFAULT 0.0
                )
            """)
            # Auto-migrate existing closed_trades schema if fee_usd or pnl_net_usd missing
            cursor.execute("PRAGMA table_info(closed_trades)")
            columns = {row[1] for row in cursor.fetchall()}
            if "fee_usd" not in columns:
                cursor.execute("ALTER TABLE closed_trades ADD COLUMN fee_usd REAL NOT NULL DEFAULT 0.0")
            if "pnl_net_usd" not in columns:
                cursor.execute("ALTER TABLE closed_trades ADD COLUMN pnl_net_usd REAL NOT NULL DEFAULT 0.0")
            # 2026-09-17 incident fix: get_closed_trades() derives `reason` and
            # `entry_timestamp` from a per-row cycle_logs lookup (LIKE-pattern scan,
            # unindexable) whenever they're missing — closed_trades never had these
            # columns, so EVERY row was a cache miss on EVERY call. GET
            # /api/v1/history/trades is polled every 5s by the dashboard, so this was
            # re-running an unindexed full-table-ish scan of cycle_logs for up to 100
            # rows every 5 seconds, permanently — confirmed live via strace on the
            # production process (100% CPU, ~20k pread64/s against logs.db). These two
            # columns let get_closed_trades() persist the computed value once and skip
            # the lookup on every subsequent call.
            if "reason" not in columns:
                cursor.execute("ALTER TABLE closed_trades ADD COLUMN reason TEXT")
            if "entry_timestamp" not in columns:
                cursor.execute("ALTER TABLE closed_trades ADD COLUMN entry_timestamp TEXT")
            # #124: raw OKX orders, kept so a closed trade's opening leg (and its fee) survives
            # after the order drops out of the ~100-order window the OKX history endpoints
            # return. Append/upsert only — nothing ever deletes from this table.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS okx_orders (
                    ord_id TEXT PRIMARY KEY,
                    inst_id TEXT,
                    side TEXT,
                    pos_side TEXT,
                    state TEXT,
                    fill_time INTEGER NOT NULL DEFAULT 0,
                    fee REAL NOT NULL DEFAULT 0.0,
                    pnl REAL NOT NULL DEFAULT 0.0,
                    updated_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_okx_orders_fill_time ON okx_orders(fill_time)")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS position_addons (
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    addon_count INTEGER NOT NULL DEFAULT 0,
                    position_key TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, side)
                )
            """)
            # NOTE: an earlier iteration of P3 (rounds 1-2, superseded by V2 —
            # base_entry_price in position_snapshots is the SSOT now) added a
            # `sl_trigger_price` column here to persist a per-addon composite SL
            # trigger price. Removed along with its read/write methods
            # (2026-09-10 cleanup) since V2 no longer gives the addon leg an
            # independent SL to persist. A database that already has this column
            # (any DB that ran the P3 V2 deploy) keeps it — SQLite doesn't need a
            # migration to ignore an extra column nothing reads anymore.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    contracts REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    last_price REAL NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, side)
                )
            """)
            # P3 V2 (2026-09-10): the ORIGINAL base entry price, pinned for the whole
            # life of a position and never moved by addon averaging — see
            # save_position_snapshot's docstring for why this replaces the addon
            # composite-SL mechanism as the SSOT for position SL.
            cursor.execute("PRAGMA table_info(position_snapshots)")
            snap_columns = {row[1] for row in cursor.fetchall()}
            if "base_entry_price" not in snap_columns:
                cursor.execute(
                    "ALTER TABLE position_snapshots ADD COLUMN base_entry_price REAL NOT NULL DEFAULT 0.0"
                )
            # Issue #71: persist InactivityManager's per-position tracker state
            # (candle_count, observed price extremes, is_split, etc.) — previously
            # in-memory only (`MMS4InactivityManager._trackers`), so any restart or
            # deploy while a position was open silently reset its protection (the
            # 2026-09-28 XAG incident's contributing factors, project memory
            # xag-protection-gaps-audit). PK (symbol, side) mirrors
            # position_snapshots; `entry_price` is used at restore time to confirm
            # a reloaded row still belongs to the SAME live position, not a stale
            # row from a different instance that happened to reuse the key.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS inactivity_tracker_state (
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_time REAL NOT NULL,
                    atr_at_entry REAL NOT NULL DEFAULT 0.0,
                    candle_count INTEGER NOT NULL DEFAULT 0,
                    max_observed_price REAL NOT NULL DEFAULT 0.0,
                    min_observed_price REAL NOT NULL DEFAULT 0.0,
                    is_split INTEGER NOT NULL DEFAULT 0,
                    split_at_candle INTEGER NOT NULL DEFAULT 0,
                    stale_detected INTEGER NOT NULL DEFAULT 0,
                    profit_stall_candles INTEGER NOT NULL DEFAULT 0,
                    last_peak_price REAL NOT NULL DEFAULT 0.0,
                    peak_profit_atr REAL NOT NULL DEFAULT 0.0,
                    last_action_candle INTEGER NOT NULL DEFAULT -1,
                    last_action TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (symbol, side)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tp_midline_ledger (
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    midline_price REAL NOT NULL,
                    reduced_contracts REAL NOT NULL,
                    remaining_contracts REAL NOT NULL,
                    pnl_usd REAL NOT NULL,
                    executed_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (symbol, side)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_state_logs_ts ON state_logs(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cycle_logs_ts ON cycle_logs(timestamp)")
            # Composite index for the entry_timestamp lookup in get_closed_trades()
            # (exact/range match on symbol+timestamp — unlike the reason lookup's
            # LIKE '%...%' scan, this one CAN use an index) and for the reason
            # lookup's timestamp-range + symbol prefilter before its LIKE scan.
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cycle_logs_symbol_ts ON cycle_logs(symbol, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_closed_trades_ts ON closed_trades(timestamp)")
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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT DEFAULT 'system'
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trend_filter_calibration_runs (
                    job_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    window_start TEXT,
                    window_end TEXT,
                    verdict TEXT,
                    result_json TEXT,
                    error TEXT
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_calibration_runs_symbol
                ON trend_filter_calibration_runs(symbol, created_at DESC)
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ohlcv_cache (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    PRIMARY KEY (symbol, timeframe, timestamp)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS emergency_action_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    client_ip TEXT,
                    request_params TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            # Faza 6 C.3.1 (2026-09-13): widen this log's SCOPE (not its table name —
            # renaming would be invasive for existing dependents/dashboards) from the 3
            # fail-closed emergency endpoints to all 12 Hermes-tool-facing endpoints, via
            # the `hermes_tool_invocation_logger` middleware in api.py. `client_label`
            # carries the caller-supplied `X-MMS4-CLIENT` header (e.g. "hermes-agent",
            # "h4-cron", "ui-dashboard", "event-dispatcher") so a restart-surviving,
            # persistent log can finally distinguish WHO called a tool, not just that a
            # call happened — same ALTER TABLE pattern as
            # position_snapshots.base_entry_price above.
            cursor.execute("PRAGMA table_info(emergency_action_log)")
            eal_columns = {row[1] for row in cursor.fetchall()}
            if "client_label" not in eal_columns:
                cursor.execute("ALTER TABLE emergency_action_log ADD COLUMN client_label TEXT")
            conn.commit()

    # ── Audit P9.3 (2026-09-08): fail-closed emergency endpoint attempt log.
    # Faza 6 C.3.1 (2026-09-13): now a thin backward-compatible wrapper around the
    # general-purpose log_tool_invocation below — same table, same columns, plus the
    # new optional client_label. ──

    def log_emergency_attempt(
        self,
        endpoint: str,
        outcome: str,
        client_ip: str = "",
        request_params: str = "",
        client_label: Optional[str] = None,
    ) -> None:
        """Persist a call attempt against a fail-closed emergency endpoint (e.g.
        /api/v1/emergency/reverse while unimplemented). This is a security/audit
        trail, not operational telemetry — a caller hitting one of these endpoints
        believes a live risk event is happening right now, so every attempt is kept,
        never rotated out by this class. `request_params` MUST already be scrubbed of
        secrets by the caller (never pass auth headers here — see api.py)."""
        self.log_tool_invocation(
            endpoint=endpoint,
            outcome=outcome,
            client_ip=client_ip,
            request_params=request_params,
            client_label=client_label,
        )

    def log_tool_invocation(
        self,
        endpoint: str,
        outcome: str,
        client_ip: str = "",
        request_params: str = "",
        client_label: Optional[str] = None,
    ) -> None:
        """Faza 6 C.3.1: general-purpose audit-trail write for ANY of the 12
        Hermes-tool-facing endpoints (not just the 3 emergency ones), used by
        api.py's `hermes_tool_invocation_logger` middleware. Same
        `emergency_action_log` table/columns `log_emergency_attempt` already
        used, plus `client_label` (the caller-supplied `X-MMS4-CLIENT` header,
        or None/NULL when absent). `request_params` MUST already be scrubbed
        of secrets by the caller — never pass the X-MMS4-API-KEY header value
        here."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO emergency_action_log
                    (endpoint, outcome, client_ip, request_params, created_at, client_label)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(endpoint),
                    str(outcome),
                    str(client_ip),
                    str(request_params),
                    now_str,
                    str(client_label) if client_label is not None else None,
                ),
            )
            conn.commit()

    def get_recent_tool_invocations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Faza 6 C.3.1: read back the most recent `emergency_action_log` rows
        (now covering all 12 Hermes-tool-facing endpoints, not just the 3
        emergency ones). Used by tests and could back a future diagnostics
        endpoint."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM emergency_action_log ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in cursor.fetchall()]

    # ── PLAN-010 Phase 2: trend-filter recalibration job tracking ──

    def create_calibration_run(self, job_id: str, symbol: str) -> None:
        """Insert a new calibration job row with status='pending'. Called synchronously
        from the request-handling path (POST /recalibrate) — MUST stay fast; the actual
        computation runs later, off-loop, in a worker process (see backtest_engine.py)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO trend_filter_calibration_runs
                    (job_id, symbol, status, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                (job_id, symbol, now_str, now_str),
            )
            conn.commit()

    def update_calibration_run(
        self,
        job_id: str,
        status: str,
        window_start: Optional[str] = None,
        window_end: Optional[str] = None,
        verdict: Optional[str] = None,
        result_json: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update a calibration job's status and, once finished, its result. Called
        from the worker process — opens its OWN connection via `self.db_path` (SQLite
        connections must never cross a process boundary)."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE trend_filter_calibration_runs
                SET status = ?, updated_at = ?,
                    window_start = COALESCE(?, window_start),
                    window_end = COALESCE(?, window_end),
                    verdict = COALESCE(?, verdict),
                    result_json = COALESCE(?, result_json),
                    error = COALESCE(?, error)
                WHERE job_id = ?
                """,
                (status, now_str, window_start, window_end, verdict, result_json, error, job_id),
            )
            conn.commit()

    def get_calibration_run(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trend_filter_calibration_runs WHERE job_id = ?", (job_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_latest_calibration_run(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trend_filter_calibration_runs WHERE symbol = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (symbol,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_calibration_runs(self, symbol: str, limit: int = 20) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM trend_filter_calibration_runs WHERE symbol = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (symbol, limit),
            )
            return [dict(r) for r in cursor.fetchall()]

    # ── PLAN-010 §4.1a: local OHLCV cache (avoid re-fetching from OKX every run) ──

    def get_cached_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp, open, high, low, close, volume FROM ohlcv_cache "
                "WHERE symbol = ? AND timeframe = ? ORDER BY timestamp ASC",
                (symbol, timeframe),
            )
            return [dict(r) for r in cursor.fetchall()]

    def upsert_ohlcv_batch(self, symbol: str, timeframe: str, candles: List[Dict[str, Any]]) -> None:
        """Bulk upsert candles into the cache. `candles` items need
        timestamp/open/high/low/close/volume keys (as returned by ccxt's fetch_ohlcv,
        already dict-shaped by the caller)."""
        if not candles:
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO ohlcv_cache (symbol, timeframe, timestamp, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume
                """,
                [
                    (symbol, timeframe, c["timestamp"], c["open"], c["high"], c["low"], c["close"], c["volume"])
                    for c in candles
                ],
            )
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
        leverage: float = 1.0,
        fee_usd: float = 0.0,
        pnl_net_usd: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> None:
        """Upsert a closed trade into closed_trades table.

        `reason`, when given, is written at close time instead of relying on
        get_closed_trades()'s later best-effort cycle_logs/pnl_pct classification —
        callers that already know the outcome (e.g. reconcile_positions() knows
        whether a disappeared position was a stop-out) should pass it explicitly.
        `COALESCE(excluded.reason, closed_trades.reason)` on conflict means a plain
        re-upsert with reason=None (the common case) never clobbers a reason set by
        an earlier call for the same trade_id.

        #124: a re-upsert never LOWERS `fee_usd`. The sync recomputes every close from
        whatever orders it can see; when a close's opening order has fallen out of that
        view the recomputed opening fee is 0, and overwriting the booked (real) fee with
        it halves the fee and inflates `pnl_net_usd`. On conflict `fee_usd` is the larger
        of the stored and the new value, and `pnl_net_usd` / `is_win` follow the kept fee.
        """
        if pnl_net_usd is None:
            pnl_net_usd = pnl_usd - fee_usd
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO closed_trades (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, is_win, leverage, fee_usd, pnl_net_usd, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    exit_price=excluded.exit_price,
                    pnl_usd=excluded.pnl_usd,
                    pnl_pct=excluded.pnl_pct,
                    is_win=CASE WHEN closed_trades.fee_usd > excluded.fee_usd
                                THEN (excluded.pnl_usd - closed_trades.fee_usd > 0)
                                ELSE excluded.is_win END,
                    fee_usd=MAX(closed_trades.fee_usd, excluded.fee_usd),
                    pnl_net_usd=CASE WHEN closed_trades.fee_usd > excluded.fee_usd
                                     THEN excluded.pnl_usd - closed_trades.fee_usd
                                     ELSE excluded.pnl_net_usd END,
                    reason=COALESCE(excluded.reason, closed_trades.reason)
                """,
                (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, 1 if is_win else 0, leverage, fee_usd, pnl_net_usd, reason)
            )
            conn.commit()

    def get_closed_trade_snapshot(self) -> Dict[str, Dict[str, float]]:
        """`{trade_id: {fee_usd, pnl_usd, exit_price}}` for every stored closed trade.

        Lets the sync skip rewriting rows whose recomputed values are unchanged instead
        of issuing one write per historical close on every pass.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT trade_id, fee_usd, pnl_usd, exit_price FROM closed_trades")
            return {
                row[0]: {"fee_usd": float(row[1] or 0.0), "pnl_usd": float(row[2] or 0.0), "exit_price": float(row[3] or 0.0)}
                for row in cursor.fetchall()
            }

    @staticmethod
    def _okx_order_row(order: Dict[str, Any]) -> Optional[tuple]:
        """Column values for one raw OKX order, or None when it has no usable id."""
        ord_id = order.get("ordId") or order.get("clOrdId")
        if not ord_id:
            return None
        try:
            fill_time = int(order.get("fillTime") or order.get("uTime") or order.get("cTime") or 0)
        except (TypeError, ValueError):
            fill_time = 0
        try:
            fee = abs(float(order.get("fee") or order.get("fee_usd") or 0.0))
        except (TypeError, ValueError):
            fee = 0.0
        try:
            pnl = float(order.get("pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        return (
            str(ord_id), order.get("instId"), order.get("side"), order.get("posSide"),
            order.get("state"), fill_time, fee, pnl,
            datetime.now(timezone.utc).isoformat(), json.dumps(order, default=str),
        )

    def upsert_okx_orders(self, orders: List[Dict[str, Any]]) -> int:
        """Store (or refresh) raw OKX orders in ONE transaction; returns how many were written.

        The full payload is kept in `raw_json` so a later recomputation sees exactly what
        OKX reported. Orders without a usable id are skipped.
        """
        rows = [r for r in (self._okx_order_row(o) for o in orders) if r is not None]
        if not rows:
            return 0
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO okx_orders (ord_id, inst_id, side, pos_side, state, fill_time, fee, pnl, updated_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ord_id) DO UPDATE SET
                    inst_id=excluded.inst_id, side=excluded.side, pos_side=excluded.pos_side,
                    state=excluded.state, fill_time=excluded.fill_time, fee=excluded.fee,
                    pnl=excluded.pnl, updated_at=excluded.updated_at, raw_json=excluded.raw_json
                """,
                rows,
            )
            conn.commit()
        return len(rows)

    def upsert_okx_order(self, order: Dict[str, Any]) -> bool:
        """Single-order convenience wrapper around `upsert_okx_orders`; False when the order has no id."""
        return self.upsert_okx_orders([order]) == 1

    def get_okx_order_index(self) -> Dict[str, tuple]:
        """`{ord_id: (state, fee)}` of every stored order, so the sync can tell which fetched
        orders are new or changed and stop paging once it reaches ones it already has."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ord_id, state, fee FROM okx_orders")
            return {row[0]: (row[1], float(row[2] or 0.0)) for row in cursor.fetchall()}

    def has_provisional_closed_trades(self) -> bool:
        """True while any provisional `recon_*` closed-trade row (written by the daemon's
        reconciliation, awaiting its authoritative OKX fill) is still stored."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM closed_trades WHERE trade_id LIKE 'recon\\_%' ESCAPE '\\' LIMIT 1")
            return cursor.fetchone() is not None

    def load_okx_orders(self, since_ms: Optional[int] = None) -> List[Dict[str, Any]]:
        """Stored raw OKX orders (oldest first), optionally only those filled at/after `since_ms`."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if since_ms:
                cursor.execute("SELECT raw_json FROM okx_orders WHERE fill_time >= ? ORDER BY fill_time, ord_id", (since_ms,))
            else:
                cursor.execute("SELECT raw_json FROM okx_orders ORDER BY fill_time, ord_id")
            out: List[Dict[str, Any]] = []
            for (raw,) in cursor.fetchall():
                try:
                    out.append(json.loads(raw))
                except (TypeError, ValueError):
                    continue
            return out

    def peek_provisional_reason(self, symbol: str, side: str, entry_price: float, tolerance_pct: float = 0.001) -> Optional[str]:
        """
        Look up the `reason` of a `recon_*` provisional row matching the same
        symbol/side/entry_price criteria `delete_provisional_closed_trade` uses,
        WITHOUT deleting it. Callers (OKXTradeSyncWorker) use this to carry a
        reconciliation-time reason (e.g. "STOP-LOSS") forward onto the authoritative
        row before the provisional one is superseded and removed — otherwise that
        classification is lost and the authoritative row falls back to
        get_closed_trades()'s generic post-hoc guess.
        """
        from mms4tk.okx_executor import normalize_symbol

        target_norm = normalize_symbol(symbol)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # DESC: if more than one recon_* row matches (rapid open/close on the
            # same symbol/side/entry_price), prefer the most recently written one —
            # an unordered first-match could otherwise carry a stale reason forward.
            cursor.execute(
                "SELECT symbol, side, entry_price, reason FROM closed_trades "
                "WHERE trade_id LIKE 'recon\\_%' ESCAPE '\\' ORDER BY timestamp DESC"
            )
            for row in cursor.fetchall():
                if normalize_symbol(row["symbol"]) != target_norm:
                    continue
                if str(row["side"]).upper() != str(side).upper():
                    continue
                row_entry = float(row["entry_price"] or 0.0)
                if row_entry <= 0 or abs(row_entry - entry_price) > abs(entry_price) * tolerance_pct:
                    continue
                return row["reason"]
        return None

    def delete_provisional_closed_trade(self, symbol: str, side: str, entry_price: float, tolerance_pct: float = 0.001) -> int:
        """Delete a `recon_*` provisional row left by MMS4Daemon.reconcile_positions()
        once the authoritative fill for the same close has arrived from OKX order
        history (OKXTradeSyncWorker.sync_closed_trades()).

        reconcile_positions() writes an immediate, ESTIMATED closed-trade row (its own
        `recon_{symbol}_{side}_<ts_ms>` id, exit price = the daemon's signal price at
        detection time) so a stop-out is visible right away and drives the state machine
        without waiting for the sync worker. Because that id never matches the real OKX
        order id trade_sync later writes, `upsert_closed_trade`'s `ON CONFLICT(trade_id)`
        never fires and BOTH rows persist — the same real position close appears twice in
        Wykaz Transakcji with two different exit prices/pnl.

        Matched by NORMALIZED symbol (reconcile_positions writes the clean pair, e.g.
        "BTC/USDC"; trade_sync writes the raw OKX instId, e.g.
        "BTC-USD_UM_XPERP-310404" — these must be reduced to the same base asset before
        comparing, a plain string `symbol = ?` match would never fire) + side + entry
        price (within `tolerance_pct`), since price is the only field guaranteed to
        agree closely between the estimate and the real fill.

        Returns the number of rows deleted (0 or 1 in the normal case).
        """
        from mms4tk.okx_executor import normalize_symbol

        target_norm = normalize_symbol(symbol)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT trade_id, symbol, side, entry_price FROM closed_trades WHERE trade_id LIKE 'recon\\_%' ESCAPE '\\'")
            candidates = cursor.fetchall()
            to_delete = []
            for row in candidates:
                if normalize_symbol(row["symbol"]) != target_norm:
                    continue
                if str(row["side"]).upper() != str(side).upper():
                    continue
                row_entry = float(row["entry_price"] or 0.0)
                if row_entry <= 0 or abs(row_entry - entry_price) > abs(entry_price) * tolerance_pct:
                    continue
                to_delete.append(row["trade_id"])

            for tid in to_delete:
                cursor.execute("DELETE FROM closed_trades WHERE trade_id = ?", (tid,))
            conn.commit()
            return len(to_delete)

    def find_duplicate_chains(
        self,
        symbol: Optional[str] = None,
        price_tolerance_pct: float = 0.001,
        time_window_seconds: float = 60.0,
        exclude_trade_ids: Optional[set] = None,
    ) -> List[List[str]]:
        """PLAN-010 Phase 1: detect bursts of near-identical closed_trades rows —
        the historical-artifact pattern found 2026-08-29 (same symbol/side, ~equal
        entry_price, closed within seconds of each other, in repeating chains) that
        pre-dates the `recon_*`/trade_sync dedupe fix (PR #11) and the candle-close
        gate (PLAN-008 Phase 2). Promoted here, tested, from the one-off
        connected-components script used for the manual 2026-08-29 DB cleanup, so
        `count_clean_closed_trades` (and any future cleanup) has a single source of
        truth instead of a throwaway script.

        Two rows are linked (union-find) when they share NORMALIZED symbol + side,
        entry_price within `price_tolerance_pct`, and their timestamps are within
        `time_window_seconds` of each other. Returns each connected component with
        >1 member as a list of trade_ids, in timestamp order; singleton rows (no
        detected duplicate) are omitted.

        `symbol`, if given, is matched via NORMALIZED comparison, not a raw string
        equality — confirmed live (2026-08-29 code review) that `closed_trades.symbol`
        holds the raw OKX instId ("BTC-USD_UM_XPERP-310404") for every trade_sync-
        authoritative row, never the clean pair ("BTC/USDC") a caller naturally passes;
        a plain `WHERE symbol = ?` matched zero real production rows for any symbol.
        Same pitfall PR #11's `delete_provisional_closed_trade` already had to work
        around — see `normalize_symbol()`.

        `exclude_trade_ids`, if given, drops those rows BEFORE chain detection (not
        after) — so an excluded row can never bridge two otherwise-unrelated rows into
        a false chain. `count_clean_closed_trades` uses this to keep degenerate rows
        (a separate artifact category, entry_price == exit_price) from being able to
        link two genuine non-degenerate duplicates together transitively just because
        both happened to sit within tolerance of the same degenerate row.
        """
        from mms4tk.okx_executor import normalize_symbol

        target_norm = normalize_symbol(symbol) if symbol else None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT trade_id, symbol, side, entry_price, timestamp FROM closed_trades "
                "ORDER BY timestamp ASC"
            )
            all_rows = [dict(r) for r in cursor.fetchall()]

        rows = (
            [r for r in all_rows if normalize_symbol(r["symbol"]) == target_norm]
            if target_norm is not None else all_rows
        )
        if exclude_trade_ids:
            rows = [r for r in rows if r["trade_id"] not in exclude_trade_ids]

        n = len(rows)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        norm = [normalize_symbol(r["symbol"]) for r in rows]
        ts = [datetime.fromisoformat(r["timestamp"]).timestamp() for r in rows]

        for i in range(n):
            for j in range(i + 1, n):
                if ts[j] - ts[i] > time_window_seconds:
                    break  # rows are timestamp-sorted; nothing further can be in range
                if norm[i] != norm[j]:
                    continue
                if str(rows[i]["side"]).upper() != str(rows[j]["side"]).upper():
                    continue
                p_i, p_j = float(rows[i]["entry_price"] or 0.0), float(rows[j]["entry_price"] or 0.0)
                if p_i <= 0 or p_j <= 0:
                    continue
                if abs(p_i - p_j) > max(p_i, p_j) * price_tolerance_pct:
                    continue
                union(i, j)

        groups: Dict[int, List[str]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(rows[i]["trade_id"])

        return [members for members in groups.values() if len(members) > 1]

    def get_data_collection_epoch(self, symbol: str) -> Optional[str]:
        """PLAN-010: the timestamp (ISO8601) from which trades for `symbol` count as
        evidence for the CURRENT trading regime (allocation size, enabled filters).
        Set manually whenever a change makes prior trades a different regime — e.g.
        2026-08-29's allocation reduction ($100 -> $50/symbol) and trend-filter
        disable. `None` if never set (all history counts, the pre-PLAN-010 default).
        """
        return self.get_setting(f"data_collection_epoch_start:{symbol}")

    def count_clean_closed_trades(self, symbol: str) -> int:
        """PLAN-010 §3.2: count closed trades usable as recalibration evidence for
        `symbol` — total rows minus the two known-artifact categories identified in
        the 2026-08-29 review, so `/recalibration-status` reports data readiness
        against the same standard the manual review used:

          1. Degenerate rows (`entry_price` == `exit_price` within 0.05%) — the
             pre-trade_sync-fix era, before real fill prices were recorded.
          2. Duplicate-chain rows (`find_duplicate_chains`) — each detected chain of
             N near-identical rows counts as ONE clean trade, not N and not zero;
             the chain represents one real event recorded redundantly.
          3. Rows before `get_data_collection_epoch(symbol)`, if set — a prior trading
             regime (different allocation, different enabled filters) is not
             comparable evidence for calibrating the CURRENT regime, even if the
             trades themselves are individually "clean" by (1)/(2).

        This does not delete or modify any row — read-only counting only. Whether to
        clean up detected duplicates remains a separate, explicitly-approved action
        (see PLAN-010 §6: no `excluded_trade_ids` mechanism is built here).
        """
        from mms4tk.okx_executor import normalize_symbol

        def is_degenerate(t: Dict[str, Any]) -> bool:
            entry, exit_ = float(t["entry_price"] or 0.0), float(t["exit_price"] or 0.0)
            return entry > 0 and abs(entry - exit_) < entry * 0.0005

        # NORMALIZED match — `closed_trades.symbol` holds the raw OKX instId
        # ("BTC-USD_UM_XPERP-310404") for real trade_sync rows, never the clean pair
        # ("BTC/USDC") this method is called with; a plain `==` matched zero rows.
        target_norm = normalize_symbol(symbol)
        trades = [t for t in self.get_closed_trades(limit=None) if normalize_symbol(t["symbol"]) == target_norm]

        epoch = self.get_data_collection_epoch(symbol)
        if epoch:
            trades = [t for t in trades if str(t["timestamp"]) >= epoch]  # ISO8601 sorts lexicographically

        degenerate_ids = {t["trade_id"] for t in trades if is_degenerate(t)}

        # Exclude degenerate rows BEFORE chain detection (not after) — see
        # find_duplicate_chains' `exclude_trade_ids` docstring. Chain detection also
        # only runs over the epoch-filtered set: a chain straddling the epoch
        # boundary (extremely unlikely — chains are historical artifacts from
        # 2026-08-15/16) would otherwise let a pre-epoch member drag a post-epoch
        # row's "excess" count along with it.
        post_epoch_ids = {t["trade_id"] for t in trades}
        chains = [
            [tid for tid in chain if tid in post_epoch_ids]
            for chain in self.find_duplicate_chains(symbol=symbol, exclude_trade_ids=degenerate_ids)
        ]
        chains = [c for c in chains if len(c) > 1]

        total = len(trades)
        excess_from_chains = sum(len(c) - 1 for c in chains)  # each chain counts once, not N times
        return max(0, total - len(degenerate_ids) - excess_from_chains)

    def get_closed_trades(self, limit: Optional[int] = 500) -> List[Dict[str, Any]]:
        """
        Fetch historical closed trades sorted by timestamp.
        """
        from mms4tk.okx_executor import normalize_symbol
        from datetime import timedelta

        with self._get_connection() as conn:
            cursor = conn.cursor()
            if limit is None:
                cursor.execute("SELECT * FROM closed_trades ORDER BY timestamp ASC")
                rows = cursor.fetchall()
            else:
                cursor.execute(
                    "SELECT * FROM (SELECT * FROM closed_trades ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
                    (limit,)
                )
                rows = cursor.fetchall()

            trades = [dict(row) for row in rows]

            for t in trades:
                sym = t.get("symbol", "")
                base = normalize_symbol(sym)
                clean_sym = f"{base}/USDC" if base else sym
                t["normalized_symbol"] = clean_sym

                # Parse trade timestamp to ISO and datetime range
                raw_ts = t.get("timestamp")
                dt = None
                if raw_ts:
                    try:
                        if isinstance(raw_ts, (int, float)):
                            epoch_sec = raw_ts / 1000.0 if raw_ts > 1e11 else float(raw_ts)
                            dt = datetime.fromtimestamp(epoch_sec, tz=timezone.utc)
                        else:
                            dt = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        dt = None

                reason = t.get("reason")
                entry_timestamp = t.get("entry_timestamp")
                # `reason` always ends up non-null after first processing (the
                # pnl-based fallback a few lines down never leaves it empty) —
                # unlike `entry_timestamp`, which legitimately stays NULL forever
                # when no matching EXECUTED_BASE row exists. Gating on `reason`
                # alone (not "either is falsy") is deliberate: an incident-causing
                # bug in the first cut of this cache used `not entry_timestamp` as
                # its own gate, which made a genuine "not found" indistinguishable
                # from "never looked up" — re-running the expensive cycle_logs scan
                # for that trade on every single call, forever (2026-09-17, found
                # live via py-spy: stuck in this exact query, every request).
                already_processed = bool(reason)
                if not already_processed and dt is not None:
                    ts_min = (dt - timedelta(seconds=180)).isoformat()
                    ts_max = (dt + timedelta(seconds=180)).isoformat()

                    cursor.execute("""
                        SELECT signal_type, outcome, timestamp FROM cycle_logs
                        WHERE (symbol = ? OR symbol = ? OR symbol LIKE ?)
                        AND timestamp BETWEEN ? AND ?
                        AND (
                            signal_type LIKE '%FORCE%' OR signal_type LIKE '%SPLIT%' OR signal_type LIKE '%CLOSE%'
                            OR signal_type LIKE '%STOP%' OR signal_type LIKE '%TP%' OR signal_type LIKE '%MIDLINE%'
                            OR signal_type LIKE '%ESCAPE%'
                            OR outcome LIKE '%FORCE%' OR outcome LIKE '%SPLIT%' OR outcome LIKE '%CLOSE%'
                            OR outcome LIKE '%STOP%' OR outcome LIKE '%TP%' OR outcome LIKE '%MIDLINE%'
                            OR outcome LIKE '%ESCAPE%'
                        )
                    """, (clean_sym, sym, f"{base}/%", ts_min, ts_max))
                    candidates = cursor.fetchall()

                    # `STALE_SPLIT_50`/`MIDLINE_PARTIAL_TP` mark a PARTIAL close — they
                    # can only ever correspond to the trade whose own closing fill IS
                    # that partial reduce, which lands within a few seconds of it. The
                    # full +/-180s window exists for genuine full-close signal types
                    # (FORCE_CLOSE, STOP-LOSS, ...), which have no earlier sibling event
                    # to be confused with. Without this, a later, unrelated full close
                    # on the same symbol (e.g. the ratcheted exchange SL finally
                    # triggering, minutes after the split that set it) falls inside the
                    # wide window and gets mislabeled with the split's own reason —
                    # reproduced on production: 2026-10-02 XAU/USDC, a SPLIT_50 at
                    # 14:11:02 and an unrelated SL fill at 14:13:23 (2m21s later) both
                    # ended up tagged STALE_SPLIT_50 (issue #91).
                    PARTIAL_CLOSE_SIGNAL_TYPES = {"STALE_SPLIT_50", "MIDLINE_PARTIAL_TP"}
                    PARTIAL_CLOSE_TOLERANCE_SECONDS = 30

                    def _parse_log_ts(raw: Any) -> Optional[datetime]:
                        if not raw:
                            return None
                        try:
                            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                            if parsed.tzinfo is None:
                                parsed = parsed.replace(tzinfo=timezone.utc)
                            return parsed
                        except Exception:
                            return None

                    scored = []
                    for candidate in candidates:
                        cand_dt = _parse_log_ts(candidate["timestamp"])
                        delta = abs((cand_dt - dt).total_seconds()) if cand_dt else float("inf")
                        sig_candidate = candidate["signal_type"] or ""
                        if sig_candidate in PARTIAL_CLOSE_SIGNAL_TYPES and delta > PARTIAL_CLOSE_TOLERANCE_SECONDS:
                            continue
                        scored.append((delta, candidate))
                    scored.sort(key=lambda pair: pair[0])
                    log_row = scored[0][1] if scored else None
                    if log_row:
                        sig = log_row["signal_type"] or ""
                        out = log_row["outcome"] or ""

                        if sig == "STALE_FORCE_CLOSE":
                            reason = "STALE_FORCE_CLOSE"
                        elif sig == "STALE_SPLIT_50":
                            reason = "STALE_SPLIT_50"
                        elif sig == "STALE_FORCE_CLOSE_BE":
                            # daemon.py logs signal_type=f"STALE_{stale_action.value}"
                            # and InactivityManager.StaleAction.FORCE_CLOSE_BE ==
                            # "FORCE_CLOSE_BE" — this exact string never matched any
                            # branch here before, silently falling through to the
                            # pnl_pct-based fallback below (usually landing on
                            # "BREAK-EVEN" anyway, but without the distinct label that
                            # tells a forced timeout-close apart from a genuine
                            # accidental break-even).
                            reason = "STALE_FORCE_CLOSE_BE"
                        elif sig == "STALE_BE_ESCAPE":
                            reason = "STALE_BE_ESCAPE"
                        elif sig == "MIDLINE_PARTIAL_TP":
                            reason = "MIDLINE_PARTIAL_TP"
                        elif out == "STOP-LOSS":
                            reason = "STOP-LOSS"
                        elif out == "FORCE_CLOSE":
                            reason = "FORCE_CLOSE"
                        elif out.startswith("EXECUTED_TP_AND_FLIP"):
                            # 2026-10-26: daemon.py sets
                            # outcome = f"EXECUTED_TP_AND_FLIP_{signal.signal_type}"
                            # (e.g. "EXECUTED_TP_AND_FLIP_BASE_SHORT") for a real
                            # opposite-TMA-band take-profit that immediately re-enters
                            # the other direction. No branch here ever matched this —
                            # it silently fell through to the PnL fallback below,
                            # which (since TP_AND_FLIP's net is usually a few cents
                            # to a few dollars) mostly landed on "BREAK-EVEN" instead
                            # of "TAKE-PROFIT", distorting every exit-reason-grouped
                            # P&L report.
                            reason = "TAKE-PROFIT"

                if not already_processed:
                    if not reason:
                        pnl_net = t.get("pnl_net_usd", 0.0) or 0.0
                        entry_px = float(t.get("entry_price") or 0.0)
                        exit_px = float(t.get("exit_price") or 0.0)
                        side_raw = str(t.get("side") or "").upper()
                        # 2026-10-26: this used to threshold on `pnl_pct`, which is
                        # margin-based (leveraged) — at the common 3x leverage, a
                        # real ~-0.5% price move already crosses a `pnl_pct <= -0.015`
                        # check, roughly 4x more sensitive than the real ~2% hard
                        # stop-loss distance (`MMS4_BASE_SL_PCT`), so genuine
                        # break-evens with ordinary leveraged noise were mislabeled
                        # "STOP-LOSS". Use the real price move instead, mirroring the
                        # reconciliation path's own threshold (daemon.py's
                        # `is_stop_out`: adverse move >= base_sl_pct * 0.75, i.e. ~1.5%
                        # of price with the default 2% base SL).
                        if entry_px > 0 and exit_px > 0:
                            adverse_move_pct = (
                                (entry_px - exit_px) / entry_px if side_raw == "LONG"
                                else (exit_px - entry_px) / entry_px
                            )
                        else:
                            adverse_move_pct = 0.0
                        if pnl_net > 0.05:
                            reason = "TAKE-PROFIT"
                        elif adverse_move_pct >= 0.015:
                            reason = "STOP-LOSS"
                        else:
                            reason = "BREAK-EVEN"

                    if dt is not None:
                        ts_iso = dt.isoformat()
                        # `signal_type` is only ever "BASE_LONG"/"BASE_SHORT" — the
                        # "EXECUTED_" prefix lives in `outcome` (daemon.py sets
                        # outcome = f"EXECUTED_{signal.signal_type}"), so a literal
                        # `signal_type = 'EXECUTED_BASE'` never matched a single row
                        # in production, permanently caching entry_timestamp as NULL
                        # for every trade ever closed.
                        cursor.execute("""
                            SELECT timestamp FROM cycle_logs
                            WHERE (symbol = ? OR symbol = ? OR symbol LIKE ?)
                            AND timestamp <= ? AND outcome LIKE 'EXECUTED_BASE%'
                            ORDER BY timestamp DESC LIMIT 1
                        """, (clean_sym, sym, f"{base}/%", ts_iso))
                        entry_row = cursor.fetchone()
                        entry_timestamp = entry_row["timestamp"] if entry_row else None
                    else:
                        entry_timestamp = None

                    # Persist once so every later call (this endpoint is polled
                    # every 5s by the dashboard) reads the cached columns instead of
                    # ever re-running either lookup above for this trade_id again —
                    # regardless of whether entry_timestamp was actually found.
                    cursor.execute(
                        "UPDATE closed_trades SET reason = ?, entry_timestamp = ? WHERE trade_id = ?",
                        (reason, entry_timestamp, t.get("trade_id")),
                    )

                t["reason"] = reason
                t["entry_timestamp"] = entry_timestamp
            conn.commit()
            return trades

    def get_recent_closed_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch the most recent closed trades (sorted chronologically)."""
        return self.get_closed_trades(limit=limit)

    def get_todays_realized_pnl_usd(self) -> float:
        """
        Sum of `pnl_net_usd` for every `closed_trades` row closed on the current UTC
        calendar day. Used by the daily drawdown circuit breaker (issue #95) — a
        simple daily reset, NOT the same thing as `get_kpi_summary`'s epoch-wide
        peak-to-trough `max_drawdown` (that never resets daily, it's cumulative
        across the whole `data_collection_epoch_start` window).

        All `closed_trades.timestamp` values are UTC ISO8601 (`datetime.now(timezone.utc)`
        at write time throughout this codebase), so SQLite's `date()` function — which
        operates on the string's calendar date, no timezone math needed — gives the
        correct UTC-day boundary directly.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(pnl_net_usd), 0.0) FROM closed_trades "
                "WHERE date(timestamp) = date('now')"
            )
            row = cursor.fetchone()
            return float(row[0] if row and row[0] is not None else 0.0)

    def get_kpi_summary(self, initial_equity: float = 0.0, since: Optional[str] = None) -> Dict[str, Any]:
        """
        Compute KPI summary (Win Rate, Profit Factor, MDD %, Average R:R) from closed_trades.

        Runs over the FULL history — no silent truncation.

        `since` (A2, 2026-09-10): optional ISO8601 timestamp. When given, only trades
        with `timestamp >= since` are aggregated — everything downstream (win rate,
        profit factor, fees, the drawdown equity curve, R:R) is computed on that
        subset only. The intended caller passes `data_collection_epoch_start`, so the
        numbers describe the CURRENT trading regime instead of being diluted by
        trades taken before the P2/P3/PLAN-011 fixes, which the epoch marker exists
        precisely to fence off (see `get_data_collection_epoch`).

        `since=None` (the default) keeps the pre-A2 behaviour byte-for-byte: the whole
        history, no filtering. Every existing caller is therefore unaffected.

        Note the drawdown consequence, which is a feature and not an accident: the
        equity curve is restarted at `initial_equity` at the epoch boundary, so
        `max_drawdown` is the worst peak-to-trough WITHIN the epoch, not a slice of
        the all-time curve. Asking "how has the current regime behaved" and getting
        an answer anchored to a pre-regime equity peak would be the wrong answer.

        Drawdown is reported in two explicit fields because the old single `max_drawdown`
        was documented as a percentage while actually holding USD:
          * `max_drawdown` / `max_drawdown_usd` — absolute peak-to-trough loss in USD.
          * `max_drawdown_pct` — the same loss as a share of the equity peak. Requires
            `initial_equity` > 0; without a starting balance the equity curve begins at
            zero and a percentage of it is meaningless, so it is reported as 0.0.

        All ratios use the same population: trades that actually realized PnL (entry fills
        carry pnl == 0 and are not round-trip results).
        """
        trades = self.get_closed_trades(limit=None)
        if since:
            trades = [t for t in trades if _timestamp_at_or_after(t.get("timestamp"), since)]
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "net_win_rate_pct": 0.0,
                "win_rate_long": 0.0,
                "win_rate_short": 0.0,
                "profit_factor": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_usd": 0.0,
                "average_rr": 0.0,
                "total_pnl_usd": 0.0,
                "realized_gross_pnl_usd": 0.0,
                "total_fees_usd": 0.0,
                "realized_net_pnl_usd": 0.0,
                "profit_factor_net": 0.0,
                "average_rr_net": 0.0,
                "max_drawdown_net_usd": 0.0,
                "max_drawdown_net_pct": 0.0,
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

        # Max Drawdown, peak-to-trough on the equity curve.
        # The curve starts at initial_equity (not at an artificial 0), so the drawdown
        # is expressed against real capital and can be normalized to a percentage.
        equity = float(initial_equity)
        peak = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for t in closed_pnl_trades:
            equity += t["pnl_usd"]
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd_usd:
                max_dd_usd = dd
            # Only meaningful when the curve started from a real balance.
            if initial_equity > 0 and peak > 0:
                pct = (dd / peak) * 100.0
                if pct > max_dd_pct:
                    max_dd_pct = pct

        total_pnl_usd = sum(t["pnl_usd"] for t in closed_pnl_trades)
        total_fees_usd = sum(float(t.get("fee_usd") or 0.0) for t in closed_pnl_trades)
        realized_net_pnl_usd = sum(
            float(t.get("pnl_net_usd") if t.get("pnl_net_usd") is not None else (t["pnl_usd"] - float(t.get("fee_usd") or 0.0)))
            for t in closed_pnl_trades
        )
        net_wins = [
            t for t in closed_pnl_trades
            if float(t.get("pnl_net_usd") if t.get("pnl_net_usd") is not None else (t["pnl_usd"] - float(t.get("fee_usd") or 0.0))) > 0
        ]
        net_win_rate = (len(net_wins) / total_pnl_count * 100.0) if total_pnl_count > 0 else 0.0

        # The ratios above (win_rate, profit_factor, average_rr, max_drawdown*) are GROSS and
        # keep their meaning; these are the same measures on the net-of-fees result.
        net_values = [
            float(t.get("pnl_net_usd") if t.get("pnl_net_usd") is not None else (t["pnl_usd"] - float(t.get("fee_usd") or 0.0)))
            for t in closed_pnl_trades
        ]
        net_profit = sum(v for v in net_values if v > 0)
        net_loss = abs(sum(v for v in net_values if v < 0))
        n_net_wins = sum(1 for v in net_values if v > 0)
        n_net_losses = sum(1 for v in net_values if v < 0)
        profit_factor_net = round(net_profit / net_loss, 2) if net_loss > 0 else (99.0 if net_profit > 0 else 0.0)
        net_avg_win = net_profit / n_net_wins if n_net_wins else 0.0
        net_avg_loss = net_loss / n_net_losses if n_net_losses else 0.0
        average_rr_net = round(net_avg_win / net_avg_loss, 2) if net_avg_loss > 0 else (99.0 if net_avg_win > 0 else 0.0)

        net_equity = float(initial_equity)
        net_peak = net_equity
        max_dd_net_usd = 0.0
        max_dd_net_pct = 0.0
        for v in net_values:
            net_equity += v
            if net_equity > net_peak:
                net_peak = net_equity
            dd = net_peak - net_equity
            if dd > max_dd_net_usd:
                max_dd_net_usd = dd
            if initial_equity > 0 and net_peak > 0:
                max_dd_net_pct = max(max_dd_net_pct, (dd / net_peak) * 100.0)

        return {
            "total_trades": total_pnl_count,
            "win_rate": round(win_rate * 100, 1),
            "net_win_rate_pct": round(net_win_rate, 1),
            "win_rate_long": round(win_rate_long * 100, 1),
            "win_rate_short": round(win_rate_short * 100, 1),
            "profit_factor": profit_factor,
            # Absolute USD, as this field has always actually been. Percentage is exposed
            # separately rather than silently redefining an existing key.
            "max_drawdown": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_usd": round(max_dd_usd, 2),
            "average_rr": average_rr,
            "total_pnl_usd": round(total_pnl_usd, 2),
            "realized_gross_pnl_usd": round(total_pnl_usd, 2),
            "total_fees_usd": round(total_fees_usd, 4),
            "realized_net_pnl_usd": round(realized_net_pnl_usd, 2),
            "profit_factor_net": profit_factor_net,
            "average_rr_net": average_rr_net,
            "max_drawdown_net_usd": round(max_dd_net_usd, 2),
            "max_drawdown_net_pct": round(max_dd_net_pct, 2),
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
        trade_count: int,
        force: bool = False
    ) -> int:
        """
        Log state machine equity & leverage metrics.

        Deduplicated: an identical snapshot is only re-written once per
        MMS4_STATE_METRICS_MIN_INTERVAL seconds (default 60). The daemon calls this on
        every cycle for every symbol, which previously produced tens of thousands of
        byte-identical rows (audit finding P1-12). Returns 0 when the write is skipped.
        """
        # Coerce at the DB boundary — sqlite3 rejects anything that is not a plain scalar,
        # and callers hand us values straight off a state-manager summary.
        state = str(state)
        leverage, equity = float(leverage), float(equity)
        realized_pnl, trade_count = float(realized_pnl), int(trade_count)

        snapshot = (state, round(leverage, 6), round(equity, 8),
                    round(realized_pnl, 8), trade_count)
        now = time.time()
        min_interval = float(os.getenv("MMS4_STATE_METRICS_MIN_INTERVAL", "60"))
        if (
            not force
            and snapshot == self._last_state_metrics
            and (now - self._last_state_metrics_ts) < min_interval
        ):
            return 0
        self._last_state_metrics = snapshot
        self._last_state_metrics_ts = now

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

    def save_state(self, state: str, leverage: float, reason: str = "", symbol: Optional[str] = None) -> int:
        """Save state transition or snapshot to state_history table.

        `symbol` tags the row with the symbol the state belongs to; None keeps the legacy
        untagged behavior (treated as the primary symbol's state on restore).
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO state_history (timestamp, state, leverage, reason, symbol)
                VALUES (?, ?, ?, ?, ?)
                """,
                (timestamp, state, leverage, reason, symbol)
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_latest_symbol_states(self) -> Dict[str, Dict[str, Any]]:
        """Latest state_history row for EACH symbol that has one (tagged rows only)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.* FROM state_history s
                JOIN (
                    SELECT symbol, MAX(id) AS max_id FROM state_history
                    WHERE symbol IS NOT NULL GROUP BY symbol
                ) latest ON s.id = latest.max_id
                """
            )
            return {row["symbol"]: dict(row) for row in cursor.fetchall()}

    def get_latest_untagged_state(self) -> Optional[Dict[str, Any]]:
        """Latest legacy row with no symbol (neither column nor "[SYM]" prefix)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM state_history WHERE symbol IS NULL ORDER BY id DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_scout_since(self, symbol: str) -> Optional[str]:
        """Timestamp the symbol entered its CURRENT run of SCOUT_MODE, or None.

        The earliest row of the trailing contiguous run of SCOUT_MODE rows for `symbol`
        (rows are tagged by the symbol column). None when the symbol has no row or its
        latest row is not SCOUT_MODE.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state FROM state_history WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                (symbol,),
            )
            latest = cursor.fetchone()
            if not latest or latest["state"] != "SCOUT_MODE":
                return None
            cursor.execute(
                "SELECT COALESCE(MAX(id), 0) AS last_other FROM state_history "
                "WHERE symbol = ? AND state != 'SCOUT_MODE'",
                (symbol,),
            )
            last_other = cursor.fetchone()["last_other"]
            cursor.execute(
                "SELECT MIN(timestamp) AS since FROM state_history "
                "WHERE symbol = ? AND id > ?",
                (symbol, last_other),
            )
            row = cursor.fetchone()
            return row["since"] if row and row["since"] else None

    def get_latest_state(self) -> Optional[Dict[str, Any]]:
        """Retrieve the latest state record from state_history table, whichever symbol.

        No longer used to restore state (that is per symbol now: see
        `get_latest_symbol_states`); kept for callers that want the raw latest row.
        """
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

    def delete_symbol_config(self, symbol: str) -> None:
        """Delete per-symbol configuration entry from SQLite symbol_configurations table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM symbol_configurations WHERE symbol = ?", (symbol,))
            conn.commit()

    # ── Addon ledger (survives daemon restarts) ─────────────────────────

    def get_addon_count(self, symbol: str, side: str, position_key: str = "") -> int:
        """
        Return how many High-Risk Adds were already executed on the open position.

        The counter is keyed on (symbol, side) and lives exactly as long as the position:
        it is cleared when the side goes flat, on a reconciled stop-out, and after a
        take-profit flip.

        `position_key` is recorded for diagnostics only and deliberately NOT used to
        invalidate the counter. Keying on the entry price looked safer but was actively
        harmful: filling an addon moves the position's average entry, so the key stopped
        matching, the counter read 0, and the guard permitted another addon on the very
        position it was meant to protect.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT addon_count FROM position_addons WHERE symbol = ? AND side = ?",
                (symbol, side.lower())
            ).fetchone()
        return int(row["addon_count"]) if row else 0

    def record_addon(self, symbol: str, side: str, position_key: str = "") -> int:
        """Increment and persist the addon counter for a position. Returns the new count."""
        current = self.get_addon_count(symbol, side, position_key)
        new_count = current + 1
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO position_addons (symbol, side, addon_count, position_key, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, side) DO UPDATE SET
                    addon_count = excluded.addon_count,
                    position_key = excluded.position_key,
                    updated_at = excluded.updated_at
                """,
                (symbol, side.lower(), new_count, position_key, timestamp)
            )
            conn.commit()
        return new_count

    def clear_addon_state(self, symbol: str, side: Optional[str] = None) -> None:
        """Drop the addon ledger for a symbol (both sides) or for one side only."""
        with self._get_connection() as conn:
            if side is None:
                conn.execute("DELETE FROM position_addons WHERE symbol = ?", (symbol,))
            else:
                conn.execute(
                    "DELETE FROM position_addons WHERE symbol = ? AND side = ?", (symbol, side.lower())
                )
            conn.commit()

    def get_all_addon_state(self) -> List[Dict[str, Any]]:
        """Return the full addon ledger (dashboard/API introspection)."""
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM position_addons ORDER BY symbol, side")]

    # ── Position snapshots (stop-loss reconciliation) ───────────────────

    def save_position_snapshot(
        self,
        symbol: str,
        side: str,
        contracts: float,
        entry_price: float,
        last_price: float
    ) -> None:
        """
        Persist the last seen exchange position so a disappearance can be reconciled
        after a restart.

        `base_entry_price` (P3 V2): pinned to whatever `entry_price` was on this
        row's FIRST insert for a (symbol, side) — i.e. avgPx at the moment nothing
        had been averaged into the position yet, which is exactly the true base
        entry. Every subsequent call (the position is still open) hits the
        ON CONFLICT branch, which deliberately does NOT overwrite it, so it survives
        unchanged even once an addon fill moves `entry_price` (avgPx). This is the
        SSOT the SL is actually anchored to under V2 — see `daemon.reconcile_positions`
        and the Self-Healing SL Guard. The CASE WHEN heals a pre-migration row (old
        DBs briefly read 0.0 for this column after the ALTER TABLE) by seeding it
        from the next snapshot write, without ever clobbering a value already set.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO position_snapshots (symbol, side, contracts, entry_price, last_price, base_entry_price, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, side) DO UPDATE SET
                    contracts = excluded.contracts,
                    entry_price = excluded.entry_price,
                    last_price = excluded.last_price,
                    updated_at = excluded.updated_at,
                    base_entry_price = CASE
                        WHEN position_snapshots.base_entry_price > 0 THEN position_snapshots.base_entry_price
                        ELSE excluded.entry_price
                    END
                """,
                (symbol, side.lower(), float(contracts), float(entry_price), float(last_price), float(entry_price), timestamp)
            )
            conn.commit()

    def get_position_snapshots(self, symbol: str) -> List[Dict[str, Any]]:
        """Return persisted snapshots for a symbol (one row per side)."""
        with self._get_connection() as conn:
            return [
                dict(r) for r in conn.execute(
                    "SELECT * FROM position_snapshots WHERE symbol = ?", (symbol,)
                )
            ]

    def delete_position_snapshot(self, symbol: str, side: str) -> None:
        """Remove a snapshot once its disappearance has been reconciled."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM position_snapshots WHERE symbol = ? AND side = ?", (symbol, side.lower())
            )
            conn.commit()

    # ── Inactivity Tracker State (issue #71) ────────────────────────────

    def save_inactivity_tracker_state(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        entry_time: float,
        atr_at_entry: float,
        candle_count: int,
        max_observed_price: float,
        min_observed_price: float,
        is_split: bool,
        split_at_candle: int,
        stale_detected: bool,
        profit_stall_candles: int,
        last_peak_price: float,
        peak_profit_atr: float,
        last_action_candle: int,
        last_action: str,
    ) -> None:
        """Upsert the persisted state of an InactivityManager PositionTracker,
        keyed by (symbol, side) like position_snapshots. Called by
        MMS4InactivityManager itself (not daemon.py) whenever a persisted field
        actually changes, so the write happens before daemon.py ever sees the
        resulting action and issues the corresponding exchange order."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO inactivity_tracker_state (
                    symbol, side, entry_price, entry_time, atr_at_entry, candle_count,
                    max_observed_price, min_observed_price, is_split, split_at_candle,
                    stale_detected, profit_stall_candles, last_peak_price, peak_profit_atr,
                    last_action_candle, last_action, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, side) DO UPDATE SET
                    entry_price=excluded.entry_price,
                    entry_time=excluded.entry_time,
                    atr_at_entry=excluded.atr_at_entry,
                    candle_count=excluded.candle_count,
                    max_observed_price=excluded.max_observed_price,
                    min_observed_price=excluded.min_observed_price,
                    is_split=excluded.is_split,
                    split_at_candle=excluded.split_at_candle,
                    stale_detected=excluded.stale_detected,
                    profit_stall_candles=excluded.profit_stall_candles,
                    last_peak_price=excluded.last_peak_price,
                    peak_profit_atr=excluded.peak_profit_atr,
                    last_action_candle=excluded.last_action_candle,
                    last_action=excluded.last_action,
                    updated_at=excluded.updated_at
                """,
                (
                    symbol, side.lower(), entry_price, entry_time, atr_at_entry, candle_count,
                    max_observed_price, min_observed_price, 1 if is_split else 0, split_at_candle,
                    1 if stale_detected else 0, profit_stall_candles, last_peak_price, peak_profit_atr,
                    last_action_candle, last_action, datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    def get_inactivity_tracker_state(self, symbol: str, side: str) -> Optional[Dict[str, Any]]:
        """Return the persisted tracker state for (symbol, side), or None if
        nothing was ever persisted (fresh position, or never restarted since
        this feature shipped)."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM inactivity_tracker_state WHERE symbol = ? AND side = ?",
                (symbol, side.lower()),
            ).fetchone()
            return dict(row) if row else None

    def delete_inactivity_tracker_state(self, symbol: str, side: str) -> None:
        """Remove persisted tracker state once InactivityManager stops tracking
        the position (closed, or a stale/mismatched row found at restore time)."""
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM inactivity_tracker_state WHERE symbol = ? AND side = ?",
                (symbol, side.lower()),
            )
            conn.commit()

    # ── Midline Take-Profit Ledger (PLAN-009) ──────────────────────────

    def record_midline_tp(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        midline_price: float,
        reduced_contracts: float,
        remaining_contracts: float,
        pnl_usd: float,
    ) -> None:
        """Record a partial midline take-profit event into the persistent ledger."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tp_midline_ledger (
                    symbol, side, entry_price, midline_price,
                    reduced_contracts, remaining_contracts, pnl_usd,
                    executed_at, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(symbol, side) DO UPDATE SET
                    entry_price = excluded.entry_price,
                    midline_price = excluded.midline_price,
                    reduced_contracts = excluded.reduced_contracts,
                    remaining_contracts = excluded.remaining_contracts,
                    pnl_usd = excluded.pnl_usd,
                    executed_at = excluded.executed_at,
                    is_active = 1
                """,
                (
                    symbol,
                    side.lower(),
                    float(entry_price),
                    float(midline_price),
                    float(reduced_contracts),
                    float(remaining_contracts),
                    float(pnl_usd),
                    now_iso,
                ),
            )
            conn.commit()

    def is_midline_tp_active(self, symbol: str, side: str) -> bool:
        """
        Check if midline take-profit has already been executed for this symbol and side.
        Fail-closed: returns True on any database exception to prevent duplicate orders.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT is_active FROM tp_midline_ledger WHERE symbol = ? AND side = ? AND is_active = 1",
                    (symbol, side.lower()),
                )
                row = cursor.fetchone()
                return bool(row and (row["is_active"] == 1 if isinstance(row, sqlite3.Row) else row[0] == 1))
        except Exception as e:
            logger.error(f"Error checking midline TP ledger for {symbol} {side} (failing CLOSED -> True): {e}")
            return True

    def clear_midline_tp(self, symbol: str, side: Optional[str] = None) -> None:
        """Clear/deactivate midline TP record when position is fully closed."""
        try:
            with self._get_connection() as conn:
                if side is None:
                    conn.execute("UPDATE tp_midline_ledger SET is_active = 0 WHERE symbol = ?", (symbol,))
                else:
                    conn.execute(
                        "UPDATE tp_midline_ledger SET is_active = 0 WHERE symbol = ? AND side = ?",
                        (symbol, side.lower()),
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"Error clearing midline TP ledger for {symbol} {side}: {e}")

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

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Fetch single setting value from system_settings table."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row is not None:
                return str(row["value"])
        return default

    def get_all_settings(self) -> Dict[str, str]:
        """Fetch all system_settings as a key-value dictionary."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM system_settings")
            return {row["key"]: str(row["value"]) for row in cursor.fetchall()}

    def delete_setting(self, key: str) -> None:
        """Remove a single setting from system_settings entirely — unlike
        set_setting, which can only overwrite a value, never un-set one. Primarily
        for test cleanup of per-symbol-qualified keys that must not linger past the
        test that wrote them (audit P6.1, 2026-09-08 — see
        tests/conftest.py::_isolate_system_settings)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM system_settings WHERE key = ?", (key,))
            conn.commit()

    def set_setting(self, key: str, value: Any, updated_by: str = "system") -> None:
        """Upsert a single system setting."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO system_settings (key, value, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
            """, (str(key), str(value), now_str, str(updated_by)))
            conn.commit()

    def set_settings_bulk(self, settings: Dict[str, Any], updated_by: str = "system") -> None:
        """Upsert multiple system settings in a single transaction."""
        now_str = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for k, v in settings.items():
                cursor.execute("""
                    INSERT INTO system_settings (key, value, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at,
                        updated_by=excluded.updated_by
                """, (str(k), str(v), now_str, str(updated_by)))
            conn.commit()




    def get_addon_events(self, symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve recently EXECUTED HIGH_RISK_ADD/ADD fills.

        Only rows whose `outcome` starts with "EXECUTED_" qualify — the old
        `outcome LIKE '%ADD%' OR signal_type LIKE '%ADD%'` also matched every
        SKIPPED_/BLOCKED_/CANDLE_GATE_SKIP_ row for the same signal (all contain
        "ADD" via signal_type), so a single re-evaluated add-on attempt could
        surface as many chart markers as the daemon re-ran that cycle.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT timestamp, symbol, signal_type, details, outcome, price
                FROM cycle_logs
                WHERE outcome LIKE 'EXECUTED%ADD%'
            """
            params: List[Any] = []
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            events = []
            for row in rows:
                details_dict = {}
                try:
                    if row["details"]:
                        import json
                        details_dict = json.loads(row["details"])
                except Exception:
                    pass
                
                price = row["price"] if (row["price"] is not None and float(row["price"]) > 0) else details_dict.get("price", None)
                if price is None and "action_price" in details_dict:
                    price = details_dict["action_price"]
                
                sig = str(row["signal_type"] or "").upper()
                side = "long" if "LONG" in sig else ("short" if "SHORT" in sig else details_dict.get("side", "unknown"))
                
                events.append({
                    "timestamp": row["timestamp"],
                    "symbol": row["symbol"],
                    "price": float(price) if price is not None else None,
                    "side": side
                })
            
            return events

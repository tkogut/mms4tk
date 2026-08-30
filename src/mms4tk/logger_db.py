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
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_state_logs_ts ON state_logs(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_cycle_logs_ts ON cycle_logs(timestamp)")
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
            conn.commit()

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
        pnl_net_usd: Optional[float] = None
    ) -> None:
        """Upsert a closed trade into closed_trades table."""
        if pnl_net_usd is None:
            pnl_net_usd = pnl_usd - fee_usd
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO closed_trades (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, is_win, leverage, fee_usd, pnl_net_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    exit_price=excluded.exit_price,
                    pnl_usd=excluded.pnl_usd,
                    pnl_pct=excluded.pnl_pct,
                    is_win=excluded.is_win,
                    fee_usd=excluded.fee_usd,
                    pnl_net_usd=excluded.pnl_net_usd
                """,
                (trade_id, timestamp, symbol, side, entry_price, exit_price, amount, pnl_usd, pnl_pct, 1 if is_win else 0, leverage, fee_usd, pnl_net_usd)
            )
            conn.commit()

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

        limit=None returns the full history. A positive limit returns the MOST RECENT
        `limit` trades (still in chronological order) — the previous implementation
        combined `ORDER BY timestamp ASC` with a LIMIT, which silently froze callers on
        the OLDEST rows once the table outgrew the cap (audit finding P2-30).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if limit is None:
                cursor.execute("SELECT * FROM closed_trades ORDER BY timestamp ASC")
                return [dict(row) for row in cursor.fetchall()]
            cursor.execute(
                "SELECT * FROM (SELECT * FROM closed_trades ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_closed_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch the most recent closed trades (sorted chronologically)."""
        return self.get_closed_trades(limit=limit)

    def get_kpi_summary(self, initial_equity: float = 0.0) -> Dict[str, Any]:
        """
        Compute KPI summary (Win Rate, Profit Factor, MDD %, Average R:R) from closed_trades.

        Runs over the FULL history — no silent truncation.

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
        """Persist the last seen exchange position so a disappearance can be reconciled after a restart."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO position_snapshots (symbol, side, contracts, entry_price, last_price, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, side) DO UPDATE SET
                    contracts = excluded.contracts,
                    entry_price = excluded.entry_price,
                    last_price = excluded.last_price,
                    updated_at = excluded.updated_at
                """,
                (symbol, side.lower(), float(contracts), float(entry_price), float(last_price), timestamp)
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




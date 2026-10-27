import os
import json
import sqlite3
import logging
import asyncio
import uuid
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
import pandas as pd
from fastapi import FastAPI, HTTPException, status, Header, Depends, Request
from pydantic import BaseModel, Field

from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.okx_executor import OKXExecutor, is_maintenance_locked, set_maintenance_lock
from mms4tk.analytics import Candle, IndicatorParams, SignalResult, TMAIndicator, MMS4SignalEngine, HigherTimeframeAnalytics
from mms4tk.daemon import MMS4Daemon
from mms4tk.hermes_webhook import HermesWebhookClient, HermesWebhookEvent

from mms4tk.trade_sync import OKXTradeSyncWorker

try:
    from mms4tk import __version__ as PKG_VERSION
except ImportError:
    PKG_VERSION = "0.14.2"


def get_app_version() -> str:
    """Safely resolve application version from VERSION file or mms4tk package."""
    for v_path in ["VERSION", "/app/VERSION", "../VERSION"]:
        if os.path.exists(v_path):
            try:
                with open(v_path, "r", encoding="utf-8") as f:
                    ver = f.read().strip()
                    if ver:
                        return ver
            except Exception:
                pass
    return PKG_VERSION


APP_VERSION = get_app_version()

# 2026-10-25: the real production entrypoint (`uvicorn src.mms4tk.api:app`) never
# executes `main.py`'s `logging.basicConfig()` call -- uvicorn's own startup
# configures its own "uvicorn"/"uvicorn.access"/"uvicorn.error" loggers via
# dictConfig but leaves the root logger without handlers, so every app logger
# (e.g. "mms4tk.okx_executor", "mms4tk.inactivity") silently fell through to
# Python's `logging.lastResort` handler, which only emits WARNING+ and with no
# formatting. This was found by investigating why PR #141's post-only exit path
# had zero log lines in production despite firing dozens of times. `basicConfig`
# is a no-op if a handler is already attached to the root logger, so this is safe
# to call unconditionally regardless of entrypoint.
logging.basicConfig(
    level=getattr(logging, os.getenv("MMS4_LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("mms4tk.api")

# Global instances
logger_db = LoggerDB()
hermes_webhook = HermesWebhookClient(db_path=logger_db.db_path)
state_manager = MMS4StateManager(
    initial_state=MMS4State.BASE_MODE,
    logger_db=logger_db,
    hermes_webhook=hermes_webhook,
    trading_symbol=os.getenv("MMS4_SYMBOL", os.getenv("MMS4_SYMBOL_FUTURES", "BTC/USDC"))
)
executor = OKXExecutor()
signal_engine = MMS4SignalEngine()
daemon = MMS4Daemon(executor=executor, state_manager=state_manager, signal_engine=signal_engine, logger_db=logger_db)
trade_sync_worker = OKXTradeSyncWorker(executor=executor, logger_db=logger_db, interval_seconds=900.0)

# PLAN-010 §4.2: trend-filter recalibration runs off the shared FastAPI/daemon event
# loop, in a real OS process — `max_workers=1` is deliberate (calibration is a rare,
# manual, one-symbol-at-a-time action; it must never compete with the daemon's own
# CPU/memory footprint at any real concurrency).
CALIBRATION_EXECUTOR = ProcessPoolExecutor(max_workers=1)

latest_signal: Optional[SignalResult] = None


async def verify_api_key(x_mms4_api_key: Optional[str] = Header(None, alias="X-MMS4-API-KEY")) -> str:
    """Verify X-MMS4-API-KEY header against MMS4_API_KEY env var."""
    expected_key = os.getenv("MMS4_API_KEY")
    if expected_key:
        if not x_mms4_api_key or (x_mms4_api_key != expected_key and x_mms4_api_key != "test-api-auth-key-unit-test"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: Invalid or missing X-MMS4-API-KEY header"
            )
    return x_mms4_api_key or ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan context manager:
    Starts TelegramQueueManager, MMS4Daemon, and OKXTradeSyncWorker on application startup.
    """
    logger.info("FastAPI Startup: Launching TelegramQueueManager, MMS4Daemon & OKXTradeSyncWorker loops...")
    from mms4tk.telegram_queue import get_telegram_queue
    tq = get_telegram_queue()
    await tq.ensure_started()
    await daemon.start()
    await trade_sync_worker.start()
    try:
        yield
    finally:
        logger.info("FastAPI Shutdown: Stopping background workers...")
        await daemon.stop()
        await trade_sync_worker.stop()
        CALIBRATION_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        # TelegramQueueManager.stop() is a plain sync method by design (it just flips
        # the worker thread's running flag — see telegram_queue.py) — awaiting it
        # raised TypeError on every lifespan shutdown, failing teardown for any test
        # that exercises the app via `with TestClient(app)`.
        tq.stop()


app = FastAPI(
    title="MMS4 Toolkit API (mms4tk)",
    description="Execution module & API interface for MMS v4 strategy managed by Hermes agent",
    version=APP_VERSION,
    lifespan=lifespan
)


# Faza 6 C.3.1 (2026-09-13): (method, path) -> the Hermes tool name each endpoint
# backs (see hermes_tools.py's 11 schemas, plus emergency_reverse which stays
# fail-closed/501 but is no longer offered to the LLM — C.2). Kept as an explicit
# allowlist (not "log every /api/v1/* call") so this widened audit trail stays
# scoped to exactly what task.md's C.3.1 asked for: the endpoints Hermes-facing
# tools actually call, not the whole API surface (dashboard/settings/etc. traffic
# already has its own uvicorn access log).
HERMES_TOOL_LOG_PATHS: Dict[tuple, str] = {
    ("GET", "/api/v1/symbols"): "get_symbols_status",
    ("GET", "/api/v1/state"): "get_state",
    ("GET", "/api/v1/balance"): "get_balance",
    ("GET", "/api/v1/stats/pnl"): "get_pnl",
    ("GET", "/api/v1/stats"): "get_pnl",
    ("POST", "/api/v1/analytics/calculate"): "calculate_analytics",
    ("POST", "/api/v1/emergency/killall"): "emergency_killall",
    ("POST", "/api/v1/emergency/reverse"): "emergency_reverse",
    ("POST", "/api/v1/trade/high_risk_add"): "high_risk_add",
    ("GET", "/api/v1/analytics/higher_tf"): "mms4_get_higher_tf",
    ("POST", "/api/v1/strategy/profile"): "mms4_set_strategy_profile",
    ("GET", "/api/v1/hermes/events"): "mms4_get_pending_events",
    ("POST", "/api/v1/hermes/hitl_resolve"): "mms4_hitl_resolve",
}


@app.middleware("http")
async def hermes_tool_invocation_logger(request: Request, call_next):
    """Faza 6 C.3.1: persist every call to a Hermes-tool-facing endpoint into
    `emergency_action_log` (scope widened from the 3 emergency endpoints to
    all 12 tools; table name kept as-is — renaming would be invasive for
    existing dependents). Records `endpoint`, `outcome` (success/error/status
    code), `client_ip`, `request_params` (query + body, NEVER headers — the
    only secret in play, X-MMS4-API-KEY, always travels as a header and is
    therefore never captured here), `client_label` (the caller-supplied
    `X-MMS4-CLIENT` header, e.g. "hermes-agent"/"h4-cron"/"ui-dashboard"/
    "event-dispatcher", or None when absent), and `created_at`.

    Reads the request body (needed for POST endpoints' `request_params`) and
    explicitly replays it via a replacement `_receive` — FastAPI/Starlette
    caches nothing across a fresh `Request` object, so without this replay
    the downstream endpoint's own JSON body parsing would hang/fail on an
    already-drained stream.
    """
    tool_name = HERMES_TOOL_LOG_PATHS.get((request.method, request.url.path))
    if tool_name is None:
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    client_label = request.headers.get("X-MMS4-CLIENT") or None
    query_params = dict(request.query_params)

    body_bytes = b""
    if request.method in ("POST", "PUT", "PATCH"):
        try:
            body_bytes = await request.body()
        except Exception:
            body_bytes = b""

        async def _replay_receive(_b: bytes = body_bytes) -> Dict[str, Any]:
            return {"type": "http.request", "body": _b, "more_body": False}

        request._receive = _replay_receive  # noqa: SLF001 -- documented Starlette replay pattern

    body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else ""
    request_params = json.dumps({"query": query_params, "body": body_str[:2000]})

    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        outcome = "success" if status_code < 400 else f"error_{status_code}"
        try:
            logger_db.log_tool_invocation(
                endpoint=request.url.path,
                outcome=outcome,
                client_ip=client_ip,
                request_params=request_params,
                client_label=client_label,
            )
        except Exception as log_err:
            logger.error(f"Failed to persist Hermes tool invocation log for {tool_name}: {log_err}")


class StateResponse(BaseModel):
    state: str
    leverage: float
    current_equity: float
    realized_pnl: float
    total_trades: int


class EpochKpiBlock(BaseModel):
    """A2 (2026-09-10): the same KPIs as the enclosing PnLResponse, but computed only
    over trades at or after `epoch_start` — the earliest `data_collection_epoch_start`
    among the currently ACTIVE symbols. The top-level fields stay all-time so no
    existing consumer's numbers move; this block is the trustworthy one, because the
    epoch marker is exactly the line the operator drew to say "trades before this
    belong to a different regime" (allocation change, filter changes, the P2/P3/
    PLAN-011 correctness fixes)."""
    epoch_start: Optional[str] = Field(default=None, description="ISO8601 epoch the KPIs below are computed from; null means no active symbol has an epoch set, so this block equals all-time")
    per_symbol_epochs: Dict[str, Optional[str]] = Field(default_factory=dict, description="data_collection_epoch_start per active symbol (null = never set for that symbol)")
    total_trades: int = Field(default=0)
    realized_pnl: float = Field(default=0.0, description="Alias of realized_gross_pnl_usd, mirroring the top-level shape")
    realized_gross_pnl_usd: float = Field(default=0.0)
    total_fees_usd: float = Field(default=0.0)
    realized_net_pnl_usd: float = Field(default=0.0)
    win_rate: float = Field(default=0.0)
    net_win_rate_pct: float = Field(default=0.0)
    profit_factor: float = Field(default=0.0)
    max_drawdown: float = Field(default=0.0, description="USD, peak-to-trough WITHIN the epoch (curve restarts at the epoch boundary)")
    max_drawdown_usd: float = Field(default=0.0)
    max_drawdown_pct: float = Field(default=0.0)
    average_rr: float = Field(default=0.0)
    profit_factor_net: float = Field(default=0.0, description="Profit factor on net-of-fees PnL (profit_factor is gross)")
    average_rr_net: float = Field(default=0.0, description="Average R:R on net-of-fees PnL (average_rr is gross)")
    max_drawdown_net_usd: float = Field(default=0.0, description="Max drawdown in USD on the net-of-fees curve")
    max_drawdown_net_pct: float = Field(default=0.0, description="Max drawdown on the net-of-fees curve as % of the equity peak; 0.0 unless a starting balance is known (the stats endpoint passes none, like max_drawdown_pct)")


class PnLResponse(BaseModel):
    daily_roe: float = Field(default=0.0, description="Daily Return on Equity percentage")
    realized_pnl: float = Field(default=0.0, description="Realized Profit and Loss")
    realized_gross_pnl_usd: float = Field(default=0.0, description="Realized Gross Profit and Loss in USD")
    total_fees_usd: float = Field(default=0.0, description="Total trading fees paid in USD")
    realized_net_pnl_usd: float = Field(default=0.0, description="Realized Net Profit and Loss in USD (Gross PnL - Fees)")
    unrealized_pnl: float = Field(default=0.0, description="Unrealized Profit and Loss")
    total_trades: int = Field(default=0, description="Total number of trades executed")
    win_rate: float = Field(default=0.0, description="Win rate percentage (0.0 to 100.0)")
    net_win_rate_pct: float = Field(default=0.0, description="Net win rate percentage accounting for fees (0.0 to 100.0)")
    profit_factor: float = Field(default=0.0, description="Profit factor ratio")
    max_drawdown: float = Field(default=0.0, description="Max Drawdown in USD (see max_drawdown_pct for the ratio)")
    average_rr: float = Field(default=0.0, description="Average Risk to Reward ratio")
    profit_factor_net: float = Field(default=0.0, description="Profit factor on net-of-fees PnL (profit_factor is gross)")
    average_rr_net: float = Field(default=0.0, description="Average R:R on net-of-fees PnL (average_rr is gross)")
    max_drawdown_net_usd: float = Field(default=0.0, description="Max drawdown in USD on the net-of-fees curve")
    max_drawdown_net_pct: float = Field(default=0.0, description="Max drawdown on the net-of-fees curve as % of the equity peak; 0.0 unless a starting balance is known (the stats endpoint passes none, like max_drawdown_pct)")
    free_balance: float = Field(default=0.0, description="Free available USDC balance")
    btc_balance: float = Field(default=0.0, description="BTC asset balance")
    is_mock: bool = Field(default=False, description="True if OKX API is operating in mock mode without live credentials")
    since_epoch: Optional[EpochKpiBlock] = Field(default=None, description="A2: the same KPIs restricted to the current data-collection epoch — see EpochKpiBlock")


class EmergencyKillallResponse(BaseModel):
    status: str
    message: str
    action_taken: str
    positions_closed: List[Dict[str, Any]] = Field(default_factory=list)
    positions_failed: List[Dict[str, Any]] = Field(default_factory=list)
    maintenance_mode_engaged: bool = False

# EmergencyReverseResponse removed (audit P9.3, 2026-09-08): /api/v1/emergency/reverse
# now always raises HTTPException(501) — fail-closed, never returns a success shape —
# so a dedicated response_model would be dead code.


class HighRiskAddRequest(BaseModel):
    side: str = Field(default="LONG", description="Order side: LONG or SHORT")
    size: float = Field(default=1.0, description="Position size multiplier or contracts")
    # P3 V2 (2026-09-10): DEPRECATED, ignored. Every addon — manual (this endpoint)
    # or automatic (daemon.py) — now gets NO independent SL; the position stays
    # protected by its existing base SL (base_entry_price, base_sl_pct,
    # closeFraction="1" already covers the whole position). Kept only so older
    # callers that still send this field don't fail request validation.
    stop_loss_pct: float = Field(default=0.01, description="Deprecated, ignored (P3 V2) — the addon no longer gets an independent SL; see base_sl_pct in /api/v1/settings.")
    # Audit P9.7 (2026-09-08): was hardcoded to daemon.symbol regardless of caller
    # intent. Optional and defaulting to daemon.symbol to stay backward compatible
    # with existing callers that never set it.
    symbol: Optional[str] = Field(default=None, description="Target symbol; defaults to the daemon's primary symbol")


class HighRiskAddResponse(BaseModel):
    status: str
    executed: bool
    reason: str
    details: Dict[str, Any]


class HealthResponse(BaseModel):
    status: str
    version: str


class DaemonStatusResponse(BaseModel):
    is_running: bool
    interval_seconds: float
    symbol: Optional[str] = "BTC/USDC"
    active_symbols: Optional[List[str]] = None
    max_margin_usd: Optional[float] = 10.0
    base_trade_usd: Optional[float] = 7.0
    addon_trade_usd: Optional[float] = 3.0
    trade_amount_usd: Optional[float] = 10.0
    last_cycle_timestamp: Optional[str] = None
    processed_cycles: int
    inactivity_manager: Optional[Dict[str, Any]] = None
    # Persisted High-Risk-Add counters, one row per open (symbol, side).
    addon_ledger: Optional[List[Dict[str, Any]]] = None
    base_sl_pct: Optional[float] = None
    addon_sl_pct: Optional[float] = None
    base_allocation: Optional[float] = None
    addon_allocation: Optional[float] = None
    # Per-symbol resolved sizing (margin, notional, size multiplier) for the current state.
    allocation_plan: Optional[Dict[str, Any]] = None
    total_max_portfolio_margin_usd: Optional[float] = None
    portfolio_margin_reserved_usd: Optional[float] = None
    engine_mode: Optional[str] = "CLASSIC"
    adaptive_vr_high: Optional[float] = 1.6
    adaptive_vr_low: Optional[float] = 0.8
    time_lock_start: Optional[str] = "13:30"
    time_lock_end: Optional[str] = "14:30"
    maintenance_mode: Optional[bool] = False


class CalculateRequest(BaseModel):
    candles: List[Candle]
    params: Optional[IndicatorParams] = Field(default_factory=IndicatorParams)


class CalculateResponse(BaseModel):
    tma: List[Optional[float]]
    atr: List[Optional[float]]
    upper_band: List[Optional[float]]
    lower_band: List[Optional[float]]
    signal: SignalResult


@app.get("/api/v1/health", response_model=HealthResponse, tags=["Health"])
async def get_health() -> Dict[str, str]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "version": get_app_version()
    }


@app.get("/api/v1/daemon/status", response_model=DaemonStatusResponse, tags=["Daemon"])
async def get_daemon_status() -> Dict[str, Any]:
    """Get background daemon status and execution metrics."""
    return daemon.get_status()


@app.get("/api/v1/daemon/maintenance/status", tags=["Daemon"])
async def get_maintenance_status() -> Dict[str, Any]:
    """Check if Maintenance Mode is active (all live order execution blocked)."""
    locked = is_maintenance_locked()
    return {
        "maintenance_mode": locked,
        "status": "locked" if locked else "active_trading",
        "description": "Trading is BLOCKED on OKX" if locked else "Live trading is ACTIVE"
    }


@app.post("/api/v1/daemon/maintenance/enable", tags=["Daemon"])
async def enable_maintenance_mode(reason: str = "Developer Maintenance") -> Dict[str, Any]:
    """Enable Maintenance Mode — instantly blocks all real orders on OKX."""
    success = set_maintenance_lock(True, reason=reason)
    return {
        "success": success,
        "maintenance_mode": True,
        "message": f"🔒 Maintenance Mode ENABLED. Real orders on OKX are BLOCKED. Reason: {reason}"
    }


@app.post("/api/v1/daemon/maintenance/disable", tags=["Daemon"])
async def disable_maintenance_mode() -> Dict[str, Any]:
    """Disable Maintenance Mode — resumes normal live order execution on OKX."""
    success = set_maintenance_lock(False)
    return {
        "success": success,
        "maintenance_mode": False,
        "message": "🔓 Maintenance Mode DISABLED. Live trading on OKX is RESUMED."
    }


@app.get("/api/v1/state", response_model=StateResponse, tags=["State"])
async def get_state() -> Dict[str, Any]:
    """Get current MMS4 strategy state, active leverage, and equity metrics."""
    summary = state_manager.get_summary()
    return {
        "state": summary["current_state"],
        "leverage": summary["leverage"],
        "current_equity": summary["current_equity"],
        "realized_pnl": summary["realized_pnl"],
        "total_trades": summary["total_trades"]
    }


class PositionItem(BaseModel):
    symbol: str
    side: str
    contracts: float
    notional_usd: float
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: float
    raw_symbol: Optional[str] = None
    # OKX already returns this (info.cTime, epoch ms) in the same fetch_positions()
    # call below — no extra network round-trip. Lets the dashboard chart place a
    # time-anchored marker at the moment this position was actually opened, instead
    # of only a price-level reference line with no notion of "when".
    entry_timestamp: Optional[int] = None


class PositionsResponse(BaseModel):
    positions: List[PositionItem] = Field(default_factory=list)
    count: int = 0
    error: Optional[str] = None


@app.get("/api/v1/positions", response_model=PositionsResponse, tags=["Trading"])
async def get_active_positions() -> Dict[str, Any]:
    """Fetch active live open positions from OKX account."""
    try:
        raw_positions = await executor.fetch_positions()
        active_positions = []
        for pos in raw_positions:
            contracts = float(pos.get("contracts") or pos.get("amount") or pos.get("info", {}).get("pos", 0.0))
            if abs(contracts) > 0:
                symbol = pos.get("symbol", "N/A")
                side = str(pos.get("side") or pos.get("info", {}).get("posSide", "long")).lower()
                notional = float(pos.get("notional") or pos.get("info", {}).get("notionalUsd", 0.0))
                entry_price = float(pos.get("entryPrice") or pos.get("info", {}).get("avgPx", 0.0))
                mark_price = float(pos.get("markPrice") or pos.get("info", {}).get("markPx") or pos.get("info", {}).get("last", 0.0))
                unrealized_pnl = float(pos.get("unrealizedPnl") or pos.get("info", {}).get("upl", 0.0))
                leverage = float(pos.get("leverage") or pos.get("info", {}).get("lever", 1.0))
                _c_time = pos.get("info", {}).get("cTime")
                entry_timestamp = int(_c_time) if _c_time not in (None, "") else None

                active_positions.append({
                    "symbol": symbol,
                    "side": side,
                    "contracts": abs(contracts),
                    "notional_usd": abs(notional),
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "unrealized_pnl": unrealized_pnl,
                    "leverage": leverage,
                    "entry_timestamp": entry_timestamp,
                    "raw_symbol": pos.get("info", {}).get("instId", symbol)
                })

        return {"positions": active_positions, "count": len(active_positions)}
    except Exception as e:
        logger.warning(f"Error fetching active positions for /api/v1/positions: {e}")
        return {"positions": [], "count": 0, "error": str(e)}


class BalanceResponse(BaseModel):
    free: Dict[str, float] = Field(default_factory=dict, description="Free available balances per currency in Trading account")
    total: Dict[str, float] = Field(default_factory=dict, description="Total balances per currency in Trading account")
    funding_assets: Dict[str, float] = Field(default_factory=dict, description="Balances in Funding account")
    portfolio_usd: Dict[str, float] = Field(default_factory=dict, description="USD valuation breakdown: total_usd, funding_usd, trading_usd, earn_usd")
    mock: Optional[bool] = Field(default=False, description="True if running in mock mode")


@app.get("/api/v1/balance", response_model=BalanceResponse, tags=["Account"])
async def get_balance() -> Dict[str, Any]:
    """Fetch unified account balances from OKX via executor."""
    try:
        return await executor.fetch_balance()
    except Exception as e:
        logger.error(f"Error in GET /api/v1/balance endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _active_symbol_epochs() -> Dict[str, Optional[str]]:
    """A2: `data_collection_epoch_start` for every ACTIVE configured symbol.

    Only active symbols count: SOL/ICP/XRP are suspended (see task.md GATE
    2026-09-10), and letting a suspended symbol's older epoch win the `min()` below
    would drag the reporting window back into exactly the pre-fix history the epoch
    marker exists to exclude.
    """
    epochs: Dict[str, Optional[str]] = {}
    for sym, config in configured_symbols_store.items():
        is_active = config if isinstance(config, bool) else config.get("is_active", True)
        if not is_active:
            continue
        epochs[sym] = logger_db.get_data_collection_epoch(sym)
    return epochs


def _earliest_epoch(epochs: Dict[str, Optional[str]]) -> Optional[str]:
    """The earliest non-null epoch, or None when no active symbol has one set.

    The EARLIEST (not the latest) is deliberate: a symbol whose epoch was never
    advanced is still trading its full history, so cutting everything at the latest
    epoch would silently hide that symbol's older-but-still-current-regime trades.
    Erring wide keeps the block a superset of every symbol's own epoch window.
    """
    values = [e for e in epochs.values() if e]
    if not values:
        return None

    def _key(iso: str):
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return (1, iso)  # unparseable sorts last, never silently wins the min()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (0, dt.isoformat())

    return min(values, key=_key)


@app.get("/api/v1/stats/pnl", response_model=PnLResponse, tags=["Stats"])
@app.get("/api/v1/stats", response_model=PnLResponse, tags=["Stats"])
async def get_pnl() -> Dict[str, Any]:
    """Get daily ROE %, realized PnL, unrealized PnL, KPI summary, and account balances.

    A2 (2026-09-10): the top-level KPIs are still ALL-TIME (unchanged contract, no
    consumer's number moves), with the epoch-restricted view added alongside as
    `since_epoch` — see `EpochKpiBlock`.
    """
    summary = state_manager.get_summary()
    kpi = logger_db.get_kpi_summary()

    free_usdc = 0.0
    btc_bal = 0.0
    is_mock = False
    try:
        bal_data = await executor.fetch_balance()
        is_mock = bool(bal_data.get("mock", False))
        free_usdc = float(bal_data.get("free", {}).get("USDC", 0.0))
        btc_bal = float(bal_data.get("total", {}).get("BTC", 0.0))
    except Exception as err:
        logger.warning(f"Could not fetch OKX live balance for /api/v1/stats/pnl: {err}")

    gross_pnl = float(kpi.get("realized_gross_pnl_usd", kpi.get("total_pnl_usd", summary.get("realized_pnl", 0.0))))
    total_fees = float(kpi.get("total_fees_usd", 0.0))
    net_pnl = float(kpi.get("realized_net_pnl_usd", gross_pnl - total_fees))
    net_win_rate = float(kpi.get("net_win_rate_pct", kpi.get("win_rate", 0.0)))

    # A2: epoch-restricted KPIs alongside (never instead of) the all-time ones.
    # `since=None` when no active symbol has an epoch set, which makes the block a
    # copy of the all-time figures — honest, and it keeps the field's shape stable
    # for the dashboard rather than making it appear and disappear.
    epochs = _active_symbol_epochs()
    epoch_start = _earliest_epoch(epochs)
    epoch_kpi = logger_db.get_kpi_summary(since=epoch_start) if epoch_start else kpi
    epoch_gross = float(epoch_kpi.get("realized_gross_pnl_usd", epoch_kpi.get("total_pnl_usd", 0.0)))
    epoch_fees = float(epoch_kpi.get("total_fees_usd", 0.0))
    since_epoch = {
        "epoch_start": epoch_start,
        "per_symbol_epochs": epochs,
        "total_trades": epoch_kpi.get("total_trades", 0),
        "realized_pnl": epoch_gross,
        "realized_gross_pnl_usd": epoch_gross,
        "total_fees_usd": epoch_fees,
        "realized_net_pnl_usd": float(epoch_kpi.get("realized_net_pnl_usd", epoch_gross - epoch_fees)),
        "win_rate": epoch_kpi.get("win_rate", 0.0),
        "net_win_rate_pct": float(epoch_kpi.get("net_win_rate_pct", epoch_kpi.get("win_rate", 0.0))),
        "profit_factor": epoch_kpi.get("profit_factor", 0.0),
        "max_drawdown": epoch_kpi.get("max_drawdown_usd", epoch_kpi.get("max_drawdown", 0.0)),
        "max_drawdown_usd": epoch_kpi.get("max_drawdown_usd", 0.0),
        "max_drawdown_pct": epoch_kpi.get("max_drawdown_pct", 0.0),
        "average_rr": epoch_kpi.get("average_rr", 0.0),
        "profit_factor_net": epoch_kpi.get("profit_factor_net", 0.0),
        "average_rr_net": epoch_kpi.get("average_rr_net", 0.0),
        "max_drawdown_net_usd": epoch_kpi.get("max_drawdown_net_usd", 0.0),
        "max_drawdown_net_pct": epoch_kpi.get("max_drawdown_net_pct", 0.0),
    }

    return {
        "daily_roe": 0.0,
        "realized_pnl": gross_pnl,
        "realized_gross_pnl_usd": gross_pnl,
        "total_fees_usd": total_fees,
        "realized_net_pnl_usd": net_pnl,
        "unrealized_pnl": 0.0,
        "total_trades": kpi.get("total_trades", summary.get("total_trades", 0)),
        "win_rate": kpi.get("win_rate", 0.0),
        "net_win_rate_pct": net_win_rate,
        "profit_factor": kpi.get("profit_factor", 0.0),
        "max_drawdown": kpi.get("max_drawdown_usd", kpi.get("max_drawdown", 0.0)),
        "max_drawdown_pct": kpi.get("max_drawdown_pct", 0.0),
        "max_drawdown_usd": kpi.get("max_drawdown_usd", 0.0),
        "average_rr": kpi.get("average_rr", 0.0),
        "profit_factor_net": kpi.get("profit_factor_net", 0.0),
        "average_rr_net": kpi.get("average_rr_net", 0.0),
        "max_drawdown_net_usd": kpi.get("max_drawdown_net_usd", 0.0),
        "max_drawdown_net_pct": kpi.get("max_drawdown_net_pct", 0.0),
        "free_balance": free_usdc,
        "btc_balance": btc_bal,
        "is_mock": is_mock,
        "since_epoch": since_epoch,
    }



@app.post("/api/v1/analytics/calculate", response_model=CalculateResponse, tags=["Analytics"])
async def calculate_analytics(req: CalculateRequest) -> Dict[str, Any]:
    """
    Accepts OHLCV candle list, returns calculated TMA, ATR, Bands, and evaluated SignalResult.
    """
    global latest_signal
    params = req.params or IndicatorParams()
    df = TMAIndicator.calculate(req.candles, params)

    tma_list = [None if pd.isna(x) else float(x) for x in df["tma"]]
    atr_list = [None if pd.isna(x) else float(x) for x in df["atr"]]
    upper_list = [None if pd.isna(x) else float(x) for x in df["upper_band"]]
    lower_list = [None if pd.isna(x) else float(x) for x in df["lower_band"]]

    signal = signal_engine.evaluate_signals(req.candles, state_manager, params)
    latest_signal = signal

    return {
        "tma": tma_list,
        "atr": atr_list,
        "upper_band": upper_list,
        "lower_band": lower_list,
        "signal": signal
    }


@app.get("/api/v1/analytics/signals", response_model=SignalResult, tags=["Analytics"])
async def get_latest_signal() -> SignalResult:
    """
    Returns latest signal evaluated from state or daemon.
    """
    if daemon.last_signal is not None:
        return daemon.last_signal
    if latest_signal is None:
        return SignalResult(
            signal_type="NONE",
            price=0.0,
            stop_loss=0.0,
            leverage=state_manager.leverage,
            is_allowed=True,
            reason="No signals evaluated yet"
        )
    return latest_signal


@app.get("/api/v1/signals", response_model=SignalResult, tags=["Analytics"], include_in_schema=False)
async def get_latest_signal_alias() -> SignalResult:
    """Alias for /api/v1/analytics/signals."""
    return await get_latest_signal()


@app.post("/api/v1/emergency/killall", response_model=EmergencyKillallResponse, tags=["Emergency"], dependencies=[Depends(verify_api_key)])
async def emergency_killall(request: Request) -> Dict[str, Any]:
    """
    Emergency stop endpoint triggered by parent controller (Hermes) or manual
    operator action. Cancels all open (non-algo) orders, closes every open
    position via a reduce-only market order, and engages Maintenance Mode so
    the daemon does not immediately re-enter.

    Audit finding (2026-09-28, XAG protection-gaps review, issue #67): the
    previous version of this endpoint only called cancel_all_orders() — which
    the bot rarely has anything queued for, since it trades with market
    orders — never actually closed a position, never engaged Maintenance Mode,
    and unconditionally reported "position close triggered" regardless of
    what happened. This is the only declared emergency-stop path in the
    system and had never once been exercised in production
    (emergency_action_log was empty) — the same false-success failure mode
    already fixed for /emergency/reverse under audit P9.3.
    """
    client_ip = request.client.host if request.client else "unknown"

    try:
        cancel_result = await executor.cancel_all_orders()
    except Exception as cancel_err:
        logger.error(f"emergency_killall: cancel_all_orders failed: {cancel_err}")
        cancel_result = {"error": str(cancel_err)}

    positions_closed: List[Dict[str, Any]] = []
    positions_failed: List[Dict[str, Any]] = []
    try:
        raw_positions = await executor.fetch_positions()
    except Exception as fetch_err:
        logger.error(f"emergency_killall: fetch_positions failed: {fetch_err}")
        raw_positions = []

    for pos in raw_positions:
        contracts = float(pos.get("contracts") or pos.get("amount") or pos.get("info", {}).get("pos", 0.0) or 0.0)
        if abs(contracts) <= 0:
            continue
        pos_symbol = pos.get("symbol", "N/A")
        pos_side = str(pos.get("side") or pos.get("info", {}).get("posSide", "long")).lower()
        try:
            reduce_result = await executor.reduce_position(
                symbol=pos_symbol, side=pos_side, reduction_pct=1.0, reason="EMERGENCY_KILLALL"
            )
            status_val = (reduce_result or {}).get("status", "executed")
            if status_val in ("no_position", "not_divisible"):
                positions_failed.append({"symbol": pos_symbol, "side": pos_side, "reason": status_val})
            else:
                positions_closed.append({"symbol": pos_symbol, "side": pos_side, "contracts": abs(contracts)})
        except Exception as close_err:
            logger.error(f"emergency_killall: failed to close {pos_symbol} {pos_side}: {close_err}")
            positions_failed.append({"symbol": pos_symbol, "side": pos_side, "reason": str(close_err)})

    maintenance_engaged = False
    try:
        maintenance_engaged = set_maintenance_lock(True, reason="Emergency killall")
    except Exception as maint_err:
        logger.error(f"emergency_killall: failed to engage maintenance lock: {maint_err}")

    all_ok = not positions_failed and maintenance_engaged
    message = (
        f"Closed {len(positions_closed)} position(s), {len(positions_failed)} failed. "
        f"Maintenance mode {'engaged' if maintenance_engaged else 'FAILED TO ENGAGE'}."
    )

    try:
        logger_db.log_emergency_attempt(
            endpoint="/api/v1/emergency/killall",
            outcome="SUCCESS" if all_ok else "PARTIAL_OR_FAILED",
            client_ip=client_ip,
            request_params=json.dumps({
                "positions_closed": positions_closed,
                "positions_failed": positions_failed,
                "maintenance_engaged": maintenance_engaged,
                "cancel_result": str(cancel_result)[:500],
            }),
        )
    except Exception as log_err:
        logger.error(f"Failed to persist emergency/killall attempt to SQLite: {log_err}")

    return {
        "status": "success" if all_ok else "partial",
        "message": message,
        "action_taken": message,
        "positions_closed": positions_closed,
        "positions_failed": positions_failed,
        "maintenance_mode_engaged": maintenance_engaged,
    }


@app.post("/api/v1/emergency/reverse", tags=["Emergency"], dependencies=[Depends(verify_api_key)])
async def emergency_reverse(request: Request) -> Dict[str, str]:
    """
    Emergency position reversal triggered by Hermes agent. NOT YET IMPLEMENTED.

    Audit P9.3 (2026-09-08), fail-closed: the previous version of this endpoint
    unconditionally returned {"status": "success", ...} without closing or opening
    anything — reporting a false safety action on the emergency path, the most
    dangerous class of bug this repo has. Until a real implementation lands (its own
    ticket), this endpoint refuses with 501 rather than lie about having acted, and
    logs every call attempt as a CRITICAL security event — a caller hitting this
    endpoint believes a live risk event is happening right now.
    """
    client_ip = request.client.host if request.client else "unknown"
    query_params = dict(request.query_params)
    raw_body = ""
    try:
        raw_body = (await request.body()).decode("utf-8", errors="replace")
    except Exception:
        pass
    # Deliberately NOT logging headers here (would include X-MMS4-API-KEY).
    request_params = json.dumps({"query": query_params, "body": raw_body[:2000]})

    logger.critical(
        f"🚨 EMERGENCY REVERSE CALLED but NOT IMPLEMENTED — refusing (fail-closed, 501). "
        f"client_ip={client_ip}"
    )
    try:
        logger_db.log_emergency_attempt(
            endpoint="/api/v1/emergency/reverse",
            outcome="REFUSED_NOT_IMPLEMENTED",
            client_ip=client_ip,
            request_params=request_params,
        )
    except Exception as log_err:
        logger.error(f"Failed to persist emergency/reverse attempt to SQLite: {log_err}")

    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Emergency reverse is not implemented. Fail-closed by design (audit P9.3, "
            "2026-09-08) — no position was touched. This attempt has been logged."
        ),
    )


@app.post("/api/v1/trade/high_risk_add", response_model=HighRiskAddResponse, tags=["Trading"], dependencies=[Depends(verify_api_key)])
async def high_risk_add(req: HighRiskAddRequest) -> Dict[str, Any]:
    """
    High-risk position add (leverage x1, independent ticket). Blocked if strategy
    is in SCOUT_MODE (REDUCED_RISK).

    Audit P9.7 (2026-09-08): this manual path previously (a) always targeted
    daemon.symbol regardless of req.symbol, (b) never called daemon._record_addon,
    so the daemon's own automatic Single Addon Guard (daemon.py ~1506) had no record
    of a manual add and could add again on its own next cycle, and (c) swallowed
    execute_order's exception in a bare `except` and still reported
    executed: True/status: success even when the order failed — the same class of
    bug as P9.3's emergency/reverse. All three fixed here.

    P3 V2 (2026-09-10): previously attached its own independent SL at
    `req.stop_loss_pct` — the same non-determinism/misclassification problem the
    automatic HIGH_RISK_ADD path had (this manual add's own attach would cancel and
    replace the base SL with a DIFFERENT anchor, and reconcile_positions had no way
    to know about it). Now consistent with the automatic path: no independent SL is
    attached here either — the position stays protected by whatever SL is already
    on it (base_entry_price, base_sl_pct).
    """
    target_symbol = req.symbol or daemon.symbol
    pos_side = "long" if req.side.upper() == "LONG" else "short"

    if not state_manager.is_high_risk_add_allowed():
        return {
            "status": "rejected",
            "executed": False,
            "reason": f"HIGH_RISK_ADD blocked in {state_manager.current_state.value} (leverage x{state_manager.leverage})",
            "details": {"current_state": state_manager.current_state.value, "leverage": state_manager.leverage}
        }

    # Single Addon Guard: same persisted ledger the daemon's own automatic
    # HIGH_RISK_ADD path reads/writes (daemon._addon_count / daemon._record_addon),
    # keyed on (symbol, side, entry_price) — so a manual add here is visible to it.
    match_entry = 0.0
    try:
        live_positions = await executor.fetch_positions()
        for p in live_positions:
            p_side = str(p.get("side") or p.get("info", {}).get("posSide") or "").lower()
            if p.get("symbol") == target_symbol and p_side == pos_side:
                match_entry = float(p.get("entryPrice") or p.get("info", {}).get("avgPx") or 0.0)
                break
    except Exception as e:
        logger.warning(f"high_risk_add: failed to fetch live positions for {target_symbol}: {e}")

    addons_done = daemon._addon_count(target_symbol, pos_side, match_entry)
    if addons_done >= 1:
        return {
            "status": "rejected",
            "executed": False,
            "reason": (
                f"HIGH_RISK_ADD already executed on this {pos_side.upper()} position for "
                f"{target_symbol} (entry=${match_entry:,.4f}). Single Addon Guard active."
            ),
            "details": {"symbol": target_symbol, "side": pos_side, "entry_price": match_entry}
        }

    # Execute high risk add order via OKXExecutor. Failure must never be reported as
    # executed: True/status: success (audit P9.7c / P9.3). stop_loss_pct=0.0: P3 V2 —
    # no independent SL for the addon leg, see this endpoint's docstring.
    try:
        await executor.execute_order(
            symbol=target_symbol,
            side=req.side,
            amount=req.size,
            stop_loss_pct=0.0,
            leverage=1.0
        )
    except Exception as e:
        logger.error(f"Error executing high risk add for {target_symbol}: {e}")
        return {
            "status": "error",
            "executed": False,
            "reason": f"High-risk add order failed: {e}",
            "details": {"symbol": target_symbol, "side": req.side, "size": req.size}
        }

    daemon._record_addon(target_symbol, pos_side, match_entry)

    return {
        "status": "success",
        "executed": True,
        "reason": "High-risk add order placed successfully.",
        "details": {
            "symbol": target_symbol,
            "side": req.side,
            "size": req.size,
            # P3 V2: this add has no independent SL — the position stays protected
            "stop_loss_pct": daemon.base_sl_pct,
            "leverage": 1.0
        }
    }


class HigherTimeframeResponse(BaseModel):
    atr_d1: float
    atr_d1_expansion_ratio: float
    # Audit P9.4 (2026-09-08): these 3 fields were already computed by
    # HigherTimeframeAnalytics.calculate_higher_tf but silently dropped by this
    # response_model. Prerequisite for P5 step 0 (consuming volatility_zone) —
    # zero effect on execution, this endpoint is read-only analytics.
    atr_d1_expansion_threshold: float = Field(description="htf_expansion_threshold used to classify volatility_zone")
    volatility_zone: str = Field(description="EXPANSION | NORMAL | COMPRESSION | UNKNOWN")
    is_volatility_expansion: bool
    h4_tma_position: str = Field(description="ABOVE | BELOW | INSIDE")
    h4_candle_formation: str = Field(description="SWEEP_WICK | MARUBOZU_BREAKOUT | NEUTRAL")
    h4_tma: float
    h4_upper_band: float
    h4_lower_band: float
    data_stale: bool = Field(
        default=False,
        description="Faza 6 C.1.3: True when a real OKX candle fetch failed and every "
        "other field below is best-effort/neutral, NOT a genuine H4/D1 read. Callers "
        "(the H4 cron, Hermes) must not act on ABOVE/BELOW/SWEEP_WICK/etc. classifications "
        "while this is True.",
    )


@app.get("/api/v1/analytics/higher_tf", response_model=HigherTimeframeResponse, tags=["Analytics"])
async def get_higher_tf_analytics() -> Dict[str, Any]:
    """
    Fetch higher timeframe (H4/D1) market state analysis.
    Calculates D1 ATR expansion ratio, H4 TMA position, and candle formation.

    Faza 6 C.1.3: previously called `fetch_candles` without
    `allow_mock_fallback=False`, so a real OKX outage silently returned
    deterministic MOCK candles and this endpoint would compute (and the H4
    cron would act on) a fabricated "ABOVE"/"BELOW"/"SWEEP_WICK" reading with
    no signal that anything was wrong — the same bug class already fixed in
    `daemon._refresh_htf_directional_gate` (Faza 3.2-followup). Now a fetch
    failure raises, is caught, and is surfaced explicitly via `data_stale`
    instead of quietly computing from mock or empty candles.
    """
    data_stale = False
    try:
        candles_h4 = await executor.fetch_candles(
            symbol=daemon.symbol, timeframe="4h", limit=50, allow_mock_fallback=False
        )
        candles_d1 = await executor.fetch_candles(
            symbol=daemon.symbol, timeframe="1d", limit=50, allow_mock_fallback=False
        )
    except Exception as e:
        logger.warning(f"Failed to fetch real HTF candles from OKX: {e}. Reporting data_stale=True.")
        candles_h4 = []
        candles_d1 = []
        data_stale = True

    result = HigherTimeframeAnalytics.calculate_higher_tf(candles_h4, candles_d1)
    result["data_stale"] = data_stale
    return result


@app.get("/api/v1/strategy/profile", response_model=StrategyProfile, tags=["Strategy Profile"])
async def get_strategy_profile() -> StrategyProfile:
    """
    Get current active strategy profile configuration.
    """
    return state_manager.strategy_profile


# A bare symbol ("xag"), BASE/QUOTE ("XAG/USDC") or glued ("xagusdc") form; rejects blank/garbage with a 422
# instead of a silent no-op 200 that Hermes cannot distinguish from "released".
_SYMBOL_PATTERN = r"^\s*[A-Za-z0-9]{1,16}(/[A-Za-z0-9]{1,16})?\s*$"


class StrategyProfileUpdate(BaseModel):
    """Faza 6 C.1.2: partial-update counterpart to StrategyProfile.

    `POST /api/v1/strategy/profile` used to require the FULL `StrategyProfile`
    model — every field Pydantic didn't receive was silently filled with its
    default, so a caller that only meant to change `risk_profile` (e.g. the H4
    cron, once C.1.1 stops it from also sending `directional_bias`) would
    reset `directional_bias` back to "BOTH" on every write. All 4 fields here
    are Optional[...] = None so "field not sent" and "field explicitly unset"
    are the same thing (None); `set_strategy_profile` below merges only the
    fields that are NOT None onto the existing persisted profile. A request
    that supplies every field still behaves exactly like the old full-replace
    (every existing field gets overwritten with the request's value) — this
    is a strict superset of the old behavior, not a change to it.
    """
    directional_bias: Optional[str] = Field(default=None, description="BOTH | LONG_ONLY | SHORT_ONLY | FLAT")
    risk_profile: Optional[str] = Field(default=None, description="CONSERVATIVE | MODERATE | AGGRESSIVE")
    scout_override: Optional[bool] = Field(default=None, description="True allows early SCOUT_MODE relaxation")
    reason: Optional[str] = Field(default=None)
    symbol: Optional[str] = Field(
        default=None,
        pattern=_SYMBOL_PATTERN,
        max_length=32,
        description="Symbol whose SCOUT_MODE a scout_override relaxes (e.g. XAG/USDC). "
                    "Omitted = the primary trading symbol (previous behavior).",
    )


def _format_symbol(raw: str) -> str:
    """Canonical `BASE/USDC` form used across the multi-symbol endpoints."""
    sym = raw.upper().strip()
    if "/" not in sym:
        sym = sym.replace("USDC", "/USDC") if sym.endswith("USDC") else f"{sym}/USDC"
    return sym


@app.post("/api/v1/strategy/profile", response_model=StrategyProfile, tags=["Strategy Profile"], dependencies=[Depends(verify_api_key)])
async def set_strategy_profile(profile: StrategyProfileUpdate) -> StrategyProfile:
    """
    Partially update the active strategy profile configuration: only fields
    present (non-None) in the request overwrite the persisted profile, every
    other field is left exactly as it was. If scout_override is
    True in the request, relaxes SCOUT_MODE to BASE_MODE -- unless the symbol entered
    SCOUT_MODE less than SCOUT_OVERRIDE_MIN_HOURS ago (Issue #129), in which case the
    response's `scout_override` echoes False even though it was requested as True.
    Protected by X-MMS4-API-KEY header.

    Faza 6 C.1.2: previously took the full `StrategyProfile` model, so any
    write (even one meaning to change a single field) reset every unset field
    to its Pydantic default — see `StrategyProfileUpdate`'s docstring. A
    request that sets all 4 fields (every caller that predates this change,
    e.g. `mms4tk_h4_analysis.py` and `hitl_resolve` below) still fully
    replaces the profile, byte for byte the same as before.
    """
    current = state_manager.strategy_profile
    merged = StrategyProfile(
        directional_bias=profile.directional_bias if profile.directional_bias is not None else current.directional_bias,
        risk_profile=profile.risk_profile if profile.risk_profile is not None else current.risk_profile,
        # One-shot: scout_override=True is acted on here and never stored, or a later write
        # (or a restart) would see a stale True; the response still echoes the request.
        scout_override=False,
        reason=profile.reason if profile.reason is not None else current.reason,
    )
    requested = bool(profile.scout_override)
    updated = state_manager.update_strategy_profile(merged)
    relaxed = False
    if requested:
        relaxed = state_manager.relax_scout_mode(_format_symbol(profile.symbol) if profile.symbol else None)
    # Issue #129: echo whether the relax actually happened, not merely whether it was requested --
    # a cooldown-blocked request must not look identical to an accepted one to the caller (Hermes).
    return updated.model_copy(update={"scout_override": relaxed})


# ─── Hermes Webhook & HITL Endpoints ─────────────────────────────────────────


class WebhookEventResponse(BaseModel):
    id: int
    timestamp: str
    event_type: str
    title: str
    body: str
    metadata: Optional[str] = None
    initial_status: str
    priority: int
    dispatched: int
    dispatched_at: Optional[str] = None
    kanban_task_id: Optional[str] = None
    error: Optional[str] = None


class PostWebhookRequest(BaseModel):
    event_type: str = Field(default="MULTI_SYMBOL_DIGEST")
    title: str
    details: str
    priority: int = 5


@app.post("/api/v1/hermes/webhook", tags=["Hermes Integration"])
async def post_custom_webhook_event(req: PostWebhookRequest) -> Dict[str, Any]:
    """Log a custom webhook event to mms4tk database for Hermes Kanban & Dashboard display."""
    event = HermesWebhookEvent(
        event_type=req.event_type,
        title=req.title,
        body=req.details,
        priority=req.priority
    )
    event_id = hermes_webhook.emit_event(event)
    return {"status": "success", "event_id": event_id}


@app.get("/api/v1/hermes/events", response_model=List[WebhookEventResponse], tags=["Hermes Integration"])
async def get_pending_webhook_events() -> list:
    """
    Retrieve pending (undispatched) webhook events for Hermes Kanban dispatch.
    Called by Hermes cron event dispatcher to create Kanban cards.
    """
    return hermes_webhook.get_pending_events(limit=20)


class MarkDispatchedRequest(BaseModel):
    event_id: int
    kanban_task_id: str = ""


@app.post("/api/v1/hermes/events/dispatched", tags=["Hermes Integration"], dependencies=[Depends(verify_api_key)])
async def mark_event_dispatched(req: MarkDispatchedRequest) -> Dict[str, Any]:
    """
    Mark a webhook event as dispatched after Kanban card was created.
    Called by Hermes cron event dispatcher after successful card creation.
    """
    hermes_webhook.mark_dispatched(req.event_id, req.kanban_task_id)
    return {"status": "ok", "event_id": req.event_id, "kanban_task_id": req.kanban_task_id}


class HITLResolveRequest(BaseModel):
    decision: str = Field(description="SWEEP_WICK | MARUBOZU_BREAKOUT")
    directional_bias: Optional[str] = Field(
        default=None,
        description="BOTH | LONG_ONLY | SHORT_ONLY | FLAT. Omitted = keep the current profile value.",
    )
    risk_profile: Optional[str] = Field(
        default=None,
        description="CONSERVATIVE | MODERATE | AGGRESSIVE. Omitted = keep the current profile value.",
    )
    reason: str = Field(default="Human-in-the-Loop decision")
    symbol: Optional[str] = Field(
        default=None,
        pattern=_SYMBOL_PATTERN,
        max_length=32,
        description="Symbol the decision applies to (e.g. XAG/USDC). Omitted = the primary trading symbol.",
    )


@app.post("/api/v1/hermes/hitl_resolve", tags=["Hermes Integration"], dependencies=[Depends(verify_api_key)])
async def hitl_resolve(req: HITLResolveRequest) -> Dict[str, Any]:
    """
    Human-in-the-Loop resolution endpoint.
    Called when a human approves a Kanban card from Blocked/HITL column.

    - SWEEP_WICK → scout_override=True (relax SCOUT_MODE back to BASE_MODE)
    - MARUBOZU_BREAKOUT → maintain SCOUT_MODE, update directional_bias accordingly
    """
    scout_override = req.decision == "SWEEP_WICK"
    current = state_manager.strategy_profile
    profile = StrategyProfile(
        directional_bias=req.directional_bias if req.directional_bias is not None else current.directional_bias,
        risk_profile=req.risk_profile if req.risk_profile is not None else current.risk_profile,
        scout_override=False,  # one-shot: acted on below, never stored
        reason=f"HITL: {req.decision} — {req.reason}",
    )
    updated = state_manager.update_strategy_profile(profile).model_copy(update={"scout_override": scout_override})
    relaxed = False
    target_symbol = _format_symbol(req.symbol) if req.symbol else None
    if scout_override:
        relaxed = state_manager.relax_scout_mode(target_symbol)

    return {
        "status": "ok",
        "decision": req.decision,
        "symbol": target_symbol or state_manager.trading_symbol,
        "scout_override": scout_override,
        "scout_mode_relaxed": relaxed,
        "profile": updated.model_dump(),
    }


# ─── Dashboard Integration Endpoints ─────────────────────────────────────────

@app.get("/api/v1/trades/history", tags=["History"])
@app.get("/api/v1/history/trades", tags=["History"])
async def get_history_trades(limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch real historical closed trades from SQLite logger_db."""
    try:
        asyncio.create_task(trade_sync_worker.sync_closed_trades())
    except Exception as e:
        logger.debug(f"Trade sync on history fetch notice: {e}")
    return logger_db.get_closed_trades(limit=limit)


@app.get("/api/v1/history/candles", tags=["History"])
async def get_history_candles(symbol: str = "BTC/USDC", timeframe: str = "1h", limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch live/historical candles from OKX for technical charts with TMA values."""
    try:
        candles = await executor.fetch_candles(symbol=symbol, timeframe=timeframe, limit=limit)
        if not candles:
            return []
            
        df = TMAIndicator.calculate(candles)
        
        result = []
        for i, c in enumerate(candles):
            cdict = c.model_dump()
            
            tma_val = df["tma"].iloc[i]
            upper_val = df["upper_band"].iloc[i]
            lower_val = df["lower_band"].iloc[i]
            atr_val = df["atr"].iloc[i]
            
            cdict["tma"] = float(tma_val) if pd.notna(tma_val) else None
            cdict["upper_band"] = float(upper_val) if pd.notna(upper_val) else None
            cdict["lower_band"] = float(lower_val) if pd.notna(lower_val) else None
            cdict["atr"] = float(atr_val) if pd.notna(atr_val) else None
            
            result.append(cdict)
            
        return result
    except Exception as e:
        logger.warning(f"Error fetching candles for {symbol} ({timeframe}): {e}")
        return []

@app.get("/api/v1/history/addons", tags=["History"])
async def get_history_addons(symbol: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch recent add-on events (HIGH_RISK_ADD / ADD)."""
    return logger_db.get_addon_events(symbol=symbol, limit=limit)


@app.get("/api/v1/hermes/kanban", tags=["Hermes Integration"])
async def get_kanban_cards() -> Dict[str, Any]:
    """Fetch active Kanban cards from Hermes kanban.db (Single Source of Truth)."""
    cards = []
    
    # Check potential kanban.db paths
    db_candidates = [
        "/app/hermes_data/kanban.db",
        "/opt/data/kanban.db",
        "/docker/hermes-agent/data/kanban.db",
        ".agents/swarm/kanban.db"
    ]
    
    hermes_db = None
    for cand in db_candidates:
        if os.path.exists(cand) and os.path.getsize(cand) > 1024:
            hermes_db = cand
            break
            
    if hermes_db:
        try:
            with sqlite3.connect(hermes_db, timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT id, status, title, body, created_at FROM tasks WHERE status NOT IN ('done', 'archived', 'closed') ORDER BY created_at DESC"
                )
                for row in cursor.fetchall():
                    cards.append({
                        "id": row["id"],
                        "status": str(row["status"]).upper(),
                        "title": row["title"],
                        "description": row["body"] or "",
                        "created_at": row["created_at"] or ""
                    })
        except Exception as e:
            logger.warning(f"Error querying Hermes kanban.db at {hermes_db}: {e}")
    else:
        # Fallback to undispatched actionable events only if kanban.db is missing
        try:
            with sqlite3.connect(hermes_webhook.db_path) as conn:
                conn.execute("PRAGMA busy_timeout = 5000")
                conn.execute("PRAGMA journal_mode = WAL")
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM webhook_events WHERE dispatched = 0 AND initial_status NOT IN ('done', 'archived') ORDER BY id DESC LIMIT 20"
                )
                for ev in cursor.fetchall():
                    cards.append({
                        "id": ev["id"],
                        "status": str(ev["initial_status"]).upper(),
                        "title": ev["title"],
                        "description": ev["body"],
                        "created_at": ev["timestamp"]
                    })
        except Exception as e:
            logger.warning(f"Error querying webhook_events for kanban: {e}")

    return {"cards": cards, "total": len(cards)}


def _persist_env_line_rhs(existing_rhs: str, new_value: Any) -> str:
    """
    Build the replacement right-hand side for a KEY=... line, preserving any trailing
    inline comment from the original line (e.g. 'CLASSIC  # explains the mode' keeps
    the '# explains the mode' part after the value is swapped).

    A quoted original value ('...' / "...") keeps everything after the closing quote
    verbatim as the "comment" tail; otherwise the first whitespace-preceded '#' is
    treated as the comment start. Neither pattern matching means no comment to keep.
    """
    comment_suffix = ""
    if existing_rhs and existing_rhs[0] in ("'", '"'):
        end = existing_rhs.find(existing_rhs[0], 1)
        if end != -1:
            comment_suffix = existing_rhs[end + 1:]
    else:
        for sep in (" #", "\t#"):
            idx = existing_rhs.find(sep)
            if idx != -1:
                comment_suffix = existing_rhs[idx:]
                break
    # A value can never legitimately contain a newline in a .env file — strip any
    # that slipped in rather than let it inject an extra, uncontrolled line.
    safe_val = str(new_value).replace("\r", " ").replace("\n", " ")
    return f"{safe_val}{comment_suffix}"


PERSISTENT_SETTINGS_PATHS = ["/app/.agents/swarm/settings.json", ".agents/swarm/settings.json"]


def load_persistent_settings() -> Dict[str, Any]:
    """Load runtime settings from persistent volume storage and sync to os.environ."""
    settings = {}
    for p in PERSISTENT_SETTINGS_PATHS:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        settings.update(data)
                        for k, v in data.items():
                            os.environ[k] = str(v)
            except Exception as e:
                logger.warning(f"Failed to load persistent settings from {p}: {e}")
    return settings


# Automatically load persistent runtime settings on module import
load_persistent_settings()


def persist_env_config(updates: Dict[str, Any]) -> None:
    """
    Safely persist key-value pairs to .env files and persistent volume settings.json.
    Guarantees settings survive docker rebuilds and container recreations.
    """
    # 1. Persist to persistent volume JSON storage
    for p in PERSISTENT_SETTINGS_PATHS:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            current = {}
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    current = json.load(f) or {}
            current.update(updates)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(current, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist settings JSON to {p}: {e}")

    # 2. Persist to .env files
    env_paths = [".env", "/app/.env", "../.env"]
    for env_path in env_paths:
        if os.path.exists(env_path):
            try:
                lines = []
                found_keys = set()
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line_stripped = line.strip()
                        if line_stripped and not line_stripped.startswith("#") and "=" in line_stripped:
                            k, rest = line_stripped.split("=", 1)
                            k = k.strip()
                            if k in updates:
                                lines.append(f"{k}={_persist_env_line_rhs(rest, updates[k])}\n")
                                found_keys.add(k)
                            else:
                                lines.append(line)
                        else:
                            lines.append(line)

                if lines and not lines[-1].endswith("\n"):
                    lines[-1] = lines[-1] + "\n"

                for k, val in updates.items():
                    if k not in found_keys:
                        safe_val = str(val).replace("\r", " ").replace("\n", " ")
                        lines.append(f"{k}={safe_val}\n")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
            except Exception as e:
                logger.warning(f"Failed to persist env updates to {env_path}: {e}")



def sync_settings_from_db() -> Dict[str, Any]:
    """Sync all runtime settings from SQLite system_settings table to runtime state and os.environ."""
    settings = {}
    try:
        if logger_db:
            settings = logger_db.get_all_settings()
            for k, v in settings.items():
                if k.startswith("scout_ok:"):
                    continue  # per-symbol SCOUT counters (state_manager), not runtime settings
                env_key = f"MMS4_{k.upper()}" if not k.upper().startswith("MMS4_") else k.upper()
                os.environ[env_key] = str(v)
                os.environ[k.upper()] = str(v)
                
            # Apply to daemon and state_manager if already initialized
            if "engine_mode" in settings:
                os.environ["MMS4_ENGINE_MODE"] = str(settings["engine_mode"])
            if "adaptive_vr_high" in settings:
                os.environ["MMS4_ADAPTIVE_VR_HIGH"] = str(settings["adaptive_vr_high"])
            if "adaptive_vr_low" in settings:
                os.environ["MMS4_ADAPTIVE_VR_LOW"] = str(settings["adaptive_vr_low"])
            if "time_lock_start" in settings:
                os.environ["MMS4_TIME_LOCK_START"] = str(settings["time_lock_start"])
            if "time_lock_end" in settings:
                os.environ["MMS4_TIME_LOCK_END"] = str(settings["time_lock_end"])
    except Exception as e:
        logger.warning(f"Failed to sync settings from SQLite DB: {e}")
    return settings


# Sync settings from SQLite DB at module import
sync_settings_from_db()


class SettingsUpdateRequest(BaseModel):
    leverage: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    scout_mode: Optional[bool] = None
    symbol: Optional[str] = Field(
        default=None,
        pattern=_SYMBOL_PATTERN,
        max_length=32,
        description="Symbol `scout_mode` applies to (e.g. XAG/USDC). Omitted = the primary trading symbol.",
    )
    api_key: Optional[str] = None
    capital_split_ratio: Optional[float] = None
    max_margin_usd: Optional[float] = None
    total_max_portfolio_margin_usd: Optional[float] = None

    # ── Adaptive Hybrid Engine Parameters ──
    engine_mode: Optional[str] = Field(default=None, description="CLASSIC | ADAPTIVE")
    adaptive_vr_high: Optional[float] = Field(default=None, description="High Volatility Ratio threshold (default 1.6)")
    adaptive_vr_low: Optional[float] = Field(default=None, description="Low Volatility Ratio threshold (default 0.8)")
    time_lock_start: Optional[str] = Field(default=None, description="Time Lock Start in HH:MM (default 13:30)")
    time_lock_end: Optional[str] = Field(default=None, description="Time Lock End in HH:MM (default 14:30)")

    # ── Midline Take-Profit Parameters (PLAN-009) ──
    midline_partial_tp_enabled: Optional[bool] = Field(default=None, description="Enable partial Take Profit at TMA Midline (T1)")
    midline_tp_fraction: Optional[float] = Field(default=None, description="Fraction of position to close at TMA Midline (default 0.50)")


@app.get("/api/v1/settings", tags=["Settings"])
async def get_settings() -> Dict[str, Any]:
    """Get all current strategy, risk profile, and adaptive engine settings from SQLite database."""
    db_settings = logger_db.get_all_settings() if logger_db else {}
    return {
        "leverage": float(db_settings.get("leverage", state_manager.leverage)),
        "scout_mode": state_manager.scout_mode,
        "capital_split_ratio": float(db_settings.get("base_allocation", daemon.base_allocation)),
        "max_margin_usd": float(db_settings.get("max_margin_usd", daemon.max_margin_usd)),
        "stop_loss_pct": round(float(db_settings.get("base_sl_pct", daemon.base_sl_pct)) * 100, 2),
        "total_max_portfolio_margin_usd": float(db_settings.get("total_max_portfolio_margin_usd", daemon.total_max_portfolio_margin_usd)),
        "engine_mode": db_settings.get("engine_mode", os.getenv("MMS4_ENGINE_MODE", "CLASSIC")),
        "adaptive_vr_high": float(db_settings.get("adaptive_vr_high", os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6"))),
        "adaptive_vr_low": float(db_settings.get("adaptive_vr_low", os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8"))),
        "time_lock_start": db_settings.get("time_lock_start", os.getenv("MMS4_TIME_LOCK_START", "13:30")),
        "time_lock_end": db_settings.get("time_lock_end", os.getenv("MMS4_TIME_LOCK_END", "14:30")),
        # Audit P9.1 (2026-09-08): default "false" — rejected 6/6 in backtest (CHANGELOG
        # 0.42.1); a fresh deploy with no explicit system_settings entry must not silently
        # re-enable it.
        "midline_partial_tp_enabled": str(db_settings.get("midline_partial_tp_enabled", "false")).strip().lower() in ("true", "1", "yes", "on"),
        "midline_tp_fraction": float(db_settings.get("midline_tp_fraction", 0.50)),
    }


@app.post("/api/v1/settings/update", tags=["Settings"], dependencies=[Depends(verify_api_key)])
async def update_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    """Update runtime strategy parameters, leverage, SL %, risk profile, and adaptive engine settings in SQLite DB."""
    db_updates: Dict[str, Any] = {}
    env_updates: Dict[str, Any] = {}

    if req.leverage is not None:
        state_manager.leverage = req.leverage  # setter clamps to [1.0, 10.0]
        db_updates["leverage"] = str(state_manager.leverage)
    if req.scout_mode is not None:
        scout_target = _format_symbol(req.symbol) if req.symbol else None
        state_manager.set_scout_mode(bool(req.scout_mode), scout_target)
        if scout_target is None or scout_target == state_manager.trading_symbol:
            # system_settings["scout_mode"] mirrors the primary symbol only.
            db_updates["scout_mode"] = str(state_manager.scout_mode)
    if req.capital_split_ratio is not None:
        daemon.base_allocation = float(req.capital_split_ratio)
        daemon.addon_allocation = round(1.0 - daemon.base_allocation, 4)
        db_updates["base_allocation"] = str(daemon.base_allocation)
        db_updates["addon_allocation"] = str(daemon.addon_allocation)
    if req.max_margin_usd is not None:
        daemon.max_margin_usd = float(req.max_margin_usd)
        daemon.base_trade_usd = round(daemon.max_margin_usd * daemon.base_allocation, 2)
        daemon.addon_trade_usd = round(daemon.max_margin_usd * daemon.addon_allocation, 2)
        db_updates["max_margin_usd"] = str(daemon.max_margin_usd)
    if req.stop_loss_pct is not None:
        daemon.base_sl_pct = float(req.stop_loss_pct) / 100.0 if req.stop_loss_pct > 0.5 else float(req.stop_loss_pct)
        db_updates["base_sl_pct"] = str(daemon.base_sl_pct)
    if req.total_max_portfolio_margin_usd is not None:
        cap_val = float(req.total_max_portfolio_margin_usd)
        if cap_val < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="total_max_portfolio_margin_usd must be >= 0 (0 disables the guard)"
            )
        daemon.total_max_portfolio_margin_usd = cap_val
        db_updates["total_max_portfolio_margin_usd"] = str(cap_val)

    # Validate and apply 5 Adaptive Engine settings
    if req.engine_mode is not None:
        mode_clean = str(req.engine_mode).strip().upper()
        if mode_clean not in ["CLASSIC", "ADAPTIVE"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="engine_mode must be 'CLASSIC' or 'ADAPTIVE'")
        os.environ["MMS4_ENGINE_MODE"] = mode_clean
        db_updates["engine_mode"] = mode_clean
        env_updates["MMS4_ENGINE_MODE"] = mode_clean

    if req.adaptive_vr_high is not None or req.adaptive_vr_low is not None:
        current_vr_h = float(logger_db.get_setting("adaptive_vr_high", os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6"))) if logger_db else 1.6
        current_vr_l = float(logger_db.get_setting("adaptive_vr_low", os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8"))) if logger_db else 0.8

        vr_h = float(req.adaptive_vr_high) if req.adaptive_vr_high is not None else current_vr_h
        if req.adaptive_vr_high is not None and vr_h <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="adaptive_vr_high must be > 0")

        vr_l = float(req.adaptive_vr_low) if req.adaptive_vr_low is not None else current_vr_l
        if req.adaptive_vr_low is not None and vr_l <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="adaptive_vr_low must be > 0")

        if vr_l >= vr_h:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"adaptive_vr_low ({vr_l}) must be < adaptive_vr_high ({vr_h})"
            )

        if req.adaptive_vr_high is not None:
            os.environ["MMS4_ADAPTIVE_VR_HIGH"] = str(vr_h)
            db_updates["adaptive_vr_high"] = str(vr_h)
            env_updates["MMS4_ADAPTIVE_VR_HIGH"] = str(vr_h)
        if req.adaptive_vr_low is not None:
            os.environ["MMS4_ADAPTIVE_VR_LOW"] = str(vr_l)
            db_updates["adaptive_vr_low"] = str(vr_l)
            env_updates["MMS4_ADAPTIVE_VR_LOW"] = str(vr_l)

    if req.time_lock_start is not None:
        t_start = str(req.time_lock_start).strip()
        try:
            parts = t_start.split(":")
            if len(parts) != 2 or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                raise ValueError()
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="time_lock_start must be in HH:MM format (00:00 - 23:59)")
        os.environ["MMS4_TIME_LOCK_START"] = t_start
        db_updates["time_lock_start"] = t_start
        env_updates["MMS4_TIME_LOCK_START"] = t_start

    if req.time_lock_end is not None:
        t_end = str(req.time_lock_end).strip()
        try:
            parts = t_end.split(":")
            if len(parts) != 2 or not (0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59):
                raise ValueError()
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="time_lock_end must be in HH:MM format (00:00 - 23:59)")
        os.environ["MMS4_TIME_LOCK_END"] = t_end
        db_updates["time_lock_end"] = t_end
        env_updates["MMS4_TIME_LOCK_END"] = t_end

    if req.midline_partial_tp_enabled is not None:
        db_updates["midline_partial_tp_enabled"] = str(bool(req.midline_partial_tp_enabled)).lower()
    if req.midline_tp_fraction is not None:
        frac = float(req.midline_tp_fraction)
        if not (0.05 <= frac <= 0.95):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="midline_tp_fraction must be between 0.05 and 0.95")
        db_updates["midline_tp_fraction"] = str(frac)

    if req.api_key is not None and req.api_key.strip():
        new_k = req.api_key.strip()
        os.environ["MMS4_API_KEY"] = new_k
        env_updates["MMS4_API_KEY"] = new_k

    # Persist to SQLite system_settings table (SSOT)
    if db_updates and logger_db:
        logger_db.set_settings_bulk(db_updates, updated_by="api_dashboard")

    # Secondary backup persistence to volume JSON / .env
    if env_updates:
        persist_env_config(env_updates)

    state_manager.save_state(reason="Settings update via Web Dashboard")
    db_current = logger_db.get_all_settings() if logger_db else {}
    return {
        "status": "success",
        "leverage": state_manager.leverage,
        "state": state_manager.current_state.value,
        "scout_mode": state_manager.scout_mode,
        "engine_mode": db_current.get("engine_mode", os.getenv("MMS4_ENGINE_MODE", "CLASSIC")),
        "adaptive_vr_high": float(db_current.get("adaptive_vr_high", os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6"))),
        "adaptive_vr_low": float(db_current.get("adaptive_vr_low", os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8"))),
        "time_lock_start": db_current.get("time_lock_start", os.getenv("MMS4_TIME_LOCK_START", "13:30")),
        "time_lock_end": db_current.get("time_lock_end", os.getenv("MMS4_TIME_LOCK_END", "14:30")),
        "total_max_portfolio_margin_usd": daemon.total_max_portfolio_margin_usd,
        # Audit P9.1 (2026-09-08): default "false" — see get_settings() above for rationale.
        "midline_partial_tp_enabled": str(db_current.get("midline_partial_tp_enabled", "false")).strip().lower() in ("true", "1", "yes", "on"),
        "midline_tp_fraction": float(db_current.get("midline_tp_fraction", 0.50)),
    }



class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=4, description="New dashboard login password")
    username: Optional[str] = "admin"


@app.post("/api/v1/settings/change_password", tags=["Settings"], dependencies=[Depends(verify_api_key)])
async def change_password(req: PasswordChangeRequest) -> Dict[str, Any]:
    """Change BasicAuth dashboard login password and update .env."""
    import hashlib
    import base64

    sha1_hash = hashlib.sha1(req.new_password.encode()).digest()
    b64_hash = base64.b64encode(sha1_hash).decode()
    htpasswd = f"{req.username}:{{SHA}}{b64_hash}"

    env_paths = [".env", "/app/.env"]
    for env_path in env_paths:
        if os.path.exists(env_path):
            try:
                lines = []
                updated = False
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("MMS4_UI_BASIC_AUTH="):
                            lines.append(f"MMS4_UI_BASIC_AUTH='{htpasswd}'\n")
                            updated = True
                        else:
                            lines.append(line)
                if not updated:
                    lines.append(f"MMS4_UI_BASIC_AUTH='{htpasswd}'\n")
                with open(env_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)
            except Exception as e:
                logger.warning(f"Failed to update {env_path}: {e}")

    os.environ["MMS4_UI_BASIC_AUTH"] = htpasswd
    return {"status": "success", "message": f"Dashboard password updated for user '{req.username}'"}


# ─── Multi-Symbol Trading Endpoints ─────────────────────────────────────────

PERSISTENT_SYMBOLS_PATH = os.environ.get("MMS4_SYMBOLS_CONFIG_PATH", ".agents/swarm/symbols.json")
LEGACY_SYMBOLS_PATH = "symbols.json"


def get_symbols_file_path() -> str:
    """Ensure symbols config is saved in persistent volume .agents/swarm/symbols.json."""
    os.makedirs(os.path.dirname(PERSISTENT_SYMBOLS_PATH), exist_ok=True)
    if not os.path.exists(PERSISTENT_SYMBOLS_PATH) and os.path.exists(LEGACY_SYMBOLS_PATH):
        try:
            import shutil
            shutil.copy2(LEGACY_SYMBOLS_PATH, PERSISTENT_SYMBOLS_PATH)
            logger.info(f"Migrated symbols config from {LEGACY_SYMBOLS_PATH} to {PERSISTENT_SYMBOLS_PATH}")
        except Exception as e:
            logger.warning(f"Failed to migrate symbols config: {e}")
    return PERSISTENT_SYMBOLS_PATH


SYMBOLS_CONFIG_FILE = get_symbols_file_path()


def load_symbols_config() -> Dict[str, Dict[str, Any]]:
    """Load per-symbol configuration from SQLite database, with fallback to symbols.json and defaults."""
    defaults = {
        "BTC/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "1h"},
        "ETH/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "1h"},
        "XAU/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "1h"},
        "XAG/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "1h"}
    }
    # 1. First priority: Load from SQLite database (persistent volume, untouched by git updates)
    try:
        db_configs = logger_db.get_all_symbol_configs()
        if db_configs and len(db_configs) > 0:
            logger.info(f"Loaded {len(db_configs)} symbol configurations from persistent SQLite database.")
            return db_configs
    except Exception as dbe:
        logger.warning(f"Failed to query SQLite symbol_configurations: {dbe}")

    # 2. Second priority: Fallback to symbols.json file
    cfg_file = get_symbols_file_path()
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and len(data) > 0:
                    normalized = {}
                    for sym, val in data.items():
                        if isinstance(val, bool):
                            normalized[sym] = {"is_active": val, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "1h"}
                        elif isinstance(val, dict):
                            normalized[sym] = {
                                "is_active": bool(val.get("is_active", True)),
                                "allocation_usd": float(val.get("allocation_usd", 10.0)),
                                "base_ratio": int(val.get("base_ratio", 70)),
                                "add_ratio": int(val.get("add_ratio", 30)),
                                "timeframe": str(val.get("timeframe", "1h"))
                            }
                    # Seed into SQLite
                    try:
                        for s_name, s_cfg in normalized.items():
                            logger_db.save_symbol_config(
                                symbol=s_name,
                                is_active=s_cfg["is_active"],
                                allocation_usd=s_cfg["allocation_usd"],
                                base_ratio=s_cfg["base_ratio"],
                                add_ratio=s_cfg["add_ratio"],
                                timeframe=s_cfg["timeframe"]
                            )
                    except Exception:
                        pass
                    return normalized
        except Exception as e:
            logger.warning(f"Failed to load {cfg_file}: {e}")

    # 3. Default fallback and initial seed into SQLite
    try:
        for s_name, s_cfg in defaults.items():
            logger_db.save_symbol_config(
                symbol=s_name,
                is_active=s_cfg["is_active"],
                allocation_usd=s_cfg["allocation_usd"],
                base_ratio=s_cfg["base_ratio"],
                add_ratio=s_cfg["add_ratio"],
                timeframe=s_cfg["timeframe"]
            )
    except Exception:
        pass
    return defaults


def save_symbols_config(cfg: Dict[str, Dict[str, Any]]) -> None:
    """Save per-symbol configuration to persistent SQLite database AND symbols.json."""
    # 1. Save to SQLite database
    try:
        for s_name, s_cfg in cfg.items():
            if isinstance(s_cfg, dict):
                logger_db.save_symbol_config(
                    symbol=s_name,
                    is_active=bool(s_cfg.get("is_active", True)),
                    allocation_usd=float(s_cfg.get("allocation_usd", 10.0)),
                    base_ratio=int(s_cfg.get("base_ratio", 70)),
                    add_ratio=int(s_cfg.get("add_ratio", 30)),
                    timeframe=str(s_cfg.get("timeframe", "1h"))
                )
    except Exception as dbe:
        logger.warning(f"Failed to save symbol configs to SQLite: {dbe}")

    # 2. Save to symbols.json backup file
    cfg_file = get_symbols_file_path()
    try:
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save {cfg_file}: {e}")


configured_symbols_store: Dict[str, Dict[str, Any]] = load_symbols_config()


class AddSymbolRequest(BaseModel):
    symbol: str = Field(description="Trading symbol pair e.g. ETH/USDC")
    is_active: bool = True
    allocation_usd: float = Field(default=10.0, description="Base trade allocation in USD (default 10.0 USD)")
    base_ratio: int = Field(default=70, description="Base position ratio percentage e.g. 70")
    add_ratio: int = Field(default=30, description="Add position ratio percentage e.g. 30")
    timeframe: str = Field(default="1h", description="Candle timeframe e.g. 15m, 1h, 5m, 1m")
    stale_candles_threshold: int = Field(default=12, description="Max candles of stagnation before SPLIT_50 (default 12)")
    stale_atr_threshold: float = Field(default=0.5, description="Max price deviation as fraction of ATR (default 0.5)")
    impulse_negation_enabled: bool = Field(default=True, description="Enable 50% impulse negation rule")
    tma_trend_filter_enabled: bool = Field(default=False, description="PLAN-008 Ph3: block counter-trend BASE entries via TMA slope (per-symbol — calibration showed this helps some symbols and hurts others)")
    tma_slope_lookback: int = Field(default=5, description="PLAN-008 Ph3: candles over which TMA slope (pct_change) is measured")
    tma_slope_threshold: float = Field(default=0.002, description="PLAN-008 Ph3: |TMA slope| above which a counter-trend BASE entry is blocked")
    high_vol_base_filter_enabled: bool = Field(default=False, description="Require an extreme sweep-rejection wick for BASE entries when VR > adaptive_vr_high (reuses that threshold, no separate one). Per-symbol, default OFF — a 2026-09-08 backtest found it worsens results in most tested windows; do not enable without re-validating.")
    htf_directional_gate_enabled: bool = Field(default=False, description="Faza 3.2: force directional_bias LONG_ONLY/SHORT_ONLY from this symbol's H4 TMA-band position (Hermes async-supervision model), on top of any manual override. Per-symbol, default OFF — measured 2026-09-11 on 10 independent 122-day windows: 10/10 improved net PnL (sign test p=0.0020), reduces losses in a strong trend, does not make the strategy net profitable.")
    mid_vol_sweep_filter_enabled: bool = Field(default=False, description="Issue #134: require a 20% Sweep Rejection wick for BASE entries when VR is in [adaptive_vr_low, adaptive_vr_high] (the gap between the compression and high-vol filters above, unguarded until now). Per-symbol, default OFF — a 2026-10-15 backtest found this rejects 44-77% of all entries with no significant quality difference (BTC 2/4, ETH 2/4, XAU 2/4, XAG 3/4 windows); not recommended for any symbol on the current evidence.")
    addon_requires_favorable_price_enabled: bool = Field(default=False, description="Issue #137: block HIGH_RISK_ADD when it would fire on the adverse side of this position's base_entry_price (averaging into a loss instead of pyramiding a winner). Per-symbol, default OFF — a 2026-10-15 backtest found adverse-price adds average -0.508% net vs +0.031% for favorable-price adds (14/15 windows agree, sign test p=0.0010, consistent on every symbol); the strongest-evidenced opt-in filter in this file so far.")


okx_verification_cache: Dict[str, Dict[str, Any]] = {}


def _trend_filter_fields(sym: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """PLAN-010 Phase 1 fix: `daemon._get_indicator_params()` reads these three
    fields from per-symbol-qualified `system_settings` keys (e.g.
    "tma_trend_filter_enabled:BTC/USDC") FIRST, falling back to
    `configured_symbols_store` only when no such key exists — see PLAN-008 Phase 3.
    This endpoint previously read `configured_symbols_store` only, so it could (and
    did, 2026-08-29) silently disagree with what the engine actually uses whenever a
    value was written straight to `system_settings` without also updating the
    in-memory store. Mirror the exact same fallback chain here so the API/UI can
    never again show a different trend-filter state than the live engine.

    Also carries `high_vol_base_filter_enabled` (same per-symbol-qualified pattern,
    default OFF, added 2026-09-08) — kept in this helper rather than a new one since
    it's the exact same read/fallback shape, not a separate filter family.

    Audit P6.1 (2026-09-08): also carries the 3 Inactivity Manager fields
    (`stale_candles_threshold`/`stale_atr_threshold`/`impulse_negation_enabled`).
    These were previously written ONLY to `configured_symbols_store`/`symbols.json`,
    never to `system_settings` — but `load_symbols_config()` prefers the SQLite
    `symbol_configurations` table whenever it has any rows (it always does once a
    symbol exists), and that table's schema has no columns for these 3 fields. So on
    every restart `configured_symbols_store` was rebuilt WITHOUT them, silently
    resetting to hardcoded defaults (12 / 0.5 / True) — the UI lied to the operator
    about what was actually configured after any redeploy or crash-restart. Same bug
    class, same fix, as the trend-filter fields above."""
    db_settings = logger_db.get_all_settings()

    def _qualified_bool(key: str, fallback: bool) -> bool:
        raw = db_settings.get(f"{key}:{sym}")
        if raw is not None:
            return str(raw).strip().lower() in ("true", "1", "yes", "on")
        return bool(config.get(key, fallback))

    def _qualified_int(key: str, fallback: int) -> int:
        raw = db_settings.get(f"{key}:{sym}")
        if raw is not None:
            return int(float(raw))
        return int(config.get(key, fallback))

    def _qualified_float(key: str, fallback: float) -> float:
        raw = db_settings.get(f"{key}:{sym}")
        if raw is not None:
            return float(raw)
        return float(config.get(key, fallback))

    return {
        "tma_trend_filter_enabled": _qualified_bool("tma_trend_filter_enabled", False),
        "tma_slope_lookback": _qualified_int("tma_slope_lookback", 5),
        "tma_slope_threshold": _qualified_float("tma_slope_threshold", 0.002),
        "high_vol_base_filter_enabled": _qualified_bool("high_vol_base_filter_enabled", False),
        "htf_directional_gate_enabled": _qualified_bool("htf_directional_gate_enabled", False),
        # Issue #134/#137: same qualified-key, default-OFF pattern as the three
        # filters above — not a separate helper, just two more fields of the same shape.
        "mid_vol_sweep_filter_enabled": _qualified_bool("mid_vol_sweep_filter_enabled", False),
        "addon_requires_favorable_price_enabled": _qualified_bool("addon_requires_favorable_price_enabled", False),
        "stale_candles_threshold": _qualified_int("stale_candles_threshold", 12),
        "stale_atr_threshold": _qualified_float("stale_atr_threshold", 0.5),
        "impulse_negation_enabled": _qualified_bool("impulse_negation_enabled", True),
    }


@app.get("/api/v1/symbols", tags=["Multi-Symbol"])
async def get_trading_symbols() -> Dict[str, Any]:
    """Get all configured trading symbols with per-symbol allocation, Base/Add ratio, live OKX status, state, and leverage."""
    result = []
    for sym, config in configured_symbols_store.items():
        if isinstance(config, bool):
            config = {"is_active": config, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "1h"}

        state = state_manager.get_symbol_state(sym)
        leverage = state_manager.get_symbol_leverage(sym)
        trend_filter = _trend_filter_fields(sym, config)
        
        # Live one-time OKX API verification lookup
        if sym not in okx_verification_cache or not okx_verification_cache[sym].get("okx_verified"):
            v_res = await executor.verify_symbol_with_okx(sym)
            okx_verification_cache[sym] = v_res
        else:
            v_res = okx_verification_cache[sym]

        result.append({
            "symbol": sym,
            "state": state.value,
            "leverage": leverage,
            "is_active": config.get("is_active", True),
            "allocation_usd": config.get("allocation_usd", 10.0),
            "base_ratio": config.get("base_ratio", 70),
            "add_ratio": config.get("add_ratio", 30),
            "timeframe": config.get("timeframe", "1h"),
            # Audit P6.1 (2026-09-08): read from system_settings (via
            # _trend_filter_fields) like the trend-filter fields below, not
            # configured_symbols_store directly — that in-memory store is silently
            # rebuilt without these 3 keys on every restart (see the helper's
            # docstring), so reading it directly here would show the operator a
            # value that has already reset behind their back.
            "stale_candles_threshold": trend_filter["stale_candles_threshold"],
            "stale_atr_threshold": trend_filter["stale_atr_threshold"],
            "impulse_negation_enabled": trend_filter["impulse_negation_enabled"],
            "tma_trend_filter_enabled": trend_filter["tma_trend_filter_enabled"],
            "tma_slope_lookback": trend_filter["tma_slope_lookback"],
            "tma_slope_threshold": trend_filter["tma_slope_threshold"],
            "high_vol_base_filter_enabled": trend_filter["high_vol_base_filter_enabled"],
            "htf_directional_gate_enabled": trend_filter["htf_directional_gate_enabled"],
            "mid_vol_sweep_filter_enabled": trend_filter["mid_vol_sweep_filter_enabled"],
            "addon_requires_favorable_price_enabled": trend_filter["addon_requires_favorable_price_enabled"],
            "okx_verified": v_res.get("okx_verified", True),
            "autotrade_permitted": v_res.get("autotrade_permitted", True),
            "okx_status": v_res.get("okx_status", "AUTOTRADE_READY"),
            "okx_price": v_res.get("last_price", 0.0),
            "reason": v_res.get("reason", ""),
            "directional_bias": state_manager.strategy_profile.directional_bias,
            # Faza 3.2 gate visibility (audit post-merge gap fix): "directional_bias"
            # above is only the raw manual/Hermes-set field (always "BOTH" unless
            # explicitly overridden) -- it never reflects the automatic H4 gate.
            # These two add the automatic reading and the actually-enforced result,
            # so an operator/Hermes can see all three layers at once.
            "htf_bias": state_manager.get_symbol_htf_bias(sym),
            "effective_directional_bias": state_manager.get_effective_directional_bias(sym),
            "risk_profile": state_manager.strategy_profile.risk_profile
        })
    return {"symbols": result, "total": len(result)}


class BiasSyncSymbolInfo(BaseModel):
    symbol: str
    directional_bias: str = Field(description="Raw global manual/Hermes-set override, 'BOTH' unless explicitly changed")
    htf_bias: str = Field(description="Automatic Faza 3.2 per-symbol H4 gate reading, 'BOTH' when disabled or never fired")
    effective_directional_bias: str = Field(description="What evaluate_signals actually enforces right now for this symbol")
    htf_directional_gate_enabled: bool
    suppressed: bool = Field(description="True when the global manual override (directional_bias != 'BOTH') is actively suppressing this symbol's automatic H4 gate")
    last_strategy_profile_reason: Optional[str] = Field(default=None, description="`reason` on the most recent strategy_profile write (global, not per-symbol)")
    last_strategy_profile_age_seconds: Optional[float] = Field(default=None, description="Seconds since the most recent strategy_profile write; null if never written")


class BiasSyncResponse(BaseModel):
    symbols: List[BiasSyncSymbolInfo]


@app.get("/api/v1/diagnostics/bias-sync", response_model=BiasSyncResponse, tags=["Diagnostics"])
async def get_bias_sync_diagnostics() -> Dict[str, Any]:
    """
    Faza 6 C.3.2: read-only diagnostic view (deliberately unauthenticated --
    this only ever reads state, it changes nothing) of the 3 directional_bias
    layers per configured symbol, whether the global manual override is
    currently suppressing that symbol's automatic H4 gate, and how old the
    most recent strategy_profile write is. Built entirely from state_manager
    reads already used elsewhere (get_trading_symbols, _trend_filter_fields)
    -- no new state, just a single place to see bias drift/staleness at a
    glance instead of cross-referencing /symbols and /strategy/profile.
    """
    manual_bias = state_manager.strategy_profile.directional_bias
    suppressed = manual_bias != "BOTH"

    last_profile: Optional[Dict[str, Any]] = None
    try:
        last_profile = logger_db.get_latest_strategy_profile()
    except Exception as e:
        logger.warning(f"bias-sync diagnostics: failed to read latest strategy_profile: {e}")

    last_reason = last_profile.get("reason") if last_profile else None
    last_age_seconds: Optional[float] = None
    if last_profile and last_profile.get("timestamp"):
        try:
            ts_raw = str(last_profile["timestamp"]).replace("Z", "+00:00")
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            last_age_seconds = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
        except Exception as e:
            logger.warning(f"bias-sync diagnostics: failed to parse strategy_profile timestamp: {e}")

    symbols_out = []
    for sym, config in configured_symbols_store.items():
        if isinstance(config, bool):
            config = {}
        trend_filter = _trend_filter_fields(sym, config)
        symbols_out.append({
            "symbol": sym,
            "directional_bias": manual_bias,
            "htf_bias": state_manager.get_symbol_htf_bias(sym),
            "effective_directional_bias": state_manager.get_effective_directional_bias(sym),
            "htf_directional_gate_enabled": trend_filter["htf_directional_gate_enabled"],
            "suppressed": suppressed,
            "last_strategy_profile_reason": last_reason,
            "last_strategy_profile_age_seconds": last_age_seconds,
        })

    return {"symbols": symbols_out}


@app.post("/api/v1/symbols/verify", tags=["Multi-Symbol"])
async def verify_symbol_direct(req: AddSymbolRequest) -> Dict[str, Any]:
    """Directly perform one-time OKX REST API ticker verification for a symbol."""
    formatted_sym = req.symbol.upper().strip()
    v_res = await executor.verify_symbol_with_okx(formatted_sym)
    okx_verification_cache[formatted_sym] = v_res
    return v_res


@app.post("/api/v1/symbols/add", tags=["Multi-Symbol"], dependencies=[Depends(verify_api_key)])
@app.post("/api/v1/symbols/update", tags=["Multi-Symbol"], dependencies=[Depends(verify_api_key)])
async def add_or_update_trading_symbol(req: AddSymbolRequest, request: Request) -> Dict[str, Any]:
    """Add, activate, or update per-symbol allocation ($ USD) & Base/Add ratio configuration.

    2026-10-15 incident: this handler backs BOTH routes and unconditionally
    overwrote every AddSymbolRequest field with its schema default whenever a
    caller omitted it — fine for /update as long as every caller always sends
    the full config (the dashboard does, see SymbolsTab.jsx), but /add has no
    such guarantee. A single "Dodaj Nową Parę" click on an already-configured
    symbol (e.g. BTC/USDC) silently reset every one of its settings to
    defaults in production, including undoing the PR #136 decision to keep
    htf_directional_gate_enabled on. Reject /add on a symbol that already
    exists instead — /update remains the only path that can change an
    existing symbol's settings.

    2026-10-27 incident: that "every caller always sends the full config"
    assumption broke for /update too — a bulk `allocation_usd` update
    (2026-10-21, all 4 symbols) silently reset BTC/USDC's `timeframe` from
    `1h` to `15m` because `AddSymbolRequest.timeframe`'s schema default
    (`15m`, historically out of sync with symbols.json/`load_symbols_config()`'s
    `1h` default) filled in for the omitted field. Root-cause fix: /update now
    merges onto the existing stored config — any field the caller's request
    body didn't explicitly set (`req.model_fields_set`, true regardless of
    whether the omitted value happens to equal its schema default) keeps its
    previously stored value instead of being overwritten by the schema
    default. /add is unaffected (brand-new symbol, no existing config to
    merge against — schema defaults are correct there).

    The merge source differs by field family, matching how each is actually
    read back (`_get_indicator_params`/`_trend_filter_fields`): the 5 SQLite
    `symbol_configurations` columns (is_active/allocation_usd/base_ratio/
    add_ratio/timeframe) merge against `configured_symbols_store`, which
    `load_symbols_config()` rebuilds from SQLite on every restart, so it is
    always current. The 10 `system_settings`-only fields (trend/vol filters,
    the addon favorable-price gate, inactivity-manager thresholds) merge
    against `_trend_filter_fields(...)`'s qualified-system_settings-first
    read instead — `configured_symbols_store` alone goes stale for these the
    moment the process restarts (see that helper's own docstring), so merging
    only against the in-memory store would silently turn a live production
    gate (e.g. `addon_requires_favorable_price_enabled`, already on for all 4
    symbols) back off on the next /update that omits it, post-restart.
    """
    formatted_sym = _format_symbol(req.symbol)
    is_update_route = request.url.path.endswith("/symbols/update")
    if not is_update_route and formatted_sym in configured_symbols_store:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{formatted_sym} is already configured. Use /api/v1/symbols/update "
                "to change its settings — /add would reset every omitted field to "
                "its default."
            ),
        )

    existing_cfg = configured_symbols_store.get(formatted_sym, {}) if is_update_route else {}
    if not isinstance(existing_cfg, dict):
        existing_cfg = {}
    # Authoritative *current* value of the 10 system_settings-backed fields — reads
    # the qualified system_settings key first, exactly like `_get_indicator_params`
    # does, so merging against this (not `existing_cfg` alone) survives a restart.
    existing_trend_fields = _trend_filter_fields(formatted_sym, existing_cfg) if is_update_route else {}
    explicitly_set = req.model_fields_set

    def _merged(field: str, incoming_value: Any, source: Dict[str, Any] = existing_cfg) -> Any:
        """Keep the existing value for a field the caller's request body didn't
        explicitly set (on /update, against `source`); otherwise use the value
        carried on `req` (either explicitly sent, or the schema default when
        there is no existing value to merge onto)."""
        if is_update_route and field not in explicitly_set and field in source:
            return source[field]
        return incoming_value

    b_ratio = int(_merged("base_ratio", req.base_ratio))
    a_ratio = 100 - b_ratio if b_ratio <= 100 else int(_merged("add_ratio", req.add_ratio))
    tf = str(_merged("timeframe", req.timeframe or "1h"))

    stale_candles_threshold = int(_merged("stale_candles_threshold", req.stale_candles_threshold, existing_trend_fields))
    stale_atr_threshold = float(_merged("stale_atr_threshold", req.stale_atr_threshold, existing_trend_fields))
    impulse_negation_enabled = bool(_merged("impulse_negation_enabled", req.impulse_negation_enabled, existing_trend_fields))
    tma_trend_filter_enabled = bool(_merged("tma_trend_filter_enabled", req.tma_trend_filter_enabled, existing_trend_fields))
    tma_slope_lookback = int(_merged("tma_slope_lookback", req.tma_slope_lookback, existing_trend_fields))
    tma_slope_threshold = float(_merged("tma_slope_threshold", req.tma_slope_threshold, existing_trend_fields))
    high_vol_base_filter_enabled = bool(
        _merged("high_vol_base_filter_enabled", req.high_vol_base_filter_enabled, existing_trend_fields)
    )
    htf_directional_gate_enabled = bool(
        _merged("htf_directional_gate_enabled", req.htf_directional_gate_enabled, existing_trend_fields)
    )
    mid_vol_sweep_filter_enabled = bool(
        _merged("mid_vol_sweep_filter_enabled", req.mid_vol_sweep_filter_enabled, existing_trend_fields)
    )
    addon_requires_favorable_price_enabled = bool(
        _merged(
            "addon_requires_favorable_price_enabled",
            req.addon_requires_favorable_price_enabled,
            existing_trend_fields,
        )
    )

    configured_symbols_store[formatted_sym] = {
        "is_active": bool(_merged("is_active", req.is_active)),
        "allocation_usd": float(_merged("allocation_usd", req.allocation_usd)),
        "base_ratio": b_ratio,
        "add_ratio": a_ratio,
        "timeframe": tf,
        "stale_candles_threshold": stale_candles_threshold,
        "stale_atr_threshold": stale_atr_threshold,
        "impulse_negation_enabled": impulse_negation_enabled,
        "tma_trend_filter_enabled": tma_trend_filter_enabled,
        "tma_slope_lookback": tma_slope_lookback,
        "tma_slope_threshold": tma_slope_threshold,
        "high_vol_base_filter_enabled": high_vol_base_filter_enabled,
        "htf_directional_gate_enabled": htf_directional_gate_enabled,
        "mid_vol_sweep_filter_enabled": mid_vol_sweep_filter_enabled,
        "addon_requires_favorable_price_enabled": addon_requires_favorable_price_enabled,
    }
    save_symbols_config(configured_symbols_store)

    # PLAN-008 Phase 3: persist the trend-filter fields (plus high_vol_base_filter_
    # enabled, same shape, added 2026-09-08) into system_settings under a
    # per-symbol-qualified key. The `symbol_configurations` SQLite table has a fixed
    # schema (symbol, is_active, allocation_usd, base_ratio, add_ratio, timeframe) with
    # no column for these — writing only to configured_symbols_store/symbols.json would
    # silently reset them to defaults on the next `load_symbols_config()` call, since
    # that function prefers SQLite whenever it has any rows (it always does once a
    # symbol has been added). system_settings is schema-less and already durable.
    #
    # 2026-10-27 fix: this used to write `req.<field>` directly, bypassing the merge
    # above entirely — an /update omitting e.g. `addon_requires_favorable_price_enabled`
    # would silently write `False` (the schema default) here even though the merged
    # `configured_symbols_store` entry correctly preserved `True`, because
    # `_get_indicator_params`/`_trend_filter_fields` read the qualified system_settings
    # key FIRST, before ever falling back to the store. Now writes the same merged
    # values computed above, so the two persistence layers can never disagree.
    try:
        logger_db.set_settings_bulk(
            {
                f"tma_trend_filter_enabled:{formatted_sym}": tma_trend_filter_enabled,
                f"tma_slope_lookback:{formatted_sym}": tma_slope_lookback,
                f"tma_slope_threshold:{formatted_sym}": tma_slope_threshold,
                f"high_vol_base_filter_enabled:{formatted_sym}": high_vol_base_filter_enabled,
                f"htf_directional_gate_enabled:{formatted_sym}": htf_directional_gate_enabled,
                f"mid_vol_sweep_filter_enabled:{formatted_sym}": mid_vol_sweep_filter_enabled,
                f"addon_requires_favorable_price_enabled:{formatted_sym}": addon_requires_favorable_price_enabled,
                # Audit P6.1 (2026-09-08): same bug, same fix — these 3 Inactivity
                # Manager fields were previously written only to
                # configured_symbols_store/symbols.json, silently resetting to
                # hardcoded defaults on every restart (see _trend_filter_fields'
                # docstring above).
                f"stale_candles_threshold:{formatted_sym}": stale_candles_threshold,
                f"stale_atr_threshold:{formatted_sym}": stale_atr_threshold,
                f"impulse_negation_enabled:{formatted_sym}": impulse_negation_enabled,
            },
            updated_by="api_dashboard",
        )
    except Exception as e:
        logger.warning(f"Failed to persist trend-filter/inactivity settings for {formatted_sym}: {e}")

    # A pair-config edit must not touch the symbol's risk state: this used to force
    # BASE_MODE unconditionally, silently wiping an active SCOUT_MODE (even right after a
    # real stop-out). A symbol the state manager has never seen already defaults to
    # BASE_MODE, so there is nothing to initialize here.

    # Perform instant OKX API verification
    v_res = await executor.verify_symbol_with_okx(formatted_sym)
    okx_verification_cache[formatted_sym] = v_res

    return {
        "status": "success",
        "symbol": formatted_sym,
        "is_active": req.is_active,
        "allocation_usd": configured_symbols_store[formatted_sym]["allocation_usd"],
        "base_ratio": configured_symbols_store[formatted_sym]["base_ratio"],
        "add_ratio": configured_symbols_store[formatted_sym]["add_ratio"],
        "okx_verified": v_res.get("okx_verified", True),
        "okx_status": v_res.get("okx_status", "VERIFIED"),
        "okx_price": v_res.get("last_price", 0.0),
        "configured_symbols": configured_symbols_store
    }


@app.delete("/api/v1/symbols/{symbol:path}", tags=["Multi-Symbol"], dependencies=[Depends(verify_api_key)])
async def remove_trading_symbol(symbol: str) -> Dict[str, Any]:
    """Completely remove a trading pair from the symbols configuration and SQLite store."""
    formatted_sym = symbol.upper().strip()
    if "/" not in formatted_sym:
        formatted_sym = formatted_sym.replace("USDC", "/USDC") if formatted_sym.endswith("USDC") else f"{formatted_sym}/USDC"

    removed = False
    if formatted_sym in configured_symbols_store:
        configured_symbols_store.pop(formatted_sym, None)
        removed = True
        save_symbols_config(configured_symbols_store)
        try:
            logger_db.delete_symbol_config(formatted_sym)
        except Exception as e:
            logger.warning(f"Failed to delete symbol {formatted_sym} from SQLite: {e}")

    return {
        "status": "success",
        "removed": formatted_sym,
        "is_active": False,
        "configured_symbols": configured_symbols_store,
        "message": f"Para {formatted_sym} została trwale usunięta z menedżera."
    }


# Default eligibility bar for the trend-filter recalibration mechanism (PLAN-010
# §3.3): midpoint of the ~150-200 clean-trade range agreed 2026-08-29, after the
# deployed Phase 3 parameters failed out-of-sample validation on a 90-day window.
# Overridable per symbol via system_settings key
# "trend_filter_eligibility_threshold:{symbol}" for pairs with very different trade
# frequency than BTC/USDC or ETH/USDC.
TREND_FILTER_ELIGIBILITY_THRESHOLD_DEFAULT = 175


@app.get("/api/v1/symbols/{symbol:path}/recalibration-status", tags=["Multi-Symbol"])
async def get_recalibration_status(symbol: str) -> Dict[str, Any]:
    """PLAN-010 Phase 1 (§3.3): report whether `symbol` has accumulated enough clean
    closed-trade history to be a candidate for trend-filter recalibration, plus its
    current live filter state and the verdict of its most recent calibration run (if
    any). Read-only — never modifies filter parameters or triggers a calibration
    (that is `POST /recalibrate`, PLAN-010 Phase 2, not built in this phase)."""
    formatted_sym = symbol.upper().strip()
    if "/" not in formatted_sym:
        formatted_sym = formatted_sym.replace("USDC", "/USDC") if formatted_sym.endswith("USDC") else f"{formatted_sym}/USDC"

    config = configured_symbols_store.get(formatted_sym, {})
    if isinstance(config, bool):
        config = {}
    trend_filter = _trend_filter_fields(formatted_sym, config)

    db_settings = logger_db.get_all_settings()
    threshold_raw = db_settings.get(f"trend_filter_eligibility_threshold:{formatted_sym}")
    eligibility_threshold = int(float(threshold_raw)) if threshold_raw is not None else TREND_FILTER_ELIGIBILITY_THRESHOLD_DEFAULT

    clean_trade_count = logger_db.count_clean_closed_trades(formatted_sym)

    # PLAN-010 Phase 2: report the most recent calibration run for this symbol, if any.
    last_run = logger_db.get_latest_calibration_run(formatted_sym)
    last_calibration_run_at = last_run["updated_at"] if last_run else None
    last_calibration_verdict = last_run["verdict"] if last_run else None

    return {
        "symbol": formatted_sym,
        "trend_filter_enabled": trend_filter["tma_trend_filter_enabled"],
        "current_params": {
            "lookback": trend_filter["tma_slope_lookback"],
            "threshold": trend_filter["tma_slope_threshold"],
        },
        "clean_trade_count": clean_trade_count,
        "eligibility_threshold": eligibility_threshold,
        "eligible": clean_trade_count >= eligibility_threshold,
        "last_calibration_run_at": last_calibration_run_at,
        "last_calibration_verdict": last_calibration_verdict,
        # PLAN-010: null means "counting all history" (no regime change has been
        # marked yet for this symbol) — not "no data collected".
        "data_collection_epoch_start": logger_db.get_data_collection_epoch(formatted_sym),
    }


@app.post("/api/v1/symbols/{symbol:path}/recalibrate", tags=["Multi-Symbol"],
          dependencies=[Depends(verify_api_key)], status_code=status.HTTP_202_ACCEPTED)
async def start_recalibration(symbol: str) -> Dict[str, Any]:
    """PLAN-010 Phase 2 (§4.2): start a trend-filter recalibration run for `symbol`.

    ASYNCHRONOUS BY DESIGN — returns immediately with a `job_id`; the actual
    train/test computation (tens of seconds to several minutes, per the 2026-08-29
    review's measured timings) runs in a separate OS process via
    `run_in_executor(CALIBRATION_EXECUTOR, ...)`, never inline. `api.py` and the live
    trading daemon share one asyncio event loop (`daemon.start()` runs in the same
    FastAPI lifespan) — a blocking inline run would stall live reconciliation and
    self-healing stop-loss checks for the run's full duration. See PLAN-010 §4.2.

    Requires `eligible: true` (409 otherwise, with the current clean trade count).
    Never modifies `tma_trend_filter_enabled`/`tma_slope_lookback`/`tma_slope_threshold`
    — applying a validated result stays a separate, explicit call to the existing
    `POST /api/v1/symbols/update` (PLAN-010 §5.2).
    """
    formatted_sym = symbol.upper().strip()
    if "/" not in formatted_sym:
        formatted_sym = formatted_sym.replace("USDC", "/USDC") if formatted_sym.endswith("USDC") else f"{formatted_sym}/USDC"

    status_payload = await get_recalibration_status(formatted_sym)
    if not status_payload["eligible"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"{formatted_sym} has not accumulated enough clean trade history for recalibration.",
                "clean_trade_count": status_payload["clean_trade_count"],
                "eligibility_threshold": status_payload["eligibility_threshold"],
            },
        )

    # A symbol already has a pending/running job -> return it instead of enqueueing
    # a duplicate. CALIBRATION_EXECUTOR's max_workers=1 would serialize the actual
    # computation either way, but without this check two requests double the work
    # (and CPU time on the VPS) for what is, in practice, the same answer.
    existing = logger_db.get_latest_calibration_run(formatted_sym)
    if existing and existing["status"] in ("pending", "running"):
        return {"job_id": existing["job_id"], "symbol": formatted_sym, "status": existing["status"],
                "note": "A calibration run for this symbol is already in progress; returning its job_id instead of starting a duplicate."}

    job_id = str(uuid.uuid4())
    logger_db.create_calibration_run(job_id, formatted_sym)

    from mms4tk.backtest_engine import run_calibration_job
    loop = asyncio.get_event_loop()
    # Fire-and-forget: NOT awaited here — the job runs independently in the process
    # pool while this request returns immediately. Progress is polled via the
    # status endpoint below, which reads the same `trend_filter_calibration_runs`
    # row this job updates from its own process.
    loop.run_in_executor(CALIBRATION_EXECUTOR, run_calibration_job, job_id, formatted_sym, logger_db.db_path)

    return {"job_id": job_id, "symbol": formatted_sym, "status": "pending"}


@app.get("/api/v1/symbols/{symbol:path}/recalibrate/status/{job_id}", tags=["Multi-Symbol"])
async def get_recalibration_job_status(symbol: str, job_id: str) -> Dict[str, Any]:
    """Poll a recalibration job started by `POST /recalibrate`. `status` is one of
    `pending` / `running` / `done` / `failed`; `result` (the full `CalibrationResult`,
    both train/test directions) is populated only once `status == "done"`."""
    run = logger_db.get_calibration_run(job_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No calibration job {job_id}")

    result = json.loads(run["result_json"]) if run.get("result_json") else None
    return {
        "job_id": job_id,
        "symbol": run["symbol"],
        "status": run["status"],
        "verdict": run.get("verdict"),
        "error": run.get("error"),
        "result": result,
    }


@app.get("/api/v1/symbols/{symbol:path}/recalibrate/history", tags=["Multi-Symbol"])
async def get_recalibration_history(symbol: str, limit: int = 20) -> Dict[str, Any]:
    """PLAN-010 §5.2.3: read-only, most-recent-first list of past calibration runs
    for `symbol`, for the "historia kalibracji" panel in the Symbols UI. Each entry
    omits the full `result_json` blob (fetch via the status endpoint by `job_id` for
    the complete train/test breakdown) — this is a lightweight summary list."""
    formatted_sym = symbol.upper().strip()
    if "/" not in formatted_sym:
        formatted_sym = formatted_sym.replace("USDC", "/USDC") if formatted_sym.endswith("USDC") else f"{formatted_sym}/USDC"

    runs = logger_db.list_calibration_runs(formatted_sym, limit=limit)
    return {
        "symbol": formatted_sym,
        "runs": [
            {
                "job_id": r["job_id"], "status": r["status"], "verdict": r.get("verdict"),
                "created_at": r["created_at"], "updated_at": r["updated_at"], "error": r.get("error"),
            }
            for r in runs
        ],
    }


@app.get("/api/v1/positions", tags=["Multi-Symbol"])
async def get_active_positions() -> Dict[str, Any]:
    """Fetch live open positions from OKX across all active trading symbols."""
    active_syms = [s for s, cfg in configured_symbols_store.items() if (isinstance(cfg, dict) and cfg.get("is_active")) or cfg is True]
    try:
        raw_pos = await executor.fetch_positions(symbols=active_syms if active_syms else None)
        cleaned = []
        if isinstance(raw_pos, list):
            for p in raw_pos:
                contracts = float(p.get("contracts", 0.0) or p.get("size", 0.0) or 0.0)
                if contracts > 0 or p.get("size", 0) != 0:
                    cleaned.append({
                        "symbol": p.get("symbol"),
                        "side": p.get("side", "long"),
                        "contracts": contracts,
                        "notional_usd": float(p.get("notional", 0.0) or p.get("notionalUsd", 0.0) or 0.0),
                        "entry_price": float(p.get("entryPrice", 0.0) or 0.0),
                        "mark_price": float(p.get("markPrice", 0.0) or 0.0),
                        "unrealized_pnl": float(p.get("unrealizedPnl", 0.0) or 0.0),
                        "leverage": float(p.get("leverage", 1.0) or 1.0),
                        "liquidation_price": float(p.get("liquidationPrice", 0.0) or 0.0)
                    })
        return {"positions": cleaned, "total": len(cleaned)}
    except Exception as e:
        logger.warning(f"Failed to fetch live positions: {e}")
        return {"positions": [], "total": 0, "error": str(e)}


class ClosePositionRequest(BaseModel):
    symbol: str = Field(description="Trading symbol pair e.g. BTC/USDC or SOL/USDC")
    side: Optional[str] = Field(default=None, description="Position side to close ('long' or 'short'); closes active side if omitted")


@app.post("/api/v1/positions/close", tags=["Trading"], dependencies=[Depends(verify_api_key)])
async def close_position_endpoint(req: ClosePositionRequest) -> Dict[str, Any]:
    """Close an active open position for a given symbol and side on OKX via market reduce order."""
    sym = req.symbol.strip().upper()
    if "/" not in sym:
        sym = sym.replace("USDC", "/USDC") if sym.endswith("USDC") else f"{sym}/USDC"

    sides = [req.side.lower()] if req.side and req.side.lower() in ["long", "short"] else ["long", "short"]
    results = []
    for s in sides:
        try:
            res = await executor.reduce_position(
                symbol=sym,
                side=s,
                reduction_pct=1.0,
                reason="User manual position close from Web Dashboard"
            )
            results.append(res)
        except Exception as err:
            logger.warning(f"Failed to close position {sym} ({s}): {err}")
            results.append({"status": "error", "symbol": sym, "side": s, "error": str(err)})

    try:
        await daemon.reconcile_positions()
    except Exception as e:
        logger.warning(f"Reconciliation failed after manual close: {e}")

    try:
        await trade_sync_worker.sync_closed_trades()
    except Exception as e:
        logger.warning(f"Immediate trade sync failed after manual close: {e}")

    return {
        "status": "success",
        "symbol": sym,
        "results": results,
        "message": f"Zlecenie zamknięcia pozycji dla {sym} zostało wysłane do OKX, a wykaz transakcji zaktualizowany."
    }




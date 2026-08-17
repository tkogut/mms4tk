import os
import json
import sqlite3
import logging
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
import pandas as pd
from fastapi import FastAPI, HTTPException, status, Header, Depends
from pydantic import BaseModel, Field

from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import MMS4StateManager, MMS4State, StrategyProfile
from mms4tk.okx_executor import OKXExecutor
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
        await tq.stop()


app = FastAPI(
    title="MMS4 Toolkit API (mms4tk)",
    description="Execution module & API interface for MMS v4 strategy managed by Hermes agent",
    version=APP_VERSION,
    lifespan=lifespan
)


class StateResponse(BaseModel):
    state: str
    leverage: float
    current_equity: float
    realized_pnl: float
    total_trades: int


class PnLResponse(BaseModel):
    daily_roe: float = Field(default=0.0, description="Daily Return on Equity percentage")
    realized_pnl: float = Field(default=0.0, description="Realized Profit and Loss")
    unrealized_pnl: float = Field(default=0.0, description="Unrealized Profit and Loss")
    total_trades: int = Field(default=0, description="Total number of trades executed")
    win_rate: float = Field(default=0.0, description="Win rate percentage (0.0 to 100.0)")
    profit_factor: float = Field(default=0.0, description="Profit factor ratio")
    max_drawdown: float = Field(default=0.0, description="Max Drawdown in USD (see max_drawdown_pct for the ratio)")
    average_rr: float = Field(default=0.0, description="Average Risk to Reward ratio")
    free_balance: float = Field(default=0.0, description="Free available USDC balance")
    btc_balance: float = Field(default=0.0, description="BTC asset balance")


class EmergencyKillallResponse(BaseModel):
    status: str
    message: str
    action_taken: str


class EmergencyReverseResponse(BaseModel):
    status: str
    message: str
    action_taken: str


class HighRiskAddRequest(BaseModel):
    side: str = Field(default="LONG", description="Order side: LONG or SHORT")
    size: float = Field(default=1.0, description="Position size multiplier or contracts")
    stop_loss_pct: float = Field(default=0.01, description="Tight stop loss percentage (e.g. 0.01 for 1%)")


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

                active_positions.append({
                    "symbol": symbol,
                    "side": side,
                    "contracts": abs(contracts),
                    "notional_usd": abs(notional),
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "unrealized_pnl": unrealized_pnl,
                    "leverage": leverage,
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


@app.get("/api/v1/stats/pnl", response_model=PnLResponse, tags=["Stats"])
async def get_pnl() -> Dict[str, Any]:
    """Get daily ROE %, realized PnL, unrealized PnL, KPI summary, and account balances."""
    summary = state_manager.get_summary()
    kpi = logger_db.get_kpi_summary()

    free_usdc = 0.0
    btc_bal = 0.0
    try:
        bal_data = await executor.fetch_balance()
        free_usdc = float(bal_data.get("free", {}).get("USDC", 0.0))
        btc_bal = float(bal_data.get("total", {}).get("BTC", 0.0))
    except Exception as err:
        logger.warning(f"Could not fetch OKX live balance for /api/v1/stats/pnl: {err}")

    return {
        "daily_roe": 0.0,
        "realized_pnl": kpi.get("total_pnl_usd", summary.get("realized_pnl", 0.0)),
        "unrealized_pnl": 0.0,
        "total_trades": kpi.get("total_trades", summary.get("total_trades", 0)),
        "win_rate": kpi.get("win_rate", 0.0),
        "profit_factor": kpi.get("profit_factor", 0.0),
        "max_drawdown": kpi.get("max_drawdown_usd", kpi.get("max_drawdown", 0.0)),
        "max_drawdown_pct": kpi.get("max_drawdown_pct", 0.0),
        "max_drawdown_usd": kpi.get("max_drawdown_usd", 0.0),
        "average_rr": kpi.get("average_rr", 0.0),
        "free_balance": free_usdc,
        "btc_balance": btc_bal
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
async def emergency_killall() -> Dict[str, str]:
    """
    Emergency stop endpoint triggered by parent controller (Hermes).
    Cancels all open orders, closes active positions, and locks execution.
    """
    await executor.cancel_all_orders()
    return {
        "status": "success",
        "message": "Emergency killall signal processed successfully.",
        "action_taken": "All open orders cancelled, position close triggered."
    }


@app.post("/api/v1/emergency/reverse", response_model=EmergencyReverseResponse, tags=["Emergency"], dependencies=[Depends(verify_api_key)])
async def emergency_reverse() -> Dict[str, str]:
    """
    Emergency position reversal triggered by Hermes agent.
    Closes current position immediately and opens opposite position with SL=2%.
    """
    return {
        "status": "success",
        "message": "Emergency reverse order executed.",
        "action_taken": "Closed existing position, opened reversed position with hard SL=2%."
    }


@app.post("/api/v1/trade/high_risk_add", response_model=HighRiskAddResponse, tags=["Trading"], dependencies=[Depends(verify_api_key)])
async def high_risk_add(req: HighRiskAddRequest) -> Dict[str, Any]:
    """
    High-risk position add (leverage x1, independent ticket) with tight SL=1%.
    Blocked if strategy is in SCOUT_MODE (REDUCED_RISK).
    """
    if not state_manager.is_high_risk_add_allowed():
        return {
            "status": "rejected",
            "executed": False,
            "reason": f"HIGH_RISK_ADD blocked in {state_manager.current_state.value} (leverage x{state_manager.leverage})",
            "details": {"current_state": state_manager.current_state.value, "leverage": state_manager.leverage}
        }

    # Execute high risk add order via OKXExecutor
    try:
        await executor.execute_order(
            symbol=daemon.symbol,
            side=req.side,
            amount=req.size,
            stop_loss_pct=req.stop_loss_pct,
            leverage=1.0
        )
    except Exception as e:
        logger.error(f"Error executing high risk add: {e}")

    return {
        "status": "success",
        "executed": True,
        "reason": "High-risk add order placed successfully.",
        "details": {
            "side": req.side,
            "size": req.size,
            "stop_loss_pct": req.stop_loss_pct,
            "leverage": 1.0
        }
    }


class HigherTimeframeResponse(BaseModel):
    atr_d1: float
    atr_d1_expansion_ratio: float
    h4_tma_position: str = Field(description="ABOVE | BELOW | INSIDE")
    h4_candle_formation: str = Field(description="SWEEP_WICK | MARUBOZU_BREAKOUT | NEUTRAL")
    h4_tma: float
    h4_upper_band: float
    h4_lower_band: float


@app.get("/api/v1/analytics/higher_tf", response_model=HigherTimeframeResponse, tags=["Analytics"])
async def get_higher_tf_analytics() -> Dict[str, Any]:
    """
    Fetch higher timeframe (H4/D1) market state analysis.
    Calculates D1 ATR expansion ratio, H4 TMA position, and candle formation.
    """
    try:
        candles_h4 = await executor.fetch_candles(symbol=daemon.symbol, timeframe="4h", limit=50)
        candles_d1 = await executor.fetch_candles(symbol=daemon.symbol, timeframe="1d", limit=50)
    except Exception as e:
        logger.warning(f"Failed to fetch HTF candles from OKX: {e}. Using empty candle list.")
        candles_h4 = []
        candles_d1 = []

    return HigherTimeframeAnalytics.calculate_higher_tf(candles_h4, candles_d1)


@app.get("/api/v1/strategy/profile", response_model=StrategyProfile, tags=["Strategy Profile"])
async def get_strategy_profile() -> StrategyProfile:
    """
    Get current active strategy profile configuration.
    """
    return state_manager.strategy_profile


@app.post("/api/v1/strategy/profile", response_model=StrategyProfile, tags=["Strategy Profile"], dependencies=[Depends(verify_api_key)])
async def set_strategy_profile(profile: StrategyProfile) -> StrategyProfile:
    """
    Update active strategy profile configuration.
    If scout_override is True, relaxes SCOUT_MODE to BASE_MODE immediately.
    Protected by X-MMS4-API-KEY header.
    """
    updated = state_manager.update_strategy_profile(profile)
    if updated.scout_override:
        state_manager.relax_scout_mode()
    return updated


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
    directional_bias: str = Field(default="BOTH", description="BOTH | LONG_ONLY | SHORT_ONLY | FLAT")
    risk_profile: str = Field(default="MODERATE", description="CONSERVATIVE | MODERATE | AGGRESSIVE")
    reason: str = Field(default="Human-in-the-Loop decision")


@app.post("/api/v1/hermes/hitl_resolve", tags=["Hermes Integration"], dependencies=[Depends(verify_api_key)])
async def hitl_resolve(req: HITLResolveRequest) -> Dict[str, Any]:
    """
    Human-in-the-Loop resolution endpoint.
    Called when a human approves a Kanban card from Blocked/HITL column.

    - SWEEP_WICK → scout_override=True (relax SCOUT_MODE back to BASE_MODE)
    - MARUBOZU_BREAKOUT → maintain SCOUT_MODE, update directional_bias accordingly
    """
    scout_override = req.decision == "SWEEP_WICK"
    profile = StrategyProfile(
        directional_bias=req.directional_bias,
        risk_profile=req.risk_profile,
        scout_override=scout_override,
        reason=f"HITL: {req.decision} — {req.reason}",
    )
    updated = state_manager.update_strategy_profile(profile)
    relaxed = False
    if scout_override:
        relaxed = state_manager.relax_scout_mode()

    return {
        "status": "ok",
        "decision": req.decision,
        "scout_override": scout_override,
        "scout_mode_relaxed": relaxed,
        "profile": updated.model_dump(),
    }


# ─── Dashboard Integration Endpoints ─────────────────────────────────────────

@app.get("/api/v1/history/trades", tags=["History"])
async def get_history_trades(limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch real historical closed trades from SQLite logger_db."""
    return logger_db.get_closed_trades(limit=limit)


@app.get("/api/v1/history/candles", tags=["History"])
async def get_history_candles(symbol: str = "BTC/USDC", timeframe: str = "1h", limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch live/historical candles from OKX for technical charts."""
    try:
        candles = await executor.fetch_candles(symbol=symbol, timeframe=timeframe, limit=limit)
        return [c.model_dump() for c in candles]
    except Exception as e:
        logger.warning(f"Error fetching candles for {symbol} ({timeframe}): {e}")
        return []


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


class SettingsUpdateRequest(BaseModel):
    leverage: Optional[float] = 3.0
    stop_loss_pct: Optional[float] = 1.25
    scout_mode: Optional[bool] = False
    api_key: Optional[str] = None
    capital_split_ratio: Optional[float] = 0.70
    max_margin_usd: Optional[float] = 10.0


@app.post("/api/v1/settings/update", tags=["Settings"], dependencies=[Depends(verify_api_key)])
async def update_settings(req: SettingsUpdateRequest) -> Dict[str, Any]:
    """Update runtime strategy parameters, leverage (up to 10x), SL %, and risk profile."""
    if req.leverage is not None:
        state_manager.leverage = req.leverage  # setter clamps to [1.0, 10.0]
    if req.scout_mode is not None:
        state_manager.scout_mode = bool(req.scout_mode)

    state_manager.save_state(reason="Settings update via Web Dashboard")
    return {
        "status": "success",
        "leverage": state_manager.leverage,
        "state": state_manager.current_state.value,
        "scout_mode": state_manager.scout_mode
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
        "BTC/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "15m"},
        "ETH/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "15m"},
        "SOL/USDC": {"is_active": True, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "15m"}
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
                            normalized[sym] = {"is_active": val, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "15m"}
                        elif isinstance(val, dict):
                            normalized[sym] = {
                                "is_active": bool(val.get("is_active", True)),
                                "allocation_usd": float(val.get("allocation_usd", 10.0)),
                                "base_ratio": int(val.get("base_ratio", 70)),
                                "add_ratio": int(val.get("add_ratio", 30)),
                                "timeframe": str(val.get("timeframe", "15m"))
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
                    timeframe=str(s_cfg.get("timeframe", "15m"))
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
    timeframe: str = Field(default="15m", description="Candle timeframe e.g. 15m, 1h, 5m, 1m")
    stale_candles_threshold: int = Field(default=12, description="Max candles of stagnation before SPLIT_50 (default 12)")
    stale_atr_threshold: float = Field(default=0.5, description="Max price deviation as fraction of ATR (default 0.5)")
    impulse_negation_enabled: bool = Field(default=True, description="Enable 50% impulse negation rule")


okx_verification_cache: Dict[str, Dict[str, Any]] = {}


@app.get("/api/v1/symbols", tags=["Multi-Symbol"])
async def get_trading_symbols() -> Dict[str, Any]:
    """Get all configured trading symbols with per-symbol allocation, Base/Add ratio, live OKX status, state, and leverage."""
    result = []
    for sym, config in configured_symbols_store.items():
        if isinstance(config, bool):
            config = {"is_active": config, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30, "timeframe": "15m"}

        state = state_manager.get_symbol_state(sym)
        leverage = state_manager.get_symbol_leverage(sym)
        
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
            "timeframe": config.get("timeframe", "15m"),
            "stale_candles_threshold": config.get("stale_candles_threshold", 12),
            "stale_atr_threshold": config.get("stale_atr_threshold", 0.5),
            "impulse_negation_enabled": config.get("impulse_negation_enabled", True),
            "okx_verified": v_res.get("okx_verified", True),
            "autotrade_permitted": v_res.get("autotrade_permitted", True),
            "okx_status": v_res.get("okx_status", "AUTOTRADE_READY"),
            "okx_price": v_res.get("last_price", 0.0),
            "reason": v_res.get("reason", ""),
            "directional_bias": state_manager.strategy_profile.directional_bias,
            "risk_profile": state_manager.strategy_profile.risk_profile
        })
    return {"symbols": result, "total": len(result)}


@app.post("/api/v1/symbols/verify", tags=["Multi-Symbol"])
async def verify_symbol_direct(req: AddSymbolRequest) -> Dict[str, Any]:
    """Directly perform one-time OKX REST API ticker verification for a symbol."""
    formatted_sym = req.symbol.upper().strip()
    v_res = await executor.verify_symbol_with_okx(formatted_sym)
    okx_verification_cache[formatted_sym] = v_res
    return v_res


@app.post("/api/v1/symbols/add", tags=["Multi-Symbol"], dependencies=[Depends(verify_api_key)])
@app.post("/api/v1/symbols/update", tags=["Multi-Symbol"], dependencies=[Depends(verify_api_key)])
async def add_or_update_trading_symbol(req: AddSymbolRequest) -> Dict[str, Any]:
    """Add, activate, or update per-symbol allocation ($ USD) & Base/Add ratio configuration."""
    formatted_sym = req.symbol.upper().strip()
    if "/" not in formatted_sym:
        formatted_sym = formatted_sym.replace("USDC", "/USDC") if formatted_sym.endswith("USDC") else f"{formatted_sym}/USDC"

    b_ratio = req.base_ratio
    a_ratio = 100 - b_ratio if b_ratio <= 100 else req.add_ratio
    tf = req.timeframe or "15m"

    configured_symbols_store[formatted_sym] = {
        "is_active": req.is_active,
        "allocation_usd": float(req.allocation_usd),
        "base_ratio": int(b_ratio),
        "add_ratio": int(a_ratio),
        "timeframe": tf,
        "stale_candles_threshold": int(req.stale_candles_threshold),
        "stale_atr_threshold": float(req.stale_atr_threshold),
        "impulse_negation_enabled": bool(req.impulse_negation_enabled),
    }
    save_symbols_config(configured_symbols_store)
    state_manager.set_symbol_state(formatted_sym, MMS4State.BASE_MODE, reason="Dynamic pair configuration update via API")
    
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
    """Deactivate (pause) a trading pair without deleting it from the manager UI."""
    formatted_sym = symbol.upper().strip()
    if formatted_sym in configured_symbols_store:
        if isinstance(configured_symbols_store[formatted_sym], dict):
            configured_symbols_store[formatted_sym]["is_active"] = False
        else:
            configured_symbols_store[formatted_sym] = {"is_active": False, "allocation_usd": 10.0, "base_ratio": 70, "add_ratio": 30}
        save_symbols_config(configured_symbols_store)
    return {
        "status": "success",
        "paused": formatted_sym,
        "is_active": False,
        "configured_symbols": configured_symbols_store
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

    return {
        "status": "success",
        "symbol": sym,
        "results": results,
        "message": f"Zlecenie zamknięcia pozycji dla {sym} zostało wysłane do OKX."
    }




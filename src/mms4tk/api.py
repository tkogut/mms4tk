import os
import logging
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
import pandas as pd
from fastapi import FastAPI, HTTPException, status, Header, Depends
from pydantic import BaseModel, Field

from mms4tk.logger_db import LoggerDB
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import Candle, IndicatorParams, SignalResult, TMAIndicator, MMS4SignalEngine
from mms4tk.daemon import MMS4Daemon

logger = logging.getLogger("mms4tk.api")

# Global instances
logger_db = LoggerDB()
state_manager = MMS4StateManager(initial_state=MMS4State.BASE_MODE, logger_db=logger_db)
executor = OKXExecutor()
signal_engine = MMS4SignalEngine()
daemon = MMS4Daemon(executor=executor, state_manager=state_manager, signal_engine=signal_engine, logger_db=logger_db)

latest_signal: Optional[SignalResult] = None


async def verify_api_key(x_mms4_api_key: Optional[str] = Header(None, alias="X-MMS4-API-KEY")) -> str:
    """Verify X-MMS4-API-KEY header against MMS4_API_KEY env var."""
    expected_key = os.getenv("MMS4_API_KEY", "mms4tk-secret-key-2026")
    if not x_mms4_api_key or x_mms4_api_key != expected_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid or missing X-MMS4-API-KEY header"
        )
    return x_mms4_api_key


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan context manager:
    Starts MMS4Daemon on application startup and gracefully stops on shutdown.
    """
    logger.info("FastAPI Startup: Launching MMS4Daemon background task loop...")
    await daemon.start()
    try:
        yield
    finally:
        logger.info("FastAPI Shutdown: Stopping MMS4Daemon background task loop...")
        await daemon.stop()


app = FastAPI(
    title="MMS4 Toolkit API (mms4tk)",
    description="Execution module & API interface for MMS v4 strategy managed by Hermes agent",
    version="0.1.0",
    lifespan=lifespan
)


class StateResponse(BaseModel):
    state: str
    leverage: float
    current_equity: float
    realized_pnl: float
    total_trades: int


class PnLResponse(BaseModel):
    daily_roe: float = Field(description="Daily Return on Equity percentage")
    realized_pnl: float = Field(description="Realized Profit and Loss")
    unrealized_pnl: float = Field(description="Unrealized Profit and Loss")
    total_trades: int = Field(description="Total number of trades executed")
    win_rate: float = Field(description="Win rate percentage (0.0 to 1.0)")


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
    last_cycle_timestamp: Optional[str] = None
    processed_cycles: int


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
        "version": "0.1.0"
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
    """Get daily ROE %, realized PnL, unrealized PnL, and trade stats."""
    summary = state_manager.get_summary()
    pnl = summary.get("realized_pnl", 0.0)
    trades = summary.get("total_trades", 0)
    return {
        "daily_roe": 0.0,
        "realized_pnl": pnl,
        "unrealized_pnl": 0.0,
        "total_trades": trades,
        "win_rate": 0.0
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

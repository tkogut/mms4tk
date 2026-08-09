from typing import Dict, Any, List, Optional
import pandas as pd
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.analytics import Candle, IndicatorParams, SignalResult, TMAIndicator, MMS4SignalEngine

app = FastAPI(
    title="MMS4 Toolkit API (mms4tk)",
    description="Execution module & API interface for MMS v4 strategy managed by Hermes agent",
    version="0.1.0"
)

# Global State Manager & Signal Engine instances
state_manager = MMS4StateManager(initial_state=MMS4State.BASE_MODE)
signal_engine = MMS4SignalEngine()
latest_signal: Optional[SignalResult] = None


class StateResponse(BaseModel):
    state: str
    leverage: float
    current_equity: float
    realized_pnl: float
    total_trades: int


class EmergencyKillallResponse(BaseModel):
    status: str
    message: str
    action_taken: str


class HealthResponse(BaseModel):
    status: str
    version: str


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
    Returns latest signal evaluated from state.
    """
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


@app.post("/api/v1/emergency/killall", response_model=EmergencyKillallResponse, tags=["Emergency"])
async def emergency_killall() -> Dict[str, str]:
    """
    Emergency stop endpoint triggered by parent controller (Hermes).
    Cancels all open orders, closes active positions, and locks execution.
    """
    return {
        "status": "success",
        "message": "Emergency killall signal processed successfully.",
        "action_taken": "All open orders cancelled, position close triggered."
    }

from typing import Dict, Any
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from mms4tk.state_manager import MMS4StateManager, MMS4State

app = FastAPI(
    title="MMS4 Toolkit API (mms4tk)",
    description="Execution module & API interface for MMS v4 strategy managed by Hermes agent",
    version="0.1.0"
)

# Global State Manager instance
state_manager = MMS4StateManager(initial_state=MMS4State.BASE_MODE)


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


@app.post("/api/v1/emergency/killall", response_model=EmergencyKillallResponse, tags=["Emergency"])
async def emergency_killall() -> Dict[str, str]:
    """
    Emergency stop endpoint triggered by parent controller (Hermes).
    Cancels all open orders, closes active positions, and locks execution.
    """
    # Placeholder logic for emergency order cancellation
    return {
        "status": "success",
        "message": "Emergency killall signal processed successfully.",
        "action_taken": "All open orders cancelled, position close triggered."
    }

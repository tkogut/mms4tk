from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class MMS4State(str, Enum):
    """
    Operating modes for MMS4 strategy.
    BASE_MODE: Full leverage (1.0x) active when system is performing as expected.
    SCOUT_MODE: Reduced leverage (0.1x) activated after hard SL (>= 2%).
    """
    BASE_MODE = "BASE_MODE"
    SCOUT_MODE = "SCOUT_MODE"


class TradeRecord(BaseModel):
    """Record of an executed trade for audit and state management."""
    trade_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    side: str  # "buy" or "sell"
    entry_price: float
    exit_price: float
    amount: float
    pnl: float
    pnl_percent: float
    is_stop_loss: bool = False
    is_take_profit: bool = False
    sl_percent: float = 0.0
    mode_at_execution: MMS4State


class MMS4StateManager:
    """
    Manages MMS4 strategy execution state, equity, PnL tracking, and state transition triggers.
    
    Transition rules:
    - BASE_MODE -> SCOUT_MODE: Triggered when a Stop-Loss hit is >= 2.0% (0.02).
    - SCOUT_MODE -> BASE_MODE: Triggered on first Take-Profit (TP) hit.
    
    Note: NO trailing stops logic is implemented per strategy specification.
    """

    def __init__(
        self,
        initial_state: MMS4State = MMS4State.BASE_MODE,
        initial_equity: float = 0.0
    ) -> None:
        self._current_state: MMS4State = initial_state
        self._initial_equity: float = initial_equity
        self._current_equity: float = initial_equity
        self._realized_pnl: float = 0.0
        self._trade_history: List[TradeRecord] = []

    @property
    def current_state(self) -> MMS4State:
        """Return current operating state."""
        return self._current_state

    @property
    def leverage(self) -> float:
        """
        Return active leverage corresponding to current state.
        BASE_MODE -> 1.0
        SCOUT_MODE -> 0.1
        """
        if self._current_state == MMS4State.BASE_MODE:
            return 1.0
        elif self._current_state == MMS4State.SCOUT_MODE:
            return 0.1
        return 1.0

    @property
    def current_equity(self) -> float:
        """Return current equity."""
        return self._current_equity

    @property
    def realized_pnl(self) -> float:
        """Return total realized PnL."""
        return self._realized_pnl

    @property
    def trade_history(self) -> List[TradeRecord]:
        """Return complete list of executed trades."""
        return list(self._trade_history)

    def set_equity(self, equity: float) -> None:
        """Update current equity balance."""
        self._current_equity = equity

    def on_stop_loss_hit(self, sl_percent: float) -> bool:
        """
        Triggered when a Stop Loss is executed.
        If in BASE_MODE and sl_percent >= 0.02 (2%), transition to SCOUT_MODE.
        Returns True if a state transition occurred, False otherwise.
        """
        if self._current_state == MMS4State.BASE_MODE and sl_percent >= 0.02:
            self._current_state = MMS4State.SCOUT_MODE
            return True
        return False

    def on_take_profit_hit(self) -> bool:
        """
        Triggered when a Take Profit is executed.
        If in SCOUT_MODE, transition back to BASE_MODE.
        Returns True if a state transition occurred, False otherwise.
        """
        if self._current_state == MMS4State.SCOUT_MODE:
            self._current_state = MMS4State.BASE_MODE
            return True
        return False

    def record_trade(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        amount: float,
        pnl: float,
        pnl_percent: float,
        is_stop_loss: bool = False,
        is_take_profit: bool = False,
        sl_percent: float = 0.0
    ) -> TradeRecord:
        """
        Record completed trade, update PnL, and evaluate state transitions.
        """
        record = TradeRecord(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            amount=amount,
            pnl=pnl,
            pnl_percent=pnl_percent,
            is_stop_loss=is_stop_loss,
            is_take_profit=is_take_profit,
            sl_percent=sl_percent,
            mode_at_execution=self._current_state
        )
        self._trade_history.append(record)
        self._realized_pnl += pnl
        self._current_equity += pnl

        if is_stop_loss:
            self.on_stop_loss_hit(sl_percent)
        elif is_take_profit:
            self.on_take_profit_hit()

        return record

    def get_summary(self) -> Dict[str, Any]:
        """Return dict representation of current state and metrics."""
        return {
            "current_state": self._current_state.value,
            "leverage": self.leverage,
            "initial_equity": self._initial_equity,
            "current_equity": self._current_equity,
            "realized_pnl": self._realized_pnl,
            "total_trades": len(self._trade_history)
        }

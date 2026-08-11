from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

import logging

logger = logging.getLogger("mms4tk.state_manager")


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


class StrategyProfile(BaseModel):
    """
    Strategy profile for Two-Tier Adaptive Autotrading System.
    Controlled by Hermes agent via REST API.
    """
    directional_bias: str = Field(default="BOTH", description="BOTH | LONG_ONLY | SHORT_ONLY | FLAT")
    risk_profile: str = Field(default="MODERATE", description="CONSERVATIVE | MODERATE | AGGRESSIVE")
    scout_override: bool = Field(default=False, description="True allows early SCOUT_MODE relaxation")
    reason: str = Field(default="Initial default profile")
    updated_at: Optional[str] = None


class MMS4StateManager:
    """
    Manages MMS4 strategy execution state, equity, PnL tracking, and state transition triggers.
    
    Transition rules:
    - BASE_MODE -> SCOUT_MODE: Triggered when a Stop-Loss hit is >= 2.0% (0.02).
    - SCOUT_MODE -> BASE_MODE: Triggered on first Take-Profit (TP) hit or scout_override relaxation.
    
    Note: NO trailing stops logic is implemented per strategy specification.
    """

    def __init__(
        self,
        initial_state: MMS4State = MMS4State.BASE_MODE,
        initial_equity: float = 0.0,
        logger_db: Optional[Any] = None,
        hermes_webhook: Optional[Any] = None,
        trading_symbol: Optional[str] = None
    ) -> None:
        self._current_state: MMS4State = initial_state
        self.trading_symbol = trading_symbol or os.getenv("MMS4_SYMBOL", os.getenv("MMS4_SYMBOL_FUTURES", "BTC/USDC:USDC"))
        self._initial_equity: float = initial_equity
        self._current_equity: float = initial_equity
        self._realized_pnl: float = 0.0
        self._trade_history: List[TradeRecord] = []
        self._strategy_profile: StrategyProfile = StrategyProfile()
        self.logger_db: Optional[Any] = logger_db
        self.hermes_webhook: Optional[Any] = hermes_webhook
        self.trading_symbol: str = trading_symbol

        if self.logger_db is not None:
            saved = self.logger_db.get_latest_state()
            if saved and "state" in saved and saved["state"]:
                self._current_state = MMS4State(saved["state"])
            self._load_strategy_profile_from_db()

    def set_logger_db(self, logger_db: Any) -> None:
        """Attach LoggerDB instance and restore latest persisted state if present."""
        self.logger_db = logger_db
        saved = self.logger_db.get_latest_state()
        if saved and "state" in saved and saved["state"]:
            self._current_state = MMS4State(saved["state"])
        self._load_strategy_profile_from_db()

    def _load_strategy_profile_from_db(self) -> None:
        if self.logger_db is not None:
            saved_prof = self.logger_db.get_latest_strategy_profile()
            if saved_prof:
                self._strategy_profile = StrategyProfile(
                    directional_bias=saved_prof.get("directional_bias", "BOTH"),
                    risk_profile=saved_prof.get("risk_profile", "MODERATE"),
                    scout_override=bool(saved_prof.get("scout_override", False)),
                    reason=saved_prof.get("reason", "Initial default profile"),
                    updated_at=saved_prof.get("timestamp")
                )

    @property
    def strategy_profile(self) -> StrategyProfile:
        """Return active StrategyProfile."""
        return self._strategy_profile

    def update_strategy_profile(self, profile: StrategyProfile) -> StrategyProfile:
        """
        Update active StrategyProfile and persist to logger_db if attached.
        """
        if not profile.updated_at:
            profile.updated_at = datetime.now(timezone.utc).isoformat()
        self._strategy_profile = profile
        if self.logger_db is not None:
            self.logger_db.save_strategy_profile(
                directional_bias=profile.directional_bias,
                risk_profile=profile.risk_profile,
                scout_override=profile.scout_override,
                reason=profile.reason
            )
        return self._strategy_profile

    def relax_scout_mode(self) -> bool:
        """
        If currently in SCOUT_MODE, transition back to BASE_MODE.
        Returns True if a state transition occurred, False otherwise.
        """
        if self._current_state == MMS4State.SCOUT_MODE:
            self._current_state = MMS4State.BASE_MODE
            if self.logger_db:
                self.logger_db.save_state(
                    state=self._current_state.value,
                    leverage=self.leverage,
                    reason="SCOUT_MODE relaxed via strategy profile scout_override"
                )
            return True
        return False

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

    def is_high_risk_add_allowed(self) -> bool:
        """
        Check if High Risk Add position is allowed in current state.
        BASE_MODE (leverage 1.0) -> True
        SCOUT_MODE (leverage 0.1) -> False
        """
        return self._current_state == MMS4State.BASE_MODE

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

    def on_stop_loss_hit(self, sl_percent: float, sl_price: float = 0.0) -> bool:
        """
        Triggered when a Stop Loss is executed.
        If in BASE_MODE and sl_percent >= 0.02 (2%), transition to SCOUT_MODE.
        Also emits a Hermes webhook event for H4 Liquidity Sweep Analysis.
        Returns True if a state transition occurred, False otherwise.
        """
        if self._current_state == MMS4State.BASE_MODE and sl_percent >= 0.02:
            self._current_state = MMS4State.SCOUT_MODE
            if self.logger_db:
                self.logger_db.save_state(
                    state=self._current_state.value,
                    leverage=self.leverage,
                    reason=f"Stop Loss hit ({sl_percent * 100:.1f}%)"
                )
            # Emit Hermes webhook event for H4 analysis
            if self.hermes_webhook:
                try:
                    self.hermes_webhook.emit_scout_mode_event(
                        symbol=self.trading_symbol,
                        sl_price=sl_price,
                        sl_percent=sl_percent,
                        reason=f"Stop Loss hit ({sl_percent * 100:.1f}%)"
                    )
                    logger.info(f"Hermes webhook emitted for SCOUT_MODE activation (SL {sl_percent*100:.1f}%)")
                except Exception as e:
                    logger.error(f"Failed to emit Hermes webhook: {e}")
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
            if self.logger_db:
                self.logger_db.save_state(
                    state=self._current_state.value,
                    leverage=self.leverage,
                    reason="Take Profit hit"
                )
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

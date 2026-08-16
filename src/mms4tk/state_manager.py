import os
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

import logging

logger = logging.getLogger("mms4tk.state_manager")


class MMS4State(str, Enum):
    """
    Operating modes for MMS4 strategy.
    BASE_MODE: Full leverage active when system is performing as expected.
    SCOUT_MODE: Reduced leverage (SCOUT_LEVERAGE, default 0.1x) after a hard SL (>= 2%).
    """
    BASE_MODE = "BASE_MODE"
    SCOUT_MODE = "SCOUT_MODE"


# Leverage per risk profile in BASE_MODE.
#
# The original MMS v4 spec describes a flat 1.0x base. The Two-Tier Adaptive system
# (Hermes-controlled risk profiles) supersedes that with per-profile leverage, so the
# values below are the source of truth and are overridable from the environment.
# Set MMS4_LEVERAGE_MODERATE=1.0 to run the strict, spec-literal configuration.
BASE_LEVERAGE_BY_PROFILE: Dict[str, float] = {
    "CONSERVATIVE": float(os.getenv("MMS4_LEVERAGE_CONSERVATIVE", "1.0")),
    "MODERATE": float(os.getenv("MMS4_LEVERAGE_MODERATE", "3.0")),
    "AGGRESSIVE": float(os.getenv("MMS4_LEVERAGE_AGGRESSIVE", "10.0")),
}
SCOUT_LEVERAGE: float = float(os.getenv("MMS4_LEVERAGE_SCOUT", "0.1"))
DEFAULT_BASE_LEVERAGE: float = 1.0


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
        self.trading_symbol = trading_symbol or os.getenv("MMS4_SYMBOL", os.getenv("MMS4_SYMBOL_FUTURES", "BTC/USDC"))
        self._initial_equity: float = initial_equity
        self._current_equity: float = initial_equity
        self._realized_pnl: float = 0.0
        self._trade_history: List[TradeRecord] = []
        self._strategy_profile: StrategyProfile = StrategyProfile()
        self._leverage_override: Optional[float] = None
        self.logger_db: Optional[Any] = logger_db
        self.hermes_webhook: Optional[Any] = hermes_webhook

        # Per-symbol state isolation dictionary
        self._symbol_states: Dict[str, MMS4State] = {
            self.trading_symbol: initial_state
        }

        if self.logger_db is not None:
            saved = self.logger_db.get_latest_state()
            if saved and "state" in saved and saved["state"]:
                self._current_state = MMS4State(saved["state"])
                self._symbol_states[self.trading_symbol] = self._current_state
            self._load_strategy_profile_from_db()

    def get_symbol_state(self, symbol: str) -> MMS4State:
        """
        Return the operating state for a specific trading symbol.

        An unknown symbol defaults to BASE_MODE, as documented. It previously fell back to
        the PRIMARY symbol's state, so a stop-out on BTC silently forced every other
        symbol into SCOUT_MODE — the opposite of the per-symbol isolation this map exists
        to provide.
        """
        return self._symbol_states.get(symbol, MMS4State.BASE_MODE)

    def set_symbol_state(self, symbol: str, state: MMS4State, reason: str = "") -> None:
        """Update operating state for a specific symbol without altering other symbols."""
        self._symbol_states[symbol] = state
        if symbol == self.trading_symbol:
            self._current_state = state
        if self.logger_db:
            self.logger_db.save_state(
                state=state.value,
                leverage=self.get_symbol_leverage(symbol),
                reason=f"[{symbol}] {reason}"
            )

    def get_symbol_leverage(self, symbol: str) -> float:
        """Return active leverage for specific trading symbol."""
        sym_state = self.get_symbol_state(symbol)
        return self._resolve_leverage(sym_state)

    def _resolve_leverage(self, state: MMS4State) -> float:
        """Single source of truth for leverage: state first, then risk profile, then override."""
        if state == MMS4State.SCOUT_MODE:
            return SCOUT_LEVERAGE
        if self._leverage_override is not None:
            return self._leverage_override
        risk_prof = self._strategy_profile.risk_profile.upper()
        return BASE_LEVERAGE_BY_PROFILE.get(risk_prof, DEFAULT_BASE_LEVERAGE)

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

    def relax_scout_mode(self, symbol: Optional[str] = None) -> bool:
        """
        If target symbol is currently in SCOUT_MODE, transition back to BASE_MODE.
        Returns True if a state transition occurred, False otherwise.
        """
        target_sym = symbol or self.trading_symbol
        if self.get_symbol_state(target_sym) == MMS4State.SCOUT_MODE:
            self.set_symbol_state(target_sym, MMS4State.BASE_MODE, reason="SCOUT_MODE relaxed via strategy profile scout_override")
            return True
        return False

    @property
    def current_state(self) -> MMS4State:
        """Return current operating state."""
        return self._current_state

    @current_state.setter
    def current_state(self, state: MMS4State) -> None:
        """Set the operating state of the primary symbol (keeps per-symbol map in sync)."""
        self.set_symbol_state(self.trading_symbol, MMS4State(state), reason="State set directly")

    @property
    def leverage(self) -> float:
        """
        Active leverage for the current state and StrategyProfile.

        SCOUT_MODE always wins and returns SCOUT_LEVERAGE (0.1x by default) — a manual
        override never re-arms risk while the system is in its defensive mode.
        In BASE_MODE: an explicit override, else BASE_LEVERAGE_BY_PROFILE.
        """
        return self._resolve_leverage(self._current_state)

    @leverage.setter
    def leverage(self, value: float) -> None:
        """
        Manually pin BASE_MODE leverage (dashboard / API). Clamped to [1.0, 10.0].
        Pass None to fall back to the risk-profile value.
        """
        if value is None:
            self._leverage_override = None
            return
        self._leverage_override = max(1.0, min(10.0, float(value)))
        logger.info(f"Leverage override set to x{self._leverage_override}")

    @property
    def scout_mode(self) -> bool:
        """True when the primary symbol is in SCOUT_MODE (reduced-risk defense)."""
        return self._current_state == MMS4State.SCOUT_MODE

    @scout_mode.setter
    def scout_mode(self, enabled: bool) -> None:
        """Force SCOUT_MODE on/off for the primary symbol."""
        if enabled:
            self.set_symbol_state(self.trading_symbol, MMS4State.SCOUT_MODE, reason="SCOUT_MODE forced manually")
        else:
            self.relax_scout_mode()

    def save_state(self, reason: str = "") -> None:
        """Persist the current state snapshot to logger_db (no-op without a DB attached)."""
        if self.logger_db is not None:
            self.logger_db.save_state(state=self._current_state.value, leverage=self.leverage, reason=reason)

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

    def on_stop_loss_hit(self, sl_percent: float, sl_price: float = 0.0, symbol: Optional[str] = None) -> bool:
        """
        Triggered when a Stop Loss is executed.
        If in BASE_MODE and sl_percent >= 0.02 (2%), transition ONLY the target symbol to SCOUT_MODE.
        Also emits a Hermes webhook event for H4 Liquidity Sweep Analysis.
        Returns True if a state transition occurred, False otherwise.
        """
        target_sym = symbol or self.trading_symbol
        curr_sym_state = self.get_symbol_state(target_sym)
        if curr_sym_state == MMS4State.BASE_MODE and sl_percent >= 0.02:
            self.set_symbol_state(target_sym, MMS4State.SCOUT_MODE, reason=f"Stop Loss hit ({sl_percent * 100:.1f}%)")
            # Emit Hermes webhook event for H4 analysis
            if self.hermes_webhook:
                try:
                    self.hermes_webhook.emit_scout_mode_event(
                        symbol=target_sym,
                        sl_price=sl_price,
                        sl_percent=sl_percent,
                        reason=f"Stop Loss hit ({sl_percent * 100:.1f}%)"
                    )
                    logger.info(f"Hermes webhook emitted for SCOUT_MODE activation on {target_sym} (SL {sl_percent*100:.1f}%)")
                except Exception as e:
                    logger.error(f"Failed to emit Hermes webhook for {target_sym}: {e}")
            return True
        return False

    def on_take_profit_hit(self, symbol: Optional[str] = None) -> bool:
        """
        Triggered when a Take Profit is executed.
        If target symbol is in SCOUT_MODE, transition back to BASE_MODE.
        Returns True if a state transition occurred, False otherwise.
        """
        target_sym = symbol or self.trading_symbol
        curr_sym_state = self.get_symbol_state(target_sym)
        if curr_sym_state == MMS4State.SCOUT_MODE:
            self.set_symbol_state(target_sym, MMS4State.BASE_MODE, reason="Take Profit hit")
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

        State transitions are applied to the trade's OWN symbol — previously the symbol
        was dropped here and the primary symbol was transitioned instead.
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
            self.on_stop_loss_hit(sl_percent, sl_price=exit_price, symbol=symbol)
        elif is_take_profit:
            self.on_take_profit_hit(symbol=symbol)

        if self.logger_db is not None:
            try:
                self.logger_db.log_state_metrics(
                    state=self._current_state.value,
                    leverage=self.leverage,
                    equity=self._current_equity,
                    realized_pnl=self._realized_pnl,
                    trade_count=len(self._trade_history),
                    force=True,
                )
            except Exception as e:
                logger.warning(f"Failed to persist state metrics after trade {trade_id}: {e}")

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

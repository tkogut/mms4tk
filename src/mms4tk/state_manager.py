import math
import os
import time
from enum import Enum
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field

import logging

logger = logging.getLogger("mms4tk.state_manager")


class MMS4State(str, Enum):
    """
    Operating modes for MMS4 strategy.
    BASE_MODE: full allocation at the risk profile's leverage.
    SCOUT_MODE: reduced POSITION SIZE at an exchange-legal leverage, after a hard SL (>= 2%).
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
DEFAULT_BASE_LEVERAGE: float = 1.0

# ── SCOUT_MODE risk reduction ────────────────────────────────────────────
#
# Risk in SCOUT is cut through POSITION SIZE, not through leverage.
#
# Leverage cannot carry the reduction: OKX only accepts integer leverage >= 1, so the
# old 0.1x was silently clamped to 1x on the exchange while the notional it implied
# ($0.70 at a $10 cap) fell under the exchange's minimum order value — the entry was then
# skipped entirely and the symbol could never trade its way back to BASE_MODE.
#
# Splitting the two knobs makes the reduction real and, crucially, scale-free:
#
#   scout_margin   = max_margin_usd * BASE_ALLOCATION * SCOUT_SIZE_MULT
#   scout_notional = scout_margin * SCOUT_LEVERAGE
#
# Against BASE (max_margin * BASE_ALLOCATION * base_leverage) the notional ratio is
# SCOUT_SIZE_MULT * SCOUT_LEVERAGE / base_leverage — independent of account size.
# At the defaults (0.3 * 1.0 / 3.0) that is a 10x reduction at ANY capital level.
# MMS4_LEVERAGE_SCOUT is the pre-rename name; honored so an existing .env keeps working.
SCOUT_LEVERAGE: float = float(
    os.getenv("MMS4_SCOUT_LEVERAGE", os.getenv("MMS4_LEVERAGE_SCOUT", "1.0"))
)
SCOUT_SIZE_MULT: float = float(os.getenv("MMS4_SCOUT_SIZE_MULT", "0.3"))

# ── SCOUT_MODE exit after K clean closes (E3 of #107) ────────────────────
#
# A full take-profit used to be the ONLY way a symbol left SCOUT_MODE (besides an operator
# override). A symbol that only ever closes via the Inactivity Manager (FORCE_CLOSE...) never
# produces one, so it could sit in SCOUT_MODE indefinitely (XAG: since 2026-10-07).
#
# Now a symbol also leaves SCOUT_MODE after SCOUT_EXIT_K CONSECUTIVE clean full closes:
#   * only FORCE_CLOSE / MIDLINE_INVALIDATION count (SCOUT_COUNTING_REASONS);
#   * "clean" = gross pnl_percent above the measured round-trip cost (== net > 0). The cost is
#     backtest_engine.ROUND_TRIP_FEE_PCT (0.0010003, measured from production); it is repeated
#     here because state_manager must not import from the backtest;
#   * the decision is taken only on a REAL fill price: a close whose PnL is only an estimate
#     (`pnl_is_estimate=True`, e.g. the last closed candle) is neutral - it neither counts nor
#     resets, because the 0.10% threshold is far below a typical intra-candle move;
#   * a counting close that is not clean, and a STOP_LOSS, RESET the counter ("K in a row");
#   * FORCE_CLOSE_BE / BE_ESCAPE / CLOSED_EXTERNAL and partial closes are neutral;
#   * a full take-profit still exits immediately (unchanged).
def _env_number(name: str, default: float, cast=float):
    """Env override that falls back to `default` (with a warning) instead of crashing at import."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        logging.getLogger("mms4tk.state_manager").warning(f"Ignoring invalid {name}={raw!r}; using {default}")
        return default


SCOUT_EXIT_K: int = max(1, _env_number("MMS4_SCOUT_EXIT_K", 3, int))
SCOUT_MIN_GROSS_PNL_PCT: float = _env_number("MMS4_SCOUT_MIN_GROSS_PNL_PCT", 0.0010003)
SCOUT_COUNTING_REASONS = frozenset({"FORCE_CLOSE", "MIDLINE_INVALIDATION"})
_SCOUT_OK_KEY_PREFIX = "scout_ok:"

# Split of the per-symbol margin cap between the base entry and the high-risk add.
BASE_ALLOCATION: float = float(os.getenv("MMS4_BASE_ALLOCATION", "0.70"))
ADDON_ALLOCATION: float = float(os.getenv("MMS4_ADDON_ALLOCATION", str(round(1.0 - BASE_ALLOCATION, 10))))


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
    is_partial: bool = False
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


# ── Close-reason classification (SSOT) ─────────────────────────────────────
# One reason -> (is_stop_loss, is_take_profit, is_partial) table shared by the daemon and
# the backtest, so every way a position can disappear is booked identically and a NEW
# close path cannot silently skip the state machine. `finalize_close()` is the only
# supported way to book a close; an unknown reason raises on purpose.
#
# Parity note: FORCE_CLOSE / FORCE_CLOSE_BE / BE_ESCAPE / MIDLINE_INVALIDATION are
# deliberately "neither SL nor TP" (inactivity-driven or ambiguous exits), STALE_SPLIT_50
# is a PARTIAL take-profit (SCOUT preserved). Changing what leaves SCOUT_MODE is a
# separate, deliberate change (E3 of #107) - not part of this table's introduction.
CLOSE_REASON_FLAGS: Dict[str, Dict[str, bool]] = {
    "STOP_LOSS":            dict(is_stop_loss=True,  is_take_profit=False, is_partial=False),
    "TP_AND_FLIP":          dict(is_stop_loss=False, is_take_profit=True,  is_partial=False),
    "STALE_SPLIT_50":       dict(is_stop_loss=False, is_take_profit=True,  is_partial=True),
    "MIDLINE_PARTIAL_TP":   dict(is_stop_loss=False, is_take_profit=True,  is_partial=True),
    "FORCE_CLOSE":          dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
    "FORCE_CLOSE_BE":       dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
    "BE_ESCAPE":            dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
    "D1_RANGE_TP":          dict(is_stop_loss=False, is_take_profit=True,  is_partial=False),
    "MIDLINE_FULL_TP":      dict(is_stop_loss=False, is_take_profit=True,  is_partial=False),
    "MIDLINE_INVALIDATION": dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
    # Reconciliation (position vanished from the exchange): the daemon picks the reason
    # from the observed outcome - a genuine stop-out, a profitable non-stop close, or any
    # other external close (neither signal).
    "CLOSED_EXTERNAL_TP":   dict(is_stop_loss=False, is_take_profit=True,  is_partial=False),
    "CLOSED_EXTERNAL":      dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
}


def close_reason_flags(reason: str) -> Dict[str, bool]:
    """Flags for a close `reason`; raises ValueError for an unknown one."""
    try:
        return dict(CLOSE_REASON_FLAGS[reason])
    except KeyError:
        raise ValueError(
            f"Unknown close reason {reason!r}; add it to CLOSE_REASON_FLAGS "
            f"(known: {sorted(CLOSE_REASON_FLAGS)})"
        ) from None


class MMS4StateManager:
    """
    Manages MMS4 strategy execution state, equity, PnL tracking, and state transition triggers.
    
    Transition rules:
    - BASE_MODE -> SCOUT_MODE: Triggered when a Stop-Loss hit is >= 2.0% (0.02).
    - SCOUT_MODE -> BASE_MODE: Triggered on first Take-Profit (TP) hit, scout_override relaxation,
      or SCOUT_EXIT_K consecutive clean FORCE_CLOSE / MIDLINE_INVALIDATION closes (E3 of #107;
      see SCOUT_COUNTING_REASONS and `_apply_scout_exit_rule`).
    
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

        # Faza 3.2: per-symbol H4 directional-bias gate override, distinct from the
        # single global (Hermes-controlled) `_strategy_profile.directional_bias`.
        # Multiple symbols run concurrently (asyncio.gather in
        # execute_multi_symbol_cycle), so a live HTF gate MUST NOT mutate the shared
        # global field directly -- see get_effective_directional_bias below.
        self._symbol_htf_bias: Dict[str, str] = {}

        # Per-symbol count of consecutive clean closes while in SCOUT_MODE (see
        # SCOUT_EXIT_K); persisted under system_settings key `scout_ok:{symbol}`.
        self._scout_ok: Dict[str, int] = {}

        if self.logger_db is not None:
            self._restore_symbol_states_from_db()
            self._load_strategy_profile_from_db()

    def _restore_symbol_states_from_db(self) -> None:
        """Rebuild `_symbol_states` from the latest persisted state of EVERY symbol.

        This used to read only the single latest `state_history` row and hand it to the
        primary symbol, so after a restart a non-primary symbol (e.g. XAG) silently
        returned to BASE_MODE without a take-profit, and a latest row belonging to XAG
        put the primary symbol (BTC) into SCOUT_MODE. Legacy rows with no symbol at all
        are only ever applied to the primary symbol, and only when it has no tagged row.
        """
        if self.logger_db is None:
            return
        restored: Dict[str, MMS4State] = {}
        for symbol, row in self.logger_db.get_latest_symbol_states().items():
            state = self._parse_persisted_state(row)
            if state is not None:
                restored[symbol] = state
        if self.trading_symbol not in restored:
            state = self._parse_persisted_state(self.logger_db.get_latest_untagged_state())
            if state is not None:
                restored[self.trading_symbol] = state
        self._symbol_states.update(restored)
        if self.trading_symbol in restored:
            self._current_state = restored[self.trading_symbol]
        self._restore_scout_counters_from_db()

    def _restore_scout_counters_from_db(self) -> None:
        """Reload the clean-close counters of symbols that are (still) in SCOUT_MODE.

        A missing, garbled or out-of-range value restores 0 - safe, the symbol just needs
        K more clean closes. Keys of symbols no longer in SCOUT are ignored.
        """
        if self.logger_db is None:
            return
        try:
            settings = self.logger_db.get_all_settings()
        except Exception as e:
            logger.warning(f"Could not load SCOUT counters: {e}")
            return
        for symbol, state in self._symbol_states.items():
            if state != MMS4State.SCOUT_MODE:
                continue
            try:
                value = int(settings.get(f"{_SCOUT_OK_KEY_PREFIX}{symbol}", 0))
            except (TypeError, ValueError):
                value = 0
            if 0 < value < SCOUT_EXIT_K:
                self._scout_ok[symbol] = value

    def get_scout_ok(self, symbol: str) -> int:
        """Consecutive clean closes counted for `symbol` toward leaving SCOUT_MODE."""
        return self._scout_ok.get(symbol, 0)

    def _set_scout_ok(self, symbol: str, count: int, *, force: bool = False) -> None:
        """Set (and persist) a symbol's clean-close counter; 0 removes it.

        `force` also removes the persisted key when the in-memory counter was already 0, so
        a SCOUT transition can never leave a stale key behind to be restored later.
        """
        if count <= 0:
            if self._scout_ok.pop(symbol, 0) == 0 and not force:
                return
        else:
            self._scout_ok[symbol] = count
        if self.logger_db is not None:
            try:
                key = f"{_SCOUT_OK_KEY_PREFIX}{symbol}"
                if count > 0:
                    self.logger_db.set_setting(key, count, updated_by="state_manager")
                else:
                    self.logger_db.delete_setting(key)
            except Exception as e:
                logger.warning(f"Could not persist SCOUT counter for {symbol}: {e}")

    @staticmethod
    def _parse_persisted_state(row: Optional[Dict[str, Any]]) -> Optional[MMS4State]:
        """MMS4State from a state_history row; None when absent or not a known state."""
        if not row or not row.get("state"):
            return None
        try:
            return MMS4State(row["state"])
        except ValueError:
            logger.warning(f"Ignoring unknown persisted state {row['state']!r}")
            return None

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
        previous = self._symbol_states.get(symbol, MMS4State.BASE_MODE)
        self._symbol_states[symbol] = state
        if symbol == self.trading_symbol:
            self._current_state = state
        if self.logger_db:
            self.logger_db.save_state(
                state=state.value,
                leverage=self.get_symbol_leverage(symbol),
                reason=f"[{symbol}] {reason}",
                symbol=symbol,
            )
        if state != previous:
            # Every entry into / exit from SCOUT_MODE starts the clean-close count afresh. Done
            # AFTER the state is persisted: if persisting raised, the counter key must survive so
            # a restart sees a consistent (SCOUT + its counter) pair, not SCOUT with a lost count.
            self._set_scout_ok(symbol, 0, force=True)

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

    def get_symbol_size_multiplier(self, symbol: str) -> float:
        """
        Fraction of the normal allocation this symbol may commit right now.

        1.0 in BASE_MODE, SCOUT_SIZE_MULT in SCOUT_MODE. This is the axis that actually
        carries the defensive reduction — leverage only decides how much margin backs the
        resulting notional, and the exchange refuses to go below 1x anyway.
        """
        return SCOUT_SIZE_MULT if self.get_symbol_state(symbol) == MMS4State.SCOUT_MODE else 1.0

    @property
    def size_multiplier(self) -> float:
        """Size multiplier for the primary symbol."""
        return SCOUT_SIZE_MULT if self._current_state == MMS4State.SCOUT_MODE else 1.0

    def get_risk_profile_snapshot(self, symbol: str) -> Dict[str, Any]:
        """Everything the position sizer needs, resolved for one symbol."""
        state = self.get_symbol_state(symbol)
        leverage = self._resolve_leverage(state)
        size_mult = self.get_symbol_size_multiplier(symbol)
        base_leverage = self._resolve_leverage(MMS4State.BASE_MODE)
        return {
            "state": state.value,
            "leverage": leverage,
            "size_multiplier": size_mult,
            # How much smaller this symbol's notional is versus a full BASE_MODE entry.
            "notional_reduction_factor": (
                (base_leverage / (leverage * size_mult)) if leverage * size_mult > 0 else 0.0
            ),
        }

    def set_logger_db(self, logger_db: Any) -> None:
        """Attach LoggerDB instance and restore latest persisted state if present."""
        self.logger_db = logger_db
        self._restore_symbol_states_from_db()
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

    def get_symbol_htf_bias(self, symbol: str) -> str:
        """Faza 3.2: the H4 directional-bias gate's current reading for `symbol`
        ("BOTH" | "LONG_ONLY" | "SHORT_ONLY"), set by daemon.py's live H4 fetch when
        `htf_directional_gate_enabled` is on for that symbol. Defaults to "BOTH"
        (no gating) for any symbol the gate has never evaluated -- same fail-open
        default as a disabled gate."""
        return self._symbol_htf_bias.get(symbol, "BOTH")

    def set_symbol_htf_bias(self, symbol: str, bias: str) -> None:
        """Update the per-symbol HTF gate reading. Deliberately NOT persisted to
        logger_db (unlike update_strategy_profile) -- this is a fast-moving,
        re-derived-every-cycle signal, not an operator decision worth an audit
        trail entry, and never touches the account-wide
        `_strategy_profile.directional_bias` field."""
        self._symbol_htf_bias[symbol] = bias

    def get_effective_directional_bias(self, symbol: Optional[str] = None) -> str:
        """Faza 3.2: the directional_bias `evaluate_signals` should actually gate
        on for `symbol` -- combines the single global, Hermes-controlled manual
        override (`strategy_profile.directional_bias`) with the optional per-symbol
        automatic H4 gate (`_symbol_htf_bias`).

        Precedence: a manual override (anything other than the "BOTH"/no-opinion
        default) ALWAYS wins over the automatic gate -- an operator or Hermes
        explicitly setting LONG_ONLY/SHORT_ONLY/FLAT must never be silently
        overridden by an automatic H4 read. Only when the manual field is at its
        neutral default does the per-symbol HTF gate (if any) take effect. With no
        `symbol` (existing single-symbol callers/tests), or when the gate has never
        set anything for this symbol, this returns exactly what
        `strategy_profile.directional_bias` alone would have -- zero behavior
        change for every caller that predates Faza 3.2."""
        manual = self._strategy_profile.directional_bias
        if manual != "BOTH" or symbol is None:
            return manual
        return self.get_symbol_htf_bias(symbol)

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

        SCOUT_MODE always wins and returns SCOUT_LEVERAGE — a manual override never
        re-arms risk while the system is in its defensive mode. In BASE_MODE: an explicit
        override, else BASE_LEVERAGE_BY_PROFILE.

        Note that leverage alone no longer describes SCOUT's risk posture; pair it with
        `size_multiplier` / `get_symbol_size_multiplier()`.
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
            self.logger_db.save_state(
                state=self._current_state.value,
                leverage=self.leverage,
                reason=reason,
                symbol=self.trading_symbol,
            )

    def is_high_risk_add_allowed(self) -> bool:
        """
        Check if High Risk Add position is allowed in current state.
        BASE_MODE (risk-profile leverage) -> True
        SCOUT_MODE (reduced position size via SCOUT_SIZE_MULT, not a 0.1x leverage —
        that mechanism was retired, see the SCOUT_LEVERAGE/SCOUT_SIZE_MULT comment
        above) -> False
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
        sl_percent: float = 0.0,
        is_partial: bool = False,
    ) -> TradeRecord:
        """
        Record completed trade, update PnL, and evaluate state transitions.

        State transitions are applied to the trade's OWN symbol — previously the symbol
        was dropped here and the primary symbol was transitioned instead.
        Partial TP (is_partial=True) preserves SCOUT_MODE without transitioning to BASE_MODE.
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
            is_partial=is_partial,
            sl_percent=sl_percent,
            mode_at_execution=self._current_state
        )
        self._trade_history.append(record)
        self._realized_pnl += pnl
        self._current_equity += pnl

        if is_stop_loss:
            self.on_stop_loss_hit(sl_percent, sl_price=exit_price, symbol=symbol)
        elif is_take_profit:
            if is_partial:
                logger.info("Partial TP recorded — SCOUT_MODE state preserved until full position closure")
            else:
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

    def finalize_close(
        self,
        symbol: str,
        side: str,
        reason: str,
        entry_price: float,
        exit_price: float,
        amount: float,
        pnl: float,
        pnl_percent: float,
        sl_percent: float = 0.0,
        trade_id: Optional[str] = None,
        pnl_is_estimate: bool = False,
    ) -> TradeRecord:
        """Book a position close - the single entry point for every close path.

        `pnl_is_estimate=True` marks a PnL derived from a price that is not the fill (e.g. the
        last closed candle): it is still booked, but never counted toward (nor does it reset) the
        SCOUT clean-close run.

        Looks the SL/TP/partial flags up from `CLOSE_REASON_FLAGS` (so the daemon and the
        backtest cannot disagree) and delegates to `record_trade`. An unknown `reason`
        raises ValueError: a new close path must be classified, not silently ignored.
        """
        flags = close_reason_flags(reason)
        try:
            record = self.record_trade(
                trade_id=trade_id or f"{reason.lower()}_{symbol}_{side}_{int(time.time() * 1000)}",
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                amount=amount,
                pnl=pnl,
                pnl_percent=pnl_percent,
                sl_percent=sl_percent,
                **flags,
            )
        except Exception:
            # Issue #99 invariant: the exchange-side close already happened, so a FULL
            # take-profit must restore SCOUT_MODE -> BASE_MODE even when the bookkeeping
            # itself failed. (Idempotent: a no-op when record_trade already restored it.)
            if flags["is_take_profit"] and not flags["is_partial"]:
                self.on_take_profit_hit(symbol=symbol)
            raise
        self._apply_scout_exit_rule(symbol, reason, flags, pnl_percent, pnl_is_estimate)
        return record

    def _apply_scout_exit_rule(
        self, symbol: str, reason: str, flags: Dict[str, bool], pnl_percent: float,
        pnl_is_estimate: bool = False,
    ) -> None:
        """E3 of #107: count clean full closes and leave SCOUT_MODE after SCOUT_EXIT_K.

        Never raises: this runs after the close is booked, so a failure here must not turn
        into a failed close (the daemon already acted on the exchange).
        """
        try:
            if self.get_symbol_state(symbol) != MMS4State.SCOUT_MODE:
                return  # not in SCOUT (a full take-profit has already exited it)
            if flags["is_stop_loss"]:
                self._set_scout_ok(symbol, 0)
                return
            if flags["is_partial"] or flags["is_take_profit"]:
                return  # partial closes are neutral
            if reason not in SCOUT_COUNTING_REASONS:
                return  # FORCE_CLOSE_BE / BE_ESCAPE / CLOSED_EXTERNAL are neutral
            if pnl_is_estimate or not math.isfinite(pnl_percent):
                logger.info(
                    f"SCOUT run for {symbol} unchanged: {reason} has no real fill price "
                    f"(estimate={pnl_is_estimate}, pnl_percent={pnl_percent})."
                )
                return
            if pnl_percent <= SCOUT_MIN_GROSS_PNL_PCT:
                self._set_scout_ok(symbol, 0)  # a net-losing close breaks the run
                return
            count = self.get_scout_ok(symbol) + 1
            if count >= SCOUT_EXIT_K:
                self.set_symbol_state(
                    symbol, MMS4State.BASE_MODE,
                    reason=f"SCOUT_EXPIRED_K: {count} consecutive clean closes",
                )
            else:
                self._set_scout_ok(symbol, count)
        except Exception as e:
            logger.error(f"SCOUT exit rule failed for {symbol} ({reason}): {e}")

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

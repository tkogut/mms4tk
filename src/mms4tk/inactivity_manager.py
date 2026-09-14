"""
MMS4 Inactivity Manager — Stale Position Detection & Evacuation Engine.

Implements 4 iron rules from MMS4 Expert Knowledge:
  I.   50% Impulse Negation — cut when retracement exceeds 50% of local impulse
  II.  Break-Even Escape — evacuate at entry price after stagnation period
  III. Time Decompress (Split 50%) — partial close after max_stale_candles
  IV.  Two-Stage Ladder — full close after additional grace candles post-split

Source: .agents/specs/knowledge/mms4-stale-positions-report.md
Tags: #NotebookLM #ExpertKnowledge
"""

import logging
import os
import time
from enum import Enum
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("mms4tk.inactivity")


class StaleAction(str, Enum):
    """Actions emitted by the Inactivity Manager."""
    HOLD = "HOLD"
    SPLIT_50 = "SPLIT_50"              # Rule III: Partial close 50%
    FORCE_CLOSE_BE = "FORCE_CLOSE_BE"  # Rule IV: Full close at break-even/market
    FORCE_CLOSE = "FORCE_CLOSE"        # Rule I: Impulse negated, full cut
    BE_ESCAPE = "BE_ESCAPE"            # Rule II: Price returned to entry after stagnation


@dataclass
class PositionTracker:
    """Per-symbol position tracking state."""
    inst_id: str
    entry_price: float
    entry_time: float
    side: str  # "long" or "short"
    atr_at_entry: float
    candle_count: int = 0
    max_observed_price: float = 0.0
    min_observed_price: float = float("inf")
    is_split: bool = False
    split_at_candle: int = 0
    stale_detected: bool = False
    profit_stall_candles: int = 0
    last_peak_price: float = 0.0
    peak_profit_atr: float = 0.0
    # Candle index of the last emitted protective action; guards against re-issuing the
    # same reduction on every cycle while the triggering condition persists.
    last_action_candle: int = -1
    last_action: str = ""
    # Set once a full-close action has been emitted. The daemon stops tracking after
    # executing it; this flag is the belt-and-braces guard in case that call is missed.
    closing: bool = False

    def __post_init__(self):
        if self.side == "long":
            self.max_observed_price = self.entry_price
            self.last_peak_price = self.entry_price
        else:
            self.min_observed_price = self.entry_price
            self.last_peak_price = self.entry_price


class MMS4InactivityManager:
    """
    Monitors open positions for stagnation and emits protective actions.

    Two-Stage Ladder (per plan):
      Stage 1 — After max_stale_candles (default 12) of price within
                vol_compression_threshold * ATR of entry → SPLIT_50
      Stage 2 — After additional grace_candles (default 5) post-split
                with continued stagnation → FORCE_CLOSE_BE

    Impulse Negation (Rule I):
      If price made a meaningful impulse (> 0.3 * ATR) then retraced > 50%
      (or > 38.2% for impulse >= 0.8 * ATR)
      → SPLIT_50 (first occurrence) or FORCE_CLOSE (after split)

    Break-Even Escape (Rule II):
      After stagnation is detected, if price returns within 0.1-0.2% of entry
      → BE_ESCAPE (close at market for ~zero loss)
    """

    # Default configuration — overridable per-symbol via config
    DEFAULT_MAX_STALE_CANDLES: int = 12
    DEFAULT_VOL_COMPRESSION_THRESHOLD: float = 0.5  # fraction of ATR
    DEFAULT_GRACE_CANDLES: int = 5
    DEFAULT_IMPULSE_NEGATION_ENABLED: bool = True
    DEFAULT_BE_ESCAPE_THRESHOLD: float = float(os.getenv("MMS4_BE_THRESHOLD", "0.002"))  # 0.2% of entry price
    # Round-trip cost allowance (taker fee on both legs + funding). A "break-even" exit
    # inside the raw price band still realizes a small loss once fees are paid; set this
    # to e.g. 0.0012 (OKX taker 0.05% x2 + funding) to require BE_ESCAPE to be net
    # non-negative.
    DEFAULT_ROUND_TRIP_COST_PCT: float = float(os.getenv("MMS4_BE_COST_PCT", "0.0012"))
    # Minimum candles between two protective actions on the same position.
    DEFAULT_ACTION_COOLDOWN_CANDLES: int = 1
    # Impulse size (in ATR) that must be made before a 50% retracement counts as a
    # negation. At 0.3 this is tight: on a low-ATR instrument the trigger can sit within
    # market noise (SOL/USDC showed a $0.01 threshold in the audit). Raise per symbol via
    # the `impulse_min_atr` config key when an instrument whipsaws out of positions.
    DEFAULT_IMPULSE_MIN_ATR: float = float(os.getenv("MMS4_IMPULSE_MIN_ATR", "0.3"))

    def __init__(self):
        self._trackers: Dict[str, PositionTracker] = {}
        # Per-symbol config overrides: {symbol: {max_stale_candles, vol_threshold, ...}}
        self._symbol_configs: Dict[str, Dict[str, Any]] = {}

    # ── Configuration ───────────────────────────────────────────────

    def set_symbol_config(self, symbol: str, config: Dict[str, Any]) -> None:
        """Set per-symbol inactivity parameters."""
        self._symbol_configs[symbol] = config
        logger.info(f"Inactivity config updated for {symbol}: {config}")

    def _get_cfg(self, symbol: str, key: str, default: Any) -> Any:
        """Resolve config value: per-symbol override → class default."""
        return self._symbol_configs.get(symbol, {}).get(key, default)

    # ── Lifecycle ───────────────────────────────────────────────────

    def track_position(
        self,
        inst_id: str,
        entry_price: float,
        entry_time: float,
        side: str,
        atr: float
    ) -> None:
        """Start tracking a newly opened position."""
        self._trackers[inst_id] = PositionTracker(
            inst_id=inst_id,
            entry_price=entry_price,
            entry_time=entry_time,
            side=side.lower(),
            atr_at_entry=atr,
        )
        logger.info(
            f"▶ Tracking position {inst_id} | side={side} "
            f"entry=${entry_price:,.2f} ATR=${atr:,.2f}"
        )

    def stop_tracking(self, inst_id: str) -> None:
        """Remove position from monitoring after close."""
        if inst_id in self._trackers:
            t = self._trackers.pop(inst_id)
            logger.info(
                f"■ Stopped tracking {inst_id} after {t.candle_count} candles "
                f"(split={t.is_split})"
            )

    def is_tracking(self, inst_id: str) -> bool:
        """Check if a position is currently being tracked."""
        return inst_id in self._trackers

    def get_tracker(self, inst_id: str) -> Optional[PositionTracker]:
        """Return tracker state for external inspection (dashboard, API)."""
        return self._trackers.get(inst_id)

    @property
    def active_trackers(self) -> Dict[str, PositionTracker]:
        """Return all active trackers (read-only snapshot)."""
        return dict(self._trackers)

    # ── Core Evaluation ─────────────────────────────────────────────

    def update_and_check(
        self,
        inst_id: str,
        current_price: float,
        current_atr: float,
        new_candle_closed: bool
    ) -> StaleAction:
        """
        Evaluate position health on each candle close.

        Called by daemon.execute_symbol_cycle() after fetching fresh candles.
        Returns the recommended protective action.
        """
        tracker = self._trackers.get(inst_id)
        if tracker is None:
            return StaleAction.HOLD

        # ── Reject non-prices ──
        # A degenerate cycle (empty candle response) yields price 0.0. Folding that into
        # the observed extremes permanently poisons the impulse maths: for a short,
        # min_observed becomes 0 and every real price then reads as a >50% retracement.
        if current_price is None or current_price <= 0 or current_price != current_price:
            logger.warning(
                f"Ignoring invalid price {current_price!r} for {inst_id} — tracker left untouched"
            )
            return StaleAction.HOLD

        # ── Update price extremes ──
        if tracker.side == "long":
            tracker.max_observed_price = max(tracker.max_observed_price, current_price)
        else:
            tracker.min_observed_price = min(tracker.min_observed_price, current_price)

        # ── Increment candle counter ──
        if new_candle_closed:
            tracker.candle_count += 1

        # ── Load per-symbol config ──
        max_stale = self._get_cfg(
            inst_id, "stale_candles_threshold", self.DEFAULT_MAX_STALE_CANDLES
        )
        vol_threshold = self._get_cfg(
            inst_id, "stale_atr_threshold", self.DEFAULT_VOL_COMPRESSION_THRESHOLD
        )
        grace = self._get_cfg(
            inst_id, "grace_candles", self.DEFAULT_GRACE_CANDLES
        )
        impulse_enabled = self._get_cfg(
            inst_id, "impulse_negation_enabled", self.DEFAULT_IMPULSE_NEGATION_ENABLED
        )
        be_threshold = self._get_cfg(
            inst_id, "be_escape_threshold", self.DEFAULT_BE_ESCAPE_THRESHOLD
        )
        cost_pct = self._get_cfg(
            inst_id, "round_trip_cost_pct", self.DEFAULT_ROUND_TRIP_COST_PCT
        )
        cooldown = self._get_cfg(
            inst_id, "action_cooldown_candles", self.DEFAULT_ACTION_COOLDOWN_CANDLES
        )

        # Use current ATR if available, else entry ATR as baseline
        atr = current_atr if current_atr > 0 else tracker.atr_at_entry
        if atr <= 0:
            return StaleAction.HOLD

        # A full close is already in flight — never queue a second one.
        if tracker.closing:
            return StaleAction.HOLD

        # A protective action was just issued; give the exchange a candle to settle
        # before evaluating again, otherwise a persistent condition re-fires every cycle.
        in_cooldown = (
            tracker.last_action_candle >= 0
            and (tracker.candle_count - tracker.last_action_candle) < cooldown
        )
        if in_cooldown:
            return StaleAction.HOLD

        # Price left the stagnation zone — the position is alive again, so the stale flag
        # must clear. Without this reset any later drift back toward entry re-triggers
        # BE_ESCAPE, even after a multi-ATR breakout.
        if tracker.stale_detected:
            if abs(current_price - tracker.entry_price) >= atr * vol_threshold:
                logger.info(
                    f"↻ {inst_id} left the stagnation zone "
                    f"(deviation=${abs(current_price - tracker.entry_price):,.2f}) — stale flag cleared"
                )
                tracker.stale_detected = False
                tracker.is_split = False
                tracker.split_at_candle = 0

        # ──────────────────────────────────────────────────────────
        # RULE 0: Profit Stall Gate (uPnL >= 0.6 ATR stalled 4 candles)
        # ──────────────────────────────────────────────────────────
        profit_atr = (
            (current_price - tracker.entry_price) / atr
            if tracker.side == "long"
            else (tracker.entry_price - current_price) / atr
        )
        tracker.peak_profit_atr = max(tracker.peak_profit_atr, profit_atr)

        # ──────────────────────────────────────────────────────────
        # Profit Giveback Protection (Fast Reversal Guard)
        # ──────────────────────────────────────────────────────────
        giveback_min_peak = self._get_cfg(inst_id, "giveback_min_peak_atr", 1.0)
        if tracker.peak_profit_atr >= giveback_min_peak and profit_atr <= tracker.peak_profit_atr * 0.5:
            if not tracker.is_split:
                logger.warning(
                    f"🛡️ Profit Giveback Protection triggered for {inst_id} "
                    f"(peak={tracker.peak_profit_atr:.2f} ATR -> now={profit_atr:.2f} ATR) → SPLIT_50"
                )
                tracker.is_split = True
                tracker.split_at_candle = tracker.candle_count
                return self._arm(tracker, StaleAction.SPLIT_50)
        if profit_atr >= 0.6:
            is_new_peak = (
                current_price > tracker.last_peak_price
                if tracker.side == "long"
                else current_price < tracker.last_peak_price
            )
            if is_new_peak:
                tracker.last_peak_price = current_price
                tracker.profit_stall_candles = 0
            elif new_candle_closed:
                tracker.profit_stall_candles += 1

            if tracker.profit_stall_candles >= 4 and not tracker.is_split:
                logger.warning(
                    f"⏰ Profit Stall Gate triggered for {inst_id} (4 candles stalled at profit) → SPLIT_50"
                )
                tracker.is_split = True
                tracker.split_at_candle = tracker.candle_count
                return self._arm(tracker, StaleAction.SPLIT_50)
        else:
            tracker.profit_stall_candles = 0

        # ──────────────────────────────────────────────────────────
        # RULE I: 50% / 38.2% Impulse Negation
        # ──────────────────────────────────────────────────────────
        if impulse_enabled:
            action = self._check_impulse_negation(
                tracker, current_price, atr,
                min_impulse_atr=self._get_cfg(inst_id, "impulse_min_atr", self.DEFAULT_IMPULSE_MIN_ATR),
            )
            if action != StaleAction.HOLD:
                return self._arm(tracker, action)

        # ──────────────────────────────────────────────────────────
        # RULE III + IV: Time-Based Stagnation (Two-Stage Ladder)
        # ──────────────────────────────────────────────────────────
        # Evaluated BEFORE Rule II: the break-even band (0.1% of price) sits inside the
        # stagnation band (0.5x ATR) for any instrument with ATR above ~0.2%, so checking
        # BE first made the second ladder rung unreachable in practice.
        if tracker.candle_count >= max_stale:
            price_deviation = abs(current_price - tracker.entry_price)
            max_allowed = atr * vol_threshold

            if price_deviation < max_allowed:
                tracker.stale_detected = True

                # Stage 2 first: grace period after a split expired → full close.
                if tracker.is_split:
                    candles_since_split = tracker.candle_count - tracker.split_at_candle
                    if candles_since_split >= grace:
                        logger.warning(
                            f"🚨 Grace period expired for {inst_id} | "
                            f"{candles_since_split} candles post-split without recovery"
                        )
                        return self._arm(tracker, StaleAction.FORCE_CLOSE_BE)
                else:
                    # Stage 1: first split
                    logger.warning(
                        f"⏰ Stagnation detected for {inst_id} | "
                        f"{tracker.candle_count} candles, deviation=${price_deviation:,.2f} "
                        f"< threshold=${max_allowed:,.2f} ({vol_threshold}×ATR)"
                    )
                    tracker.is_split = True
                    tracker.split_at_candle = tracker.candle_count
                    return self._arm(tracker, StaleAction.SPLIT_50)

        # ──────────────────────────────────────────────────────────
        # RULE II: Break-Even Escape (after stagnation detected)
        # ──────────────────────────────────────────────────────────
        # Only escape once price has cleared the round-trip cost, otherwise "break even"
        # is a guaranteed small loss.
        if tracker.stale_detected:
            if tracker.side == "long":
                pnl_pct = (current_price - tracker.entry_price) / tracker.entry_price
            else:
                pnl_pct = (tracker.entry_price - current_price) / tracker.entry_price

            within_band = abs(current_price - tracker.entry_price) <= tracker.entry_price * be_threshold
            if within_band:
                if pnl_pct >= cost_pct:
                    logger.warning(
                        f"🏃 BE Escape triggered for {inst_id} | "
                        f"price=${current_price:,.2f} ≈ entry=${tracker.entry_price:,.2f} "
                        f"(PnL {pnl_pct*100:.3f}%, cost allowance {cost_pct*100:.3f}%)"
                    )
                    return self._arm(tracker, StaleAction.BE_ESCAPE)
                logger.info(
                    f"BE Escape for {inst_id} deferred: PnL {pnl_pct*100:.3f}% "
                    f"below round-trip cost {cost_pct*100:.3f}% — the two-stage ladder "
                    f"remains the exit path"
                )

        return StaleAction.HOLD

    def _arm(self, tracker: PositionTracker, action: StaleAction) -> StaleAction:
        """Record an emitted action so the cooldown can suppress immediate repeats."""
        tracker.last_action_candle = tracker.candle_count
        tracker.last_action = action.value
        if action in (StaleAction.FORCE_CLOSE, StaleAction.FORCE_CLOSE_BE, StaleAction.BE_ESCAPE):
            tracker.closing = True
        return action

    # ── Private Helpers ─────────────────────────────────────────────

    def _check_impulse_negation(
        self,
        tracker: PositionTracker,
        current_price: float,
        atr: float,
        min_impulse_atr: Optional[float] = None
    ) -> StaleAction:
        """
        Rule I: If price made a meaningful impulse (> 0.3 * ATR in profitable
        direction) then retraced > 50% of that impulse, the move has lost momentum.

        The first negation halves the position and LATCHES `is_split`. Without that latch
        the rule re-emitted SPLIT_50 on every cycle while price sat below the negation
        level, and — because reduce_position rounds a 1-contract halving up to 1 — drained
        the whole position within a few cycles in separate taker orders.
        """
        if min_impulse_atr is None:
            min_impulse_atr = self.DEFAULT_IMPULSE_MIN_ATR
        min_impulse = min_impulse_atr * atr

        if tracker.side == "long":
            local_impulse = tracker.max_observed_price - tracker.entry_price
            fib_ratio = 0.382 if local_impulse >= 0.8 * atr else 0.5
            negation_level = tracker.max_observed_price - (local_impulse * fib_ratio)
            negated = local_impulse >= min_impulse and current_price < negation_level
            peak_label, peak_value = "peak", tracker.max_observed_price
        else:
            local_impulse = tracker.entry_price - tracker.min_observed_price
            fib_ratio = 0.382 if local_impulse >= 0.8 * atr else 0.5
            negation_level = tracker.min_observed_price + (local_impulse * fib_ratio)
            negated = local_impulse >= min_impulse and current_price > negation_level
            peak_label, peak_value = "trough", tracker.min_observed_price

        if not negated:
            return StaleAction.HOLD

        logger.warning(
            f"⚡ Impulse negation ({tracker.side.upper()}) for {tracker.inst_id} | "
            f"{peak_label}=${peak_value:,.2f} fib={fib_ratio*100:.1f}% negation_level=${negation_level:,.2f} "
            f"current=${current_price:,.2f}"
        )

        if tracker.is_split:
            return StaleAction.FORCE_CLOSE

        tracker.is_split = True
        tracker.split_at_candle = tracker.candle_count
        return StaleAction.SPLIT_50

    # ── Status / Telemetry ──────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Return summary of all tracked positions for API/dashboard."""
        positions = {}
        for inst_id, t in self._trackers.items():
            max_stale = self._get_cfg(
                inst_id, "stale_candles_threshold", self.DEFAULT_MAX_STALE_CANDLES
            )
            positions[inst_id] = {
                "side": t.side,
                "entry_price": t.entry_price,
                "candle_count": t.candle_count,
                "max_stale_candles": max_stale,
                "is_split": t.is_split,
                "stale_detected": t.stale_detected,
                "atr_at_entry": t.atr_at_entry,
                "max_observed_price": t.max_observed_price,
                "min_observed_price": t.min_observed_price,
                "peak_profit_atr": t.peak_profit_atr,
                "age_seconds": int(time.time() - t.entry_time),
            }
        return {
            "tracked_positions": len(self._trackers),
            "positions": positions
        }

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

    def __post_init__(self):
        if self.side == "long":
            self.max_observed_price = self.entry_price
        else:
            self.min_observed_price = self.entry_price


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
      → SPLIT_50 (first occurrence) or FORCE_CLOSE (after split)

    Break-Even Escape (Rule II):
      After stagnation is detected, if price returns within 0.1% of entry
      → BE_ESCAPE (close at market for ~zero loss)
    """

    # Default configuration — overridable per-symbol via config
    DEFAULT_MAX_STALE_CANDLES: int = 12
    DEFAULT_VOL_COMPRESSION_THRESHOLD: float = 0.5  # fraction of ATR
    DEFAULT_GRACE_CANDLES: int = 5
    DEFAULT_IMPULSE_NEGATION_ENABLED: bool = True
    DEFAULT_BE_ESCAPE_THRESHOLD: float = 0.001  # 0.1% of entry price

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

        # Use current ATR if available, else entry ATR as baseline
        atr = current_atr if current_atr > 0 else tracker.atr_at_entry
        if atr <= 0:
            return StaleAction.HOLD

        # ──────────────────────────────────────────────────────────
        # RULE I: 50% Impulse Negation
        # ──────────────────────────────────────────────────────────
        if impulse_enabled:
            action = self._check_impulse_negation(tracker, current_price, atr)
            if action != StaleAction.HOLD:
                return action

        # ──────────────────────────────────────────────────────────
        # RULE II: Break-Even Escape (after stagnation detected)
        # ──────────────────────────────────────────────────────────
        if tracker.stale_detected:
            price_from_entry = abs(current_price - tracker.entry_price)
            be_zone = tracker.entry_price * be_threshold
            if price_from_entry <= be_zone:
                logger.warning(
                    f"🏃 BE Escape triggered for {inst_id} | "
                    f"price=${current_price:,.2f} ≈ entry=${tracker.entry_price:,.2f}"
                )
                return StaleAction.BE_ESCAPE

        # ──────────────────────────────────────────────────────────
        # RULE III + IV: Time-Based Stagnation (Two-Stage Ladder)
        # ──────────────────────────────────────────────────────────
        if tracker.candle_count >= max_stale:
            price_deviation = abs(current_price - tracker.entry_price)
            max_allowed = atr * vol_threshold

            if price_deviation < max_allowed:
                tracker.stale_detected = True

                # Stage 1: First split
                if not tracker.is_split:
                    logger.warning(
                        f"⏰ Stagnation detected for {inst_id} | "
                        f"{tracker.candle_count} candles, deviation=${price_deviation:,.2f} "
                        f"< threshold=${max_allowed:,.2f} (0.5×ATR)"
                    )
                    tracker.is_split = True
                    tracker.split_at_candle = tracker.candle_count
                    return StaleAction.SPLIT_50

                # Stage 2: Grace period expired → full close
                candles_since_split = tracker.candle_count - tracker.split_at_candle
                if candles_since_split >= grace:
                    logger.warning(
                        f"🚨 Grace period expired for {inst_id} | "
                        f"{candles_since_split} candles post-split without recovery"
                    )
                    return StaleAction.FORCE_CLOSE_BE

        return StaleAction.HOLD

    # ── Private Helpers ─────────────────────────────────────────────

    def _check_impulse_negation(
        self,
        tracker: PositionTracker,
        current_price: float,
        atr: float
    ) -> StaleAction:
        """
        Rule I: If price made a meaningful impulse (> 0.3 * ATR in profitable
        direction) then retraced > 50% of that impulse, the move has lost momentum.
        """
        min_impulse = 0.3 * atr

        if tracker.side == "long":
            local_impulse = tracker.max_observed_price - tracker.entry_price
            if local_impulse > min_impulse:
                negation_level = tracker.max_observed_price - (local_impulse * 0.5)
                if current_price < negation_level:
                    logger.warning(
                        f"⚡ Impulse negation (LONG) for {tracker.inst_id} | "
                        f"peak=${tracker.max_observed_price:,.2f} "
                        f"50%=${negation_level:,.2f} current=${current_price:,.2f}"
                    )
                    return StaleAction.SPLIT_50 if not tracker.is_split else StaleAction.FORCE_CLOSE
        else:
            local_impulse = tracker.entry_price - tracker.min_observed_price
            if local_impulse > min_impulse:
                negation_level = tracker.min_observed_price + (local_impulse * 0.5)
                if current_price > negation_level:
                    logger.warning(
                        f"⚡ Impulse negation (SHORT) for {tracker.inst_id} | "
                        f"trough=${tracker.min_observed_price:,.2f} "
                        f"50%=${negation_level:,.2f} current=${current_price:,.2f}"
                    )
                    return StaleAction.SPLIT_50 if not tracker.is_split else StaleAction.FORCE_CLOSE

        return StaleAction.HOLD

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
                "age_seconds": int(time.time() - t.entry_time),
            }
        return {
            "tracked_positions": len(self._trackers),
            "positions": positions
        }

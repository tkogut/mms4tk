"""PLAN-010 Phase 2: maintained, tested calibration engine for the TMA slope trend
filter — promotes the scratchpad harness used for the 2026-08-29 review
(`backtest_plan008_phase3_v2.py`, `oos_train_test_v2.py`) into production code, so
recalibration is reproducible from the API/UI instead of re-derived from throwaway
scripts each time.

Design principles carried over from that review (do not relax without re-deriving
the evidence that justified them):

  * SPLIT_50 mirrors the REAL `OKXExecutor.reduction_amount()` no-op rule — a
    position that can't be halved without fully closing it is HELD, not partially
    booked. The original v1 harness always booked a partial, which inflated its
    apparent edge; fixing this did NOT change the qualitative overfitting verdict,
    but any future harness change must preserve this fidelity.
  * A calibration is only trustworthy if it passes train/test validation in BOTH
    directions (train H1 -> test H2, AND train H2 -> test H1) — full-window stats
    alone produced the false positive that shipped in PLAN-008 Phase 3 and had to be
    walked back 2026-08-29. `train_test_validate` enforces this structurally: there
    is no code path that reports a verdict from a single direction.
  * The BASE_MODE/SCOUT_MODE state machine is modeled faithfully (2026-09-02): a
    real `MMS4StateManager` tracks state across closed trades, so a >=2% stop-loss
    correctly shrinks subsequent entries the way it does in production, instead of
    every entry being sized as if BASE_MODE never left.
"""
import asyncio
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

import pandas as pd

from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine, TMAIndicator
from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction
from mms4tk.logger_db import LoggerDB
from mms4tk.okx_executor import OKXExecutor
from mms4tk.state_manager import MMS4StateManager, MMS4State

BASE_SL_PCT = 0.02
STALE_ATR_THRESHOLD = 0.5
IMPULSE_NEGATION_ENABLED = True
MIN_FULL_CLOSES_HALF = 12  # below this, a half-window config's stats are too noisy to trust

# HIGH_RISK_ADD (addon) constants, mirroring daemon.py's own defaults/env vars exactly
# so an operator's .env change to the live values is picked up here too.
BASE_ALLOCATION = 0.70
ADDON_ALLOCATION = 0.30
ADDON_SL_PCT = 0.0125  # tighter COMPOSITE stop-loss for the whole position once an addon exists

# Real base-entry contract size per symbol at the CURRENT allocation, from
# `OKXExecutor.calculate_order_amount()` against the live-resolved XPERP contract
# size (verified 2026-08-29 via `discover_eea_xperp_symbol()` — NOT the raw OKX
# instId string, whose contract metadata is not indexed the same way in
# `exchange.markets`). Used only for the SPLIT_50 `reduction_amount()` no-op check,
# not for real order sizing.
#
# 2026-08-29: allocation reduced $100 -> $50/symbol alongside unlocking trading —
# these MUST be updated again if allocation changes, or the no-op check silently
# reverts to modeling the OLD position-size regime.
#   BTC/USDC @ $50 (70/30 base/add): base=4 contracts, addon=2
#   ETH/USDC @ $50 (70/30 base/add): base=14 contracts, addon=6
BASE_CONTRACTS: Dict[str, float] = {"BTC/USDC": 4.0, "ETH/USDC": 14.0}
BASE_CONTRACTS_DEFAULT = 2.0


def _trade_record_flags(exit_reason: str) -> Dict[str, bool]:
    """Map a harness `exit_reason` to the `(is_stop_loss, is_take_profit, is_partial)`
    flags `MMS4StateManager.record_trade()` needs to reproduce the real
    BASE_MODE/SCOUT_MODE state machine (see `state_manager.py`'s `on_stop_loss_hit`/
    `on_take_profit_hit`, both invoked from `record_trade`). `sl_percent` is NOT
    resolved here — the caller passes the trade's actual stop-loss percentage.

    FORCE_CLOSE/FORCE_CLOSE_BE/BE_ESCAPE map to neither SL nor TP: production's own
    `inactivity_manager.py`/`daemon.py` never call either state hook for these exits
    (they are inactivity-driven exits, not a signal-driven take-profit or a hard
    stop-loss fill) — confirmed by grep, not assumed.
    """
    table = {
        "STOP_LOSS":          dict(is_stop_loss=True,  is_take_profit=False, is_partial=False),
        "TP_AND_FLIP":        dict(is_stop_loss=False, is_take_profit=True,  is_partial=False),
        "STALE_SPLIT_50":     dict(is_stop_loss=False, is_take_profit=True,  is_partial=True),
        "MIDLINE_PARTIAL_TP": dict(is_stop_loss=False, is_take_profit=True,  is_partial=True),
        "FORCE_CLOSE":        dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
        "FORCE_CLOSE_BE":     dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
        "BE_ESCAPE":          dict(is_stop_loss=False, is_take_profit=False, is_partial=False),
    }
    return table[exit_reason]


def _size_new_entry(symbol: str, sm: MMS4StateManager) -> Dict[str, Any]:
    """Contracts + bookkeeping for a fresh BASE entry, scaled by SCOUT_MODE if the
    state machine is currently in it — mirrors `daemon.py`'s `plan_allocation()`
    (`margin_base * size_mult * leverage`) for the SIZE axis; the LEVERAGE axis only
    matters for `notional_weight` (see below), since `pnl_pct` is price-relative,
    not margin-relative.

    `notional_weight` is a *notional* fraction of a full BASE_MODE entry — NOT a
    contracts fraction — reusing `get_risk_profile_snapshot()`'s own
    `notional_reduction_factor` (`base_leverage / (leverage * size_mult)`) rather
    than re-deriving it, so this can never silently drift from what
    `state_manager.py` actually computes. ~1.0 for a full BASE entry (matches
    pre-SCOUT-fidelity behavior exactly), ~0.1 for a SCOUT entry at defaults
    (leverage 1.0x vs base 3.0x, size 0.3x)."""
    snap = sm.get_risk_profile_snapshot(symbol)
    size_mult = float(snap["size_multiplier"])
    reduction_factor = float(snap["notional_reduction_factor"]) or 1.0
    # Known simplification: a SCOUT_MODE entry can come out fractional here (e.g.
    # 4.0 * 0.3 = 1.2 "contracts"), unlike a real order which is always a whole
    # number. reduction_amount()'s floor() handles it without crashing, but this
    # is not a claim that SCOUT-sized backtest positions are literally
    # order-representable — only that their NOTIONAL is proportionally correct.
    contracts = BASE_CONTRACTS.get(symbol, BASE_CONTRACTS_DEFAULT) * size_mult
    return {
        "contracts": contracts,
        "notional_weight": 1.0 / reduction_factor,
        "state_at_entry": snap["state"],
    }


def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """pnl/R:R weighted by each trade's `weight` — a NOTIONAL fraction of a full
    BASE_MODE entry (see `_size_new_entry`'s `notional_weight`), not a contracts
    fraction. A SPLIT_50/Midline-TP-reduced remainder, or any entry sized while in
    SCOUT_MODE, contributes proportionally less than a full-size BASE entry.

    Semantic note: this changed 2026-09 from a pure `contracts/base_contracts`
    ratio once SCOUT_MODE sizing landed — any `trend_filter_calibration_runs` row
    persisted before that change is not bit-for-bit comparable to a post-change run
    for any window that included a >=2% stop-loss (full-BASE-only runs, i.e. ones
    that never triggered SCOUT_MODE, are unaffected — `notional_weight` was already
    1.0 there)."""
    if not trades:
        return dict(win_rate=0.0, rr=float("nan"), total_pnl=0.0, full_closes=0)
    for t in trades:
        t.setdefault("weight", 1.0)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    win_rate = len(wins) / len(trades) * 100
    w_win = sum(t["weight"] for t in wins) or 1.0
    w_loss = sum(t["weight"] for t in losses) or 1.0
    avg_win = sum(t["pnl_pct"] * t["weight"] for t in wins) / w_win if wins else 0.0
    avg_loss = abs(sum(t["pnl_pct"] * t["weight"] for t in losses) / w_loss) if losses else 0.0
    rr = (avg_win / avg_loss) if avg_loss > 0 else float("nan")
    total_pnl = sum(t["pnl_pct"] * t["weight"] for t in trades)
    full_closes = len([t for t in trades if not t.get("partial")])
    return dict(win_rate=win_rate, rr=rr, total_pnl=total_pnl, full_closes=full_closes)


def run_backtest(
    symbol: str,
    candles: List[Candle],
    params: IndicatorParams,
    stale_candles_threshold: int,
    trend_filter_enabled: bool,
    slope_lookback: int = 5,
    slope_threshold: float = 0.002,
    midline_tp_enabled: bool = False,
    midline_tp_fraction: float = 0.50,
) -> List[Dict[str, Any]]:
    """Single-symbol, candle-granularity simulation using the real production
    engine classes. Returns closed trades (dicts: side, entry_price, exit_price,
    pnl_pct, exit_reason, weight, optionally partial=True).

    `MMS4InactivityManager` is the same class daemon.py uses in production, so
    Inactivity v2's Profit Stall Gate and 38.2% Fibonacci impulse negation
    (PLAN-009 v2, shipped 2026-08-31) are modeled automatically here — no harness
    change needed for those. Midline Partial TP lives in `daemon.py`'s
    `execute_symbol_cycle()` instead (not in the inactivity manager), so it IS
    modeled explicitly below, mirroring that function's exact trigger/guard logic:
    TMA-midline touch in the position's favor, evaluated BEFORE inactivity checks,
    fires at most once per position (a fresh position clears the flag), skipped for
    positions the real `reduction_amount()` can't halve (mirrors the ledger's
    fail-closed / micro-position guard), hard stop-loss stays at its original
    entry-relative price (NOT moved to break-even) — matching the real
    implementation's choice not to lock in break-even, just to de-risk gains.

    Also models the BASE_MODE/SCOUT_MODE state machine faithfully: `sm` is a real
    `MMS4StateManager` (no `logger_db` — every write site in that class is already
    guarded with `if self.logger_db is not None`, so state transitions/sizing work
    identically without one, and this avoids a synchronous SQLite write per closed
    trade inside a large grid search). A >=2% stop-loss transitions the symbol to
    SCOUT_MODE (shrinks new entries via `_size_new_entry`); only a FULL (non-partial)
    take-profit-flagged close exits it — a partial (SPLIT_50/Midline TP) explicitly
    preserves SCOUT_MODE, matching `record_trade()`'s own behavior exactly.

    Also models HIGH_RISK_ADD (addon) signals, previously silently dropped (the
    harness only handled BASE_LONG/SHORT for entries — a real, undocumented bias in
    every prior result, since analytics.py returns HIGH_RISK_ADD *instead of* BASE*
    on a qualifying continuation candle, not in addition to it): a matching-side
    addon fires at most once per position (Single Addon Guard, keyed on the
    position, never reset by the entry-price shift — matching `logger_db.py`'s
    `get_addon_count` design), blocked entirely in SCOUT_MODE, sized at
    ADDON_ALLOCATION/BASE_ALLOCATION (30/70 by default) of a full base entry, using
    a tighter COMPOSITE stop-loss for the whole position (`ADDON_SL_PCT`) and an
    explicit weighted-average entry-price approximation of OKX's own `avgPx` merge
    (production has no in-code equivalent — it relies on the exchange).
    """
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol=symbol, logger_db=None)
    engine = MMS4SignalEngine()
    im = MMS4InactivityManager()
    im.set_symbol_config(symbol, {
        "stale_candles_threshold": stale_candles_threshold,
        "stale_atr_threshold": STALE_ATR_THRESHOLD,
        "impulse_negation_enabled": IMPULSE_NEGATION_ENABLED,
    })

    # Precompute TMA/slope ONCE over the whole closed-candle history (causal
    # rolling windows only — no lookahead). evaluate_signals() is still called
    # incrementally per step for faithful entry/HIGH_RISK_ADD/adaptive-VR decisions.
    full_df = TMAIndicator.calculate(candles, params)
    tma_slope = full_df["tma"].pct_change(periods=slope_lookback)

    trades: List[Dict[str, Any]] = []
    position: Optional[Dict[str, Any]] = None
    warmup = max(params.tma_length, params.atr_period) + slope_lookback + 2

    def close_position(exit_price: float, reason: str, idx: int) -> None:
        nonlocal position
        assert position is not None
        side = position["side"]
        entry = position["entry_price"]
        weight = position["notional_weight"]
        pnl_pct = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
        flags = _trade_record_flags(reason)
        # A composite (addon) SL is TIGHTER (1.25% < 2%) — since on_stop_loss_hit's
        # own SCOUT_MODE-entry check requires sl_percent >= 0.02, an addon-affected
        # stop-loss correctly does NOT trigger SCOUT_MODE, matching production
        # (daemon.py passes the addon-position's real, tighter sl_pct too).
        sl_percent = position.get("sl_pct_override", BASE_SL_PCT) if reason == "STOP_LOSS" else 0.0
        sm.record_trade(
            trade_id=f"{symbol}-{idx}", symbol=symbol, side=side,
            entry_price=entry, exit_price=exit_price, amount=position["contracts"],
            pnl=pnl_pct * entry, pnl_percent=pnl_pct, sl_percent=sl_percent, **flags,
        )
        trades.append({
            "side": side, "entry_price": entry, "exit_price": exit_price,
            "pnl_pct": pnl_pct, "exit_reason": reason, "exit_idx": idx, "weight": weight,
            "addon_applied": bool(position.get("addon_done", False)),  # test/diagnostic observability only
        })
        im.stop_tracking(symbol)
        position = None

    def record_partial(reason: str) -> None:
        """`sm.record_trade()` call for a partial booking (SPLIT_50/Midline TP) that
        does NOT close `position` — mirrors `record_trade`'s own is_partial=True
        branch, which explicitly preserves SCOUT_MODE rather than exiting it."""
        flags = _trade_record_flags(reason)
        sm.record_trade(
            trade_id=f"{symbol}-partial", symbol=symbol, side=position["side"],
            entry_price=position["entry_price"], exit_price=position["entry_price"],
            amount=0.0, pnl=0.0, pnl_percent=0.0, sl_percent=0.0, **flags,
        )

    for i in range(warmup, len(candles) - 1):
        window = candles[: i + 2]
        curr_c = candles[i]

        if position is not None:
            side = position["side"]
            entry = position["entry_price"]
            sl_pct = position.get("sl_pct_override", BASE_SL_PCT)
            sl_price = entry * (1 - sl_pct) if side == "long" else entry * (1 + sl_pct)
            sl_hit = (curr_c.low <= sl_price) if side == "long" else (curr_c.high >= sl_price)
            if sl_hit:
                close_position(sl_price, "STOP_LOSS", i)
                continue

            # ── Midline Partial TP (PLAN-009 v2), evaluated BEFORE inactivity
            # checks, matching daemon.py's ordering ──
            if midline_tp_enabled and not position.get("midline_tp_done"):
                tma_mid = float(full_df["tma"].iloc[i]) if not pd.isna(full_df["tma"].iloc[i]) else 0.0
                if tma_mid > 0:
                    if side == "long":
                        midline_hit = (curr_c.high >= tma_mid) and (entry <= tma_mid or curr_c.close > entry)
                    else:
                        midline_hit = (curr_c.low <= tma_mid) and (entry >= tma_mid or curr_c.close < entry)
                    if midline_hit:
                        contracts = position["contracts"]
                        reduce_amt = OKXExecutor.reduction_amount(contracts, midline_tp_fraction)
                        position["midline_tp_done"] = True  # at most once per position, hit or skipped
                        if reduce_amt is not None and contracts >= 2:
                            reduced_frac = reduce_amt / contracts
                            mid_pnl = (tma_mid - entry) / entry if side == "long" else (entry - tma_mid) / entry
                            trades.append({
                                "side": side, "entry_price": entry, "exit_price": tma_mid,
                                "pnl_pct": mid_pnl, "exit_reason": "MIDLINE_PARTIAL_TP", "exit_idx": i,
                                "partial": True, "weight": position["notional_weight"] * reduced_frac,
                            })
                            record_partial("MIDLINE_PARTIAL_TP")
                            position["contracts"] = contracts - reduce_amt
                            position["notional_weight"] *= (1.0 - reduced_frac)
                            # Hard SL stays at its original entry-relative price on the
                            # remainder — the real implementation re-attaches the SAME
                            # 2% SL, never break-even. `sl_price` above is already
                            # computed from `entry`, unchanged, so no further action
                            # needed here — this comment documents that it's deliberate.

            atr_now = float(full_df["atr"].iloc[i]) if not pd.isna(full_df["atr"].iloc[i]) else 0.0
            action = im.update_and_check(
                inst_id=symbol, current_price=curr_c.close, current_atr=atr_now, new_candle_closed=True,
            )
            if action in (StaleAction.FORCE_CLOSE, StaleAction.FORCE_CLOSE_BE, StaleAction.BE_ESCAPE):
                close_position(curr_c.close, action.value, i)
                continue
            elif action == StaleAction.SPLIT_50:
                # Mirror the REAL reduction_amount() no-op rule: None (or the full
                # remainder) means the position can't be halved without fully
                # closing it -> HELD, nothing booked, position stays intact.
                contracts = position["contracts"]
                reduce_amt = OKXExecutor.reduction_amount(contracts, 0.50)
                if reduce_amt is not None:
                    reduced_frac = reduce_amt / contracts
                    half_pnl = (curr_c.close - entry) / entry if side == "long" else (entry - curr_c.close) / entry
                    trades.append({
                        "side": side, "entry_price": entry, "exit_price": curr_c.close,
                        "pnl_pct": half_pnl, "exit_reason": "STALE_SPLIT_50", "exit_idx": i,
                        "partial": True, "weight": position["notional_weight"] * reduced_frac,
                    })
                    record_partial("STALE_SPLIT_50")
                    position["contracts"] = contracts - reduce_amt
                    position["notional_weight"] *= (1.0 - reduced_frac)

        signal = engine.evaluate_signals(window, sm, params=params, symbol=symbol)
        slope_now = tma_slope.iloc[i] if i < len(tma_slope) else float("nan")
        trend_blocked = False
        if trend_filter_enabled and not pd.isna(slope_now):
            if signal.signal_type == "BASE_SHORT" and slope_now > slope_threshold:
                trend_blocked = True
            elif signal.signal_type == "BASE_LONG" and slope_now < -slope_threshold:
                trend_blocked = True

        if not signal.is_allowed or trend_blocked:
            continue

        if position is None:
            if signal.signal_type in ("BASE_LONG", "BASE_SHORT"):
                side = "long" if signal.signal_type == "BASE_LONG" else "short"
                position = {"side": side, "entry_price": curr_c.close, "entry_idx": i,
                            **_size_new_entry(symbol, sm)}
                atr_now = float(full_df["atr"].iloc[i]) if not pd.isna(full_df["atr"].iloc[i]) else 0.0
                im.track_position(inst_id=symbol, entry_price=curr_c.close, entry_time=float(curr_c.timestamp),
                                   side=side, atr=atr_now)
        else:
            side = position["side"]
            matching_add = "HIGH_RISK_ADD_LONG" if side == "long" else "HIGH_RISK_ADD_SHORT"
            opposite_signal = "BASE_SHORT" if side == "long" else "BASE_LONG"
            if (signal.signal_type == matching_add
                    and sm.get_symbol_state(symbol) == MMS4State.BASE_MODE  # addons blocked in SCOUT — daemon.py
                    and not position.get("addon_done", False)):             # Single Addon Guard, never reset
                # Weighted-average entry price: an EXPLICIT APPROXIMATION of OKX's
                # own avgPx merge — production never computes this in code (relies
                # on the exchange reporting the merged average for the same-side
                # same-symbol fill); the harness must, since there is no exchange
                # to ask.
                addon_contracts = BASE_CONTRACTS.get(symbol, BASE_CONTRACTS_DEFAULT) * (ADDON_ALLOCATION / BASE_ALLOCATION)
                old_notional = position["contracts"] * position["entry_price"]
                add_notional = addon_contracts * curr_c.close
                new_contracts = position["contracts"] + addon_contracts
                position["entry_price"] = (old_notional + add_notional) / new_contracts
                # notional_weight scales with the combined contracts relative to a
                # full BASE entry, at the state the position was opened in — reuses
                # the same notional_reduction_factor formula against the new total
                # rather than trying to sum two separate weighted contributions.
                position["notional_weight"] = position["notional_weight"] * (new_contracts / position["contracts"])
                position["contracts"] = new_contracts
                position["addon_done"] = True
                # Tighter COMPOSITE stop-loss for the WHOLE position once an addon
                # exists — matches daemon.py's addon_sl_pct override; the addon
                # signal's own hardcoded 1% SL (analytics.py) is deliberately
                # ignored, since production overrides it at execution time too.
                position["sl_pct_override"] = ADDON_SL_PCT
                # Inactivity tracker is NOT re-tracked at the new averaged entry —
                # a documented, lower-distortion approximation (re-tracking would
                # reset accumulated stagnation/impulse progress that production's
                # own tracker may or may not reset on an addon fill; unverified
                # either way, so the choice that erases less state is preferred).
            elif signal.signal_type == opposite_signal:
                close_position(curr_c.close, "TP_AND_FLIP", i)
                new_side = "short" if side == "long" else "long"
                flip_blocked = trend_filter_enabled and not pd.isna(slope_now) and (
                    (new_side == "short" and slope_now > slope_threshold) or
                    (new_side == "long" and slope_now < -slope_threshold)
                )
                if not flip_blocked:
                    position = {"side": new_side, "entry_price": curr_c.close, "entry_idx": i,
                                **_size_new_entry(symbol, sm)}
                    atr_now = float(full_df["atr"].iloc[i]) if not pd.isna(full_df["atr"].iloc[i]) else 0.0
                    im.track_position(inst_id=symbol, entry_price=curr_c.close, entry_time=float(curr_c.timestamp),
                                       side=new_side, atr=atr_now)

    return trades


@dataclass
class DirectionResult:
    """One train->test direction of a calibration run."""
    train_window: str  # "H1" or "H2"
    candidate_found: bool
    chosen_lookback: Optional[int] = None
    chosen_threshold: Optional[float] = None
    train_stats: Optional[Dict[str, Any]] = None
    test_stats: Optional[Dict[str, Any]] = None
    test_baseline: Optional[Dict[str, Any]] = None
    generalizes: bool = False


@dataclass
class CalibrationResult:
    symbol: str
    window_start: str
    window_end: str
    lookbacks: List[int]
    thresholds: List[float]
    h1_to_h2: DirectionResult
    h2_to_h1: DirectionResult
    verdict: str  # "VALIDATED" | "OVERFIT_REJECTED" | "NO_CANDIDATE"
    recommended_params: Optional[Dict[str, Any]] = None
    note: str = field(default="")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def _grid_search_train(
    symbol: str, train_candles: List[Candle], params: IndicatorParams,
    lookbacks: List[int], thresholds: List[float], baseline_train: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Search (lookback, threshold) on the training half; return the cell with the
    highest total_pnl among those that beat baseline on BOTH R:R and total_pnl with
    adequate sample, or None if no cell qualifies."""
    candidates = []
    for lb in lookbacks:
        for th in thresholds:
            trades = run_backtest(symbol, train_candles, params, stale_candles_threshold=12,
                                   trend_filter_enabled=True, slope_lookback=lb, slope_threshold=th)
            s = _stats(trades)
            if (s["full_closes"] >= MIN_FULL_CLOSES_HALF
                    and s["rr"] > baseline_train["rr"] and s["total_pnl"] > baseline_train["total_pnl"]):
                candidates.append((lb, th, s))
    if not candidates:
        return None
    lb, th, s = max(candidates, key=lambda c: c[2]["total_pnl"])
    return {"lookback": lb, "threshold": th, "stats": s}


def train_test_validate(
    symbol: str,
    candles: List[Candle],
    lookbacks: List[int],
    thresholds: List[float],
    params: Optional[IndicatorParams] = None,
) -> CalibrationResult:
    """PLAN-010 §4.1: the ONLY calibration entry point — structurally enforces
    train/test validation in both directions (see module docstring). There is no
    function in this module that returns a verdict from full-window stats alone."""
    if params is None:
        params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)

    mid = len(candles) // 2
    h1, h2 = candles[:mid], candles[mid:]
    window_start = str(candles[0].timestamp) if candles else ""
    window_end = str(candles[-1].timestamp) if candles else ""

    baseline_h1 = _stats(run_backtest(symbol, h1, params, stale_candles_threshold=12, trend_filter_enabled=False))
    baseline_h2 = _stats(run_backtest(symbol, h2, params, stale_candles_threshold=12, trend_filter_enabled=False))

    def _direction(train_label: str, train_c: List[Candle], test_c: List[Candle],
                   baseline_train: Dict[str, Any], baseline_test: Dict[str, Any]) -> DirectionResult:
        best = _grid_search_train(symbol, train_c, params, lookbacks, thresholds, baseline_train)
        if best is None:
            return DirectionResult(train_window=train_label, candidate_found=False)
        test_trades = run_backtest(symbol, test_c, params, stale_candles_threshold=12,
                                    trend_filter_enabled=True, slope_lookback=best["lookback"],
                                    slope_threshold=best["threshold"])
        test_stats = _stats(test_trades)
        generalizes = test_stats["rr"] > baseline_test["rr"] and test_stats["total_pnl"] > baseline_test["total_pnl"]
        return DirectionResult(
            train_window=train_label, candidate_found=True,
            chosen_lookback=best["lookback"], chosen_threshold=best["threshold"],
            train_stats=best["stats"], test_stats=test_stats, test_baseline=baseline_test,
            generalizes=generalizes,
        )

    h1_to_h2 = _direction("H1", h1, h2, baseline_h1, baseline_h2)
    h2_to_h1 = _direction("H2", h2, h1, baseline_h2, baseline_h1)

    if not h1_to_h2.candidate_found or not h2_to_h1.candidate_found:
        verdict = "NO_CANDIDATE"
        note = "No configuration beat baseline on both R:R and total_pnl with adequate sample in at least one training half."
    elif h1_to_h2.generalizes and h2_to_h1.generalizes:
        verdict = "VALIDATED"
        note = "Both training directions found a configuration that generalized to its unseen test half."
        if (h1_to_h2.chosen_lookback, h1_to_h2.chosen_threshold) != (h2_to_h1.chosen_lookback, h2_to_h1.chosen_threshold):
            note += (" NOTE: the two directions chose DIFFERENT parameters "
                     f"(H1->H2: lookback={h1_to_h2.chosen_lookback}, threshold={h1_to_h2.chosen_threshold}; "
                     f"H2->H1: lookback={h2_to_h1.chosen_lookback}, threshold={h2_to_h1.chosen_threshold}) "
                     "-- both individually generalized, but this disagreement is itself a reason for caution; "
                     "review both directions' full stats before applying either.")
    else:
        verdict = "OVERFIT_REJECTED"
        note = "At least one direction's best training-half configuration failed to generalize to its held-out test half."

    recommended_params = None
    if verdict == "VALIDATED":
        recommended_params = {"lookback": h1_to_h2.chosen_lookback, "threshold": h1_to_h2.chosen_threshold}

    return CalibrationResult(
        symbol=symbol, window_start=window_start, window_end=window_end,
        lookbacks=lookbacks, thresholds=thresholds,
        h1_to_h2=h1_to_h2, h2_to_h1=h2_to_h1,
        verdict=verdict, recommended_params=recommended_params, note=note,
    )


DEFAULT_LOOKBACKS = [3, 5, 8, 12, 16, 20, 24, 30]
DEFAULT_THRESHOLDS = [0.001, 0.002, 0.003, 0.005, 0.007, 0.010, 0.015, 0.020]
CALIBRATION_WINDOW_DAYS = 90


def run_calibration_job(job_id: str, symbol: str, db_path: str) -> None:
    """PLAN-010 §4.2: the ProcessPoolExecutor worker entry point. MUST be a
    module-level function (picklable) and MUST open its own DB connection — SQLite
    connections and asyncio event loops from the parent process never cross a
    `ProcessPoolExecutor` boundary safely.

    Runs entirely off the FastAPI/daemon event loop, in a separate OS process — see
    PLAN-010 §4.2 for why this MUST NOT run inline: a full train/test grid takes
    tens of seconds to several minutes, and `api.py`'s daemon shares one event loop
    with the API server, so a blocking inline run would stall live trading.

    Never writes to `system_settings` / `tma_trend_filter_enabled` — this function
    only ever calls `update_calibration_run`. Applying a result to live trading
    parameters is a separate, explicitly-triggered call to the existing
    `POST /api/v1/symbols/update` path (PLAN-010 §5.2), never automatic here.
    """
    db = LoggerDB(db_path=db_path)
    try:
        db.update_calibration_run(job_id, status="running")

        candles = asyncio.run(_fetch_calibration_candles(symbol, db))
        if len(candles) < MIN_FULL_CLOSES_HALF * 4:
            # Too little history to even attempt a meaningful train/test split.
            db.update_calibration_run(
                job_id, status="failed",
                error=f"Only {len(candles)} candles available for {symbol}; need substantially more for a meaningful train/test split.",
            )
            return

        result = train_test_validate(symbol, candles, DEFAULT_LOOKBACKS, DEFAULT_THRESHOLDS)
        db.update_calibration_run(
            job_id, status="done",
            window_start=result.window_start, window_end=result.window_end,
            verdict=result.verdict, result_json=json.dumps(result.to_dict()),
        )
    except Exception as e:  # noqa: BLE001 - must never leave a job stuck at "running"
        db.update_calibration_run(job_id, status="failed", error=f"{type(e).__name__}: {e}")


async def _fetch_calibration_candles(symbol: str, db: LoggerDB) -> List[Candle]:
    ex = OKXExecutor()
    try:
        await ex.initialize()
    except Exception:
        pass  # fetch_candles_paginated works off public market data even without credentials
    return await ex.fetch_candles_paginated(symbol, timeframe="1h", days=CALIBRATION_WINDOW_DAYS, logger_db=db)

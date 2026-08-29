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
"""
import asyncio
import json
import os
import tempfile
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

# Empirical median live base-entry contract size per symbol (from production
# closed_trades.amount, 2026-08-29 review) — used only for the SPLIT_50
# reduction_amount() no-op check, not for real position sizing. Symbols without a
# specific entry fall back to the conservative default (more likely to trigger the
# no-op, matching the smallest observed live sizes).
BASE_CONTRACTS: Dict[str, float] = {"BTC/USDC": 2.0, "ETH/USDC": 8.0}
BASE_CONTRACTS_DEFAULT = 2.0


def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """pnl/R:R weighted by each trade's `weight` (fraction of the base position size
    still open at that close) — a SPLIT_50-reduced remainder contributes less than a
    full-size close once `reduction_amount()` has shrunk it."""
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
) -> List[Dict[str, Any]]:
    """Single-symbol, candle-granularity simulation using the real production
    engine classes. Returns closed trades (dicts: side, entry_price, exit_price,
    pnl_pct, exit_reason, weight, optionally partial=True)."""
    with tempfile.TemporaryDirectory(prefix="mms4tk_backtest_") as tmp_dir:
        db = LoggerDB(db_path=os.path.join(tmp_dir, f"bt_{symbol.replace('/', '_')}.db"))
        sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol=symbol, logger_db=db)
        engine = MMS4SignalEngine()
        im = MMS4InactivityManager()
        im.set_symbol_config(symbol, {
            "stale_candles_threshold": stale_candles_threshold,
            "stale_atr_threshold": STALE_ATR_THRESHOLD,
            "impulse_negation_enabled": IMPULSE_NEGATION_ENABLED,
        })

        base_contracts = BASE_CONTRACTS.get(symbol, BASE_CONTRACTS_DEFAULT)

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
            weight = position["contracts"] / base_contracts
            pnl_pct = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
            trades.append({
                "side": side, "entry_price": entry, "exit_price": exit_price,
                "pnl_pct": pnl_pct, "exit_reason": reason, "exit_idx": idx, "weight": weight,
            })
            im.stop_tracking(symbol)
            position = None

        for i in range(warmup, len(candles) - 1):
            window = candles[: i + 2]
            curr_c = candles[i]

            if position is not None:
                side = position["side"]
                entry = position["entry_price"]
                sl_price = entry * (1 - BASE_SL_PCT) if side == "long" else entry * (1 + BASE_SL_PCT)
                sl_hit = (curr_c.low <= sl_price) if side == "long" else (curr_c.high >= sl_price)
                if sl_hit:
                    close_position(sl_price, "STOP_LOSS", i)
                    continue

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
                            "partial": True, "weight": reduced_frac,
                        })
                        position["contracts"] = contracts - reduce_amt

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
                                "contracts": base_contracts}
                    atr_now = float(full_df["atr"].iloc[i]) if not pd.isna(full_df["atr"].iloc[i]) else 0.0
                    im.track_position(inst_id=symbol, entry_price=curr_c.close, entry_time=float(curr_c.timestamp),
                                       side=side, atr=atr_now)
            else:
                side = position["side"]
                opposite_signal = "BASE_SHORT" if side == "long" else "BASE_LONG"
                if signal.signal_type == opposite_signal:
                    close_position(curr_c.close, "TP_AND_FLIP", i)
                    new_side = "short" if side == "long" else "long"
                    flip_blocked = trend_filter_enabled and not pd.isna(slope_now) and (
                        (new_side == "short" and slope_now > slope_threshold) or
                        (new_side == "long" and slope_now < -slope_threshold)
                    )
                    if not flip_blocked:
                        position = {"side": new_side, "entry_price": curr_c.close, "entry_idx": i,
                                    "contracts": base_contracts}
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

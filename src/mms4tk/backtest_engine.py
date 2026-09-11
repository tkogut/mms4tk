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
import datetime as dt
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

# Audit P2 (2026-09-08): round-trip fee + funding cost model. Previously this engine
# had ZERO fee/funding/slippage modeling despite production tracking full net PnL
# (PLAN-011, fee_usd/pnl_net_usd) — every calibration/backtest verdict to date
# (Midline TP off, trend filter off, high-vol filter off) was measured on GROSS pnl,
# a systematically biased instrument: gross overstates the edge of anything that
# trades MORE (filters that let more entries through look relatively better than
# they are) and understates anything that trades LESS (a filter that reduces churn
# looks relatively worse than it is).
#
# TAKER_FEE_PCT: OKX taker fee per leg, the exchange's published schedule figure.
# Kept as documentation of where the ORIGINAL assumption came from — it is no
# longer what ROUND_TRIP_FEE_PCT is derived from (see the calibration below).
TAKER_FEE_PCT = 0.0005
#
# ── A1 CALIBRATION FROM PRODUCTION (2026-09-10), audit P2(c) point 3 ──────────
# The audit's own instruction was explicit: "Kalibracja round_trip_cost_pct na
# realnych danych: logger_db.get_closed_trades() ma fee_usd per transakcja — nie
# zgadywać, wyprowadzić z produkcji." Until now this constant was ASSUMED — it was
# `TAKER_FEE_PCT * 2`, itself reused from inactivity_manager's
# DEFAULT_ROUND_TRIP_COST_PCT. That assumption is now REPLACED by a measurement.
#
#   formula:  effective_round_trip_pct = SUM(fee_usd) / SUM(notional)
#             notional = entry_price * amount * contract_size
#             (contract_size per daemon._get_contract_size: BTC 1e-4, ETH 1e-3)
#   source:   production `closed_trades`, exported from the live logs.db
#   window:   2026-08-30 .. 2026-09-10 (from v0.41.0, the first release writing a
#             real fee_usd; earlier rows default fee_usd to 0.0 and are unusable)
#   n:        52 closed trades (BTC 29, ETH 23)
#   measured: BTC   0.00099968   (sum_fee $1.942895 / notional $1943.5098)
#             ETH   0.00100103   (sum_fee $1.585347 / notional $1583.7099)
#             ALL   0.00100029   (sum_fee $3.528242 / notional $3527.2197)
#
# fee_usd on a `closed_trades` row is the commission for the WHOLE round trip
# (open+close already settled together by the time the row is written), so the
# measurement is directly comparable to this constant — no x2 and no /2.
#
# Blended BTC+ETH is used: the per-symbol spread is 0.00000135 absolute, i.e.
# 0.13% of the blended rate — far below any level at which a per-symbol constant
# could change a backtest verdict, and well inside the noise of a 52-trade sample.
#
# RESULT: the measurement CONFIRMS the previous assumption to within 0.03%
# relative (0.00100029 vs 0.0010). The value below is nonetheless written as the
# measured figure rather than left as `TAKER_FEE_PCT * 2`, so the constant is
# traceable to production data instead of to another constant's assumption — the
# next person changing OKX fee tiers must re-measure, not re-derive.
ROUND_TRIP_FEE_PCT = 0.0010003  # measured (see above); duration-independent, unlike funding
# OKX perpetual funding: charged every 8 hours. On H1 candles (this engine's only
# timeframe, see run_calibration_job/_fetch_calibration_candles) that is every 8
# candles. 0.01% is a conservative flat estimate (real funding rates float and can
# be negative — a fixed positive cost is the conservative assumption for a filter
# being asked to prove it survives costs, not a precise funding-rate model).
FUNDING_RATE_PCT_PER_INTERVAL = 0.0001
FUNDING_INTERVAL_CANDLES = 8


def _round_trip_cost_pct(candles_held: int) -> Dict[str, float]:
    """fee_pct is flat (duration-independent); funding_pct scales with how many
    8-candle funding intervals the position was held across (ceil — a position held
    for 1 candle into a new interval still pays that interval in full, matching how
    OKX actually charges funding at the interval boundary regardless of how briefly
    a position was open during it)."""
    held = max(int(candles_held), 0)
    funding_intervals = -(-held // FUNDING_INTERVAL_CANDLES)  # ceil division, stdlib-free
    return {
        "fee_pct": ROUND_TRIP_FEE_PCT,
        "funding_pct": funding_intervals * FUNDING_RATE_PCT_PER_INTERVAL,
    }
MIN_FULL_CLOSES_HALF = 30  # below this, a half-window config's stats are too noisy to trust
# Audit P7 "same file" follow-on (2026-09-08): raised 12 -> 30. 12 was the only
# sample-size floor gating a TRAIN-half candidate; the TEST-half "generalizes" check
# had none at all (see the full_closes gate added below in _direction) — a test half
# with a handful of lucky trades could pass purely on variance and help produce a
# VALIDATED verdict. Applies to both halves via this shared constant.

# HIGH_RISK_ADD (addon) constants, mirroring daemon.py's own defaults/env vars exactly
# so an operator's .env change to the live values is picked up here too.
BASE_ALLOCATION = 0.70
ADDON_ALLOCATION = 0.30
ADDON_SL_PCT = 0.0125  # V0 (PRE-P3 production, superseded 2026-09-10): tighter COMPOSITE stop-loss for the whole position once an addon exists
# Audit P1 (2026-09-08): V1 candidate — composite SL widened to 2.0% of the averaged
# (avgPx) entry, reading the spec's "max 3% na całość" as ~2% on the blended
# position. See run_backtest's `addon_sl_variant` param for V0/V1/V2 semantics.
# NOTE (2026-09-11): neither constant is on the default path any more — P3 V2 shipped
# to production (v0.44.4+) and `addon_sl_variant` now defaults to "V2", where the
# addon gets NO separate SL at all. V0/V1 are kept only so the P1 analysis harness
# can still reproduce the historical comparison.
ADDON_SL_PCT_V1 = 0.02

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
    1.0 there).

    Audit P2 (2026-09-08): win/loss classification, R:R and total_pnl are now all
    computed on NET pnl (`pnl_pct - fee_pct - funding_pct`), not gross — see the
    module-level fee/funding constants above. `fee_pct`/`funding_pct` default to 0.0
    via `.setdefault` when absent, so this is a NO-OP (net == gross) for any trade
    dict that doesn't set them — every existing synthetic/hand-built test trade
    (the `_trades()` helper in tests/test_backtest_engine.py, and anything built by
    hand elsewhere) is completely unaffected; only real trades produced by
    `run_backtest()`'s own three trade-construction sites (which now DO set both
    fields) actually change behavior here."""
    if not trades:
        return dict(win_rate=0.0, rr=float("nan"), total_pnl=0.0, full_closes=0)
    for t in trades:
        t.setdefault("weight", 1.0)
        t.setdefault("fee_pct", 0.0)
        t.setdefault("funding_pct", 0.0)
    net = lambda t: t["pnl_pct"] - t["fee_pct"] - t["funding_pct"]
    wins = [t for t in trades if net(t) > 0]
    losses = [t for t in trades if net(t) <= 0]
    win_rate = len(wins) / len(trades) * 100
    w_win = sum(t["weight"] for t in wins) or 1.0
    w_loss = sum(t["weight"] for t in losses) or 1.0
    avg_win = sum(net(t) * t["weight"] for t in wins) / w_win if wins else 0.0
    avg_loss = abs(sum(net(t) * t["weight"] for t in losses) / w_loss) if losses else 0.0
    rr = (avg_win / avg_loss) if avg_loss > 0 else float("nan")
    total_pnl = sum(net(t) * t["weight"] for t in trades)
    full_closes = len([t for t in trades if not t.get("partial")])
    return dict(win_rate=win_rate, rr=rr, total_pnl=total_pnl, full_closes=full_closes)


def _compute_d1_exhaustion_flags(
    candles: List[Candle],
    params: IndicatorParams,
) -> List[bool]:
    """Phase 3-4 (task.md, D1 ATR exhaustion filter): a causal, per-H1-candle
    "the current UTC day is already overextended" flag, one bool per index of
    `candles`, no lookahead.

    Mirrors `analytics.HigherTimeframeAnalytics.calculate_higher_tf`'s own D1
    range-vs-ATR_D1 classification (same ATR calc via `TMAIndicator.calculate`,
    same reference threshold `params.htf_expansion_threshold` = 2.5 — NOT a new
    number invented for this filter) but adapted for intraday, causal use:
    production's version only ever looks at the last FULLY CLOSED D1 candle
    (fine for a read-only Hermes-agent data point), whereas this filter has to
    gate HIGH_RISK_ADD signals that fire mid-day, so it must decide *before* the
    day is over.

    Method, for each H1 candle at index i, belonging to calendar day D (UTC):
      1. `atr_ref` = ATR_D1 computed from all FULLY CLOSED days strictly before
         D (i.e. the ATR value as of yesterday's close) — this part uses only
         past days, exactly like the production HTF analytics.
      2. `running_range` = max(high) - min(low) over day D's H1 candles from
         the start of the day up to AND INCLUDING candle i only (never later
         candles in the same day) — the same day-to-date range a live daemon
         could observe in real time.
      3. flagged[i] = atr_ref > 0 and running_range >= threshold * atr_ref.

    The first calendar day in `candles` has no prior-day ATR reference and is
    never flagged (matches this module's existing warmup convention elsewhere).
    """
    if not candles:
        return []
    ts = [float(c.timestamp) for c in candles]
    days = [dt.datetime.fromtimestamp(t / 1000.0, tz=dt.timezone.utc).date() for t in ts]
    df = pd.DataFrame({
        "day": days, "ts": ts,
        "open": [c.open for c in candles], "high": [c.high for c in candles],
        "low": [c.low for c in candles], "close": [c.close for c in candles],
    })

    daily = df.groupby("day", sort=True).agg(
        open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), ts=("ts", "first"),
    ).reset_index()
    day_order = list(daily["day"])
    if len(day_order) < 2:
        return [False] * len(candles)  # no prior day exists anywhere -> no reference ever

    d1_candles = [
        Candle(timestamp=r.ts, open=r.open, high=r.high, low=r.low, close=r.close)
        for r in daily.itertuples(index=False)
    ]
    d1_df = TMAIndicator.calculate(d1_candles, params)
    atr_by_day = dict(zip(day_order, d1_df["atr"]))
    day_index = {d: idx for idx, d in enumerate(day_order)}
    threshold = float(params.htf_expansion_threshold)

    df["running_high"] = df.groupby("day")["high"].cummax()
    df["running_low"] = df.groupby("day")["low"].cummin()
    running_range = df["running_high"] - df["running_low"]

    flags: List[bool] = []
    for pos, day in enumerate(days):
        j = day_index[day]
        if j == 0:
            flags.append(False)
            continue
        atr_ref = atr_by_day.get(day_order[j - 1])
        if atr_ref is None or pd.isna(atr_ref) or atr_ref <= 0:
            flags.append(False)
            continue
        flags.append(bool(running_range.iloc[pos] >= threshold * atr_ref))
    return flags


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
    sl_cooldown_candles: int = 3,
    addon_sl_variant: str = "V2",
    d1_exhaustion_filter_enabled: bool = False,
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

    Audit P7 "same file" follow-on (2026-09-08): also models daemon.py's Post-SL
    Cooldown (PLAN-008 Phase 2, `_sl_cooldown_until`) — after a genuine STOP_LOSS
    close, new BASE_LONG/BASE_SHORT entries are blocked for `sl_cooldown_candles`
    candles (default 3, matching `MMS4_SL_COOLDOWN_CANDLES`'s default), mirroring
    exactly the one branch daemon.py gates on it (a fresh position open when none
    exists) — TP_AND_FLIP re-entries and HIGH_RISK_ADD are untouched, matching
    production. Without this, the calibration engine could validate a filter whose
    apparent edge is partly just re-entering immediately after every stop-out, a
    behavior production does not actually allow.

    Also models HIGH_RISK_ADD (addon) signals, previously silently dropped (the
    harness only handled BASE_LONG/SHORT for entries — a real, undocumented bias in
    every prior result, since analytics.py returns HIGH_RISK_ADD *instead of* BASE*
    on a qualifying continuation candle, not in addition to it): a matching-side
    addon fires at most once per position (Single Addon Guard, keyed on the
    position, never reset by the entry-price shift — matching `logger_db.py`'s
    `get_addon_count` design), blocked entirely in SCOUT_MODE, sized at
    ADDON_ALLOCATION/BASE_ALLOCATION (30/70 by default) of a full base entry, with
    an explicit weighted-average entry-price approximation of OKX's own `avgPx` merge
    (production has no in-code equivalent — it relies on the exchange). How the SL
    behaves across that fill is selected by `addon_sl_variant`, below.

    Audit P1 (2026-09-08): `addon_sl_variant` selects how the SL is recomputed once
    an addon fills, mirroring the analysis harness the audit asked for (same 6
    windows as Midline TP: BTC/ETH x H1/H2/full). Production behavior IS a variant
    here, not a separate code path — deliberately, so the default stays provably
    identical to what production does rather than drifting from it.
      - "V2" (DEFAULT since 2026-09-11 — this is what production does today, P3 V2,
        shipped v0.44.4+): the SL is pegged to the ORIGINAL base entry price
        (`base_entry_price`, pinned at entry and never touched by the addon average)
        at `BASE_SL_PCT` (2%); the addon leg gets NO separate SL of its own.
        Mirrors production 1:1 — see `daemon.reconcile_positions` (classifies the
        stop-out off `base_entry_price` * `base_sl_pct`), the Self-Healing SL Guard
        (re-attaches at `_base_entry_price(...)`, never live avgPx), the Midline-TP
        SL re-attach (same anchor) and the HIGH_RISK_ADD branch (calls
        `execute_order(..., stop_loss_pct=0.0, ...)`, leaving the existing
        `closeFraction="1"` base algo — which already covers the ENLARGED position —
        untouched). The trigger price itself is `okx_executor._compute_sl_trigger_price`'s
        formula: `ref * (1 - pct)` long, `ref * (1 + pct)` short, with
        `ref = base_entry_price` and `pct = base_sl_pct`.
      - "V0" (PRE-P3 production, historical): composite `ADDON_SL_PCT` (1.25%) from
        the averaged (avgPx) entry. This is what production did until 2026-09-10 and
        what EVERY backtest run before 2026-09-11 measured. Kept only so the P1
        analysis (`test_p1_addon_sl_gate_analysis`) remains reproducible.
      - "V1" (never shipped): composite `ADDON_SL_PCT_V1` (2.0%) from the averaged
        entry — same mechanism as V0, wider threshold.

    Default changed V0 -> V2 on 2026-09-11: production was re-architected by P3 V2
    (a determinism fix — one position could otherwise get two different SL prices
    depending on which code path attached last — NOT a PnL optimization), and the
    harness had silently kept modeling the retired V0 mechanism.

    Phase 3-4 (task.md, D1 ATR exhaustion filter, default OFF — measurement only,
    NOT wired into daemon.py unless/until a backtest proves it clearly positive):
    when `d1_exhaustion_filter_enabled` is True, a HIGH_RISK_ADD signal is dropped
    entirely (no addon fills, base position is completely unaffected) on any H1
    candle whose calendar day's day-to-date high-low range has already exceeded
    `params.htf_expansion_threshold` (2.5, reused as-is from `analytics.py` —
    NOT a new number) times the prior day's D1 ATR. See
    `_compute_d1_exhaustion_flags` for the exact causal method. Only the addon
    leg is gated — a fresh BASE_LONG/BASE_SHORT entry on an overextended day is
    untouched, matching the audit's "blocks the 30%-budget High Risk Add pool
    only" framing.
    """
    if addon_sl_variant not in ("V0", "V1", "V2"):
        raise ValueError(f"addon_sl_variant must be one of V0/V1/V2, got {addon_sl_variant!r}")
    d1_exhausted = _compute_d1_exhaustion_flags(candles, params) if d1_exhaustion_filter_enabled else None
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

    # Post-SL Cooldown (PLAN-008 Phase 2 fidelity, see docstring): timestamp until
    # which new BASE_LONG/BASE_SHORT entries are blocked, set below on a genuine
    # STOP_LOSS close. 0 means no cooldown currently active.
    sl_cooldown_until_ts: float = 0.0
    candle_interval = 0.0
    if len(candles) >= 2:
        try:
            candle_interval = abs(float(candles[1].timestamp) - float(candles[0].timestamp))
        except (TypeError, ValueError):
            candle_interval = 0.0

    def close_position(exit_price: float, reason: str, idx: int) -> None:
        nonlocal position
        assert position is not None
        side = position["side"]
        entry = position["entry_price"]
        weight = position["notional_weight"]
        pnl_pct = (exit_price - entry) / entry if side == "long" else (entry - exit_price) / entry
        flags = _trade_record_flags(reason)
        # Under the default V2 this is always BASE_SL_PCT (2%) — addon or not — so
        # on_stop_loss_hit's own `sl_percent >= 0.02` SCOUT_MODE/Hermes-H4 gate fires
        # for addon-carrying positions too, matching production exactly
        # (daemon.reconcile_positions derives sl_percent from base_entry_price, not
        # avgPx, precisely so that gate stops silently never firing).
        # Under the legacy V0/V1 variants the composite override is passed instead
        # (1.25% < 2% for V0 => no SCOUT_MODE transition), reproducing pre-P3
        # production for the historical P1 comparison.
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
            **_round_trip_cost_pct(idx - position["entry_idx"]),
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
            # V2 (the default, = live production since P3 V2): SL stays pegged to the
            # ORIGINAL base entry price, never the addon-averaged one — same anchor
            # and same formula as `okx_executor._compute_sl_trigger_price` called with
            # `daemon._base_entry_price(...)` and `daemon.base_sl_pct`.
            # Legacy V0/V1 (and any position that never took an addon, where the two
            # prices are equal anyway) use the averaged `entry_price`.
            sl_basis_price = position["base_entry_price"] if position.get("sl_price_basis") == "base_entry" else entry
            sl_price = sl_basis_price * (1 - sl_pct) if side == "long" else sl_basis_price * (1 + sl_pct)
            sl_hit = (curr_c.low <= sl_price) if side == "long" else (curr_c.high >= sl_price)
            if sl_hit:
                close_position(sl_price, "STOP_LOSS", i)
                if sl_cooldown_candles > 0 and candle_interval > 0:
                    sl_cooldown_until_ts = float(curr_c.timestamp) + (sl_cooldown_candles * candle_interval)
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
                                **_round_trip_cost_pct(i - position["entry_idx"]),
                            })
                            record_partial("MIDLINE_PARTIAL_TP")
                            position["contracts"] = contracts - reduce_amt
                            position["notional_weight"] *= (1.0 - reduced_frac)
                            # Hard SL stays at its original price on the remainder —
                            # the real implementation re-attaches the SAME 2% SL at the
                            # SAME anchor (`daemon.py`'s Midline-TP re-attach uses
                            # `_base_entry_price(...)` under P3 V2), never break-even.
                            # `sl_price` above is already computed from that same basis
                            # (`base_entry_price` under the default V2, `entry` under
                            # legacy V0/V1), unchanged, so no further action is needed
                            # here — this comment documents that it's deliberate.

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
                        **_round_trip_cost_pct(i - position["entry_idx"]),
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
            cooldown_active = sl_cooldown_until_ts > 0 and float(curr_c.timestamp) < sl_cooldown_until_ts
            if signal.signal_type in ("BASE_LONG", "BASE_SHORT") and not cooldown_active:
                side = "long" if signal.signal_type == "BASE_LONG" else "short"
                position = {"side": side, "entry_price": curr_c.close, "base_entry_price": curr_c.close, "entry_idx": i,
                            **_size_new_entry(symbol, sm)}
                atr_now = float(full_df["atr"].iloc[i]) if not pd.isna(full_df["atr"].iloc[i]) else 0.0
                im.track_position(inst_id=symbol, entry_price=curr_c.close, entry_time=float(curr_c.timestamp),
                                   side=side, atr=atr_now)
        else:
            side = position["side"]
            matching_add = "HIGH_RISK_ADD_LONG" if side == "long" else "HIGH_RISK_ADD_SHORT"
            opposite_signal = "BASE_SHORT" if side == "long" else "BASE_LONG"
            d1_exhaustion_blocked = bool(d1_exhausted is not None and d1_exhausted[i])
            if (signal.signal_type == matching_add
                    and sm.get_symbol_state(symbol) == MMS4State.BASE_MODE  # addons blocked in SCOUT — daemon.py
                    and not position.get("addon_done", False)                # Single Addon Guard, never reset
                    and not d1_exhaustion_blocked):                          # Phase 3-4: D1 overextension filter
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
                # Audit P1 (2026-09-08): addon_sl_variant selects how the SL is
                # recomputed for the WHOLE position once an addon exists — see
                # run_backtest's docstring for V0/V1/V2 semantics. The addon
                # signal's own hardcoded 1% SL (analytics.py) is deliberately
                # ignored in all three variants, exactly as production does: the
                # pre-P3 paths overrode it at execution time, and P3 V2 attaches no
                # addon SL at all (`execute_order(..., stop_loss_pct=0.0, ...)`).
                if addon_sl_variant == "V2":  # default — live production (P3 V2)
                    position["sl_pct_override"] = BASE_SL_PCT
                    position["sl_price_basis"] = "base_entry"
                elif addon_sl_variant == "V1":  # never shipped
                    position["sl_pct_override"] = ADDON_SL_PCT_V1
                else:  # V0 — pre-P3 production, historical comparison only
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
                    position = {"side": new_side, "entry_price": curr_c.close, "base_entry_price": curr_c.close, "entry_idx": i,
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
        # Audit P7 (2026-09-08): previously hardcoded adaptive_vr_high=1.0,
        # adaptive_vr_low=0.75 here — those exact thresholds were reset to 1.6/0.8 in
        # production during this same audit session, so any caller that didn't
        # explicitly pass params (chiefly run_calibration_job, fixed below) silently
        # calibrated against a VR fast-path that no longer exists live. Falling back
        # to IndicatorParams()'s own field defaults instead: those already resolve
        # from MMS4_ADAPTIVE_VR_HIGH/_LOW (and MMS4_TIME_LOCK_*) env vars, the same
        # fallback every other component in this codebase uses when system_settings
        # has no override (see daemon._get_indicator_params, daemon.py:636).
        # engine_mode is still forced to ADAPTIVE here — that is this function's
        # deliberate contract (it exists to evaluate the adaptive VR fast-path
        # itself), not part of the stale-settings bug.
        params = IndicatorParams(engine_mode="ADAPTIVE")

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
        # Audit P7 "same file" follow-on (2026-09-08): gate on TEST-half transaction
        # count too, not just R:R/total_pnl — previously the only sample-size floor
        # anywhere in this function applied to the TRAIN half (_grid_search_train's
        # own MIN_FULL_CLOSES_HALF check); a test half could "generalize" on a
        # handful of trades whose R:R/pnl beat baseline by pure variance.
        generalizes = (
            test_stats["full_closes"] >= MIN_FULL_CLOSES_HALF
            and test_stats["rr"] > baseline_test["rr"]
            and test_stats["total_pnl"] > baseline_test["total_pnl"]
        )
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


# Audit P7 "same file" follow-on (2026-09-08): reduced from the original 8x8 (64
# cells) to the approved 4x4 (16 cells) grid from PLAN-010 TICKET-3 / task.md. 64
# cells is a 4x multiple-testing exposure over the reviewed/approved grid with no
# corresponding benefit — more cells only inflate the chance some (lookback,
# threshold) pair beats baseline by chance in the train half.
DEFAULT_LOOKBACKS = [3, 5, 8, 13]
DEFAULT_THRESHOLDS = [0.0010, 0.0015, 0.0020, 0.0030]
CALIBRATION_WINDOW_DAYS = 90


def _live_indicator_params(db: LoggerDB) -> IndicatorParams:
    """Build the IndicatorParams a calibration job should validate against, from live
    SQLite system_settings — mirroring daemon._get_indicator_params (daemon.py:636)
    for the fields that matter to the adaptive VR fast-path.

    Audit P7 (2026-09-08): run_calibration_job previously called train_test_validate()
    without `params` at all, so it always fell through to that function's internal
    default — literals that had already drifted from the live production values by
    the time of this audit. system_settings is this codebase's SSOT for these values
    (every other read path — daemon, the API — treats it that way), so a calibration
    job that doesn't consult it is validating a strategy that no longer runs live.

    engine_mode is forced to ADAPTIVE regardless of the live setting: calibration
    exists specifically to evaluate the adaptive VR fast-path, not to replay whatever
    mode happens to be live.
    """
    try:
        db_settings = db.get_all_settings()
    except Exception:
        db_settings = {}
    defaults = IndicatorParams()
    return IndicatorParams(
        engine_mode="ADAPTIVE",
        adaptive_vr_high=float(db_settings.get("adaptive_vr_high", defaults.adaptive_vr_high)),
        adaptive_vr_low=float(db_settings.get("adaptive_vr_low", defaults.adaptive_vr_low)),
        time_lock_start=str(db_settings.get("time_lock_start", defaults.time_lock_start)),
        time_lock_end=str(db_settings.get("time_lock_end", defaults.time_lock_end)),
    )


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

        params = _live_indicator_params(db)
        result = train_test_validate(symbol, candles, DEFAULT_LOOKBACKS, DEFAULT_THRESHOLDS, params=params)
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

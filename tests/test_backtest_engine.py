"""PLAN-010 Phase 2: the calibration engine's decision logic (train_test_validate)
and the real-data regression guard against reintroducing the 2026-08-29 overfitting
bug (PLAN-008 Phase 3's deployed trend-filter parameters looked like a clear win on
full-window stats, but failed when validated train-half -> test-half in both
directions).
"""
import csv
import os
from typing import Any, Dict

import pytest

from mms4tk import backtest_engine as be
from mms4tk.analytics import Candle, IndicatorParams

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def load_fixture_candles(name: str):
    candles = []
    with open(os.path.join(FIXTURES, name)) as f:
        for row in csv.DictReader(f):
            candles.append(Candle(
                timestamp=int(row["timestamp"]), open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]),
            ))
    return candles


def _trades(pnls, weight=1.0, partial=False):
    out = []
    for p in pnls:
        t = {"side": "short", "entry_price": 100.0, "exit_price": 100.0 * (1 - p),
             "pnl_pct": p, "exit_reason": "TEST", "weight": weight}
        if partial:
            t["partial"] = True
        out.append(t)
    return out


# ── _stats: pure function, real logic under test ──

def test_stats_empty():
    s = be._stats([])
    assert s["full_closes"] == 0
    assert s["total_pnl"] == 0.0


def test_stats_weights_partial_closes_by_remaining_fraction():
    trades = _trades([0.02], weight=1.0) + _trades([-0.02], weight=0.5, partial=True)
    s = be._stats(trades)
    # avg_loss weighted: only the 0.5-weight loss -> avg_loss stays 0.02 (weighted mean
    # of a single element), but total_pnl reflects the 0.5 weight directly.
    assert s["full_closes"] == 1  # the partial one is excluded from full_closes
    assert s["total_pnl"] == pytest.approx(0.02 * 1.0 + (-0.02) * 0.5)


# ── train_test_validate: decision logic, isolated from the real simulation ──

def test_train_test_validate_no_candidate(monkeypatch):
    monkeypatch.setattr(be, "_grid_search_train", lambda *a, **k: None)
    monkeypatch.setattr(be, "run_backtest", lambda *a, **k: _trades([0.01, -0.01] * 10))

    candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(20)]
    result = be.train_test_validate("BTC/USDC", candles, lookbacks=[5], thresholds=[0.002])

    assert result.verdict == "NO_CANDIDATE"
    assert result.recommended_params is None


def test_train_test_validate_overfit_rejected_when_direction_fails_to_generalize(monkeypatch):
    monkeypatch.setattr(
        be, "_grid_search_train",
        lambda *a, **k: {"lookback": 5, "threshold": 0.002, "stats": {"rr": 2.0, "total_pnl": 0.3, "full_closes": 30}},
    )

    def fake_run_backtest(symbol, candles, params, stale_candles_threshold, trend_filter_enabled, **kw):
        if not trend_filter_enabled:
            return _trades([0.02] * 5 + [-0.01] * 5)  # baseline: decent R:R
        return _trades([0.01] * 5 + [-0.02] * 5)  # filtered/test: WORSE than baseline

    monkeypatch.setattr(be, "run_backtest", fake_run_backtest)

    candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(20)]
    result = be.train_test_validate("BTC/USDC", candles, lookbacks=[5], thresholds=[0.002])

    assert result.verdict == "OVERFIT_REJECTED"
    assert result.recommended_params is None
    assert result.h1_to_h2.candidate_found and not result.h1_to_h2.generalizes


def test_train_test_validate_validated_when_both_directions_generalize(monkeypatch):
    monkeypatch.setattr(
        be, "_grid_search_train",
        lambda *a, **k: {"lookback": 5, "threshold": 0.002, "stats": {"rr": 2.0, "total_pnl": 0.3, "full_closes": 30}},
    )

    def fake_run_backtest(symbol, candles, params, stale_candles_threshold, trend_filter_enabled, **kw):
        if not trend_filter_enabled:
            return _trades([0.01] * 5 + [-0.02] * 5)  # baseline: mediocre
        return _trades([0.02] * 5 + [-0.01] * 5)  # filtered/test: BETTER than baseline

    monkeypatch.setattr(be, "run_backtest", fake_run_backtest)

    candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(20)]
    result = be.train_test_validate("BTC/USDC", candles, lookbacks=[5], thresholds=[0.002])

    assert result.verdict == "VALIDATED"
    assert result.recommended_params == {"lookback": 5, "threshold": 0.002}
    assert result.h1_to_h2.generalizes and result.h2_to_h1.generalizes


def test_train_test_validate_notes_disagreeing_params_even_when_validated(monkeypatch):
    """Both directions can independently 'generalize' yet disagree on WHICH params
    won — this must be surfaced, not hidden behind a bare VALIDATED verdict, since
    the review found params chosen by training on one half often failed when
    training on the other half of the very same disagreement."""
    calls = {"n": 0}

    def fake_grid_search(symbol, train_c, params, lookbacks, thresholds, baseline_train):
        calls["n"] += 1
        # First direction call picks lookback=5, second picks lookback=20.
        lb = 5 if calls["n"] == 1 else 20
        return {"lookback": lb, "threshold": 0.002, "stats": {"rr": 2.0, "total_pnl": 0.3, "full_closes": 30}}

    monkeypatch.setattr(be, "_grid_search_train", fake_grid_search)

    def fake_run_backtest(symbol, candles, params, stale_candles_threshold, trend_filter_enabled, **kw):
        if not trend_filter_enabled:
            return _trades([0.01] * 5 + [-0.02] * 5)
        return _trades([0.02] * 5 + [-0.01] * 5)

    monkeypatch.setattr(be, "run_backtest", fake_run_backtest)

    candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(20)]
    result = be.train_test_validate("BTC/USDC", candles, lookbacks=[5, 20], thresholds=[0.002])

    assert result.verdict == "VALIDATED"
    assert "DIFFERENT parameters" in result.note


# ── BASE_MODE/SCOUT_MODE state machine fidelity ──
# Added 2026-09-02 after finding run_backtest() never actually left BASE_MODE:
# `sm` was constructed but close_position() never called sm.record_trade(), so
# every entry was sized as if a hard stop-loss never happened. Tested directly
# against MMS4StateManager/the two extracted pure helpers wherever possible —
# avoids synthesizing candles for state-machine logic that doesn't depend on
# candle shape.

from mms4tk.state_manager import MMS4StateManager, MMS4State


def _sm(symbol="BTC/USDC"):
    return MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol=symbol, logger_db=None)


@pytest.mark.parametrize("exit_reason,expected", [
    ("STOP_LOSS", dict(is_stop_loss=True, is_take_profit=False, is_partial=False)),
    ("TP_AND_FLIP", dict(is_stop_loss=False, is_take_profit=True, is_partial=False)),
    ("STALE_SPLIT_50", dict(is_stop_loss=False, is_take_profit=True, is_partial=True)),
    ("MIDLINE_PARTIAL_TP", dict(is_stop_loss=False, is_take_profit=True, is_partial=True)),
    ("FORCE_CLOSE", dict(is_stop_loss=False, is_take_profit=False, is_partial=False)),
    ("FORCE_CLOSE_BE", dict(is_stop_loss=False, is_take_profit=False, is_partial=False)),
    ("BE_ESCAPE", dict(is_stop_loss=False, is_take_profit=False, is_partial=False)),
])
def test_trade_record_flags_table(exit_reason, expected):
    assert be._trade_record_flags(exit_reason) == expected


def test_scout_mode_entered_after_hard_sl():
    sm = _sm()
    sm.record_trade(trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0,
                     exit_price=98.0, amount=1.0, pnl=-2.0, pnl_percent=-0.02,
                     is_stop_loss=True, sl_percent=0.02)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE
    assert sm.get_symbol_size_multiplier("BTC/USDC") == pytest.approx(0.3)


def test_scout_mode_not_entered_below_2pct_sl():
    """Boundary case: the real check is `>=`, so exactly 2% DOES enter SCOUT, but
    1.5% must not."""
    sm = _sm()
    sm.record_trade(trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0,
                     exit_price=98.5, amount=1.0, pnl=-1.5, pnl_percent=-0.015,
                     is_stop_loss=True, sl_percent=0.015)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE


def test_partial_tp_preserves_scout_mode():
    """A partial TP (SPLIT_50/Midline TP) must NOT exit SCOUT_MODE — record_trade's
    own is_partial=True branch explicitly preserves it."""
    sm = _sm()
    sm.record_trade(trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0,
                     exit_price=98.0, amount=1.0, pnl=-2.0, pnl_percent=-0.02,
                     is_stop_loss=True, sl_percent=0.02)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE

    sm.record_trade(trade_id="t2", symbol="BTC/USDC", side="short", entry_price=90.0,
                     exit_price=90.0, amount=0.0, pnl=0.0, pnl_percent=0.0,
                     is_take_profit=True, is_partial=True)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE


def test_full_tp_exits_scout_mode():
    sm = _sm()
    sm.record_trade(trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0,
                     exit_price=98.0, amount=1.0, pnl=-2.0, pnl_percent=-0.02,
                     is_stop_loss=True, sl_percent=0.02)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE

    sm.record_trade(trade_id="t2", symbol="BTC/USDC", side="short", entry_price=90.0,
                     exit_price=85.0, amount=1.0, pnl=5.0, pnl_percent=0.05,
                     is_take_profit=True, is_partial=False)
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE


def test_dynamic_sizing_shrinks_in_scout_mode():
    sm = _sm()
    base = be._size_new_entry("BTC/USDC", sm)
    assert base["contracts"] == pytest.approx(be.BASE_CONTRACTS["BTC/USDC"])
    assert base["notional_weight"] == pytest.approx(1.0)
    assert base["state_at_entry"] == "BASE_MODE"

    sm.record_trade(trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0,
                     exit_price=98.0, amount=1.0, pnl=-2.0, pnl_percent=-0.02,
                     is_stop_loss=True, sl_percent=0.02)
    scout = be._size_new_entry("BTC/USDC", sm)
    assert scout["contracts"] == pytest.approx(be.BASE_CONTRACTS["BTC/USDC"] * 0.3)
    # notional_weight ~= SCOUT_SIZE_MULT * SCOUT_LEVERAGE / base_leverage ~= 0.3*1.0/3.0 = 0.1
    assert scout["notional_weight"] == pytest.approx(0.1, abs=0.01)
    assert scout["state_at_entry"] == "SCOUT_MODE"


def test_run_backtest_shrinks_entries_after_synthetic_stop_loss():
    """End-to-end (candle-driven) sanity check that the SCOUT_MODE hookup actually
    reaches run_backtest(), not just the isolated helpers above: on the real 90-day
    BTC fixture, at least one entry after a STOP_LOSS close must carry a
    notional_weight below the full-BASE 1.0, and the run must not crash."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:600]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) > 0  # this window has real SL hits — precondition for the check below
    assert any(t["weight"] < 1.0 for t in trades)


# ── HIGH_RISK_ADD (addon) simulation ──
# Added 2026-09-02 (PR 2/3): analytics.py can return HIGH_RISK_ADD_LONG/SHORT
# *instead of* BASE_LONG/SHORT on a qualifying continuation candle — the harness
# previously only handled BASE_* for entries, silently dropping every addon
# candle (a real, undocumented bias in every prior backtest result, not merely a
# missing feature). Driving analytics.py's real candle-shape detection
# deterministically by hand is its own large surface; these tests instead
# monkeypatch `MMS4SignalEngine.evaluate_signals` with a scripted signal sequence
# keyed by candle index (`len(window) - 2`), so the SIZING/GUARD/STATE logic in
# run_backtest() itself is exercised precisely and deterministically.

from mms4tk.analytics import MMS4SignalEngine, SignalResult


def _flat_candles(n, price=100.0):
    return [Candle(timestamp=i, open=price, high=price + 0.5, low=price - 0.5, close=price, volume=1.0)
            for i in range(n)]


def _scripted_engine(monkeypatch, script: Dict[int, str]):
    """`script` maps candle index `i` -> signal_type; anything unlisted returns
    NONE/is_allowed=False so nothing else fires by accident."""
    def fake_evaluate_signals(self, candles, state_manager, params=None, symbol=None):
        i = len(candles) - 2
        curr_c = candles[i]
        signal_type = script.get(i, "NONE")
        return SignalResult(signal_type=signal_type, price=curr_c.close, stop_loss=0.0,
                             leverage=1.0, is_allowed=signal_type != "NONE")
    monkeypatch.setattr(MMS4SignalEngine, "evaluate_signals", fake_evaluate_signals)


def test_addon_respects_single_addon_guard(monkeypatch):
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2  # default slope_lookback=5
    candles = _flat_candles(warmup + 10)
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",
        warmup + 1: "HIGH_RISK_ADD_LONG",
        warmup + 2: "HIGH_RISK_ADD_LONG",  # second candle in a row — must be a no-op
        warmup + 3: "BASE_SHORT",          # closes the position
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)
    closing_trades = [t for t in trades if not t.get("partial")]
    assert len(closing_trades) >= 1
    assert closing_trades[0]["addon_applied"] is True
    # Exactly ONE addon's worth of extra notional, not two — if the Single Addon
    # Guard failed and a second addon applied, weight would be ~1 + 2*(0.3/0.7).
    assert closing_trades[0]["weight"] == pytest.approx(1.0 + be.ADDON_ALLOCATION / be.BASE_ALLOCATION, abs=0.01)


def test_addon_blocked_in_scout_mode(monkeypatch):
    """The concrete proof of the PR1->PR2 sequencing dependency: this guard has no
    meaning until SCOUT_MODE exists in the harness at all."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    # SL-trigger candle dips low enough to hit a LONG entered at 100.0 (2% SL = 98.0).
    candles[warmup + 1] = Candle(timestamp=warmup + 1, open=100.0, high=100.5, low=95.0,
                                  close=100.0, volume=1.0)
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",          # entry #1, later stopped out -> SCOUT_MODE
        warmup + 2: "BASE_LONG",      # entry #2, sized under SCOUT_MODE
        warmup + 3: "HIGH_RISK_ADD_LONG",  # must be BLOCKED — state is SCOUT_MODE
        warmup + 4: "BASE_SHORT",     # closes entry #2 (still SCOUT-sized)
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1  # confirms entry #1 really was stopped out (precondition)
    scout_close = [t for t in trades if t["exit_reason"] == "TP_AND_FLIP"]
    assert len(scout_close) == 1
    assert scout_close[0]["addon_applied"] is False
    assert scout_close[0]["weight"] == pytest.approx(0.1, abs=0.01)  # pure SCOUT sizing, no addon bump


def test_addon_uses_tighter_composite_sl(monkeypatch):
    """After an addon fires, the position's SL must move to the tighter 1.25%
    composite threshold, not stay at the original 2% base threshold."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    # 1.5% below entry: BETWEEN the tight addon SL (1.25%) and the base SL (2%) —
    # discriminates the two thresholds directly.
    candles[warmup + 2] = Candle(timestamp=warmup + 2, open=100.0, high=100.5, low=98.5,
                                  close=100.0, volume=1.0)
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",
        warmup + 1: "HIGH_RISK_ADD_LONG",
        # candle warmup+2 supplies the price dip; no signal needed there
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1
    assert stop_losses[0]["addon_applied"] is True
    # exit_price is the computed SL trigger price (entry * (1 - sl_pct)), not the
    # candle's raw low. Without the addon's 1.25% composite override this would
    # never have fired at all: the base 2% SL sits at 98.0, below the candle's
    # 98.5 low, so only the tighter addon threshold (98.75) is actually crossed.
    assert stop_losses[0]["exit_price"] == pytest.approx(98.75, abs=0.01)


# ── Midline Partial TP (PLAN-009 v2) harness support ──
# Added 2026-09-02 after finding this feature shipped to production 2026-08-31
# without backtest evidence. Real-fixture-data A/B comparison (not a synthetic
# candle sequence — driving MMS4SignalEngine's real band/entry logic accurately
# with hand-built candles is its own large surface area; a slice of real,
# already-loaded market data exercises the exact same code path faster to write
# and just as faithfully) found it consistently WORSENS R:R in 6/6 windows
# (both 45-day halves and the full 90 days, both BTC/USDC and ETH/USDC) — this
# evidence led to disabling `midline_partial_tp_enabled` in production
# `system_settings` the same day. These tests lock in the wiring, not the verdict.

def test_midline_tp_disabled_by_default_produces_no_midline_exits():
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, midline_tp_enabled=False)
    assert not any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)


def test_midline_tp_enabled_produces_midline_exits_on_real_data():
    """Confirms the feature actually fires (not just that it's silent by default) —
    the discriminating half of the A/B pair used for the R:R comparison above."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, midline_tp_enabled=True, midline_tp_fraction=0.5)
    midline_trades = [t for t in trades if t["exit_reason"] == "MIDLINE_PARTIAL_TP"]
    assert len(midline_trades) > 0
    for t in midline_trades:
        assert t["partial"] is True
        assert 0 < t["weight"] < 1.0  # a 50% reduction of a >=2-contract position


def test_midline_tp_fires_at_most_once_per_position(monkeypatch):
    """Mirrors daemon.py's real guard: `position["midline_tp_done"]` must be set the
    FIRST time the midline condition is checked for a position, hit or skipped —
    a position can't collect two midline partials even across many stalled candles
    still sitting past the midline."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, midline_tp_enabled=True, midline_tp_fraction=0.5)
    # Group by entry_idx-adjacent full closes isn't tracked directly on trade dicts,
    # but the invariant that matters is global: no MORE midline partials than full
    # closes (STOP_LOSS/TP_AND_FLIP/etc.) — i.e. never two per the same position.
    full_closes = [t for t in trades if not t.get("partial")]
    midline_trades = [t for t in trades if t["exit_reason"] == "MIDLINE_PARTIAL_TP"]
    assert len(midline_trades) <= len(full_closes)


def test_midline_tp_skips_micro_positions(monkeypatch):
    """`OKXExecutor.reduction_amount()` no-ops when floor(contracts*fraction) < 1 —
    mirrors the real ledger's fail-closed micro-position guard (daemon.py:
    `existing_contracts < 2` skip). A 1-contract base size must never produce a
    MIDLINE_PARTIAL_TP trade even when the geometric trigger condition is met."""
    monkeypatch.setitem(be.BASE_CONTRACTS, "BTC/USDC", 1.0)
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, midline_tp_enabled=True, midline_tp_fraction=0.5)
    assert not any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)


# ── Real-data regression guard (slow: runs the actual candle-by-candle simulation) ──

@pytest.mark.slow
def test_real_btc_90day_data_does_not_validate_with_small_grid():
    """Regression guard: the exact BTC/USDC 90-day window used in the 2026-08-29
    review, with a small grid around the originally-shipped-then-walked-back
    parameters (lookback=3, threshold=0.002). Must NOT return VALIDATED — if this
    ever starts passing again without a deliberate, reviewed change to the
    validation methodology, treat it as a reintroduction of the original bug, not
    a win."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")
    result = be.train_test_validate("BTC/USDC", candles, lookbacks=[3, 12], thresholds=[0.001, 0.002])
    assert result.verdict != "VALIDATED"


# ── P7 (audit 2026-09-08): calibration engine must validate against LIVE params,
# not stale hardcoded literals ──

def test_different_vr_thresholds_yield_different_full_closes():
    """Required regression test (audit P7): two identical-candle runs with
    explicitly different adaptive VR thresholds must produce different
    full_closes counts — the only direct proof a VR parameter change actually
    reaches and changes engine behavior, rather than being silently ignored
    behind a hardcoded default."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]

    params_tight = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.05, adaptive_vr_low=0.95,
                                    high_vol_base_filter_enabled=True)
    params_wide = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=9.9, adaptive_vr_low=0.01,
                                   high_vol_base_filter_enabled=True)

    trades_tight = be.run_backtest("BTC/USDC", candles, params_tight, stale_candles_threshold=12,
                                    trend_filter_enabled=False)
    trades_wide = be.run_backtest("BTC/USDC", candles, params_wide, stale_candles_threshold=12,
                                   trend_filter_enabled=False)

    fc_tight = be._stats(trades_tight)["full_closes"]
    fc_wide = be._stats(trades_wide)["full_closes"]
    assert fc_tight != fc_wide


def test_train_test_validate_without_params_reads_env_not_hardcoded_literals(monkeypatch):
    """train_test_validate() called without `params` must NOT silently fall back to
    the old hardcoded adaptive_vr_high=1.0/adaptive_vr_low=0.75 literals (stale —
    production runs 1.6/0.8 by default). It must fall back to IndicatorParams()'s own
    env-var-resolved defaults instead, same as every other component in this
    codebase."""
    monkeypatch.setenv("MMS4_ADAPTIVE_VR_HIGH", "9.9")
    monkeypatch.setenv("MMS4_ADAPTIVE_VR_LOW", "0.05")

    captured: Dict[str, Any] = {}
    real_run_backtest = be.run_backtest

    def _spy(symbol, candles, params, **kwargs):
        captured["adaptive_vr_high"] = params.adaptive_vr_high
        captured["adaptive_vr_low"] = params.adaptive_vr_low
        captured["engine_mode"] = params.engine_mode
        return real_run_backtest(symbol, candles, params, **kwargs)

    monkeypatch.setattr(be, "run_backtest", _spy)

    candles = [
        Candle(timestamp=i * 3600, open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0)
        for i in range(60)
    ]
    be.train_test_validate("BTC/USDC", candles, lookbacks=[5], thresholds=[0.002])

    assert captured["adaptive_vr_high"] == 9.9
    assert captured["adaptive_vr_low"] == 0.05
    assert captured["engine_mode"] == "ADAPTIVE"


def test_run_calibration_job_passes_live_system_settings_params(tmp_path, monkeypatch):
    """run_calibration_job must read adaptive_vr_high/_low (and engine_mode/
    time_lock_*) from system_settings via LoggerDB.get_all_settings() and pass them
    explicitly to train_test_validate — not rely on that function's internal
    default, which previously meant a calibration job silently validated against
    since-reset-in-production literals instead of whatever is actually configured."""
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "job_params.db")
    db = LoggerDB(db_path=db_path)
    db.create_calibration_run("job_params", "BTC/USDC")
    db.set_setting("adaptive_vr_high", "4.2")
    db.set_setting("adaptive_vr_low", "0.15")

    fake_candles = [Candle(timestamp=i * 3600, open=1, high=1, low=1, close=1, volume=1) for i in range(100)]
    monkeypatch.setattr(be, "_fetch_calibration_candles", lambda symbol, db_: _async_return(fake_candles))

    captured: Dict[str, Any] = {}

    def _spy(symbol, candles, lookbacks, thresholds, params=None):
        captured["params"] = params
        return be.CalibrationResult(
            symbol=symbol, window_start="0", window_end="99", lookbacks=lookbacks, thresholds=thresholds,
            h1_to_h2=be.DirectionResult(train_window="H1", candidate_found=False),
            h2_to_h1=be.DirectionResult(train_window="H2", candidate_found=False),
            verdict="NO_CANDIDATE",
        )

    monkeypatch.setattr(be, "train_test_validate", _spy)

    be.run_calibration_job("job_params", "BTC/USDC", db_path)

    assert captured["params"] is not None
    assert captured["params"].adaptive_vr_high == 4.2
    assert captured["params"].adaptive_vr_low == 0.15
    assert captured["params"].engine_mode == "ADAPTIVE"


# ── run_calibration_job: the ProcessPoolExecutor worker entry point ──

def test_run_calibration_job_marks_done_on_success(tmp_path, monkeypatch):
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "job.db")
    db = LoggerDB(db_path=db_path)
    db.create_calibration_run("job1", "BTC/USDC")

    fake_candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(100)]
    monkeypatch.setattr(be, "_fetch_calibration_candles",
                         lambda symbol, db_: _async_return(fake_candles))
    fake_result = be.CalibrationResult(
        symbol="BTC/USDC", window_start="0", window_end="99", lookbacks=[5], thresholds=[0.002],
        h1_to_h2=be.DirectionResult(train_window="H1", candidate_found=False),
        h2_to_h1=be.DirectionResult(train_window="H2", candidate_found=False),
        verdict="NO_CANDIDATE",
    )
    monkeypatch.setattr(be, "train_test_validate", lambda *a, **k: fake_result)

    be.run_calibration_job("job1", "BTC/USDC", db_path)

    run = db.get_calibration_run("job1")
    assert run["status"] == "done"
    assert run["verdict"] == "NO_CANDIDATE"
    assert run["result_json"] is not None


def test_run_calibration_job_marks_failed_on_exception(tmp_path, monkeypatch):
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "job2.db")
    db = LoggerDB(db_path=db_path)
    db.create_calibration_run("job2", "BTC/USDC")

    def boom(symbol, db_):
        raise RuntimeError("OKX unreachable")

    monkeypatch.setattr(be, "_fetch_calibration_candles", boom)

    be.run_calibration_job("job2", "BTC/USDC", db_path)

    run = db.get_calibration_run("job2")
    assert run["status"] == "failed"
    assert "RuntimeError" in run["error"]


def test_run_calibration_job_fails_gracefully_with_too_few_candles(tmp_path, monkeypatch):
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "job3.db")
    db = LoggerDB(db_path=db_path)
    db.create_calibration_run("job3", "BTC/USDC")

    tiny_candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(3)]
    monkeypatch.setattr(be, "_fetch_calibration_candles", lambda symbol, db_: _async_return(tiny_candles))

    be.run_calibration_job("job3", "BTC/USDC", db_path)

    run = db.get_calibration_run("job3")
    assert run["status"] == "failed"
    assert "candles" in run["error"].lower()


def test_run_calibration_job_never_touches_system_settings(tmp_path, monkeypatch):
    """The worker is analysis-only — applying a VALIDATED result to live
    tma_trend_filter_* parameters is a separate, explicit action (PLAN-010 §5.2),
    never automatic from this function."""
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "job4.db")
    db = LoggerDB(db_path=db_path)
    db.create_calibration_run("job4", "BTC/USDC")
    db.set_setting("tma_trend_filter_enabled:BTC/USDC", "False")

    fake_candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(100)]
    monkeypatch.setattr(be, "_fetch_calibration_candles", lambda symbol, db_: _async_return(fake_candles))
    fake_result = be.CalibrationResult(
        symbol="BTC/USDC", window_start="0", window_end="99", lookbacks=[5], thresholds=[0.002],
        h1_to_h2=be.DirectionResult(train_window="H1", candidate_found=True, chosen_lookback=5,
                                     chosen_threshold=0.002, generalizes=True),
        h2_to_h1=be.DirectionResult(train_window="H2", candidate_found=True, chosen_lookback=5,
                                     chosen_threshold=0.002, generalizes=True),
        verdict="VALIDATED", recommended_params={"lookback": 5, "threshold": 0.002},
    )
    monkeypatch.setattr(be, "train_test_validate", lambda *a, **k: fake_result)

    be.run_calibration_job("job4", "BTC/USDC", db_path)

    assert db.get_setting("tma_trend_filter_enabled:BTC/USDC") == "False"


async def _async_return(value):
    return value

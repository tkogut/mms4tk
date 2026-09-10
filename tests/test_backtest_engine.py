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


def test_stats_defaults_fee_and_funding_to_zero_for_backward_compatibility():
    """Audit P2 (2026-09-08): any trade dict without fee_pct/funding_pct — every
    synthetic `_trades()`-built test trade in this file, and anything hand-built
    elsewhere — must produce IDENTICAL _stats() output to before P2 (net == gross
    when those fields are absent). Only real trades from run_backtest() (which now
    set both fields) actually become net."""
    trades = _trades([0.02, -0.01])
    s = be._stats(trades)
    assert s["total_pnl"] == pytest.approx(0.02 - 0.01)


def test_stats_subtracts_fee_and_funding_when_present():
    trades = _trades([0.02])
    trades[0]["fee_pct"] = 0.0010
    trades[0]["funding_pct"] = 0.0002
    s = be._stats(trades)
    assert s["total_pnl"] == pytest.approx(0.02 - 0.0010 - 0.0002)


# ── P2 (audit 2026-09-08): round-trip fee + funding cost helper ──

def test_round_trip_cost_pct_fee_is_flat_funding_scales_with_hold_duration():
    zero_hold = be._round_trip_cost_pct(0)
    short_hold = be._round_trip_cost_pct(3)   # < 8 candles -> 1 funding interval (ceil)
    long_hold = be._round_trip_cost_pct(9)    # > 8 candles -> 2 funding intervals

    # fee_pct is flat — duration-independent — regardless of hold length.
    assert zero_hold["fee_pct"] == short_hold["fee_pct"] == long_hold["fee_pct"] == be.ROUND_TRIP_FEE_PCT
    assert zero_hold["funding_pct"] == 0.0
    assert short_hold["funding_pct"] == pytest.approx(be.FUNDING_RATE_PCT_PER_INTERVAL)
    assert long_hold["funding_pct"] == pytest.approx(be.FUNDING_RATE_PCT_PER_INTERVAL * 2)


def test_run_backtest_trades_carry_fee_and_funding_fields():
    """Real trades produced by run_backtest() must set fee_pct/funding_pct — this is
    what makes _stats() on real results actually net, not silently gross by
    omission."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)
    assert len(trades) > 0
    for t in trades:
        assert t["fee_pct"] == pytest.approx(be.ROUND_TRIP_FEE_PCT)
        assert t["funding_pct"] >= 0.0


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


def test_train_test_validate_rejects_test_half_with_too_few_trades(monkeypatch):
    """Audit P7 'same file' follow-on (2026-09-08): a test half that beats baseline
    on R:R and total_pnl but with too few trades (< MIN_FULL_CLOSES_HALF) must NOT
    count as generalizing — otherwise a handful of lucky trades could pass purely on
    variance. Without this gate, this exact scenario (great R:R, only 3 trades)
    would have generalized."""
    monkeypatch.setattr(
        be, "_grid_search_train",
        lambda *a, **k: {"lookback": 5, "threshold": 0.002, "stats": {"rr": 2.0, "total_pnl": 0.3, "full_closes": 30}},
    )

    def fake_run_backtest(symbol, candles, params, stale_candles_threshold, trend_filter_enabled, **kw):
        if not trend_filter_enabled:
            return _trades([0.01] * 5 + [-0.02] * 5)  # baseline: mediocre, 10 trades
        return _trades([0.05, 0.05, 0.05])  # test half: great R:R/pnl, only 3 trades

    monkeypatch.setattr(be, "run_backtest", fake_run_backtest)

    candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(20)]
    result = be.train_test_validate("BTC/USDC", candles, lookbacks=[5], thresholds=[0.002])

    assert result.verdict == "OVERFIT_REJECTED"
    assert result.h1_to_h2.candidate_found and not result.h1_to_h2.generalizes


def test_train_test_validate_validated_when_both_directions_generalize(monkeypatch):
    monkeypatch.setattr(
        be, "_grid_search_train",
        lambda *a, **k: {"lookback": 5, "threshold": 0.002, "stats": {"rr": 2.0, "total_pnl": 0.3, "full_closes": 30}},
    )

    def fake_run_backtest(symbol, candles, params, stale_candles_threshold, trend_filter_enabled, **kw):
        if not trend_filter_enabled:
            return _trades([0.01] * 5 + [-0.02] * 5)  # baseline: mediocre
        # filtered/test: BETTER than baseline, and >= MIN_FULL_CLOSES_HALF (30) trades
        return _trades([0.02] * 15 + [-0.01] * 15)

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
        return _trades([0.02] * 15 + [-0.01] * 15)  # >= MIN_FULL_CLOSES_HALF (30) trades

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
    # sl_cooldown_candles=0: this test is scripting entry #2 for the very next candle
    # after the SL that enters SCOUT_MODE, to isolate the Single Addon Guard/SCOUT_MODE
    # sequencing — not Post-SL Cooldown fidelity (covered separately), which would
    # otherwise block entry #2 outright and make this scenario untestable.
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, sl_cooldown_candles=0)
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


# ── Addon SL variants (audit P1, 2026-09-08) ──
# Base LONG @ 100.0 (4 contracts), addon fires at 110.0 (not the same price as the
# base — needed to make avgPx (103.0, worked out below) diverge from
# base_entry_price (100.0), the only way to actually discriminate V2 from V0/V1:
#   addon_contracts = 4.0 * (0.3/0.7) ≈ 1.714286
#   avgPx = (4.0*100.0 + 1.714286*110.0) / (4.0+1.714286) = 103.0 exactly
# A deep dip (low=90.0) guarantees a trigger under ALL three variants' thresholds,
# so exit_price alone discriminates which formula actually fired.

def _addon_sl_variant_scenario(monkeypatch, variant):
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    candles[warmup + 1] = Candle(timestamp=warmup + 1, open=100.0, high=110.5, low=99.5,
                                  close=110.0, volume=1.0)  # addon fires here, at 110.0
    candles[warmup + 2] = Candle(timestamp=warmup + 2, open=110.0, high=110.5, low=90.0,
                                  close=110.0, volume=1.0)  # deep dip: triggers under any variant
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",
        warmup + 1: "HIGH_RISK_ADD_LONG",
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, addon_sl_variant=variant)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1
    assert stop_losses[0]["addon_applied"] is True
    return stop_losses[0]


def test_addon_sl_variant_v0_is_composite_from_avgpx(monkeypatch):
    """V0 (current production, also the default): composite 1.25% from the
    averaged (avgPx=103.0) entry -> 103.0 * 0.9875 = 101.7125."""
    sl = _addon_sl_variant_scenario(monkeypatch, "V0")
    assert sl["exit_price"] == pytest.approx(101.7125, abs=0.01)


def test_addon_sl_variant_v1_is_wider_composite_from_avgpx(monkeypatch):
    """V1: composite 2.0% from the same averaged (avgPx=103.0) entry ->
    103.0 * 0.98 = 100.94 — same mechanism as V0, wider threshold."""
    sl = _addon_sl_variant_scenario(monkeypatch, "V1")
    assert sl["exit_price"] == pytest.approx(100.94, abs=0.01)


def test_addon_sl_variant_v2_stays_pegged_to_original_base_entry(monkeypatch):
    """V2: SL pegged to the ORIGINAL base entry (100.0), never the addon-averaged
    103.0 -> 100.0 * 0.98 = 98.0. Proves the addon fill did NOT move the SL basis
    at all, unlike V0/V1."""
    sl = _addon_sl_variant_scenario(monkeypatch, "V2")
    assert sl["exit_price"] == pytest.approx(98.0, abs=0.01)


def test_run_backtest_rejects_unknown_addon_sl_variant():
    params = IndicatorParams()
    candles = _flat_candles(50)
    with pytest.raises(ValueError, match="addon_sl_variant"):
        be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                         trend_filter_enabled=False, addon_sl_variant="V3")


# ── Post-SL Cooldown (audit P7 "same file" follow-on, 2026-09-08) ──
# Mirrors daemon.py's PLAN-008 Phase 2 mechanism (_sl_cooldown_until): after a
# genuine STOP_LOSS close, new BASE_LONG/BASE_SHORT entries are blocked for
# sl_cooldown_candles candles (default 3). Both tests below place a SECOND price
# dip at the candle a blocked-but-not-actually-blocked re-entry would occupy —
# if the cooldown fails to block it, that second dip stops it out too, giving a
# second STOP_LOSS; if correctly blocked, no position exists there to be stopped.

def test_post_sl_cooldown_blocks_immediate_reentry(monkeypatch):
    """With the default 3-candle cooldown active, a re-entry attempted 2 candles
    after the stop-out must be blocked entirely — only entry #1's STOP_LOSS shows
    up, never a second one."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    # Entry #1 (opened at warmup, price 100.0) is stopped out here (SL@98.0).
    candles[warmup + 1] = Candle(timestamp=warmup + 1, open=100.0, high=100.5, low=95.0,
                                  close=100.0, volume=1.0)
    # Would ALSO stop out a LONG entered at warmup+2 (100.0 -> SL@98.0) if the
    # cooldown (active until ts=warmup+4) failed to block that re-entry. A LONG
    # entered only at warmup+4, once cooldown clears, is unaffected — this candle
    # has already passed by then.
    candles[warmup + 3] = Candle(timestamp=warmup + 3, open=100.0, high=100.5, low=95.0,
                                  close=100.0, volume=1.0)
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",       # entry #1
        warmup + 2: "BASE_LONG",   # within cooldown (until ts=warmup+4) -> must be BLOCKED
        warmup + 4: "BASE_LONG",   # cooldown just cleared -> allowed, no more dips ahead
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, sl_cooldown_candles=3)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1  # only entry #1 — entry attempted at warmup+2 never opened


def test_post_sl_cooldown_disabled_lets_second_stop_loss_through(monkeypatch):
    """Sanity check for the test above: with sl_cooldown_candles=0 (disabled) on the
    IDENTICAL scenario, the re-entry at warmup+2 DOES open and DOES get stopped out
    by the second dip — proving the scenario is capable of detecting the cooldown's
    absence, not just always producing one STOP_LOSS regardless."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    candles[warmup + 1] = Candle(timestamp=warmup + 1, open=100.0, high=100.5, low=95.0,
                                  close=100.0, volume=1.0)
    candles[warmup + 3] = Candle(timestamp=warmup + 3, open=100.0, high=100.5, low=95.0,
                                  close=100.0, volume=1.0)
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",
        warmup + 2: "BASE_LONG",
        warmup + 4: "BASE_LONG",
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, sl_cooldown_candles=0)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 2


# ── P2 (audit 2026-09-08): NET-pnl re-validation of two already-decided filters ──
# These are regression tests, not new analysis: the audit's own required step for
# this ticket is proving the fee/funding model does NOT silently change a decision
# that was made on gross pnl. If either of these ever starts failing because a
# verdict genuinely flipped, that filter needs re-review as a fresh candidate
# (audit §5 stage 1) — do not "fix" this test to force it back to passing without
# that review.

@pytest.mark.slow
def test_p2_midline_tp_still_loses_all_6_windows_net_of_fees():
    """Re-validates the Midline TP decision (BTC/ETH x H1/H2/full = 6 windows,
    matching the original 2026-09-02 review below) under NET pnl. Computed
    2026-09-09: Midline TP does not beat baseline on BOTH R:R and total_pnl in any
    of the 6 windows, gross or net — the fee/funding model does not change this
    conclusion. `system_settings`'s `midline_partial_tp_enabled=False` stays correct."""
    windows = []
    for label, fname, symbol in [("BTC", "ohlcv_BTC_USDC_1h.csv", "BTC/USDC"),
                                  ("ETH", "ohlcv_ETH_USDC_1h.csv", "ETH/USDC")]:
        candles = load_fixture_candles(fname)
        mid = len(candles) // 2
        windows.append((f"{label}_H1", symbol, candles[:mid]))
        windows.append((f"{label}_H2", symbol, candles[mid:]))
        windows.append((f"{label}_full", symbol, candles))

    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    beats: Dict[str, bool] = {}
    for label, symbol, candles in windows:
        off = be._stats(be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                                         trend_filter_enabled=False, midline_tp_enabled=False))
        on = be._stats(be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                                        trend_filter_enabled=False, midline_tp_enabled=True,
                                        midline_tp_fraction=0.5))
        beats[label] = on["rr"] > off["rr"] and on["total_pnl"] > off["total_pnl"]

    assert not any(beats.values()), f"Midline TP beat baseline (NET) in: {[k for k, v in beats.items() if v]}"


@pytest.mark.slow
def test_p2_high_vol_base_filter_verdict_unchanged_net_of_fees():
    """Re-validates the high-vol base filter decision (BTC/ETH x H1/H2 = 4 windows)
    under NET pnl. Computed 2026-09-09: beats baseline in exactly BTC_H2 and ETH_H2
    (2/4), the SAME pattern gross and net — the fee/funding model does not flip
    this verdict either way. Stays opt-in (`high_vol_base_filter_enabled=False`
    default) in `system_settings`."""
    windows = []
    for label, fname, symbol in [("BTC", "ohlcv_BTC_USDC_1h.csv", "BTC/USDC"),
                                  ("ETH", "ohlcv_ETH_USDC_1h.csv", "ETH/USDC")]:
        candles = load_fixture_candles(fname)
        mid = len(candles) // 2
        windows.append((f"{label}_H1", symbol, candles[:mid]))
        windows.append((f"{label}_H2", symbol, candles[mid:]))

    beats: Dict[str, bool] = {}
    for label, symbol, candles in windows:
        params_off = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8,
                                      high_vol_base_filter_enabled=False)
        params_on = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8,
                                     high_vol_base_filter_enabled=True)
        off = be._stats(be.run_backtest(symbol, candles, params_off, stale_candles_threshold=12,
                                         trend_filter_enabled=False))
        on = be._stats(be.run_backtest(symbol, candles, params_on, stale_candles_threshold=12,
                                        trend_filter_enabled=False))
        beats[label] = on["rr"] > off["rr"] and on["total_pnl"] > off["total_pnl"]

    expected = {"BTC_H1": False, "BTC_H2": True, "ETH_H1": False, "ETH_H2": True}
    assert beats == expected, f"High-vol filter NET verdict changed: {beats} vs expected {expected}"


@pytest.mark.slow
def test_p1_addon_sl_gate_analysis():
    """Audit P1 (2026-09-08): required gate for implementing V1 or V2 in production
    — V1 OR V2 must beat V0 on BOTH R:R and total_pnl (NET) in >= 5/6 windows
    (BTC/ETH x H1/H2/full).

    CORRECTED 2026-09-10 after independent review (2nd agent) found the first
    version of this test used adaptive_vr_high=1.0/adaptive_vr_low=0.75 — the exact
    literals audit P7 (§1.5) calls "stale, already reset in production" (production
    runs 1.6/0.8). Under those stale thresholds 2 of the 6 "windows" had ZERO addon
    fills at all (BTC_H1, ETH_H2 — V0/V1/V2 were bit-for-bit identical), so they
    carried no information yet still counted toward the 5/6 denominator.

    Recomputed under PRODUCTION thresholds (adaptive_vr_high=1.6, adaptive_vr_low=0.8,
    confirmed live in system_settings) — real numbers, 2026-09-10:
        strict gate (BOTH R:R and total_pnl beat V0): V1 = 3/6, V2 = 3/6
        total_pnl ALONE (no R:R requirement):         V1 = 5/6 (never worse than V0,
                                                        tied in ETH_H2 which has zero
                                                        addon fills under either VR
                                                        setting), V2 = 4/6

    Neither variant clears the strict 5/6 gate, so per the audit's own fallback
    ("poniżej — zostaje V0...") V0 stays in production for now. But this is NOT a
    clean rejection of the hypothesis: V1's total_pnl is consistently at least as
    good as V0's, never worse, in every window that has any addon activity at all —
    it fails the STRICT gate only because R:R softens by a few percent in 2 of the
    6 windows (BTC_H1: 1.32->1.18, ETH_H1: 1.31->1.29). A future reader should NOT
    read "V0 wins" as "V1/V2 are worse" — read it as "the combined R:R+PnL gate is a
    high bar that a directionally-positive PnL signal doesn't clear alone".

    Separately, and NOT fixed by this correction: V0 in this harness models the
    composite SL as 1.25% of the AVERAGED (avgPx) entry — but production's actual
    mechanism (daemon.py ~1574, okx_executor's attach_stop_loss with
    price=signal.price) computes the addon leg's SL off the ADDON FILL PRICE, not
    avgPx. For the audit §1.1 example (base@100 short, addon@99), that is ~0.54%
    real risk from avgPx, not the harness's 1.25% — i.e. V0-in-this-harness is
    already ~2.3x more conservative than production's real V0. A null/weak result
    against this softened baseline does NOT falsify the §1.1 diagnosis, because the
    pathological case §1.1 describes was never actually modeled here. This is a
    known, documented limitation of this analysis, not something this test can
    self-correct — closing it requires modeling per-fill-price SL in the harness,
    a separate, larger piece of work.

    This test locks in the STRICT gate numbers (3/6, 3/6) as a regression guard: if
    either ever clears 5/6, a real change to run_backtest's addon/SL/signal logic
    moved a variant past the gate — that IS grounds to revisit the P1 production
    decision. It does NOT lock in "V0 is confirmed correct" — that conclusion was
    withdrawn along with the stale-VR-params bug this docstring documents.
    """
    windows = []
    for label, fname, symbol in [("BTC", "ohlcv_BTC_USDC_1h.csv", "BTC/USDC"),
                                  ("ETH", "ohlcv_ETH_USDC_1h.csv", "ETH/USDC")]:
        candles = load_fixture_candles(fname)
        mid = len(candles) // 2
        windows.append((f"{label}_H1", symbol, candles[:mid]))
        windows.append((f"{label}_H2", symbol, candles[mid:]))
        windows.append((f"{label}_full", symbol, candles))

    # Production thresholds — NOT the stale 1.0/0.75 the first version of this test
    # used (see docstring). Confirm against live system_settings before trusting
    # this literal again if VR calibration ever changes.
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    v1_strict_wins = 0
    v2_strict_wins = 0
    v1_pnl_wins = 0
    v2_pnl_wins = 0
    any_addon_activity = 0
    for label, symbol, candles in windows:
        v0_trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                                     trend_filter_enabled=False, addon_sl_variant="V0")
        v1_trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                                     trend_filter_enabled=False, addon_sl_variant="V1")
        v2_trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                                     trend_filter_enabled=False, addon_sl_variant="V2")
        v0, v1, v2 = be._stats(v0_trades), be._stats(v1_trades), be._stats(v2_trades)

        if any(t.get("addon_applied") for t in v0_trades):
            any_addon_activity += 1
        if v1["rr"] > v0["rr"] and v1["total_pnl"] > v0["total_pnl"]:
            v1_strict_wins += 1
        if v2["rr"] > v0["rr"] and v2["total_pnl"] > v0["total_pnl"]:
            v2_strict_wins += 1
        if v1["total_pnl"] > v0["total_pnl"]:
            v1_pnl_wins += 1
        if v2["total_pnl"] > v0["total_pnl"]:
            v2_pnl_wins += 1

    # Sanity: at least SOME windows must actually have addon fills, or this whole
    # analysis is measuring nothing (the stale-VR-params bug hid this before).
    assert any_addon_activity >= 4, (
        f"only {any_addon_activity}/6 windows had any addon activity — the analysis "
        "may be running under a VR configuration that starves HIGH_RISK_ADD signals"
    )

    assert v1_strict_wins < 5, f"V1 now clears the strict P1 gate ({v1_strict_wins}/6) — revisit the production decision"
    assert v2_strict_wins < 5, f"V2 now clears the strict P1 gate ({v2_strict_wins}/6) — revisit the production decision"
    # Locks in the softer PnL-only signal too, so it stays visible rather than
    # silently regressing without anyone noticing — this is documentation, not a
    # deployment gate.
    assert v1_pnl_wins == 5, f"V1's total_pnl-only signal changed ({v1_pnl_wins}/6, was 5/6) — re-read this test's docstring"


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

    fake_candles = [Candle(timestamp=i * 3600, open=1, high=1, low=1, close=1, volume=1) for i in range(150)]
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


def test_default_grid_is_the_approved_4x4():
    """Audit P7 'same file' follow-on (2026-09-08) regression guard: the default
    calibration grid must stay at the reviewed/approved 4x4 (16 cells, PLAN-010
    TICKET-3) — if this ever grows again without a deliberate, reviewed change,
    treat it as reintroducing the 4x multiple-testing exposure the reduction from
    8x8 (64 cells) fixed."""
    assert be.DEFAULT_LOOKBACKS == [3, 5, 8, 13]
    assert be.DEFAULT_THRESHOLDS == [0.0010, 0.0015, 0.0020, 0.0030]
    assert len(be.DEFAULT_LOOKBACKS) * len(be.DEFAULT_THRESHOLDS) == 16


# ── run_calibration_job: the ProcessPoolExecutor worker entry point ──

def test_run_calibration_job_marks_done_on_success(tmp_path, monkeypatch):
    from mms4tk.logger_db import LoggerDB

    db_path = str(tmp_path / "job.db")
    db = LoggerDB(db_path=db_path)
    db.create_calibration_run("job1", "BTC/USDC")

    fake_candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(150)]
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

    fake_candles = [Candle(timestamp=i, open=1, high=1, low=1, close=1, volume=1) for i in range(150)]
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

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
from mms4tk.okx_executor import OKXExecutor

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


# ── A1 (2026-09-10): ROUND_TRIP_FEE_PCT calibrated on production fee_usd ──

def test_round_trip_fee_pct_is_the_production_calibrated_value_not_an_assumption():
    """Audit P2(c) point 3 required this constant to be DERIVED from production
    `closed_trades.fee_usd`, not assumed. Measured 2026-09-10 over 52 trades from
    2026-08-30 (v0.41.0, first release with real fee_usd) to 2026-09-10:

        SUM(fee_usd) / SUM(entry_price * amount * contract_size) = 0.00100029
        (BTC 0.00099968 over 29 trades, ETH 0.00100103 over 23 trades)

    The measurement confirmed the previous assumption to within 0.03% relative,
    but the constant must no longer be `TAKER_FEE_PCT * 2` — that expression is an
    assumption about the OKX fee schedule, and re-deriving it instead of
    re-measuring is exactly what the audit called out. If OKX fee tiers change,
    re-run the calibration against fresh production data and update this test with
    the new measurement; do not restore the arithmetic-from-TAKER_FEE_PCT form."""
    assert be.ROUND_TRIP_FEE_PCT == pytest.approx(0.0010003, abs=1e-9)
    # Within 0.1% relative of the measured blended production rate.
    assert be.ROUND_TRIP_FEE_PCT == pytest.approx(0.00100029, rel=1e-3)
    # Per-symbol rates bracket it: a per-symbol constant was rejected because the
    # BTC/ETH spread is only 0.13% of the blended rate.
    assert 0.00099968 <= be.ROUND_TRIP_FEE_PCT <= 0.00100103 or \
        abs(be.ROUND_TRIP_FEE_PCT - 0.00100029) < 1e-6


# ── Faza 5 (2026-09-12): round_trip_fee_pct_override (maker-first measurement) ──

def test_round_trip_cost_pct_override_none_is_identical_to_no_override():
    """Default (None) must reproduce today's behavior bit-for-bit — no override
    param at all vs explicit None are the same call."""
    default = be._round_trip_cost_pct(5)
    explicit_none = be._round_trip_cost_pct(5, None)
    assert default == explicit_none
    assert default["fee_pct"] == be.ROUND_TRIP_FEE_PCT


def test_round_trip_cost_pct_override_replaces_fee_pct_only():
    """A non-None override replaces fee_pct with the override value; funding_pct
    (an independent, maker/taker-agnostic mechanism) is untouched."""
    baseline = be._round_trip_cost_pct(9)
    overridden = be._round_trip_cost_pct(9, be.MAKER_ROUND_TRIP_FEE_PCT)
    assert overridden["fee_pct"] == pytest.approx(be.MAKER_ROUND_TRIP_FEE_PCT)
    assert overridden["fee_pct"] != baseline["fee_pct"]
    assert overridden["funding_pct"] == baseline["funding_pct"]


def test_maker_round_trip_fee_pct_matches_a1_calibration_ratio():
    """MAKER_ROUND_TRIP_FEE_PCT must be derived by applying the SAME A1
    calibration ratio (ROUND_TRIP_FEE_PCT / (2 * TAKER_FEE_PCT)) to OKX's public
    maker rate (0.02%/leg) as was used for the taker constant — not an
    independently-invented number (task.md, Faza 5, Krok 1)."""
    calibration_ratio = be.ROUND_TRIP_FEE_PCT / (2 * be.TAKER_FEE_PCT)
    expected = 2 * 0.0002 * calibration_ratio
    assert be.MAKER_ROUND_TRIP_FEE_PCT == pytest.approx(expected, rel=1e-6)
    # Sanity: maker round-trip must be materially cheaper than taker round-trip.
    assert be.MAKER_ROUND_TRIP_FEE_PCT < be.ROUND_TRIP_FEE_PCT


def test_run_backtest_without_override_is_unchanged():
    """No override passed (existing call signature) -> identical trades/fee_pct to
    before this parameter existed."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    baseline = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)
    with_explicit_none = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False,
        round_trip_fee_pct_override=None,
    )
    assert len(baseline) == len(with_explicit_none) > 0
    for a, b in zip(baseline, with_explicit_none):
        assert a["fee_pct"] == b["fee_pct"] == pytest.approx(be.ROUND_TRIP_FEE_PCT)
        assert a["pnl_pct"] == pytest.approx(b["pnl_pct"])
        assert a["exit_reason"] == b["exit_reason"]


def test_run_backtest_override_changes_fee_and_stats_cost():
    """override actually changes the per-trade fee_pct AND the resulting _stats()
    total_pnl (net cost), on the same trade population/entries as the baseline."""
    candles = load_fixture_candles("ohlcv_BTC_USDC_1h.csv")[:400]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.0, adaptive_vr_low=0.75)
    taker_trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False)
    maker_trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=12, trend_filter_enabled=False,
        round_trip_fee_pct_override=be.MAKER_ROUND_TRIP_FEE_PCT,
    )
    assert len(taker_trades) == len(maker_trades) > 0
    for t in maker_trades:
        assert t["fee_pct"] == pytest.approx(be.MAKER_ROUND_TRIP_FEE_PCT)
    # Same entries/exits, only the fee model differs -> gross pnl_pct/funding_pct
    # identical, net total_pnl strictly higher under the cheaper maker fee.
    for a, b in zip(taker_trades, maker_trades):
        assert a["pnl_pct"] == pytest.approx(b["pnl_pct"])
        assert a["funding_pct"] == pytest.approx(b["funding_pct"])
    taker_stats = be._stats(taker_trades)
    maker_stats = be._stats(maker_trades)
    assert maker_stats["total_pnl"] > taker_stats["total_pnl"]
    fee_delta_per_leg = be.ROUND_TRIP_FEE_PCT - be.MAKER_ROUND_TRIP_FEE_PCT
    total_weight = sum(t.get("weight", 1.0) for t in taker_trades)
    expected_delta = fee_delta_per_leg * total_weight
    assert maker_stats["total_pnl"] - taker_stats["total_pnl"] == pytest.approx(expected_delta, rel=1e-6)


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


def _tight_dip_addon_scenario(monkeypatch, **kwargs):
    """Base LONG @ 100.0, addon on the next (flat, same-price) candle, then a dip to
    98.5 — i.e. 1.5% below entry, BETWEEN the legacy composite addon SL (1.25% =>
    98.75) and the base SL (2% => 98.0). Discriminates "the addon tightened the SL"
    from "it did not" directly, in a geometry where base_entry_price == avgPx, so the
    ONLY thing under test is the SL PERCENTAGE, not the anchor."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    candles[warmup + 2] = Candle(timestamp=warmup + 2, open=100.0, high=100.5, low=98.5,
                                  close=100.0, volume=1.0)
    _scripted_engine(monkeypatch, {
        warmup: "BASE_LONG",
        warmup + 1: "HIGH_RISK_ADD_LONG",
        # candle warmup+2 supplies the price dip; no signal needed there
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False, **kwargs)
    return trades


def test_addon_does_not_tighten_sl_under_default(monkeypatch):
    """P3 V2 (production since 2026-09-10, harness default since 2026-09-11): an
    addon fill must NOT tighten the position's stop. The addon leg gets no SL of its
    own and the base algo (`closeFraction="1"`, already covering the enlarged
    position) is left untouched at base_sl_pct from base_entry_price — so a 1.5% dip,
    which the retired V0 composite 1.25% stop WOULD have caught, leaves the position
    open here."""
    trades = _tight_dip_addon_scenario(monkeypatch)
    assert not [t for t in trades if t["exit_reason"] == "STOP_LOSS"]


def test_addon_uses_tighter_composite_sl_under_legacy_v0(monkeypatch):
    """The pre-P3 mechanism, still reachable explicitly for the P1 analysis: after an
    addon fires, the SL moves to the tighter 1.25% composite threshold. Same scenario
    as the test above — the ONLY difference is `addon_sl_variant="V0"`, which is what
    makes this pair a real discriminator of the default rather than two unrelated
    assertions."""
    trades = _tight_dip_addon_scenario(monkeypatch, addon_sl_variant="V0")
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1
    assert stop_losses[0]["addon_applied"] is True
    # exit_price is the computed SL trigger price (entry * (1 - sl_pct)), not the
    # candle's raw low: 100.0 * (1 - 0.0125) = 98.75.
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
    """`variant=None` means "call run_backtest without the argument at all" — used to
    prove the DEFAULT equals an explicit variant, not merely that the default exists."""
    kwargs = {} if variant is None else {"addon_sl_variant": variant}
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
                              trend_filter_enabled=False, **kwargs)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1
    assert stop_losses[0]["addon_applied"] is True
    return stop_losses[0]


def test_addon_sl_variant_v0_is_composite_from_avgpx(monkeypatch):
    """V0 (PRE-P3 production, retired 2026-09-10, no longer the default): composite
    1.25% from the averaged (avgPx=103.0) entry -> 103.0 * 0.9875 = 101.7125."""
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


def test_addon_sl_variant_defaults_to_v2_the_live_production_mechanism(monkeypatch):
    """The DEFAULT must be bit-for-bit identical to explicit V2 (2026-09-11).

    Until this change `run_backtest()` defaulted to V0 — the composite-off-avgPx stop
    production retired on 2026-09-10 when P3 V2 shipped — so every backtest, the P4 SL
    grid, the engine_mode A/B and the P1 gate itself measured a mechanism the live
    system no longer runs. Comparing the no-argument call against an explicit "V2"
    call (rather than just reading the signature's default) also catches any future
    branch that reads the variant string somewhere the default doesn't reach."""
    default_sl = _addon_sl_variant_scenario(monkeypatch, None)
    explicit_v2 = _addon_sl_variant_scenario(monkeypatch, "V2")
    assert default_sl == explicit_v2
    assert default_sl["exit_price"] == pytest.approx(98.0, abs=0.01)  # NOT V0's 101.7125


@pytest.mark.parametrize("side,signal", [("long", "BASE_LONG"), ("short", "BASE_SHORT")])
def test_default_variant_sl_price_matches_production_formula_exactly(monkeypatch, side, signal):
    """Fidelity lock: the harness's own stop trigger under the default variant must
    equal what production actually places on OKX for the same position — the SHARED
    production formula `OKXExecutor._compute_sl_trigger_price(pos_side, ref, pct)`
    evaluated at `ref = base_entry_price` (daemon's `_base_entry_price()`, pinned at
    the position's first snapshot and never moved by addon averaging) and
    `pct = base_sl_pct` (daemon's `MMS4_BASE_SL_PCT`, default 0.02 == BASE_SL_PCT here).

    That is the one anchor production uses in all three places after P3 V2:
    `reconcile_positions` (stop-out classification), the Self-Healing SL Guard and the
    Midline-TP re-attach — while the HIGH_RISK_ADD branch attaches nothing at all
    (`execute_order(..., stop_loss_pct=0.0, ...)`). Asserting against the imported
    production function, not a copied literal, is what makes this a 1:1 check."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10)
    # Addon fills 10% AWAY from the base entry, so avgPx (103.0 long / 97.0 short)
    # differs from base_entry_price (100.0): if the harness ever anchored to avgPx
    # again, the expected price below could not match.
    # The addon candle must move only in the position's FAVOUR — a wick through the
    # base stop here would close the position before the addon signal is even read.
    addon_close = 110.0 if side == "long" else 90.0
    candles[warmup + 1] = Candle(
        timestamp=warmup + 1, open=100.0,
        high=110.5 if side == "long" else 100.5,
        low=99.5 if side == "long" else 89.5,
        close=addon_close, volume=1.0)
    candles[warmup + 2] = Candle(timestamp=warmup + 2, open=addon_close, high=120.0, low=80.0,
                                  close=addon_close, volume=1.0)  # deep both ways: triggers either side
    _scripted_engine(monkeypatch, {
        warmup: signal,
        warmup + 1: "HIGH_RISK_ADD_LONG" if side == "long" else "HIGH_RISK_ADD_SHORT",
    })
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False)
    stop_losses = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(stop_losses) == 1
    assert stop_losses[0]["addon_applied"] is True

    expected = OKXExecutor._compute_sl_trigger_price(side, 100.0, be.BASE_SL_PCT)
    assert stop_losses[0]["exit_price"] == pytest.approx(expected, abs=1e-9)


def test_run_backtest_rejects_unknown_addon_sl_variant():
    params = IndicatorParams()
    candles = _flat_candles(50)
    with pytest.raises(ValueError, match="addon_sl_variant"):
        be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                         trend_filter_enabled=False, addon_sl_variant="V3")


# ── Phase 3-4 (task.md): D1 ATR exhaustion filter — measurement-only, default OFF ──
# Reuses params.htf_expansion_threshold (2.5, analytics.py) as the reference, not a
# new number. `_daily_epoch_candles` below builds REAL epoch-ms timestamps (unlike
# `_flat_candles`'s index-as-timestamp, which would collapse everything into one
# UTC day) so day boundaries are meaningful.

DAY_MS = 24 * 3_600_000


def _daily_epoch_candles(n_days, spike_day=None, spike_hour=5,
                          spike_high=101.2, spike_low=98.3, start_ts=1_700_000_000_000):
    """`n_days` full UTC days of flat, quiet hourly candles (open=close=100.0,
    high=100.5/low=99.5 -> D1 True Range/ATR converges to ~1.0 once warmed up).
    `spike_day` (0-indexed) gets ONE overextended candle at `spike_hour` whose
    high/low blow the day's running range past `2.5 x ATR_D1(~1.0)` while staying
    inside the 2% base SL band (98.0-102.0 for a LONG @ 100.0) so the position
    survives to be closed by a scripted signal instead of a real stop-out."""
    start_ts -= start_ts % DAY_MS  # align to a UTC day boundary
    candles = []
    ts = start_ts
    for d in range(n_days):
        for h in range(24):
            if d == spike_day and h == spike_hour:
                c = Candle(timestamp=ts, open=100.0, high=spike_high, low=spike_low, close=100.0, volume=1.0)
            else:
                c = Candle(timestamp=ts, open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0)
            candles.append(c)
            ts += 3_600_000
    return candles


def test_compute_d1_exhaustion_flags_is_causal_and_resets_next_day():
    """Pure unit test of the flag builder: never flagged on the very first day (no
    prior-day ATR reference exists yet), flagged from the exact hour the running
    day range crosses threshold x ATR_ref onward (not before, not the whole day),
    and back to unflagged the next day even though ATR_ref is now elevated by the
    spike (the next day's OWN range is small, so it clears easily)."""
    params = IndicatorParams()
    candles = _daily_epoch_candles(n_days=22, spike_day=20, spike_hour=5)
    flags = be._compute_d1_exhaustion_flags(candles, params)
    assert len(flags) == len(candles)
    assert not any(flags[0:24])  # day 0: no reference day exists yet
    spike_day_flags = flags[20 * 24:21 * 24]
    assert spike_day_flags[:5] == [False] * 5           # before the spike hour: not exhausted
    assert all(spike_day_flags[5:])                     # from the spike hour on: exhausted, stays exhausted
    assert not any(flags[21 * 24:22 * 24])               # the day AFTER: resets, quiet range clears easily


def test_compute_d1_exhaustion_flags_empty_input():
    assert be._compute_d1_exhaustion_flags([], IndicatorParams()) == []


def _d1_exhaustion_scenario(monkeypatch, addon_hour, filter_enabled):
    """BASE_LONG opened the hour before `spike_day` starts; a HIGH_RISK_ADD_LONG is
    scripted at `addon_hour` on the spike day itself; the position is closed by a
    BASE_SHORT (TP_AND_FLIP) two hours later so the addon's fate is observable on
    `addon_applied` without a real stop-out or inactivity exit muddying the result."""
    params = IndicatorParams()
    spike_day = 20
    candles = _daily_epoch_candles(n_days=22, spike_day=spike_day, spike_hour=5)
    entry_idx = spike_day * 24 - 1     # last hour of the day before the spike day
    addon_idx = spike_day * 24 + addon_hour
    close_idx = addon_idx + 2
    _scripted_engine(monkeypatch, {
        entry_idx: "BASE_LONG",
        addon_idx: "HIGH_RISK_ADD_LONG",
        close_idx: "BASE_SHORT",
    })
    trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        d1_exhaustion_filter_enabled=filter_enabled,
    )
    closes = [t for t in trades if t["exit_reason"] == "TP_AND_FLIP"]
    assert len(closes) == 1, "the scripted BASE_SHORT must close the position exactly once"
    return closes[0]


def test_d1_exhaustion_filter_blocks_addon_once_day_range_crosses_threshold(monkeypatch):
    close = _d1_exhaustion_scenario(monkeypatch, addon_hour=5, filter_enabled=True)  # exhausted hour
    assert close["addon_applied"] is False
    assert close["weight"] == pytest.approx(1.0, abs=0.01)  # no addon notional at all


def test_d1_exhaustion_filter_disabled_lets_the_same_addon_through(monkeypatch):
    """Same exact geometry (overextended day, same hour) as the test above — the
    ONLY difference is `d1_exhaustion_filter_enabled=False` (the default) — proves
    the block above is caused by the new filter, not some incidental guard."""
    close = _d1_exhaustion_scenario(monkeypatch, addon_hour=5, filter_enabled=False)
    assert close["addon_applied"] is True
    assert close["weight"] == pytest.approx(1.0 + be.ADDON_ALLOCATION / be.BASE_ALLOCATION, abs=0.01)


def test_d1_exhaustion_filter_does_not_block_addon_before_threshold_is_crossed(monkeypatch):
    """Same day, same filter ON, but the addon fires at hour 2 — BEFORE the spike
    at hour 5 pushes the running range over threshold. Confirms this is a causal,
    intraday gate (blocks once the day is actually overextended), not a blanket
    per-calendar-day ban applied retroactively to hours that preceded the spike."""
    close = _d1_exhaustion_scenario(monkeypatch, addon_hour=2, filter_enabled=True)
    assert close["addon_applied"] is True


def test_d1_exhaustion_filter_never_blocks_base_entries(monkeypatch):
    """The filter is scoped to HIGH_RISK_ADD only (per task.md: 'blokada puli 30%
    budżetu na High Risk Add', not the base position) — a fresh BASE_LONG on the
    overextended day itself must still open normally."""
    params = IndicatorParams()
    spike_day = 20
    candles = _daily_epoch_candles(n_days=22, spike_day=spike_day, spike_hour=5)
    entry_idx = spike_day * 24 + 10  # well inside the exhausted window (flagged from hour 5)
    close_idx = entry_idx + 2
    _scripted_engine(monkeypatch, {entry_idx: "BASE_LONG", close_idx: "BASE_SHORT"})
    trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        d1_exhaustion_filter_enabled=True,
    )
    closes = [t for t in trades if t["exit_reason"] == "TP_AND_FLIP"]
    assert len(closes) == 1
    assert closes[0]["weight"] == pytest.approx(1.0, abs=0.01)  # a normal, full-size BASE entry opened


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
def test_p1_addon_sl_gate_analysis(monkeypatch):
    """Audit P1 (2026-09-08): required gate for implementing V1 or V2 in production
    — V1 OR V2 must beat V0 on BOTH R:R and total_pnl (NET) in >= 5/6 windows
    (BTC/ETH x H1/H2/full).

    CORRECTED 2026-09-10 after independent review (2nd agent) found the first
    version of this test used adaptive_vr_high=1.0/adaptive_vr_low=0.75 — the exact
    literals audit P7 (§1.5) calls "stale, already reset in production" (production
    runs 1.6/0.8). Under those stale thresholds addon volume was much lower in
    several windows (e.g. addon-affected STOP_LOSS trades were 0 in BTC_H1/ETH_H2),
    understating how much the SL variant could possibly matter there.

    Recomputed under PRODUCTION thresholds (adaptive_vr_high=1.6, adaptive_vr_low=0.8,
    confirmed live in system_settings) — real numbers, 2026-09-10, every one of the
    6 windows now has real addon activity (12 to 56 addon fills each):
        strict gate (BOTH R:R and total_pnl beat V0): V1 = 3/6, V2 = 3/6
        total_pnl ALONE (no R:R requirement):         V1 = 5/6 (never worse than V0),
                                                        V2 = 4/6

    Neither variant clears the strict 5/6 gate, so per the audit's own fallback
    ("poniżej — zostaje V0...") V0 stays in production for now. But this is NOT a
    clean rejection of the hypothesis: V1's total_pnl is at least as good as V0's in
    every one of the 6 windows (strictly better in 5, tied in ETH_H2) — it fails the
    STRICT gate only because R:R softens in 2 of the 6 windows (BTC_H1: 1.3235 ->
    1.1818, a real ~10.7% drop, not "a few percent"; ETH_H1: 1.3121 -> 1.2919, ~1.5%).
    A future reader should NOT read "V0 wins" as "V1/V2 are worse" — read it as "the
    combined R:R+PnL gate is a high bar that a directionally-positive PnL signal
    doesn't clear alone".

    CORRECTED AGAIN 2026-09-10 (same review round, verification pass caught a second
    error in THIS docstring): ETH_H2's tie is NOT because that window "has zero
    addon fills" — it has 16, same order of magnitude as the other windows. The real
    reason V0/V1/V2 are bit-for-bit identical there is that none of those 16
    addon-affected positions' actual exits were sensitive to which composite SL
    threshold was used in that window's specific price action — addon activity
    existed, the compared mechanism just never ended up mattering. Do not conflate
    "no addon activity" with "addon activity that happened not to matter" again.

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
    a separate, larger piece of work. (Also note: the stale 1.0/0.75 VR literals this
    docstring flags remain in several OTHER tests in this same file — fixing this one
    test does not fix those; out of scope here, but do not read this test's fix as
    having closed that class of problem file-wide.)

    This test locks in the EXACT gate numbers (3/6, 3/6, and the 5/6 pnl-only signal)
    as a regression guard: if any of them changes, a real change to run_backtest's
    addon/SL/signal logic moved the analysis's outcome — that IS grounds to revisit
    the P1 production decision, not something to silently absorb by loosening the
    assertion back to "still under 5". It does NOT lock in "V0 is confirmed
    correct" — that conclusion was withdrawn along with the stale-VR-params bug this
    docstring documents.

    E3 NOTE (2026-10-12, #112): this analysis isolates the addon-SL mechanism, so it runs with
    the SCOUT clean-close exit (`SCOUT_EXIT_K`) switched OFF - i.e. under the SCOUT semantics
    it was measured under. With that exit on, SCOUT now expires inside the backtest, position
    sizing changes and the frozen numbers move (observed: V1 strict-gate wins 2/6 instead of
    3/6); neither variant comes near the 5/6 gate either way, so the P1 production decision
    (V0 stays) is unaffected. Re-running this analysis WITH the E3 exit is a separate piece of
    work, not something to absorb by editing the numbers below.
    """
    # Keep the historical baseline: no SCOUT_MODE expiry by K clean closes (module global read
    # at call time by MMS4StateManager._apply_scout_exit_rule).
    monkeypatch.setattr("mms4tk.state_manager.SCOUT_EXIT_K", 10**9)
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

    # Sanity: under production VR every one of the 6 windows has real addon
    # activity (measured 2026-09-10: 12-56 fills each) — if this drops, the analysis
    # is silently measuring less than it claims to again (the stale-VR-params bug
    # this test was corrected for hid exactly this).
    assert any_addon_activity == 6, (
        f"only {any_addon_activity}/6 windows had any addon activity — the analysis "
        "may be running under a VR configuration that starves HIGH_RISK_ADD signals"
    )

    # Exact lock-in, not "< 5" — a change from 3/6 to e.g. 4/6 would silently pass a
    # "< 5" check while still being a real, worth-noticing shift in the analysis.
    assert v1_strict_wins == 3, f"V1's strict-gate win count changed ({v1_strict_wins}/6, was 3/6) — re-read this test's docstring"
    assert v2_strict_wins == 3, f"V2's strict-gate win count changed ({v2_strict_wins}/6, was 3/6) — re-read this test's docstring"
    # Locks in the softer PnL-only signal too, so it stays visible rather than
    # silently regressing without anyone noticing — this is documentation, not a
    # deployment gate.
    assert v1_pnl_wins == 5, f"V1's total_pnl-only signal changed ({v1_pnl_wins}/6, was 5/6) — re-read this test's docstring"
    assert v2_pnl_wins == 4, f"V2's total_pnl-only signal changed ({v2_pnl_wins}/6, was 4/6) — re-read this test's docstring"


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


# ── Faza 4 candidate #2: D1 Range Target (task.md) ──
# `D1_RANGE_LOOKBACK_DAYS` is fixed a priori (=4, from the Mastermind document) —
# not exercised as a variable here, exactly per the candidate's own spec (only
# `k` is grid-searched, never the lookback).

def _variable_range_daily_candles(n_days, start_ts=1_700_000_000_000):
    """`n_days` of flat-close (100.0) hourly candles whose DAILY high/low range
    grows by 2 each day (day d: high=100+(d+1), low=100-(d+1) => range=2*(d+1)),
    so `_compute_d1_range_ref_series`'s rolling max-high/min-low window can be
    hand-verified against a known day-by-day sequence."""
    start_ts -= start_ts % DAY_MS
    candles = []
    ts = start_ts
    for d in range(n_days):
        hi, lo = 100.0 + (d + 1), 100.0 - (d + 1)
        for _h in range(24):
            candles.append(Candle(timestamp=ts, open=100.0, high=hi, low=lo, close=100.0, volume=1.0))
            ts += 3_600_000
    return candles


def test_compute_d1_range_ref_series_is_causal_rolling_window():
    candles = _variable_range_daily_candles(n_days=6)
    refs = be._compute_d1_range_ref_series(candles, lookback_days=4)
    assert len(refs) == len(candles)
    assert all(r is None for r in refs[:4 * 24])  # first 4 days: not enough prior history
    # day 4 (0-indexed): window = days 0-3 -> high max=104 (day3), low min=96 (day3) -> ref=8
    assert all(r == pytest.approx(8.0) for r in refs[4 * 24:5 * 24])
    # day 5: window = days 1-4 -> high max=105 (day4), low min=95 (day4) -> ref=10
    assert all(r == pytest.approx(10.0) for r in refs[5 * 24:6 * 24])


def test_compute_d1_range_ref_series_empty_input():
    assert be._compute_d1_range_ref_series([]) == []


def test_d1_range_target_disabled_by_default_produces_no_target_exits():
    candles = _daily_epoch_candles(n_days=10)
    params = IndicatorParams()
    trades = be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                              trend_filter_enabled=False)
    assert not any(t["exit_reason"] == "D1_RANGE_TP" for t in trades)


def test_d1_range_target_closes_position_at_frozen_entry_time_target(monkeypatch):
    """A LONG position gets a hard TP at `base_entry_price + k*D1_range_ref`,
    frozen from the D1_range_ref as of the ENTRY candle. Quiet flat days
    (`_daily_epoch_candles`, no spike) give a constant D1_range_ref=1.0 (each
    day's own high 100.5 - low 99.5); k=0.5 places the target exactly at the
    quiet candles' own high (100.5), so it fires deterministically on the very
    next candle after entry, without needing a scripted price spike."""
    params = IndicatorParams()
    candles = _daily_epoch_candles(n_days=10)
    entry_idx = 5 * 24  # day 5: 5 fully-closed prior days exist (>=4 required)
    _scripted_engine(monkeypatch, {entry_idx: "BASE_LONG"})
    trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        d1_range_target_enabled=True, d1_range_target_k=0.5,
    )
    target_closes = [t for t in trades if t["exit_reason"] == "D1_RANGE_TP"]
    assert len(target_closes) == 1
    assert target_closes[0]["exit_price"] == pytest.approx(100.5)  # 100.0 + 0.5*1.0
    assert target_closes[0]["entry_idx"] == entry_idx
    assert target_closes[0]["exit_idx"] == entry_idx + 1


def test_d1_range_target_absent_before_lookback_days_available(monkeypatch):
    """An entry too early (fewer than `D1_RANGE_LOOKBACK_DAYS` fully-closed prior
    days) must get NO target at all — never blocks the entry, never fires a
    premature D1_RANGE_TP; the position survives to whatever closes it normally."""
    params = IndicatorParams()
    candles = _daily_epoch_candles(n_days=10)
    entry_idx = 2 * 24  # day 2: only 2 fully-closed prior days exist, need 4
    close_idx = entry_idx + 5
    _scripted_engine(monkeypatch, {entry_idx: "BASE_LONG", close_idx: "BASE_SHORT"})
    trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        d1_range_target_enabled=True, d1_range_target_k=0.5,
    )
    assert not any(t["exit_reason"] == "D1_RANGE_TP" for t in trades)
    assert len([t for t in trades if t["exit_reason"] == "TP_AND_FLIP"]) == 1


# ── Faza 4 candidate #1: Protected Stage-1 (Midline TP remainder protection) ──

def test_run_backtest_rejects_unknown_midline_tp_remainder_mode():
    candles = _flat_candles(50)
    params = IndicatorParams()
    with pytest.raises(ValueError):
        be.run_backtest("BTC/USDC", candles, params, stale_candles_threshold=12,
                         trend_filter_enabled=False, midline_tp_remainder_mode="BOGUS")


def _midline_remainder_scenario(monkeypatch, remainder_mode):
    """Flat candles @ 100.0 (TMA converges to 100.0 once warmed up): a scripted
    BASE_LONG entry touches the midline on the very next candle (entry <= tma_mid
    and high >= tma_mid both hold trivially at a flat 100.0), booking a 50%
    partial with pnl=0 at the midline itself — isolates the REMAINDER-protection
    mechanism under test from any partial-booking PnL noise."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10, price=100.0)
    _scripted_engine(monkeypatch, {warmup: "BASE_LONG"})
    return be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        midline_tp_enabled=True, midline_tp_fraction=0.5, midline_tp_remainder_mode=remainder_mode,
    )


def test_midline_remainder_none_keeps_original_2pct_sl_unchanged(monkeypatch):
    """Variant (1), the control: default "NONE" must reproduce today's shipped
    behavior bit-for-bit — the remainder's SL stays at the original 2%, so the
    quiet candles' own -0.5% wick (99.5, well inside 98.0) never stops it out."""
    trades = _midline_remainder_scenario(monkeypatch, "NONE")
    assert any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)
    assert not any(t["exit_reason"] == "STOP_LOSS" for t in trades)


def test_midline_remainder_breakeven_tightens_sl_after_partial(monkeypatch):
    """Variant (2): after the partial, the remainder's SL moves to
    `base_entry_price` + a round-trip-fee buffer — MUCH tighter than the
    original 2% SL, so the SAME quiet -0.5% wick (99.5) that variant (1) ignores
    now stops the remainder out, at a small NET-OF-FEE breakeven, not a loss."""
    trades = _midline_remainder_scenario(monkeypatch, "BREAKEVEN")
    assert any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)
    sl_trades = [t for t in trades if t["exit_reason"] == "STOP_LOSS"]
    assert len(sl_trades) == 1
    assert sl_trades[0]["exit_price"] == pytest.approx(100.0 * (1 + be.ROUND_TRIP_FEE_PCT), rel=1e-6)
    assert sl_trades[0]["pnl_pct"] > 0  # a small gross gain, not a fresh loss


def test_midline_remainder_invalidation_closes_on_close_back_across_midline(monkeypatch):
    """Variant (3): the candle right after the partial closes back BELOW the
    frozen midline reference (100.0) -- the remainder must invalidate at that
    close, even though it never reaches the (much looser) original 2% SL."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10, price=100.0)
    candles[warmup + 2] = Candle(timestamp=warmup + 2, open=99.5, high=100.0, low=98.5, close=99.0, volume=1.0)
    _scripted_engine(monkeypatch, {warmup: "BASE_LONG"})
    trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        midline_tp_enabled=True, midline_tp_fraction=0.5, midline_tp_remainder_mode="INVALIDATION",
    )
    assert any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)
    inv = [t for t in trades if t["exit_reason"] == "MIDLINE_INVALIDATION"]
    assert len(inv) == 1
    assert inv[0]["exit_price"] == pytest.approx(99.0)


def test_midline_remainder_invalidation_does_not_fire_while_price_holds_above_midline(monkeypatch):
    """Same mode, but price never actually closes back below the frozen
    reference -- the remainder must NOT be invalidated just because the mode is
    armed; it needs the actual close-back-across-midline event."""
    trades = _midline_remainder_scenario(monkeypatch, "INVALIDATION")
    assert any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)
    assert not any(t["exit_reason"] == "MIDLINE_INVALIDATION" for t in trades)


def test_midline_full_exit_closes_entire_position_no_partial(monkeypatch):
    """Variant (4): 100% exit at the midline -- a full, non-partial close, no
    MIDLINE_PARTIAL_TP booked at all."""
    params = IndicatorParams()
    warmup = max(params.tma_length, params.atr_period) + 5 + 2
    candles = _flat_candles(warmup + 10, price=100.0)
    _scripted_engine(monkeypatch, {warmup: "BASE_LONG"})
    trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=1000, trend_filter_enabled=False,
        midline_tp_enabled=True, midline_tp_full_exit=True,
    )
    full_exits = [t for t in trades if t["exit_reason"] == "MIDLINE_FULL_TP"]
    assert len(full_exits) == 1
    assert not full_exits[0].get("partial")
    assert full_exits[0]["weight"] == pytest.approx(1.0, abs=0.01)
    assert not any(t["exit_reason"] == "MIDLINE_PARTIAL_TP" for t in trades)

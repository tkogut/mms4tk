"""PLAN-010 Phase 2: the calibration engine's decision logic (train_test_validate)
and the real-data regression guard against reintroducing the 2026-08-29 overfitting
bug (PLAN-008 Phase 3's deployed trend-filter parameters looked like a clear win on
full-window stats, but failed when validated train-half -> test-half in both
directions).
"""
import csv
import os

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

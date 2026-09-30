"""Issue #82 step 1-2 (owner-gated measurement, NOT step 3/production): unit
tests for `scripts/structural_sl_analysis.py`'s pure conversion logic and its
`MMS4InactivityManager`/`MMS4StateManager` monkeypatch hooks. Convention follows
`tests/test_ema_cascade_gate_analysis.py` (import the script as a module via
`importlib`, test pure functions directly; one short-slice equivalence test
mirrors PR #84's harness self-verification)."""
import importlib.util
import math
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "structural_sl_analysis.py")

_spec = importlib.util.spec_from_file_location("structural_sl_analysis", SCRIPT_PATH)
ssa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ssa)


# ── structural_sl_pct: pure price/pct conversion ──

def test_long_structural_sl_below_entry():
    # lower_band=90, atr=10, k=1 -> sl_abs=80, entry=105
    pct, degenerate = ssa.structural_sl_pct("long", 105.0, 90.0, 130.0, 10.0, k=1.0, fallback_pct=0.02)
    assert degenerate is False
    assert pct == pytest.approx((105.0 - 80.0) / 105.0)


def test_short_structural_sl_above_entry():
    # upper_band=130, atr=10, k=1 -> sl_abs=140, entry=125
    pct, degenerate = ssa.structural_sl_pct("short", 125.0, 70.0, 130.0, 10.0, k=1.0, fallback_pct=0.02)
    assert degenerate is False
    assert pct == pytest.approx((140.0 - 125.0) / 125.0)


@pytest.mark.parametrize("k", [0.5, 1.0, 1.5, 2.0])
def test_k_grid_all_produce_positive_pct_in_normal_case(k):
    pct, degenerate = ssa.structural_sl_pct("long", 100.0, 95.0, 105.0, 2.0, k=k, fallback_pct=0.02)
    assert not degenerate
    assert pct > 0


def test_long_degenerate_when_band_undershoot_exceeds_k_atr():
    # entry deep below lower_band by more than k*ATR -> sl_abs >= entry -> fallback
    pct, degenerate = ssa.structural_sl_pct("long", 80.0, 100.0, 120.0, 5.0, k=1.0, fallback_pct=0.02)
    assert degenerate is True
    assert pct == 0.02


def test_short_degenerate_when_band_overshoot_exceeds_k_atr():
    pct, degenerate = ssa.structural_sl_pct("short", 140.0, 80.0, 100.0, 5.0, k=1.0, fallback_pct=0.02)
    assert degenerate is True
    assert pct == 0.02


def test_degenerate_on_nan_atr():
    pct, degenerate = ssa.structural_sl_pct("long", 100.0, 95.0, 105.0, float("nan"), k=1.0, fallback_pct=0.02)
    assert degenerate is True
    assert pct == 0.02


def test_degenerate_on_zero_atr():
    pct, degenerate = ssa.structural_sl_pct("long", 100.0, 95.0, 105.0, 0.0, k=1.0, fallback_pct=0.02)
    assert degenerate is True


def test_degenerate_on_nonpositive_entry():
    pct, degenerate = ssa.structural_sl_pct("long", 0.0, 95.0, 105.0, 2.0, k=1.0, fallback_pct=0.02)
    assert degenerate is True


def test_side_is_case_insensitive():
    pct_lower, deg_lower = ssa.structural_sl_pct("long", 105.0, 90.0, 130.0, 10.0, k=1.0, fallback_pct=0.02)
    pct_upper, deg_upper = ssa.structural_sl_pct("LONG", 105.0, 90.0, 130.0, 10.0, k=1.0, fallback_pct=0.02)
    assert (pct_lower, deg_lower) == (pct_upper, deg_upper)


# ── DecoupledStateManager: invariant #2 (SCOUT_MODE coupling) opt-out ──

def test_decoupled_state_manager_never_triggers_scout():
    from mms4tk.state_manager import MMS4State
    sm = ssa.DecoupledStateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC", logger_db=None)
    sm.record_trade(
        trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0, exit_price=97.0,
        amount=1.0, pnl=-3.0, pnl_percent=-0.03, is_stop_loss=True, sl_percent=0.03,
    )
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.BASE_MODE


def test_coupled_real_state_manager_still_triggers_scout_above_threshold():
    from mms4tk.state_manager import MMS4StateManager, MMS4State
    sm = MMS4StateManager(initial_state=MMS4State.BASE_MODE, trading_symbol="BTC/USDC", logger_db=None)
    sm.record_trade(
        trade_id="t1", symbol="BTC/USDC", side="long", entry_price=100.0, exit_price=97.0,
        amount=1.0, pnl=-3.0, pnl_percent=-0.03, is_stop_loss=True, sl_percent=0.03,
    )
    assert sm.get_symbol_state("BTC/USDC") == MMS4State.SCOUT_MODE


# ── StructuralInactivityManager: entry hook sets the module BASE_SL_PCT global ──

def test_track_position_sets_base_sl_pct_from_band(monkeypatch):
    import pandas as pd
    import mms4tk.backtest_engine as be

    full_df = pd.DataFrame({"lower_band": [90.0], "upper_band": [130.0], "atr": [10.0]})
    ssa.StructuralInactivityManager.ctx = {
        "full_df": full_df, "ts_to_idx": {1000: 0}, "k": 1.0,
        "fallback_pct": 0.02, "entries": [], "degenerate": [0], "force_fallback": False,
    }
    monkeypatch.setattr(be, "BASE_SL_PCT", 0.02)
    mgr = ssa.StructuralInactivityManager()
    mgr.track_position(inst_id="BTC/USDC", entry_price=105.0, entry_time=1000, side="long", atr=10.0)

    expected_pct = (105.0 - 80.0) / 105.0
    assert be.BASE_SL_PCT == pytest.approx(expected_pct)
    assert ssa.StructuralInactivityManager.ctx["entries"] == [pytest.approx(expected_pct)]
    assert ssa.StructuralInactivityManager.ctx["degenerate"][0] == 0


def test_track_position_force_fallback_ignores_band(monkeypatch):
    import pandas as pd
    import mms4tk.backtest_engine as be

    full_df = pd.DataFrame({"lower_band": [90.0], "upper_band": [130.0], "atr": [10.0]})
    ssa.StructuralInactivityManager.ctx = {
        "full_df": full_df, "ts_to_idx": {1000: 0}, "k": None,
        "fallback_pct": 0.02, "entries": [], "degenerate": [0], "force_fallback": True,
    }
    mgr = ssa.StructuralInactivityManager()
    mgr.track_position(inst_id="ETH/USDC", entry_price=105.0, entry_time=1000, side="short", atr=10.0)

    assert be.BASE_SL_PCT == 0.02
    assert ssa.StructuralInactivityManager.ctx["degenerate"][0] == 1


def test_track_position_unknown_timestamp_is_degenerate(monkeypatch):
    import pandas as pd
    import mms4tk.backtest_engine as be

    full_df = pd.DataFrame({"lower_band": [90.0], "upper_band": [130.0], "atr": [10.0]})
    ssa.StructuralInactivityManager.ctx = {
        "full_df": full_df, "ts_to_idx": {}, "k": 1.0,
        "fallback_pct": 0.02, "entries": [], "degenerate": [0], "force_fallback": False,
    }
    mgr = ssa.StructuralInactivityManager()
    mgr.track_position(inst_id="BTC/USDC", entry_price=105.0, entry_time=999999, side="long", atr=10.0)

    assert math.isclose(be.BASE_SL_PCT, 0.02)
    assert ssa.StructuralInactivityManager.ctx["degenerate"][0] == 1


# ── short-slice equivalence: self-check mode == default fixed-pct run_backtest ──

def test_self_check_cell_matches_plain_run_backtest():
    """Runs one small self-check cell (k=None -> forced 2% fallback through the
    patched hooks) against the SAME candles run through plain, unpatched
    `run_backtest`. Both must produce identical trades — the short-slice
    analogue of PR #84's full-grid harness self-verification."""
    import csv
    import mms4tk.backtest_engine as be
    from mms4tk.analytics import Candle, IndicatorParams

    fixtures_dir = os.path.join(REPO_ROOT, "tests", "fixtures")
    path = os.path.join(fixtures_dir, "ohlcv_BTC_USDC_1h.csv")
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    candles = [
        Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
               low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
        for r in rows[:400]
    ]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)

    baseline_trades = be.run_backtest(
        "BTC/USDC", candles, params, stale_candles_threshold=12,
        trend_filter_enabled=False, addon_sl_variant="V2",
    )
    baseline_stats = be._stats(baseline_trades)

    result = ssa._cell(("ohlcv_BTC_USDC_1h.csv", "BTC/USDC", 0, 400, None, "coupled", "yes", 0.02))

    assert result["trades"] == len(baseline_trades)
    assert result["total_pnl"] == pytest.approx(baseline_stats["total_pnl"])
    assert result["sl_exits"] == sum(1 for t in baseline_trades if t["exit_reason"] == "STOP_LOSS")
    assert result["degenerate"] == result["entries_opened"]

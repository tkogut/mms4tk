"""Phase 3.3: smoke tests for scripts/rolling_tma_atr_recalibration.py.

Deliberately NOT a full 610-day / 5-window run (task.md asks for the small-slice
smoke test only -- the real 610d sweep takes minutes, far too slow for the default
`-m "not slow"` CI pass). These tests exercise the script's pure functions directly,
without `ProcessPoolExecutor`, on a small slice of a real fixture, and check that the
returned shapes/types/invariants are sane -- not that any particular number comes out
of it.
"""

import importlib.util
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "rolling_tma_atr_recalibration.py")

_spec = importlib.util.spec_from_file_location("rolling_tma_atr_recalibration", SCRIPT_PATH)
rolling = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rolling)


@pytest.fixture(scope="module")
def small_candles():
    """~4 days of real BTC/USDC candles -- enough to clear the largest grid cell's
    warmup (tma_length=50, atr_period=21 -> warmup=57) with headroom for a signal or
    two, small enough that 9 grid backtests finish in well under a second."""
    candles = rolling.load_fixture("ohlcv_BTC_USDC_1h.csv")
    return candles[:100]


def test_grid_constants_match_task_spec():
    # The literal 3x3 grid the task specified -- not a wider/tuned search.
    assert rolling.TMA_GRID == [20, 34, 50]
    assert rolling.ATR_PERIOD_GRID == [10, 14, 21]
    assert rolling.STATIC_DEFAULT == (20, 14)


def test_build_subwindow_jobs_shapes(small_candles):
    jobs = rolling.build_subwindow_jobs(
        small_candles, "BTC/USDC", calib_days_list=[1], test_window_days=3, max_test_windows=1,
    )
    # 3-day test window (72 candles) sliced into 1-day (24 candle) sub-windows -> 3 jobs.
    assert len(jobs) == 3
    for symbol, sub_candles, calib_days, test_window_idx, sub_idx in jobs:
        assert symbol == "BTC/USDC"
        assert len(sub_candles) == 24
        assert calib_days == 1
        assert test_window_idx == 0
    assert [j[4] for j in jobs] == [0, 1, 2]  # sub-window index increases in order


def test_build_subwindow_jobs_respects_max_test_windows(small_candles):
    # 100 candles / (3d = 72 candles) = 1 test window available; asking for more
    # than exist must not fabricate one, and capping below what's available works.
    jobs_uncapped = rolling.build_subwindow_jobs(
        small_candles, "BTC/USDC", calib_days_list=[1], test_window_days=3, max_test_windows=None,
    )
    assert {j[3] for j in jobs_uncapped} == {0}

    jobs_capped = rolling.build_subwindow_jobs(
        small_candles, "BTC/USDC", calib_days_list=[1], test_window_days=1, max_test_windows=2,
    )
    assert {j[3] for j in jobs_capped} == {0, 1}


def test_calibrate_subwindow_returns_sane_shape(small_candles):
    sub = small_candles[:72]  # clears warmup (57) with room to spare
    job = ("BTC/USDC", sub, 3, 0, 0)
    row = rolling._calibrate_subwindow(job)

    assert row["symbol"] == "BTC/USDC"
    assert row["calib_days"] == 3
    assert row["test_window"] == 0
    assert row["sub_window"] == 0
    assert row["chosen_tma_length"] in rolling.TMA_GRID
    assert row["chosen_atr_period"] in rolling.ATR_PERIOD_GRID
    assert isinstance(row["rolling_total_pnl"], float)
    assert isinstance(row["static_total_pnl"], float)
    # The rolling (best-of-grid) pick can never do WORSE than the static (20,14)
    # cell on the SAME window -- it is chosen by argmax over a set that includes
    # that exact cell.
    assert row["rolling_total_pnl"] >= row["static_total_pnl"] - 1e-12
    assert row["rolling_trades"] >= 0
    assert row["static_trades"] >= 0
    # start/end are ISO date strings bracketing the sub-window
    assert row["start"] <= row["end"]


def test_calibrate_subwindow_static_cell_matches_direct_default_run(small_candles):
    """The `static_total_pnl` reported alongside the rolling pick must be exactly
    the (20, 14) grid cell -- today's production IndicatorParams() default -- not
    some other baseline."""
    sub = small_candles[:72]
    row = rolling._calibrate_subwindow(("BTC/USDC", sub, 3, 0, 0))
    direct = rolling._run_one("BTC/USDC", sub, 20, 14)
    assert row["static_total_pnl"] == pytest.approx(direct["total_pnl"])
    assert row["static_trades"] == direct["trades"]


def test_stability_all_same_combo_has_zero_switch_rate():
    rows = [
        {"test_window": 0, "sub_window": i, "chosen_tma_length": 20, "chosen_atr_period": 14}
        for i in range(5)
    ]
    stab = rolling._stability(rows)
    assert stab["n_windows"] == 5
    assert stab["mode_combo"] == (20, 14)
    assert stab["mode_share"] == 1.0
    assert stab["switch_rate"] == 0.0


def test_stability_alternating_combo_has_full_switch_rate():
    rows = [
        {"test_window": 0, "sub_window": i, "chosen_tma_length": 20 if i % 2 == 0 else 50,
         "chosen_atr_period": 14}
        for i in range(4)
    ]
    stab = rolling._stability(rows)
    assert stab["switch_rate"] == 1.0  # every consecutive pair differs
    assert stab["mode_share"] == 0.5   # tied 2/2 between the two combos


def test_stability_orders_by_test_window_then_sub_window_not_input_order():
    # Deliberately out of chronological order in the input list.
    rows = [
        {"test_window": 1, "sub_window": 0, "chosen_tma_length": 50, "chosen_atr_period": 21},
        {"test_window": 0, "sub_window": 1, "chosen_tma_length": 20, "chosen_atr_period": 14},
        {"test_window": 0, "sub_window": 0, "chosen_tma_length": 20, "chosen_atr_period": 14},
    ]
    stab = rolling._stability(rows)
    # Chronological sequence is (0,0)=20/14 -> (0,1)=20/14 -> (1,0)=50/21: one switch.
    assert stab["switch_rate"] == pytest.approx(0.5)


def test_symbol_for_infers_eth_vs_btc():
    assert rolling.symbol_for("ohlcv_ETH_USDT_1h_swap_24m.csv") == "ETH/USDC"
    assert rolling.symbol_for("ohlcv_BTC_USDT_1h_swap_24m.csv") == "BTC/USDC"


def test_continuous_baseline_shape(small_candles):
    per_window_rows, full_row = rolling.continuous_baseline(
        small_candles, "BTC/USDC", test_window_days=1, max_test_windows=3,
    )
    assert len(per_window_rows) == 3
    for i, row in enumerate(per_window_rows):
        assert row["test_window"] == i
        assert row["symbol"] == "BTC/USDC"
        assert isinstance(row["total_pnl"], float)
    assert full_row["symbol"] == "BTC/USDC"
    assert full_row["candles"] == 3 * 24
    assert isinstance(full_row["total_pnl"], float)


def test_main_runs_end_to_end_on_small_slice(tmp_path):
    """Full CLI entry point, small slice, real subprocess (so `ProcessPoolExecutor`
    behaves exactly as it does for a real invocation -- calling `main()` in-process
    under pytest breaks worker pickling because the module is loaded here via a
    synthetic `importlib` spec, not its real `scripts.` import path) -- not the
    610-day headline sweep, just a shape check that the CLI does not blow up and
    prints the expected sections."""
    import subprocess
    import sys

    csv_out = tmp_path / "raw.csv"
    env = dict(os.environ, PYTHONPATH=os.path.join(REPO_ROOT, "src"))
    result = subprocess.run(
        [sys.executable, SCRIPT_PATH,
         "--fixtures", "ohlcv_BTC_USDC_1h.csv",
         "--calib-days", "1",
         "--test-window-days", "3",
         "--max-test-windows", "1",
         "--workers", "1",
         "--csv-out", str(csv_out)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "calib_days=1" in result.stdout
    assert "stability:" in result.stdout
    assert csv_out.exists()
    content = csv_out.read_text()
    assert "chosen_tma_length" in content.splitlines()[0]

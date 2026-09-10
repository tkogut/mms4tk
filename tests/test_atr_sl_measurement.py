"""Audit P4 step 0 (2026-09-08): measurement, not a code change. Tests whether the
audit's illustrative hypothesis for the ETH problem — that the fixed 2% price-relative
stop-loss represents a materially TIGHTER stop in ATR-normalized terms for ETH/USDC
than for BTC/USDC (because ETH's ATR is a larger fraction of its price) — actually
holds on real data, using the SAME TMAIndicator ATR calculation and IndicatorParams
defaults (tma_length=20, atr_period=14, atr_method from MMS4_ATR_METHOD env var,
default "sma") production/the backtest engine already use.

This is a real measurement (computed 2026-09-10 on the 90-day 1h fixtures — the same
BTC/ETH data used throughout this audit), not the audit's own illustrative numbers
(ATR≈0.6%/1.0%, "~40% tighter") — those were explicitly flagged in the audit as
"ilustracyjne — to hipoteza, nie ustalenie", not measured.

Per the audit's own stop condition ("Jeśli różnica BTC vs ETH okaże się mała,
hipoteza upada i tu kończymy"): the measured difference is real and substantial
(~26-27% tighter for ETH in ATR units, both mean and median), so step 0 does NOT
kill the hypothesis — but this test locks in ONLY the measurement, not a verdict on
whether to act on it. Proceeding to the audit's variant #2 (per-symbol base_sl_pct
grid, full 2-way OOS) or variant #3 (SL = k*ATR, changes doctrine) is a SEPARATE,
larger decision this test does not make.
"""
import csv
import os

import numpy as np
import pytest

from mms4tk.analytics import Candle, IndicatorParams, TMAIndicator

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
BASE_SL_PCT = 0.02  # KB 1.4 / mastermind_weekly_tactics.md §1: "niezmiennie 2.0%"


def load_fixture_candles(name):
    candles = []
    with open(os.path.join(FIXTURES, name)) as f:
        for row in csv.DictReader(f):
            candles.append(Candle(
                timestamp=int(row["timestamp"]), open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]),
            ))
    return candles


def _atr_pct_and_sl_over_atr(fname: str):
    """Returns (atr_pct_array, sl_distance_over_atr_array) over every candle with a
    valid (post-warmup) ATR value, using production's own TMAIndicator/defaults."""
    candles = load_fixture_candles(fname)
    df = TMAIndicator.calculate(candles, IndicatorParams())
    assert len(df) == len(candles)  # one row per input candle, NaN during warmup only

    closes = np.array([c.close for c in candles])
    atr = df["atr"].to_numpy()
    valid = ~np.isnan(atr)
    closes, atr = closes[valid], atr[valid]

    atr_pct = atr / closes
    sl_over_atr = (closes * BASE_SL_PCT) / atr
    return atr_pct, sl_over_atr


def test_eth_stop_is_measurably_tighter_than_btc_in_atr_units():
    """Real measurement, 90-day 1h fixtures, 2026-09-10:
        BTC/USDC: ATR/price median=0.498% (mean=0.556%) -> SL/ATR median=4.017 (mean=4.888)
        ETH/USDC: ATR/price median=0.672% (mean=0.735%) -> SL/ATR median=2.975 (mean=3.567)
    ETH's SL/ATR ratio is ~26% tighter than BTC's on both mean and median — a real,
    substantial, direction-consistent difference (audit's illustrative guess was a
    steeper ~40%, but "small" would have been anything under roughly 10-15%; this
    is neither zero nor illustrative-guess-sized, it is its own real number)."""
    btc_atr_pct, btc_sl_over_atr = _atr_pct_and_sl_over_atr("ohlcv_BTC_USDC_1h.csv")
    eth_atr_pct, eth_sl_over_atr = _atr_pct_and_sl_over_atr("ohlcv_ETH_USDC_1h.csv")

    # Direction: ETH's ATR really is a larger fraction of its price than BTC's.
    assert np.median(eth_atr_pct) > np.median(btc_atr_pct)
    assert np.mean(eth_atr_pct) > np.mean(btc_atr_pct)

    # Direction + magnitude: ETH's fixed-2% stop sits at fewer multiples of its own
    # ATR than BTC's does — i.e. it IS the tighter stop in volatility-normalized terms.
    btc_median, eth_median = np.median(btc_sl_over_atr), np.median(eth_sl_over_atr)
    btc_mean, eth_mean = np.mean(btc_sl_over_atr), np.mean(eth_sl_over_atr)
    assert eth_median < btc_median
    assert eth_mean < btc_mean

    # Magnitude gate matching the audit's own stop condition: this must NOT be a
    # "small" difference (audit: "jeśli różnica... okaże się mała, hipoteza upada").
    # 15% relative tightening is a conservative floor for "not small" — comfortably
    # below the ~26-27% actually measured, so this assertion has real margin and
    # won't flip on minor fixture/indicator changes.
    relative_tightening_median = (btc_median - eth_median) / btc_median
    relative_tightening_mean = (btc_mean - eth_mean) / btc_mean
    assert relative_tightening_median > 0.15, (
        f"ETH SL/ATR only {relative_tightening_median:.1%} tighter than BTC's (median) — "
        "if this drops below 15%, the audit's own stop condition applies: the P4 "
        "hypothesis would no longer be clearly supported by this measurement."
    )
    assert relative_tightening_mean > 0.15


def test_measurement_uses_production_defaults_not_a_bespoke_calculation():
    """Guards against silently drifting from what daemon.py/run_backtest actually
    compute: tma_length=20, atr_period=14, atr_method env-driven default 'sma'."""
    defaults = IndicatorParams()
    assert defaults.tma_length == 20
    assert defaults.atr_period == 14
    assert defaults.atr_method in ("sma", "wilder")

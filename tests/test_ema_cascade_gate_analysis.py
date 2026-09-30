"""Issue #83 step 1: unit tests for the EMA 26/50/144/200 cascade classifier in
scripts/ema_cascade_gate_analysis.py (pure functions only -- no backtest runs)."""
import importlib.util
import os

import pytest

from mms4tk.analytics import Candle

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_PATH = os.path.join(REPO_ROOT, "scripts", "ema_cascade_gate_analysis.py")

_spec = importlib.util.spec_from_file_location("ema_cascade_gate_analysis", SCRIPT_PATH)
ema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ema)

H4_ALIGNED_TS = 1_726_012_800_000  # 2024-09-11 00:00 UTC, on a 4h boundary


def _hourly(closes, start=H4_ALIGNED_TS):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle(timestamp=start + i * 3_600_000, open=prev, high=max(prev, c),
                          low=min(prev, c), close=c, volume=1.0))
        prev = c
    return out


# ── classify_ema_regime ──

def test_cascade_ordered_up_is_long_only():
    assert ema.classify_ema_regime(110, 105, 100, 95, 90) == "LONG_ONLY"


def test_cascade_ordered_down_is_short_only():
    assert ema.classify_ema_regime(80, 85, 90, 95, 100) == "SHORT_ONLY"


@pytest.mark.parametrize("vals", [
    (110, 100, 105, 95, 90),   # e26 < e50
    (104, 105, 100, 95, 90),   # close below e26 though EMAs ordered up
    (110, 105, 100, 90, 95),   # e144 < e200
    (100, 100, 100, 100, 100),  # all equal: strict inequality -> no cascade
    (110, 105, 105, 95, 90),   # tie e26 == e50
])
def test_cascade_mixed_or_tied_is_both(vals):
    assert ema.classify_ema_regime(*vals) == "BOTH"


def test_cascade_nan_is_both():
    assert ema.classify_ema_regime(110, 105, 100, 95, float("nan")) == "BOTH"


def test_zone_boundaries_long():
    # cascade holds in all three; only distance from e200=100 differs
    assert ema.classify_ema_regime(109, 108, 107, 106, 100, "cascade_plus_zone") == "BOTH"
    assert ema.classify_ema_regime(111, 108, 107, 106, 100, "cascade_plus_zone") == "LONG_ONLY"
    assert ema.classify_ema_regime(109, 108, 107, 106, 100, "cascade") == "LONG_ONLY"


def test_zone_boundaries_short():
    assert ema.classify_ema_regime(91, 92, 93, 94, 100, "cascade_plus_zone") == "BOTH"
    assert ema.classify_ema_regime(89, 92, 93, 94, 100, "cascade_plus_zone") == "SHORT_ONLY"


def test_zone_requires_cascade_too():
    # far above e200 but cascade broken -> still BOTH
    assert ema.classify_ema_regime(150, 100, 105, 95, 90, "cascade_plus_zone") == "BOTH"


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        ema.classify_ema_regime(1, 1, 1, 1, 1, "zone")


# ── ema_series ──

def test_ema_series_hand_computed_span3():
    # span=3 -> alpha=0.5, seeded with first value
    # 10 ; 0.5*20+0.5*10=15 ; 0.5*10+0.5*15=12.5 ; 0.5*30+0.5*12.5=21.25
    assert ema.ema_series([10, 20, 10, 30], 3) == pytest.approx([10, 15, 12.5, 21.25])


# ── combine_bias ──

@pytest.mark.parametrize("a,b,exp", [
    ("BOTH", "BOTH", "BOTH"), ("LONG_ONLY", "BOTH", "LONG_ONLY"), ("BOTH", "SHORT_ONLY", "SHORT_ONLY"),
    ("LONG_ONLY", "LONG_ONLY", "LONG_ONLY"), ("LONG_ONLY", "SHORT_ONLY", "FLAT"),
    ("SHORT_ONLY", "LONG_ONLY", "FLAT"),
])
def test_combine_bias(a, b, exp):
    assert ema.combine_bias(a, b) == exp


# ── ema_cascade_bias_series: warm-up, lag, causality ──

def test_bias_series_both_during_warmup():
    # steady uptrend but far fewer than EMA_MIN_BARS H4 bars -> BOTH everywhere
    candles = _hourly([100 + i for i in range(4 * 50)])
    bias = ema.ema_cascade_bias_series(candles)
    assert len(bias) == len(candles)
    assert set(bias) == {"BOTH"}


def test_bias_series_uptrend_long_downtrend_short_with_small_min_bars():
    up = ema.ema_cascade_bias_series(_hourly([100 * 1.001 ** i for i in range(4 * 400)]), min_bars=250)
    assert up[-1] == "LONG_ONLY"
    down = ema.ema_cascade_bias_series(_hourly([100 * 0.999 ** i for i in range(4 * 400)]), min_bars=250)
    assert down[-1] == "SHORT_ONLY"


def test_bias_series_one_bucket_lag():
    """Bias of an H1 candle in bucket b must equal the classification of closed H4 bar
    b-1 -- and a sharp move INSIDE bucket b must not affect bucket b's own bias."""
    n_h4 = 300
    closes = [100 * 1.001 ** i for i in range(4 * n_h4)]
    # last H4 bucket crashes hard: must only show up in a later bucket, never itself
    closes[-4:] = [1.0, 1.0, 1.0, 1.0]
    candles = _hourly(closes)
    bias = ema.ema_cascade_bias_series(candles, min_bars=250)
    assert bias[-4:] == ["LONG_ONLY"] * 4  # still reading bar b-1 (pre-crash uptrend)
    # manual reference for bucket b: classify H4 bar b-1
    h4 = [closes[k * 4 + 3] for k in range(n_h4)]
    e = {p: ema.ema_series(h4, p) for p in ema.EMA_PERIODS}
    k = n_h4 - 2
    assert bias[-1] == ema.classify_ema_regime(h4[k], e[26][k], e[50][k], e[144][k], e[200][k])


def test_bias_series_min_bars_threshold_exact():
    """Bucket b gets a real classification iff bar b-1 has index >= min_bars-1."""
    min_bars = 220
    candles = _hourly([100 * 1.001 ** i for i in range(4 * 260)])
    bias = ema.ema_cascade_bias_series(candles, min_bars=min_bars)
    # bucket min_bars-1 reads bar min_bars-2 -> BOTH; bucket min_bars reads bar min_bars-1 -> classified
    assert bias[4 * (min_bars - 1)] == "BOTH"
    assert bias[4 * min_bars] == "LONG_ONLY"


def test_bias_series_no_lookahead_prefix_invariant():
    closes = [100 * (1 + 0.01 * ((i * 37) % 11 - 5) / 5) * 1.0005 ** i for i in range(4 * 320)]
    candles = _hourly(closes)
    full = ema.ema_cascade_bias_series(candles, min_bars=210)
    prefix = ema.ema_cascade_bias_series(candles[:4 * 280 + 2], min_bars=210)
    assert full[:len(prefix)] == prefix

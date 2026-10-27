#!/usr/bin/env python3
"""Issue #83 step 1: EMA 26/50/144/200 cascade as an alternative HTF bias source,
measured against the H4 TMA-band gate that is ALREADY enabled in production.

Research only -- nothing in `src/` is modified. The EMA classifier is injected into
the unmodified production harness (`backtest_engine.run_backtest`) by swapping the
module-level `_htf_directional_bias_series` for the duration of one run (it is looked
up as a module global at call time), then calling `run_backtest(...,
htf_directional_gate_enabled=True)`. Everything downstream -- `sm.strategy_profile.
directional_bias = bias[i]`, `analytics.filter_bias`, the exit/flip/addon logic -- is
production code, untouched.

Methodology copied from `scripts/htf_directional_gate_analysis.py` (Faza 3.2):
consecutive NON-OVERLAPPING `--window-days` windows (default 122d), production
parameters (engine_mode=ADAPTIVE, VR 1.6/0.8, stale_candles_threshold=12, same-interval
trend filter OFF, addon_sl_variant default V2), net PnL via `_stats` (A1-calibrated
ROUND_TRIP_FEE_PCT + funding).

Modes
-----
GATE_OFF               no directional gate (secondary baseline)
H4_TMA_GATE            production gate: `run_backtest(htf_directional_gate_enabled=True)`,
                       computed IN-WINDOW exactly like the Faza 3.2 script (TMA20 on H4
                       warms up in ~6 days, and production itself only fetches 50 H4 bars).
                       This is THE reference the success criterion is measured against.
EMA_CASCADE            H4 close > EMA26 > EMA50 > EMA144 > EMA200 -> LONG_ONLY; mirror ->
                       SHORT_ONLY; else BOTH.
EMA_CASCADE_PLUS_ZONE  cascade AND close >= EMA200*(1+zone) (LONG_ONLY) / close <=
                       EMA200*(1-zone) (SHORT_ONLY), zone=0.10 by default (`--zone`).
                       Interpretation of "EMA Alerts" +/-10% zone: inside the zone the
                       trend is not considered strong enough to gate -> BOTH. Stricter
                       (gates less often) than plain cascade.
BOTH_COMBINED          H4_TMA_GATE AND EMA_CASCADE: a signal passes only if BOTH gates
                       allow it. Agreeing biases pass through; one-sided bias wins over
                       BOTH; conflicting LONG_ONLY vs SHORT_ONLY -> FLAT (blocks every
                       signal, which is what "both must allow" means -- counted separately).

Causality / lag
---------------
H4 candles are built with the engine's own `_h4_bucket_ids`/`_build_h4_candles`
(UTC-clock-aligned). An H1 candle in bucket b reads H4 bar b-1 -- the same one-bucket
lag as `_htf_directional_bias_series`. EMAs are `ewm(span=N, adjust=False)`, seeded with
the first H4 close (recursive EMA, purely causal).

EMA200 warm-up
--------------
Residual weight of the seed after n bars is (1 - 2/201)^n: ~13% @200, ~5% @300,
~1% @460, ~0.25% @600. We require `EMA_MIN_BARS = 600` closed H4 bars (~100 days)
before classifying; before that the bias is BOTH. A 122d window holds only ~732 H4 bars,
so the EMAs are computed ONCE over the whole fixture (causal) and each window looks up
its own timestamps -- i.e. a window inherits the fixture history before it, which is
legitimate (no future data) and is what a live system with enough history would see.
Consequence: W1 of every fixture is "cold" (EMA gate inactive for its first ~100 days,
so EMA ~= GATE_OFF there), which biases W1 AGAINST the hypothesis. The verdict is
therefore tallied on WARM windows only (>= EMA_MIN_BARS H4 bars of history before the
window start); the all-window tally is printed too.

TP-and-flip blocking (issue #83's key exit risk)
-----------------------------------------------
`filter_bias` blocks the opposite-band signal that, on an open position, would be a
TP_AND_FLIP. `MMS4SignalEngine.evaluate_signals` is wrapped (original called first,
result returned unchanged): whenever the result is "Signal blocked by directional_bias",
the call is re-probed with bias=BOTH to learn the raw signal type. After the run the
blocked candles are matched against the open-position intervals (entry_idx < i <
exit_idx of non-partial trades) -> blocked flips (BASE_SHORT vs open long / BASE_LONG vs
open short), blocked addons, blocked entries. `evaluate_signals` only reads state
(verified by grep: no self./state_manager writes), so the probe cannot alter the run;
a runtime self-check also asserts wrapped == unwrapped results on one window.

Reproduce:

    PYTHONPATH=src python3 scripts/ema_cascade_gate_analysis.py \
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \
                   ohlcv_BTC_USDC_1h_max.csv ohlcv_ETH_USDC_1h_max.csv \
        --json ema_cascade_results.json
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

import pandas as pd  # noqa: E402

from mms4tk.analytics import Candle, IndicatorParams, MMS4SignalEngine  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
EMA_PERIODS = (26, 50, 144, 200)
EMA_MIN_BARS = 600          # closed H4 bars before classifying (seed residual ~0.25%)
DEFAULT_ZONE = 0.10
MODES = ("GATE_OFF", "H4_TMA_GATE", "EMA_CASCADE", "EMA_CASCADE_PLUS_ZONE", "BOTH_COMBINED")
EMA_MODES = ("EMA_CASCADE", "EMA_CASCADE_PLUS_ZONE", "BOTH_COMBINED")
_ORIG_BIAS_FN = be._htf_directional_bias_series
_ORIG_EVAL = MMS4SignalEngine.evaluate_signals
BLOCK_PREFIX = "Signal blocked by directional_bias"


# ── pure classifier (unit-tested in tests/test_ema_cascade_gate_analysis.py) ──

def classify_ema_regime(close: float, e26: float, e50: float, e144: float, e200: float,
                        mode: str = "cascade", zone: float = DEFAULT_ZONE) -> str:
    """LONG_ONLY / SHORT_ONLY / BOTH from one closed H4 bar. Strict inequalities:
    any tie breaks the cascade -> BOTH. NaN anywhere -> BOTH."""
    if mode not in ("cascade", "cascade_plus_zone"):
        raise ValueError(f"mode must be cascade|cascade_plus_zone, got {mode!r}")
    vals = (close, e26, e50, e144, e200)
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in vals):
        return "BOTH"
    up = close > e26 > e50 > e144 > e200
    down = close < e26 < e50 < e144 < e200
    if mode == "cascade_plus_zone":
        up = up and close >= e200 * (1.0 + zone)
        down = down and close <= e200 * (1.0 - zone)
    if up:
        return "LONG_ONLY"
    if down:
        return "SHORT_ONLY"
    return "BOTH"


def ema_series(values: List[float], span: int) -> List[float]:
    """Recursive EMA, alpha=2/(span+1), seeded with values[0] (pandas adjust=False)."""
    return list(pd.Series(values, dtype=float).ewm(span=span, adjust=False).mean())


def ema_cascade_bias_series(candles: List[Candle], mode: str = "cascade", zone: float = DEFAULT_ZONE,
                            min_bars: int = EMA_MIN_BARS) -> List[str]:
    """Per-H1-candle bias, causal: H1 candle in H4 bucket b reads closed H4 bar b-1,
    and only once b-1 has index >= min_bars-1 (i.e. >= min_bars closed H4 bars)."""
    bucket_ids = be._h4_bucket_ids(candles)
    h4 = be._build_h4_candles(candles, bucket_ids)
    closes = [c.close for c in h4]
    emas = {p: ema_series(closes, p) for p in EMA_PERIODS}
    per_bucket = [
        classify_ema_regime(closes[k], emas[26][k], emas[50][k], emas[144][k], emas[200][k], mode, zone)
        if k >= min_bars - 1 else "BOTH"
        for k in range(len(h4))
    ]
    return ["BOTH" if bid - 1 < 0 else per_bucket[bid - 1] for bid in bucket_ids]


def combine_bias(a: str, b: str) -> str:
    """AND of two gates: signal passes only if both allow it."""
    if a == b or b == "BOTH":
        return a
    if a == "BOTH":
        return b
    return "FLAT"  # LONG_ONLY vs SHORT_ONLY (or anything with FLAT): nothing allowed by both


# ── fixture handling ──

def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


_CACHE: Dict[str, dict] = {}


def _fixture_ctx(name: str, zone: float) -> dict:
    key = f"{name}|{zone}"
    if key not in _CACHE:
        candles = load_fixture(name)
        ts = [c.timestamp for c in candles]
        casc = ema_cascade_bias_series(candles, "cascade", zone)
        zoned = ema_cascade_bias_series(candles, "cascade_plus_zone", zone)
        bucket_ids = be._h4_bucket_ids(candles)
        _CACHE[key] = {
            "candles": candles,
            "EMA_CASCADE": dict(zip(ts, casc)),
            "EMA_CASCADE_PLUS_ZONE": dict(zip(ts, zoned)),
            "bucket_of_ts": dict(zip(ts, bucket_ids)),
        }
    return _CACHE[key]


# ── one run, with injected bias + blocked-signal instrumentation ──

def _install(mode: str, ctx: dict, blocked: Optional[list]):
    if mode in ("EMA_CASCADE", "EMA_CASCADE_PLUS_ZONE"):
        lut = ctx[mode]
        be._htf_directional_bias_series = lambda cs, params: [lut[c.timestamp] for c in cs]
    elif mode == "BOTH_COMBINED":
        lut = ctx["EMA_CASCADE"]
        be._htf_directional_bias_series = lambda cs, params: [
            combine_bias(h, lut[c.timestamp]) for h, c in zip(_ORIG_BIAS_FN(cs, params), cs)]
    else:
        be._htf_directional_bias_series = _ORIG_BIAS_FN

    if blocked is None:
        MMS4SignalEngine.evaluate_signals = _ORIG_EVAL
        return

    def wrapped(self, candles, state_manager, params=None, symbol=None):
        res = _ORIG_EVAL(self, candles, state_manager, params=params, symbol=symbol)
        if (not res.is_allowed) and res.reason.startswith(BLOCK_PREFIX):
            prof = state_manager.strategy_profile
            saved = prof.directional_bias
            prof.directional_bias = "BOTH"
            try:
                raw = _ORIG_EVAL(self, candles, state_manager, params=params, symbol=symbol)
            finally:
                prof.directional_bias = saved
            blocked.append((len(candles) - 2, raw.signal_type, bool(raw.is_allowed), saved))
        return res

    MMS4SignalEngine.evaluate_signals = wrapped


def _uninstall():
    be._htf_directional_bias_series = _ORIG_BIAS_FN
    MMS4SignalEngine.evaluate_signals = _ORIG_EVAL


def _classify_blocked(trades: List[dict], blocked: list) -> dict:
    intervals = [(t["entry_idx"], t["exit_idx"], t["side"]) for t in trades if not t.get("partial")]
    out = dict(blocked_flip_candles=0, blocked_flip_positions=0, blocked_addons=0,
               blocked_entries=0, blocked_other=0, flat_candles=0)
    flip_pos = set()
    for i, raw, raw_allowed, bias in blocked:
        if bias == "FLAT":
            out["flat_candles"] += 1
        open_pos = next((iv for iv in intervals if iv[0] < i < iv[1]), None)
        if not raw_allowed:
            out["blocked_other"] += 1  # raw signal would itself have been rejected downstream
            continue
        if open_pos is None:
            if raw in ("BASE_LONG", "BASE_SHORT"):
                out["blocked_entries"] += 1
            else:
                out["blocked_other"] += 1
            continue
        side = open_pos[2]
        if (side == "long" and raw == "BASE_SHORT") or (side == "short" and raw == "BASE_LONG"):
            out["blocked_flip_candles"] += 1
            flip_pos.add(open_pos[0])
        elif raw == ("HIGH_RISK_ADD_LONG" if side == "long" else "HIGH_RISK_ADD_SHORT"):
            out["blocked_addons"] += 1
        else:
            out["blocked_other"] += 1
    out["blocked_flip_positions"] = len(flip_pos)
    return out


def one_run(symbol: str, chunk: List[Candle], mode: str, ctx: dict, instrument: bool = True) -> dict:
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)
    blocked: Optional[list] = [] if instrument else None
    _install(mode, ctx, blocked)
    try:
        trades = be.run_backtest(symbol, chunk, params, stale_candles_threshold=12,
                                 trend_filter_enabled=False,
                                 htf_directional_gate_enabled=(mode != "GATE_OFF"))
    finally:
        _uninstall()
    s = be._stats(trades)
    row = {
        "total_pnl": s["total_pnl"], "rr": s["rr"], "win_rate": s["win_rate"],
        "txn": len(trades), "full_closes": s["full_closes"],
        "sl_exits": sum(t["exit_reason"] == "STOP_LOSS" for t in trades),
        "tp_flips": sum(t["exit_reason"] == "TP_AND_FLIP" for t in trades),
        "shorts": sum(t.get("side") == "short" for t in trades),
        "longs": sum(t.get("side") == "long" for t in trades),
        "addons": sum(bool(t.get("addon_applied")) for t in trades),
        "exit_reasons": {},
    }
    for t in trades:
        row["exit_reasons"][t["exit_reason"]] = row["exit_reasons"].get(t["exit_reason"], 0) + 1
    if blocked is not None:
        row.update(_classify_blocked(trades, blocked))
    # regime occupancy over the window's H1 candles (what the gate was set to)
    if mode in ("EMA_CASCADE", "EMA_CASCADE_PLUS_ZONE"):
        seq = [ctx[mode][c.timestamp] for c in chunk]
    elif mode == "H4_TMA_GATE":
        seq = _ORIG_BIAS_FN(chunk, params)
    elif mode == "BOTH_COMBINED":
        seq = [combine_bias(h, ctx["EMA_CASCADE"][c.timestamp]) for h, c in zip(_ORIG_BIAS_FN(chunk, params), chunk)]
    else:
        seq = ["BOTH"] * len(chunk)
    n = max(len(seq), 1)
    row["pct_gated"] = sum(b != "BOTH" for b in seq) / n * 100
    return row


def _task(args):
    name, zone, w, per_window, mode, instrument = args
    ctx = _fixture_ctx(name, zone)
    candles = ctx["candles"]
    chunk = candles[w * per_window:(w + 1) * per_window]
    symbol = "ETH/USDC" if "ETH" in name.upper() else "BTC/USDC"
    return (name, w, mode, one_run(symbol, chunk, mode, ctx, instrument))


def _self_check(name: str, zone: float, per_window: int) -> None:
    """Instrumentation must be a pure observer: wrapped == unwrapped on one EMA window.
    Uses window index 1 (warm on the 24m fixtures) so the EMA gate is actually active."""
    ctx = _fixture_ctx(name, zone)
    chunk = ctx["candles"][per_window:2 * per_window]
    symbol = "ETH/USDC" if "ETH" in name.upper() else "BTC/USDC"
    a = one_run(symbol, chunk, "EMA_CASCADE", ctx, instrument=True)
    b = one_run(symbol, chunk, "EMA_CASCADE", ctx, instrument=False)
    for k in ("total_pnl", "txn", "tp_flips", "sl_exits", "win_rate"):
        if a[k] != b[k]:
            raise SystemExit(f"SELF-CHECK FAILED: instrumentation changed {k}: {a[k]} vs {b[k]}")
    print(f"self-check OK ({name} W2 EMA_CASCADE): wrapped == unwrapped "
          f"(pnl={a['total_pnl']:+.6f}, txn={a['txn']}, blocked_flips={a['blocked_flip_candles']})", flush=True)


def measure(fixtures, window_days: int, zone: float, workers: int, self_check: bool) -> list:
    per_window = window_days * 24
    if self_check:
        _self_check(fixtures[0], zone, per_window)
    tasks, meta = [], {}
    for name in fixtures:
        ctx = _fixture_ctx(name, zone)
        candles = ctx["candles"]
        n_windows = len(candles) // per_window
        first_bucket = ctx["bucket_of_ts"][candles[0].timestamp]
        print(f"=== {name}: {len(candles)} candles -> {n_windows} x {window_days}d windows ===", flush=True)
        for w in range(n_windows):
            chunk = candles[w * per_window:(w + 1) * per_window]
            h4_hist = ctx["bucket_of_ts"][chunk[0].timestamp] - first_bucket  # closed H4 bars before window
            meta[(name, w)] = {
                "fixture": name,
                "symbol": "ETH" if "ETH" in name.upper() else "BTC",
                "set": "USDT_24m" if "USDT" in name.upper() else "USDC_max",
                "window": f"{'ETH' if 'ETH' in name.upper() else 'BTC'}_W{w + 1}",
                "start": str(datetime.fromtimestamp(chunk[0].timestamp / 1000, timezone.utc).date()),
                "end": str(datetime.fromtimestamp(chunk[-1].timestamp / 1000, timezone.utc).date()),
                "candles": len(chunk),
                "h4_history_bars": h4_hist,
                "ema_warm": h4_hist >= EMA_MIN_BARS,
            }
            for m in MODES:
                tasks.append((name, zone, w, per_window, m, True))
    rows = {k: dict(v) for k, v in meta.items()}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for name, w, mode, res in ex.map(_task, tasks):
            rows[(name, w)][mode] = res
            r = rows[(name, w)]
            print(f"  {r['set']} {r['window']} {mode:22s} pnl={res['total_pnl'] * 100:+7.2f}% "
                  f"wr={res['win_rate']:5.1f}% txn={res['txn']:4d} flips={res['tp_flips']:3d} "
                  f"blk_flip={res['blocked_flip_candles']:3d}/{res['blocked_flip_positions']:2d}pos "
                  f"gated={res['pct_gated']:5.1f}%", flush=True)
    return list(rows.values())


# ── reporting ──

def _sign_test(k: int, n: int) -> float:
    """Exact two-sided binomial sign test p-value for k successes out of n."""
    if n == 0:
        return float("nan")
    tail = sum(math.comb(n, i) for i in range(max(k, n - k), n + 1))
    return min(1.0, 2 * tail / 2 ** n)


def _cmp(sub, a: str, b: str) -> str:
    n = len(sub)
    if n == 0:
        return "n=0"
    d = [r[a]["total_pnl"] - r[b]["total_pnl"] for r in sub]
    pos, zero = sum(x > 0 for x in d), sum(x == 0 for x in d)
    mean = sum(d) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1)) if n > 1 else float("nan")
    return (f"{a} > {b}: {pos}/{n} (ties {zero}), mean Δ {mean * 100:+.2f} pp, sd {sd * 100:.2f} pp, "
            f"sign p={_sign_test(pos, n - zero):.3f}, pooled {sum(r[a]['total_pnl'] for r in sub) * 100:+.2f}% "
            f"vs {sum(r[b]['total_pnl'] for r in sub) * 100:+.2f}%")


def report(rows, zone: float = DEFAULT_ZONE) -> None:
    rows.sort(key=lambda r: (r["set"], r["symbol"], int(r["window"].split("W")[1])))
    short = {"GATE_OFF": "OFF", "H4_TMA_GATE": "H4", "EMA_CASCADE": "EMA",
             "EMA_CASCADE_PLUS_ZONE": "EMA+Z", "BOTH_COMBINED": "H4&EMA"}
    print(f"\nEMA_MIN_BARS={EMA_MIN_BARS} H4 bars, zone=±{zone * 100:.0f}% | cold = EMA not warm at window start")
    print("\n### Net PnL / win-rate / txn per window\n")
    print("| set | okno | zakres | warm | " + " | ".join(f"{short[m]} pnl / wr / n" for m in MODES) + " |")
    print("|---|---|---|---|" + "---|" * len(MODES))
    for r in rows:
        cells = [f"{r[m]['total_pnl'] * 100:+.2f}% / {r[m]['win_rate']:.1f}% / {r[m]['txn']}" for m in MODES]
        print(f"| {r['set']} | {r['window']} | {r['start']}..{r['end']} | {'yes' if r['ema_warm'] else 'COLD'} | "
              + " | ".join(cells) + " |")

    print("\n### TP-and-flip exposure (flips executed; blocked-flip candles/positions; blocked entries; blocked addons; % of H1 candles gated)\n")
    gm = MODES[1:]
    print("| set | okno | " + " | ".join(f"{short[m]} flips / blkF c/p / blkE / blkA / gated%" for m in gm)
          + f" | OFF flips |")
    print("|---|---|" + "---|" * (len(gm) + 1))
    for r in rows:
        cells = [f"{r[m]['tp_flips']} / {r[m]['blocked_flip_candles']}/{r[m]['blocked_flip_positions']} / "
                 f"{r[m]['blocked_entries']} / {r[m]['blocked_addons']} / {r[m]['pct_gated']:.0f}%" for m in gm]
        print(f"| {r['set']} | {r['window']} | " + " | ".join(cells) + f" | {r['GATE_OFF']['tp_flips']} |")

    print("\n### Aggregates (primary: EMA mode vs H4_TMA_GATE)\n")
    for set_ in ("USDT_24m", "USDC_max"):
        for sym in ("BTC", "ETH", None):
            for warm_only in (True, False):
                sub = [r for r in rows if r["set"] == set_ and (sym is None or r["symbol"] == sym)
                       and (r["ema_warm"] or not warm_only)]
                if not sub:
                    continue
                print(f"{set_} {sym or 'BTC+ETH'} [{'warm only' if warm_only else 'all windows'}] n={len(sub)}")
                for m in EMA_MODES:
                    print(f"  {_cmp(sub, m, 'H4_TMA_GATE')}")
                print(f"  {_cmp(sub, 'H4_TMA_GATE', 'GATE_OFF')}")
                for m in EMA_MODES:
                    print(f"  (secondary) {_cmp(sub, m, 'GATE_OFF')}")
                tot = {m: {k: sum(r[m][k] for r in sub) for k in
                           ("txn", "tp_flips", "sl_exits", "blocked_flip_candles", "blocked_flip_positions",
                            "blocked_entries", "blocked_addons", "flat_candles")} for m in MODES}
                for m in MODES:
                    t = tot[m]
                    print(f"    {short[m]:7s} txn={t['txn']:5d} flips={t['tp_flips']:4d} SL={t['sl_exits']:4d} "
                          f"blocked: flip-candles={t['blocked_flip_candles']:4d} flip-positions={t['blocked_flip_positions']:4d} "
                          f"entries={t['blocked_entries']:4d} addons={t['blocked_addons']:4d} FLAT={t['flat_candles']}")

    print("\n### Success criterion (issue #83): EMA mode beats H4_TMA_GATE on net PnL in a majority "
          "of WARM windows on BOTH symbols (USDT_24m primary; USDC_max control)\n")
    for set_ in ("USDT_24m", "USDC_max"):
        for m in EMA_MODES:  # BOTH_COMBINED listed for information; the #83 criterion names the EMA modes
            parts, ok = [], True
            for sym in ("BTC", "ETH"):
                sub = [r for r in rows if r["set"] == set_ and r["symbol"] == sym and r["ema_warm"]]
                k = sum(r[m]["total_pnl"] > r["H4_TMA_GATE"]["total_pnl"] for r in sub)
                ok = ok and len(sub) > 0 and k > len(sub) / 2
                parts.append(f"{sym} {k}/{len(sub)}")
            verdict = ('MET' if ok else 'NOT MET') if m != "BOTH_COMBINED" else \
                f"({'majority' if ok else 'no majority'}; informational, NOT the #83 criterion)"
            print(f"  {set_} {m}: {', '.join(parts)} -> {verdict}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", default=[
        "ohlcv_BTC_USDT_1h_swap_24m.csv", "ohlcv_ETH_USDT_1h_swap_24m.csv",
        "ohlcv_BTC_USDC_1h_max.csv", "ohlcv_ETH_USDC_1h_max.csv"])
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--zone", type=float, default=DEFAULT_ZONE, help="EMA200 zone for cascade_plus_zone")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--no-self-check", action="store_true")
    ap.add_argument("--json", default=None, help="write raw per-window results here")
    ap.add_argument("--report", nargs="*", default=None, help="re-print report from --json files")
    args = ap.parse_args()

    if args.report is not None:
        rows = []
        for p in args.report:
            with open(p, encoding="utf-8") as f:
                rows.extend(json.load(f))
        report(rows, args.zone)
        return

    rows = measure(args.fixtures, args.window_days, args.zone, args.workers, not args.no_self_check)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)
        print(f"\nwrote {args.json}")
    report(rows, args.zone)


if __name__ == "__main__":
    main()

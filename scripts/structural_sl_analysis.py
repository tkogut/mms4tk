#!/usr/bin/env python3
"""Issue #82 step 1-2 (owner-gated, MEASUREMENT ONLY): does a structural stop-loss
anchored to the H1 TMA band (`lower_band(entry) - k*ATR` for LONG, symmetric for
SHORT) beat the fixed `base_sl_pct=2.0%` on net PnL?

Background (task.md "variant #3 (SL=k*ATR)", parked, never backtested). The
candle-low variant is already falsified in this repo (see issue #82 Analysis
section and `scripts/sl_grid_analysis.py` / task.md:1187-1199): base_sl_pct=1.5%
loses to 2.0% on net PnL in 9/10, 9/12 and 6/6 grid cells despite a better R:R —
the one reproducible result in the whole `sl_grid_analysis.py` grid. This script
measures the wider, still-open "band anchored" variant on the SAME production
harness (`backtest_engine.run_backtest`), so the two are directly comparable.

NEVER RUN AS PRODUCTION CODE. This is a measurement script only — see issue #82,
step 3 ("persist SL price per position, update Self-Healing/midline/ratchet,
epoch bump") requires a SEPARATE issue and a separate owner approval, even if
this measurement comes back positive. Zero changes to src/ — every hook below is
a module-attribute monkeypatch of `mms4tk.backtest_engine`'s own late-bound
lookups, exactly like `scripts/sl_grid_analysis.py` patches `BASE_SL_PCT` and
`scripts/ema_cascade_gate_analysis.py` patches `_htf_directional_bias_series`.

How the structural SL is modeled without touching backtest_engine.py's ~500-line
loop (see that file's SL check at :806-817 and the V2 addon overwrite at :991):

  1. `BASE_SL_PCT` is a module GLOBAL, read fresh at three places every run:
     the SL check itself (:806), the V2 addon SL re-anchor (:991 — this means a
     structural SL survives an addon "for free", still anchored to the ORIGINAL
     base_entry_price, matching invariant #4/#5 from issue #82), and
     `close_position`'s `sl_percent` fed to `on_stop_loss_hit`'s hardcoded
     `>= 0.02` SCOUT_MODE gate (:755, invariant #2). Since only one position per
     symbol is open at a time, setting this global exactly once per position, at
     the moment it opens, is sufficient and does not leak between positions.
  2. The ONLY place a fresh position is opened is `MMS4InactivityManager.track_position`
     (backtest_engine.py :945 and :1013 — the two BASE_LONG/SHORT and flip entry
     sites, and nowhere else). Subclassing it and patching
     `backtest_engine.MMS4InactivityManager` (a late-bound name, resolved at
     `im = MMS4InactivityManager()` call time, same trick as #1) gives a clean
     hook with `entry_price`, `entry_time`, `side` — enough to look up that
     candle's `lower_band`/`upper_band`/`atr` (precomputed once per cell via the
     same causal `TMAIndicator.calculate`, position-aligned 1:1 with `candles`)
     and compute the structural absolute SL price, then convert it to the
     equivalent `pct` that reproduces it via `BASE_SL_PCT`.
  3. Invariant #2 (SCOUT_MODE coupling) is modeled two ways, per the issue's
     explicit "model it, or run both and document the choice": `--mode coupled`
     (default) leaves `MMS4StateManager.on_stop_loss_hit`'s real `>= 0.02` gate
     untouched — a structural SL that happens to exceed 2% still triggers SCOUT,
     exactly like a real wide 2%+ base_sl_pct would; `--mode decoupled` patches
     `backtest_engine.MMS4StateManager` to a subclass whose `on_stop_loss_hit` is
     a no-op, modeling a hypothetical world where that hardcoded threshold was
     adapted alongside the structural SL. Both are reported; the PR states which
     is treated as primary.

Degenerate case (see analytics.py's own "ATR==0 / bands collapsed" guard at
:353-361 for precedent): if the structural price would sit on the WRONG side of
entry (`lower_band - k*ATR >= entry` for LONG, `upper_band + k*ATR <= entry` for
SHORT — a deep band-undershoot bigger than `k*ATR`, or NaN bands pre-warmup),
there is no valid structural stop. Fallback: reuse the fixed `--fallback-pct`
(default 2.0%, i.e. today's production value) for that ONE position, and count
it — reported per (symbol, k) as a degenerate rate, since a k this large would
matter for the recommendation even if net PnL looks good.

Self-check (mirrors PR #84's harness-verification convention): `--self-check`
runs the SAME cells with `k` ignored, forcing every entry onto the fixed
`--fallback-pct`, with THE SAME InactivityManager/StateManager patches installed
(entry hook always takes the degenerate branch). This must reproduce
`sl_grid_analysis.py`'s own `base_sl_pct=<fallback-pct>` row bit-for-bit on the
same fixtures/windows before any structural result is trusted.

Usage:

    # headline measurement: 5 independent 122d windows on the 730d USDT-swap
    # proxy, both symbols, k in {0.5, 1.0, 1.5, 2.0}, coupled SCOUT (default)
    PYTHONPATH=src python3 scripts/structural_sl_analysis.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \\
        --window-days 122

    # decoupled SCOUT variant
    PYTHONPATH=src python3 scripts/structural_sl_analysis.py \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv \\
        --mode decoupled

    # harness self-check (must match sl_grid_analysis.py's 2.0% row exactly)
    PYTHONPATH=src python3 scripts/structural_sl_analysis.py --self-check \\
        --fixtures ohlcv_BTC_USDT_1h_swap_24m.csv ohlcv_ETH_USDT_1h_swap_24m.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

logging.disable(logging.CRITICAL)

from mms4tk.analytics import Candle, IndicatorParams, TMAIndicator  # noqa: E402
from mms4tk.inactivity_manager import MMS4InactivityManager  # noqa: E402
from mms4tk.state_manager import MMS4StateManager  # noqa: E402
import mms4tk.backtest_engine as be  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
K_GRID = [0.5, 1.0, 1.5, 2.0]
FIXED_SL_PCT = 0.02  # today's production base_sl_pct — the value structural must beat


def load_fixture(name: str) -> List[Candle]:
    path = name if os.path.isabs(name) else os.path.join(FIXTURES, name)
    with open(path, encoding="utf-8") as f:
        return [Candle(timestamp=int(r["timestamp"]), open=float(r["open"]), high=float(r["high"]),
                       low=float(r["low"]), close=float(r["close"]), volume=float(r["volume"]))
                for r in csv.DictReader(f)]


def structural_sl_pct(
    side: str, entry_price: float, lower_band: float, upper_band: float, atr: float,
    k: float, fallback_pct: float,
) -> Tuple[float, bool]:
    """Pure conversion: absolute band-anchored SL price -> the `pct` that
    reproduces it via `ref * (1 -+ pct)`. Returns (pct, is_degenerate).
    Degenerate (NaN inputs, non-positive ATR, or the structural price landing on
    the wrong side of entry) falls back to `fallback_pct`, `is_degenerate=True`.
    """
    import math

    if (
        entry_price <= 0
        or any(math.isnan(v) for v in (lower_band, upper_band, atr))
        or atr <= 0
    ):
        return fallback_pct, True

    if side.lower() == "long":
        sl_abs = lower_band - k * atr
        if 0 < sl_abs < entry_price:
            return (entry_price - sl_abs) / entry_price, False
        return fallback_pct, True
    else:
        sl_abs = upper_band + k * atr
        if sl_abs > entry_price > 0:
            return (sl_abs - entry_price) / entry_price, False
        return fallback_pct, True


class StructuralInactivityManager(MMS4InactivityManager):
    """Patched in as `backtest_engine.MMS4InactivityManager` for the duration of
    one worker cell. `track_position` is the ONLY entry hook in run_backtest
    (BASE_LONG/SHORT and flip re-entries) — see module docstring point 2."""

    ctx: Dict[str, Any] = {}  # set by _cell() before run_backtest() is called

    def track_position(self, inst_id: str, entry_price: float, entry_time: float,
                        side: str, atr: float) -> None:
        ctx = StructuralInactivityManager.ctx
        if ctx.get("force_fallback"):
            pct, degenerate = ctx["fallback_pct"], True
        else:
            idx = ctx["ts_to_idx"].get(int(entry_time))
            if idx is not None and idx < len(ctx["full_df"]):
                row = ctx["full_df"].iloc[idx]
                lower_v, upper_v, atr_v = float(row["lower_band"]), float(row["upper_band"]), float(row["atr"])
            else:
                lower_v = upper_v = atr_v = float("nan")
            pct, degenerate = structural_sl_pct(
                side, entry_price, lower_v, upper_v, atr_v, ctx["k"], ctx["fallback_pct"]
            )
        be.BASE_SL_PCT = pct
        ctx["entries"].append(pct)
        if degenerate:
            ctx["degenerate"][0] += 1
        super().track_position(inst_id, entry_price, entry_time, side, atr)


class DecoupledStateManager(MMS4StateManager):
    """`--mode decoupled`: the hardcoded `sl_percent >= 0.02` SCOUT_MODE gate
    (invariant #2, issue #82) never fires for a structural stop-out — models a
    hypothetical world where that threshold was adapted alongside the SL."""

    def on_stop_loss_hit(self, sl_percent: float, sl_price: float = 0.0,
                          symbol: Optional[str] = None) -> bool:
        return False


def _cell(args: Tuple[str, str, int, int, Optional[float], str, str, float]) -> Dict[str, Any]:
    """One (fixture, symbol, window, k) run, in its own process."""
    fixture, symbol, w, per_window, k, mode, self_check, fallback_pct = args
    candles = load_fixture(fixture)[w * per_window:(w + 1) * per_window]
    params = IndicatorParams(engine_mode="ADAPTIVE", adaptive_vr_high=1.6, adaptive_vr_low=0.8)

    full_df = TMAIndicator.calculate(candles, params)
    ts_to_idx = {int(c.timestamp): i for i, c in enumerate(candles)}

    # self-check / reference cell: k=None forces every entry onto the fixed
    # fallback_pct, regardless of side, reproducing plain fixed-pct behavior
    # through the SAME patched hooks (not through structural_sl_pct's own
    # degenerate branch, which is side-asymmetric and not meant for this).
    StructuralInactivityManager.ctx = {
        "full_df": full_df, "ts_to_idx": ts_to_idx, "k": k,
        "fallback_pct": fallback_pct, "entries": [], "degenerate": [0],
        "force_fallback": k is None,
    }
    be.MMS4InactivityManager = StructuralInactivityManager
    be.MMS4StateManager = DecoupledStateManager if mode == "decoupled" else MMS4StateManager
    be.BASE_SL_PCT = fallback_pct  # pre-warmup default before the first track_position call

    trades = be.run_backtest(symbol, candles, params, stale_candles_threshold=12,
                             trend_filter_enabled=False, addon_sl_variant="V2")
    stats = be._stats(trades)

    entries = StructuralInactivityManager.ctx["entries"]
    return {
        "fixture": fixture, "symbol": symbol, "window": w, "k": k, "mode": mode,
        "total_pnl": stats["total_pnl"], "rr": stats["rr"], "trades": len(trades),
        "sl_exits": sum(1 for t in trades if t["exit_reason"] == "STOP_LOSS"),
        "tp_flips": sum(1 for t in trades if t["exit_reason"] == "TP_AND_FLIP"),
        "entries_opened": len(entries),
        "degenerate": StructuralInactivityManager.ctx["degenerate"][0],
        "sl_pcts": entries,
        "start": datetime.fromtimestamp(candles[0].timestamp / 1000, timezone.utc).date().isoformat(),
        "end": datetime.fromtimestamp(candles[-1].timestamp / 1000, timezone.utc).date().isoformat(),
    }


def _pctile(values: List[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", nargs="+", required=True)
    ap.add_argument("--window-days", type=int, default=122)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--csv-out", default=None)
    ap.add_argument("--mode", choices=("coupled", "decoupled"), default="coupled",
                    help="coupled: real >=2%% SCOUT_MODE gate applies to structural stop-outs too "
                         "(default, production-faithful). decoupled: that gate never fires.")
    ap.add_argument("--fallback-pct", type=float, default=FIXED_SL_PCT,
                    help="degenerate-case and self-check fixed SL pct (default 2.0%%)")
    ap.add_argument("--self-check", action="store_true",
                    help="ignore --mode's k grid; force every entry onto --fallback-pct through "
                         "the same patched hooks, to verify against sl_grid_analysis.py's own row")
    args = ap.parse_args()

    per_window = args.window_days * 24
    ks: List[Optional[float]] = [None] if args.self_check else list(K_GRID)
    jobs: List[Tuple[str, str, int, int, Optional[float], str, str, float]] = []
    for fixture in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
        n_windows = len(load_fixture(fixture)) // per_window
        for w in range(n_windows):
            for k in ks:
                jobs.append((fixture, symbol, w, per_window, k, args.mode,
                            "yes" if args.self_check else "no", args.fallback_pct))

    print(f"{len(jobs)} cells (window={args.window_days}d = {per_window} candles), "
          f"mode={args.mode}, self_check={args.self_check}, fee={be.ROUND_TRIP_FEE_PCT}", flush=True)
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(_cell, jobs))

    if args.csv_out:
        with open(args.csv_out, "w", newline="", encoding="utf-8") as f:
            fieldnames = [k for k in rows[0].keys() if k != "sl_pcts"]
            wr = csv.DictWriter(f, fieldnames=fieldnames)
            wr.writeheader()
            wr.writerows({k: v for k, v in r.items() if k != "sl_pcts"} for r in rows)

    if args.self_check:
        by = {(r["fixture"], r["window"]): r for r in rows}
        for fixture in args.fixtures:
            wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
            print(f"\n=== SELF-CHECK {fixture} (fallback_pct={args.fallback_pct * 100:.1f}%) ===")
            for w in wins:
                r = by[(fixture, w)]
                print(f"  W{w + 1} {r['start']}..{r['end']}: pnl={r['total_pnl'] * 100:+.2f}% "
                      f"rr={r['rr']:.3f} trades={r['trades']} sl_exits={r['sl_exits']} "
                      f"(degenerate {r['degenerate']}/{r['entries_opened']} entries, "
                      f"expect 100% degenerate)")
        print("\nCompare these rows to `sl_grid_analysis.py --variant V2`'s SL%=2.0 row "
              "for the SAME fixtures/window-days. They must match exactly.")
        return

    by = {(r["fixture"], r["window"], r["k"]): r for r in rows}
    for fixture in args.fixtures:
        wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
        print(f"\n=== {fixture} (mode={args.mode}) ===")
        header = " | ".join(f"W{w + 1}" for w in wins)
        print(f"  {'k':<6} | {header} | SUM")
        fixed_by_w = {w: None for w in wins}  # 2.0% fixed reference, computed below via self-check-free run
        for k in K_GRID:
            cells = [by[(fixture, w, k)] for w in wins]
            body = " | ".join(f"{c['total_pnl'] * 100:+6.2f}%" for c in cells)
            print(f"  {k:<6.1f} | {body} | {sum(c['total_pnl'] for c in cells) * 100:+7.2f}%")
        print("  --- SL distance realized (median / p10 / p90, non-degenerate only) & degenerate rate ---")
        for k in K_GRID:
            cells = [by[(fixture, w, k)] for w in wins]
            all_pcts = [p for c in cells for p in c["sl_pcts"]]
            deg = sum(c["degenerate"] for c in cells)
            tot = sum(c["entries_opened"] for c in cells)
            med = statistics.median(all_pcts) * 100 if all_pcts else float("nan")
            p10 = _pctile(all_pcts, 0.10) * 100
            p90 = _pctile(all_pcts, 0.90) * 100
            print(f"  k={k:<4.1f} | sl% median={med:5.2f} p10={p10:5.2f} p90={p90:5.2f} "
                  f"| degenerate {deg}/{tot} ({(deg / tot * 100) if tot else 0:.0f}%)")
        print("  --- trades / sl_exits / tp_flips ---")
        for k in K_GRID:
            cells = [by[(fixture, w, k)] for w in wins]
            txn = " ".join(f"{c['trades']:>3d}" for c in cells)
            sle = " ".join(f"{c['sl_exits']:>3d}" for c in cells)
            print(f"  k={k:<4.1f} | txn [{txn}] (tot {sum(c['trades'] for c in cells)}) "
                  f"| sl [{sle}] (tot {sum(c['sl_exits'] for c in cells)})")

    # Fixed 2.0% reference, run through the SAME patched hooks (self-check config, k
    # irrelevant since every entry hits the fallback branch) so the comparison is
    # apples-to-apples under this script's harness, not sl_grid_analysis.py's.
    ref_jobs = []
    for fixture in args.fixtures:
        symbol = "ETH/USDC" if "ETH" in fixture.upper() else "BTC/USDC"
        n_windows = len(load_fixture(fixture)) // per_window
        for w in range(n_windows):
            ref_jobs.append((fixture, symbol, w, per_window, None, args.mode, "yes", args.fallback_pct))
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        ref_rows = list(ex.map(_cell, ref_jobs))
    ref_by = {(r["fixture"], r["window"]): r for r in ref_rows}

    print(f"\n=== win-rate vs fixed {args.fallback_pct * 100:.1f}% (net PnL, per independent window) ===")
    for fixture in args.fixtures:
        wins = sorted({r["window"] for r in rows if r["fixture"] == fixture})
        for k in K_GRID:
            wins_beaten = sum(
                1 for w in wins if by[(fixture, w, k)]["total_pnl"] > ref_by[(fixture, w)]["total_pnl"]
            )
            print(f"  {fixture} k={k:<4.1f}: beats fixed in {wins_beaten}/{len(wins)} windows")


if __name__ == "__main__":
    main()

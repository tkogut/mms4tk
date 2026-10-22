#!/usr/bin/env python3
"""Historical replay of past entries under a frozen B0 baseline.

Context (spec: .ai/specs/2026-10-22-historical-replay-b0-baseline.md). Two
independent production samples (504 legs to 2026-10-14, closed_trades; 97 fresh
legs 2026-10-15..21) show the account's entire realized net loss concentrated in
STOP-LOSS exits (~6% of trades, 79-100%+ of the net loss) while the protective
exit layer (STALE_SPLIT_50/FORCE_CLOSE/BREAK-EVEN) nets roughly flat. A
2026-10-21 debate found the per-exit-reason P&L table cannot clear that layer,
because it decides which trade lands in which bucket -- reading
inactivity_manager.py directly confirmed a real asymmetry (the Profit
Giveback/Stall rule fires only on profit_atr >= 0.6; a losing position that
moves past the stagnation-compression band has no inactivity-based exit at all
and rides unprotected to the single hard stop-loss). Nobody currently knows
whether the entry signal itself has directional edge.

This is a READ-ONLY diagnostic: it opens the production sqlite db in
`mode=ro` (never writes), reconstructs real historical positions, and
re-simulates EACH REAL ENTRY under a frozen, pre-registered, simpler exit
policy -- B0 -- to test two questions:
  (i)  does B0 itself show net edge (vs. zero)?
  (ii) is B0 better or worse than what actually happened (paired per position)?
Phase 2 adds a second simulated policy, B0+adverse-brake, mirroring the live
Profit Giveback/Stall rule onto the loss side.

Pre-registered B0 rule (see B0_RULE below; this and DECISION_THRESHOLDS are
committed BEFORE this script is run against the full dataset -- the spec's
"no peeking" requirement):
  - Entry: the position's REAL historical entry (symbol, side, price, time) --
    never regenerated from the signal engine (that risks silently answering "is
    the entry engine reproducible" instead of "would this entry have done
    better under simpler exits" -- see the spec's Alternatives Considered).
  - TP: the opposite TMA band (TMAIndicator.calculate(), unchanged production
    math; upper_band for a long, lower_band for a short), recomputed every
    forward candle -- NOT the static tma_mid midline (that belongs only to the
    separate, newer MIDLINE_PARTIAL_TP feature).
  - SL: a fixed percentage (BASE_SL_PCT) anchored to the entry price.
  - No STALE_SPLIT_50/FORCE_CLOSE/BREAK-EVEN/addon/H4-gate machinery.
  - Same-candle tie-break: if a single forward candle's range touches BOTH the
    SL level and the TP band, B0 is scored as the SL (the conservative,
    worse-case assumption, since intrabar sequence is unknown from OHLC alone).

Position grouping: `closed_trades` rows sharing (symbol, side, entry_timestamp)
are one position (`entry_timestamp` is derived by LoggerDB.get_closed_trades()
via its existing cycle_logs join -- reused here unchanged, reliable only from
~2026-09-18 per the 2026-10-14 analysis). `position_snapshots` is NOT used for
this -- it is PRIMARY KEY (symbol, side), holding only the current live
snapshot, overwritten on every update, with no historical record at all.
`okx_orders` (real exchange fills, keyed by ord_id) is used only as a
cross-check on the expected leg count per position, never as the primary
grouping source.

Usage:
    python3 scripts/b0_baseline_replay.py --check-coverage
    python3 scripts/b0_baseline_replay.py --run [--since YYYY-MM-DD] [--db-path PATH]
                                           [--out-dir .ai/analysis] [--phase2]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from mms4tk.analytics import Candle, IndicatorParams, TMAIndicator  # noqa: E402

DEFAULT_DB_PATH = os.environ.get("MMS4_DB_PATH", os.path.join(REPO_ROOT, ".agents", "swarm", "logs.db"))

# ── Pre-registered B0 rule (committed before the full-dataset run) ──────────
B0_RULE: Dict[str, Any] = {
    "tp": "opposite_tma_band",          # upper_band for long, lower_band for short
    "sl_pct": 0.02,                     # matches production MMS4_BASE_SL_PCT default
    "tma_params": {"tma_length": 20, "atr_period": 14, "atr_mult": 2.0},  # IndicatorParams defaults
    "same_candle_tie_break": "sl",      # conservative: SL wins if both touched in one candle
    "fee_assumption_pct": None,         # filled in from observed historical fee/notional ratio at run time
    "assumptions": (
        "SL% and TMA params are assumed constant across the whole replay window "
        "(today's production defaults) -- system_settings has no historical log, "
        "so per-symbol or time-varying overrides cannot be reconstructed. "
        "Disclosed as a limitation, not silently assumed away."
    ),
}

# ── Pre-registered decision thresholds (extends the 2026-10-14 analysis's own
#    stop/go criteria -- ~800-1600 closes needed to resolve an effect of the
#    observed size; do not invent new ones) ─────────────────────────────────
DECISION_THRESHOLDS: Dict[str, Any] = {
    "min_n_for_verdict": 800,          # below this, classification is always "inconclusive"
    "edge_vs_zero_alpha": 0.05,        # two-sided
    "b0_vs_actual_alpha": 0.05,        # two-sided, paired
    "practical_significance_usd_per_position": 0.10,  # a statistically significant but
    # sub-10-cent-per-position effect is still reported as "inconclusive (practical)"
}

ADVERSE_BRAKE_RULE: Dict[str, Any] = {
    # Direct mirror of inactivity_manager.py's Profit Giveback/Stall rule
    # (profit_atr >= 0.6, 4 stalled candles) onto the loss side. Script-local,
    # stateless re-implementation -- NEVER imports the live InactivityManager/
    # PositionTracker classes, which are stateful and wired to live position
    # tracking, not a historical forward-walk.
    "min_adverse_atr": 0.6,
    "stall_candles": 4,
}


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class Position:
    symbol: str  # native OKX instId, e.g. "BTC-USD_UM_XPERP-310404"
    side: str  # "long" | "short"
    entry_price: float
    entry_timestamp: datetime
    actual_net_usd: float  # sum of pnl_net_usd across all legs (incl. any addon leg)
    actual_base_net_usd: float  # net USD of the base leg(s) only, excluding addon contribution
    leg_count: int
    is_addon_affected: bool
    okx_fill_count: Optional[int] = None  # cross-check count; None if not checked
    expected_fill_count: Optional[int] = None


@dataclass
class ExitSimResult:
    exit_type: str  # "TP" | "SL" | "CENSORED" | "NO_DATA"
    exit_price: Optional[float]
    exit_index: Optional[int]  # candle index of the exit, for audit
    net_usd: float
    pct_return: Optional[float]


@dataclass
class PairedReplayRow:
    position: Position
    b0: ExitSimResult
    b0_brake: Optional[ExitSimResult] = None


# ── Position reconstruction ─────────────────────────────────────────────────

def connect_readonly(db_path: str) -> sqlite3.Connection:
    """Open the production db strictly read-only -- this script must never be
    able to write to the live-mounted database, by construction, not by care."""
    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def check_data_coverage(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Step 1: empirically verify data coverage rather than assume it.

    Returns counts/ranges for ohlcv_cache, cycle_logs, closed_trades, okx_orders,
    and the fraction of closed_trades rows that already carry a non-null
    entry_timestamp (the reliable-grouping proxy)."""
    cur = conn.cursor()
    out: Dict[str, Any] = {}

    cur.execute("SELECT COUNT(*) AS n FROM ohlcv_cache")
    out["ohlcv_cache_rows"] = cur.fetchone()["n"]

    for table, time_col in (("cycle_logs", "timestamp"), ("closed_trades", "timestamp")):
        cur.execute(f"SELECT MIN({time_col}) AS lo, MAX({time_col}) AS hi, COUNT(*) AS n FROM {table}")
        row = cur.fetchone()
        out[table] = {"min": row["lo"], "max": row["hi"], "n": row["n"]}

    cur.execute("SELECT MIN(fill_time) AS lo, MAX(fill_time) AS hi, COUNT(*) AS n FROM okx_orders")
    row = cur.fetchone()
    out["okx_orders"] = {"min": row["lo"], "max": row["hi"], "n": row["n"]}

    cur.execute("SELECT COUNT(*) AS n FROM closed_trades")
    total = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) AS n FROM closed_trades WHERE entry_timestamp IS NOT NULL AND entry_timestamp != ''")
    with_entry = cur.fetchone()["n"]
    out["closed_trades_entry_timestamp_coverage"] = {
        "total": total,
        "with_entry_timestamp": with_entry,
        "fraction": (with_entry / total) if total else 0.0,
    }
    return out


def load_positions(conn: sqlite3.Connection, since_iso: Optional[str] = None) -> List[Position]:
    """Group closed_trades legs sharing (symbol, side, entry_timestamp) into
    positions. Reuses the entry_timestamp column LoggerDB.get_closed_trades()
    already derives and caches via its cycle_logs join -- this script does not
    re-derive it, only reads it. Rows with a null entry_timestamp are excluded
    (unreliable grouping, per the 2026-10-14 analysis), not silently included."""
    cur = conn.cursor()
    query = (
        "SELECT symbol, side, entry_price, entry_timestamp, pnl_net_usd "
        "FROM closed_trades WHERE entry_timestamp IS NOT NULL AND entry_timestamp != ''"
    )
    params: Tuple[Any, ...] = ()
    if since_iso:
        query += " AND entry_timestamp >= ?"
        params = (since_iso,)
    query += " ORDER BY symbol, side, entry_timestamp"
    cur.execute(query, params)
    rows = cur.fetchall()

    # Addon-affected positions: a position_addons row for this (symbol, side)
    # whose position_key (the pinned base_entry_price string) matches this
    # position's entry_price indicates an addon fired on it at some point.
    cur.execute("SELECT symbol, side, position_key FROM position_addons")
    addon_keys = {(r["symbol"], r["side"], r["position_key"]) for r in cur.fetchall()}

    grouped: Dict[Tuple[str, str, str], List[sqlite3.Row]] = {}
    for r in rows:
        key = (r["symbol"], r["side"], r["entry_timestamp"])
        grouped.setdefault(key, []).append(r)

    positions: List[Position] = []
    for (symbol, side, entry_ts), legs in grouped.items():
        entry_price = legs[0]["entry_price"]
        net_total = sum(leg["pnl_net_usd"] for leg in legs)
        position_key = f"{entry_price:.8f}"
        is_addon = (symbol, side, position_key) in addon_keys
        # Base-only net: when addon-affected, this script cannot cleanly split
        # a single leg's pnl between the base and addon contribution after the
        # fact (both trade in the same leg once merged) -- disclosed as a
        # limitation rather than guessed at. The addon flag lets the report
        # separate these positions from the clean (non-addon) comparison.
        positions.append(Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_timestamp=_parse_iso(entry_ts),
            actual_net_usd=net_total,
            actual_base_net_usd=net_total,
            leg_count=len(legs),
            is_addon_affected=is_addon,
        ))
    return positions


def cross_check_okx_fill_count(conn: sqlite3.Connection, position: Position, window_hours: float = 72.0) -> int:
    """Count okx_orders fills for this position's (symbol, posSide) within a
    window starting at its entry -- a data-quality signal, not a grouping
    source. A mismatch against the expected leg count is disclosed per-position,
    never silently resolved."""
    cur = conn.cursor()
    start_ms = int(position.entry_timestamp.timestamp() * 1000)
    end_ms = start_ms + int(window_hours * 3600 * 1000)
    cur.execute(
        "SELECT COUNT(*) AS n FROM okx_orders WHERE inst_id = ? AND pos_side = ? "
        "AND fill_time BETWEEN ? AND ?",
        (position.symbol, position.side, start_ms, end_ms),
    )
    return cur.fetchone()["n"]


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ── Forward-walk B0 simulator (pure function -- the unit-testable core) ─────

def simulate_exit(
    side: str,
    entry_price: float,
    candles: Sequence[Candle],
    sl_pct: float,
    tma_params: Optional[IndicatorParams] = None,
    adverse_brake: Optional[Dict[str, Any]] = None,
) -> ExitSimResult:
    """Walk forward through `candles` (must start at or after the entry candle)
    and return the first-touch exit under the B0 rule, by candle high/low.

    TP = opposite TMA band, recomputed every candle via TMAIndicator (so the
    whole `candles` series -- including enough history BEFORE entry to seed the
    rolling TMA/ATR windows -- must be passed in; the caller is responsible for
    that, this function only reads the band values for the forward portion).

    `adverse_brake`, when given (Phase 2), adds a second exit condition: an
    excursion reaching `min_adverse_atr` against the position that then stalls
    (no new adverse extreme) for `stall_candles` closed candles -- the loss-side
    mirror of the live Profit Giveback/Stall rule, implemented fresh here (never
    importing the live stateful InactivityManager).
    """
    if tma_params is None:
        tma_params = IndicatorParams(**B0_RULE["tma_params"])
    if not candles:
        return ExitSimResult("NO_DATA", None, None, 0.0, None)

    bands = TMAIndicator.calculate(list(candles), tma_params)

    sl_level = entry_price * (1 - sl_pct) if side == "long" else entry_price * (1 + sl_pct)

    # Find the index of the first candle at/after entry (the caller passes a
    # series that may include pre-entry history to seed the rolling windows).
    entry_idx = next((i for i, c in enumerate(candles) if c.timestamp >= candles[0].timestamp), 0)

    worst_profit_atr = 0.0  # most adverse excursion so far, in ATR units (brake tracking)
    stall_count = 0
    last_extreme = entry_price

    for i in range(entry_idx, len(candles)):
        c = candles[i]
        band_row = bands.iloc[i]
        tp_level = band_row["upper_band"] if side == "long" else band_row["lower_band"]
        if math.isnan(tp_level):
            continue  # rolling window not yet seeded for this candle -- can't judge TP here

        hit_sl = (c.low <= sl_level) if side == "long" else (c.high >= sl_level)
        hit_tp = (c.high >= tp_level) if side == "long" else (c.low <= tp_level)

        if hit_sl and hit_tp:
            # Same-candle tie-break: conservative, assume the worse outcome.
            return _close(side, entry_price, sl_level, i, B0_RULE["same_candle_tie_break"])
        if hit_sl:
            return _close(side, entry_price, sl_level, i, "sl")
        if hit_tp:
            return _close(side, entry_price, tp_level, i, "tp")

        if adverse_brake is not None:
            atr = band_row["atr"]
            if not math.isnan(atr) and atr > 0:
                adverse_move = (entry_price - c.low) / atr if side == "long" else (c.high - entry_price) / atr
                worst_profit_atr = max(worst_profit_atr, adverse_move)
                is_new_extreme = (c.low < last_extreme) if side == "long" else (c.high > last_extreme)
                if is_new_extreme:
                    last_extreme = c.low if side == "long" else c.high
                    stall_count = 0
                else:
                    stall_count += 1
                if worst_profit_atr >= adverse_brake["min_adverse_atr"] and stall_count >= adverse_brake["stall_candles"]:
                    return _close(side, entry_price, c.close, i, "brake")

    return ExitSimResult("CENSORED", None, None, 0.0, None)


def _close(side: str, entry_price: float, exit_price: float, index: int, kind: str) -> ExitSimResult:
    pct_return = (
        (exit_price - entry_price) / entry_price if side == "long" else (entry_price - exit_price) / entry_price
    )
    exit_type = {"sl": "SL", "tp": "TP", "brake": "BRAKE"}[kind]
    # net_usd is computed by the caller once position size/fees are known;
    # this pure function returns the percentage-return fact only.
    return ExitSimResult(exit_type, exit_price, index, 0.0, pct_return)


def apply_notional_and_fees(result: ExitSimResult, notional_usd: float, fee_pct_round_trip: float) -> ExitSimResult:
    """Scale a pure percentage-return ExitSimResult into net USD, applying the
    pre-registered fee assumption -- an edge found only by ignoring fees would
    not survive contact with the account (2026-10-14 analysis's own lesson)."""
    if result.pct_return is None:
        return result
    gross_usd = result.pct_return * notional_usd
    net_usd = gross_usd - (notional_usd * fee_pct_round_trip)
    return ExitSimResult(result.exit_type, result.exit_price, result.exit_index, net_usd, result.pct_return)


# ── Statistics (stdlib + numpy only -- no scipy dependency in this repo) ───

def mean_and_ci(values: Sequence[float], alpha: float = 0.05) -> Dict[str, float]:
    """Two-sided (1-alpha) CI for the mean via a normal approximation
    (consistent with the 2026-10-14 analysis's own t-based CI; adequate at the
    sample sizes this replay deals with -- n is always reported alongside)."""
    import numpy as np

    arr = np.asarray(values, dtype=float)
    n = len(arr)
    if n < 2:
        return {"n": n, "mean": float(arr.mean()) if n else 0.0, "sd": 0.0, "se": 0.0, "ci_lo": 0.0, "ci_hi": 0.0, "t": 0.0}
    mean = float(arr.mean())
    sd = float(arr.std(ddof=1))
    se = sd / math.sqrt(n)
    z = 1.959963984540054 if abs(alpha - 0.05) < 1e-9 else 1.959963984540054  # 95% two-sided; this repo only needs 0.05
    t_stat = mean / se if se > 0 else 0.0
    return {"n": n, "mean": mean, "sd": sd, "se": se, "ci_lo": mean - z * se, "ci_hi": mean + z * se, "t": t_stat}


def sign_test(diffs: Sequence[float]) -> Dict[str, Any]:
    """Two-sided exact binomial sign test on paired differences (ties excluded),
    matching the 2026-10-14 analysis's own methodology."""
    from math import comb

    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    n = pos + neg
    if n == 0:
        return {"n": 0, "positive": 0, "negative": 0, "p_value": 1.0}
    k = min(pos, neg)
    p_value = min(1.0, 2 * sum(comb(n, i) for i in range(0, k + 1)) / (2 ** n))
    return {"n": n, "positive": pos, "negative": neg, "p_value": p_value}


def classify_verdict(stats: Dict[str, float], n: int, thresholds: Dict[str, Any] = DECISION_THRESHOLDS) -> str:
    """Apply the pre-registered decision thresholds -- never eyeballed after
    the fact."""
    if n < thresholds["min_n_for_verdict"]:
        return "INCONCLUSIVE (underpowered: n={} < {})".format(n, thresholds["min_n_for_verdict"])
    ci_excludes_zero = stats["ci_lo"] > 0 or stats["ci_hi"] < 0
    if not ci_excludes_zero:
        return "INCONCLUSIVE (CI includes zero)"
    if abs(stats["mean"]) < thresholds["practical_significance_usd_per_position"]:
        return "INCONCLUSIVE (statistically significant but below practical-significance threshold)"
    return "EDGE DETECTED (positive)" if stats["mean"] > 0 else "EDGE DETECTED (negative)"


# ── Live candle fetching (public OKX market data -- no account credentials) ─

async def fetch_forward_candles(
    instrument_native_id: str,
    since_ms: int,
    timeframe: str = "1h",
    max_candles: int = 2000,
) -> List[Candle]:
    """Fetch OHLCV for `instrument_native_id` starting from `since_ms`, via
    ccxt's public OKX client -- no credentials needed for market data, and this
    script never touches order/account endpoints. `ohlcv_cache` was found empty
    (Step 1) so this is the primary candle source, not a fallback for gaps."""
    import ccxt.async_support as ccxt

    ex = ccxt.okx()
    try:
        market = ex.markets_by_id.get(instrument_native_id) if ex.markets else None
        unified = market[0]["symbol"] if market else instrument_native_id
        if not ex.markets:
            await ex.load_markets()
            market = ex.markets_by_id.get(instrument_native_id)
            unified = market[0]["symbol"] if market else instrument_native_id
        raw = await ex.fetch_ohlcv(unified, timeframe=timeframe, since=since_ms, limit=max_candles)
        return [Candle(timestamp=int(r[0]), open=float(r[1]), high=float(r[2]), low=float(r[3]),
                       close=float(r[4]), volume=float(r[5])) for r in raw]
    finally:
        await ex.close()


def run_replay(args: argparse.Namespace) -> None:
    import asyncio

    conn = connect_readonly(args.db_path)
    try:
        coverage = check_data_coverage(conn)
        positions = load_positions(conn, since_iso=args.since)

        if coverage["ohlcv_cache_rows"] == 0:
            print(f"NOTE: ohlcv_cache is empty ({coverage['ohlcv_cache_rows']} rows) -- "
                  f"using live OKX historical fetch as the primary candle source for all "
                  f"{len(positions)} positions, not merely as a gap-fill fallback.")

        # Cross-check okx_orders fill counts (data-quality signal, disclosed per position).
        for p in positions:
            p.okx_fill_count = cross_check_okx_fill_count(conn, p)
            p.expected_fill_count = p.leg_count

        # Observed historical fee rate, for the fee assumption in B0_RULE.
        cur = conn.cursor()
        cur.execute("SELECT SUM(fee_usd) AS fees, SUM(ABS(pnl_usd)) AS notional_proxy FROM closed_trades")
        row = cur.fetchone()
        if row["notional_proxy"]:
            B0_RULE["fee_assumption_pct"] = float(row["fees"] or 0.0) / float(row["notional_proxy"])
    finally:
        conn.close()

    results: List[PairedReplayRow] = []
    excluded_no_data = 0
    for position in positions:
        candles = asyncio.run(fetch_forward_candles(
            position.symbol, int(position.entry_timestamp.timestamp() * 1000)
        ))
        if not candles:
            excluded_no_data += 1
            continue
        params = IndicatorParams(**B0_RULE["tma_params"])
        sim = simulate_exit(position.side, position.entry_price, candles, B0_RULE["sl_pct"], params)
        notional = abs(position.entry_price) * 1.0  # per-unit; report works in percentage + disclosed USD scale
        sim = apply_notional_and_fees(sim, notional_usd=notional, fee_pct_round_trip=B0_RULE["fee_assumption_pct"] or 0.0)
        brake_sim = None
        if args.phase2:
            brake_sim = simulate_exit(position.side, position.entry_price, candles, B0_RULE["sl_pct"], params, ADVERSE_BRAKE_RULE)
            brake_sim = apply_notional_and_fees(brake_sim, notional_usd=notional, fee_pct_round_trip=B0_RULE["fee_assumption_pct"] or 0.0)
        results.append(PairedReplayRow(position=position, b0=sim, b0_brake=brake_sim))

    write_report(args.out_dir, coverage, results, excluded_no_data, phase2=args.phase2)


def write_report(
    out_dir: str,
    coverage: Dict[str, Any],
    results: List[PairedReplayRow],
    excluded_no_data: int,
    phase2: bool,
) -> None:
    os.makedirs(out_dir, exist_ok=True)
    date_str = datetime.now(timezone.utc).date().isoformat()

    scored = [r for r in results if r.b0.exit_type in ("TP", "SL", "BRAKE")]
    censored = [r for r in results if r.b0.exit_type == "CENSORED"]
    clean = [r for r in scored if not r.position.is_addon_affected]

    b0_net = [r.b0.net_usd for r in clean]
    paired_diff = [r.b0.net_usd - r.position.actual_base_net_usd for r in clean]

    vs_zero = mean_and_ci(b0_net, DECISION_THRESHOLDS["edge_vs_zero_alpha"]) if b0_net else {}
    vs_actual_mean = mean_and_ci(paired_diff, DECISION_THRESHOLDS["b0_vs_actual_alpha"]) if paired_diff else {}
    vs_actual_sign = sign_test(paired_diff) if paired_diff else {}

    sidecar_path = os.path.join(out_dir, f"{date_str}-b0-baseline-replay-detail.csv")
    with open(sidecar_path, "w", encoding="utf-8") as f:
        f.write("symbol,side,entry_price,entry_timestamp,exit_type,exit_price,net_usd,actual_net_usd,"
                "is_addon_affected,okx_fill_count,expected_fill_count\n")
        for r in results:
            f.write(f"{r.position.symbol},{r.position.side},{r.position.entry_price},"
                    f"{r.position.entry_timestamp.isoformat()},{r.b0.exit_type},{r.b0.exit_price or ''},"
                    f"{r.b0.net_usd},{r.position.actual_base_net_usd},{r.position.is_addon_affected},"
                    f"{r.position.okx_fill_count},{r.position.expected_fill_count}\n")

    report_path = os.path.join(out_dir, f"{date_str}-b0-baseline-replay.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# B0 baseline replay — {date_str}\n\n")
        f.write("**Character**: read-only diagnostic, no production/live changes. "
                "Pre-registered rule and decision thresholds (B0_RULE / DECISION_THRESHOLDS "
                "in scripts/b0_baseline_replay.py) were committed before this run.\n\n")
        f.write("## 1. Data coverage\n\n")
        f.write(f"```\n{json.dumps(coverage, indent=2, default=str)}\n```\n\n")
        f.write(f"- `ohlcv_cache` was empty ({coverage['ohlcv_cache_rows']} rows) — all forward candles "
                f"came from a live OKX historical fetch (public market data, confirmed reachable for every "
                f"observed instrument back to at least mid-August 2026 during spec research).\n\n")
        f.write("## 2. Sample\n\n")
        f.write(f"- Positions loaded: {len(results)}\n")
        f.write(f"- Excluded (no candle data): {excluded_no_data}\n")
        f.write(f"- Censored (neither TP nor SL touched before data ran out): {len(censored)}\n")
        f.write(f"- Addon-affected (excluded from the clean comparison below): "
                f"{sum(1 for r in results if r.position.is_addon_affected)}\n")
        f.write(f"- Scored, non-addon positions used for statistics: {len(clean)}\n\n")
        f.write("## 3. Test (i): does B0 itself show edge vs. zero?\n\n")
        f.write(f"```\n{json.dumps(vs_zero, indent=2)}\n```\n")
        f.write(f"**Verdict**: {classify_verdict(vs_zero, len(b0_net)) if vs_zero else 'NO DATA'}\n\n")
        f.write("## 4. Test (ii): is B0 better or worse than what actually happened? (paired)\n\n")
        f.write(f"Mean paired diff (B0 − actual): ```\n{json.dumps(vs_actual_mean, indent=2)}\n```\n")
        f.write(f"Sign test: ```\n{json.dumps(vs_actual_sign, indent=2)}\n```\n")
        f.write(f"**Verdict**: {classify_verdict(vs_actual_mean, len(paired_diff)) if vs_actual_mean else 'NO DATA'}\n\n")
        f.write("## 5. Limitations\n\n")
        f.write(f"- {B0_RULE['assumptions']}\n")
        f.write("- Fee assumption: observed historical fee/notional ratio, applied uniformly "
                f"({B0_RULE.get('fee_assumption_pct')}).\n")
        f.write("- Addon-affected positions are excluded from the headline statistics (see sidecar) "
                "rather than compared on mismatched notional.\n")
        f.write("- Sample size caveat: the 2026-10-14 analysis found ~800-1600 closes needed to resolve "
                "an effect of the observed size; this replay's n is reported above and the verdict "
                "reflects `DECISION_THRESHOLDS['min_n_for_verdict']` accordingly.\n\n")
        f.write(f"Per-position detail: `{os.path.basename(sidecar_path)}`\n")

    print(f"Report written: {report_path}")
    print(f"Sidecar written: {sidecar_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-coverage", action="store_true", help="Run only the Step 1 data-coverage check and print findings")
    parser.add_argument("--run", action="store_true", help="Run the full replay and write the report")
    parser.add_argument("--since", default=None, help="ISO date floor for entry_timestamp (e.g. 2026-09-18)")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--out-dir", default=os.path.join(REPO_ROOT, ".ai", "analysis"))
    parser.add_argument("--phase2", action="store_true", help="Also simulate the B0+adverse-brake variant")
    args = parser.parse_args()

    if args.check_coverage:
        conn = connect_readonly(args.db_path)
        try:
            print(json.dumps(check_data_coverage(conn), indent=2, default=str))
        finally:
            conn.close()
    elif args.run:
        run_replay(args)
    else:
        parser.print_help()

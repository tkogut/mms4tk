# B0 baseline replay — 2026-10-22

**Character**: read-only diagnostic, no production/live changes. Pre-registered rule and decision thresholds (B0_RULE / DECISION_THRESHOLDS in scripts/b0_baseline_replay.py) were committed before this run.

## 1. Data coverage

```
{
  "ohlcv_cache_rows": 0,
  "cycle_logs": {
    "min": "2026-08-14T00:00:04.849821+00:00",
    "max": "2026-10-22T16:12:05.952417+00:00",
    "n": 1258833
  },
  "closed_trades": {
    "min": "2026-08-15T23:15:10.240000+00:00",
    "max": "2026-10-22T14:05:49.520000+00:00",
    "n": 619
  },
  "okx_orders": {
    "min": 1786739993646,
    "max": 1792677949520,
    "n": 1093
  },
  "closed_trades_entry_timestamp_coverage": {
    "total": 619,
    "with_entry_timestamp": 475,
    "fraction": 0.7673667205169629
  }
}
```

- `ohlcv_cache` was empty (0 rows) — all forward candles came from a live OKX historical fetch (public market data, confirmed reachable for every observed instrument back to at least mid-August 2026 during spec research).

## 2. Sample

- Positions loaded: 226
- Excluded (no derivable notional — e.g. every leg closed at exact breakeven): 15
- Excluded (no candle data from OKX): 0
- Censored (neither TP nor SL touched before data ran out): 4
- Addon-affected (excluded from the clean comparison below): 0
- Scored, non-addon positions used for statistics: 222

## 3. Test (i): does B0 itself show edge vs. zero?

```
{
  "n": 222,
  "mean": -0.28416082917251434,
  "sd": 3.6987125319276664,
  "se": 0.24824133122992487,
  "ci_lo": -0.7707048978574452,
  "ci_hi": 0.20238323951241655,
  "t": -1.1446958802735403
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=222 < 800)

## 4. Test (ii): is B0 better or worse than what actually happened? (paired)

Mean paired diff (B0 − actual): ```
{
  "n": 222,
  "mean": -0.14496397990253387,
  "sd": 3.61782394276668,
  "se": 0.24281244458861329,
  "ci_lo": -0.6208676262943434,
  "ci_hi": 0.33093966648927575,
  "t": -0.5970203880947705
}
```
Sign test: ```
{
  "n": 222,
  "positive": 153,
  "negative": 69,
  "p_value": 1.732287384861425e-08
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=222 < 800)

## 5. Phase 2 — B0+adverse-brake variant (three-way comparison)

Scored positions under the brake variant: 222

### (i) B0+brake vs. zero

```
{
  "n": 222,
  "mean": -0.1320969420260502,
  "sd": 1.6889587978335305,
  "se": 0.11335549241729731,
  "ci_lo": -0.3542696246137561,
  "ci_hi": 0.09007574056165571,
  "t": -1.165333405634724
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=222 < 800)

### (ii) B0+brake vs. actual (paired)

Mean paired diff: ```
{
  "n": 222,
  "mean": 0.007099907243930254,
  "sd": 1.573332435739359,
  "se": 0.10559515911111006,
  "ci_lo": -0.199862801555622,
  "ci_hi": 0.2140626160434825,
  "t": 0.06723705237717897
}
```
Sign test: ```
{
  "n": 222,
  "positive": 133,
  "negative": 89,
  "p_value": 0.003803471785645223
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=222 < 800)

### (iii) B0+brake vs. plain B0 (paired) — does the mirrored loss-side brake help on top of B0 itself?

```
{
  "n": 222,
  "mean": 0.15206388714646413,
  "sd": 2.4302931847840608,
  "se": 0.1631105986913198,
  "ci_lo": -0.16762701178528877,
  "ci_hi": 0.471754786078217,
  "t": 0.9322747164593447
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=222 < 800)

**Variant-specific recommendation**: n=222 is far below the 800-close threshold this analysis uses for any verdict — there is not yet enough data to recommend pursuing the adverse-brake rule as a real (non-simulated) change. This does not contradict Phase 1's freeze recommendation below; both point the same direction (collect more data before changing the exit-rule layer further).

## 6. Limitations

- SL% and TMA params are assumed constant across the whole replay window (today's production defaults) -- system_settings has no historical log, so per-symbol or time-varying overrides cannot be reconstructed. Disclosed as a limitation, not silently assumed away.
- Fee assumption: observed historical fee/notional ratio, applied uniformly (0.0014956453044963985).
- Notional per position is derived from its largest leg's own realized pnl_usd and pct price move (closed_trades has no reliable, consistently-scaled contracts/amount field this script could otherwise convert to USD without knowing each instrument's contract multiplier) — this reuses real, already-correct data rather than guessing a multiplier, but is itself an approximation for positions whose size changed mid-life (e.g. a SPLIT_50 partial close).
- **`position_addons` caveat (found during this run, not assumed)**: it is `PRIMARY KEY (symbol, side)` — the same current-state-only shape as `position_snapshots` — so it can only ever flag the MOST RECENT addon-affected position per (symbol, side), never a historical one already superseded by a later position on the same pair. The 'Addon-affected: 0' count above very likely undercounts real historical addons (a known one exists in the 2026-10-15 XAG incident, outside what this table can still see) — this is a data-availability limit of the source table, not evidence addons didn't happen.
- Addon-affected positions are excluded from the headline statistics (see sidecar) rather than compared on mismatched notional.
- Sample size caveat: the 2026-10-14 analysis found ~800-1600 closes needed to resolve an effect of the observed size; this replay's n is reported above and the verdict reflects `DECISION_THRESHOLDS['min_n_for_verdict']` accordingly.

## 7. Recommendation

With n=222 (far below the 800-close threshold used throughout this analysis), neither test above can be read as a verdict — both report INCONCLUSIVE by construction, not because the effect is absent. The recorded point estimates are directionally consistent with prior findings (no demonstrated edge either way) but prove nothing on their own.

**Recommendation** (extending `.ai/analysis/2026-10-14-sl-loss-attribution.md`'s own stop/go criteria, not inventing new ones): freeze new/changed entry, exit, and filter rules and default flips while a forward confirmation sample accumulates toward 800-1600 closes. Bug fixes, safety fixes, and risk-sizing changes (per-position USD risk cap, BTC+ETH correlated-exposure cap, leverage) are explicitly exempt — those act on the STOP-LOSS tail directly and were independently ranked the highest-value lever regardless of this replay's outcome. This is a recommendation for the repository owner to accept or reject, not something this script enforces.

Per-position detail: `2026-10-22-b0-baseline-replay-detail.csv`

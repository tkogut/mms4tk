# B0 baseline replay — 2026-10-22

**Character**: read-only diagnostic, no production/live changes. Pre-registered rule and decision thresholds (B0_RULE / DECISION_THRESHOLDS in scripts/b0_baseline_replay.py) were committed before this run.

## 1. Data coverage

```
{
  "ohlcv_cache_rows": 0,
  "cycle_logs": {
    "min": "2026-08-14T00:00:04.849821+00:00",
    "max": "2026-10-22T15:22:44.809169+00:00",
    "n": 1258151
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
- Censored (neither TP nor SL touched before data ran out): 7
- Addon-affected (excluded from the clean comparison below): 0
- Scored, non-addon positions used for statistics: 219

## 3. Test (i): does B0 itself show edge vs. zero?

```
{
  "n": 219,
  "mean": -0.20604037036013317,
  "sd": 3.939449234021144,
  "se": 0.26620330977353107,
  "ci_lo": -0.7277892700816135,
  "ci_hi": 0.31570852936134713,
  "t": -0.7739962757616322
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=219 < 800)

## 4. Test (ii): is B0 better or worse than what actually happened? (paired)

Mean paired diff (B0 − actual): ```
{
  "n": 219,
  "mean": -0.06569248221887444,
  "sd": 3.763107430091052,
  "se": 0.2542872349445354,
  "ci_lo": -0.564086304438439,
  "ci_hi": 0.43270134000069005,
  "t": -0.25833967730705454
}
```
Sign test: ```
{
  "n": 219,
  "positive": 148,
  "negative": 71,
  "p_value": 2.1314655424200623e-07
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=219 < 800)

## 5. Phase 2 — B0+adverse-brake variant (three-way comparison)

Scored positions under the brake variant: 219

### (i) B0+brake vs. zero

```
{
  "n": 219,
  "mean": -0.1154352620881938,
  "sd": 3.164082362329203,
  "se": 0.21380887205603882,
  "ci_lo": -0.5344929508931623,
  "ci_hi": 0.30362242671677464,
  "t": -0.5398993080976475
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=219 < 800)

### (ii) B0+brake vs. actual (paired)

Mean paired diff: ```
{
  "n": 219,
  "mean": 0.024912626053064952,
  "sd": 2.945387614505131,
  "se": 0.1990308504996013,
  "ci_lo": -0.3651806727385294,
  "ci_hi": 0.41500592484465926,
  "t": 0.12516967088534275
}
```
Sign test: ```
{
  "n": 219,
  "positive": 129,
  "negative": 90,
  "p_value": 0.010072669086908102
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=219 < 800)

### (iii) B0+brake vs. plain B0 (paired) — does the mirrored loss-side brake help on top of B0 itself?

```
{
  "n": 219,
  "mean": 0.09060510827193938,
  "sd": 1.537252778579243,
  "se": 0.1038779162534463,
  "ci_lo": -0.11299186637388328,
  "ci_hi": 0.29420208291776206,
  "t": 0.8722268557147094
}
```
**Verdict**: INCONCLUSIVE (underpowered: n=219 < 800)

**Variant-specific recommendation**: n=219 is far below the 800-close threshold this analysis uses for any verdict — there is not yet enough data to recommend pursuing the adverse-brake rule as a real (non-simulated) change. This does not contradict Phase 1's freeze recommendation below; both point the same direction (collect more data before changing the exit-rule layer further).

## 6. Limitations

- SL% and TMA params are assumed constant across the whole replay window (today's production defaults) -- system_settings has no historical log, so per-symbol or time-varying overrides cannot be reconstructed. Disclosed as a limitation, not silently assumed away.
- Fee assumption: observed historical fee/notional ratio, applied uniformly (0.0014956453044963985).
- Notional per position is derived from its largest leg's own realized pnl_usd and pct price move (closed_trades has no reliable, consistently-scaled contracts/amount field this script could otherwise convert to USD without knowing each instrument's contract multiplier) — this reuses real, already-correct data rather than guessing a multiplier, but is itself an approximation for positions whose size changed mid-life (e.g. a SPLIT_50 partial close).
- **`position_addons` caveat (found during this run, not assumed)**: it is `PRIMARY KEY (symbol, side)` — the same current-state-only shape as `position_snapshots` — so it can only ever flag the MOST RECENT addon-affected position per (symbol, side), never a historical one already superseded by a later position on the same pair. The 'Addon-affected: 0' count above very likely undercounts real historical addons (a known one exists in the 2026-10-15 XAG incident, outside what this table can still see) — this is a data-availability limit of the source table, not evidence addons didn't happen.
- Addon-affected positions are excluded from the headline statistics (see sidecar) rather than compared on mismatched notional.
- Sample size caveat: the 2026-10-14 analysis found ~800-1600 closes needed to resolve an effect of the observed size; this replay's n is reported above and the verdict reflects `DECISION_THRESHOLDS['min_n_for_verdict']` accordingly.

## 7. Recommendation

With n=219 (far below the 800-close threshold used throughout this analysis), neither test above can be read as a verdict — both report INCONCLUSIVE by construction, not because the effect is absent. The recorded point estimates are directionally consistent with prior findings (no demonstrated edge either way) but prove nothing on their own.

**Recommendation** (extending `.ai/analysis/2026-10-14-sl-loss-attribution.md`'s own stop/go criteria, not inventing new ones): freeze new/changed entry, exit, and filter rules and default flips while a forward confirmation sample accumulates toward 800-1600 closes. Bug fixes, safety fixes, and risk-sizing changes (per-position USD risk cap, BTC+ETH correlated-exposure cap, leverage) are explicitly exempt — those act on the STOP-LOSS tail directly and were independently ranked the highest-value lever regardless of this replay's outcome. This is a recommendation for the repository owner to accept or reject, not something this script enforces.

Per-position detail: `2026-10-22-b0-baseline-replay-detail.csv`

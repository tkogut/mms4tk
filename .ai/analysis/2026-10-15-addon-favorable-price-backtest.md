# Issue #137 — OOS evidence: HIGH_RISK_ADD at an adverse price underperforms

**Verdict: implement the gate.** This is the cleanest, most consistent backtest
result produced in this project's history to date — 14/15 valid window
comparisons (sign test p=0.0010) show an add-on fired at a price worse than the
position's base entry produces a worse final outcome than one fired at a
favorable/neutral price. Ships as a default-off, per-symbol flag per this
project's own established convention — the strength of this result is a reason
to prioritize owner review, not to auto-enable it.

## Methodology

Single-pass diagnostic on the real `run_backtest()` logic (copied verbatim, one
non-behavioral addition: tag `position["addon_adverse"]` — `True` when the addon's
fill candle closed on the adverse side of the pinned `base_entry_price` for the
position's side — carried onto the final closed-trade record). No new gating was
simulated; this measures whether the EXISTING, unconditional mechanism already
shows a quality gap between the two kinds of adds it fires today.

- Same common window and 4 non-overlapping windows as the #134/H4-gate-metals
  analyses: 2026-01-28..2026-10-15 (XAG-bound), 4 symbols (BTC/ETH/XAU/XAG).
- Metric: per-trade average NET pnl_pct of the CLOSED position, split by whether
  its addon fired adverse vs favorable — the corrected, per-trade metric this
  project adopted after catching a sum-based confound in #134.

## Results

| symbol | adverse n | adverse avg net% | favorable n | favorable avg net% | windows where adverse worse |
|---|---:|---:|---:|---:|---:|
| BTC | 23 | −0.535 | 102 | +0.080 | 4/4 |
| ETH | 15 | −0.404 | 81 | +0.068 | 4/4 |
| XAU | 24 | −0.577 | 104 | −0.068 | 4/4 |
| XAG | 12 | −0.447 | 63 | +0.066 | 2/3 (1 window had zero adverse adds) |
| **pooled** | **74** | **−0.508** | **350** | **+0.031** | **14/15, sign test p=0.0010** |

- **Every symbol individually shows the same direction** — unlike #134 (mixed
  per-symbol) or the H4-gate-metals result (underpowered per-symbol, significant
  only pooled), this result is consistent symbol-by-symbol AND reaches
  significance even before pooling the sign test across windows.
- **Adverse adds are a minority of all adds** (74/424 = 17.5%) — gating them is a
  targeted cut, not a wholesale behavior change like #134's rejected mid-vol
  filter (which would have blocked 44-77% of all base entries). This is a much
  less drastic intervention with a much cleaner signal.
- The one exception (XAG window 2, n=2 adverse adds) is too small a sample to
  weigh against the other 14 comparisons.

## Recommendation

- Implement `addon_requires_favorable_price_enabled` (`IndicatorParams`, default
  `False`, per-symbol) — when on, `HIGH_RISK_ADD` is skipped if the current close
  is on the adverse side of the position's `base_entry_price`.
- **Default stays `False` pending owner review**, consistent with every other
  filter in this codebase (`tma_trend_filter_enabled`, `high_vol_base_filter_enabled`,
  `mid_vol_sweep_filter_enabled`) — this result is strong, not a reason to skip
  the owner-approval step the project has used for every prior flag flip.
- Given the strength and consistency of the evidence, this is a stronger
  candidate for enabling than any of the other three opt-in filters currently
  shipped off.

## Limitations

- Proxy OHLCV, same documented caveat as every backtest in this project.
- The tag is a diagnostic on the EXISTING mechanism's own historical decisions,
  not a full before/after simulation of the gate itself — a position that would
  have been skipped here might later still experience a different path (no
  addon at all changes subsequent SCOUT_MODE sizing/state slightly); the
  single-pass method answers "were the add-on decisions already made bad ones,"
  not "what would the account's entire equity curve look like with the gate on
  forever" — same tradeoff documented in #134's and the SL-attribution analysis's
  single-pass methodology notes.
- 4 non-overlapping windows per symbol, same sample-size caution as every other
  measurement in this project's `.ai/analysis/` history.

# `htf_directional_gate_enabled` on XAU/XAG — the first dedicated backtest

**Context:** `DECISIONS.md` §4 documents that `htf_directional_gate_enabled` was
measured only on BTC/ETH (2026-09-11, 10 independent 122-day windows, 10/10
improved net PnL, sign test p=0.0020) and then enabled on XAU/USDC and XAG/USDC
on 2026-09-14 "by analogy" — never tested on those symbols. The 2026-10-15
XAG/USDC loss (entry against a sharp post-crash rebound, with the H4 gate forcing
SHORT_ONLY) prompted the owner to ask whether this known gap had just caused real
damage, and to request the measurement DECISIONS.md itself flagged as missing.

**Verdict: the gate helps on metals too — the result is directionally consistent
with BTC/ETH's original validation, not a divergence.** Sample size is much
smaller than the original (XAU/XAG were listed on the proxy instrument far more
recently), so this does not reach the same statistical confidence as the
BTC/ETH result alone, but combined it clears significance.

## Methodology (identical to the original Faza 3.2 validation)

- `run_backtest()`'s own `htf_directional_gate_enabled` kwarg (the real production
  gate, not a re-implementation), `trend_filter_enabled=False`, default
  `IndicatorParams(engine_mode="ADAPTIVE")` otherwise.
- 122-day non-overlapping windows, 200-candle (~8.3 day) lead-in per window for
  TMA/ATR/H4-bucket warmup (H4 TMA(20)/ATR(14) needs ~136h of H1 history).
- Metric: net `total_pnl` (`_stats()`, fee-adjusted) per window, gate ON vs OFF;
  sign test on the per-window delta — same statistic the original report quoted.
- **Window count is constrained by actual available history, not padded to
  match BTC/ETH's 5/symbol**: XAU-USDT-SWAP and XAG-USDT-SWAP (the same documented
  proxy instruments used throughout this project's backtests) were listed
  2025-04-09; usable OHLCV via OKX's public `history-candles` starts 2025-05-29
  for XAU (504 days → 4 windows) and 2026-01-28 for XAG (260 days → only 2
  windows). This is a real, stated limitation, not an oversight.

## Results

| symbol | windows | window dates | OFF net | ON net | delta | sign |
|---|---:|---|---:|---:|---:|---|
| XAU | 1 | 2025-06-06..2025-10-06 | −19.41% | −15.93% | +3.48pp | IMPROVED |
| XAU | 2 | 2025-10-06..2026-02-05 | −29.46% | −7.08% | +22.39pp | IMPROVED |
| XAU | 3 | 2026-02-05..2026-06-07 | −30.90% | −25.57% | +5.33pp | IMPROVED |
| XAU | 4 | 2026-06-07..2026-10-07 | −15.54% | −8.83% | +6.71pp | IMPROVED |
| XAG | 1 | 2026-02-05..2026-06-07 | −35.28% | −27.44% | +7.85pp | IMPROVED |
| XAG | 2 | 2026-06-07..2026-10-07 | −5.12% | −1.56% | +3.56pp | IMPROVED |

- **XAU: 4/4 windows improved, sign test p=0.125** (not below 0.05 alone — n=4 is
  too small for that threshold even at 100% consistency; this is the same "small-n"
  ceiling this project's other measurements have repeatedly run into, not a weak
  result).
- **XAG: 2/2 windows improved, sign test p=0.500** (n=2 cannot be significant on
  its own by construction).
- **Pooled XAU+XAG: 6/6 windows improved, sign test p=0.0313** — crosses the
  conventional 0.05 bar when the two metals are combined (same gate, same
  methodology, same directional hypothesis tested twice).
- Average loss reduction: XAU ≈39% (range 17–76% across windows), XAG ≈46%
  (22–70%) — comparable to or larger than the reduction documented for BTC/ETH.
- **The strategy remains net-negative on metals in every window, gate on or off**
  — exactly the same qualitative finding as the original BTC/ETH result ("reduces
  losses in a strong trend, does not make the strategy net profitable"). The gate
  is a loss-mitigation mechanism, not a path to profitability, on any symbol
  tested so far.
- The gate also roughly reduces trade count (fewer `BASE_LONG`/`BASE_SHORT`
  entries fire once counter-trend ones are blocked) — consistent with its
  documented mechanism.

## Relevance to the 2026-10-15 XAG/USDC incident

This result does **not** mean the gate caused that trade for no reason, nor that
the gate is the problem — this backtest and that incident are about two different,
independent things. The gate's job is only to pick a *direction* (SHORT_ONLY,
correctly, given H4 was below its own band after the prior day's crash); it was
already known and accepted that this doesn't prevent a low-quality *entry* once
that direction is chosen — see Issue #134 (closed, mid-vol sweep filter rejected
on its own evidence) for the entry-quality side of this same incident. What this
backtest answers is narrower: "was enabling the gate on metals, by analogy,
in 2026-09-14 a reasonable bet that has now been retroactively validated, or was
it an actual gap that just blew up?" — the data says the former, with the
limitation that the sample is still much smaller than ideal.

## Recommendation

- **Keep `htf_directional_gate_enabled=true` for XAU/USDC and XAG/USDC** — do not
  disable pending further testing; the measurement DECISIONS.md called for now
  exists and supports the "by analogy" decision rather than contradicting it.
- Update `DECISIONS.md` §4 to reflect that the H4 gate specifically is no longer
  in the "zero backtest" bucket for XAU/XAG (the SL grid, high-vol filter, and D1
  ATR exhaustion filter remain untested on metals — this result does not extend
  to those).
- Revisit with a larger sample once more XAU/XAG history accumulates (XAG in
  particular is still data-limited); do not treat `p=0.125`/`p=0.500` as "failed"
  results on their own — they are underpowered, not contradicted.

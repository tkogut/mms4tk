# Switch STALE_SPLIT_50/FORCE_CLOSE exits from market to maker (post-only) with a market fallback

- Date: 2026-10-22
- Category: feature
- Priority signal: medium — real, quantified cost recovery (fees), but does not address the account's dominant loss driver (confirmed separately)
- Risk signal: medium — touches live order execution with real money; fill-rate risk must be bounded by a fallback
- Routing: om-auto-create-pr "Switch STALE_SPLIT_50/FORCE_CLOSE position-reduction exits from market to post-only maker orders with a bounded timeout + market fallback — brief: .ai/specs/briefs/2026-10-22-maker-post-only-stale-exits.md"

## Problem

Two independent production samples show `reduce_position()` (`src/mms4tk/okx_executor.py:1211`) executes every STALE_SPLIT_50 and STALE_FORCE_CLOSE close via a guaranteed **market** order (`create_order(..., type="market")`, confirmed by reading the code and its docstring), which always pays OKX taker fees. Historical sample (504 legs to 2026-10-14): these two reasons grossed +17.14 and +12.43 USD but paid 13.60 and 12.19 USD in fees respectively — thin net positive (+3.54, +0.24). Fresh out-of-sample week (97 legs, 2026-10-15 to 2026-10-21): the same two reasons (79 closes combined) grossed only +1.30 USD against 6.85 USD in fees — **net flipped negative (−5.55 USD)**. The gross edge per STALE close (~0.02–0.07 USD) is smaller than the taker/maker fee differential, so execution cost is now eating the entire thin edge these protective exits were generating.

## Agreed direction

Add a maker (post-only) limit-order path for STALE_SPLIT_50 and STALE_FORCE_CLOSE closes specifically, with a bounded timeout (exact seconds left to the implementer, informed by this instrument's typical fill latency) that falls back to the existing market order if unfilled in time. This preserves the guarantee STALE_FORCE_CLOSE's name and purpose depend on — the position must actually exit, not merely "attempt a cheaper exit."

Explicitly rejected: pure post-only with no fallback. `reduce_position()`'s own docstring says it exists to force an exit now; a limit order that can go unfilled defeats that purpose, particularly during the kind of fast move that often follows a stagnation period.

Explicitly rejected: treating this change as a fix for the account's profitability. Arithmetic check performed before this brief was written: even full recovery of the historical fee gap (~0.07 USD/close average) across all 504 legs only reaches roughly breakeven (−34.88 + ~35 ≈ ~0), not a clearly positive result, and does nothing for the fresh week's dominant loss driver (STOP-LOSS: −22.04 of −27.80 net, 79% of the loss, from only 6 of 97 closes). This PR is a cost-reduction optimization, independent of and smaller than the SL-tail question being addressed separately (see the companion B0-replay spec brief).

## Resolved unknowns

| Question | Answer (from the conversation) |
|----------|--------------------------------|
| Should FORCE_CLOSE ever be pure post-only with no fallback? | No — bounded timeout + market fallback is required; FORCE_CLOSE's entire purpose is a guaranteed exit. |
| Does this fix overall account profitability? | No — confirmed via arithmetic it only closes the fee gap on STALE_* exits; the SL tail (dominant loss driver in both samples) is untouched and out of scope here. |
| Which exits are in scope? | STALE_SPLIT_50 and STALE_FORCE_CLOSE (the `reduce_position()` call sites behind the stagnation ladder in `inactivity_manager.py`). |
| What about BREAK-EVEN / STALE_BE_ESCAPE / TAKE-PROFIT? | Out of scope unless the implementer finds they already share the same execution path as STALE_SPLIT_50/FORCE_CLOSE — do not expand scope to them without checking first. |

## Non-goals

- Changing the stagnation-ladder trigger logic (when a position is deemed stale) — only how its resulting close is executed.
- Changing SL or TP order types/execution.
- The historical replay / frozen-baseline experiment addressing the SL tail (separate brief: `2026-10-22-historical-replay-b0-baseline.md`).
- Any change to entry-signal logic.

## Affected areas (if known)

- `src/mms4tk/okx_executor.py` — `reduce_position()` and whatever helper issues the market order inside it.
- Likely `src/mms4tk/daemon.py` call sites that invoke `reduce_position()` for STALE_SPLIT_50/FORCE_CLOSE (not yet located precisely — implementer should grep for callers before changing the shared function's signature/behavior, since other reasons may call the same function).
- Tests: existing `reduce_position`/STALE-exit test coverage should be extended for the new fallback path (fill vs. timeout-then-market), not just the happy path.

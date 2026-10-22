# Read-only historical replay of entries under a frozen B0 baseline to test whether the entry signal has measurable edge

- Date: 2026-10-22
- Category: feature
- Priority signal: high — addresses the account's dominant, repeatedly-confirmed loss driver; the underlying recommendation has been sitting unexecuted since 2026-10-14
- Risk signal: low — read-only analysis against historical data; no live trading or production behavior change
- Routing: om-spec-writing "Read-only historical replay of past entries under a frozen baseline B0 (hold to opposite Bollinger/TMA band or hard SL, no STALE/BE/addon/H4-gate machinery), positions grouped via position_snapshots/okx_orders, pre-registered metric = net USD per position — brief: .ai/specs/briefs/2026-10-22-historical-replay-b0-baseline.md"

## Problem

Two independent production samples show the account's entire realized net loss is concentrated in STOP-LOSS exits, not in the protective rule layer (STALE_SPLIT_50/FORCE_CLOSE/BREAK-EVEN):

- Historical (504 legs to 2026-10-14, `.ai/analysis/2026-10-14-sl-loss-attribution.md`): STOP-LOSS = 30 legs (6%) = −79.83 USD net, while the total account result was −34.88 USD net (everything else nets positive, ~+45). Statistically, aggregate edge is not distinguishable from zero (t≈1.5, 95% CI [−0.087, +0.090] per-close gross).
- Fresh out-of-sample (97 legs, 2026-10-15 to 2026-10-21): STOP-LOSS = 6 legs (6%) = −22.04 USD net of a −27.80 USD total (79% of the loss from 6% of the trades) — same pattern, independently reproduced.

A structured two-round adversarial debate (main thread vs. an Opus challenger subagent, 2026-10-21, full transcript in this conversation) converged on: the per-exit-reason P&L table cannot be used to clear the protective layer of responsibility, because the layer decides which trade lands in which bucket. Reading `src/mms4tk/inactivity_manager.py:371-568` directly confirmed a real asymmetry: the Profit Giveback/Stall rule (→ SPLIT_50) fires only on `profit_atr >= 0.6` (winners only); a position that moves against the entry past the stagnation-compression band has **no** inactivity-based exit at all and rides unprotected until the single hard 2% stop-loss, which arrives late (6.6–15h after entry, per the 2026-10-14 analysis). The practical effect: nobody currently knows whether the entry signal itself has directional edge, because the protective layer has removed the "middle" of the outcome distribution before anyone can observe it, and attempts to look at the unprotected TP/SL subset (51 legs, −0.81 USD/leg) are themselves selection-biased toward fast-moving entries (conceded in the debate, round 2) and should not be treated as a clean answer either.

The 2026-10-14 analysis already named the fix (recommendation #5, "Kontrfaktyka STALE_* na realnych transakcjach... jako test tezy RFC") and it has not been run in the week since — the fresh 97-leg sample reproducing the same pattern makes it overdue rather than optional.

## Agreed direction

Spec and build a **read-only** replay that re-simulates each historical entry under a frozen baseline B0:

- B0 definition: the existing band-touch entry signal, unchanged; fixed stop-loss; fixed take-profit at the opposite band/TMA target (the target picked once, before seeing any replay results — no peeking); **no** STALE_SPLIT_50/FORCE_CLOSE/BREAK-EVEN/addon/H4-directional-gate machinery; inverse-sequencing only (SCOUT_MODE's existing size reduction after a loss approximates this and may be reused as-is).
- Group legs into positions via `position_snapshots`/`okx_orders`, not `entry_timestamp` — the 2026-10-14 analysis found `entry_timestamp` unreliable before 2026-09-18 (one value spans 24–30 legs). If reliable grouping can't be extended further back, scope the replay to post-09-18 data and say so explicitly rather than silently shrinking the sample.
- Pre-register before running: the primary metric is **net USD per position** (not per leg — legs double-count a single position's outcome), the exact comparison (B0's simulated result vs. the period's actual realized result), and which outcome triggers which decision (what counts as "the layer helped," "the layer hurt," or "inconclusive, need more data").
- Include a second B0 variant that adds a symmetric adverse-move exit (time-based or ATR-based, mirroring the Profit Giveback/Stall rule's threshold but on the loss side) — this directly tests whether the asymmetry found in `inactivity_manager.py` (loss side has no brake but the hard SL) is itself a fixable gap, evaluated inside the replay as a hypothesis, not deployed live first.
- Report in the same style as the existing `.ai/analysis/` documents: facts, limitations, and recommendations sections, explicit about sample size and confidence.

Explicitly rejected: trusting the per-exit-reason P&L split as sufficient evidence the protective layer is "fine" — this was the main thread's own initial position in the debate and it did not survive the challenger's round-1 argument once checked against the code.

Explicitly rejected: using the 51-leg "pure TP/SL" subsample as a clean read of the entry signal's edge — selection-biased toward fast-moving entries (the stagnation ladder has 12 candles, default, to intervene; only entries that moved decisively before then land in this subsample). The challenger conceded this in round 2 and it should not resurface as a shortcut.

Explicitly rejected (for this spec specifically): deciding whether to freeze strategy-layer PRs while the replay runs. The debate converged on recommending a freeze covering new/changed entry/exit/filter rules and default flips (not bug fixes, safety fixes, or risk-sizing changes — those act on the SL tail directly and were ranked as the highest-value lever regardless of the replay's outcome), but that is a process decision for the repository owner, not something this spec should mandate or implement.

## Resolved unknowns

| Question | Answer (from the conversation) |
|----------|--------------------------------|
| Primary metric? | Net USD per **position**, not per leg — pre-registered before running. |
| How to group legs into positions? | Via `position_snapshots`/`okx_orders`; `entry_timestamp` is known-unreliable before 2026-09-18. Scope to post-09-18 if deeper grouping isn't achievable, and disclose the resulting sample-size reduction. |
| Does this replay need a strategy-layer freeze to be run first? | No — the replay itself needs no live trading and can run immediately against historical data. The freeze question is about what happens *after*, while a forward confirmation sample accumulates, and is the user's call, not a spec gate. |
| Should a symmetric adverse-move exit be tested? | Yes, as a second B0 variant within the same replay — it directly tests the asymmetry finding from `inactivity_manager.py`, without deploying anything live. |
| Is the 51-leg TP/SL subsample usable as a fallback answer if the replay is inconclusive? | No — it's selection-biased (fast movers only) and was explicitly retracted as a headline number during the debate. Don't fall back to it. |
| Does this spec decide whether mms4tk should "return to the simpler MasterMind-article model"? | No — that question is explicitly out of scope; this replay answers "does the entry signal have measurable edge," not "which architecture to run." The article's own published backtests (reviewed visually, `mastermindzx.pl/backtesting2025.html`) carry the identical unresolved-overfitting caveat in the author's own words, so neither side of that question is currently backed by demonstrated evidence — adopting the article's model would not inherit a proven edge either. |

## Non-goals

- Any change to live trading behavior, order execution, or production code as part of this spec/replay.
- Implementing the maker/post-only execution change for STALE exits (separate brief: `2026-10-22-maker-post-only-stale-exits.md`) — that is a cost optimization, independent of this edge question.
- Settling the freeze-vs-no-freeze process question — surface it as a recommendation in the report, let the owner decide.
- Re-litigating "should mms4tk resemble the MasterMind article's simpler system" as an architecture decision — out of scope for this spec.

## Affected areas (if known)

- A new read-only analysis script, most likely under `.ai/analysis/` or a `scripts/` location following the lineage of `.ai/analysis/2026-10-14-sl-loss-attribution.md` (exact location left to the spec/implementer).
- Reads `closed_trades`, `position_snapshots`, `okx_orders` (read-only, `mode=ro` per the prior analysis's own convention).
- No production source changes expected; if the spec process determines a live component is needed (e.g., to simulate signal generation faithfully), that must be called out explicitly as a scope increase, not assumed.

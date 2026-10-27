# Exit-Risk Rebalance: Position-Sizing Asymmetry + Stale/Giveback Layer Review

## 📝 TLDR
Production has two live findings this spec must act on without a full multi-week backtest cycle: (1) losing positions carry systematically more notional than winners (median margin proxy $68 vs $48, ~1.4x), which independently of SL/TP distance widens the dollar loss gap; (2) the stale/giveback exit layer (`inactivity_manager.py`) only protects positions that were first profitable, letting immediate losers ride to the fixed 2% hard stop untouched. The user wants both addressed now, accepts the risk of skipping a full replay validation, and wants a bounded, reversible rollout instead (feature flag + canary + kill-switch) rather than a blind full-fleet change.

## Open Questions

**Q1 (scope split — blocks PR structure).** This brief bundles at least three independently-deployable changes: (a) a position-sizing cap/rule for losing-prone setups, (b) removing or weakening the stale/giveback exit layer, (c) "other risky ideas" an Opus subagent would brainstorm with no stated boundary. Each would ship, be tested, and be rolled back independently. Split into separate specs/PRs (recommended — isolates blast radius and lets us keep (a) if (b) turns out to hurt), or ship as one combined PR?

**Q2 (direct conflict with a prior approved decision).** On 2026-10-14 you explicitly approved: *"Mechanizm wyjścia STALE/SPLIT_50 zostaje nienaruszony dla BTC i ETH (dane udowodniły, że ratuje ~42 USD)"* — based on `.ai/analysis/2026-10-14-sl-loss-attribution.md`, which shows STALE_SPLIT_50 + STALE_FORCE_CLOSE are net **positive** after fees (+$3.78) and are not the loss source; STOP-LOSS alone is ~100%+ of the net loss. Today's request asks to remove/weaken the *entire* stale/giveback layer, including STALE_SPLIT_50, for all symbols. Pick one:
  - **(a)** Keep STALE_SPLIT_50/STALE_FORCE_CLOSE untouched for BTC/ETH exactly as approved 10-14; only remove/weaken the *giveback/profit-stall/impulse-negation* rules (the ones proven in the 2026-10-26 Opus analysis to only fire after a position was first profitable) — narrower, consistent with the prior decision.
  - **(b)** Override the 10-14 decision and remove the whole layer everywhere (matches the MasterMind reference exactly, but contradicts your own already-validated data and risks giving back the $3.78/position net-positive STALE_SPLIT_50 was earning).
  - **(c)** Something else (specify).

**Q3 (replacement for backtest validation — the real ask here).** You said there's no time for test cycles. I will not ship an unvalidated change to live-money exit logic with zero safety net. Pick the guardrail package (or specify your own):
  - **(a)** Feature-flagged + canary on one lowest-blast-radius symbol only (e.g. XAU, which has had 0 recorded STOP-LOSS losses and no stale-layer dependency shown) for 48h before expanding, with an automatic kill-switch (e.g. daily-loss-limit breach reverts the flag to OFF and pages nothing but logs loudly) and a manual one-command rollback.
  - **(b)** Ship to all symbols at once behind a single kill-switch env var, monitored manually every few hours for the first 48h, manual rollback only.
  - **(c)** Ship with no flag/rollback mechanism at all, rely on `git revert` + redeploy if it goes wrong (not recommended — redeploy lag on live positions means real-money exposure during the window).

**Q4 (position-sizing asymmetry mechanism).** The $68-vs-$48 median-margin gap between losers and winners needs a concrete rule, not just "cap it." Pick one:
  - **(a)** Cap `notional_cap_usd` / addon stacking harder specifically when the position is already underwater (reduce or forbid HIGH_RISK_ADD on a red position) — directly targets "adding to losers."
  - **(b)** Flat per-symbol notional ceiling independent of position state (simpler, but doesn't target the actual asymmetry mechanism — addons on losers vs. winners — since it caps everyone equally).
  - **(c)** Investigate first (one read-only query grouping `position_addons` by whether the position was red/green at addon time) before picking a mechanism — this is the only option with evidence behind it before coding, but costs ~15 extra minutes, not days.

**Q5 (risk ceiling for "other risky ideas").** You asked Opus to propose "other risky solutions" to squeeze more profit. I will have it brainstorm, but I need a hard boundary so it doesn't propose something like disabling the hard stop-loss entirely, increasing leverage, or removing the daily-loss-limit (MasterMind's own circuit breaker) — all of which would turn "higher risk" into "unbounded risk." What's off the table, unconditionally? (Default I'll apply unless you say otherwise: leverage, the hard 2% SL floor, and the daily-loss-limit circuit breaker are never touched by this spec — only the stale/giveback *exit* layer and position-sizing are in scope.)

---
*Spec paused at the Open Questions gate per the project's spec-writing protocol — answer above (or say "use your defaults") to continue to Architecture/Design.*

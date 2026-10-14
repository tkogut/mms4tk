# Execution plan — scout_override cooldown + BTC/ETH correlation guard

Engine: om-auto-create-pr (steps: 7, --loop: no)
Branch: `fix/scout-cooldown-correlation-guard` · Base: `main` · Role: Builder (SWARM)

## Goal
Close two concrete gaps found in `.ai/analysis/2026-10-14-sl-loss-attribution.md` (§4, §6):
`scout_override` relaxes SCOUT_MODE instantly with no cooldown (Issue #129), and BTC/ETH open
same-direction base positions independently despite being highly correlated, doubling SL damage
from a single market move (Issue #130).

## Scope
- `src/mms4tk/state_manager.py`, `src/mms4tk/api.py`: cooldown gate on `relax_scout_mode()`.
- `src/mms4tk/daemon.py`: Mutual Exclusion Guard on fresh BASE entries for a configured
  correlated pair (BTC/USDC, ETH/USDC).
- Tests for both; `BACKWARD_COMPATIBILITY.md` + `CHANGELOG.md` updates (both changes alter
  production entry/exit behavior per `SDLC.md` Phase 4).

## Non-goals
- Issue #107 (SCOUT stuck too long) — untouched, opposite problem.
- Extending the correlation guard to XAU/XAG — no evidence of correlated SL there.
- Changing `SCOUT_EXIT_K`, `SCOUT_STALE_ALERT_HOURS`, or the STALE/SPLIT_50 exit mechanism
  itself — explicitly left unchanged per owner decision (BTC/ETH data proved it saves ~42 USD).

## Risks
- `scout_override`'s semantics are explicitly protected in `BACKWARD_COMPATIBILITY.md` (line 15)
  — the cooldown must not block the TP-hit or `SCOUT_EXIT_K` exit paths (they bypass
  `relax_scout_mode()` already; a regression test guards this).
- The correlation guard runs under `execute_multi_symbol_cycle`'s `asyncio.gather` concurrency —
  must reuse a lock (same pattern as the Portfolio Margin Guard) or two correlated symbols can
  race the same stale `live_positions` snapshot.

## Implementation Plan

### Phase 1: `scout_override` cooldown (#129)
1.1 Add `SCOUT_OVERRIDE_MIN_HOURS` constant + cooldown check inside `relax_scout_mode()`; thread
    `respect_cooldown` through `api.py`'s two `scout_override` call sites and `set_scout_mode()`.
1.2 Tests: cooldown blocks early override, allows after window, does not affect TP/`SCOUT_EXIT_K`
    paths, API response reflects blocked vs accepted.
1.3 `BACKWARD_COMPATIBILITY.md` + `CHANGELOG.md` + `VERSION` bump for the `scout_override`
    semantics change.

### Phase 2: BTC/ETH Mutual Exclusion Guard (#130)
2.1 Add `CORRELATED_PAIRS` constant + guard check in `daemon.py`'s fresh-BASE-entry branch,
    reusing/extending the Portfolio Margin Guard's lock.
2.2 Tests: blocks same-side correlated entry, allows opposite-side/no-position, does not affect
    HIGH_RISK_ADD or TP-close logic, XAU/XAG unaffected.
2.3 `BACKWARD_COMPATIBILITY.md` + `CHANGELOG.md` + `VERSION` bump for the new entry guard.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: scout_override cooldown (#129)

- [ ] 1.1 Cooldown gate in relax_scout_mode + api.py threading
- [ ] 1.2 Tests for cooldown behavior
- [ ] 1.3 BACKWARD_COMPATIBILITY.md / CHANGELOG.md / VERSION

### Phase 2: BTC/ETH Mutual Exclusion Guard (#130)

- [ ] 2.1 Correlation guard in daemon.py
- [ ] 2.2 Tests for correlation guard
- [ ] 2.3 BACKWARD_COMPATIBILITY.md / CHANGELOG.md / VERSION

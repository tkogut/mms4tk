# Execution plan — SCOUT_STALE alert, SCOUT visibility in API, E4 tail (#119)

Engine: om-auto-create-pr (steps: 12, --loop: no)
Branch: `feat/scout-stale` · Base: `main` @ 6276c58 · Issue: #119 (refs #107) · Role: Builder (SWARM)

## Goal

A symbol stuck in SCOUT_MODE for 24 h raises a Telegram alert (repeated every 24 h), SCOUT age/counter is visible in `/daemon/status`, and the manual SCOUT controls (`hitl_resolve`, `/settings/update`) are per-symbol and no longer silently reset the strategy profile.

## Design (owner decisions 1-4 accepted)

1. `scout_since` per symbol: set on transition into SCOUT in `set_symbol_state`, cleared on exit; restored after restart from `state_history` (earliest row of the trailing contiguous run of SCOUT rows for the symbol — new `LoggerDB.get_scout_since`).
2. Alert `SCOUT_STALE` after `SCOUT_STALE_ALERT_HOURS` (default 24, env-tunable via `_env_number`), repeated every 24 h; last-alert time persisted in `system_settings` key `scout_stale_alerted:{symbol}` (removed when SCOUT ends). Event via `HermesWebhookClient.send_telegram` path used by the existing stale-bias alert; message shows age and counter like `1/3`.
3. Check runs once per loop iteration in `run_loop` (pure observability, exceptions swallowed), same place as `_check_stale_directional_bias_alert`.
4. `plan_allocation` exposes `scout_since`, `scout_age_hours`, `scout_ok`, `scout_exit_k`.
5. E4 tail: `hitl_resolve` `directional_bias`/`risk_profile` become Optional and merged with the current profile; `scout_override` is one-shot (never persisted True); `/settings/update` `scout_mode` gets optional `symbol`.

## Non-goals

- No change to trading logic, SCOUT entry/exit rules, or DB schema (settings keys only).
- Does not modify `.agents/plans/`; Coordinator-owned docs are only touched where the SSOT rules require (MEMORY.md paragraph, BACKWARD_COMPATIBILITY.md) and flagged for ack.

## Risks

- Per-cycle check must stay cheap and must never raise into the trading loop (wrapped in try/except).
- Changing `hitl_resolve` defaults changes behavior for callers that relied on the implicit BOTH/MODERATE reset: documented in BACKWARD_COMPATIBILITY.md.
- Alert spam: persisted last-alert key + 24 h repeat; tests cover restart.

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` **including `slow`**; new tests mutation-checked; handshake validated.

## Progress

PR: #120

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: State tracking

- [x] 1.1 `LoggerDB.get_scout_since` + `scout_since` tracking/restore in `MMS4StateManager` — f88c6c2
- [x] 1.2 Expose `scout_since/scout_age_hours/scout_ok/scout_exit_k` in `plan_allocation` — f88c6c2

### Phase 2: Alert

- [x] 2.1 `SCOUT_STALE_ALERT_HOURS` + `Daemon._check_scout_stale_alert` with persisted repeat key — f88c6c2
- [x] 2.2 Call the check from `run_loop` (exception-safe) — f88c6c2

### Phase 3: E4 tail

- [x] 3.1 `hitl_resolve`: Optional + merge `directional_bias`/`risk_profile`; one-shot `scout_override` — f88c6c2
- [x] 3.2 `/settings/update` `scout_mode` with optional `symbol` — f88c6c2

### Phase 4: Tests

- [x] 4.1 Tests: scout_since restore, alert threshold/repeat/clear, plan_allocation fields — f88c6c2
- [x] 4.2 Tests: hitl_resolve merge + one-shot, settings symbol — f88c6c2

### Phase 5: Docs & release

- [x] 5.1 Update `.agents/MEMORY.md` SCOUT paragraph, `BACKWARD_COMPATIBILITY.md`, runbook note — b0f9b07
- [x] 5.2 Version bump 0.48.13 (`VERSION`, `src/mms4tk/__init__.py`, `CHANGELOG.md`) — b0f9b07

### Phase 6: Validate

- [x] 6.1 Full suite including slow: 0 failures; handshake generated and validated — b0f9b07

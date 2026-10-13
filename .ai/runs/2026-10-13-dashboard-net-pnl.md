# Execution plan — dashboard shows gross as net; KPI ratios on net (#122)

Engine: om-auto-create-pr (steps: 8, --loop: no)
Branch: `fix/dashboard-net-pnl` · Base: `main` @ 7121c97 · Issue: #122 · Role: Builder (SWARM)

## Goal

The dashboard's hero number, Realized tile, Win Rate, Profit Factor and Max Drawdown reflect results **net of fees**, so they match the real result (weekly audit 06-13.10: net −5.89 USD, win rate 27.1%, PF 0.27, MDD 7.50 — not the 75.9% / 0.99 / 4.75 the dashboard derived from gross).

## Design

1. Backend is **additive only** (`/api/v1/stats/pnl` is a protected surface): `get_kpi_summary` keeps `win_rate`, `profit_factor`, `max_drawdown*`, `average_rr`, `realized_pnl` (gross, unchanged meaning) and adds `profit_factor_net`, `average_rr_net`, `max_drawdown_net_usd`, `max_drawdown_net_pct`; `net_win_rate_pct` already exists.
2. `/api/v1/stats/pnl` forwards the new keys at top level and in `since_epoch`.
3. `AggregatedPnLCard.jsx` uses net: `totalCombinedPnl = netRealizedPnl + uPnL`, Realized tile = net (gross and fees shown in the hero sub-line), Win Rate = `net_win_rate_pct`, PF / Max DD from `*_net` with fallback to the old fields (older backend), labels say "netto".

## Non-goals

- No change to trading logic, DB schema, or the meaning of existing API keys.
- Not touching the zero-gross-row filter, `fee_usd` provenance (real vs 0.1 % estimate), all-time vs `since_epoch` choice, or `entry_price`/`reason` labelling in split pairs (see #122 "Out of scope").
- Does not modify `.agents/plans/`.

## Risks

- The UI has no test framework: verified by `npm run build` and by checking the displayed values against the audited week.
- Deploy needs both `antigravity_core` (API) and `ui_dashboard` (new bundle) rebuilt; the UI falls back to the old fields while only the UI is deployed.

## Validation

`MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` **including `slow`**; new tests mutation-checked; `npm run build` in `ui_dashboard`.

## Progress

PR: #123

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend

- [x] 1.1 `get_kpi_summary`: add `profit_factor_net`, `average_rr_net`, `max_drawdown_net_usd/_pct` — 718621e
- [x] 1.2 `/api/v1/stats/pnl`: forward the new keys (top level and `since_epoch`) — 718621e
- [x] 1.3 Tests: gross>0 / net<0 fixture, old keys unchanged, endpoint shape (mutation-checked) — 718621e

### Phase 2: Dashboard

- [x] 2.1 `AggregatedPnLCard.jsx`: net hero value, net Realized/Win Rate/PF/Max DD, labels — 718621e
- [x] 2.2 `npm run build` passes — 718621e

### Phase 3: Docs & release

- [x] 3.1 `BACKWARD_COMPATIBILITY.md` note (additive keys), version bump 0.48.14 (`VERSION`, `src/mms4tk/__init__.py`, `CHANGELOG.md`) — a969e6b

### Phase 4: Validate

- [x] 4.1 Full suite including slow: 0 failures; handshake generated and validated — a969e6b

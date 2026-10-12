# Backward Compatibility — mms4tk

Protected contract surfaces for this repository, inventoried from the actual codebase. Review skills check changes against this file; implementation skills warn when a change would violate it.

## 1. HTTP API (`src/mms4tk/api.py`)

All routes are versioned under `/api/v1/`. Treat every route below as a protected surface — renaming a route, removing a response field another consumer reads, or changing a field's type/units is breaking.

**Known external consumers**: the UI dashboard (Streamlit), the Hermes ops assistant (`scripts/hermes/*.py`), and the operator directly via `curl`/scripts used throughout this project's own history.

Key routes and why they're sensitive:
- `GET /api/v1/positions`, `GET /api/v1/state`, `GET /api/v1/daemon/status` — polled continuously by the dashboard; a shape change breaks the UI silently (no version negotiation exists).
- `POST /api/v1/emergency/killall`, `POST /api/v1/emergency/reverse`, `POST /api/v1/daemon/maintenance/{enable,disable}` — the only manual circuit breakers. `emergency_killall`'s response shape (`positions_closed`, `positions_failed`, `maintenance_mode_engaged`) was fixed in #73 specifically because its old shape was misleading; don't regress it.
- `POST /api/v1/hermes/webhook`, `POST /api/v1/hermes/hitl_resolve`, `GET /api/v1/hermes/events`, `GET /api/v1/hermes/kanban` — the Hermes ops-assistant integration surface. Breaking these breaks an external system this repo doesn't control the release cadence of.
- `POST /api/v1/strategy/profile`, `GET /api/v1/strategy/profile` — `StrategyProfile`/`StrategyProfileUpdate` schema. Adding an optional field is safe; removing one or changing `scout_override`'s semantics is not (see §4).
- `POST /api/v1/settings/update`, `GET /api/v1/settings` — the live-tunable risk parameters (`base_sl_pct` and friends) surface through here; changing a key name breaks the dashboard's settings form without warning.

**Safe change**: adding a new optional response field, a new route. **Breaking change**: removing/renaming a field or route, changing a field's unit (e.g. percent vs fraction) or type.

## 2. Database schema (`src/mms4tk/logger_db.py`, SQLite)

Tables: `closed_trades`, `cycle_logs`, `state_logs`, `state_history`, `strategy_profile`, `position_addons`, `position_snapshots`, `inactivity_tracker_state`, `tp_midline_ledger`, `symbol_configurations`, `system_settings`, `trend_filter_calibration_runs`, `ohlcv_cache`, `emergency_action_log`.

- **`closed_trades.reason`** is read by the UI's Wykaz Transakcji and by every retrospective measurement this project has done (issues #80, #92, #98, #102). Its possible values (`STOP-LOSS`, `TAKE-PROFIT`, `BREAK-EVEN`, `STALE_SPLIT_50`, `STALE_FORCE_CLOSE`, `STALE_BE_ESCAPE`, `MIDLINE_PARTIAL_TP`, `CLOSED_EXTERNAL`) are an informal enum relied on by name in analysis scripts — adding a new value is safe, renaming or repurposing an existing one is not (breaks every historical script that filters on it, e.g. `scripts/*_analysis.py`).
- **`inactivity_tracker_state`**, **`position_snapshots`** — persisted so `MMS4InactivityManager`/position tracking survive a container restart (#71, #77). Schema changes here must keep old rows loadable or add an explicit migration — a restart during a live position must not silently reset its protective state.
- **`data_collection_epoch_start`** (a key in `system_settings`, not a table) is itself a protected contract: it is the dividing line every KPI/recalibration count filters on. **Any change to exit/risk logic that can alter the outcome distribution of positions opened under the prior behavior requires bumping it** for the affected symbol(s) (precedent: #75's ratchet SL, bumped 2026-09-28). A change that only adds observability (new logging, new API/dashboard field) must **not** bump it (precedent: #79, #91). See `DECISIONS.md` §2 for the verified live epoch values and `CODE_REVIEW.md` for the review-time check.
- Adding a column is safe (SQLite `ALTER TABLE ADD COLUMN`, defaults to NULL for old rows — most read paths already use `.get(...)`/`COALESCE`). Dropping or renaming a column, or changing what a column means without a migration, is breaking.

## 3. Configuration (env vars, `MMS4_*`)

Full surface (grep `os.getenv("MMS4_...")` across `src/`): `MMS4_API_KEY`, `MMS4_BASE_SL_PCT`, `MMS4_ADDON_SL_PCT`, `MMS4_BASE_ALLOCATION`, `MMS4_ADDON_ALLOCATION`, `MMS4_MAX_MARGIN_USD`, `MMS4_TRADE_AMOUNT_USD`, `MMS4_TOTAL_MAX_PORTFOLIO_MARGIN_USD`, `MMS4_MIN_NOTIONAL_USD`, `MMS4_DAILY_DRAWDOWN_LIMIT_USD`, `MMS4_SYMBOL`, `MMS4_SYMBOL_FUTURES`, `MMS4_ENGINE_MODE`, `MMS4_ATR_METHOD`, `MMS4_ADAPTIVE_VR_HIGH`, `MMS4_ADAPTIVE_VR_LOW`, `MMS4_IMPULSE_MIN_ATR`, `MMS4_BE_THRESHOLD`, `MMS4_BE_COST_PCT`, `MMS4_SL_COOLDOWN_CANDLES`, `MMS4_TIME_LOCK_START`, `MMS4_TIME_LOCK_END`, `MMS4_LEVERAGE_{CONSERVATIVE,MODERATE,AGGRESSIVE,SCOUT}`, `MMS4_SCOUT_LEVERAGE`, `MMS4_SCOUT_SIZE_MULT`, `MMS4_MAINTENANCE_MODE`, `MMS4_DB_PATH`, `MMS4_STATE_METRICS_MIN_INTERVAL`, `MMS4_SIZING_TOLERANCE`, `MMS4_TRADE_SYNC_START_ISO`.

Renaming an env var, or changing what its *default* value means (e.g. the default risk posture), is a behavior change — treat it with the same epoch-bump test as a code change to the same parameter, since most of these are read with a hardcoded fallback that silently takes effect for anyone not setting it explicitly.

## 4. Hermes tool schemas (`src/mms4tk/hermes_tools.py`)

Defines the tool-call surface the external Hermes ops assistant invokes. A parameter rename, a changed required/optional status, or a changed return shape is a breaking change for Hermes specifically — Hermes is not part of this repo's own test suite or release process, so nothing here will catch the break automatically. Cross-check `docs/WATCHDOG_RUNBOOK.md` and `scripts/hermes/*.py` for any script that also depends on the same shapes.

## 5. Signal/risk semantics that are contracts in spirit, not just code

- **P3 V2**: the stop-loss is always anchored to `base_entry_price`, never `avgPx`; an addon (`HIGH_RISK_ADD`) never carries its own independent SL. Reverting to a per-addon SL reopens a closed, previously-buggy design (see `task.md` archive, P3 V2 history) — treat any PR doing this as a protected-contract violation requiring explicit sign-off, not just a code review comment.
- **SCOUT_MODE's documented contract** (`state_manager.py:98-99` docstring): "SCOUT_MODE -> BASE_MODE: Triggered on first Take-Profit (TP) hit." Any change that makes a symbol unable to exit SCOUT_MODE on a genuine profitable close (the #99 class of bug) is a correctness regression against this file's own stated contract, not just a new bug.
  **Extended by E3 (#112):** a symbol also leaves SCOUT_MODE after `SCOUT_EXIT_K` (default 3) consecutive clean `FORCE_CLOSE` / `MIDLINE_INVALIDATION` full closes (gross `pnl_percent` above the measured round-trip cost); a STOP_LOSS or a net-losing counting close resets the run; `FORCE_CLOSE_BE` / `BE_ESCAPE` / `CLOSED_EXTERNAL` and partial closes are neutral. This only ADDS an exit path — the first-take-profit exit above must keep working, and no change may stop a genuinely profitable full close from exiting SCOUT_MODE.

## Required path to change any of the above

1. State explicitly in the PR description which protected surface is touched and why.
2. For DB/API/env changes: note whether existing consumers (dashboard, Hermes, analysis scripts) need updating in the same PR or a coordinated follow-up.
3. For exit/risk-logic changes: state the epoch-bump decision explicitly (bump / no-bump and why) — see `CODE_REVIEW.md` and `DECISIONS.md` §2.
4. Get the strategy owner's (`tkogut`) explicit sign-off before merging a change to §5's semantics — these are closed decisions (`DECISIONS.md` §1/§5), not implementation details.

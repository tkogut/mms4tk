# 🔁 PLAN-010: Trend Filter Recalibration Mechanism (Backend + UI, per-symbol)

**Status:** DRAFT — awaiting Coordinator/user review before implementation.
**Depends on:** PLAN-008 Phase 3 (TMA Slope Trend Filter), now disabled in production
pending this mechanism (2026-08-29 — see below).

---

## 1. Problem Statement

PLAN-008 Phase 3 shipped a per-symbol TMA slope trend filter calibrated once, on a
single 90-day backtest window, using only full-window statistics. A later objective
review (2026-08-29) found the deployed parameters do **not** survive a proper
train/test-in-both-directions split: whichever half of the window a config is fit on,
it fails (or is not confirmed) on the other half, for both BTC/USDC and ETH/USDC. The
filter has been **disabled at its source of truth** (`system_settings` keys
`tma_trend_filter_enabled:{symbol}`) for both symbols as an immediate mitigation.

Two structural gaps caused this to reach production undetected:

1. **No recurring recalibration process** — the filter was tuned once, by hand, in a
   one-off scratchpad script, with no mechanism to know when live data has
   accumulated enough to re-validate or when the deployed config has gone stale.
2. **No UI visibility** — `tma_trend_filter_enabled` / `tma_slope_lookback` /
   `tma_slope_threshold` exist in `api.py`/`daemon.py` but are **not exposed anywhere
   in `SymbolsTab.jsx`**. An operator watching the dashboard cannot see the filter is
   active, what its parameters are, or that its evidence has expired — confirmed live:
   `GET /api/v1/symbols` currently reports `tma_trend_filter_enabled: false` for both
   symbols while `system_settings` (the value `daemon._get_indicator_params()` 
   actually reads) held `true` — the display and the engine had silently diverged.

This plan defines a permanent mechanism — backend service + UI panel per trading
pair in the Symbols window — to track calibration eligibility, run the validated
train/test methodology on demand, and gate re-enabling the filter behind an explicit
human decision. It does **not** re-enable the filter itself; that remains a future,
separately-approved action once this mechanism exists and a symbol becomes eligible.

---

## 2. Design Principles (non-negotiable, derived from the 2026-08-29 incident)

- **Never trust full-window stats alone.** Every calibration run MUST perform the
  train-half/test-half split in **both directions** (train H1→test H2 AND train
  H2→test H1) and only report a config as "validated" if it beats baseline on R:R
  *and* total_pnl on the held-out half, in **both** directions independently, or on
  an equivalent walk-forward scheme once more history exists.
- **The backtest harness must mirror live execution mechanics exactly**, notably
  `OKXExecutor.reduction_amount()`'s no-op behavior for `SPLIT_50` at small contract
  counts — the harness bug fixed ad hoc in the scratchpad script during the 2026-08-29
  review must be ported into a maintained module, not left as a one-off script.
- **No unattended auto-recalibration and no unattended auto-apply.** Per swarm
  governance (CLAUDE.md §1/§6): Builder implements, Coordinator/user approves.
  Running a calibration is a read-only analysis action; applying its output to
  live trading parameters is a **second, separately confirmed** action. Nothing in
  this mechanism may write to `tma_trend_filter_enabled`/`tma_slope_lookback`/
  `tma_slope_threshold` except that explicit apply action.
- **Eligibility is data-driven, not calendar-driven.** A symbol becomes eligible for
  recalibration once it has accumulated a minimum number of *clean* closed trades
  (excluding known duplicate/consolidated artifacts — see PR #11 dedupe fix), not
  after a fixed number of days. Per-symbol clean trade counts vary with each pair's
  own trading frequency.

---

## 3. Phase 1 — Data Readiness Tracking + Display-Bug Fix

**Goal:** know, per symbol, how much clean data exists and whether the dashboard is
telling the truth about the filter's live state.

### 3.1 Fix the `GET /api/v1/symbols` display divergence

`add_or_update_trading_symbol()` / the symbols-list endpoint must read
`tma_trend_filter_enabled`/`tma_slope_lookback`/`tma_slope_threshold` from the same
source `daemon._get_indicator_params()` uses (`system_settings`, qualified per
symbol), not from `configured_symbols_store` alone — the two are currently allowed to
diverge silently, which is exactly what happened. Add a regression test asserting
`GET /api/v1/symbols` and `system_settings` agree for these three fields after any
`POST /api/v1/symbols/update`.

### 3.2 Clean-trade counter

New helper `LoggerDB.count_clean_closed_trades(symbol) -> int`: counts closed trades
for a symbol excluding rows identified as artifacts by the same heuristics used in
the 2026-08-29 review —
  - `abs(entry_price - exit_price) < entry_price * 0.0005` (degenerate pre-fix rows),
  - rows already known to belong to a duplicate-consolidation chain (reuse the
    connected-components detection used for the historical 15-chain cleanup, promoted
    from a one-off script into `logger_db.py` as a named, tested function),
  - rows inside a manually-flagged bug cluster window (see `excluded_trade_ids`
    consideration in §6 — out of scope for Phase 1, tracked separately).

### 3.3 New read endpoint: `GET /api/v1/symbols/{symbol}/recalibration-status`

Returns:
```json
{
  "symbol": "BTC/USDC",
  "trend_filter_enabled": false,
  "current_params": {"lookback": 3, "threshold": 0.002},
  "clean_trade_count": 81,
  "eligibility_threshold": 175,
  "eligible": false,
  "last_calibration_run_at": "2026-08-29T11:40:00Z",
  "last_calibration_verdict": "OVERFIT_REJECTED"
}
```
`eligibility_threshold` defaults to 175 (midpoint of the agreed 150–200 range),
overridable per symbol via `system_settings` for symbols with very different trade
frequency.

### 3.4 Verification
- Unit tests for `count_clean_closed_trades` against fixture DBs containing known
  degenerate/duplicate patterns.
- Unit test asserting the `GET /api/v1/symbols` / `system_settings` consistency fix.

---

## 4. Phase 2 — Calibration Engine as a Maintained Backend Module

**Goal:** turn the ad hoc scratchpad scripts (`backtest_plan008_phase3_v2.py`,
`oos_train_test_v2.py`) into a tested, importable module so recalibration is
reproducible and auditable, not re-derived from scratch each time.

### 4.1 New module: `src/mms4tk/backtest_engine.py`

Ports, with tests:
- `run_backtest(...)` — candle-granularity simulation using the real
  `TMAIndicator`/`MMS4SignalEngine`/`MMS4InactivityManager`/`MMS4StateManager`
  classes (as in the Phase 3 scratchpad harness), **including the corrected
  `SPLIT_50` semantics via `OKXExecutor.reduction_amount()`** — no synthetic-always
  partial-close modeling.
- `stats(trades)` — weighted by remaining position fraction, as fixed 2026-08-29.
- `train_test_validate(symbol, candles, lookbacks, thresholds, params) -> CalibrationResult`
  — runs the grid on H1, picks the best cell passing on H1, evaluates it on H2;
  repeats train-on-H2/test-on-H1; returns both directions' verdicts plus the
  underlying stats, never just one aggregate number.

`CalibrationResult` (dataclass, JSON-serializable) fields: `symbol`, `window_start`,
`window_end`, `candidates_h1_to_h2` (chosen params + train/test stats),
`candidates_h2_to_h1`, `verdict` (`VALIDATED` only if **both** directions pass,
`OVERFIT_REJECTED` otherwise), `recommended_params` (present only if `VALIDATED`).

### 4.1a Historical OHLCV sourcing (train/test needs 90–180 days, not just recent candles)

`OKXExecutor.fetch_candles()` today makes a single `fetch_ohlcv(..., limit=N)` call —
OKX caps a single response at ~300 candles, i.e. ~12.5 days at 1h resolution. That is
not enough for a 90–180 day calibration window, so it cannot be reused as-is.

- **New method** `OKXExecutor.fetch_candles_paginated(symbol, timeframe, days) ->
  List[Candle]`: ports the pagination loop already proven in the scratchpad
  (`fetch_ohlcv_90d.py`) — walks `since` forward in `limit=300`-candle batches,
  respecting `ex.rateLimit`, until reaching "now" — into production code, as a
  tested, reusable function (not a one-off script).
- **Local cache, not a full re-fetch per run**: add an `ohlcv_cache` SQLite table
  (`symbol, timeframe, timestamp, open, high, low, close, volume`, unique on
  `(symbol, timeframe, timestamp)`). `fetch_candles_paginated` first reads the cache,
  then fetches only the missing tail (`since = last_cached_timestamp`) from OKX,
  appends, and returns the merged series. This keeps repeated `/recalibrate` calls
  fast and avoids hammering OKX's public API on every run.
- **Source is spot/swap OHLCV (ccxt symbol, e.g. `"BTC/USDC"`), not the traded XPERP
  instrument** (`BTC-USD_UM_XPERP-...`), which has no equivalent deep public OHLCV
  history via ccxt. This is the same approximation the original Phase 3 calibration
  used (spot/swap price as a proxy for XPERP mark price — the two track closely) —
  carried forward deliberately, and must stay documented as a known assumption
  wherever calibration results are shown (UI result modal, §5.2) so it is never
  mistaken for using the exact traded price series.
- No API credentials required for this path — public market data, safe to fetch even
  while the maintenance lock is engaged.

### 4.2 New endpoint: `POST /api/v1/symbols/{symbol}/recalibrate` — ASYNCHRONOUS (job + polling)

**Decision (2026-08-29): background job with polling, not a blocking synchronous
call.** Two independent reasons, both evidence-based from this session's own runs:

1. **Runtime is not small.** A 4×4 grid is not "~50 cheap simulations" — each cell is
   a full candle-by-candle simulation (~2,160–4,320 candles) through the real
   `MMS4SignalEngine`/`TMAIndicator`/`MMS4InactivityManager`/`LoggerDB` stack
   (including real SQLite I/O per run). Measured today: a 12-run split-half check
   took ~70–90s; a two-symbol, both-directions train/test grid (16 + 64 cells) took
   **over 10 minutes**, exceeding a 590s command timeout. With both-direction
   validation (§2) mandatory, a single symbol's run realistically costs tens of
   seconds to several minutes — incompatible with a synchronous HTTP request/response
   cycle (reverse proxy and browser timeouts, plus a stuck spinner UX).
2. **`api.py` and the live trading daemon share one process and one asyncio event
   loop** (`daemon = MMS4Daemon(...)` is instantiated directly in `api.py`, and
   `daemon.start()` runs in the same FastAPI lifespan event loop). A synchronous,
   CPU-bound backtest running inline — even inside an `async def` endpoint — would
   block that event loop for its full duration, stalling the live daemon's 15s
   reconciliation/self-healing-SL/signal-evaluation cycle. On a system trading real
   capital this is not acceptable at any duration, let alone minutes.

**Implementation:**
- `POST /recalibrate` validates eligibility (§3.3), inserts a `trend_filter_calibration_runs`
  row with `status='pending'`, returns `202 Accepted` + `{"job_id": ...}` immediately.
- The actual `train_test_validate` computation runs in a `ProcessPoolExecutor` (via
  `loop.run_in_executor(...)`) — a real OS process, not merely an `asyncio.create_task`
  wrapping blocking pandas/loop code, which would still starve the event loop. A
  worker updates the same DB row's `status` (`running` → `done`/`failed`) and writes
  the full `CalibrationResult` JSON on completion.
- `GET /api/v1/symbols/{symbol}/recalibrate/status/{job_id}` — polled by the UI
  every ~2–3s (§5.2) — returns `status` and, once `done`, the full result payload.
  No new infrastructure (Redis/Celery) needed — reuses the existing SQLite-first
  architecture via the `trend_filter_calibration_runs` table already planned in §4.2.
- Fetches the freshest available OHLCV covering at least the eligible window via
  `fetch_candles_paginated` (§4.1a), backed by the local `ohlcv_cache`, inside the
  worker process (never on the request-handling path).
- **Does not modify `tma_trend_filter_enabled`, `tma_slope_lookback`, or
  `tma_slope_threshold`** — applying a result remains the separate, explicit action
  in §5.2.

### 4.3 Verification
- Regression test: feed `train_test_validate` a synthetic series with a known
  planted regime-invariant edge → expect `VALIDATED`. Feed it the real 90-day BTC/ETH
  data from the 2026-08-29 review (checked into `tests/fixtures/`) → expect
  `OVERFIT_REJECTED`, guarding against silently reintroducing the original bug.
- Test that `/recalibrate` 409s when `clean_trade_count < eligibility_threshold`.
- Test that `/recalibrate` returns `202` + `job_id` immediately (does not block on
  the calibration itself), and that `/recalibrate/status/{job_id}` transitions
  `pending → running → done`.
- Integration/perf test: assert the daemon's 15s cycle timestamp keeps advancing
  (does not stall) while a `/recalibrate` job is running — the concrete regression
  guard for reason #2 above.
- Test that no code path from `/recalibrate` writes to `system_settings`.

---

## 5. Phase 3 — UI: Symbols Window, Per-Pair Recalibration Panel

**Goal:** make the filter's state, evidence, and staleness visible where an operator
already looks — `SymbolCard` in `SymbolsTab.jsx` — and make recalibration a two-step,
explicit, auditable action from there.

### 5.1 New `SymbolCard` section: "Filtr Trendu (TMA Slope)"

Placed alongside the existing "OCHRONA STAGNACJI" section. Shows:
- Enabled/disabled state (from `GET /api/v1/symbols`, now trustworthy per §3.1) with
  the same toggle pattern as `impulseNeg`.
- Current `lookback` / `threshold` when enabled, with an "ostatnia kalibracja: brak
  potwierdzenia (OVERFIT)" badge when `last_calibration_verdict !=
  VALIDATED` — so a stale/rejected state is never silently indistinguishable from a
  genuinely validated one.
- A progress indicator: `clean_trade_count / eligibility_threshold` (e.g. "81/175
  czystych transakcji") with a disabled "🔬 Rekalibruj" button until eligible —
  matches the existing disabled-state pattern already used for inactive symbols.

### 5.2 Recalibration flow (two explicit steps, no auto-apply)

1. **"🔬 Rekalibruj"** (enabled only when eligible) → calls
   `POST /api/v1/symbols/{symbol}/recalibrate`, which returns immediately with a
   `job_id` (§4.2 — the run itself takes tens of seconds to a few minutes, not
   instant). The modal opens right away in a "🔄 Kalibracja w toku…" state and polls
   `GET /recalibrate/status/{job_id}` every ~2–3s until `status` is `done` or
   `failed`; only then does it render both train/test directions' R:R/pnl for
   baseline vs. filtered, and the verdict. Closing the modal early does not cancel
   the job — reopening "historia kalibracji" (§5.2.3) still shows it once finished.
2. If `verdict == VALIDATED`: a **separate** "✅ Zastosuj nowe parametry" button in
   that same modal, which calls the existing `POST /api/v1/symbols/update` with the
   recommended `lookback`/`threshold`/`enabled=true` — i.e. reuses Phase 1's proven
   write path, not a new one.
   If `verdict == OVERFIT_REJECTED`: only a "Zamknij" (close) button — no apply
   affordance is rendered at all, so a rejected result cannot be applied by mistake.
3. Every run (applied or not) remains visible via a small "historia kalibracji"
   expandable list per card, backed by `trend_filter_calibration_runs` (§4.2) —
   read-only, most-recent-first.

### 5.3 Verification
- Component test: apply button does not render when verdict is `OVERFIT_REJECTED`.
- Component test: 🔬 button stays disabled below eligibility threshold, with the
  correct progress text.
- Manual QA: full round trip on a symbol with synthetic eligible data in a
  non-production DB.

---

## 6. Explicitly Out of Scope for This Plan

- **`excluded_trade_ids` mechanism** for permanently excluding bug-affected
  OKX-order-id rows from `closed_trades`/KPIs (the 12-trade and 15-chain artifacts
  discussed 2026-08-29) — user chose to leave this unresolved for now
  ("Zostań tutaj"); `count_clean_closed_trades` (§3.2) uses the same heuristics only
  as a local filter for the recalibration counter, without persisting any exclusion.
- **Auto-scheduling** a periodic recalibration job (cron/Celery beat) — deliberately
  not built; recalibration stays a manual, dashboard-initiated action per §2.
- **Re-enabling the trend filter today.** This plan only builds the mechanism; a
  symbol only becomes a candidate for re-enabling once it is `eligible`, a run
  returns `VALIDATED`, and a human clicks "Zastosuj" in the UI.

---

## 7. Rollout Sequence (SDLC, mirrors PLAN-008)

```
Phase 1 (data readiness + display fix) → Phase 2 (calibration engine + API) → Phase 3 (UI)
```
Each phase: worktree → implementation → tests → Builder handshake → QA gate → PR
(not auto-merged) → explicit user approval to merge/deploy, exactly as PLAN-008
Phases 1–3 were executed.

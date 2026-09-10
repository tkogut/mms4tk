# 🧠 MMS4TK — PROJECT MEMORY & HANDOFF CONTEXT (v0.42.0)

## 📌 1. ENVIRONMENT & INFRASTRUCTURE
- **Repository**: `/home/tkogut/projects/mms4tk`
- **Stack**: Python 3.12 (FastAPI, CCXT, Pandas), SQLite3, React/Vite (UI Dashboard).
- **Exchange**: OKX Futures (EEA Multi-Collateral X-Perp: e.g. `BTC-USD_UM_XPERP-310404`, `ETH-USD_UM_XPERP-310404`, `SOL-USD_UM_XPERP-310404`).
- **VPS Server**: Hostinger Cloud (`srv1490214.hstgr.cloud`, container `antigravity_core`).
  - Production DB path: `/app/.agents/swarm/logs.db`.
  - SSH command template: `SSH_AUTH_SOCK=/tmp/ssh-rtdSFzKKo9XW/agent.2049427 ssh -o StrictHostKeyChecking=no root@srv1490214.hstgr.cloud "docker exec antigravity_core ..."`
- **Test suite**: `MMS4_API_KEY=test-api-auth-key-unit-test python3 -m pytest tests/ -v` (243/243 PASS).

> ✅ **FIXED (2026-09-10, P3 conftest hygiene)** — `tests/conftest.py` now forces
> `MMS4_DB_PATH` and every `okx_executor.MAINTENANCE_LOCK_PATHS` entry to an
> isolated per-session temp directory, unconditionally, before any `mms4tk.*`
> module is imported. Running this suite via `docker exec antigravity_core
> pytest ...` on the production container is no longer expected to touch
> `/app/.agents/swarm/logs.db` or `/app/.agents/swarm/MAINTENANCE_LOCK` — verified:
> 328/328 tests still pass with this fix in place. Kept as a documented rule
> anyway (defense in depth, and because older worktrees/branches predating this
> fix still exist): still prefer running the suite against a local checkout / CI.
>
> Original finding (confirmed live risk, since fixed): `_isolate_system_settings`
> (autouse) talked to `mms4tk.api.logger_db` — the SAME shared instance the
> running daemon uses inside that container — unconditionally forcing 5 global
> settings keys and calling `set_maintenance_lock(False)` in both setup and
> teardown, which would have silently overwritten live production config and
> released a maintenance lock an operator deliberately engaged for a live deploy.

---

## 🤖 2. SWARM GOVERNANCE & SAFETY RULES
- **Triad Roles**: Coordinator (plans & git push), Builder (atomic edits in isolated worktree `tmp/worktrees/`), Auditor (read-only verification).
- **Caveman Ultra+**: Logic-first speech, prompt compaction, commits <= 50 chars.
- **Mandatory Versioning (SSOT 4-file sync)**:
  - Every commit MUST increment version identically in:
    1. `VERSION`
    2. `src/mms4tk/__init__.py` (`__version__`)
    3. `ui_dashboard/app.py` (`APP_VERSION`)
    4. `CHANGELOG.md`
- **Zero-Loss Maintenance Directive**:
  - Lock before tests/deploy: `python3 scripts/maintenance.py --enable`
  - Release after verification: `python3 scripts/maintenance.py --disable`
- **Handshakes**: All changes require Builder handshake in `.agents/swarm/<id>_builder_handshake.json`.

---

## 📈 3. CURRENT PRODUCTION STATE (v0.42.0)
- **Live Trading**: Active on OKX X-Perp (`BTC/USDC`, `ETH/USDC`).
- **Capital Allocation**: $50 USD per symbol (BTC: 4 base / 2 addon contracts, ETH: 14 base / 6 addon contracts).
- **Trend Filter State**: **DISABLED** (`tma_trend_filter_enabled = false`) for all symbols.
- **Midline Partial TP & Inactivity Engine (PLAN-009 v2.0 / v0.42.0)**:
  - Czyste Cząstkowe TP 50% na Linii Środkowej TMA (`T1`).
  - Bezwzględne utrzymanie twardego 2.0% SL dla pozostałych 50% pozycji (zero BE / trailing stops).
  - Dedykowana tabela `tp_midline_ledger` w SQLite z odczytem **fail-closed** (`is_midline_tp_active`).
  - `MMS4InactivityManager`: *Profit Stall Gate* po 4 świecach przy uPnL >= 0.6 ATR, negacja impulsu 38.2% Fibonacciego (dla impulsu >= 0.8 ATR), bufor prowizji 0.12% dla `BE_ESCAPE`.
  - `MMS4StateManager`: blokada resetu `SCOUT_MODE` do `BASE_MODE` przy częściowych zyskach (`is_partial=True`).
- **Fee Accounting & Telegram Reporting (PLAN-011 + v0.41.1)**:
  - Round-trip OKX trading fee tracking via FIFO (`fee_usd = open_fee_prorated + close_fee`).
  - Net PnL (`pnl_net_usd = pnl_gross - fee_usd`).
  - Telegram alerts & 4-hour Swarm Digest report Net PnL, OKX Fees, Gross PnL, and Net Win Rate.
- **Epoch & Data Collection**:
  - `data_collection_epoch_start`: `2026-08-29T16:09:09Z`.
  - `clean_trade_count`: Tracking towards 175 threshold for trend filter recalibration.

---

## 📜 4. HISTORY OF DELIVERED PLANS (PLAN-001 -> v0.42.0)
- **PLAN-001 / PLAN-003**: SQLite `system_settings` as SSOT; per-symbol allocation hierarchy (`allocation_usd > budget_usd > max_margin_usd`).
- **PLAN-004**: OKX `instId` resolution (`to_native_inst_id`) for Stop Loss conditional algo orders (fixed Error 51000).
- **PLAN-005**: UI Base Stop Loss synchronization and labeling.
- **PLAN-006**: Contract size scaling in reconciliation and Take Profit PnL calculation (0.0001 BTC, 0.001 ETH).
- **PLAN-008 (v0.34.1 -> v0.36.0)**: Live indicator params propagation, Candle-Close Gate, Post-SL Cooldown.
- **v0.36.1 (PR #11)**: Deduplication in Wykaz Transakcji — cleanup of provisional `recon_*` rows upon receiving OKX trade_sync fills.
- **PLAN-010 (v0.37.0 -> v0.40.0)**: Trend filter recalibration mechanism with 2-way train/test split validation, UI Recalibration Panel, and `data_collection_epoch_start` marker.
- **PLAN-011 (v0.41.0 -> v0.41.1)**: Full Round-Trip Fee Tracking (`fee_usd`, `pnl_net_usd`) + Telegram Net PnL & Fee reports.
- **PLAN-009 v2.0 (v0.42.0)**: Midline Partial Take Profit (T1) + Pure 2.0% Hard-SL + Dynamic Inactivity Management (Profit Stall Gate 4-candle, Fibonacci 38.2% negation, 0.12% BE buffer, fail-closed `tp_midline_ledger`).

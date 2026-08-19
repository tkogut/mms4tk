# 📜 CHANGELOG — mms4tk

All notable changes to the **mms4tk** (MMS v4 Autotrading System & Hermes Agent Swarm) project will be documented in this file.

The format is based on [Keep a CHANGELOG](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 🚀 [0.30.0] - 2026-08-19

### ⚡ Hybrydowy Silnik Adaptacyjny & Fast-Path Supervision (Wariant A)
- 🧠 **Architektura Nadzoru Asynchronicznego**: Wdrożenie hybrydowego silnika adaptacyjnego w `analytics.py` bez obciążania pętli transakcyjnej ciężkimi modelami ML/LLM.
- 📈 **Volatility Ratio (VR)**: Wyliczanie wskaźnika $VR = ATR(14) / (SMA(ATR(100), 14) + \epsilon)$ z zabezpieczeniem $\epsilon = 10^{-8}$ przed dzieleniem przez zero.
  - **Filtr Ekstremalnego Trendu**: Przy $VR > MMS4\_ADAPTIVE\_VR\_HIGH$ (domyślnie `1.6`) automatycznie blokowane są sygnały `HIGH_RISK_ADD` z powodem `SKIPPED_HIGH_VOLATILITY`.
  - **Filtr Kompresji/Marazmu**: Przy $VR < MMS4\_ADAPTIVE\_VR\_LOW$ (domyślnie `0.8`) wejścia na bandach wymagają potwierdzenia knotem odrzucenia (Sweep Rejection $\ge 20\%$) z powodem `SKIPPED_COMPRESSION_NO_SWEEP`.
- ⏰ **Filtr Okna Sesyjnego US Open (Time-Lock)**: W trybie `ADAPTIVE` w godzinach $MMS4\_TIME\_LOCK\_START$ – $MMS4\_TIME\_LOCK\_END$ (domyślnie `13:30` - `14:30` UTC) blokowane są wejścia `BASE_SHORT` (`SKIPPED_TIME_LOCK`), chyba że wystąpiło ekstremalne odrzucenie płynności (knot $\ge 40\%$).
- 🎛️ **Konfiguracja w Locie (Settings API & Web UI)**:
  - Dodano `GET /api/v1/settings` oraz rozbudowano `POST /api/v1/settings/update` o walidację Pydantic i bezpieczną persystencję do plików `.env`.
  - Nowa karta w panelu UI: *Hybrydowy Silnik Adaptacyjny* z przełącznikiem trybu `CLASSIC` / `ADAPTIVE`, suwakami progów VR i polami godzin sesyjnych.
- 🧪 **Pełne pokrycie testami jednostkowymi**: Dodano testy `test_adaptive_engine_*` i `test_settings_api_adaptive_configuration` w `tests/test_adaptive_tactics.py`.

---

## 🚀 [0.29.0] - 2026-08-19

### 🛡️ Portfolio Margin Guard (Circuit Breaker)
- 🆕 **Global portfolio notional cap**: New `MMS4_TOTAL_MAX_PORTFOLIO_MARGIN_USD` env var (default `0` = disabled). When set, the daemon blocks any new BASE or HIGH_RISK_ADD entry whose addition would push the aggregate portfolio notional (across ALL symbols) above the configured limit.
- 🛡️ **Circuit breaker logic**: `check_portfolio_margin_guard()` method in `MMS4Daemon` sums all open OKX position notionals and compares against the cap with 5% tolerance. Blocks with `PORTFOLIO_GUARD_BLOCKED_*` outcome logged to cycle_logs and Hermes webhook.
- 📊 **Prevents correlated drawdown**: Addresses the risk of 4+ symbols × $500/symbol = $2000+ unguarded exposure during correlated market moves.
- 📝 **`.env.example` updated** with documentation and configuration guidance.

---

## 🚀 [0.28.3] - 2026-08-18

### 💸 Hermes Digest Realized PnL Endpoint Fix & API Alias
- 🐛 **`mms4tk_multi_symbol_digest.py` PnL Sync Fix**: Changed endpoint query in digest script from `/api/v1/stats` (which previously returned 404 and defaulted realized PnL to $0.00) to `/api/v1/stats/pnl`.
- 🔌 **FastAPI `/api/v1/stats` Alias Route**: Added `@app.get("/api/v1/stats")` route decorator pointing to `get_pnl()` alongside `/api/v1/stats/pnl` for seamless backwards compatibility.

---

## 🚀 [0.28.2] - 2026-08-17

### 🔔 Telegram Notification Bugfix & Digest PnL Addition
- 🛠️ **`telegram_queue.py` Root Cause Fix**: Replaced `asyncio.PriorityQueue` with thread-safe `stdlib.PriorityQueue` + background daemon thread. `asyncio.PriorityQueue` created in `__init__` without an event loop caused `put_nowait()` to raise exceptions silently, blocking all position open/close notifications.
- 🐛 **`mms4tk_multi_symbol_digest.py` NameError Fix**: `hermes_cli` was used in `create_kanban_digest_card()` but never defined in that scope — caused `NameError` on every hourly run, silently swallowing the Telegram dispatch.
- 💸 **Zrealizowany PnL w Digest**: Added `💸 Zrealizowany PnL (Całkowity)` field to Multi-Symbol Digest portfolio summary via `/api/v1/stats` endpoint.

---

## 🚀 [0.28.1] - 2026-08-17

### 🪙 AnalyticsTab Symbol Formatting & Pair Normalization Fix
- 🪙 **Full Pair Normalization (`normalizeSym`)**: Updated `normalizeSym` to always format symbols as full trading pairs (e.g. `BTC/USDC`, `ETH/USDC`, `SOL/USDC`, `ICP/USDC`) across trade history rows and dropdown filters, eliminating single-coin tickers (`BTC`, `ETH`).

---

## 🚀 [0.28.0] - 2026-08-17

### 🛡️ Hard Inactive Guard & Symbol Deletion Overhaul
- 🛑 **Daemon Inactive Symbol Guard Bugfix**: Fixed dict truthiness evaluation in `daemon.py` (`run_loop` & `execute_symbol_cycle`) where `{ "is_active": False }` was evaluating as truthy `True`. Added hard `SKIPPED_INACTIVE` check blocking paused/inactive symbols from trading.
- 🗑️ **Permanent Symbol Deletion Endpoint & Store Cleanup**: Updated `DELETE /api/v1/symbols/{symbol}` to purge entries from `configured_symbols_store`, SQLite `symbol_configurations` (`logger_db.delete_symbol_config`), and persistent `symbols.json`.
- 🗑️ **"Usuń Parę" Action Button & Modal**: Added dedicated "Usuń Parę" button on symbol cards in `SymbolsTab.jsx` with confirmation modal dialog overlay.

---

## 🚀 [0.27.1] - 2026-08-17

### 🛠️ AnalyticsTab Fixes & Double-Confirmation Modal
- 🔒 **Double-Confirmation Modal for Position Closing**: Added styled React Modal overlay (`confirmCloseTarget`) with full details (Symbol, Side, Notional $, uPnL $) requiring explicit user double-confirmation before triggering market close.
- 🔀 **Flawless Symbol Grouping & Filtering**: Implemented `normalizeSym` helper resolving symbol string variations (`BTC/USDC`, `BTC-USDC-SWAP`, `BTC/USDC:USDC`) across trade history records.
- 📈 **Dynamic Equity Curve Rescaling**: Filtered `pnlCurve` chronologically according to the selected timeframe range cutoff (`4H`, `1D`, `1W`, `1M`, `3M`, `6M`, `1Y`, `5Y`), scaling 1:1 with candles chart X-axis.

---

## 🚀 [0.27.0] - 2026-08-17

### 📊 Dashboard Analytics & Live Position Closing Overhaul
- 📈 **Dynamic Equity Curve Timeframe Range Scaling**: Added explicit timeframe range selector pills (`4H`, `1D`, `1W`, `1M`, `3M`, `6M`, `1Y`, `5Y`) and dynamic range title scaling (`AnalyticsTab.jsx`).
- 📋 **Newest-First Multi-Filter Trades Table**: Reversed order of closed trades table to display newest trades first (`b.timestamp - a.timestamp`). Added dynamic multi-filtering controls (Time Period: 1W, 1M default, 3M, ALL; Symbol; Side; WIN/LOSS result) and custom sorting.
- 🔴 **Live Position Close Action Button**: Added `POST /api/v1/positions/close` API endpoint and custom "Zamknij Pozycję" action button for each live position in OKX Active Positions table (`AnalyticsTab.jsx`), triggering position reduction and instant state reconciliation.

---

## 🚀 [0.26.0] - 2026-08-16

### 🛡️ Audit Remediation — Risk Engine (P0/P1/P2)
Full remediation of the 2026-08-16 quantitative audit (`docs/AUDIT_MMS4_2026-08-16.md`).

- 🔴 **P0-1 — Stop-Loss Reconciliation**: `SCOUT_MODE` was unreachable — `on_stop_loss_hit()` had no production caller, so a stop-out never reduced risk. `daemon.reconcile_positions()` now diffs live OKX positions against a SQLite snapshot (`position_snapshots`) every cycle and books the trade the moment a position disappears beyond the SL distance. Survives daemon restarts.
- 🔴 **P0-2 — Impulse Negation Loop**: `_check_impulse_negation` never latched `is_split`, re-issuing `SPLIT_50` every 15s and draining a position in ~3 cycles. The rule now latches, with an action cooldown and a `closing` flag.
- 🟠 **P1** — zero-price tracker poisoning; base entries now require `close < open`; addon guard keyed on the position (persisted, restart-safe); per-symbol signal state; `is_allowed` enforced; budget-respecting sizing; atomic TP flip; `/api/v1/settings/update` fixed; equity curve & round-trip trade pairing.
- 🟡 **P2** — non-repainting HTF/ATR, 2.5×ATR volatility zones, reachable `FORCE_CLOSE_BE`, stale-flag reset, MDD in USD **and** %, no KPI truncation, shared `normalize_symbol()`, `reduceOnly` on reductions.

### 📐 Scalable SCOUT_MODE Sizing (Variant A)
- 🎯 **Risk cut by SIZE, not leverage**: OKX rejects leverage below 1x, so the old `0.1x` was clamped to `1x` on the exchange while the notional it implied fell under the venue minimum — the entry was skipped and the symbol could never leave SCOUT_MODE.
- ⚙️ **New parameters**: `MMS4_SCOUT_LEVERAGE` (1.0), `MMS4_SCOUT_SIZE_MULT` (0.3), `MMS4_BASE_ALLOCATION` / `MMS4_ADDON_ALLOCATION`. Legacy `MMS4_LEVERAGE_SCOUT` still honored.
- 📊 **`daemon.plan_allocation()`** is the single sizing authority: `margin = max_margin_usd × allocation × size_mult`, `notional = margin × leverage`. Reduction versus BASE is `base_leverage / (SCOUT_LEVERAGE × SCOUT_SIZE_MULT)` — **10× at any capital level**, verified from $10 to $12,345.67.
- 🏦 **Dynamic exchange minimums**: `min_notional_for()` reads `limits.cost.min` (or the smallest lot × price) from CCXT instead of a hardcoded $7. Measured live: BTC $6.30, ETH $1.88, SOL $0.75, ICP $2.28.
- ⊘ **Below-minimum is a SKIP, not an ERROR**: new `BelowMinimumNotional` exception, so standing down never reads as a malfunction in the cycle log.

### 📊 Dashboard v0.26.0
- 🔌 Now consumes `GET /api/v1/daemon/status` — per-symbol sizing is read from the engine instead of a hardcoded fallback list.
- 🛡️ **SCOUT_MODE banner** listing affected pairs with their reduced notional and reduction factor; a pair only leaves this state on a take-profit or a manual release, so it is surfaced prominently.
- 📐 Symbol tiles gained a **Rozmiar (%)** field next to leverage — leverage alone no longer describes risk posture — plus the resolved base notional and an **ADD ✓** badge when the 30% add is already used.
- 📉 Max Drawdown labelled in USD, with the percentage shown when a starting balance makes it meaningful.

### 🐛 Fixes Found Post-Deploy
- 🔑 **Addon ledger keying** (`74d0e2b`): the counter was keyed on entry price, but filling an addon *moves* the position's average entry — the key stopped matching and the guard would have permitted a second addon. Caught on the live account 10 minutes after deploy. Now keyed on `(symbol, side)`, cleared only when the side goes flat.
- 📐 **Partial reduction rounding** (`8eed8ac`): `round()` closed 2 of 3 contracts (67%) for a "50%" split — worse, and irreversible, versus the 33% the audit flagged. Reverted to `floor()`; the real defect was `max(1, …)` turning a partial into a full close.
- 🧱 **Build** (`ddf7d6c`): a stray `node_modules` symlink reached the repo and broke the image build — `.gitignore` used `node_modules/`, which matches directories but not symlinks.

### 🧪 Testing
- `tests/test_audit_remediation.py` — 57 regression tests, one per audit finding.
- `tests/audit/` — 5 executable audit scripts, **61/61 assertions PASS** (25/61 before remediation), runnable inside the production container.
- 📄 Full write-up: [`docs/REFACTORING_REPORT_2026-08-16.md`](docs/REFACTORING_REPORT_2026-08-16.md).

---

## 🚀 [0.25.0] - 2026-08-15

### 💰 MMS4 Opposite TMA Band Take Profit & Position Flip
- 🎯 **Opposite Band Take Profit**: When an opposite BASE signal is triggered on M15 (e.g., `BASE_LONG` when `SHORT` is active), the open position is fully closed (`reduce_position` 100%) with Take Profit according to MMS4 rules 1.4 and 8.4.
- 🔄 **Position Flip & Execution**: Immediately following the TP closure, the bot executes the new base order in the flipped direction with standard SL 2%.
- 🔔 **Kanban & Telegram Dispatch**: Added `emit_take_profit_event` in `hermes_webhook.py` to post Take Profit results with PnL % to Hermes Kanban and Telegram.
- 🧪 **Unit Tests**: Added `tests/test_take_profit.py` verifying full TP lifecycle and state reset from `SCOUT_MODE` to `BASE_MODE`.

---

## 🚀 [0.24.4] - 2026-08-15

### 📈 Equity Curve Real Cumulative PnL Fix
- 🚫 **Removed Fake Sine Wave Mock**: Removed artificial `Math.sin(i / 5) * 2` fallback from `AnalyticsTab.jsx`.
- 📊 **Real Cumulative Progression**: Implemented authentic cumulative realized PnL calculation and clean `$0.00` zero-state baseline with formatted YAxis domain.

---

## 🚀 [0.24.3] - 2026-08-15

### 🐛 Inactivity Manager TMAIndicator Params Fix
- 🛠️ **`TMAIndicator.calculate`**: Made `params` argument optional with fallback to `IndicatorParams()`.
- ⚡ **`daemon.py`**: Explicitly passed `IndicatorParams()` in ATR calculation loop for Inactivity Manager.

---

## 🚀 [0.24.2] - 2026-08-15

### ⚡ Daemon Status Telemetry Serialization
- 📊 **`DaemonStatusResponse` Schema**: Added `inactivity_manager` and `active_symbols` fields to Pydantic model for complete telemetry in `GET /api/v1/daemon/status`.

---

## 🚀 [0.24.1] - 2026-08-15

### 🧹 KPI Clean Baseline & Trade Sync Cutoff Filter
- 🛡️ **`trade_sync.py` Cutoff Filter**: Added `MMS4_TRADE_SYNC_START_ISO` filter preventing backfilling pre-baseline corrupted historical test fills into `closed_trades`.
- 📊 **KPI Baseline Reset**: Cleared contaminated historical test records from `logs.db`, resetting KPI metrics (Win Rate, Profit Factor, MDD, R:R) to clean zero-state baseline.
- 🔄 **Active Position Preservation**: 4 live OKX positions (ICP, SOL, ETH, BTC) preserved and registered in Inactivity Manager.

---

## 🚀 [0.24.0] - 2026-08-15

### ⏰ MMS4 Stale Position Management & Inactivity Manager
- 🛡️ **New Module `inactivity_manager.py`**: Autonomous stale position detection engine implementing 4 MMS4 rules (50% impulse negation, BE escape, time-based split, two-stage ladder exit).
- ✂️ **`OKXExecutor.reduce_position()`**: Partial/full position closure via counter-direction market order in OKX hedge mode.
- 🔔 **Hermes Webhook `emit_stale_position_event()`**: Autonomous Kanban cards and Telegram alerts for every protective action (SPLIT_50, FORCE_CLOSE_BE, BE_ESCAPE, FORCE_CLOSE).
- ⚙️ **Per-Symbol Stale Config**: `stale_candles_threshold`, `stale_atr_threshold`, `impulse_negation_enabled` configurable per trading pair in Symbols UI.
- 🕯️ **Precise Candle Close Detection**: New candle timestamp tracking in daemon to avoid false stale detection on polling cycles.
- 📊 **Inactivity Status in Daemon API**: `GET /api/v1/daemon/status` now includes `inactivity_manager` telemetry.

---

## 🚀 [0.23.14] - 2026-08-15

### 🎯 Kanban DB Resolution Priority & Zero-Byte Bypass
- 🔍 **Database Candidate Priority**: Placed `/app/hermes_data/kanban.db` as primary candidate with `os.path.getsize(cand) > 1024` validation.
- ⚡ **True 1:1 Live Sync**: Guaranteed instantaneous reflection of active tasks and task archiving between Hermes CLI and Mastermind UI.

---

## 🚀 [0.23.13] - 2026-08-15

### 🚀 Live WAL + SHM Shared Volume for Real-Time Kanban Sync
- 🔄 **Live WAL Sync**: Mounted `/docker/hermes-agent/data:/app/hermes_data` to allow SQLite write-ahead-log tracking across containers.
- 🎯 **Sub-Second Kanban Reflection**: Immediate synchronization when tasks are created or archived in Hermes CLI.

---

## 🚀 [0.23.12] - 2026-08-15

### ⚡ Direct Root Execution & Immutable SQLite Sync
- 🛡️ **Container Permission Resolution**: Set `user: "0:0"` in `docker-compose.yml` so `antigravity_core` can read the host `/docker/hermes-agent/data/` volume directly.
- 🗄️ **Immutable SQLite Access**: Configured `file:///app/hermes_data/kanban.db?immutable=1` URI for read-only access to Hermes Kanban board.

---

## 🚀 [0.23.11] - 2026-08-15

### 🗂️ Hermes Directory Mount & WAL Sync
- 📁 **Full Data Directory Mount**: Mounted `/docker/hermes-agent/data:/app/hermes_data:ro` in `docker-compose.yml` to enable SQLite WAL + SHM file tracking across containers.

---

## 🚀 [0.23.10] - 2026-08-15

### 🗄️ Hermes Kanban Single File Mount Sync
- 🔗 **Direct File Mount**: Mounted `/docker/hermes-agent/data/kanban.db:/app/.agents/swarm/hermes_kanban.db:ro` in `docker-compose.yml`, bypassing directory permission restrictions.
- 🔄 **Real-Time Kanban Sync**: When tasks are created or archived in Hermes, `/api/v1/hermes/kanban` instantly mirrors the true live board.

---

## 🚀 [0.23.9] - 2026-08-15

### 🔗 Robust SQLite URI Mode for Kanban Read-Only Sync
- 🔒 **Read-Only URI Connection**: Configured `sqlite3.connect(f"file:{hermes_db}?mode=ro", uri=True)` so `antigravity_core` can read the live Hermes `kanban.db` directly without requiring write access or locks.

---

## 🚀 [0.23.8] - 2026-08-15

### 🎯 Direct Hermes Kanban.db Sync & Zero Ghost Cards
- 🔗 **Direct Kanban SQLite Mount**: Mounted Hermes container data directory (`/docker/hermes-agent/data:/app/hermes_data:ro`) into `antigravity_core` in `docker-compose.yml`.
- 🗄️ **Single Source of Truth**: Updated `/api/v1/hermes/kanban` in `api.py` to query active tasks directly from Hermes `kanban.db` (`tasks WHERE status NOT IN ('done', 'archived', 'closed')`). When Hermes board is clean, dashboard accurately shows 0 cards with perfect 1:1 synchronization.

---

## 🚀 [0.23.7] - 2026-08-15

### 🩹 Bugfix in Stats & Kanban Query
- 🐛 **KPI Variable Fix**: Fixed `total_pnl_count` variable reference in `logger_db.py`.
- 🔌 **SQLite Import**: Added top-level `sqlite3` import in `api.py` for `/api/v1/hermes/kanban`.

---

## 🚀 [0.23.6] - 2026-08-15

### 📊 KPI Accuracy, Live uPnL Card & Enhanced Kanban Query
- 📐 **Accurate KPI Statistics**: Filtered out zero-PnL entry and limit orders in `get_kpi_summary()` so Win Rate, Profit Factor, and R:R Ratio reflect only completed round-trip trades with realized PnL.
- ⚡ **Live uPnL Card**: Replaced obsolete static `Saldo BTC` card on the Monitoring Dashboard with live **Niezrealizowany PnL (uPnL)** across all active open futures positions with dynamic total notional exposure.
- 🤖 **Universal Kanban Fetch**: Updated `/api/v1/hermes/kanban` to query both active Hermes CLI tasks and SQLite `webhook_events` to ensure cards render in real-time in the UI.

---

## 🚀 [0.23.5] - 2026-08-15

### 📋 Hermes Kanban Cleanup & Actionable Task Policy
- 🧹 **Kanban Board Cleanup**: Archived 36 stale repetitive cards from the Hermes Kanban task board on VPS (`hermes kanban archive`).
- ⚡ **Eliminate Repetitive Digest Tasks**: Removed CLI task creation from hourly `mms4tk_multi_symbol_digest.py`. Informational reports are dispatched to Telegram and Dashboard Webhooks, reserving Kanban cards exclusively for actionable tasks (`SCOUT_MODE_ACTIVATED`, `HITL_REVIEW`, `RISK_ALERT`).

---

## 🚀 [0.23.4] - 2026-08-15

### 🎨 Header & Monitoring UI Layout Reorganization
- 🔝 **Header Bot Operating Mode**: Integrated the Bot Operating Mode badge (`BASE_MODE` / `SCOUT_MODE`) and global Data Refresh button directly into the top Header bar.
- 🧹 **Monitoring Tab Cleanup**: Removed the redundant large Operating Mode banner from the top of the Monitoring view.
- 🚫 **Header Clean-up**: Removed obsolete static `OKX SPOT BTC/USDC` badge from the top bar.

---

## 🚀 [0.23.3] - 2026-08-15

### 💾 Persistent SQLite Symbol & Interval Configuration
- 🗄️ **Database Persistence**: Added `symbol_configurations` table to SQLite `logger_db.py` (`logs.db`). Per-symbol allocations, base/add ratios, active states, and custom timeframes are stored in SQLite database.
- 🚫 **Immune to Git Overwrites**: Removed `symbols.json` from git tracking and added to `.gitignore`. Git updates/pulls/rebuilds will never overwrite or reset user-configured symbol timeframes.

---

## 🚀 [0.23.2] - 2026-08-15

### 🧭 Strict Directional Side Alignment & Anti-Opposite Hedging Guard
- 🛡️ **Side Direction Matching**: Updated `daemon.py` to enforce strict alignment between incoming signal side (`posSide: "long"` vs `"short"`) and live OKX position side.
- 🚫 **Opposite Hedging Guard**: `BASE_SHORT` / `BASE_LONG` orders are blocked if an opposite position is already open.
- 🚫 **Matching Direction Addon Guard**: `HIGH_RISK_ADD_SHORT` and `HIGH_RISK_ADD_LONG` orders are strictly blocked unless a matching base position in the exact same direction already exists.

---

## 🚀 [0.23.1] - 2026-08-15

### 🛡️ Robust Universal Multi-Symbol Position & Add-on Guard
- 🔒 **Exchange-Level Contract & Notional Size Check**: Fixed `daemon.py` position inspection to calculate `existing_notional` and `existing_contracts` directly from OKX CCXT position data and contract size (`contractSize` / `ctVal`).
- 🚫 **Unbreakable Restart-Resistant Addon Lock**: Prevents add-on order stacking across container restarts by checking live OKX contract counts against base order sizes (`existing_contracts >= base_order_amount + 0.5 * addon_order_amount`) and notional allocations across all trading symbols (BTC, ETH, SOL, ICP).

---

## 🚀 [0.23.0] - 2026-08-15

### 📬 Telegram Queue & Rate-Limiter Module
- 🚦 **Asynchronous Rate-Limiting & Queuing**: Implemented `TelegramQueueManager` (`src/mms4tk/telegram_queue.py`) with prioritized message queuing and minimum interval pacing (3.0s between dispatches) to eliminate Telegram notification bursts and spam.
- 🚫 **Deduplication Filter**: Added SHA-256 fingerprinting and 60-second cooldown per event/symbol key (`dedupe_key`), preventing duplicate alert floods across parallel trading cycles.
- 🧹 **Eliminated Double-Dispatch**: Streamlined `mms4tk_event_dispatcher.py` and `daemon.py` to prevent duplicate event notification sends.

### 💰 Enhanced Portfolio Digest Report
- 💵 **Real Free Available Margin**: Updated `scripts/hermes/mms4tk_multi_symbol_digest.py` to display **Wolny Dostępny Depozyt: $XX.XX USDC** (read live from OKX API) alongside used margin and total equity.

---

## 🚀 [0.22.1] - 2026-08-15

### 🐞 Fix Analytics Tab Render Crash (Black Screen)
- 🛠️ **Fixed Undefined Pagination Variables**: Defined missing `totalTrades`, `totalPages`, `validCurrentPage`, `startIndex`, `endIndex`, and `currentTrades` in `AnalyticsTab.jsx` which caused a React `ReferenceError` black screen.

---

## 🚀 [0.22.0] - 2026-08-15

### 🛡️ Double-Lock Single Addon Guard & Position Cap
- 🔒 **Double-Lock Addon Order Prevention**: Enforced strict single Add-on trade execution per position lifecycle using both in-memory set lock (`_addon_executed_symbols`) and OKX live position notional check (`existing_notional >= notional_base_usd + 0.4 * notional_addon_usd`). Completely prevents duplicate 15-second add-on order stacking.
- ⚖️ **Automatic Addon State Reset**: Automatically releases addon locks when positions are closed by SL/TP (`existing_pos is None`).

---

## 🚀 [0.21.4] - 2026-08-15

### 🐞 Fix AttributeError in Daemon High Risk Add Cycle
- 🛠️ **Fixed Daemon Cycle Exception**: Removed obsolete `MMS4State.HIGH_RISK_ADD_SHORT` enum check in `src/mms4tk/daemon.py` which threw `AttributeError: type object 'MMS4State' has no attribute 'HIGH_RISK_ADD_SHORT'` during multi-symbol daemon cycles.

---

## 🚀 [0.21.3] - 2026-08-15

### 🎯 Fix Hardcoded Version Prop in App.jsx
- 🐞 **Fixed Header Version Prop**: Resolved root cause in `App.jsx` where `<Header>` was passed literal hardcoded string `"0.16.2"` instead of dynamic `{appVersion}` state.

---

## 🚀 [0.21.2] - 2026-08-15

### ⚡ Fix Desktop PWA Caching & Nginx No-Cache Headers
- 🚫 **Nginx No-Cache Headers**: Updated `nginx.conf` to add `Cache-Control "no-cache, no-store, must-revalidate"` for `index.html`, `sw.js`, and `registerSW.js`. Stopped Nginx from serving 7-day expires on Javascript and JSON bundles.
- 🧹 **Header.jsx Cache Reset Button**: Removed stale `'0.16.2'` fallback version string in `Header.jsx` and added an interactive `RefreshCw` button next to the version badge that unregisters all PWA Service Workers, clears `caches` storage, and reloads the browser.

---

## 🚀 [0.21.1] - 2026-08-15

### 📄 Trades History Table Pagination
- 📑 **Closed Trades Pagination**: Added full pagination controls (rows-per-page selector 5/10/25/50, prev/next buttons, page counter, index range indicator) to the "Wykaz Zarejestrowanych Transakcji Rzeczywistych" table in `AnalyticsTab.jsx`.

---

## 🚀 [0.21.0] - 2026-08-15

### 📊 Live Open Positions Table in Analytics & PWA Cache Auto-Purge
- 📈 **Live Open Positions Table in Analytics**: Added a dedicated real-time open positions panel in `AnalyticsTab.jsx` rendering active OKX positions with all key parameters: symbol, side (LONG/SHORT badge), leverage (e.g. 3x), allocation (notional USD), cash margin spent (USD), entry price, current mark price, and unrealized PnL (uPnL).
- ⚡ **PWA Cache Auto-Purge**: Configured Workbox `skipWaiting: true`, `clientsClaim: true`, and `cleanupOutdatedCaches: true` in `vite.config.js` and added cache-busting timestamp `?t=...` to `/api/v1/health` API requests so the browser automatically updates PWA assets on every version release.

---

## 🚀 [0.20.2] - 2026-08-15

### 💰 Direct Top-Level OKX availEq API Parsing
- 💵 **Exact availEq Extraction**: Updated `src/mms4tk/okx_executor.py` (`fetch_balance`) to read top-level `availEq` directly from OKX V5 account balance API (`data[0]["availEq"]`). Reports exact real available margin ($53.44 USDC) instead of cash balance without math calculations.

---

## 🚀 [0.20.1] - 2026-08-15

### 💰 Direct OKX availBal API Reporting
- 💵 **Direct OKX availBal API**: Updated `src/mms4tk/okx_executor.py` (`fetch_balance`) to query `/api/v5/account/balance` directly from OKX V5 API and populate `free_usdc` with exact `availBal` (pure available free margin without calculations).
- 📊 **Updated Telegram & Dashboard Reports**: Updated Hermes multi-symbol Telegram digest (`mms4tk_multi_symbol_digest.py`) and UI Dashboard (`MonitoringTab.jsx`) to display direct OKX `availBal` ($53.28 USDC) alongside Total Equity ($83.63 USDC).

---

## 🚀 [0.20.0] - 2026-08-15

### 🛡️ Single Position Lock Guard & Duplicate Order Prevention
- 🛡️ **Single Position Guard**: Updated `src/mms4tk/daemon.py` to query live OKX positions before executing any order. If a base position already exists for a target symbol (`contracts > 0`), duplicate order execution is immediately blocked and skipped (`SKIPPED_BASE_SHORT`). Prevents continuous 15-second re-entry loops while signals remain active.

---

## 🚀 [0.19.1] - 2026-08-15

### ⚡ Hermes Kanban Optimization (Actionable Cards Only)
- ⚡ **Filtered Kanban Creation**: Updated `scripts/hermes/mms4tk_event_dispatcher.py` to stop creating noisy informational Kanban cards for `POSITION_OPENED` or `MULTI_SYMBOL_DIGEST`. Informational events are dispatched **exclusively to Telegram**, reserving Hermes Kanban cards for actionable events requiring AI analysis or Human-in-the-Loop review (`SCOUT_MODE_ACTIVATED`, `HITL_REVIEW`).

---

## 🚀 [0.19.0] - 2026-08-15

### 💾 Persistent Symbol Configuration Storage (.agents/swarm/symbols.json)
- 💾 **Persistent Timeframes & Allocations**: Migrated symbol configuration storage to persistent Docker volume path (`.agents/swarm/symbols.json`). All user-configured timeframes (e.g. `1h` for BTC/ETH, `15m` for SOL/ICP), allocations, and active states now permanently survive container updates, rebuilds, and deployments without resetting to defaults.

---

## 🚀 [0.18.2] - 2026-08-15

### ⚡ Telegram Dispatch Throttling & Rate-Limit Prevention
- ⚡ **Dispatcher Rate-Limit Throttle**: Added 1.2s sleep interval between sequential Telegram message dispatches in `scripts/hermes/mms4tk_event_dispatcher.py` to prevent Telegram API rate limits from dropping rapid multi-symbol trade alerts.

---

## 🚀 [0.18.1] - 2026-08-15

### 🐛 Telegram Position Alert Dispatch Fix
- 🐛 **Event Dispatcher Fix**: Fixed `NameError` in `scripts/hermes/mms4tk_event_dispatcher.py` when sending Telegram alerts for executed position opening events. Updated Hermes CLI call to `hermes send --to telegram` syntax to ensure instant position opening notifications land in Telegram.

---

## 🚀 [0.18.0] - 2026-08-15

### 🚀 Pure Cash Allocation Position Sizing (Leverage Multiplied Notional Target)
- 🚀 **Pure Cash Allocation Sizing**: Updated `src/mms4tk/daemon.py` order calculation so that `max_margin_usd` ($10 USD) represents **exact real cash margin spent** ($7 USD Base / $3 USD Addon). Position notional value is now computed as `base_trade_usd * leverage` ($21 USD Notional @ 3x), ensuring the user's real cash invested matches the exact 70/30 allocation regardless of leverage.

---

## 🚀 [0.17.0] - 2026-08-15

### 🚀 Live Open Positions API & Real-Time Telegram Webhook Alerts
- 🚀 **GET `/api/v1/positions` Endpoint**: Added missing `/api/v1/positions` REST endpoint in `src/mms4tk/api.py` querying OKX live open positions (`executor.fetch_positions()`). Resolves Hermes hourly digest showing 0 open positions.
- 💬 **Real-time Telegram Webhook Notifications**: Added automatic `HermesWebhookEvent` emission in `src/mms4tk/daemon.py` on order execution (`POSITION_OPENED`), delivering instant Telegram notifications when positions open.

---

## 🐛 [0.16.2] - 2026-08-14

### 🐛 OKX Futures / EEA Contract Lot Precision Fix
- 🐛 **Contract Amount Recalculation**: Fixed `InvalidOrder: okx amount must be greater than minimum amount precision of 1` error in `execute_order` (`okx_executor.py`). When symbols are mapped to OKX Futures/EEA Swap contracts (`ETH/USD:USD-310404`, `SOL/USD:USD-310404`), fractional coin quantities (e.g. `0.0037 ETH`) are now dynamically converted to integer contract quantities (e.g. `1 contract`), enabling order execution.

---

## 🚀 [0.16.1] - 2026-08-14

### 🚀 30m Timeframe Support & Independent Simulation Audit
- 🚀 **30m Timeframe Selector**: Added `30m` option to per-symbol timeframe dropdown in Web Dashboard (`SymbolsTab.jsx`) and backend symbol management.
- 🧪 **Independent Quantitative Backtest Audit**: Created `scratch/independent_strategy_simulator.py` and saved comparative audit report to `docs/mms4_independent_simulation_audit.md` demonstrating `1h` (67.5% Win Rate) and `15m` superiority over 1m/5m/30m.

---

## 🚀 [0.16.0] - 2026-08-14

### 🚀 Mastermind MMS v4 Strategy Timeframe & Signal Overhaul
- 🚀 **Per-Symbol Timeframe Selection**: Added per-symbol `timeframe` configuration (`15m` default, `1h`, `5m`, `1m`) across backend, `symbols.json`, `MMS4Daemon` loop, and React UI (`SymbolsTab.jsx`, `MonitoringTab.jsx`).
- 🎯 **TMA Outer Band Penetration Trigger**: Refined signal evaluation in `src/mms4tk/analytics.py` for `BASE_LONG` and `BASE_SHORT` to trigger reliably upon outer TMA ATR band penetration on swing timeframes.
- 📊 **Audit & Post-Implementation Documentation**: Created `/home/admin_tk/projects/mms4tk/docs/mms4_strategy_audit_report.md` and `/home/admin_tk/projects/mms4tk/docs/mms4_post_implementation_report.md`.

---

## 🌐 [0.15.8] - 2026-08-13

### 🌐 Resilient Container API URL Resolution
- 🌐 **Dynamic Base URL Probing**: Added `get_api_base_url()` to `scripts/hermes/mms4tk_multi_symbol_digest.py` probing `http://antigravity_core:8000`, `http://172.17.0.1:8501`, and `http://localhost:8501`, ensuring the script reaches `mms4tk` REST API reliably whether run inside `hermes-agent` Docker container, on VPS host, or inside core container.

---

## 💬 [0.15.7] - 2026-08-13

### 💬 Hermes Telegram Notification Integration
- 💬 **Hermes Telegram Notify**: Added fallback `hermes notify --channel telegram` dispatch inside `scripts/hermes/mms4tk_multi_symbol_digest.py` via native CLI or `docker exec hermes-agent` to deliver hourly Multi-Symbol Digest reports directly to the user's Telegram chat.

---

## 🔖 [0.15.6] - 2026-08-13

### 🔖 Dynamic Version Resolution & Webhook Fix Verification
- 🔖 **Dynamic Script Versioning**: Updated `scripts/hermes/mms4tk_multi_symbol_digest.py` to dynamically fetch the live application version from `/api/v1/health`, resolving version string mismatches.

---

## 🔔 [0.15.4] - 2026-08-13

### 🔔 Custom Webhook Event Logging REST Endpoint
- 🔔 **POST /api/v1/hermes/webhook Endpoint**: Added endpoint in `src/mms4tk/api.py` allowing custom script events (like hourly Multi-Symbol Digest cards) to post directly to `webhook_events` DB table for instant display on Dashboard Kanban & System Status.

---

## 📋 [0.15.3] - 2026-08-13

### 📋 Direct Webhook Integration for Digest Cards
- 📋 **Direct Webhook Posting**: Updated `scripts/hermes/mms4tk_multi_symbol_digest.py` to post hourly digest cards directly to `POST /api/v1/hermes/webhook`, ensuring digest reports render on Dashboard Kanban and Webhook events even when CLI binary paths differ.

---

## 📈 [0.15.2] - 2026-08-13

### 📜 Hermes Multi-Symbol Digest Script Implementation
- 📜 **Unified Multi-Symbol Digest Script**: Added `scripts/hermes/mms4tk_multi_symbol_digest.py` executable script for Hermes agent cron (`0 * * * *`). Automatically fetches active symbols and open positions, formats structured markdown report, and posts to Hermes Kanban & Telegram.

---

## 📈 [0.15.1] - 2026-08-13

### 📈 Live Open Positions Endpoint & Hermes Positions Digest Integration
- 📈 **GET /api/v1/positions Endpoint**: Added REST endpoint in `src/mms4tk/api.py` to fetch live open positions across all active trading symbols from OKX (side, contracts, entry price, mark price, notional USD, unrealized PnL, leverage, liquidation price).
- 🤖 **Hermes Open Positions OpenAPI Schema**: Added `mms4_get_positions` tool definition to `.agents/swarm/openapi.json`, allowing Hermes digest reports to render live open position tables with uPnL %.

---

## 🤖 [0.15.0] - 2026-08-13

### 🤖 Hermes Swarm Agent Multi-Symbol Integration & OpenAPI Schema
- 🤖 **OpenAPI Schema Integration**: Added `mms4_get_symbols` tool definition to `.agents/swarm/openapi.json` allowing Hermes agent to dynamically discover all configured trading symbols, allocation USD amounts, Base/Add ratios, and live states.
- 📐 **Cron Strategy Architecture**: Formulated Unified Multi-Symbol Digest & Event-Driven Alerting architecture for Hermes agent swarm (`hermes_multi_symbol_plan.md`).

---

## 🌍 [0.14.4] - 2026-08-13

### 🌐 Universal Contract Amount Precision & JSON Import Fix
- 🌍 **Universal Contract Precision**: Expanded contract pattern matching (`:`, `-`, `XPERP`, `SWAP`, `PERP`, `FUTURES`, or any non-spot symbol) in `calculate_order_amount` in `src/mms4tk/okx_executor.py` so integer contract amounts are guaranteed across ALL present and future pairs (`BTC`, `SOL`, `ICP`, `ETH`, `AVAX`, etc.).
- 📦 **API JSON Import Fix**: Added `import json` at top of `src/mms4tk/api.py` to fix `Failed to save symbols.json: name 'json' is not defined` when adding dynamic symbols.

---

## 🩹 [0.14.3] - 2026-08-13

### 🔢 OKX Contract Order Amount Integer Precision Enforcement
- 🔢 **Contract Integer Amount Precision**: Updated `calculate_order_amount` in `src/mms4tk/okx_executor.py` to enforce integer contract amounts (`contracts = math.ceil(raw_contracts)` and `max(contracts, 1)`) for OKX contract/futures markets. Fixes `InvalidOrder: okx amount must be greater than minimum amount precision of 1` on pairs like `SOL/USD:USD-310404`.

---

## 🩹 [0.14.2] - 2026-08-13

### 🐛 Import Resolution Fix for API Version Resolver
- ⚡ **Fixed Import & NameError**: Added `get_app_version()` resolver in `src/mms4tk/api.py` to safely resolve `APP_VERSION` from `VERSION` file or `mms4tk` package with fallback, eliminating `NameError: name '__version__' is not defined`.
- 🩺 **Health Endpoint Synchronized**: Updated `/api/v1/health` to return dynamic `APP_VERSION` string.

---

## 🎯 [0.14.1] - 2026-08-13

### 🎯 Core FastAPI & Docker Volume Version Synchronization Fix
- ⚡ **FastAPI Version Synchronization**: Fixed `app = FastAPI(..., version=__version__)` constructor in `src/mms4tk/api.py` and unit test assertions, replacing the old `0.1.0` string.
- 🐳 **Docker Volume Alignment**: Mounted `./VERSION:/app/VERSION:ro` into the `antigravity_core` service in `docker-compose.yml` to guarantee identical container versioning.

---

## 💵 [0.14.0] - 2026-08-13

### 💵 Per-Symbol Trade Allocation (USD) & Base/Add Ratio Customization
- 💵 **Individual Pair Allocation Controls**: Added per-symbol `allocation_usd` (default $10.00 USD) and `base_ratio` / `add_ratio` (default 70% / 30%) settings to `SymbolsTab.jsx` and backend store (`symbols.json`).
- ⚡ **Dynamic API Health Version Fix**: Fixed `/api/v1/health` endpoint in `src/mms4tk/api.py` to return the real `__version__` string (e.g. `0.14.0`), removing hardcoded `"0.1.0"`.
- 📊 **Monitoring Grid Allocation Display**: Updated `MonitoringTab.jsx` to render individual USD allocation amounts and Base/Add percentage ratios for each active symbol card.

---

## 🔄 [0.13.2] - 2026-08-13

### 🐛 Dynamic Header Version & Active-Only Symbol Filtering in Monitoring Grid
- ⚡ **Dynamic Live Version Fetching**: Updated `App.jsx` and `Header.jsx` to fetch live system version from `/api/v1/health` (rendering dynamic `v0.13.2`), eliminating static fallback `0.13.0`.
- ⏸️ **Active-Only Symbol Grid Filtering**: Updated `MonitoringTab.jsx` to filter `data.symbols` for `is_active !== false`. Paused symbols in the `Symbols` tab are now correctly hidden from the active trading grid on the `Monitoring` tab.

---

## 🟢 [0.13.1] - 2026-08-13

### 🟢 Simplified Ticker Verification via Live OKX Price Confirmation
- 🟢 **Live OKX Price Confirmation**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to confirm pairs directly via live OKX `fetch_ticker` price fetching. If OKX returns a valid live price, the pair is confirmed and marked **🟢 AUTOTRADE_READY** with its live price display, reflecting unlocked user API permissions.

---

## 🔑 [0.13.0] - 2026-08-12

### 🔑 OKX API Key Trade Fee Permission Probe (`GET /api/v5/account/trade-fee`)
- 🔑 **Authenticated Trade Fee Probe**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to query `privateGetAccountTradeFee` directly for the user's specific API Key. This asks OKX's private authenticated API for exact trading permissions and fee schedules per pair, correctly setting Red LED badges 🔴 for unpermitted pairs.

---

## 🎯 [0.12.2] - 2026-08-12

### 🎯 Account-Specific Permitted Instrument Whitelist Check (`GET /api/v5/account/instruments`)
- 🎯 **Exact User Account Whitelist Verification**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to query `privateGetAccountInstruments` for the exact list of trading instruments enabled on the User's OKX API Key/subaccount. Pairs not in the user's permitted instrument list (e.g. `AVAX/USDC`) are marked `autotrade_permitted: False` with Red LED badge 🔴.

---

## 🛠️ [0.12.1] - 2026-08-12

### 🛠️ Multi-Mode (`cross`, `cash`, `isolated`) OKX Account Permission Verification
- 🛠️ **Spot/Swap Account Probing Fix**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to probe `privateGetAccountMaxSize` across `cash` (Spot), `cross` (Cross Margin/Swap), and `isolated` (Isolated Margin) modes. This fixes false negative errors for Spot pairs like `ETH/USDC`, `BTC/USDC`, `SOL/USDC` while correctly restricting unauthorized pairs (`AVAX/USDC`).

---

## 🤖 [0.12.0] - 2026-08-12

### 🤖 Autotrading Account Permission Verification & Badge Differentiation
- 🤖 **Private Autotrading Permission Check**: Differentiated public ticker availability from private autotrading execution permissions on the User's OKX API Key (`privateGetAccountLeverageInfo`).
- 🔴 **Red LED for Blocked Autotrading**: `SymbolsTab.jsx` explicitly displays **`🔴 HANDEL AUTOMATYCZNY: BLOKOWANY`** when a pair (e.g. `AVAX/USDC`) lacks autotrading execution permissions on the user's OKX API Key, while showing **`🟢 HANDEL AUTOMATYCZNY: GOTOWY`** for permitted pairs (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`).

---

## 💚 [0.11.1] - 2026-08-12

### 💚 Multi-Candidate OKX Ticker Verification Restoration
- 💚 **Resilient Ticker Candidate Probing**: Restored multi-candidate ticker verification (`BTC/USDC`, `BTC-USDC`, `BTC/USDC:USDC`, `BTC-USDC-SWAP`) across Spot and Perpetual Swap OKX markets, restoring Green LED status for standard pairs (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`).

---

## 🔐 [0.11.0] - 2026-08-12

### 🔐 User OKX API Key Account Permission Verification
- 🔑 **Account API Key Permission Check**: Updated `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` to run `privateGetAccountMaxSize` using the User's authenticated OKX credentials. If a pair is available globally on OKX but not permitted on the user's API Key / Account, it returns `UNSUPPORTED_ON_USER_API_KEY`.
- 🔴 **Red LED Account Badge**: `SymbolsTab.jsx` renders **Red LED Dot** + **`Status API OKX: BRAK UPRAWNIEŃ W KLUCZU API`** for pairs restricted by user account limits.

---

## 🎯 [0.10.1] - 2026-08-12

### 🎯 Strict OKX Live REST API Verification Fix
- 🔴 **Eliminated Mock Fallback in Verification**: Fixed `verify_symbol_with_okx` in `src/mms4tk/okx_executor.py` so that verification performs strict live OKX API `fetch_ticker` tests without falling back to mock candles, ensuring unsupported pairs (e.g. `AVAX/USDC`) correctly render the Red LED status light.

---

## 🔴🟢 [0.10.0] - 2026-08-12

### 🔴🟢 OKX API LED Status Light & Live Pair Verification
- 🟢 **Pulsing Green / Red LED Status Lights**: Added live API LED indicators (Pulsing Green for verified OKX pairs with live price, Red for invalid/unsupported tickers) to every trading pair card in `SymbolsTab.jsx`.
- 🔍 **One-Time OKX REST API Verification**: Integrated live OKX ticker verification endpoint (`/api/v1/symbols/verify`) and modal `Sprawdź OKX` button to test and confirm pair availability prior to activation.

---

## ⏸️ [0.9.4] - 2026-08-12

### ⏸️ Paused Symbol UI Persistence & OKX Instrument Mapping Clarification
- ⏸️ **UI Paused Symbol State**: Refactored symbol store in `src/mms4tk/api.py` so deactivated pairs remain visible in `SymbolsTab.jsx` with the **`[WSTRZYMANA]`** status badge instead of disappearing from the manager view.
- 🔗 **Automatic OKX Ticker Resolution**: OKX Executor automatically formats standard trading pairs (`ETH/USDC` -> `ETH-USDC` / `ETH-USDC-SWAP`) without manual ID lookup.

---

## 📈 [0.9.3] - 2026-08-12

### 📈 Dynamic Symbol Titles & Labels in Analytics Price Chart
- 📈 **Dynamic Chart Titles**: Updated `AnalyticsTab.jsx` price chart title and tooltips to adapt dynamically to the currently selected symbol (e.g. `Ścieżka Ceny ETH/USDC & Pasma TMA`, `Cena ETH`), removing all hardcoded BTC text.

---

## 🔐 [0.9.2] - 2026-08-12

### 🔐 Auth Header Fix for UI Symbol Operations & Icon Cleanup
- 🔐 **Web Dashboard Auth Bypass Fix**: Updated `verify_api_key` in `src/mms4tk/api.py` to allow requests originating from the Basic-Auth protected UI dashboard without triggering unwanted browser-native login popups when adding/toggling trading pairs.
- 🎨 **Symbol Form Auto-Formatting & Icon Cleanup**: Removed duplicate `+` icon from "Dodaj Nową Parę" button and auto-appends `/USDC` suffix (e.g. `SOL` -> `SOL/USDC`).

---

## 📊 [0.9.1] - 2026-08-12

### 📊 Monitoring Tab Multi-Symbol Redesign & Zero-Trade KPI Fix
- 📊 **Multi-Symbol Active Grid**: Redesigned `MonitoringTab.jsx` layout to render a live grid of all active trading pairs (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`), displaying individual operating mode (`BASE_MODE`/`SCOUT_MODE`), leverage, and capital allocation.
- 🎯 **Zero-Trade KPI Guard**: Fixed KPI calculations for Win Rate %, Profit Factor, Max Drawdown, and R:R Ratio when 0 trades exist in SQLite `logs.db`, cleanly displaying `0.0%` / `0.00` and `"Oczekiwanie na pierwsze transakcje z OKX"`.

---

## 🎛️ [0.9.0] - 2026-08-12

### 🎛️ User-Controlled Multi-Symbol UI Manager & Hermes Multi-Pair Tools
- 🎛️ **User Active Pair Toggle Switch**: Integrated interactive per-symbol Active/Paused toggles and `➕ Dodaj Nową Parę` modal in `SymbolsTab.jsx`, allowing users to activate or pause trading pairs dynamically.
- 📊 **Per-Symbol Chart Switcher**: Added symbol selection dropdown (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`) in `AnalyticsTab.jsx` to view technical charts for any active pair.
- 🤖 **Hermes Agent Multi-Pair Tools**: Updated `src/mms4tk/hermes_tools.py` with multi-symbol status inspection and per-pair emergency actions.

---

## ⚡ [0.8.1] - 2026-08-12

### ⚡ Multi-Symbol Async Execution Loop (Phase 2)
- ⚡ **Parallel Multi-Symbol Loop**: Upgraded `MMS4Daemon` in `src/mms4tk/daemon.py` with `execute_multi_symbol_cycle()` running parallel candle fetching (`asyncio.gather`) and signal evaluation across all active trading symbols (`BTC/USDC`, `ETH/USDC`, `SOL/USDC`).
- 📊 **Per-Symbol Audit Logging**: Extended `LoggerDB` cycle logging to record individual cycle results per active trading symbol.

---

## 🔀 [0.8.0] - 2026-08-12

### 🚀 Multi-Symbol Trading Backend & State Isolation
- 🔀 **Per-Symbol State Isolation**: Extended `MMS4StateManager` to track operating states (`BASE_MODE` / `SCOUT_MODE`), leverage, and Stop-Loss triggers per active trading symbol independently.
- ⚡ **Multi-Symbol Asynchronous Execution**: Upgraded `MMS4Daemon` to execute parallel candle fetching, indicator calculation, and order evaluation across multiple configured active symbols (e.g. `BTC/USDC`, `ETH/USDC`, `SOL/USDC`).
- 🌐 **Multi-Symbol REST API Endpoints**: Added `GET /api/v1/symbols`, `POST /api/v1/symbols/add`, `POST /api/v1/symbols/pause`, and `DELETE /api/v1/symbols/{symbol}` endpoints.
- 🛡️ **Global Portfolio Risk Governor**: Implemented multi-symbol capital allocation split and circuit breaker.

---

## 🐛 [0.7.1] - 2026-08-12

### 🛠️ React UI Chart Scaling, Risk Profile Brackets & Password Management
- 📈 **Synchronized Timeframe Scaling**: Added 4H, 1D, 1W, 1M, 3M, 6M, 1Y, 5Y timeframes with synchronized date X-axis scaling across Price & Equity charts.
- 📜 **Closed Trades History Table**: Restored real SQLite `logs.db` trade history audit table below analytics charts.
- ⚡ **Risk Profile Brackets**: Strictly coupled leverage brackets (CONSERVATIVE 1x-2x, MODERATE 2x-5x, AGGRESSIVE 5x-10x) to active strategy risk profile.
- 🔑 **In-App Password Manager**: Added HTTP BasicAuth password update feature in Settings card updating `.env` configuration.

---

## ⚛️ [0.7.0] - 2026-08-12

### 🚀 React 18 + Vite + TailwindCSS UI Migration
- ⚛️ **React Single-Page Application**: Replaced Streamlit UI with high-performance React 18, Vite, and TailwindCSS dashboard.
- 📲 **Native PWA Integration**: Integrated `vite-plugin-pwa` with native `beforeinstallprompt` handling, Service Worker caching, and custom app icons.
- 📊 **Recharts & Lucide Icons**: Added smooth interactive chart components for TMA indicators, balance progression, and trading signals.
- 🐳 **Nginx Production Container**: Optimized multi-stage Docker build for ultra-fast load times and static caching.

---

## 📝 [0.6.7] - 2026-08-12

### 📝 Comprehensive System & Technical Documentation Refresh
- 📝 **Updated System Architecture Diagrams**: Refreshed `README.md` to reflect Traefik BasicAuth HTTPS reverse proxy, Streamlit Web Dashboard v0.6.7, In-App Password Manager, Mobile PWA, and Scalable Plotly Analytics.
- 📡 **REST API Reference Refresh**: Synchronized `docs/API.md` and `docs/DEPLOYMENT_GUIDE.md` with active `0.6.7` deployment parameters and `{SHA}` htpasswd basicauth specs.

---

## 📋 [0.6.6] - 2026-08-12

### 🐛 Dashboard Kanban Cards & Completed Status Filter
- 📋 **Live Kanban Status Filtering**: Updated `fetch_kanban_cards()` to query active Hermes CLI card statuses (filtering out `done`, `closed`, and `archived` cards).
- 🧹 **Undispatched Webhook Events Filter**: Filtered `webhook_events` to only display pending undispatched events (`dispatched = 0`), preventing historic completed alerts from polluting Tab 1 Monitoring.

---

## ⏱️ [0.6.5] - 2026-08-12

### 📈 Dynamic Plotly X-Axis Range Scaling Across Timeframes
- ⏱️ **Explicit Axis Range Scaling (`range=[cutoff_dt, now]`)**: Dynamically pass `xaxis_range` to Plotly for every timeframe option (`4h`, `1d`, `7d`, `30d`, `3m`, `6m`, `1y`, `5y`).
- 🎯 **Visual Context Expansion**: Ensures the X-axis visibly expands and scales to match the selected timeframe even when data is sparse or young (e.g. 14 hours of history).

---

## ⏱️ [0.6.4] - 2026-08-12

### 🐛 Timeframe Filter Datetime & Fallback Fixes
- ⏱️ **UTC Datetime Matching**: Enforced `pd.to_datetime(df['timestamp'], utc=True)` and `pd.Timestamp.now(tz='UTC')` matching to eliminate timezone-aware vs naive comparison exceptions in Streamlit.
- ℹ️ **Empty Timeframe Fallback Informative Notice**: Added explicit user notification when zero trades fall within narrow timeframe windows (e.g. `4h`), preventing silent chart resets.

---

## 📱 [0.6.3] - 2026-08-12

### 📱 Touch Safety & Plotly ModeBar Re-activation
- 🎛️ **Re-enabled Plotly ModeBar Toolbar**: Restored modebar controls (`displayModeBar: true`) so tools like zoom in/out, pan, autoscale, and download plot are available on demand.
- 📱 **Mobile Touch Drag Safety (`dragmode=False`)**: Disabled default touch dragging/zooming when swiping across chart areas on smartphones, allowing smooth page scrolling without accidental chart parameter mutations.

---

## 🎨 [0.6.2] - 2026-08-12

### 🐛 Chart Overlap Fixes & Benchmark Y-Axis Normalization
- 🎨 **Chart Title & Legend Collision Fix**: Extracted chart headers to Markdown headings (`st.markdown("#### ...")`) and positioned legend neatly at top-left (`y=1.02`, `x=0`), removing native Plotly rangeselector/modebar overlaps.
- 📈 **Benchmark Y-Axis Normalization**: Filtered out invalid 0.0 entry prices to prevent 3,000,000% Y-axis scaling spikes on benchmark series.

## 🔀 [0.8.0] - 2026-08-12

### 🚀 Multi-Symbol Trading Backend & State Isolation
- 🔀 **Per-Symbol State Isolation**: Extended `MMS4StateManager` to track operating states (`BASE_MODE` / `SCOUT_MODE`), leverage, and Stop-Loss triggers per active trading symbol independently.
- ⚡ **Multi-Symbol Asynchronous Execution**: Upgraded `MMS4Daemon` to execute parallel candle fetching, indicator calculation, and order evaluation across multiple configured active symbols (e.g. `BTC/USDC`, `ETH/USDC`, `SOL/USDC`).
- 🌐 **Multi-Symbol REST API Endpoints**: Added `GET /api/v1/symbols`, `POST /api/v1/symbols/add`, `POST /api/v1/symbols/pause`, and `DELETE /api/v1/symbols/{symbol}` endpoints.
- 🛡️ **Global Portfolio Risk Governor**: Implemented multi-symbol capital allocation split and circuit breaker.

---

## 📌 [0.6.1] - 2026-08-12

### 🐛 Fixes & Dynamic Version Resolution
- 📌 **Docker Volume Version Mounting**: Mounted `./VERSION:/app/VERSION:ro` in `docker-compose.yml` so `ui_dashboard` container accesses the authoritative root `VERSION` file.
- 🔄 **Dynamic Package Version Import**: Added fallback import of `mms4tk.__version__` in `app.py` to prevent stale version string fallbacks.

---

## 📊 [0.6.0] - 2026-08-12

### ✨ Scalable Analytics & Interactive Charts Redesign
- ⏱️ **Timeframe Range Selector**: Added interactive time range filter pills bar (`4h`, `1d`, `7d`, `30d`, `3m`, `6m`, `1y`, `5y`, `ALL`) dynamically filtering the trade history DataFrame.
- 📌 **Compact Legend Layout Fix**: Moved Plotly legend to a single horizontal line above chart (`orientation="h"`, `yanchor="bottom"`, `y=1.02`), restoring 100% of vertical chart height on mobile devices.
- 🎛️ **Plotly Interactive Range Buttons**: Embedded native Plotly range buttons inside chart headers.
- 🎨 **Visual Aesthetics Upgrade**: Applied dark theme (`#0f172a`), smooth gradient area fills (`fill='tozeroy'`), and unified hover tooltips (`hovermode='x unified'`).

---

## 🔔 [0.5.2] - 2026-08-12

### ✨ PWA Installation Prompt Popup
- Added automatic mobile PWA "Install App" bottom popup modal (`#pwa-install-banner`) triggering `beforeinstallprompt` on Android Chrome/Edge devices.
- Added native iOS Safari instructions popup ("Touch Share 📤 -> Add to Home Screen").
- Stored dismissal state in `localStorage.setItem('pwa_prompt_dismissed', 'true')` to avoid bothering returning users.

---

## 📱 [0.5.1] - 2026-08-12

### 📱 PWA & Mobile Enhancements
- Enforced mobile PWA `manifest.json` static serving (`--server.enableStaticServing=true`).
- Added iOS (`apple-mobile-web-app-capable`) & Android standalone viewport meta headers for instant "Add to Home Screen" installation on mobile devices.

---

## 🎛️ [0.5.0] - 2026-08-12

### ✨ Added
- 📱 **Mastermind Control Center Web UI (`ui_dashboard`)**:
  - Built dedicated Streamlit microservice (Python 3.11) with Mobile-First PWA support (`manifest.json` + iOS/Android viewport headers).
  - 4 Interactive Tabs: **Monitoring** (live OKX positions & Hermes HITL), **Analytics** (KPIs & 3 Plotly charts), **Settings** (.env editor & UI password change), **Symbols** (X-Perps manager).
- 🔄 **15-Min OKX Trade Sync Worker (`trade_sync.py`)**:
  - Asynchronous background worker syncing closed trades from OKX API into SQLite `closed_trades` table in `logs.db`.
- 📊 **KPI Analytics Engine (`logger_db.py`)**:
  - Instant calculations for Win Rate (Total/Long/Short), Profit Factor, Max Drawdown (MDD %), and Average R:R.
- 🛡️ **Traefik HTTPS & HTTP BasicAuth Protection**:
  - Traefik reverse proxy integration with Let's Encrypt TLS and HTTP BasicAuth middleware on `mastermind.srv1490214.hstgr.cloud`.
  - Added in-app password manager UI in Tab 3 Settings.
- 📌 **Application Versioning Protocol**:
  - Root `VERSION` file, `src/mms4tk/__init__.py`, `ui_dashboard/app.py` badge, and strict agent governance rules requiring version increment on every commit.

---

## 🇪🇺 [0.4.1] - 2026-08-12

### ✨ Added & Improved
- 🇪🇺 **OKX Europe (EEA) X-Perp Derivatives Support**:
  - Full support for OKX Europe regional REST domain `https://eea.okx.com` under MiCA/MiFID compliance.
  - **Dynamic Instrument Auto-Discovery (`discover_eea_xperp_symbol`)**: Automatically queries `GET /api/v5/account/instruments?instType=FUTURES` and resolves active live X-Perp contracts (`ruleType=xperp`, `settleCcy=USDC`) such as `BTC-USD_UM_XPERP-310404`.
  - **CCXT Unified Market Mapping**: Resolved internal CCXT market table mapping from OKX native `instId` to CCXT unified symbols (e.g. `BTC/USD:USD-310404`), eliminating `BadSymbol` exceptions.
  - **OKX Hedge Mode (`posMode: long_short_mode`)**: Enforced `posSide` (`"short"` for SELL, `"long"` for BUY) and `tdMode: "cross"` parameters across all derivative orders.
- 📚 **RAG Knowledge Base & Documentation**:
  - Created `.agents/specs/knowledge/okx_europe_eea_x_perps.md` expert integration guide synchronized from NotebookLM.
  - Registered node and edge links in `.agents/specs/graph.json`.
  - Updated `README.md` and `docs/API.md` with OKX Europe EEA derivative details.

---

## 🚀 [0.4.0] - 2026-08-11

### ✨ Added
- 🤖 **Native Hermes Architecture Integration**: Fully event-driven webhook subsystem (`hermes_webhook.py`) and SQLite event log (`logs.db`).
- ⏱️ **Scheduled Hermes Cron Workflows**:
  - `mms4tk_event_dispatcher.py` (`*/1 * * * *`): Automatic dispatch of pending `SCOUT_MODE` webhook events to Hermes Kanban.
  - `mms4tk_h4_analysis.py` (`0 */4 * * *`): Higher timeframe H4 TMA band & candle formation evaluation (SWEEP_WICK vs MARUBOZU_BREAKOUT) for dynamic `directional_bias` modulation.
  - `mms4tk_d1_atr_check.py` (`5 0 * * *`): Daily D1 ATR expansion monitor to throttle `risk_profile` on extreme volatility ($>2.5\times\text{ATR\_D1}$).
- 🙋‍♂️ **Human-in-the-Loop (HITL) Endpoint**: `POST /api/v1/hermes/hitl_resolve` enabling Web UI approval of ambiguous H4 candle sweeps to relax `SCOUT_MODE` back to `BASE_MODE` (`scout_override=True`).
- 💵 **Dynamic USD Position Sizing**: `MMS4_TRADE_AMOUNT_USD` environment variable enabling exact USD margin allocation per order (e.g. $10.0 USD), calculated dynamically against live coin prices.

### 🛡️ Security & Reliability
- 🔑 **API Key Sanitization**: Removed all static secret fallbacks from codebase and documentation. Enforced environment-driven strict authentication (`MMS4_API_KEY`).
- 🌐 **CCXT Symbol & Timeframe Normalization**: Fixed market symbol resolution (`BTC/USDC:USDC` Swap vs Spot) and normalized timeframe strings (`1d`, `4h`, `15m`) for OKX execution.

---

## 🔒 [0.3.0] - 2026-08-10

### ✨ Added
- 🎯 **Two-Tier Adaptive Autotrading System**:
  - Tactical Layer (`mms4tk`): M15 execution loop with TMA/ATR indicators and leverage modulation (`BASE_MODE` x1.0 vs `SCOUT_MODE` x0.1).
  - Strategic Layer (`Hermes Agent`): H4/D1 higher timeframe analytics via `HigherTimeframeAnalytics` and dynamic `StrategyProfile` updates.
- 🛠️ **OpenAPI Tool Generator**: `get_hermes_tools()` generating GFM function schemas for LLM tool-calling in Hermes Agent.
- 📡 **REST API Expansion**: `/api/v1/strategy/profile`, `/api/v1/analytics/higher_tf`, `/api/v1/balance`, and `/api/v1/stats/pnl`.

### 🧪 Testing
- 📊 Expanded test suite to 52 unit and integration tests (`52/52 PASSED`).

---

## 🛡️ [0.2.0] - 2026-08-09

### ✨ Added
- 💾 **SQLite State Persistence**: Added `LoggerDB` state saving and restoration upon daemon restart (`FIX-1`).
- 🕯️ **Closed Candle Signal Evaluation**: Modified `MMS4SignalEngine` to evaluate TMA/ATR strictly on closed candles (`candles[:-1]`) eliminating look-ahead bias (`FIX-2`).
- 🔐 **REST API Authentication**: Implemented `X-MMS4-API-KEY` header validation for all mutating endpoints (`POST /api/v1/emergency/*`, `/api/v1/trade/*`) (`FIX-3`).

---

## 📦 [0.1.0] - 2026-08-09

### ✨ Added
- 🏗️ **Core Foundation**:
  - `MMS4StateManager`: State machine tracking `BASE_MODE` (1.0x) and `SCOUT_MODE` (0.1x).
  - `OKXExecutor`: Async exchange executor using `ccxt.async_support.okx` with strict environment secret hygiene (R-SEC-01).
  - `MMS4SignalEngine`: Triangular Moving Average (TMA) and Average True Range (ATR) calculation engine.
  - `MMS4Daemon`: Async market evaluation loop running at 15-second intervals.
- 🐳 **Docker & Traefik Infrastructure**: Dockerfile, `docker-compose.yml`, and Traefik reverse proxy integration on Hostinger VPS.

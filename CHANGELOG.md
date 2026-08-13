# 📜 CHANGELOG — mms4tk

All notable changes to the **mms4tk** (MMS v4 Autotrading System & Hermes Agent Swarm) project will be documented in this file.

The format is based on [Keep a CHANGELOG](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

# 📜 CHANGELOG — mms4tk

All notable changes to the **mms4tk** (MMS v4 Autotrading System & Hermes Agent Swarm) project will be documented in this file.

The format is based on [Keep a CHANGELOG](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

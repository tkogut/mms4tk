# 📜 CHANGELOG — mms4tk

All notable changes to the **mms4tk** (MMS v4 Autotrading System & Hermes Agent Swarm) project will be documented in this file.

The format is based on [Keep a CHANGELOG](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

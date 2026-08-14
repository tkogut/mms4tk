# 🤖 mms4tk — MMS v4 Autotrading Engine & Hermes Agent Swarm

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![React Dashboard](https://img.shields.io/badge/React--UI-v0.16.1-blue.svg)](ui_dashboard/src/App.jsx)
[![OKX CCXT](https://img.shields.io/badge/CCXT-OKX--Swap-orange.svg)](https://github.com/ccxt/ccxt)
[![Swarm Triad](https://img.shields.io/badge/Governance-v6.5%20Swarm-purple.svg)](.agents/rules/GOVERNANCE.md)
[![Version: 0.16.1](https://img.shields.io/badge/Version-0.16.1-emerald.svg)](VERSION)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**mms4tk** is a high-reliability, multi-symbol adaptive algorithmic trading system designed for **OKX Europe (EEA) X-Perps / Expiry Perps & Perpetual Swaps (e.g. BTC/USDC, ETH/USDC, SOL/USDC, ICP/USDC)** and Spot markets. It combines an asynchronous execution daemon (`mms4tk`), a Traefik-secured React Web Dashboard (`ui_dashboard`), and an autonomous LLM strategic supervisor ([Hermes Agent](https://hermes-agent.nousresearch.com/)).

---

## 🏛️ System Architecture & Multi-Symbol Engine

```mermaid
flowchart TD
    subgraph 1. React Web Dashboard (v0.16.1)
        A1["Multi-Symbol Manager\n(Per-symbol allocation $, Base/Add ratio, Timeframe)"]
        A2["Live Monitoring Grid\n(Real-time OKX Prices, State, Leverage)"]
    end

    subgraph 2. FastAPI REST Gateway
        B1["GET/POST /api/v1/symbols\n(dynamic symbol & timeframe management: 15m, 30m, 1h)"]
        B2["GET /api/v1/state & /api/v1/balance"]
    end

    subgraph 3. MMS4Daemon & Analytics Engine
        C1["MMS4Daemon Run Loop (co 15s)"] --> C2["Fetch OHLCV (configured timeframe: 15m / 1h)"]
        C2 --> C3["TMA & ATR Bands Calculation"]
        C3 --> C4{"Signal Evaluation (Outer ATR Band Penetration)"}
        C4 -->|BASE_LONG / BASE_SHORT| C5["70/30 Capital Split Execution ($7.00 Base / $3.00 Add)"]
    end

    subgraph 4. Strategic Supervisor (Hermes Agent)
        D1["H4 Liquidity Sweep Analysis & D1 ATR Monitor"]
        D2["Telegram Digest & Kanban Alerts"]
    end

    A1 & A2 --> B1 & B2
    B1 --> C1
    C5 --> D1 & D2
```

---

## 🚀 Key Features in v0.16.1

1. **Per-Symbol Timeframe Selection (`timeframe: "15m" / "1h" / "30m"`)**:
   - Allows configuring individual candle timeframes per trading symbol (`15m` tactical, `1h` swing, `30m`).
   - Replaced 1m micro-noise to guarantee clean trend signal generation and empirical z-score accuracy.
2. **70/30 Capital Split & Margin Guard**:
   - **Base Order (70%)**: $7.00 USD allocation with hard 2.0% Stop Loss.
   - **High Risk Addon (30%)**: $3.00 USD allocation with 1.25% Stop Loss.
   - **Max Loss per SL**: Capped at **$0.14 USD** (14 cents).
3. **Defensive State Machine (`BASE_MODE` / `SCOUT_MODE`)**:
   - Automatically downgrades leverage **10x (from 1.0x to 0.1x)** upon hitting SL $\ge 2.0\%$, reducing risk to $0.02 USD per trade.
4. **Hermes Agent Swarm & Telegram Integration**:
   - Hourly digest cron job (`mms4tk_multi_symbol_digest.py`) sending live fleet reports to Telegram.
   - Kanban board integration for Human-in-the-Loop (HITL) candle sweep decisions.

---

## ⚙️ Environment Variables & Configuration

Create a `.env` file in the root directory (or `/docker/mms4tk/.env` on VPS):

```ini
# OKX API Credentials (R-SEC-01 Compliant)
OKX_API_KEY=your_okx_api_key
OKX_SECRET=your_okx_api_secret
OKX_PASSPHRASE=your_okx_passphrase

# Trading Configuration
MMS4_SYMBOL=BTC/USDC
MMS4_MAX_MARGIN_USD=10.0
MMS4_LOG_LEVEL=INFO

# Security & Authentication
MMS4_API_KEY=your_secret_mms4tk_api_key
MMS4_UI_BASIC_AUTH=admin:{SHA}wVzGfjblQLdYsJ5xfQcACBwdZ4c=

# Hermes Integration
HERMES_API_BASE=http://hermes-agent:8300
```

---

## 🚀 Quickstart & Local Setup

### 1. Requirements
- Python 3.11+
- Node.js 18+ & npm (for React Dashboard UI)
- Docker & Docker Compose (for production deployment)

### 2. Run Core FastAPI Daemon Locally
```bash
# Clone repository
git clone https://github.com/tkogut/mms4tk.git
cd mms4tk

# Install dependencies
pip install -r requirements.txt

# Run Core FastAPI Daemon
PYTHONPATH=src python3 -m uvicorn mms4tk.api:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Run React Web Dashboard UI
```bash
cd ui_dashboard
npm install
npm run dev
```

---

## 🐳 Docker Deployment (VPS Hostinger)

Deploy using Docker Compose with Traefik integration:

```bash
cd /docker/mms4tk
git pull origin main
docker compose up -d --build
```

Verify application version:
```bash
docker exec ui_dashboard cat /app/VERSION
# Output: 0.16.1
```

---

## 📚 Technical Documentation Sitemap

- 📜 [MMS v4 Strategy & Architecture Diagram](docs/mms4_mastermind_full_strategy_and_architecture.md)
- 📊 [Quantitative Audit Report](docs/mms4_strategy_audit_report.md)
- 🧪 [Independent Backtest Simulation Report](docs/mms4_independent_simulation_audit.md)
- 🏁 [Post-Implementation Report (v0.16.0/v0.16.1)](docs/mms4_post_implementation_report.md)
- 📡 [REST API Documentation](docs/API.md)
- 🚀 [Deployment Guide](docs/DEPLOYMENT_GUIDE.md)

---

## 📜 Governance & License

Developed under **AGENTS-OS v6.5 Swarm Edition** protocols.
Released under the [MIT License](LICENSE).

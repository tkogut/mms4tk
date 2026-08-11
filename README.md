# 🤖 mms4tk — MMS v4 Autotrading Engine & Hermes Agent Swarm

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)](https://fastapi.tiangolo.com/)
[![OKX CCXT](https://img.shields.io/badge/CCXT-OKX--Swap-orange.svg)](https://github.com/ccxt/ccxt)
[![Swarm Triad](https://img.shields.io/badge/Governance-v6.5%20Swarm-purple.svg)](.agents/rules/GOVERNANCE.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**mms4tk** is a high-reliability, two-tier adaptive algorithmic trading system designed for **OKX Perpetual Swaps (e.g. BTC/USDC, ETH/USDC)**. It combines a high-speed execution daemon (`mms4tk`) with an autonomous LLM strategic supervisor ([Hermes Agent](https://hermes-agent.nousresearch.com/)).

---

## 🏛️ System Architecture

```mermaid
graph TD
    SubGraph1[OKX Exchange API] <-->|Async CCXT WebSocket/REST| CoreEngine[mms4tk Core Daemon]
    
    subgraph Tactical Execution Layer (M15)
        CoreEngine -->|Closed Candle Evaluation| TMA[TMA & ATR Bands]
        CoreEngine -->|State Machine| StateMgr[MMS4StateManager]
        StateMgr -->|BASE_MODE (1.0x) / SCOUT_MODE (0.1x)| RiskControl[Risk & Leverage Control]
        StateMgr -->|Persist State| DB[(SQLite logs.db)]
    end

    subgraph Strategic Supervisor Layer (H4 / D1)
        Hermes[Hermes Agent Swarm] -->|Cron H4/D1 Analytics| StrategyProfile[Strategy Profile / Bias]
        CoreEngine -->|Webhook SL 2% Hit| HermesKanban[(Hermes Kanban.db)]
        HermesKanban -->|Blocked / HITL Card| WebUI[Human-in-the-Loop Web UI]
        WebUI -->|POST /api/v1/hermes/hitl_resolve| StateMgr
    end
    
    StrategyProfile -->|REST API X-MMS4-API-KEY| CoreEngine
```

### Key Architectural Layers:
1. **Tactical Layer (`mms4tk` Daemon)**: Evaluates M15 closed candles every 15 seconds. Computes Triangular Moving Averages (TMA) and Average True Range (ATR) envelopes. Executes orders with fixed SL (2.0%) and position sizing ($10 USD).
2. **Strategic Layer (`Hermes Agent`)**: Analyzes H4/D1 higher timeframes via scheduled Cron jobs. Dynamically modulates `directional_bias` (`BOTH`, `LONG_ONLY`, `SHORT_ONLY`) and `risk_profile` (`CONSERVATIVE`, `MODERATE`, `AGGRESSIVE`).
3. **Human-in-the-Loop (HITL)**: Ambiguous candle sweeps trigger cards on Hermes Kanban Web UI for human confirmation.

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
MMS4_TRADE_AMOUNT_USD=10.0
MMS4_LOG_LEVEL=INFO

# Security & Authentication
MMS4_API_KEY=your_secret_mms4tk_api_key

# Hermes Integration
HERMES_API_BASE=http://hermes-agent:8300
```

---

## 🚀 Quickstart & Local Setup

### 1. Requirements
- Python 3.11+
- Docker & Docker Compose (for production deployment)

### 2. Installation & Testing
```bash
# Clone repository
git clone https://github.com/tkogut/mms4tk.git
cd mms4tk

# Install dependencies
pip install -r requirements.txt

# Run pytest suite
MMS4_API_KEY="test-key" python3 -m pytest tests/ -v
```

### 3. Run FastAPI & Daemon Locally
```bash
PYTHONPATH=src python3 -m uvicorn mms4tk.api:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🐳 Docker Deployment (VPS Hostinger)

Deploy using Docker Compose with Traefik integration:

```bash
cd /docker/mms4tk
git pull origin main
docker compose up -d --build
```

Verify service status:
```bash
docker exec hermes-agent curl -s http://antigravity_core:8000/api/v1/daemon/status
```

---

## 📊 REST API & Hermes Tool Reference

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Public | System health check & version |
| `GET` | `/api/v1/daemon/status` | Public | Daemon status, symbol, trade allocation, and processed cycles |
| `GET` | `/api/v1/state` | Public | Current state (`BASE_MODE` / `SCOUT_MODE`), leverage, equity |
| `GET` | `/api/v1/balance` | Public | OKX account balance breakdown (Free, Total per currency) |
| `GET` | `/api/v1/stats/pnl` | Public | PnL stats, win rate, realized PnL, ROE % |
| `GET` | `/api/v1/strategy/profile` | Public | Active `StrategyProfile` (`directional_bias`, `risk_profile`) |
| `POST` | `/api/v1/strategy/profile` | `X-MMS4-API-KEY` | Dynamic strategy profile update |
| `POST` | `/api/v1/emergency/killall` | `X-MMS4-API-KEY` | Emergency stop: cancel all orders & close positions |
| `POST` | `/api/v1/emergency/reverse` | `X-MMS4-API-KEY` | Emergency position reversal with 2% SL |
| `POST` | `/api/v1/hermes/hitl_resolve` | `X-MMS4-API-KEY` | Resolve HITL candle sweep decision from Kanban Web UI |

Detailed OpenAPI documentation is available in [`docs/API.md`](docs/API.md).

---

## 🛡️ Risk Management Rules

1. **Stop Loss (SL 2.0%)**: Every primary position (`BASE_LONG` / `BASE_SHORT`) is executed with a hard 2.0% Stop Loss.
2. **SCOUT_MODE Transition**: Hitting SL 2.0% automatically downgrades state from `BASE_MODE` (leverage 1.0x) to `SCOUT_MODE` (leverage 0.1x), reducing risk by 90%.
3. **Recovery Condition**: A single profitable trade (Take Profit) in `SCOUT_MODE` restores `BASE_MODE` (1.0x).
4. **Position Sizing**: Controlled by `MMS4_TRADE_AMOUNT_USD` (default $10.0 USD). Max loss per SL is limited to **$0.20 USD** (20 cents).

---

## 📜 Governance & License

Developed under **AGENTS-OS v6.5 Swarm Edition** protocols.
Released under the [MIT License](LICENSE).

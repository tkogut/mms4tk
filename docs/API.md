# 📡 mms4tk REST API & Hermes Function Tools Reference

**Application Version:** `v0.30.1`  
This document provides technical documentation for all HTTP REST API endpoints exposed by `mms4tk` (FastAPI) and consumed by the **Hermes Agent** function-calling engine and Web Dashboard UI.

---

## 🔒 Authentication

All mutating endpoints require an HTTP header containing the secret key specified in the `MMS4_API_KEY` environment variable:

```http
X-MMS4-API-KEY: <your_secret_api_key>
```

Unauthenticated mutating requests will return `401 Unauthorized`.

---

## 📑 Endpoints Summary

### 1. Health Check
- **`GET /api/v1/health`**
  - **Auth**: Public
  - **Response**:
    ```json
    {
      "status": "ok",
      "version": "0.30.1"
    }
    ```

---

### 2. Daemon Status & Adaptive Metrics
- **`GET /api/v1/daemon/status`**
  - **Auth**: Public
  - **Response**:
    ```json
    {
      "is_running": true,
      "interval_seconds": 15.0,
      "symbol": "BTC/USDC",
      "active_symbols": ["BTC/USDC", "ETH/USDC", "SOL/USDC", "ICP/USDC"],
      "max_margin_usd": 10.0,
      "base_trade_usd": 7.0,
      "addon_trade_usd": 3.0,
      "trade_amount_usd": 10.0,
      "total_max_portfolio_margin_usd": 0.0,
      "engine_mode": "CLASSIC",
      "adaptive_vr_high": 1.6,
      "adaptive_vr_low": 0.8,
      "time_lock_start": "13:30",
      "time_lock_end": "14:30",
      "last_cycle_timestamp": "2026-08-19T16:35:00.000000+00:00",
      "processed_cycles": 1420
    }
    ```

---

### 3. Multi-Symbol Management (`symbols.json`)
- **`GET /api/v1/symbols`**
  - **Auth**: Public
  - **Description**: Returns all configured trading symbols with per-symbol allocation ($ USD), Base/Add ratio (%), timeframe (`15m`, `30m`, `1h`, `5m`, `1m`), live OKX price, and autotrade status.
  - **Response**:
    ```json
    {
      "symbols": [
        {
          "symbol": "BTC/USDC",
          "state": "BASE_MODE",
          "leverage": 3.0,
          "is_active": true,
          "allocation_usd": 10.0,
          "base_ratio": 70,
          "add_ratio": 30,
          "timeframe": "1h",
          "okx_verified": true,
          "autotrade_permitted": true,
          "okx_status": "AUTOTRADE_READY",
          "okx_price": 62780.0
        },
        {
          "symbol": "SOL/USDC",
          "state": "BASE_MODE",
          "leverage": 3.0,
          "is_active": true,
          "allocation_usd": 7.0,
          "base_ratio": 70,
          "add_ratio": 30,
          "timeframe": "15m",
          "okx_verified": true,
          "autotrade_permitted": true,
          "okx_status": "AUTOTRADE_READY",
          "okx_price": 75.39
        }
      ],
      "total": 2
    }
    ```

- **`POST /api/v1/symbols/add` / `POST /api/v1/symbols/update`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Request Body**:
    ```json
    {
      "symbol": "SOL/USDC",
      "is_active": true,
      "allocation_usd": 7.0,
      "base_ratio": 70,
      "add_ratio": 30,
      "timeframe": "15m"
    }
    ```
  - **Response**: Updated symbol entry with verification status.

- **`POST /api/v1/symbols/verify`**
  - **Auth**: Public
  - **Description**: Performs an instant live OKX REST API verification for a symbol pair.

- **`DELETE /api/v1/symbols/{symbol}`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Deactivates a symbol pair from active trading cycles.

---

### 4. State Engine Status
- **`GET /api/v1/state`**
  - **Auth**: Public
  - **Description**: Returns active state (`BASE_MODE` / `SCOUT_MODE`), leverage multiplier, equity, and trade metrics.
  - **Response**:
    ```json
    {
      "state": "BASE_MODE",
      "leverage": 3.0,
      "current_equity": 30.39,
      "realized_pnl": 0.0,
      "total_trades": 0
    }
    ```

---

### 5. Account Balances & PnL
- **`GET /api/v1/balance`**
  - **Auth**: Public
  - **Description**: Fetches unified balances from OKX across Trading and Funding accounts.

- **`GET /api/v1/stats/pnl`**
  - **Auth**: Public
  - **Description**: Daily ROE %, realized PnL, unrealized PnL, and KPI summary.

---

### 6. Strategy Profile Management
- **`GET /api/v1/strategy/profile`**
  - **Auth**: Public
  - **Response**:
    ```json
    {
      "directional_bias": "BOTH",
      "risk_profile": "MODERATE",
      "scout_override": false,
      "reason": "Default initialization"
    }
    ```

- **`POST /api/v1/strategy/profile`**
  - **Auth**: Required (`X-MMS4-API-KEY`)

---

### 7. Settings & Adaptive Hybrid Engine Configuration
- **`GET /api/v1/settings`**
  - **Auth**: Public
  - **Description**: Returns all runtime risk parameters, portfolio cap, and Adaptive Engine configuration.
  - **Response**:
    ```json
    {
      "leverage": 3.0,
      "scout_mode": false,
      "capital_split_ratio": 0.70,
      "max_margin_usd": 10.0,
      "stop_loss_pct": 2.0,
      "total_max_portfolio_margin_usd": 0.0,
      "engine_mode": "CLASSIC",
      "adaptive_vr_high": 1.6,
      "adaptive_vr_low": 0.8,
      "time_lock_start": "13:30",
      "time_lock_end": "14:30"
    }
    ```

- **`POST /api/v1/settings/update`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Updates strategy parameters, leverage (1x–10x), Stop Loss %, portfolio cap, and Adaptive Engine settings in memory and persists them to `.env`.
  - **Request Body**:
    ```json
    {
      "leverage": 3.0,
      "scout_mode": false,
      "capital_split_ratio": 0.70,
      "max_margin_usd": 10.0,
      "stop_loss_pct": 1.25,
      "total_max_portfolio_margin_usd": 1500.0,
      "engine_mode": "ADAPTIVE",
      "adaptive_vr_high": 1.6,
      "adaptive_vr_low": 0.8,
      "time_lock_start": "13:30",
      "time_lock_end": "14:30"
    }
    ```
  - **Response**: Status object with all updated parameters.

- **`POST /api/v1/settings/change_password`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Updates Traefik HTTP BasicAuth login password and updates `.env`.
  - **Request Body**:
    ```json
    {
      "new_password": "your_secure_password",
      "username": "admin"
    }
    ```

---

### 8. Hermes Webhook Integration
- **`POST /api/v1/hermes/webhook`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Receives custom webhook events from Hermes Agent swarm.
  - **Request Body**:
    ```json
    {
      "title": "MMS4TK Alert",
      "body": "SCOUT_MODE activated for BTC/USDC after SL hit",
      "symbol": "BTC/USDC",
      "initial_status": "IN_PROGRESS"
    }
    ```

- **`POST /api/v1/hermes/hitl_resolve`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Resolves Human-in-the-Loop Kanban decision from Web UI or Telegram.

---

### 9. Emergency Actions
- **`POST /api/v1/emergency/killall`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Immediately cancels all open orders on OKX and closes active positions.

- **`POST /api/v1/emergency/reverse`**
  - **Auth**: Required (`X-MMS4-API-KEY`)
  - **Description**: Immediately closes active position and opens an opposite position with 2.0% Stop Loss.

---

## 🤖 Hermes Function Tool Manifest (`openapi.json`)

Tools exposed to Hermes Agent:
1. `get_state`
2. `get_balance`
3. `get_pnl`
4. `get_trading_symbols`
5. `add_or_update_trading_symbol`
6. `calculate_analytics`
7. `emergency_killall`
8. `emergency_reverse`
9. `high_risk_add`
10. `mms4_get_higher_tf`
11. `mms4_set_strategy_profile`
12. `mms4_get_pending_events`
13. `mms4_hitl_resolve`

---

## 🇪🇺 OKX Europe (EEA) Execution Details

When executing orders on OKX Europe (`https://eea.okx.com`):
- **Dynamic URL Resolution**: Automatically probes `https://eea.okx.com` and fallback endpoints.
- **Hedge Mode (`posMode: long_short_mode`)**: Passes `params={"tdMode": "cross", "posSide": "short"}` for SELL orders and `params={"tdMode": "cross", "posSide": "long"}` for BUY orders.
- **Contract Size Calculation (`ctVal`)**: Converts target USD allocation into precise contract quantities (`sz`).

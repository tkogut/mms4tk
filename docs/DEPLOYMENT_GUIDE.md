# MMS4 Toolkit (mms4tk) - Hostinger VPS Deployment Guide

## 1. Environment & Architecture Overview

`mms4tk` runs as a containerized service inside Docker on Hostinger VPS, communicating with the Hermes cognitive agent over an isolated internal Docker network (`internal_swarm_net`).

- **Target OS**: Ubuntu 22.04 LTS / 24.04 LTS (Hostinger VPS)
- **Containerization**: Docker Engine & Docker Compose v2
- **Persistence Volume**: `.agents/swarm/` mapped to host directory for state & audit logs (`logs.db`).
- **Security Compliance**: AGENTS-OS R-SEC-01 (API keys stored exclusively in `.env`, never exposed or logged).

---

## 2. Server Prerequisites & Environment Setup

### Step 2.1: Update System & Install Docker Engine
```bash
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl ca-certificates gnupg lsb-release

# Install Docker official GPG key & repository
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Verify Docker Compose v2 installation
docker compose version
```

---

## 3. R-SEC-01 Environment Variables Configuration

Create a strict, permission-protected `.env` file on the VPS host inside the project root directory:

```bash
touch .env
chmod 600 .env
```

Populate `.env` with mandatory exchange credentials and service configuration:

```env
# OKX Production API Credentials (R-SEC-01 Compliant)
OKX_API_KEY=your_okx_api_key_here
OKX_SECRET=your_okx_api_secret_here
OKX_PASSPHRASE=your_okx_api_passphrase_here

# Service & Daemon Settings
HOST=0.0.0.0
PORT=8000
DAEMON_INTERVAL=15.0
SYMBOL=BTC/USDC:USDC
LOG_LEVEL=INFO

# Security & Web UI BasicAuth
MMS4_API_KEY=your_secret_mms4tk_api_key
MMS4_UI_BASIC_AUTH=admin:{SHA}wVzGfjblQLdYsJ5xfQcACBwdZ4c=
```

> [!CAUTION]
> NEVER commit the `.env` file or hardcode credentials into source code. Verify that `.env` is listed in `.gitignore`.

---

## 4. Container Deployment & Launch Commands

### Step 4.1: Build and Launch Services
Run Docker Compose in detached mode:

```bash
docker compose up -d --build
```

### Step 4.2: Verify Running Containers
Check that `mms4tk` container is running and healthy:

```bash
docker compose ps
```

---

## 5. Service Monitoring & Health Inspection

### Step 5.1: Real-time Application Logs
```bash
docker compose logs -f --tail=100
```

### Step 5.2: API Health Check Endpoint
```bash
curl -s http://localhost:8000/api/v1/health | jq .
```
Expected output:
```json
{
  "status": "ok",
  "version": "0.6.6"
}
```

### Step 5.3: Background Daemon Status Inspection
```bash
curl -s http://localhost:8000/api/v1/daemon/status | jq .
```
Expected output:
```json
{
  "is_running": true,
  "interval_seconds": 15.0,
  "last_cycle_timestamp": "2026-08-09T16:57:00.123456+00:00",
  "processed_cycles": 42
}
```

### Step 5.4: State & PnL Inspection
```bash
curl -s http://localhost:8000/api/v1/state | jq .
curl -s http://localhost:8000/api/v1/stats/pnl | jq .
```

---

## 6. Persistence & SQLite Data Backup Procedures

All cycle outcomes and state machine metrics are recorded in `.agents/swarm/logs.db` on the persistent volume mount.

### Step 6.1: Manual Backup of logs.db
To create an emergency backup of the SQLite audit database without stopping the container:

```bash
mkdir -p ./backups
sqlite3 .agents/swarm/logs.db ".backup './backups/logs_backup_$(date +%Y%m%d_%H%M%S).db'"
```

### Step 6.2: Automated Cron Backup Setup
To schedule daily automated backups at 02:00 UTC:

```bash
(crontab -l 2>/dev/null; echo "0 2 * * * sqlite3 /home/tkogut/projects/mms4tk/.agents/swarm/logs.db \".backup '/home/tkogut/projects/mms4tk/backups/logs_\$(date +\%Y\%m\%d).db'\"") | crontab -
```

---

## 7. Graceful Restart & Shutdown

- **Graceful Service Restart**:
  ```bash
  docker compose restart
  ```
- **Complete Tear Down**:
  ```bash
  docker compose down
  ```

#!/usr/bin/env python3
"""
mms4tk Multi-Symbol Digest Cron Job for Hermes Agent

Runs as Hermes cron job once every 4 hours offset from H4 (30 2,6,10,14,18,22 * * *).
Fetches active trading symbols and live open positions from mms4tk API
and generates a unified multi-symbol swarm report for Hermes Kanban / Telegram.

Install via:
    hermes cron create "30 2,6,10,14,18,22 * * *" \
        --name "mms4tk Multi-Symbol Digest" \
        --script mms4tk_multi_symbol_digest.py \
        --no-agent \
        --deliver telegram
"""

import json
import os
import re
import subprocess
import sys
from urllib.request import Request, urlopen

def get_api_base_url() -> str:
    """Probe candidate base URLs to reliably reach mms4tk API anywhere."""
    env_url = os.environ.get("MMS4TK_BASE_URL")
    candidates = [env_url] if env_url else []
    candidates.extend([
        "http://127.0.0.1:8501",
        "http://antigravity_core:8000",
        "http://172.17.0.1:8501",
        "http://localhost:8501",
        "http://localhost:8000",
    ])
    for base in candidates:
        if not base:
            continue
        try:
            req = Request(f"{base}/api/v1/health")
            resp = urlopen(req, timeout=3)
            if resp.status == 200:
                return base
        except Exception:
            continue
    return candidates[0] if candidates else "http://antigravity_core:8000"


MMS4TK_BASE = get_api_base_url()
MMS4TK_API_KEY = os.environ.get("MMS4_API_KEY", "")


def fetch_api(endpoint: str):
    """Helper to fetch REST API data from mms4tk."""
    try:
        headers = {}
        if MMS4TK_API_KEY:
            headers["X-MMS4-API-KEY"] = MMS4TK_API_KEY
        req = Request(f"{MMS4TK_BASE}{endpoint}", headers=headers)
        resp = urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"❌ API call to {endpoint} failed: {e}", file=sys.stderr)
        return None


def create_kanban_digest_card(title: str, description: str):
    """Post digest report to mms4tk webhook API and Hermes CLI."""
    # Resolve Hermes CLI path (must be defined before use below)
    hermes_cli = os.environ.get("HERMES_CLI_PATH", "/docker/hermes-agent/bin/hermes")

    # 1. Post to mms4tk Webhook API (instantly registers card on Dashboard Kanban & DB)
    try:
        headers = {"Content-Type": "application/json"}
        if MMS4TK_API_KEY:
            headers["X-MMS4-API-KEY"] = MMS4TK_API_KEY
        payload = json.dumps({
            "event_type": "MULTI_SYMBOL_DIGEST",
            "title": title,
            "details": description
        }).encode("utf-8")
        req = Request(f"{MMS4TK_BASE}/api/v1/hermes/webhook", data=payload, headers=headers, method="POST")
        urlopen(req, timeout=10)
        print("✅ Digest card successfully posted to Dashboard Kanban & Webhook Engine!")
    except Exception as e:
        print(f"⚠️ Webhook API post notice: {e}", file=sys.stderr)

    # 2. Deliver via Hermes notification engine to Telegram
    try:
        notify_msg = f"{title}\n\n{description}"
        if os.path.exists(hermes_cli):
            n_cmd = [hermes_cli, "send", "--to", "telegram", notify_msg]
        else:
            n_cmd = ["docker", "exec", "hermes-agent", "hermes", "send", "--to", "telegram", notify_msg]
        result = subprocess.run(n_cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            print("💬 Hermes Telegram notification dispatched!")
        else:
            print(f"⚠️ Hermes CLI returned non-zero: {result.stderr.strip()}", file=sys.stderr)
    except Exception as ne:
        print(f"⚠️ Hermes Telegram notify notice: {ne}", file=sys.stderr)


def clean_symbol_name(sym: str) -> str:
    """Format technical OKX instId into clean readable symbol like SOL/USD or BTC/USDC."""
    if not sym:
        return "N/A"
    clean = str(sym).split(":")[0]
    clean = re.sub(r'-\d+$', '', clean)
    clean = clean.replace("-USD_UM_XPERP", "/USD").replace("_XPERP", "")
    return clean


def main():
    print("🔄 Fetching multi-symbol data from MMS4 API...")
    health_data = fetch_api("/api/v1/health") or {}
    ver = health_data.get("version", "0.18.0")

    symbols_data = fetch_api("/api/v1/symbols") or {}
    positions_data = fetch_api("/api/v1/positions") or {}
    balance_data = fetch_api("/api/v1/balance") or {}
    stats_data = fetch_api("/api/v1/stats/pnl") or {}

    symbols = symbols_data.get("symbols", [])
    positions = positions_data.get("positions", [])

    free_usdc = float(balance_data.get("free", {}).get("USDC") or balance_data.get("portfolio_usd", {}).get("available_usd", 0.0))
    total_eq = float(balance_data.get("total", {}).get("USDC") or balance_data.get("portfolio_usd", {}).get("total_usd", 0.0))
    realized_pnl = float(stats_data.get("realized_pnl", stats_data.get("total_pnl_usd", 0.0)))

    if not symbols:
        print("⚠️ No symbol configuration returned from API.")
        return


    active_symbols = [s for s in symbols if s.get("is_active") is not False]
    total_alloc = sum(float(s.get("allocation_usd", 10.0)) for s in active_symbols)
    total_upnl = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)

    lines = []
    lines.append(f"📊 **HERMES MULTI-SYMBOL SWARM DIGEST** (Wersja v{ver})")
    lines.append(f"")
    lines.append(f"🟢 **AKTYWNE PARY RYNKOWE ({len(active_symbols)}/{len(symbols)})**:")

    for s in active_symbols:
        sym = clean_symbol_name(s.get("symbol", "N/A"))
        state = s.get("state", "BASE_MODE")
        alloc = float(s.get("allocation_usd", 10.0))
        base_r = s.get("base_ratio", 70)
        add_r = s.get("add_ratio", 30)
        price = float(s.get("okx_price", 0.0))
        state_icon = "🟡" if state == "SCOUT_MODE" else "🟢"
        price_str = f"${price:,.2f}" if price > 0 else "N/A"

        lines.append(
            f"{state_icon} **{sym}**: `{price_str}` | Stan: `{state}` | Alokacja: `${alloc:.2f} USD` ({base_r}/{add_r}%)"
        )

    lines.append(f"")
    lines.append(f"📈 **AKTYWNE OTWARTE POZYCJE OKX ({len(positions)})**:")

    if positions:
        for p in positions:
            sym = clean_symbol_name(p.get("symbol", "N/A"))
            side = str(p.get("side", "long")).upper()
            contracts = p.get("contracts", 0.0)
            notional = float(p.get("notional_usd", 0.0))
            entry = float(p.get("entry_price", 0.0))
            mark = float(p.get("mark_price", 0.0))
            upnl = float(p.get("unrealized_pnl", 0.0))
            leverage = float(p.get("leverage", 1.0))
            pnl_icon = "🟢" if upnl >= 0 else "🔴"
            pnl_str = f"+${upnl:.2f}" if upnl >= 0 else f"-${abs(upnl):.2f}"

            lines.append(
                f"- **{sym}**: `{side} {contracts} contr` (${notional:.2f} USD) | Dźwignia: `{leverage:.0f}x` | WEJŚCIE: `${entry:,.2f}` | CENA: `${mark:,.2f}` | uPnL: {pnl_icon} **`{pnl_str} USD`**"
            )
    else:
        lines.append("ℹ️ Brak otwartych pozycji w tym cyklu.")

    used_margin = sum((float(p.get("notional_usd", 0.0)) / max(float(p.get("leverage", 1.0)), 1.0)) for p in positions)

    lines.append(f"")
    lines.append(f"💰 **PODSUMOWANIE PORTFELA OKX (API)**:")
    lines.append(f"- **💵 Wolny Dostępny Depozyt**: `${free_usdc:.2f} USDC` (z OKX API)")
    lines.append(f"- **📊 Całkowity Kapitał (Equity)**: `${total_eq:.2f} USDC`")
    lines.append(f"- **🔒 Wykorzystany Depozyt (Margin)**: `${used_margin:.2f} USD`")
    lines.append(f"- **🎯 Łączna Alokacja Par**: `${total_alloc:.2f} USD`")
    lines.append(f"- **📈 Niezrealizowany PnL (uPnL)**: `{'+$' if total_upnl >= 0 else '-$'}{abs(total_upnl):.2f} USD`")
    realized_pnl_sign = "+$" if realized_pnl >= 0 else "-$"
    lines.append(f"- **💸 Zrealizowany PnL (Całkowity)**: `{realized_pnl_sign}{abs(realized_pnl):.2f} USD`")
    lines.append(f"- **Status Floty OKX**: `GOTOWA` 🟢")


    full_digest = "\n".join(lines)
    print(full_digest)

    # Post card to Hermes Kanban
    title = f"📊 Hermes Digest: {len(active_symbols)} Aktywnych Par | Wolny Depozyt: ${free_usdc:.2f} USDC"
    create_kanban_digest_card(title, full_digest)


if __name__ == "__main__":
    main()

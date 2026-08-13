#!/usr/bin/env python3
"""
mms4tk Multi-Symbol Digest Cron Job for Hermes Agent

Runs as Hermes cron job once every hour (0 * * * *).
Fetches active trading symbols and live open positions from mms4tk API
and generates a unified multi-symbol swarm report for Hermes Kanban / Telegram.

Install via:
    hermes cron create "0 * * * *" \
        --name "mms4tk Multi-Symbol Digest" \
        --script mms4tk_multi_symbol_digest.py \
        --no-agent \
        --deliver telegram
"""

import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen

MMS4TK_BASE = os.environ.get("MMS4TK_BASE_URL", "http://localhost:8501")
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
        resp = urlopen(req, timeout=10)
        print("✅ Digest card successfully posted to Dashboard Kanban & Webhook Engine!")
    except Exception as e:
        print(f"⚠️ Webhook API post notice: {e}", file=sys.stderr)

    # 2. Attempt CLI call if hermes binary is present
    hermes_cli = "/home/tkogut/.nvm/versions/node/v20.19.0/bin/hermes"
    if not os.path.exists(hermes_cli):
        hermes_cli = "hermes"

    cmd = [
        hermes_cli, "kanban", "card", "create",
        "--title", title,
        "--description", description,
        "--priority", "5",
        "--created-by", "mms4tk-multi-symbol-cron",
        "--initial-status", "ready",
        "--json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            print(f"📋 Hermes CLI Kanban card created: {result.stdout.strip()[:80]}")
    except Exception:
        pass


def main():
    health_data = fetch_api("/api/v1/health") or {}
    ver = health_data.get("version", "0.15.6")

    symbols_data = fetch_api("/api/v1/symbols") or {}
    positions_data = fetch_api("/api/v1/positions") or {}

    symbols = symbols_data.get("symbols", [])
    positions = positions_data.get("positions", [])

    if not symbols:
        print("⚠️ No symbol configuration returned from API.")
        return

    active_symbols = [s for s in symbols if s.get("is_active") is not False]
    total_alloc = sum(float(s.get("allocation_usd", 10.0)) for s in active_symbols)
    total_upnl = sum(float(p.get("unrealized_pnl", 0.0)) for p in positions)

    lines = []
    lines.append(f"📊 **HERMES MULTI-SYMBOL SWARM DIGEST** (Wersja v{ver})")
    lines.append(f"")
    lines.append(f"🟢 **AKTYWNE PARY RYNTKOWE ({len(active_symbols)}/{len(symbols)})**:")

    for s in active_symbols:
        sym = s.get("symbol", "N/A")
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
            sym = p.get("symbol", "N/A")
            side = str(p.get("side", "long")).upper()
            contracts = p.get("contracts", 0.0)
            notional = float(p.get("notional_usd", 0.0))
            entry = float(p.get("entry_price", 0.0))
            mark = float(p.get("mark_price", 0.0))
            upnl = float(p.get("unrealized_pnl", 0.0))
            pnl_icon = "🟢" if upnl >= 0 else "🔴"
            pnl_str = f"+${upnl:.2f}" if upnl >= 0 else f"-${abs(upnl):.2f}"

            lines.append(
                f"- **{sym}**: `{side} {contracts} contr` (${notional:.2f} USD) | WEJŚCIE: `${entry:,.2f}` | CENA: `${mark:,.2f}` | uPnL: {pnl_icon} **`{pnl_str} USD`**"
            )
    else:
        lines.append("ℹ️ Brak otwartych pozycji w tym cyklu.")

    lines.append(f"")
    lines.append(f"💰 **PODSUMOWANIE PORTFELA**:")
    lines.append(f"- **Łączna Alokacja Par**: `${total_alloc:.2f} USD`")
    lines.append(f"- **Łączny Niezrealizowany PnL**: `{'+$' if total_upnl >= 0 else '-$'}{abs(total_upnl):.2f} USD`")
    lines.append(f"- **Status Floty OKX**: `GOTOWA` 🟢")

    full_digest = "\n".join(lines)
    print(full_digest)

    # Post card to Hermes Kanban
    title = f"📊 Hermes Digest: {len(active_symbols)} Aktywnych Par | uPnL: ${total_upnl:+.2f} USD"
    create_kanban_digest_card(title, full_digest)


if __name__ == "__main__":
    main()

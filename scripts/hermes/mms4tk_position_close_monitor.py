#!/usr/bin/env python3
"""
mms4tk Position Close Monitor for Hermes Agent (Cron Script)

Wykrywa zamknięcie pozycji na OKX i wysyła powiadomienie na Telegram z PnL.
"""

import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen


def get_api_key() -> str:
    """Safely find MMS4 API key from env or config files."""
    key = os.environ.get("MMS4_API_KEY", "")
    if key and len(key) >= 16:
        return key
    for env_path in [
        "/opt/data/.env",
        "/docker/mms4tk/.env",
        os.path.expanduser("~/.env"),
        ".env",
    ]:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("MMS4_API_KEY="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if val and len(val) >= 16:
                                return val
            except Exception:
                pass
    return "acd0f0c03314502a7bf9447956054bb86cd43abb86786b84ba67a6ba9cdb604b"


BASE = os.environ.get("MMS4TK_BASE_URL", "http://antigravity_core:8000")
API_KEY = get_api_key()

STATE_FILE = os.environ.get(
    "MMS4TK_CLOSE_MONITOR_STATE",
    "/opt/data/state/mms4tk_last_trade.json",
)


def fetch_trades(limit: int = 50):
    """Pobierz najnowsze zamknięte transakcje."""
    try:
        headers = {}
        if API_KEY:
            headers["X-MMS4-API-KEY"] = API_KEY
        req = Request(f"{BASE}/api/v1/history/trades?limit={limit}", headers=headers)
        resp = urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"⚠️ API /history/trades error: {e}", file=sys.stderr)
        return None


def send_telegram(text: str):
    """Wyślij wiadomość na Telegram via hermes CLI."""
    hermes_cli = os.environ.get("HERMES_CLI_PATH", "hermes")
    try:
        cmd = [hermes_cli, "send", "--to", "telegram", text]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        print("💬 Telegram notification dispatched")
    except Exception as e:
        print(f"⚠️ Telegram notify skipped: {e}", file=sys.stderr)


def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(state: dict):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"⚠️ Nie udało się zapisać stanu: {e}", file=sys.stderr)


def main():
    trades = fetch_trades(limit=50)
    if trades is None:
        return

    state = load_state()
    last_trade_id = state.get("last_trade_id", None)

    closed = [t for t in trades if t.get("exit_price") and float(t.get("exit_price", 0) or 0) > 0]

    if not closed:
        if last_trade_id is None:
            save_state({"last_trade_id": None})
        return

    newest = closed[-1]
    newest_id = str(newest.get("trade_id") or newest.get("id") or "")

    if last_trade_id is None:
        save_state({"last_trade_id": newest_id})
        print(f"ℹ️ Monitor zamknięć zainicjalizowany (last_trade={newest_id}).")
        return

    if newest_id != last_trade_id:
        symbol = newest.get("symbol", "N/A")
        side = str(newest.get("side", "")).upper()
        entry = float(newest.get("entry_price", 0) or 0)
        exit_p = float(newest.get("exit_price", 0) or 0)
        pnl = float(newest.get("pnl_usd", 0) or 0)
        pnl_pct_raw = float(newest.get("pnl_pct", 0) or 0)
        
        if entry > 0:
            if side == "SHORT":
                pnl_pct_calc = (entry - exit_p) / entry * 100
            else:
                pnl_pct_calc = (exit_p - entry) / entry * 100
        else:
            pnl_pct_calc = pnl_pct_raw * 100
            
        is_win = bool(newest.get("is_win", pnl >= 0))
        amount = float(newest.get("amount", 0) or 0)
        leverage = float(newest.get("leverage", 3.0) or 3.0)

        pnl_icon = "🟢" if is_win else "🔴"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        result_label = "ZYSK" if is_win else "STRATA"

        msg = (
            f"🔔 ZAMKNIĘCIE POZYCJI OKX: {symbol}\n\n"
            f"- Strona: `{side}`\n"
            f"- Ilość: `{amount:.2f}`\n"
            f"- Dźwignia: `{leverage:.0f}x`\n"
            f"- Wejście: `${entry:,.4f}`\n"
            f"- Wyjście: `${exit_p:,.4f}`\n"
            f"- Wynik: {pnl_icon} **`{pnl_str}`** ({pnl_pct_calc:+.2f}%)\n"
            f"- Rodzaj: `{result_label}`\n"
        )

        print(msg)
        send_telegram(msg)

        state["last_trade_id"] = newest_id
        save_state(state)


if __name__ == "__main__":
    main()

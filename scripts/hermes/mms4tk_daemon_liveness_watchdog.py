#!/usr/bin/env python3
"""
mms4tk Daemon Liveness Watchdog for Hermes Agent (Cron Script)

Runs OUTSIDE antigravity_core (as a Hermes cron job, or bare host cron) —
detects a stalled or dead trading loop and alerts via Telegram with a
computed suggested manual Stop-Loss price for any open position showing
significant floating profit. A hung or crashed bot leaves its in-memory
software trailing stop (Impulse Negation, inactivity_manager.py) unable to
act, and antigravity_core's own /api/v1/health is hardcoded {"status":"ok"}
— it checks that uvicorn is up, never that the daemon's run_loop is actually
cycling — and Docker does not restart a container merely marked "unhealthy".

Issue: #68 (XAG Incident Postmortem, 2026-09-28, project memory
xag-protection-gaps-audit). This is the cheap, external backstop: it
persists the last known daemon state to a HOST file on every run, so a
suggested SL price is still available at the exact moment the API stops
answering — which is precisely when it's needed.

Install via:
    hermes cron create "* * * * *" \
        --name "mms4tk Daemon Liveness Watchdog" \
        --script mms4tk_daemon_liveness_watchdog.py \
        --no-agent
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
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
    print(
        "⚠️ MMS4_API_KEY not found in env or any .env candidate path — "
        "requests will be sent unauthenticated and the API will reject them.",
        file=sys.stderr,
    )
    return ""


BASE = os.environ.get("MMS4TK_BASE_URL", "http://antigravity_core:8000")
API_KEY = get_api_key()

STATE_FILE = os.environ.get(
    "MMS4TK_WATCHDOG_STATE",
    "/opt/data/state/mms4tk_watchdog_state.json",
)

# How stale last_cycle_timestamp must be before we consider the daemon dead.
STALE_THRESHOLD_SECONDS = float(os.environ.get("MMS4TK_WATCHDOG_STALE_SECONDS", "180"))

# Floating-profit floor (USD) below which a stalled daemon isn't worth paging a human for.
MIN_PROFIT_USD_TO_ALERT = float(os.environ.get("MMS4TK_WATCHDOG_MIN_PROFIT_USD", "1.0"))


def fetch_json(path: str):
    """GET an mms4tk API endpoint. Returns None (never raises) on any failure —
    unreachability IS the signal this script exists to detect, not an error to
    crash on."""
    try:
        headers = {}
        if API_KEY:
            headers["X-MMS4-API-KEY"] = API_KEY
        req = Request(f"{BASE}{path}", headers=headers)
        resp = urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"⚠️ API {path} error: {e}", file=sys.stderr)
        return None


def compute_negation_level(side: str, entry_price: float, min_observed_price: float,
                            max_observed_price: float, atr: float) -> float:
    """
    Mirrors InactivityManager._check_impulse_negation's negation_level formula
    (src/mms4tk/inactivity_manager.py) — the same 38.2%/50% Fibonacci rule the
    live bot uses for its software trailing stop (Rule I). Kept as a small,
    standalone, pure function here — not imported from mms4tk — because this
    script runs outside the antigravity_core container/package, on a bare
    Hermes cron or host cron.

    Raises ValueError if atr <= 0 or the relevant extreme was never observed
    (min_observed_price == 0 for a short, max_observed_price == 0 for a long)
    — both mean "no usable level yet", which the caller must handle, not paper
    over with a nonsense price.
    """
    if atr <= 0:
        raise ValueError("atr must be positive")
    if side == "long":
        if max_observed_price <= 0:
            raise ValueError("max_observed_price never recorded for long position")
        local_impulse = max_observed_price - entry_price
        fib_ratio = 0.382 if local_impulse >= 0.8 * atr else 0.5
        return max_observed_price - (local_impulse * fib_ratio)
    elif side == "short":
        if min_observed_price <= 0:
            raise ValueError("min_observed_price never recorded for short position")
        local_impulse = entry_price - min_observed_price
        fib_ratio = 0.382 if local_impulse >= 0.8 * atr else 0.5
        return min_observed_price + (local_impulse * fib_ratio)
    else:
        raise ValueError(f"unknown side: {side!r}")


def is_fresh(last_cycle_iso, now: float, threshold: float = STALE_THRESHOLD_SECONDS) -> bool:
    """True when `last_cycle_iso` (ISO8601, as returned by /api/v1/daemon/status)
    is within `threshold` seconds of `now` (a time.time() epoch value)."""
    if not last_cycle_iso:
        return False
    try:
        dt = datetime.fromisoformat(str(last_cycle_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = now - dt.timestamp()
        return 0 <= age < threshold
    except Exception:
        return False


def load_state() -> dict:
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"⚠️ Nie udało się zapisać stanu watchdoga: {e}", file=sys.stderr)


def send_telegram(text: str) -> None:
    """Wyślij wiadomość na Telegram via hermes CLI (same dispatch path as the
    other scripts/hermes/*.py cron jobs — this process has no access to
    antigravity_core's in-process TelegramQueueManager, since it runs
    outside that container by design)."""
    hermes_cli = os.environ.get("HERMES_CLI_PATH", "hermes")
    try:
        cmd = [hermes_cli, "send", "--to", "telegram", text]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        print("💬 Telegram notification dispatched")
    except Exception as e:
        print(f"⚠️ Telegram notify skipped: {e}", file=sys.stderr)


def build_alert_message(status, state: dict, profitable_positions: list) -> str:
    lines = ["🚨 mms4tk DAEMON WYGLĄDA NA MARTWY", ""]
    if status is None:
        lines.append("Nie udało się połączyć z /api/v1/daemon/status — kontener może nie działać.")
    else:
        lines.append(f"Ostatni cykl daemona: {state.get('last_cycle_timestamp', 'nieznany')}")
    lines.append("")
    lines.append("Pozycje z istotnym pływającym zyskiem — rozważ ręczne ustawienie SL na OKX:")

    suggestions = state.get("suggestions", {})
    for p in profitable_positions:
        symbol = p.get("symbol", "N/A")
        side = str(p.get("side", "")).upper()
        pnl = float(p.get("unrealized_pnl") or 0.0)
        sugg = suggestions.get(symbol)
        sugg_str = f"${sugg['suggested_sl']:.4f}" if sugg else "brak (nigdy nie zaobserwowano ekstremum ceny)"
        lines.append(f"- {symbol} {side}: pływający zysk ${pnl:.2f} — sugerowany SL: {sugg_str}")

    return "\n".join(lines)


def main() -> None:
    now = time.time()
    status = fetch_json("/api/v1/daemon/status")
    positions_resp = fetch_json("/api/v1/positions")
    state = load_state()

    if status is not None:
        tracked = status.get("inactivity_manager", {}).get("positions", {})
        suggestions = {}
        for symbol, t in tracked.items():
            try:
                level = compute_negation_level(
                    side=t.get("side"),
                    entry_price=float(t.get("entry_price") or 0.0),
                    min_observed_price=float(t.get("min_observed_price") or 0.0),
                    max_observed_price=float(t.get("max_observed_price") or 0.0),
                    atr=float(t.get("atr_at_entry") or 0.0),
                )
            except (ValueError, TypeError):
                continue
            suggestions[symbol] = {"side": t.get("side"), "suggested_sl": level}

        state["last_cycle_timestamp"] = status.get("last_cycle_timestamp")
        state["suggestions"] = suggestions
        if is_fresh(state.get("last_cycle_timestamp"), now):
            state["alert_sent"] = False

    if positions_resp is not None:
        state["last_positions"] = positions_resp

    save_state(state)

    stale = status is None or not is_fresh(state.get("last_cycle_timestamp"), now)
    if not stale:
        return

    if state.get("alert_sent"):
        print("ℹ️ Daemon nadal wygląda na martwy, ale alert już wysłany dla tego epizodu — pomijam.")
        return

    live_positions = positions_resp if positions_resp is not None else state.get("last_positions")
    all_positions = (live_positions or {}).get("positions", []) if isinstance(live_positions, dict) else []
    profitable = [p for p in all_positions if float(p.get("unrealized_pnl") or 0.0) >= MIN_PROFIT_USD_TO_ALERT]

    if not profitable:
        print("ℹ️ Daemon wygląda na martwy, ale żadna pozycja nie ma istotnego pływającego zysku — nie alarmuję.")
        return

    msg = build_alert_message(status, state, profitable)
    print(msg)
    send_telegram(msg)
    state["alert_sent"] = True
    save_state(state)


if __name__ == "__main__":
    main()

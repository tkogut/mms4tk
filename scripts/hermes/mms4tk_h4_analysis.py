#!/usr/bin/env python3
"""
mms4tk H4 Analysis Cron Job for Hermes Agent

Runs as Hermes cron job every 4 hours (0 */4 * * *).
Fetches H4/D1 analytics from mms4tk and updates the strategy profile
based on MastermindZX rules:

1. Evaluates H4 candle formation (SWEEP_WICK vs MARUBOZU_BREAKOUT)
2. If uncertain, creates a Blocked Kanban card for HITL

Faza 6 C.1.1 (2026-09-13): this cron no longer sets `directional_bias` —
that competency moved entirely to the internal per-symbol H4 directional
gate (Faza 3.2, `daemon._refresh_htf_directional_gate`), which reads real
per-symbol H4 candles every 15s instead of this cron's single global,
BTC-only reading every 4h (audit finding: the cron's global override was
measured to wrongly restrict ~5.9% of ETH H4 candles vs its own per-symbol
reading). The H4 TMA position (`h4_pos`) is still fetched and logged to
stdout below for cron-log diagnostics, just never sent to
`POST /api/v1/strategy/profile` — thanks to that endpoint's Faza 6 C.1.2
partial-update support, omitting `directional_bias` from the payload now
safely leaves whatever the internal gate (or a human) already set, instead
of resetting it to "BOTH" on every 4h write.

Install via:
    hermes cron create "0 */4 * * *" \
        --name "mms4tk H4 Analysis" \
        --script mms4tk_h4_analysis.py \
        --no-agent \
        --deliver telegram
"""

import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen

MMS4TK_BASE = os.environ.get("MMS4TK_BASE_URL", "http://antigravity_core:8000")
MMS4TK_API_KEY = os.environ.get("MMS4_API_KEY", "")


def fetch_higher_tf():
    """Fetch H4/D1 analytics from mms4tk."""
    try:
        headers = {}
        if MMS4TK_API_KEY:
            headers["X-MMS4-API-KEY"] = MMS4TK_API_KEY
        req = Request(f"{MMS4TK_BASE}/api/v1/analytics/higher_tf", headers=headers)
        resp = urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"❌ Failed to fetch H4/D1 analytics: {e}", file=sys.stderr)
        return None


def fetch_state():
    """Fetch current engine state from mms4tk."""
    try:
        headers = {}
        if MMS4TK_API_KEY:
            headers["X-MMS4-API-KEY"] = MMS4TK_API_KEY
        req = Request(f"{MMS4TK_BASE}/api/v1/state", headers=headers)
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"❌ Failed to fetch state: {e}", file=sys.stderr)
        return None


def update_strategy_profile(profile_data):
    """Update strategy profile in mms4tk."""
    try:
        payload = json.dumps(profile_data).encode()
        req = Request(
            f"{MMS4TK_BASE}/api/v1/strategy/profile",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-MMS4-API-KEY": MMS4TK_API_KEY,
            },
            method="POST",
        )
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"❌ Failed to update strategy profile: {e}", file=sys.stderr)
        return None


def create_hitl_card(analytics, state):
    """Create a Blocked Kanban card for human review when uncertain."""
    title = "H4 Analysis Uncertain — HITL Required"
    body = (
        f"## Uncertain H4 Candle Classification\n\n"
        f"**H4 TMA Position:** {analytics.get('h4_tma_position', 'N/A')}\n"
        f"**H4 Candle Formation:** {analytics.get('h4_candle_formation', 'N/A')}\n"
        f"**ATR D1 Expansion Ratio:** {analytics.get('atr_d1_expansion_ratio', 'N/A')}\n"
        f"**Current State:** {state.get('state', 'N/A')}\n"
        f"**Current Leverage:** {state.get('leverage', 'N/A')}x\n\n"
        f"### Action Required\n"
        f"Review H4 chart and classify:\n"
        f"- **SWEEP_WICK** → Approve scout_override (relax to BASE_MODE)\n"
        f"- **MARUBOZU_BREAKOUT** → Maintain SCOUT_MODE\n\n"
        f"Then call `POST /api/v1/hermes/hitl_resolve` with your decision.\n"
    )

    cmd = [
        "hermes", "kanban", "create",
        title,
        "--body", body,
        "--priority", "9",
        "--created-by", "mms4tk-h4-cron",
        "--initial-status", "blocked",
        "--json",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"📋 HITL card created: {result.stdout.strip()[:80]}")
        else:
            print(f"❌ Failed to create HITL card: {result.stderr}", file=sys.stderr)
    except Exception as e:
        print(f"❌ Exception creating HITL card: {e}", file=sys.stderr)


def main():
    analytics = fetch_higher_tf()
    if not analytics:
        print("⚠️ No analytics data available. Skipping cycle.")
        return

    state = fetch_state() or {}
    current_engine_state = state.get("state", "UNKNOWN")

    h4_pos = analytics.get("h4_tma_position", "INSIDE")
    h4_candle = analytics.get("h4_candle_formation", "NEUTRAL")
    atr_ratio = analytics.get("atr_d1_expansion_ratio", 0.0)

    print(f"📊 H4 Analysis Report:")
    print(f"   H4 TMA Position: {h4_pos}")
    print(f"   H4 Candle Formation: {h4_candle}")
    print(f"   ATR D1 Expansion Ratio: {atr_ratio}")
    print(f"   Engine State: {current_engine_state}")

    # ─── MastermindZX Rules ───────────────────────────────────────────

    # Rule 1 (directional_bias from H4 TMA position) removed — Faza 6 C.1.1.
    # h4_pos is still fetched/logged above for diagnostics only; the internal
    # H4 directional gate (Faza 3.2) now owns directional_bias exclusively.

    # Rule 2: Risk Profile from D1 ATR Expansion
    if atr_ratio > 2.5:
        risk = "CONSERVATIVE"
        print(f"   ⚠️ D1 ATR Overextension ({atr_ratio}x > 2.5x) → CONSERVATIVE")
    elif atr_ratio > 1.5:
        risk = "MODERATE"
    else:
        risk = "MODERATE"

    # Rule 3: SCOUT_MODE + H4 Candle Classification
    scout_override = False
    if current_engine_state == "SCOUT_MODE":
        if h4_candle == "SWEEP_WICK":
            scout_override = True
            print(f"   ✅ H4 Sweep Wick detected → scout_override=True (relax to BASE_MODE)")
        elif h4_candle == "MARUBOZU_BREAKOUT":
            print(f"   🔒 H4 Marubozu Breakout → maintaining SCOUT_MODE")
        elif h4_candle == "NEUTRAL":
            # Uncertain case → HITL
            print(f"   ❓ H4 NEUTRAL in SCOUT_MODE → creating HITL card for human review")
            create_hitl_card(analytics, state)
            return  # Don't auto-update profile on uncertain classification

    # Build and push profile update — directional_bias deliberately omitted
    # (Faza 6 C.1.1); the partial-update endpoint (C.1.2) merges this onto
    # whatever directional_bias is already set, leaving it untouched.
    profile = {
        "risk_profile": risk,
        "scout_override": scout_override,
        "reason": f"H4 Cron: pos={h4_pos}, candle={h4_candle}, atr_ratio={atr_ratio}",
    }

    result = update_strategy_profile(profile)
    if result:
        print(f"\n   ✅ Strategy Profile Updated:")
        print(f"      directional_bias: {result.get('directional_bias')}")
        print(f"      risk_profile: {result.get('risk_profile')}")
        print(f"      scout_override: {result.get('scout_override')}")
        print(f"      reason: {result.get('reason')}")
    else:
        print(f"\n   ❌ Failed to update strategy profile")


if __name__ == "__main__":
    main()

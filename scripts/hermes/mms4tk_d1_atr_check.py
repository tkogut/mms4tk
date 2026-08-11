#!/usr/bin/env python3
"""
mms4tk D1 ATR Overextension Monitor for Hermes Agent

Runs as Hermes cron job once daily after D1 candle close (5 0 * * *).
Checks if daily range exceeds 2.5x ATR D1 → signals CONSERVATIVE mode.

Install via:
    hermes cron create "5 0 * * *" \
        --name "mms4tk D1 ATR Monitor" \
        --script mms4tk_d1_atr_check.py \
        --no-agent \
        --deliver telegram
"""

import json
import os
import sys
from urllib.request import Request, urlopen

MMS4TK_BASE = os.environ.get("MMS4TK_BASE_URL", "http://antigravity_core:8000")
MMS4TK_API_KEY = os.environ.get("MMS4_API_KEY", "")


def fetch_higher_tf():
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


def fetch_current_profile():
    try:
        headers = {}
        if MMS4TK_API_KEY:
            headers["X-MMS4-API-KEY"] = MMS4TK_API_KEY
        req = Request(f"{MMS4TK_BASE}/api/v1/strategy/profile", headers=headers)
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"❌ Failed to fetch current profile: {e}", file=sys.stderr)
        return None


def update_strategy_profile(profile_data):
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


def main():
    analytics = fetch_higher_tf()
    if not analytics:
        print("⚠️ No analytics data. Skipping D1 ATR check.")
        return

    atr_d1 = analytics.get("atr_d1", 0.0)
    atr_ratio = analytics.get("atr_d1_expansion_ratio", 0.0)

    print(f"📊 D1 ATR Daily Report:")
    print(f"   ATR D1: {atr_d1}")
    print(f"   ATR D1 Expansion Ratio: {atr_ratio}")

    current_profile = fetch_current_profile()
    if not current_profile:
        return

    current_risk = current_profile.get("risk_profile", "MODERATE")

    # MastermindZX Rule: ATR D1 Overextension
    if atr_ratio > 2.5:
        if current_risk != "CONSERVATIVE":
            print(f"   ⚠️ OVEREXTENSION DETECTED (ratio={atr_ratio} > 2.5)")
            print(f"   Switching risk_profile: {current_risk} → CONSERVATIVE")
            result = update_strategy_profile({
                "directional_bias": current_profile.get("directional_bias", "BOTH"),
                "risk_profile": "CONSERVATIVE",
                "scout_override": current_profile.get("scout_override", False),
                "reason": f"D1 ATR Overextension ({atr_ratio:.2f}x > 2.5x) — Złota Zasada 3 Dni",
            })
            if result:
                print(f"   ✅ Profile updated to CONSERVATIVE")
        else:
            print(f"   ℹ️ Already in CONSERVATIVE mode. No change needed.")
    elif atr_ratio <= 1.0:
        if current_risk == "CONSERVATIVE":
            print(f"   ✅ ATR normalized ({atr_ratio:.2f}x ≤ 1.0x)")
            print(f"   Switching risk_profile: CONSERVATIVE → MODERATE")
            result = update_strategy_profile({
                "directional_bias": current_profile.get("directional_bias", "BOTH"),
                "risk_profile": "MODERATE",
                "scout_override": current_profile.get("scout_override", False),
                "reason": f"D1 ATR normalized ({atr_ratio:.2f}x ≤ 1.0x) — risk restored",
            })
            if result:
                print(f"   ✅ Profile restored to MODERATE")
        else:
            print(f"   ✅ ATR within normal range ({atr_ratio:.2f}x). Profile: {current_risk}")
    else:
        print(f"   ✅ ATR within acceptable range ({atr_ratio:.2f}x). No changes.")


if __name__ == "__main__":
    main()

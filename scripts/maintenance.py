#!/usr/bin/env python3
"""
MMS4 Maintenance Lock CLI Tool.
Blocks or enables live trading on OKX during developer maintenance, updates, or testing.
Self-contained without heavy third-party dependencies.
"""
import sys
import argparse
import os
from datetime import datetime, timezone

MAINTENANCE_LOCK_PATHS = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".agents", "swarm", "MAINTENANCE_LOCK")),
    "/app/.agents/swarm/MAINTENANCE_LOCK",
    "/tmp/MMS4_MAINTENANCE_LOCK",
    os.path.expanduser("~/.mms4_maintenance_lock"),
]


def is_maintenance_locked() -> bool:
    """Returns True if live trading is blocked due to Maintenance Mode."""
    if os.getenv("MMS4_MAINTENANCE_MODE", "false").lower() in ["true", "1", "yes", "on"]:
        return True
    return any(os.path.exists(p) for p in MAINTENANCE_LOCK_PATHS)


def set_maintenance_lock(enable: bool, reason: str = "Developer Maintenance") -> bool:
    """Enable or disable Maintenance Mode by creating or removing the lock files."""
    try:
        if enable:
            for lock_path in MAINTENANCE_LOCK_PATHS:
                try:
                    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
                    with open(lock_path, "w", encoding="utf-8") as f:
                        f.write(f"LOCKED at {datetime.now(timezone.utc).isoformat()} - Reason: {reason}\n")
                except Exception:
                    pass
            print(f"🔒 MAINTENANCE MODE ENABLED: Live OKX trading is BLOCKED. (Reason: {reason})")
            return True
        else:
            for p in MAINTENANCE_LOCK_PATHS:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            print("🟢 MAINTENANCE MODE DISABLED: Live OKX trading is RESUMED.")
            return True
    except Exception as e:
        print(f"❌ Error setting maintenance lock: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="MMS4 Trading Maintenance Lock Manager")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--on", "--enable", action="store_true", help="Enable Maintenance Lock (Block real OKX trading)")
    group.add_argument("--off", "--disable", action="store_true", help="Disable Maintenance Lock (Resume OKX trading)")
    group.add_argument("--status", action="store_true", help="Check current Maintenance Lock status")
    parser.add_argument("--reason", default="Manual Developer Maintenance", help="Reason for maintenance lock")

    args = parser.parse_args()

    if args.status:
        locked = is_maintenance_locked()
        if locked:
            print("🔒 STATUS: MAINTENANCE MODE ACTIVE — Real OKX trading is BLOCKED.")
            sys.exit(1)
        else:
            print("🟢 STATUS: LIVE TRADING ACTIVE — Real OKX trading is ENABLED.")
            sys.exit(0)
    elif args.on:
        set_maintenance_lock(True, reason=args.reason)
    elif args.off:
        set_maintenance_lock(False)


if __name__ == "__main__":
    main()

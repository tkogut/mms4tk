#!/usr/bin/env python3
"""
MMS4 Maintenance Lock CLI Tool.
Blocks or enables live trading on OKX during developer maintenance, updates, or testing.
"""
import sys
import argparse
import os

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from mms4tk.okx_executor import is_maintenance_locked, set_maintenance_lock


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
        print(f"🔒 Maintenance Mode ENABLED. All live order execution on OKX is now BLOCKED. Reason: {args.reason}")
    elif args.off:
        set_maintenance_lock(False)
        print("🟢 Maintenance Mode DISABLED. Live trading on OKX is now RESUMED.")


if __name__ == "__main__":
    main()

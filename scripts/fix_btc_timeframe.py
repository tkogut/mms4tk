#!/usr/bin/env python3
"""
One-time correction: reset BTC/USDC's `symbol_configurations.timeframe` back to
`1h` if it currently holds anything else, following the exact dry-run/--apply
pattern of `scripts/bump_data_collection_epoch.py` / `scripts/reclassify_exit_reason_labels.py`.

Root cause (2026-10-27 incident, fixed in `src/mms4tk/api.py` alongside this
script): `POST /api/v1/symbols/update` used to rebuild a symbol's entire stored
config straight from `AddSymbolRequest`, whose `timeframe` field defaulted to
`15m` — out of sync with `symbols.json` / `load_symbols_config()`'s `defaults`
dict, which has always intended `1h` for every symbol including BTC/USDC. A
2026-10-21 bulk `allocation_usd` update (all 4 symbols) that omitted
`timeframe` silently reset BTC/USDC's stored value to that mismatched `15m`
schema default; ETH/XAU/XAG were unaffected only because their most recent
prior update happened to also carry `timeframe=1h` explicitly. The API fix
makes /update preserve any field the caller's request didn't explicitly set,
closing this bug class going forward — this script is the one-time correction
for the value already wrong in a deployed database.

Only ever touches BTC/USDC. Every other symbol is left untouched regardless
of its current timeframe, even if it also happens to differ from `1h` — this
script is a targeted correction for the one confirmed-affected symbol, not a
general "reset everything to 1h" tool.

Usage:
    # Inspect current value, without writing anything (default):
    python3 scripts/fix_btc_timeframe.py --db-path .agents/swarm/logs.db

    # Apply the correction:
    python3 scripts/fix_btc_timeframe.py --db-path .agents/swarm/logs.db --apply

Always back up the database file before running with --apply:
    cp .agents/swarm/logs.db .agents/swarm/logs.db.bak_$(date +%Y%m%d)

IMPORTANT — restart the running process right after --apply (or stop it first):
`api.py`'s `configured_symbols_store` (in-memory, loaded once at process start
from this same SQLite table) is NOT updated by this script. The NEXT call to
`POST /api/v1/symbols/update` for ANY symbol calls `save_symbols_config(
configured_symbols_store)`, which rewrites every symbol's row from that stale
in-memory dict — silently clobbering this correction back to `15m` if the
process hasn't restarted since. Restart `antigravity_core` immediately after
applying, before any dashboard/API `/update` call touches any symbol.
"""

import sys
import argparse

from mms4tk.logger_db import LoggerDB

TARGET_SYMBOL = "BTC/USDC"
CORRECT_TIMEFRAME = "1h"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", required=True, help="Path to logs.db")
    parser.add_argument("--apply", action="store_true", help="Write the change. Without this flag, only prints a dry-run report.")
    args = parser.parse_args()

    db = LoggerDB(db_path=args.db_path)

    configs = db.get_all_symbol_configs()
    cfg = configs.get(TARGET_SYMBOL)

    print(f"{'APPLYING' if args.apply else 'DRY RUN'} — target: {TARGET_SYMBOL}, correct timeframe: {CORRECT_TIMEFRAME}\n")

    if cfg is None:
        print(f"  {TARGET_SYMBOL}: no symbol_configurations row found — nothing to correct.")
        return 0

    current_tf = cfg["timeframe"]
    if current_tf == CORRECT_TIMEFRAME:
        print(f"  {TARGET_SYMBOL}: timeframe already {CORRECT_TIMEFRAME!r} — no-op.")
        return 0

    print(f"  {TARGET_SYMBOL}: timeframe {current_tf!r} -> {CORRECT_TIMEFRAME!r} "
          f"(is_active={cfg['is_active']}, allocation_usd={cfg['allocation_usd']}, "
          f"base_ratio={cfg['base_ratio']}, add_ratio={cfg['add_ratio']} — all preserved)")

    if args.apply:
        db.save_symbol_config(
            symbol=TARGET_SYMBOL,
            is_active=cfg["is_active"],
            allocation_usd=cfg["allocation_usd"],
            base_ratio=cfg["base_ratio"],
            add_ratio=cfg["add_ratio"],
            timeframe=CORRECT_TIMEFRAME,
        )
        print(
            "\nApplied.\n"
            "WARNING: restart the running process (e.g. `antigravity_core`) now, before any "
            "/api/v1/symbols/update call for ANY symbol — otherwise its stale in-memory "
            "configured_symbols_store will overwrite this correction back to the old value "
            "on the next such call (save_symbols_config() rewrites every symbol's row)."
        )
    else:
        print("\nDry run only — pass --apply to write this change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

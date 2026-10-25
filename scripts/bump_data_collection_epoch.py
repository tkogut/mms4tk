#!/usr/bin/env python3
"""
Bump `data_collection_epoch_start:<SYMBOL>` in `system_settings`, replacing the
undocumented ad hoc `UPDATE system_settings ...` SQL used for past epoch bumps
(see `.agents/MEMORY.md` / PLAN-008/PLAN-010 history).

`LoggerDB.count_clean_closed_trades(symbol)` only counts `closed_trades` rows at or
after this timestamp — it is the "do not mix exit-execution regimes into one sample"
fence described in `.ai/analysis/2026-10-14-sl-loss-attribution.md` and
`.ai/analysis/2026-10-22-b0-baseline-replay.md`. Any behavior-changing deploy
(an entry/exit/filter rule change, not a pure bug/observability fix) should bump the
epoch for every symbol it affects, using the deployed container's actual start time
(`docker inspect antigravity_core --format '{{.State.StartedAt}}'`), not the merge
commit's timestamp — there can be a build/deploy delay.

Usage:
    # Inspect current values, without writing anything (default):
    python3 scripts/bump_data_collection_epoch.py --db-path .agents/swarm/logs.db \\
        --symbol BTC/USDC --symbol ETH/USDC --symbol XAU/USDC --symbol XAG/USDC

    # Apply, using the real container start time:
    python3 scripts/bump_data_collection_epoch.py --db-path .agents/swarm/logs.db \\
        --symbol BTC/USDC --symbol ETH/USDC --symbol XAU/USDC --symbol XAG/USDC \\
        --at 2026-10-25T21:00:00+00:00 --apply --updated-by builder_pr143_deploy

Always back up the database file before running with --apply:
    cp .agents/swarm/logs.db .agents/swarm/logs.db.bak_$(date +%Y%m%d)
"""

import argparse
import sys
from datetime import datetime, timezone

from mms4tk.logger_db import LoggerDB


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", required=True, help="Path to logs.db")
    parser.add_argument(
        "--symbol", action="append", required=True, dest="symbols",
        help="Symbol to bump (repeatable), e.g. --symbol BTC/USDC",
    )
    parser.add_argument(
        "--at", default=None,
        help="ISO8601 UTC timestamp for the new epoch (default: now). Prefer the "
             "deployed container's real start time over the merge commit's timestamp.",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag, only prints a dry-run report.")
    parser.add_argument("--updated-by", default="manual_epoch_bump", help="Recorded in system_settings.updated_by")
    args = parser.parse_args()

    new_epoch = args.at or datetime.now(timezone.utc).isoformat()
    try:
        datetime.fromisoformat(new_epoch.replace("Z", "+00:00"))
    except ValueError:
        print(f"ERROR: --at value is not a valid ISO8601 timestamp: {new_epoch!r}", file=sys.stderr)
        return 2

    db = LoggerDB(db_path=args.db_path)

    print(f"{'APPLYING' if args.apply else 'DRY RUN'} — new epoch: {new_epoch}\n")
    for symbol in args.symbols:
        key = f"data_collection_epoch_start:{symbol}"
        before = db.get_setting(key)
        before_count = db.count_clean_closed_trades(symbol)
        print(f"  {symbol}: {before or '(unset)'} ({before_count} clean trades) -> {new_epoch} (0 clean trades)")
        if args.apply:
            db.set_setting(key, new_epoch, updated_by=args.updated_by)

    if not args.apply:
        print("\nDry run only — pass --apply to write these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

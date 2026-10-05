#!/usr/bin/env python3
"""
One-time migration for issue #91: clear the cached `reason` on closed_trades rows
tagged STALE_SPLIT_50/MIDLINE_PARTIAL_TP so LoggerDB.get_closed_trades() reclassifies
them with the fixed matcher (PR #93) on its next read.

Background: get_closed_trades()'s cycle_logs lookup used to match a trade's own
closing fill against the nearest cycle_logs row within a +/-180s window, with no way
to tell "the row for *this* closing fill" from "the row for a different, earlier
closing event on the same symbol." PR #93 narrowed this to a 30s tolerance for
partial-close signal types (STALE_SPLIT_50, MIDLINE_PARTIAL_TP) — but the fix only
applies to rows whose `reason` column is still NULL at read time (the
`already_processed` cache gate). Rows that were already read and cached with the
wrong reason BEFORE the fix deployed keep the stale value forever unless explicitly
reset.

This script does not reclassify anything itself — it only clears `reason` to NULL on
the rows that need a fresh look, then calls the real get_closed_trades() (the same
code path production uses) so the already-fixed classifier recomputes and persists
the correct value. No reimplementation of the matching logic here, by design — the
production code is the single source of truth for what "correct" means.

Usage:
    # Inspect what would be cleared, without writing anything (default):
    python3 scripts/reclassify_split_50_reason.py --db-path .agents/swarm/logs.db

    # Apply: clear + immediately reclassify via get_closed_trades():
    python3 scripts/reclassify_split_50_reason.py --db-path .agents/swarm/logs.db --apply

Always back up the database file before running with --apply:
    cp .agents/swarm/logs.db .agents/swarm/logs.db.bak_$(date +%Y%m%d)
"""

import argparse
import sqlite3
import sys


AFFECTED_REASONS = ("STALE_SPLIT_50", "MIDLINE_PARTIAL_TP")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", required=True, help="Path to logs.db")
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag, only prints a dry-run report.")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    placeholders = ",".join("?" for _ in AFFECTED_REASONS)
    cursor.execute(
        f"SELECT trade_id, symbol, side, timestamp, reason FROM closed_trades "
        f"WHERE reason IN ({placeholders}) ORDER BY timestamp",
        AFFECTED_REASONS,
    )
    rows = cursor.fetchall()

    if not rows:
        print("No STALE_SPLIT_50/MIDLINE_PARTIAL_TP rows found. Nothing to do.")
        return 0

    print(f"{'trade_id':<28} {'symbol':<26} {'side':<6} {'timestamp':<30} {'current reason'}")
    print("-" * 110)
    for row in rows:
        print(f"{row['trade_id']:<28} {row['symbol']:<26} {row['side']:<6} {row['timestamp']:<30} {row['reason']}")
    print(f"\n{len(rows)} row(s) will be reset to NULL and reclassified.")

    if not args.apply:
        print("\nDry run only — pass --apply to clear and reclassify these rows.")
        conn.close()
        return 0

    cursor.execute(
        f"UPDATE closed_trades SET reason = NULL WHERE reason IN ({placeholders})",
        AFFECTED_REASONS,
    )
    conn.commit()
    conn.close()

    # Reuse the real, already-fixed classifier (LoggerDB.get_closed_trades) instead
    # of reimplementing the matching logic here.
    sys.path.insert(0, "src")
    from mms4tk.logger_db import LoggerDB

    db = LoggerDB(db_path=args.db_path)
    trades = {t["trade_id"]: t for t in db.get_closed_trades(limit=None)}

    print(f"\n{'trade_id':<28} {'symbol':<26} {'new reason'}")
    print("-" * 70)
    changed = 0
    for row in rows:
        new_reason = trades.get(row["trade_id"], {}).get("reason")
        marker = " (changed)" if new_reason != row["reason"] else ""
        if marker:
            changed += 1
        print(f"{row['trade_id']:<28} {row['symbol']:<26} {new_reason}{marker}")

    print(f"\nDone. {changed}/{len(rows)} row(s) reclassified to a different reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

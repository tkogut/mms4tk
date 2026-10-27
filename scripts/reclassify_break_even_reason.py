#!/usr/bin/env python3
"""
One-time migration: recompute pnl_pct and reclassify mislabeled "BREAK-EVEN" rows
in closed_trades.

Background: before the ctVal scaling fix in src/mms4tk/trade_sync.py, `pnl_pct` was
computed from `exit_price * size` (OKX contracts, not coins) instead of
`exit_price * size * contractSize`. For BTC this inflated notional/margin by
~10000x, ETH/XAU by ~1000x, XAG by ~100x — driving every real stop-loss's pnl_pct
toward zero, so LoggerDB.get_closed_trades()'s `pnl_pct <= -0.015` fallback never
classified it as "STOP-LOSS" and it fell through to the generic "BREAK-EVEN" label.

The code fix only affects rows synced AFTER it ships. This script repairs rows
already persisted with the wrong pnl_pct (and therefore the wrong reason) by
recomputing pnl_pct from columns already stored on each row (entry_price,
exit_price, amount, leverage, pnl_net_usd) plus the same per-symbol contractSize
fallback table daemon.py/trade_sync.py use, then clearing `reason` so
get_closed_trades() reclassifies the row correctly on its next read (using the
corrected pnl_pct).

Only rows with reason='BREAK-EVEN' are touched — other reasons (TAKE-PROFIT,
STALE_*, FORCE_CLOSE, ...) were not driven by the pnl_pct bug and are left alone.

Usage:
    # Inspect what would change, without writing anything (default):
    python3 scripts/reclassify_break_even_reason.py --db-path .agents/swarm/logs.db

    # Apply the fix:
    python3 scripts/reclassify_break_even_reason.py --db-path .agents/swarm/logs.db --apply

Always back up the database file before running with --apply:
    cp .agents/swarm/logs.db .agents/swarm/logs.db.bak_$(date +%Y%m%d)
"""

import argparse
import sqlite3
import sys


def get_contract_size(symbol: str) -> float:
    """Mirrors MMS4Daemon._get_contract_size's fallback table (daemon.py) — this
    script has no live exchange connection, so it can only use the fallback, not a
    markets lookup. Fine for BTC/ETH/XAU/XAG/SOL, mms4tk's traded universe."""
    sym_upper = symbol.upper()
    if "BTC" in sym_upper:
        return 0.0001
    if "ETH" in sym_upper or "XAU" in sym_upper:
        return 0.001
    if "XAG" in sym_upper:
        return 0.01
    return 1.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", required=True, help="Path to logs.db")
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag, only prints a dry-run report.")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT trade_id, symbol, exit_price, amount, leverage, pnl_net_usd, pnl_pct
        FROM closed_trades
        WHERE reason = 'BREAK-EVEN'
    """)
    rows = cursor.fetchall()

    if not rows:
        print("No BREAK-EVEN rows found. Nothing to do.")
        return 0

    print(f"{'trade_id':<28} {'symbol':<28} {'old pnl_pct':>14} {'new pnl_pct':>14}  new_reason")
    print("-" * 100)

    updates = []
    for row in rows:
        symbol = row["symbol"]
        exit_price = float(row["exit_price"] or 0.0)
        amount = float(row["amount"] or 0.0)
        leverage = float(row["leverage"] or 1.0) or 1.0
        pnl_net = float(row["pnl_net_usd"] or 0.0)
        old_pnl_pct = float(row["pnl_pct"] or 0.0)

        ct_size = get_contract_size(symbol)
        notional = exit_price * amount * ct_size
        margin = (notional / leverage) if leverage > 0 else notional
        new_pnl_pct = (pnl_net / margin) if margin > 0 else 0.0

        if new_pnl_pct <= -0.015:
            new_reason = "STOP-LOSS"
        elif pnl_net > 0.05:
            new_reason = "TAKE-PROFIT"
        else:
            new_reason = "BREAK-EVEN"

        print(f"{row['trade_id']:<28} {symbol:<28} {old_pnl_pct:>14.6f} {new_pnl_pct:>14.6f}  {new_reason}")
        updates.append((new_pnl_pct, new_reason, row["trade_id"]))

    changed = sum(1 for _, new_reason, _ in updates if new_reason != "BREAK-EVEN")
    print("-" * 100)
    print(f"{len(updates)} BREAK-EVEN row(s) inspected, {changed} would be reclassified.")

    if not args.apply:
        print("\nDry run only — no changes written. Re-run with --apply to write.")
        conn.close()
        return 0

    cursor.executemany(
        "UPDATE closed_trades SET pnl_pct = ?, reason = ? WHERE trade_id = ?",
        updates,
    )
    conn.commit()
    conn.close()
    print(f"\nApplied: updated pnl_pct and reason for {len(updates)} row(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

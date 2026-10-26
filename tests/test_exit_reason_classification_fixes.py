"""Two classification bugs in `LoggerDB.get_closed_trades()`, found 2026-10-26 while
auditing why STOP-LOSS exits routinely cost $2-6 while TAKE-PROFIT exits rarely
exceed ~$1 (`.ai/runs/2026-10-26-fix-exit-reason-labels.md`):

1. `EXECUTED_TP_AND_FLIP_*` outcomes (daemon.py sets
   `outcome = f"EXECUTED_TP_AND_FLIP_{signal.signal_type}"`) had no mapping here, so
   real band take-profits fell through to the PnL fallback and mostly landed on
   "BREAK-EVEN" instead of "TAKE-PROFIT".
2. That fallback's STOP-LOSS branch thresholded on `pnl_pct`, which is margin-based
   (leveraged) rather than the real price move — at 3x leverage this made the check
   ~4x more sensitive than the real ~2% stop-loss distance.

Both bugs independently inflated the apparent STOP-LOSS count/share in every
exit-reason-grouped P&L report this project produces.
"""
import sqlite3
from datetime import datetime, timezone

from mms4tk.logger_db import LoggerDB


def _insert_cycle_log(db_file, timestamp, symbol, signal_type, outcome):
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            "INSERT INTO cycle_logs (timestamp, symbol, signal_type, state, leverage, price, outcome, details) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp, symbol, signal_type, "BASE_MODE", 3.0, 0.0, outcome, ""),
        )
        conn.commit()


def test_tp_and_flip_outcome_is_classified_as_take_profit(tmp_path):
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)
    trade_ts = datetime(2026, 10, 20, 6, 0, 10, tzinfo=timezone.utc)

    db.upsert_closed_trade(
        trade_id="tp_flip_1", timestamp=trade_ts.isoformat(), symbol="ETH/USDC",
        side="SHORT", entry_price=2500.0, exit_price=2480.0, amount=10.0,
        pnl_usd=0.20, pnl_pct=0.008, is_win=True, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=0.15,
    )
    _insert_cycle_log(
        db_file, trade_ts.isoformat(), "ETH/USDC",
        "BASE_LONG", "EXECUTED_TP_AND_FLIP_BASE_LONG",
    )

    trades = db.get_closed_trades()
    assert trades[0]["reason"] == "TAKE-PROFIT"


def test_fallback_stop_loss_uses_price_move_not_margin_pnl_pct(tmp_path):
    """At 3x leverage, a genuine ~-0.5% price move produces pnl_pct <= -0.015
    (margin-based) even though the real stop-loss distance is ~2% of price. The
    fixed fallback must classify this as BREAK-EVEN, not STOP-LOSS."""
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)

    # LONG, 0.5% adverse price move, 3x leverage -> pnl_pct = -0.015 (the exact
    # value that used to trip the old margin-based threshold).
    db.upsert_closed_trade(
        trade_id="small_adverse_move", timestamp="2026-10-20T06:00:00+00:00",
        symbol="BTC/USDC", side="LONG", entry_price=80000.0, exit_price=79600.0,
        amount=1.0, pnl_usd=-1.2, pnl_pct=-0.015, is_win=False, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=-0.01,
    )

    trades = db.get_closed_trades()
    assert trades[0]["reason"] == "BREAK-EVEN", (
        "a 0.5% price move must not be classified as STOP-LOSS just because 3x "
        "leverage makes margin-based pnl_pct look large"
    )


def test_fallback_stop_loss_still_fires_on_a_real_adverse_move(tmp_path):
    """A genuine ~2% adverse price move must still classify as STOP-LOSS under the
    corrected, price-move-based fallback."""
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)

    db.upsert_closed_trade(
        trade_id="real_stop_out", timestamp="2026-10-20T06:00:00+00:00",
        symbol="BTC/USDC", side="SHORT", entry_price=80000.0, exit_price=81600.0,
        amount=1.0, pnl_usd=-1.6, pnl_pct=-0.06, is_win=False, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=-0.01,
    )

    trades = db.get_closed_trades()
    assert trades[0]["reason"] == "STOP-LOSS"


def test_fallback_take_profit_threshold_is_unaffected(tmp_path):
    """The pnl_net-based TAKE-PROFIT fallback branch is untouched by this fix."""
    db_file = str(tmp_path / "logs.db")
    db = LoggerDB(db_path=db_file)

    db.upsert_closed_trade(
        trade_id="plain_win", timestamp="2026-10-20T06:00:00+00:00",
        symbol="BTC/USDC", side="LONG", entry_price=80000.0, exit_price=80100.0,
        amount=1.0, pnl_usd=0.30, pnl_pct=0.004, is_win=True, leverage=3.0,
        fee_usd=0.05, pnl_net_usd=0.20,
    )

    trades = db.get_closed_trades()
    assert trades[0]["reason"] == "TAKE-PROFIT"

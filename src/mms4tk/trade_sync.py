"""
OKX Trade History Sync Worker (mms4tk.trade_sync)

Polls closed orders/trades history from OKX API (v5) every 15 minutes
and upserts normalized trade records into SQLite `closed_trades` table in `logs.db`.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from mms4tk.okx_executor import OKXExecutor
from mms4tk.logger_db import LoggerDB

logger = logging.getLogger("mms4tk.trade_sync")


class OKXTradeSyncWorker:
    """
    Background worker that periodically syncs OKX closed orders/trades
    and persists them to SQLite `closed_trades` table for fast, sub-millisecond Streamlit analytics.
    """

    def __init__(
        self,
        executor: Optional[OKXExecutor] = None,
        logger_db: Optional[LoggerDB] = None,
        interval_seconds: float = 900.0  # 15 minutes default
    ) -> None:
        self.executor = executor or OKXExecutor()
        self.logger_db = logger_db or LoggerDB()
        self.interval_seconds = interval_seconds
        self._is_running: bool = False
        self._task: Optional[asyncio.Task] = None

    @staticmethod
    def _is_closing_fill(fill: Dict[str, Any]) -> bool:
        """
        A fill closes a position when its order side opposes its position side
        (sell on a long, buy on a short). A non-zero realized PnL confirms it.
        """
        if abs(fill["pnl_usd"]) > 1e-12:
            return True
        pos_side, side = fill["pos_side"], fill["side"]
        if pos_side == "long":
            return side == "sell"
        if pos_side == "short":
            return side == "buy"
        return False

    @staticmethod
    def _consume_entry_price(open_queue: List[Dict[str, Any]], size: float) -> float:
        """
        FIFO-match `size` against queued opening fills and return their size-weighted
        average price. Consumed quantity is removed from the queue.
        """
        if not open_queue or size <= 0:
            return 0.0
        remaining = size
        weighted = 0.0
        matched = 0.0
        while open_queue and remaining > 1e-12:
            leg = open_queue[0]
            take = min(leg["size"], remaining)
            weighted += leg["price"] * take
            matched += take
            remaining -= take
            leg["size"] -= take
            if leg["size"] <= 1e-12:
                open_queue.pop(0)
        return (weighted / matched) if matched > 0 else 0.0

    @staticmethod
    def _infer_entry_price(fill: Dict[str, Any]) -> float:
        """
        Reconstruct the entry price from the exit price and realized PnL when the opening
        fill predates the sync window. Falls back to the exit price if PnL is zero.
        """
        exit_price, size, pnl = fill["price"], fill["size"], fill["pnl_usd"]
        if size <= 0 or exit_price <= 0 or abs(pnl) < 1e-12:
            return exit_price
        delta = pnl / size
        entry = exit_price - delta if fill["pos_side"] == "long" else exit_price + delta
        return entry if entry > 0 else exit_price

    async def sync_closed_trades(self) -> int:
        """
        Fetch closed orders from OKX and insert/update them in closed_trades.
        Returns the number of trades synced.
        """
        if not self.executor.has_credentials:
            logger.debug("OKX credentials missing. Skipping closed trades sync.")
            return 0

        try:
            await self.executor.initialize()
            
            # Query closed orders history from OKX via CCXT
            # instType=FUTURES for EEA X-Perps, fallback to SPOT/SWAP
            params = {"instType": "FUTURES"}
            raw_orders = await self.executor.exchange.private_get_trade_orders_history_archive(params)
            orders_data = raw_orders.get("data", []) if isinstance(raw_orders, dict) else []

            # 1. Normalize the raw fills, oldest first, so opens can be paired with closes.
            fills: List[Dict[str, Any]] = []
            sync_start_iso = os.getenv("MMS4_TRADE_SYNC_START_ISO", "2026-08-15T21:00:00+00:00")
            for item in orders_data:
                if item.get("state") not in ["filled", "partially_filled"]:
                    continue
                ord_id = item.get("ordId") or item.get("clOrdId")
                if not ord_id:
                    continue

                ts_ms = int(item.get("fillTime") or item.get("uTime") or item.get("cTime") or 0)
                ts_iso = (
                    datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()
                    if ts_ms > 0 else datetime.now(timezone.utc).isoformat()
                )
                if sync_start_iso and ts_iso < sync_start_iso:
                    continue

                fills.append({
                    "ord_id": str(ord_id),
                    "ts_ms": ts_ms,
                    "ts_iso": ts_iso,
                    "symbol": item.get("instId", "BTC/USDC"),
                    "pos_side": str(item.get("posSide") or "").lower(),
                    "side": str(item.get("side") or "buy").lower(),
                    "price": float(item.get("avgPx") or item.get("fillPx") or item.get("px") or 0.0),
                    "size": float(item.get("accFillSz") or item.get("sz") or 0.0),
                    "pnl_usd": float(item.get("pnl") or 0.0),
                    "lever": float(item.get("lever") or 1.0),
                })
            fills.sort(key=lambda f: f["ts_ms"])

            # 2. Pair each closing fill with the open(s) it liquidated (FIFO per
            #    instrument + position side). OKX only reports `pnl` on the closing leg,
            #    so opens carry the entry price and closes carry the result.
            open_legs: Dict[tuple, List[Dict[str, Any]]] = {}
            synced_count = 0

            for f in fills:
                key = (f["symbol"], f["pos_side"] or f["side"])
                is_close = self._is_closing_fill(f)

                if not is_close:
                    open_legs.setdefault(key, []).append(f)
                    continue

                entry_price = self._consume_entry_price(open_legs.get(key, []), f["size"])
                if entry_price <= 0:
                    # No matching open in this window (position opened before the sync
                    # cutoff). Derive the entry from the realized PnL where possible.
                    entry_price = self._infer_entry_price(f)

                exit_price = f["price"]
                lever = f["lever"] or 1.0
                notional = exit_price * f["size"]
                margin = (notional / lever) if lever > 0 else notional
                # Return on the capital actually committed, not on notional.
                pnl_pct = (f["pnl_usd"] / margin) if margin > 0 else 0.0

                self.logger_db.upsert_closed_trade(
                    trade_id=f["ord_id"],
                    timestamp=f["ts_iso"],
                    symbol=f["symbol"],
                    side=(f["pos_side"] or f["side"]).upper(),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=f["size"],
                    pnl_usd=f["pnl_usd"],
                    pnl_pct=pnl_pct,
                    is_win=f["pnl_usd"] > 0,
                    leverage=lever,
                )
                synced_count += 1

            logger.info(f"Synced {synced_count} closed trades from OKX into logs.db")
            return synced_count

        except Exception as e:
            logger.warning(f"Trade history sync attempt note: {e}")
            return 0

    async def run_loop(self) -> None:
        """Background execution loop running sync every interval_seconds."""
        while self._is_running:
            try:
                await self.sync_closed_trades()
            except Exception as e:
                logger.error(f"Error in trade sync loop: {e}")
            await asyncio.sleep(self.interval_seconds)

    async def start(self) -> None:
        """Start the background sync worker."""
        if not self._is_running:
            self._is_running = True
            # Execute initial sync immediately
            await self.sync_closed_trades()
            self._task = asyncio.create_task(self.run_loop())
            logger.info(f"OKXTradeSyncWorker started (interval: {self.interval_seconds}s)")

    async def stop(self) -> None:
        """Stop the background sync worker."""
        if self._is_running:
            self._is_running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            logger.info("OKXTradeSyncWorker stopped.")

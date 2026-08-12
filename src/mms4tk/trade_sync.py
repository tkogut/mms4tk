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

            synced_count = 0
            for item in orders_data:
                state = item.get("state")
                if state not in ["filled", "partially_filled"]:
                    continue

                ord_id = item.get("ordId") or item.get("clOrdId")
                if not ord_id:
                    continue

                ts_ms = int(item.get("fillTime") or item.get("uTime") or item.get("cTime") or 0)
                ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat() if ts_ms > 0 else datetime.now(timezone.utc).isoformat()

                symbol = item.get("instId", "BTC/USDC")
                side = item.get("posSide") or item.get("side", "buy")
                
                fill_px = float(item.get("avgPx") or item.get("fillPx") or item.get("px") or 0.0)
                fill_sz = float(item.get("accFillSz") or item.get("sz") or 0.0)
                pnl_usd = float(item.get("pnl") or 0.0)
                
                # Compute PnL percentage estimate
                notional = fill_px * fill_sz if fill_px > 0 else 1.0
                pnl_pct = (pnl_usd / notional) if notional > 0 else 0.0
                is_win = pnl_usd > 0
                lever = float(item.get("lever") or 1.0)

                self.logger_db.upsert_closed_trade(
                    trade_id=str(ord_id),
                    timestamp=ts_iso,
                    symbol=symbol,
                    side=side.upper(),
                    entry_price=fill_px,
                    exit_price=fill_px,
                    amount=fill_sz,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                    is_win=is_win,
                    leverage=lever
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

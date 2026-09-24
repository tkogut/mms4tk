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
        # Guards against overlapping sync_closed_trades() calls. Callers today
        # include api.py's GET /api/v1/history/trades (fired via asyncio.create_task
        # on every poll — the frontend polls this every 5s, far faster than a single
        # sync round-trip against OKX completes) alongside the 15-minute run_loop and
        # a direct manual-trigger endpoint. Without this lock, overlapping invocations
        # all drive the same shared `executor.exchange` concurrently (initialize()
        # reassigns its markets/symbols mid-flight), which is what was actually
        # producing the "Unclosed client session" warnings and cascading OKX
        # RequestTimeouts on unrelated endpoints (balance/positions) — not a slow
        # exchange, but unbounded concurrent self-contention on one shared instance.
        self._sync_lock: asyncio.Lock = asyncio.Lock()

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
    def _consume_open_legs(open_queue: List[Dict[str, Any]], size: float) -> tuple[float, float]:
        """
        FIFO-match `size` against queued opening fills and return:
        (size-weighted average entry price, total allocated opening fee).
        Consumed quantity is removed from the queue.
        """
        if not open_queue or size <= 0:
            return 0.0, 0.0
        remaining = size
        weighted = 0.0
        matched = 0.0
        open_fee = 0.0
        while open_queue and remaining > 1e-12:
            leg = open_queue[0]
            take = min(leg["size"], remaining)
            weighted += leg["price"] * take
            open_fee_unit = float(leg.get("open_fee_per_unit") or 0.0)
            open_fee += take * open_fee_unit
            matched += take
            remaining -= take
            leg["size"] -= take
            if leg["size"] <= 1e-12:
                open_queue.pop(0)
        entry_price = (weighted / matched) if matched > 0 else 0.0
        return entry_price, open_fee

    @classmethod
    def _consume_entry_price(cls, open_queue: List[Dict[str, Any]], size: float) -> float:
        """
        FIFO-match `size` against queued opening fills and return their size-weighted
        average price. Consumed quantity is removed from the queue.
        """
        return cls._consume_open_legs(open_queue, size)[0]

    def _get_contract_size(self, symbol: str) -> float:
        """
        Return contract size (ctVal) for symbol from executor markets, falling back to
        per-asset defaults. Mirrors MMS4Daemon._get_contract_size (daemon.py) — OKX
        reports fill sizes in contracts, not coins, so every USD notional/margin
        computation from a fill's `size` must scale by this factor.
        """
        try:
            if getattr(self.executor, "exchange", None) and getattr(self.executor.exchange, "markets", None):
                m = self.executor.exchange.market(symbol)
                if m and m.get("contractSize"):
                    return float(m["contractSize"])
        except Exception:
            pass
        sym_upper = symbol.upper()
        if "BTC" in sym_upper:
            return 0.0001
        if "ETH" in sym_upper or "XAU" in sym_upper:
            return 0.001
        if "XAG" in sym_upper:
            return 0.01
        return 1.0

    def _infer_entry_price(self, fill: Dict[str, Any]) -> float:
        """
        Reconstruct the entry price from the exit price and realized PnL when the opening
        fill predates the sync window. Falls back to the exit price if PnL is zero.
        """
        exit_price, size, pnl = fill["price"], fill["size"], fill["pnl_usd"]
        if size <= 0 or exit_price <= 0 or abs(pnl) < 1e-12:
            return exit_price
        ct_size = self._get_contract_size(fill["symbol"])
        coin_size = size * ct_size
        delta = pnl / coin_size
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

        if self._sync_lock.locked():
            logger.debug("Trade sync already in progress — skipping overlapping call.")
            return 0

        async with self._sync_lock:
            return await self._sync_closed_trades_locked()

    async def _sync_closed_trades_locked(self) -> int:
        """Body of sync_closed_trades(), only ever run with `_sync_lock` held."""
        try:
            await self.executor.initialize()
            
            # Query closed orders history from OKX via CCXT
            # Combines recent orders history (last 7 days) and archive (older history)
            params = {"instType": "FUTURES"}
            orders_data: List[Dict[str, Any]] = []
            seen_ord_ids = set()

            try:
                recent_orders = await self.executor.exchange.private_get_trade_orders_history(params)
                if isinstance(recent_orders, dict) and "data" in recent_orders:
                    for ord_item in recent_orders["data"]:
                        oid = ord_item.get("ordId") or ord_item.get("clOrdId")
                        if oid and oid not in seen_ord_ids:
                            seen_ord_ids.add(oid)
                            orders_data.append(ord_item)
            except Exception as r_err:
                logger.warning(f"Failed to fetch recent orders history from OKX: {r_err}")

            try:
                archive_orders = await self.executor.exchange.private_get_trade_orders_history_archive(params)
                if isinstance(archive_orders, dict) and "data" in archive_orders:
                    for ord_item in archive_orders["data"]:
                        oid = ord_item.get("ordId") or ord_item.get("clOrdId")
                        if oid and oid not in seen_ord_ids:
                            seen_ord_ids.add(oid)
                            orders_data.append(ord_item)
            except Exception as a_err:
                logger.warning(f"Failed to fetch archive orders history from OKX: {a_err}")

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

                fee_usd = abs(float(item.get("fee") or item.get("fee_usd") or 0.0))
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
                    "fee_usd": fee_usd,
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
                    f["open_fee_per_unit"] = (f["fee_usd"] / f["size"]) if f["size"] > 0 else 0.0
                    open_legs.setdefault(key, []).append(f)
                    continue

                entry_price, open_fee = self._consume_open_legs(open_legs.get(key, []), f["size"])
                if entry_price <= 0:
                    # No matching open in this window (position opened before the sync
                    # cutoff). Derive the entry from the realized PnL where possible.
                    entry_price = self._infer_entry_price(f)

                close_fee = abs(float(f.get("fee_usd") or f.get("fee") or 0.0))
                total_fee = open_fee + close_fee
                pnl_gross = float(f["pnl_usd"])
                pnl_net = pnl_gross - total_fee
                is_win = pnl_net > 0

                exit_price = f["price"]
                lever = f["lever"] or 1.0
                # `size` is in OKX contracts, not coins — scale by ctVal or notional
                # (and therefore margin and pnl_pct) comes out 100x-10000x too large,
                # which drives every real stop-loss's pnl_pct toward zero and makes it
                # misclassify as "BREAK-EVEN" downstream (LoggerDB.get_closed_trades'
                # pnl_pct <= -0.015 fallback never trips).
                ct_size = self._get_contract_size(f["symbol"])
                notional = exit_price * f["size"] * ct_size
                margin = (notional / lever) if lever > 0 else notional
                # Return on the capital actually committed, not on notional.
                pnl_pct = (pnl_net / margin) if margin > 0 else 0.0

                trade_side = (f["pos_side"] or f["side"]).upper()

                # This authoritative fill supersedes any provisional `recon_*` row
                # MMS4Daemon.reconcile_positions() wrote for the same close (see
                # LoggerDB.delete_provisional_closed_trade docstring) — different
                # trade_id schemes mean upsert_closed_trade's ON CONFLICT would never
                # dedupe them on its own, leaving the same real close duplicated in
                # Wykaz Transakcji with two different exit prices/pnl.
                try:
                    removed = self.logger_db.delete_provisional_closed_trade(
                        symbol=f["symbol"], side=trade_side, entry_price=entry_price,
                    )
                    if removed:
                        logger.info(
                            f"Removed {removed} provisional reconciliation row(s) for "
                            f"{f['symbol']} {trade_side} — superseded by authoritative fill {f['ord_id']}"
                        )
                except Exception as dedupe_err:
                    logger.warning(f"Failed to clean up provisional closed_trade for {f['symbol']}: {dedupe_err}")

                self.logger_db.upsert_closed_trade(
                    trade_id=f["ord_id"],
                    timestamp=f["ts_iso"],
                    symbol=f["symbol"],
                    side=trade_side,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=f["size"],
                    pnl_usd=pnl_gross,
                    pnl_pct=pnl_pct,
                    is_win=is_win,
                    leverage=lever,
                    fee_usd=total_fee,
                    pnl_net_usd=pnl_net,
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

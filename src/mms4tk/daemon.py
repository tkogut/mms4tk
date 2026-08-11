import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor
from mms4tk.analytics import MMS4SignalEngine, SignalResult, Candle
from mms4tk.logger_db import LoggerDB

logger = logging.getLogger("mms4tk.daemon")


class MMS4Daemon:
    """
    Background market evaluation daemon for MMS v4 strategy.
    
    Execution loop steps:
    1. Fetch OHLCV candles via OKXExecutor.fetch_candles(symbol)
    2. Evaluate signals via MMS4SignalEngine.evaluate_signals(candles, state_manager)
    3. Execute trade signals according to MMS4 state rules:
       - BASE_SHORT / BASE_LONG: execute order with SL 2%.
       - HIGH_RISK_ADD: check is_high_risk_add_allowed(). If allowed, execute order with SL 1%.
    4. Log cycle outcome and state metrics via LoggerDB (.agents/swarm/logs.db).
    """

    def __init__(
        self,
        executor: Optional[OKXExecutor] = None,
        state_manager: Optional[MMS4StateManager] = None,
        signal_engine: Optional[MMS4SignalEngine] = None,
        logger_db: Optional[LoggerDB] = None,
        symbol: Optional[str] = None,
        interval_seconds: float = 15.0,
        trade_amount_usd: Optional[float] = None
    ) -> None:
        self.executor = executor or OKXExecutor()
        self.state_manager = state_manager or MMS4StateManager(initial_state=MMS4State.BASE_MODE)
        self.signal_engine = signal_engine or MMS4SignalEngine()
        self.logger_db = logger_db or LoggerDB()
        self.symbol = symbol or os.getenv("MMS4_SYMBOL", os.getenv("MMS4_SYMBOL_FUTURES", "BTC/USDC"))
        self.interval_seconds = interval_seconds
        
        # Capital Sizing in USD (Default $10.0 USD total max margin cap per symbol)
        # Base Position = 70% ($7.00), High-Risk Addon = 30% ($3.00)
        if trade_amount_usd is not None:
            self.max_margin_usd = float(trade_amount_usd)
        else:
            self.max_margin_usd = float(os.getenv("MMS4_MAX_MARGIN_USD", os.getenv("MMS4_TRADE_AMOUNT_USD", "10.0")))
        
        self.base_trade_usd = round(self.max_margin_usd * 0.70, 2)
        self.addon_trade_usd = round(self.max_margin_usd * 0.30, 2)
        self.trade_amount_usd = self.max_margin_usd

        self._is_running: bool = False
        self.processed_cycles: int = 0
        self.last_cycle_timestamp: Optional[str] = None
        self.last_signal: Optional[SignalResult] = None
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        """Start the background daemon loop."""
        if self._is_running:
            logger.info("Daemon is already running.")
            return

        logger.info(f"Starting MMS4Daemon (symbol={self.symbol}, interval={self.interval_seconds}s)...")
        await self.executor.initialize()
        self._is_running = True
        self._task = asyncio.create_task(self.run_loop())

    async def stop(self) -> None:
        """Stop the daemon gracefully."""
        if not self._is_running:
            logger.info("Daemon is not running.")
            return

        logger.info("Stopping MMS4Daemon...")
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        await self.executor.close()
        logger.info("MMS4Daemon stopped successfully.")

    async def execute_cycle(self) -> Dict[str, Any]:
        """
        Executes a single market evaluation cycle.
        Can be called directly or run inside run_loop.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # 1. Fetch candles
        candles: List[Candle] = await self.executor.fetch_candles(self.symbol, limit=100)

        # 2. Evaluate signals
        signal: SignalResult = self.signal_engine.evaluate_signals(candles, self.state_manager)
        self.last_signal = signal

        # 3. Process signal and execute trades if triggered
        outcome = "NO_ACTION"
        details = signal.reason or ""
        order_info: Optional[Dict[str, Any]] = None

        # Dynamic contract/coin amounts from 70% Base ($7.00) / 30% Addon ($3.00) split
        base_order_amount = round(self.base_trade_usd / signal.price, 6) if signal.price > 0 else 0.0001
        addon_order_amount = round(self.addon_trade_usd / signal.price, 6) if signal.price > 0 else 0.0001

        if signal.signal_type in ["BASE_SHORT", "BASE_LONG"]:
            side = "SELL" if signal.signal_type == "BASE_SHORT" else "BUY"
            try:
                order_info = await self.executor.execute_order(
                    symbol=self.symbol,
                    side=side,
                    amount=base_order_amount,
                    stop_loss_pct=0.02,
                    leverage=self.state_manager.leverage
                )
                outcome = f"EXECUTED_{signal.signal_type}"
                details = f"Executed {signal.signal_type} base order for 70% cap (${self.base_trade_usd:.2f} USD / {base_order_amount} units) with SL=2% (leverage x{self.state_manager.leverage})"
            except Exception as e:
                outcome = f"ERROR_{signal.signal_type}"
                details = f"Failed to execute order: {str(e)}"

        elif "HIGH_RISK_ADD" in signal.signal_type:
            if self.state_manager.is_high_risk_add_allowed():
                side = "SELL" if "SHORT" in signal.signal_type else "BUY"
                try:
                    order_info = await self.executor.execute_order(
                        symbol=self.symbol,
                        side=side,
                        amount=addon_order_amount,
                        stop_loss_pct=0.01,
                        leverage=1.0
                    )
                    outcome = f"EXECUTED_{signal.signal_type}"
                    details = f"Executed High Risk Add order for 30% cap (${self.addon_trade_usd:.2f} USD / {addon_order_amount} units) with SL=1%"
                except Exception as e:
                    outcome = f"ERROR_{signal.signal_type}"
                    details = f"Failed to execute High Risk Add: {str(e)}"
            else:
                outcome = f"BLOCKED_{signal.signal_type}"
                details = f"High Risk Add blocked in state {self.state_manager.current_state.value}"

        # 4. Log cycle outcome and state metrics
        self.logger_db.log_cycle(
            symbol=self.symbol,
            signal_type=signal.signal_type,
            state=self.state_manager.current_state.value,
            leverage=self.state_manager.leverage,
            price=signal.price,
            outcome=outcome,
            details=details
        )

        summary = self.state_manager.get_summary()
        self.logger_db.log_state_metrics(
            state=summary["current_state"],
            leverage=summary["leverage"],
            equity=summary["current_equity"],
            realized_pnl=summary["realized_pnl"],
            trade_count=summary["total_trades"]
        )

        self.processed_cycles += 1
        self.last_cycle_timestamp = now_iso

        return {
            "cycle": self.processed_cycles,
            "timestamp": now_iso,
            "signal_type": signal.signal_type,
            "outcome": outcome,
            "state": summary["current_state"],
            "leverage": summary["leverage"],
            "order": order_info
        }

    async def run_loop(self) -> None:
        """Background loop executing cycles at configured intervals."""
        while self._is_running:
            try:
                await self.execute_cycle()
            except Exception as e:
                logger.error(f"Error during daemon cycle execution: {type(e).__name__}: {str(e)}")

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def get_status(self) -> Dict[str, Any]:
        """Return current status dictionary of the daemon."""
        return {
            "is_running": self._is_running,
            "interval_seconds": self.interval_seconds,
            "symbol": self.symbol,
            "max_margin_usd": self.max_margin_usd,
            "base_trade_usd": self.base_trade_usd,
            "addon_trade_usd": self.addon_trade_usd,
            "trade_amount_usd": self.trade_amount_usd,
            "last_cycle_timestamp": self.last_cycle_timestamp,
            "processed_cycles": self.processed_cycles
        }

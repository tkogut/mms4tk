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

    async def execute_symbol_cycle(self, target_symbol: str) -> Dict[str, Any]:
        """
        Executes a single market evaluation cycle for a specific trading symbol.
        Isolates state, leverage, and logging per symbol.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # 1. Fetch candles for target symbol using symbol's configured timeframe
        sym_tf = "15m"
        try:
            from mms4tk.api import configured_symbols_store
            sym_cfg = configured_symbols_store.get(target_symbol, {})
            if isinstance(sym_cfg, dict) and sym_cfg.get("timeframe"):
                sym_tf = str(sym_cfg.get("timeframe", "15m"))
        except Exception:
            pass

        candles: List[Candle] = await self.executor.fetch_candles(target_symbol, timeframe=sym_tf, limit=100)

        # 2. Evaluate signals with per-symbol state isolation
        sym_state = self.state_manager.get_symbol_state(target_symbol)
        sym_leverage = self.state_manager.get_symbol_leverage(target_symbol)
        if not isinstance(sym_leverage, (int, float)):
            sym_leverage = getattr(self.state_manager, "leverage", 1.0)
            if not isinstance(sym_leverage, (int, float)):
                sym_leverage = 1.0

        if not isinstance(sym_state, MMS4State):
            sym_state = getattr(self.state_manager, "current_state", MMS4State.BASE_MODE)
            if not isinstance(sym_state, MMS4State):
                sym_state = MMS4State.BASE_MODE

        signal: SignalResult = self.signal_engine.evaluate_signals(candles, self.state_manager)
        if target_symbol == self.symbol:
            self.last_signal = signal

        # 3. Process signal and execute trades if triggered
        outcome = "NO_ACTION"
        details = signal.reason or ""
        order_info: Optional[Dict[str, Any]] = None

        # Calculate notional position value so that real cash margin spent matches exact 70/30 allocation
        notional_base_usd = self.base_trade_usd * sym_leverage
        notional_addon_usd = self.addon_trade_usd * sym_leverage

        base_order_amount = self.executor.calculate_order_amount(target_symbol, notional_base_usd, signal.price)
        if not isinstance(base_order_amount, (int, float)):
            base_order_amount = round(notional_base_usd / signal.price, 6) if signal.price > 0 else 0.0001

        addon_order_amount = self.executor.calculate_order_amount(target_symbol, notional_addon_usd, signal.price)
        if not isinstance(addon_order_amount, (int, float)):
            addon_order_amount = round(notional_addon_usd / signal.price, 6) if signal.price > 0 else 0.0001

        # Fetch live open positions from OKX to enforce Single Position Lock Guard
        live_positions = []
        try:
            live_positions = await self.executor.fetch_positions()
        except Exception as pe:
            logger.warning(f"Failed to fetch live positions for position guard check: {pe}")

        existing_pos = None
        for p in live_positions:
            p_sym = p.get("symbol", "")
            p_clean = p_sym.split(":")[0].split("-3")[0].replace("-USD_UM_XPERP", "").replace("_XPERP", "").replace("/USDC", "").replace("/USD", "")
            t_clean = target_symbol.split(":")[0].split("-3")[0].replace("-USD_UM_XPERP", "").replace("_XPERP", "").replace("/USDC", "").replace("/USD", "")
            if p_clean == t_clean:
                contracts = float(p.get("contracts", 0) or 0)
                if contracts > 0:
                    existing_pos = p
                    break

        if signal.signal_type in ["BASE_SHORT", "BASE_LONG"]:
            if existing_pos is not None:
                outcome = f"SKIPPED_{signal.signal_type}"
                details = f"Base position already open for {target_symbol} ({existing_pos.get('contracts')} contracts). Single Position Guard active."
            else:
                side = "SELL" if signal.signal_type == "BASE_SHORT" else "BUY"
                try:
                    order_info = await self.executor.execute_order(
                        symbol=target_symbol,
                        side=side,
                        amount=base_order_amount,
                        stop_loss_pct=0.02,
                        leverage=sym_leverage,
                        price=signal.price
                    )
                    outcome = f"EXECUTED_{signal.signal_type}"
                    details = f"Executed {signal.signal_type} base order for {target_symbol} (${self.base_trade_usd:.2f} USD margin, ${notional_base_usd:.2f} USD notional) with SL=2% (leverage x{sym_leverage})"
                except Exception as e:
                    outcome = f"ERROR_{signal.signal_type}"
                    details = f"Failed to execute order for {target_symbol}: {str(e)}"

        elif "HIGH_RISK_ADD" in signal.signal_type:
            if sym_state != MMS4State.BASE_MODE:
                outcome = f"BLOCKED_{signal.signal_type}"
                details = f"High Risk Add blocked for {target_symbol} in state {sym_state.value}"
            elif existing_pos is None:
                outcome = f"BLOCKED_{signal.signal_type}"
                details = f"High Risk Add blocked for {target_symbol} because no base position exists yet."
            elif sym_state == MMS4State.HIGH_RISK_ADD_SHORT or sym_state == MMS4State.HIGH_RISK_ADD_LONG:
                outcome = f"SKIPPED_{signal.signal_type}"
                details = f"High Risk Add already executed for {target_symbol}."
            else:
                side = "SELL" if "SHORT" in signal.signal_type else "BUY"
                try:
                    order_info = await self.executor.execute_order(
                        symbol=target_symbol,
                        side=side,
                        amount=addon_order_amount,
                        stop_loss_pct=0.0125,
                        leverage=sym_leverage,
                        price=signal.price
                    )
                    outcome = f"EXECUTED_{signal.signal_type}"
                    details = f"Executed High Risk Add order for {target_symbol} (${self.addon_trade_usd:.2f} USD margin, ${notional_addon_usd:.2f} USD notional) with composite SL=1.25% (leverage x{sym_leverage})"
                except Exception as e:
                    outcome = f"ERROR_{signal.signal_type}"
                    details = f"Failed to execute High Risk Add for {target_symbol}: {str(e)}"

        # 4. Log cycle outcome for specific symbol
        self.logger_db.log_cycle(
            symbol=target_symbol,
            signal_type=signal.signal_type,
            state=sym_state.value,
            leverage=sym_leverage,
            price=signal.price,
            outcome=outcome,
            details=details
        )

        # 4b. Dispatch event notification to Hermes Kanban & Telegram when order is executed
        if "EXECUTED" in outcome:
            try:
                from mms4tk.hermes_webhook import HermesWebhookClient, HermesWebhookEvent
                wh_client = HermesWebhookClient()
                clean_sym = target_symbol.split(":")[0].split("-3")[0].replace("-USD_UM_XPERP", "/USD").replace("_XPERP", "")
                title = f"🚀 OTWARCIE POZYCJI OKX: {clean_sym} ({signal.signal_type})"
                body = (
                    f"Otwarto nową pozycję na OKX dla pary **{clean_sym}**!\n"
                    f"- Sygnał: `{signal.signal_type}`\n"
                    f"- Strona: `{side}`\n"
                    f"- Cena wejścia: `${signal.price:,.2f}`\n"
                    f"- Dźwignia: `{sym_leverage:.0f}x`\n"
                    f"- Stop-Loss: `{2.0 if 'BASE' in signal.signal_type else 1.25}%`\n"
                    f"- Stan strategii: `{sym_state.value}`"
                )
                wh_client.emit_event(HermesWebhookEvent(
                    event_type="POSITION_OPENED",
                    title=title,
                    body=body,
                    initial_status="in_progress",
                    priority=4
                ))
                HermesWebhookClient.send_telegram(title, body)
                logger.info(f"Emitted Telegram webhook notification for position opening: {target_symbol}")
            except Exception as whe:
                logger.warning(f"Failed to emit position opening webhook notification: {whe}")

        summary = self.state_manager.get_summary()
        self.logger_db.log_state_metrics(
            state=summary["current_state"],
            leverage=summary["leverage"],
            equity=summary["current_equity"],
            realized_pnl=summary["realized_pnl"],
            trade_count=summary["total_trades"]
        )

        return {
            "symbol": target_symbol,
            "timestamp": now_iso,
            "signal_type": signal.signal_type,
            "outcome": outcome,
            "state": sym_state.value,
            "leverage": sym_leverage,
            "order": order_info
        }

    async def execute_cycle(self) -> Dict[str, Any]:
        """Executes a single market evaluation cycle for primary symbol."""
        res = await self.execute_symbol_cycle(self.symbol)
        self.processed_cycles += 1
        self.last_cycle_timestamp = res.get("timestamp")
        res["cycle"] = self.processed_cycles
        return res

    async def execute_multi_symbol_cycle(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        Executes parallel market evaluation cycles across multiple active symbols.
        Uses asyncio.gather for asynchronous, non-blocking I/O execution.
        """
        tasks = [self.execute_symbol_cycle(sym) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        valid_results = []
        for sym, res in zip(symbols, results):
            if isinstance(res, Exception):
                logger.error(f"Error executing cycle for {sym}: {res}")
            else:
                valid_results.append(res)
                
        self.processed_cycles += 1
        self.last_cycle_timestamp = datetime.now(timezone.utc).isoformat()
        return valid_results

    async def run_loop(self) -> None:
        """Background loop executing multi-symbol cycles at configured intervals."""
        while self._is_running:
            try:
                from mms4tk.api import configured_symbols_store
                symbols = [s for s, act in configured_symbols_store.items() if act]
            except Exception:
                symbols = [self.symbol]
            try:
                await self.execute_multi_symbol_cycle(symbols)
            except Exception as e:
                logger.error(f"Error during multi-symbol daemon loop: {type(e).__name__}: {str(e)}")

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def get_status(self) -> Dict[str, Any]:
        """Return current status dictionary of the daemon."""
        try:
            from mms4tk.api import configured_symbols_store
            active_syms = [s for s, act in configured_symbols_store.items() if act]
        except Exception:
            active_syms = [self.symbol]
        return {
            "is_running": self._is_running,
            "interval_seconds": self.interval_seconds,
            "symbol": self.symbol,
            "active_symbols": active_syms,
            "max_margin_usd": self.max_margin_usd,
            "base_trade_usd": self.base_trade_usd,
            "addon_trade_usd": self.addon_trade_usd,
            "trade_amount_usd": self.trade_amount_usd,
            "last_cycle_timestamp": self.last_cycle_timestamp,
            "processed_cycles": self.processed_cycles
        }


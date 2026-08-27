import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path

from mms4tk.okx_executor import OKXExecutor
from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.logger_db import LoggerDB
from mms4tk.analytics import Candle


def test_to_native_inst_id_conversions():
    executor = OKXExecutor()

    # 1. Empty / None
    assert executor.to_native_inst_id("") == ""
    assert executor.to_native_inst_id(None) == ""

    # 2. CCXT markets lookup
    mock_exchange = MagicMock()
    mock_exchange.markets = {
        "ETH/USD:USD-310404": {"id": "ETH-USD_UM_XPERP-310404"},
        "BTC/USDC:USDC": {"id": "BTC-USDC-SWAP"},
    }
    mock_exchange.market = lambda sym: mock_exchange.markets.get(sym, {})
    executor.exchange = mock_exchange

    assert executor.to_native_inst_id("ETH/USD:USD-310404") == "ETH-USD_UM_XPERP-310404"
    assert executor.to_native_inst_id("BTC/USDC:USDC") == "BTC-USDC-SWAP"

    # 3. Fallback when not in exchange markets
    executor.exchange = None
    assert executor.to_native_inst_id("ETH/USD:USD-310404") == "ETH-USD_UM_XPERP-310404"
    assert executor.to_native_inst_id("BTC/USD:USD-310404") == "BTC-USD_UM_XPERP-310404"
    assert executor.to_native_inst_id("ETH/USD:USD-XPERP") == "ETH-USD_UM_XPERP-310404"
    assert executor.to_native_inst_id("BTC/USDC") == "BTC-USDC-SWAP"
    assert executor.to_native_inst_id("ETH/USDT") == "ETH-USDT-SWAP"
    assert executor.to_native_inst_id("ETH-USDC-SWAP") == "ETH-USDC-SWAP"
    assert executor.to_native_inst_id("ETH-USD_UM_XPERP-310404") == "ETH-USD_UM_XPERP-310404"


def test_attach_stop_loss_uses_native_inst_id():
    async def _run():
        executor = OKXExecutor()
        executor.has_credentials = True

        mock_exchange = MagicMock()
        mock_exchange.markets = {
            "ETH/USD:USD-310404": {"id": "ETH-USD_UM_XPERP-310404"},
        }
        mock_exchange.market = lambda sym: mock_exchange.markets.get(sym, {})
        mock_exchange.price_to_precision = MagicMock(side_effect=lambda sym, px: f"{px:.2f}")

        mock_exchange.private_get_trade_orders_algo_pending = AsyncMock(return_value={
            "data": [
                {"algoId": "algo_prev_1", "posSide": "long"},
                {"algoId": "algo_prev_2", "posSide": "short"}
            ]
        })
        mock_exchange.private_post_trade_cancel_algos = AsyncMock(return_value={"code": "0"})
        mock_exchange.private_post_trade_order_algo = AsyncMock(return_value={"code": "0", "data": [{"algoId": "algo_new_1"}]})
        executor.exchange = mock_exchange

        # Test Long Position Stop Loss
        res = await executor.attach_stop_loss(
            symbol="ETH/USD:USD-310404",
            side="buy",
            entry_price=2000.0,
            stop_loss_pct=0.02,
            pos_side="long"
        )

        assert res == {"code": "0", "data": [{"algoId": "algo_new_1"}]}

        # Verify query for pending algos used native instId
        mock_exchange.private_get_trade_orders_algo_pending.assert_awaited_with({
            "instId": "ETH-USD_UM_XPERP-310404",
            "ordType": "conditional"
        })

        # Verify cancellation of long SL used native instId
        mock_exchange.private_post_trade_cancel_algos.assert_awaited_with([
            {"algoId": "algo_prev_1", "instId": "ETH-USD_UM_XPERP-310404"}
        ])

        # Verify order placement used native instId and closeFraction='1'
        mock_exchange.private_post_trade_order_algo.assert_awaited_with({
            "instId": "ETH-USD_UM_XPERP-310404",
            "tdMode": "cross",
            "side": "sell",
            "posSide": "long",
            "ordType": "conditional",
            "slTriggerPx": "1960.00",
            "slOrdPx": "-1",
            "closeFraction": "1",
        })

        # Test Short Position Stop Loss
        mock_exchange.private_get_trade_orders_algo_pending.reset_mock()
        mock_exchange.private_post_trade_cancel_algos.reset_mock()
        mock_exchange.private_post_trade_order_algo.reset_mock()

        mock_exchange.private_get_trade_orders_algo_pending.return_value = {
            "data": [{"algoId": "algo_prev_2", "posSide": "short"}]
        }

        res_short = await executor.attach_stop_loss(
            symbol="ETH/USD:USD-310404",
            side="sell",
            entry_price=2000.0,
            stop_loss_pct=0.02,
            pos_side="short"
        )

        assert res_short == {"code": "0", "data": [{"algoId": "algo_new_1"}]}

        mock_exchange.private_post_trade_order_algo.assert_awaited_with({
            "instId": "ETH-USD_UM_XPERP-310404",
            "tdMode": "cross",
            "side": "buy",
            "posSide": "short",
            "ordType": "conditional",
            "slTriggerPx": "2040.00",
            "slOrdPx": "-1",
            "closeFraction": "1",
        })

    asyncio.run(_run())


def test_daemon_self_healing_sl_uses_native_inst_id(tmp_path: Path):
    async def _run():
        db_file = tmp_path / "test_daemon_sl.db"
        db = LoggerDB(db_path=str(db_file))

        executor = OKXExecutor()
        executor.has_credentials = True

        mock_exchange = MagicMock()
        mock_exchange.markets = {
            "ETH/USD:USD-310404": {"id": "ETH-USD_UM_XPERP-310404"},
        }
        mock_exchange.market = lambda sym: mock_exchange.markets.get(sym, {})
        mock_exchange.price_to_precision = MagicMock(side_effect=lambda sym, px: f"{px:.2f}")

        # No pending SL exists -> triggers self-healing
        mock_exchange.private_get_trade_orders_algo_pending = AsyncMock(return_value={"data": []})
        mock_exchange.private_post_trade_cancel_algos = AsyncMock(return_value={"code": "0"})
        mock_exchange.private_post_trade_order_algo = AsyncMock(return_value={"code": "0", "data": [{"algoId": "healed_sl_1"}]})
        executor.exchange = mock_exchange

        mock_pos = {
            "symbol": "ETH/USD:USD-310404",
            "info": {"instId": "ETH-USD_UM_XPERP-310404"},
            "posSide": "long",
            "side": "long",
            "contracts": 1.0,
            "notionalUsd": 200.0,
            "entryPrice": 2000.0,
            "markPrice": 2010.0,
            "unrealizedPnl": 1.0,
        }
        executor.fetch_positions = AsyncMock(return_value=[mock_pos])
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=2000, high=2010, low=1990, close=2005, volume=10),
        ])

        from mms4tk.api import configured_symbols_store
        configured_symbols_store["ETH/USD:USD-310404"] = {"allocation_usd": 100.0, "is_active": True, "base_ratio": 70, "add_ratio": 30}

        state_mgr = MMS4StateManager()
        daemon = MMS4Daemon(
            state_manager=state_mgr,
            executor=executor,
            logger_db=db,
            interval_seconds=1.0,
            symbol="ETH/USD:USD-310404"
        )

        await daemon.execute_symbol_cycle("ETH/USD:USD-310404")

        # Verify pending algos query was called with the native instId
        mock_exchange.private_get_trade_orders_algo_pending.assert_awaited_with({
            "instId": "ETH-USD_UM_XPERP-310404",
            "ordType": "conditional"
        })

        # Verify SL algo order was placed with native instId
        mock_exchange.private_post_trade_order_algo.assert_awaited()
        call_args = mock_exchange.private_post_trade_order_algo.call_args[0][0]
        assert call_args["instId"] == "ETH-USD_UM_XPERP-310404"
        assert call_args["slTriggerPx"] == "1960.00"

    asyncio.run(_run())

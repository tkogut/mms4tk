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


def test_daemon_self_healing_sl_uses_base_entry_price_not_avgpx_on_addon_position(tmp_path: Path):
    """
    P3 V2: the whole point of pinning base_entry_price is that the Self-Healing
    SL Guard must reproduce the SAME trigger price the original base-open attach
    used, even for a position that has since had an addon fill move its avgPx —
    not a DIFFERENT price derived from the live (post-addon) avgPx. This is the
    determinism bug V2 fixes: previously this guard used avgPx + an addon-aware
    %, which could differ from whatever attach_stop_loss originally computed.
    """
    async def _run():
        db_file = tmp_path / "test_daemon_sl_v2.db"
        db = LoggerDB(db_path=str(db_file))
        # base opened at 2000 (first snapshot); a later addon fill moved avgPx to
        # 1950 — base_entry_price must stay pinned at 2000.
        db.save_position_snapshot(
            symbol="ETH/USD:USD-310404", side="long", contracts=1.0,
            entry_price=2000.0, last_price=2000.0,
        )
        db.save_position_snapshot(
            symbol="ETH/USD:USD-310404", side="long", contracts=1.4,
            entry_price=1950.0, last_price=1950.0,
        )
        db.record_addon("ETH/USD:USD-310404", "long")

        executor = OKXExecutor()
        executor.has_credentials = True

        mock_exchange = MagicMock()
        mock_exchange.markets = {"ETH/USD:USD-310404": {"id": "ETH-USD_UM_XPERP-310404"}}
        mock_exchange.market = lambda sym: mock_exchange.markets.get(sym, {})
        mock_exchange.price_to_precision = MagicMock(side_effect=lambda sym, px: f"{px:.2f}")
        mock_exchange.private_get_trade_orders_algo_pending = AsyncMock(return_value={"data": []})
        mock_exchange.private_post_trade_cancel_algos = AsyncMock(return_value={"code": "0"})
        mock_exchange.private_post_trade_order_algo = AsyncMock(return_value={"code": "0", "data": [{"algoId": "healed_sl_2"}]})
        executor.exchange = mock_exchange

        mock_pos = {
            "symbol": "ETH/USD:USD-310404",
            "info": {"instId": "ETH-USD_UM_XPERP-310404"},
            "posSide": "long",
            "side": "long",
            "contracts": 1.4,
            "notionalUsd": 200.0,
            "entryPrice": 1950.0,  # live avgPx, post-addon — must NOT be used as the anchor
            "markPrice": 1960.0,
            "unrealizedPnl": 1.0,
        }
        executor.fetch_positions = AsyncMock(return_value=[mock_pos])
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=1950, high=1960, low=1940, close=1955, volume=10),
        ])

        from mms4tk.api import configured_symbols_store
        configured_symbols_store["ETH/USD:USD-310404"] = {"allocation_usd": 100.0, "is_active": True, "base_ratio": 70, "add_ratio": 30}

        state_mgr = MMS4StateManager()
        daemon = MMS4Daemon(
            state_manager=state_mgr, executor=executor, logger_db=db,
            interval_seconds=1.0, symbol="ETH/USD:USD-310404",
        )

        await daemon.execute_symbol_cycle("ETH/USD:USD-310404")

        mock_exchange.private_post_trade_order_algo.assert_awaited()
        call_args = mock_exchange.private_post_trade_order_algo.call_args[0][0]
        # 2000 * (1 - 0.02) = 1960.00 — base_entry_price * base_sl_pct, NOT
        # 1950 * (1 - 0.0125) = 1925.6875 (what the pre-V2 addon-aware path would
        # have computed from the live avgPx).
        assert call_args["slTriggerPx"] == "1960.00"

    asyncio.run(_run())


def test_daemon_self_healing_sl_hedge_mode_uses_independent_base_entry_price_per_side(tmp_path: Path):
    """
    Hedge mode: LONG and SHORT open simultaneously on the same symbol. Each side
    has its own base_entry_price, and the Self-Healing SL Guard must re-attach
    each one at ITS OWN anchor — never mixing them up (e.g. attaching the short's
    base_entry_price to the long's re-attach). Independent review, round 3,
    identified this as a coverage gap.
    """
    async def _run():
        db_file = tmp_path / "test_daemon_sl_hedge.db"
        db = LoggerDB(db_path=str(db_file))
        db.save_position_snapshot(
            symbol="ETH/USD:USD-310404", side="long", contracts=1.0, entry_price=2000.0, last_price=2000.0,
        )
        db.save_position_snapshot(
            symbol="ETH/USD:USD-310404", side="short", contracts=1.0, entry_price=1800.0, last_price=1800.0,
        )

        executor = OKXExecutor()
        executor.has_credentials = True

        mock_exchange = MagicMock()
        mock_exchange.markets = {"ETH/USD:USD-310404": {"id": "ETH-USD_UM_XPERP-310404"}}
        mock_exchange.market = lambda sym: mock_exchange.markets.get(sym, {})
        mock_exchange.price_to_precision = MagicMock(side_effect=lambda sym, px: f"{px:.2f}")
        mock_exchange.private_get_trade_orders_algo_pending = AsyncMock(return_value={"data": []})
        mock_exchange.private_post_trade_cancel_algos = AsyncMock(return_value={"code": "0"})
        mock_exchange.private_post_trade_order_algo = AsyncMock(return_value={"code": "0", "data": [{"algoId": "healed_sl_hedge"}]})
        executor.exchange = mock_exchange

        # Hedge mode: both sides open at once for the same symbol.
        long_pos = {
            "symbol": "ETH/USD:USD-310404", "info": {"instId": "ETH-USD_UM_XPERP-310404"},
            "posSide": "long", "side": "long", "contracts": 1.0,
            "notionalUsd": 200.0, "entryPrice": 2000.0, "markPrice": 2010.0, "unrealizedPnl": 1.0,
        }
        short_pos = {
            "symbol": "ETH/USD:USD-310404", "info": {"instId": "ETH-USD_UM_XPERP-310404"},
            "posSide": "short", "side": "short", "contracts": 1.0,
            "notionalUsd": 180.0, "entryPrice": 1800.0, "markPrice": 1790.0, "unrealizedPnl": 1.0,
        }
        executor.fetch_positions = AsyncMock(return_value=[long_pos, short_pos])
        executor.fetch_candles = AsyncMock(return_value=[
            Candle(timestamp=1000, open=1900, high=1910, low=1890, close=1900, volume=10),
        ])

        from mms4tk.api import configured_symbols_store
        configured_symbols_store["ETH/USD:USD-310404"] = {"allocation_usd": 100.0, "is_active": True, "base_ratio": 70, "add_ratio": 30}

        state_mgr = MMS4StateManager()
        daemon = MMS4Daemon(
            state_manager=state_mgr, executor=executor, logger_db=db,
            interval_seconds=1.0, symbol="ETH/USD:USD-310404",
        )

        await daemon.execute_symbol_cycle("ETH/USD:USD-310404")

        assert mock_exchange.private_post_trade_order_algo.await_count == 2
        calls_by_pos_side = {c.args[0]["posSide"]: c.args[0] for c in mock_exchange.private_post_trade_order_algo.await_args_list}
        # LONG: 2000 * (1 - 0.02) = 1960.00
        assert calls_by_pos_side["long"]["slTriggerPx"] == "1960.00"
        # SHORT: 1800 * (1 + 0.02) = 1836.00 — must NOT be derived from the long's anchor
        assert calls_by_pos_side["short"]["slTriggerPx"] == "1836.00"

    asyncio.run(_run())


def test_attach_stop_loss_uses_explicit_trigger_price_when_given():
    """Issue #69: an explicit `trigger_price` must be used verbatim, not the
    entry_price × stop_loss_pct derivation — this is what the SPLIT_50 SL
    ratchet relies on to place the SL at the Impulse Negation level instead
    of the fixed 2% anchor."""
    async def _run():
        executor = OKXExecutor()
        executor.has_credentials = True

        mock_exchange = MagicMock()
        mock_exchange.markets = {}
        mock_exchange.market = lambda sym: {}
        mock_exchange.price_to_precision = MagicMock(side_effect=lambda sym, px: f"{px:.4f}")
        mock_exchange.private_get_trade_orders_algo_pending = AsyncMock(return_value={"data": []})
        mock_exchange.private_post_trade_cancel_algos = AsyncMock(return_value={"code": "0"})
        mock_exchange.private_post_trade_order_algo = AsyncMock(return_value={"code": "0", "data": [{"algoId": "algo_1"}]})
        executor.exchange = mock_exchange

        await executor.attach_stop_loss(
            symbol="XAG/USDC",
            side="buy",
            entry_price=64.2498,
            stop_loss_pct=0.02,
            pos_side="short",
            trigger_price=62.14,
        )

        called_with = mock_exchange.private_post_trade_order_algo.await_args.args[0]
        # NOT 64.2498 * 1.02 = 65.5348 — the explicit ratchet level must win.
        assert called_with["slTriggerPx"] == "62.1400"

    asyncio.run(_run())


def test_attach_stop_loss_derives_price_when_trigger_price_omitted():
    """Every existing caller (self-heal guard, midline TP re-attach, initial
    entry) omits `trigger_price` and must see byte-identical behavior to
    before this parameter existed."""
    async def _run():
        executor = OKXExecutor()
        executor.has_credentials = True

        mock_exchange = MagicMock()
        mock_exchange.markets = {}
        mock_exchange.market = lambda sym: {}
        mock_exchange.price_to_precision = MagicMock(side_effect=lambda sym, px: f"{px:.2f}")
        mock_exchange.private_get_trade_orders_algo_pending = AsyncMock(return_value={"data": []})
        mock_exchange.private_post_trade_cancel_algos = AsyncMock(return_value={"code": "0"})
        mock_exchange.private_post_trade_order_algo = AsyncMock(return_value={"code": "0", "data": [{"algoId": "algo_1"}]})
        executor.exchange = mock_exchange

        await executor.attach_stop_loss(
            symbol="BTC/USDC", side="buy", entry_price=2000.0, stop_loss_pct=0.02, pos_side="long",
        )

        called_with = mock_exchange.private_post_trade_order_algo.await_args.args[0]
        assert called_with["slTriggerPx"] == "1960.00"  # 2000 * (1 - 0.02), unchanged

    asyncio.run(_run())

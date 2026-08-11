import os
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

try:
    import ccxt.async_support as ccxt
except ImportError:
    ccxt = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from mms4tk.analytics import Candle

logger = logging.getLogger("mms4tk.okx_executor")


def _generate_mock_candles(count: int = 100, base_price: float = 50000.0) -> List[Candle]:
    """Generate deterministic mock OHLCV candles when OKX API keys are not provided."""
    now_ts = int(time.time() * 1000)
    candles: List[Candle] = []
    price = base_price
    for i in range(count):
        ts = now_ts - ((count - i) * 60 * 1000)
        open_p = price
        delta = 15.0 if (i % 3 == 0) else (-10.0 if i % 2 == 0 else 5.0)
        close_p = open_p + delta
        high_p = max(open_p, close_p) + 10.0
        low_p = min(open_p, close_p) - 10.0
        price = close_p
        candles.append(
            Candle(
                timestamp=ts,
                open=round(open_p, 2),
                high=round(high_p, 2),
                low=round(low_p, 2),
                close=round(close_p, 2),
                volume=1.0
            )
        )
    return candles


class OKXExecutor:
    """
    OKX Async Exchange Executor adhering to R-SEC-01 compliance:
    - API credentials MUST be read strictly from environment variables.
    - Keys, secret headers, and passphrases are NEVER logged or printed.
    """

    def __init__(self, sandbox: bool = False) -> None:
        api_key = os.getenv("OKX_API_KEY")
        secret = os.getenv("OKX_SECRET")
        passphrase = os.getenv("OKX_PASSPHRASE")

        self.has_credentials = bool(api_key and secret and passphrase and ccxt is not None)

        if not self.has_credentials:
            logger.warning("OKX API credentials incomplete or ccxt missing. Operating in Mock Mode.")

        rest_url = os.getenv("OKX_REST_URL", "https://eea.okx.com")

        if ccxt is not None:
            exchange_config: Dict[str, Any] = {
                "apiKey": api_key or "",
                "secret": secret or "",
                "password": passphrase or "",
                "enableRateLimit": True,
                "urls": {
                    "api": {
                        "rest": rest_url,
                    }
                },
                "options": {
                    "defaultType": "swap",  # Perpetual Futures (USDC/USDT)
                }
            }
            self.exchange = ccxt.okx(exchange_config)
            if sandbox:
                self.exchange.set_sandbox_mode(True)
        else:
            self.exchange = None

    async def initialize(self) -> None:
        """Load markets and verify connection to OKX."""
        logger.info("Initializing OKX executor and loading public markets...")
        try:
            # Create an unauthenticated exchange instance for public market metadata loading
            public_ex = ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
            await public_ex.load_markets()
            if self.exchange is not None:
                self.exchange.markets = public_ex.markets
                self.exchange.symbols = public_ex.symbols
            await public_ex.close()
            logger.info("OKX public markets loaded successfully.")
        except Exception as e:
            logger.warning(f"Failed to load public OKX markets ({e}).")

    async def close(self) -> None:
        """Close exchange connection gracefully."""
        if self.exchange is not None:
            await self.exchange.close()

    async def fetch_candles(
        self,
        symbol: Optional[str] = None,
        timeframe: str = "1m",
        limit: int = 100
    ) -> List[Candle]:
        """
        Fetch real live OHLCV candles from OKX public API, with fallback to mock if unreachable.
        """
        target_symbol = symbol or os.getenv("MMS4_SYMBOL", os.getenv("MMS4_SYMBOL_FUTURES", "BTC/USDC:USDC"))
        try:
            # Use public exchange instance or unauthenticated call to ensure real market candles
            ex = self.exchange or ccxt.okx({"enableRateLimit": True})
            ohlcv = await ex.fetch_ohlcv(target_symbol, timeframe=timeframe, limit=limit)
            candles: List[Candle] = []
            for item in ohlcv:
                candles.append(
                    Candle(
                        timestamp=item[0],
                        open=float(item[1]),
                        high=float(item[2]),
                        low=float(item[3]),
                        close=float(item[4]),
                        volume=float(item[5]) if len(item) > 5 else 0.0
                    )
                )
            return candles
        except Exception as e:
            logger.error(f"Error fetching candles from OKX: {type(e).__name__}: {str(e)}. Falling back to mock candles.")
            return _generate_mock_candles(count=limit)


    async def execute_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_loss_pct: float,
        leverage: float = 1.0
    ) -> Dict[str, Any]:
        """
        Execute market order with stop loss.
        If credentials missing, return mock order result.
        """
        side_lower = side.lower()
        if not self.has_credentials or self.exchange is None:
            logger.info(f"Mock Order Execution: symbol={symbol}, side={side}, amount={amount}, leverage={leverage}x, SL={stop_loss_pct*100}%")
            return {
                "id": f"mock_order_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": side_lower,
                "amount": amount,
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct,
                "status": "executed",
                "mock": True
            }

        try:
            await self.set_leverage(leverage, symbol)
            order = await self.exchange.create_order(
                symbol=symbol,
                type="market",
                side=side_lower,
                amount=amount,
                params={"stopLoss": {"triggerPrice": stop_loss_pct}}
            )
            logger.info(f"Successfully executed OKX order {order.get('id')} for {symbol}")
            return order
        except Exception as e:
            logger.error(f"Error executing order on OKX: {type(e).__name__}: {str(e)}")
            raise

    async def set_hedge_mode(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify/Set Hedge mode (long/short dual position mode) on OKX.
        OKX API endpoint: set_position_mode (posMode='long_short_mode').
        """
        target_symbol = symbol or os.getenv("MMS4_SYMBOL_FUTURES", os.getenv("MMS4_SYMBOL", "BTC/USDC:USDC"))
        if not self.has_credentials or self.exchange is None:
            return {"status": "success", "mode": "long_short_mode", "mock": True}

        try:
            response = await self.exchange.set_position_mode(hedged=True, symbol=target_symbol)
            logger.info("Successfully set Hedge mode on OKX.")
            return response
        except Exception as e:
            logger.error(f"Error setting Hedge mode: {type(e).__name__}: {str(e)}")
            raise

    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch full unified account balances and USD portfolio breakdown matching OKX UI."""
        if not self.has_credentials or self.exchange is None:
            return {
                "free": {"USDC": 10000.0},
                "total": {"USDC": 10000.0},
                "portfolio_usd": {
                    "total_usd": 10000.0,
                    "funding_usd": 0.0,
                    "trading_usd": 10000.0,
                    "earn_usd": 0.0
                },
                "mock": True
            }
        try:
            bal = await self.exchange.fetch_balance()
            portfolio_usd = {
                "total_usd": 0.0,
                "funding_usd": 0.0,
                "trading_usd": 0.0,
                "earn_usd": 0.0
            }
            funding_assets = {}

            # Attempt OKX V5 Asset Valuation call for exact breakdown
            try:
                val = await self.exchange.privateGetAssetAssetValuation({"ccy": "USD"})
                if val and val.get("data"):
                    val_data = val["data"][0]
                    portfolio_usd["total_usd"] = float(val_data.get("totalBal", 0.0))
                    details = val_data.get("details", {})
                    portfolio_usd["funding_usd"] = float(details.get("funding", 0.0))
                    portfolio_usd["trading_usd"] = float(details.get("trading", 0.0))
                    portfolio_usd["earn_usd"] = float(details.get("earn", 0.0))
            except Exception as ve:
                logger.warning(f"Failed to fetch privateGetAssetAssetValuation: {ve}")

            # Attempt OKX V5 Funding asset breakdown
            try:
                fund = await self.exchange.privateGetAssetBalances()
                if fund and fund.get("data"):
                    for item in fund["data"]:
                        b = float(item.get("bal", 0.0))
                        if b > 0:
                            funding_assets[item.get("ccy")] = b
            except Exception as fe:
                logger.warning(f"Failed to fetch privateGetAssetBalances: {fe}")

            bal["portfolio_usd"] = portfolio_usd
            bal["funding_assets"] = funding_assets
            return bal
        except Exception as e:
            logger.error(f"Error fetching balance: {type(e).__name__}: {str(e)}")
            raise

    async def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Fetch active positions."""
        if not self.has_credentials or self.exchange is None:
            return []
        try:
            return await self.exchange.fetch_positions(symbols=symbols)
        except Exception as e:
            logger.error(f"Error fetching positions: {type(e).__name__}: {str(e)}")
            raise

    async def set_leverage(self, leverage: float, symbol: str) -> Dict[str, Any]:
        """Set leverage for specific market symbol."""
        if not self.has_credentials or self.exchange is None:
            return {"symbol": symbol, "leverage": leverage, "mock": True}
        try:
            lev_int = int(leverage) if leverage >= 1.0 else 1
            logger.info(f"Setting leverage {leverage}x for symbol {symbol}")
            return await self.exchange.set_leverage(lev_int, symbol)
        except Exception as e:
            logger.error(f"Error setting leverage: {type(e).__name__}: {str(e)}")
            raise

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Emergency action: Cancel all active orders safely."""
        if not self.has_credentials or self.exchange is None:
            return [{"status": "cancelled_all_mock"}]
        try:
            logger.warning(f"Emergency cancel_all_orders invoked for symbol: {symbol or 'ALL'}")
            cancelled_orders = []
            try:
                open_orders = await self.exchange.fetch_open_orders(symbol=symbol)
                for order in open_orders:
                    try:
                        res = await self.exchange.cancel_order(order["id"], symbol=order.get("symbol") or symbol)
                        cancelled_orders.append(res)
                    except Exception as ce:
                        logger.error(f"Error cancelling order {order.get('id')}: {ce}")
            except Exception as fe:
                logger.warning(f"Fetch open orders failed during cancel_all_orders: {fe}")
            return cancelled_orders
        except Exception as e:
            logger.error(f"Error executing cancel_all_orders: {type(e).__name__}: {str(e)}")
            raise

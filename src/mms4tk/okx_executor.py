import os
import logging
from typing import Dict, Any, List, Optional
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mms4tk.okx_executor")


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

        if not api_key or not secret or not passphrase:
            logger.warning("OKX API credentials incomplete in environment (OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE).")

        exchange_config: Dict[str, Any] = {
            "apiKey": api_key or "",
            "secret": secret or "",
            "password": passphrase or "",
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",  # Perpetual Futures (USDC/USDT)
            }
        }

        self.exchange = ccxt.okx(exchange_config)
        if sandbox:
            self.exchange.set_sandbox_mode(True)

    async def initialize(self) -> None:
        """Load markets and verify connection to OKX."""
        logger.info("Initializing OKX executor and loading markets...")
        await self.exchange.load_markets()

    async def close(self) -> None:
        """Close exchange connection gracefully."""
        await self.exchange.close()

    async def set_hedge_mode(self, symbol: str = "BTC/USDC:USDC") -> Dict[str, Any]:
        """
        Verify/Set Hedge mode (long/short dual position mode) on OKX.
        OKX API endpoint: set_position_mode (posMode='long_short_mode').
        """
        try:
            # Set position mode to long_short_mode (Hedge mode)
            response = await self.exchange.set_position_mode(hedged=True, symbol=symbol)
            logger.info("Successfully set Hedge mode on OKX.")
            return response
        except Exception as e:
            logger.error(f"Error setting Hedge mode: {type(e).__name__}: {str(e)}")
            raise

    async def fetch_balance(self) -> Dict[str, Any]:
        """Fetch unified account balances."""
        try:
            return await self.exchange.fetch_balance()
        except Exception as e:
            logger.error(f"Error fetching balance: {type(e).__name__}: {str(e)}")
            raise

    async def fetch_positions(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Fetch active positions."""
        try:
            return await self.exchange.fetch_positions(symbols=symbols)
        except Exception as e:
            logger.error(f"Error fetching positions: {type(e).__name__}: {str(e)}")
            raise

    async def set_leverage(self, leverage: float, symbol: str) -> Dict[str, Any]:
        """Set leverage for specific market symbol."""
        try:
            lev_int = int(leverage) if leverage >= 1.0 else 1
            logger.info(f"Setting leverage {leverage}x for symbol {symbol}")
            return await self.exchange.set_leverage(lev_int, symbol)
        except Exception as e:
            logger.error(f"Error setting leverage: {type(e).__name__}: {str(e)}")
            raise

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Emergency action: Cancel all active orders."""
        try:
            logger.warning(f"Emergency cancel_all_orders invoked for symbol: {symbol or 'ALL'}")
            return await self.exchange.cancel_all_orders(symbol=symbol)
        except Exception as e:
            logger.error(f"Error executing cancel_all_orders: {type(e).__name__}: {str(e)}")
            raise

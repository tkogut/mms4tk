import os
import logging
import time
import math
import asyncio
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


class BelowMinimumNotional(ValueError):
    """
    The intended order value is under the venue's minimum for that instrument.

    Deliberately its own type: this is a correct decision not to trade (risk was reduced
    below what the exchange will accept), not a malfunction. Callers classify it as a
    skip so it never shows up as an ERROR_ outcome once per cycle.
    """

# Last-resort floor used only when the exchange exposes no limits for an instrument.
# The real minimum is read per-symbol from CCXT market metadata — see min_notional_for().
# Set MMS4_MIN_NOTIONAL_USD to override the fallback; leave unset to rely on the exchange.
FALLBACK_MIN_NOTIONAL_USD = float(os.getenv("MMS4_MIN_NOTIONAL_USD", "7.0"))

# How far a rounded contract count may exceed the target allocation before it is rejected.
SIZING_TOLERANCE = float(os.getenv("MMS4_SIZING_TOLERANCE", "0.10"))

MAINTENANCE_LOCK_PATHS = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".agents", "swarm", "MAINTENANCE_LOCK")),
    "/app/.agents/swarm/MAINTENANCE_LOCK",
    "/tmp/MMS4_MAINTENANCE_LOCK",
    os.path.expanduser("~/.mms4_maintenance_lock"),
]


def is_maintenance_locked() -> bool:
    """
    Returns True if live trading is blocked due to Maintenance Mode.
    Checks environment variable MMS4_MAINTENANCE_MODE or existence of lock file.
    """
    if os.getenv("MMS4_MAINTENANCE_MODE", "false").lower() in ["true", "1", "yes", "on"]:
        return True
    return any(os.path.exists(p) for p in MAINTENANCE_LOCK_PATHS)


def set_maintenance_lock(enable: bool, reason: str = "Developer Maintenance") -> bool:
    """
    Enable or disable Maintenance Mode by writing or deleting the lock file.

    The `enable` branch writes to MAINTENANCE_LOCK_PATHS[0] — the same list
    `is_maintenance_locked()`/the `disable` branch below check/clear — instead of
    a separately hardcoded path. Previously this branch recomputed its own path,
    so a test harness that redirects MAINTENANCE_LOCK_PATHS (see tests/conftest.py)
    would still hit the real production path here (round-3 independent review).
    """
    lock_path = MAINTENANCE_LOCK_PATHS[0]
    try:
        if enable:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write(f"LOCKED at {datetime.now(timezone.utc).isoformat()} - Reason: {reason}\n")
            logger.warning(f"🔒 MAINTENANCE MODE ENABLED: Lock file written at {lock_path} (Reason: {reason})")
        else:
            for p in MAINTENANCE_LOCK_PATHS:
                if os.path.exists(p):
                    os.remove(p)
                    logger.info(f"🔓 MAINTENANCE MODE DISABLED: Removed lock file at {p}")
        return True
    except Exception as e:
        logger.error(f"Failed to set maintenance lock ({enable}): {e}")
        return False


def normalize_symbol(symbol: str) -> str:
    """
    Reduce any OKX/CCXT symbol spelling to its bare base asset.

    'BTC/USD:USD-310404', 'BTC-USD_UM_XPERP-310404', 'BTC-USDC-SWAP' and 'BTC/USDC' all
    collapse to 'BTC'. Single definition shared by the executor and the daemon — the two
    previously carried duplicate ten-step replace() chains that could drift apart.
    """
    if not symbol:
        return ""
    s = str(symbol).split(":")[0]
    s = s.split("_UM_XPERP")[0].split("_XPERP")[0]
    for suffix in ("-SWAP", "-PERP", "-FUTURES"):
        s = s.replace(suffix, "")
    # Strip any expiry/date tail such as '-310404'
    parts = [p for p in s.replace("/", "-").split("-") if p]
    if parts and parts[-1].isdigit():
        parts = parts[:-1]
    if not parts:
        return ""
    base = parts[0]
    return base.strip().upper()


def _timeframe_to_ms(timeframe: str) -> int:
    """Milliseconds per candle for a ccxt-style timeframe string ('1m','15m','1h','4h','1d')."""
    unit = timeframe[-1].lower()
    value = int(timeframe[:-1])
    unit_ms = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}.get(unit)
    if unit_ms is None:
        raise ValueError(f"Unsupported timeframe unit: {timeframe!r}")
    return value * unit_ms


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
        rest_url = os.getenv("OKX_REST_URL", "https://eea.okx.com")
        try:
            # Create an unauthenticated exchange instance for public market metadata loading
            public_ex = ccxt.okx({"enableRateLimit": True, "urls": {"api": {"rest": rest_url}}, "options": {"defaultType": "swap"}})
            await public_ex.load_markets()
            if self.exchange is not None:
                self.exchange.markets = public_ex.markets
                self.exchange.symbols = public_ex.symbols
                self.exchange.markets_by_id = public_ex.markets_by_id
            await public_ex.close()
            logger.info("OKX public markets loaded successfully.")
        except Exception as e:
            logger.warning(f"Failed to load public OKX markets ({e}).")

    async def close(self) -> None:
        """Close exchange connection gracefully."""
        if self.exchange is not None:
            await self.exchange.close()

    async def verify_symbol_with_okx(self, symbol: str) -> Dict[str, Any]:
        """
        Verification of a trading pair against OKX live ticker API.
        Confirms symbol existence and fetches current live price from OKX.
        """
        clean_base = symbol.split("/")[0].split("-")[0].split(":")[0].upper()
        quote = symbol.split("/")[1] if "/" in symbol else "USDC"
        
        candidates = [
            f"{clean_base}/{quote}",
            f"{clean_base}-{quote}",
            f"{clean_base}/{quote}:{quote}",
            f"{clean_base}-{quote}-SWAP",
            f"{clean_base}-USDC",
            f"{clean_base}-USDT"
        ]

        # 0. Check if discover_eea_xperp_symbol finds an active EEA contract (e.g. XAU-USD_UM_XPERP-310502)
        try:
            eea_cand = await self.discover_eea_xperp_symbol(symbol)
            if eea_cand and eea_cand not in candidates:
                candidates.append(eea_cand)
        except Exception:
            pass

        last_price = 0.0
        public_ticker_found = False

        # 1. Try authenticated exchange instance first
        if self.exchange is not None:
            for cand in candidates:
                try:
                    ticker = await self.exchange.fetch_ticker(cand)
                    if ticker and ticker.get("last") is not None and float(ticker["last"]) > 0:
                        last_price = float(ticker["last"])
                        public_ticker_found = True
                        logger.info(f"OKX verified symbol {symbol} with live price ${last_price} using candidate {cand}")
                        break
                except Exception as e:
                    logger.debug(f"OKX self.exchange fetch_ticker failed for candidate {cand}: {e}")

        # 2. Public fallback instance
        if not public_ticker_found and ccxt is not None:
            try:
                rest_url = os.getenv("OKX_REST_URL", "https://eea.okx.com")
                pub_ex = ccxt.okx({"enableRateLimit": True, "urls": {"api": {"rest": rest_url}}})
                for cand in candidates:
                    try:
                        ticker = await pub_ex.fetch_ticker(cand)
                        if ticker and ticker.get("last") is not None and float(ticker["last"]) > 0:
                            last_price = float(ticker["last"])
                            public_ticker_found = True
                            logger.info(f"Public OKX verified symbol {symbol} with live price ${last_price} using candidate {cand}")
                            await pub_ex.close()
                            break
                    except Exception:
                        pass
                await pub_ex.close()
            except Exception as e:
                logger.debug(f"Public OKX fallback check error: {e}")

        if not public_ticker_found or last_price <= 0:
            return {
                "symbol": symbol,
                "okx_verified": False,
                "autotrade_permitted": False,
                "okx_status": "UNSUPPORTED",
                "last_price": 0.0,
                "reason": f"Instrument {symbol} nie odpowiada na giełdzie OKX lub brak aktualnej ceny"
            }

        return {
            "symbol": symbol,
            "okx_verified": True,
            "autotrade_permitted": True,
            "okx_status": "AUTOTRADE_READY",
            "last_price": last_price,
            "reason": f"Para potwierdzona z aktualną ceną OKX: ${last_price:,.4f}"
        }

    async def fetch_candles(
        self,
        symbol: Optional[str] = None,
        timeframe: str = "1m",
        limit: int = 100,
        allow_mock_fallback: bool = True
    ) -> List[Candle]:
        """
        Fetch real live OHLCV candles from OKX public API, with fallback to mock if unreachable.

        `allow_mock_fallback` (default True, preserves prior behavior for every
        existing caller): when False, a real fetch failure RAISES the underlying
        exception instead of silently returning deterministic mock candles.
        Audit Phase 3.2-followup (2026-09-11): callers that gate live trading
        decisions on the candle content (e.g. `daemon._refresh_htf_directional_gate`)
        need to be able to tell "OKX is down" apart from "here are 50 real H4
        candles" -- the mock fallback exists for callers where SOME data (even
        synthetic) is better than none (e.g. UI/demo paths), not for callers whose
        own `except Exception` fail-safe depends on a real failure actually raising.
        """
        target_symbol = symbol or os.getenv("MMS4_SYMBOL", os.getenv("MMS4_SYMBOL_FUTURES", "BTC/USDC"))
        symbols_to_try = [target_symbol]
        if ":" in target_symbol:
            symbols_to_try.append(target_symbol.split(":")[0])
        if "_XPERP" not in target_symbol:
            try:
                eea_sym = await self.discover_eea_xperp_symbol(target_symbol)
                if eea_sym and eea_sym not in symbols_to_try:
                    symbols_to_try.append(eea_sym)
            except Exception:
                pass

        last_error = None
        for sym in symbols_to_try:
            try:
                ex = self.exchange or ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
                ohlcv = await ex.fetch_ohlcv(sym, timeframe=timeframe.lower(), limit=limit)
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
                last_error = e

        logger.error(f"Error fetching candles from OKX for {target_symbol}: {type(last_error).__name__}: {str(last_error)}.")
        if not allow_mock_fallback:
            raise last_error
        logger.error(f"Falling back to mock candles for {target_symbol}.")
        return _generate_mock_candles(count=limit)

    async def fetch_candles_paginated(
        self,
        symbol: str,
        timeframe: str = "1h",
        days: int = 90,
        logger_db: Optional[Any] = None,
    ) -> List[Candle]:
        """PLAN-010 §4.1a: fetch `days` of OHLCV history, walking OKX's public API in
        `limit=300`-candle batches (a single `fetch_ohlcv` call is capped at ~300
        candles — ~12.5 days at 1h resolution — nowhere near enough for the 90-180 day
        recalibration window). Ports the pagination loop proven in the PLAN-008 Phase 3
        calibration scratchpad (`fetch_ohlcv_90d.py`) into production code.

        `logger_db`, if given, is used as a local cache (`ohlcv_cache` table): only the
        missing tail since the last cached candle is fetched from OKX, not the whole
        window every call — recalibration can be triggered repeatedly without
        re-fetching days of history OKX already gave us. No API credentials are needed
        for this path — public market data, safe to call even under the maintenance
        lock (it never touches order execution).

        Source is the spot/swap OHLCV series for `symbol` (ccxt notation, e.g.
        "BTC/USDC") — a proxy for the actually-traded XPERP instrument's price, which
        has no equivalent deep public OHLCV history via ccxt. The two track closely,
        but this MUST stay a documented assumption wherever a calibration result is
        shown, never presented as the exact traded price series.
        """
        cached: List[Dict[str, Any]] = []
        if logger_db is not None:
            try:
                cached = logger_db.get_cached_ohlcv(symbol, timeframe)
            except Exception as e:
                logger.warning(f"fetch_candles_paginated: cache read failed for {symbol}: {e}")

        interval_ms = _timeframe_to_ms(timeframe)
        want_since = int((time.time() - days * 86400) * 1000)
        if cached and cached[0]["timestamp"] <= want_since:
            # Cache already covers the full requested window from its earliest point;
            # only the tail (newest candles) may be missing.
            since = cached[-1]["timestamp"] + interval_ms
        else:
            since = want_since

        now_ms = int(time.time() * 1000)
        fetched: List[Dict[str, Any]] = []
        ex = self.exchange or ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        try:
            seen_ts = {c["timestamp"] for c in cached}
            while since < now_ms:
                batch = await ex.fetch_ohlcv(symbol, timeframe=timeframe.lower(), since=since, limit=300)
                if not batch:
                    break
                new = [c for c in batch if c[0] not in seen_ts]
                if not new and len(batch) < 2:
                    break
                for c in new:
                    seen_ts.add(c[0])
                    fetched.append({
                        "timestamp": int(c[0]), "open": float(c[1]), "high": float(c[2]),
                        "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]) if len(c) > 5 else 0.0,
                    })
                since = batch[-1][0] + interval_ms
                if len(batch) < 2:
                    break
                await asyncio.sleep(ex.rateLimit / 1000.0)
        except Exception as e:
            logger.error(f"fetch_candles_paginated: OKX fetch failed for {symbol}: {type(e).__name__}: {e}")
            if not cached:
                raise
        finally:
            if self.exchange is None:
                await ex.close()

        if logger_db is not None and fetched:
            try:
                logger_db.upsert_ohlcv_batch(symbol, timeframe, fetched)
            except Exception as e:
                logger.warning(f"fetch_candles_paginated: cache write failed for {symbol}: {e}")

        merged = {c["timestamp"]: c for c in cached}
        for c in fetched:
            merged[c["timestamp"]] = c
        all_candles = sorted(merged.values(), key=lambda c: c["timestamp"])
        all_candles = [c for c in all_candles if c["timestamp"] >= want_since]

        return [
            Candle(timestamp=c["timestamp"], open=c["open"], high=c["high"],
                   low=c["low"], close=c["close"], volume=c["volume"])
            for c in all_candles
        ]

    async def discover_eea_xperp_symbol(self, symbol: str = "BTC") -> Optional[str]:
        """
        Discover active live X-Perp instrument for OKX Europe (EEA).
        Returns CCXT unified symbol (e.g. 'BTC/USD:USD-310404') if markets loaded,
        or OKX native instId (e.g. 'BTC-USD_UM_XPERP-310404').
        """
        clean_base = symbol.split("/")[0].split("-")[0].split(":")[0].upper()
        
        # 1. If exchange markets loaded, check markets_by_id / markets for matching ruleType=='xperp'
        if self.exchange and self.exchange.markets:
            for sym, market in self.exchange.markets.items():
                info = market.get("info", {})
                if info.get("ruleType") == "xperp" and info.get("state") == "live":
                    inst_id = market.get("id", "")
                    if clean_base in inst_id or clean_base in info.get("instFamily", ""):
                        logger.info(f"Resolved OKX EEA X-Perp CCXT symbol: {sym} (instId: {inst_id})")
                        return sym

        # 2. Public API fallback
        try:
            ex = self.exchange or ccxt.okx({"enableRateLimit": True, "urls": {"api": {"rest": "https://eea.okx.com"}}})
            res = await ex.publicGetPublicInstruments({"instType": "FUTURES"})
            if res and "data" in res:
                for inst in res["data"]:
                    if inst.get("ruleType") == "xperp" and inst.get("state") == "live":
                        inst_id = inst.get("instId", "")
                        inst_family = inst.get("instFamily", "")
                        if clean_base in inst_id or clean_base in inst_family:
                            if self.exchange and getattr(self.exchange, "markets_by_id", None) and inst_id in self.exchange.markets_by_id:
                                ccxt_sym = self.exchange.markets_by_id[inst_id][0]["symbol"]
                                logger.info(f"Resolved OKX EEA X-Perp CCXT symbol from id: {ccxt_sym}")
                                return ccxt_sym
                            logger.info(f"Discovered OKX EEA X-Perp instId for {symbol}: {inst_id}")
                            return inst_id
        except Exception as e:
            logger.warning(f"Failed to discover EEA X-Perp instrument for {symbol}: {e}")
        return None

    def to_native_inst_id(self, symbol: str) -> str:
        """
        Convert a CCXT unified symbol or trading pair to OKX native instId.
        e.g. 'ETH/USD:USD-310404' -> 'ETH-USD_UM_XPERP-310404'
             'BTC/USDC' -> 'BTC-USDC-SWAP' or 'BTC-USD_UM_XPERP-310404'
        """
        if not symbol:
            return ""
        if self.exchange and getattr(self.exchange, "markets", None) and symbol in self.exchange.markets:
            market_info = self.exchange.market(symbol)
            if market_info and market_info.get("id"):
                return str(market_info["id"])
        
        # Fallback conversion
        clean = symbol.split(":")[0] if ":" in symbol else symbol
        if "/" in clean:
            base, quote = clean.split("/", 1)
            # Check if XPERP in symbol
            if "XPERP" in symbol or "310404" in symbol:
                return f"{base.upper()}-USD_UM_XPERP-310404"
            return f"{base.upper()}-{quote.upper()}-SWAP"
        return symbol.replace("/", "-")

    def min_notional_for(self, symbol: str, price: float) -> float:
        """
        Smallest order value the exchange will accept for this instrument, in USD.

        Derived from live CCXT market metadata so it scales with the instrument rather
        than a hardcoded number:
          * `limits.cost.min` when the venue publishes a notional floor, else
          * `limits.amount.min` x contractSize x price (the smallest tradeable lot).
        Falls back to FALLBACK_MIN_NOTIONAL_USD only when neither is available.
        """
        if price <= 0:
            return FALLBACK_MIN_NOTIONAL_USD
        try:
            if self.exchange and self.exchange.markets and symbol in self.exchange.markets:
                market = self.exchange.market(symbol)
                limits = market.get("limits", {}) or {}

                cost_min = (limits.get("cost", {}) or {}).get("min")
                if cost_min:
                    return float(cost_min)

                amount_min = (limits.get("amount", {}) or {}).get("min")
                if amount_min:
                    contract_size = float(market.get("contractSize") or 1.0) or 1.0
                    is_contract = bool(
                        market.get("swap") or market.get("contract") or market.get("future")
                    )
                    lot = float(amount_min) * (contract_size if is_contract else 1.0)
                    return lot * price
        except Exception as e:
            logger.debug(f"Could not read exchange minimum for {symbol}: {e}")
        return FALLBACK_MIN_NOTIONAL_USD

    def calculate_order_amount(self, symbol: str, target_usd: float, price: float) -> float:
        """
        Calculate precise order amount (contracts or coins) for target USD allocation.

        Accounts for CCXT contractSize, market type, min order limits and OKX integer
        contract precision. Contract counts are rounded to the NEAREST whole contract and
        rejected (0.0) when even the smallest tradeable size would overshoot the target by
        more than SIZING_TOLERANCE — the previous unconditional math.ceil() pushed the
        realized margin past max_margin_usd on every instrument measured.

        Returns 0.0 when no size satisfies the budget; callers must treat that as
        "skip this entry", not as "send a minimum order".
        """
        if price <= 0 or target_usd <= 0:
            return 0.0

        # Exchange minimum applies to every sizing path, including the whole-contract
        # branch. Checking it only inside execute_order's recalculation block left
        # contract-symbol configurations ungated.
        min_notional = self.min_notional_for(symbol, price)
        if target_usd < min_notional:
            logger.info(
                f"Sizing skipped for {symbol}: target ${target_usd:,.2f} is below the "
                f"exchange minimum ${min_notional:,.2f} for this instrument."
            )
            return 0.0

        is_contract_symbol = (
            ":" in symbol or 
            "-" in symbol or 
            "XPERP" in symbol or 
            "SWAP" in symbol or 
            "PERP" in symbol or 
            "FUTURES" in symbol
        )

        if self.exchange and self.exchange.markets and symbol in self.exchange.markets:
            try:
                market = self.exchange.market(symbol)
                contract_size = market.get("contractSize") or 1.0
                is_contract = (
                    market.get("swap", False) or 
                    market.get("contract", False) or 
                    market.get("future", False) or 
                    market.get("type") in ["swap", "future", "option"] or
                    is_contract_symbol
                )

                if is_contract:
                    if contract_size <= 0:
                        contract_size = 1.0
                    raw_contracts = target_usd / (price * contract_size)
                    min_amt = float(market.get("limits", {}).get("amount", {}).get("min") or 1.0)
                    contracts = self._fit_contracts(
                        raw_contracts, min_amt, target_usd, price, contract_size, symbol
                    )
                    if contracts <= 0:
                        return 0.0
                    try:
                        formatted = self.exchange.amount_to_precision(symbol, float(contracts))
                        val = float(formatted)
                        return val if val >= 1.0 else float(contracts)
                    except Exception:
                        return float(contracts)
                else:
                    raw_coins = target_usd / price
                    formatted = self.exchange.amount_to_precision(symbol, raw_coins)
                    return float(formatted)
            except Exception as e:
                logger.warning(f"Failed to compute precision amount for {symbol}: {e}")

        # Universal fallback for any contract instrument (BTC, ETH, XAU, SOL, ICP, AVAX, etc.)
        if is_contract_symbol or "/" not in symbol:
            sym_u = symbol.upper()
            if "BTC" in sym_u:
                ct_val = 0.0001
            elif "ETH" in sym_u or "XAU" in sym_u:
                ct_val = 0.001
            elif "XAG" in sym_u:
                ct_val = 0.01
            else:
                ct_val = 1.0
            raw_contracts = target_usd / (price * ct_val)
            return float(self._fit_contracts(raw_contracts, 1.0, target_usd, price, ct_val, symbol))

        return round(target_usd / price, 6)

    def _fit_contracts(
        self,
        raw_contracts: float,
        min_amount: float,
        target_usd: float,
        price: float,
        contract_size: float,
        symbol: str
    ) -> int:
        """
        Round a fractional contract count to a whole number that respects the budget.

        Rounds to nearest, falls back to floor when that overshoots, and returns 0 when
        even one minimum-size contract costs more than target_usd * (1 + tolerance).
        """
        if raw_contracts <= 0:
            return 0
        floor_amt = max(int(math.floor(min_amount)), 1)
        budget = target_usd * (1.0 + SIZING_TOLERANCE)

        candidates = [int(round(raw_contracts)), int(math.floor(raw_contracts))]
        for contracts in candidates:
            if contracts < floor_amt:
                continue
            if contracts * price * contract_size <= budget:
                return contracts

        smallest_cost = floor_amt * price * contract_size
        logger.warning(
            f"Sizing rejected for {symbol}: smallest tradeable size ({floor_amt} contract(s) "
            f"= ${smallest_cost:,.2f}) exceeds target ${target_usd:,.2f} "
            f"+{SIZING_TOLERANCE*100:.0f}% tolerance. Skipping entry."
        )
        return 0


    async def execute_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_loss_pct: float = 0.02,
        leverage: float = 1.0,
        price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Execute market order on OKX.
        For Futures/Swap/X-Perp markets, automatically resolves EEA instId, sets leverage, and attaches Stop Loss trigger price.
        """
        side_lower = side.lower()

        # Attempt to resolve EEA X-Perp instrument if symbol is standard spot/swap
        target_symbol = symbol
        if "XPERP" not in symbol and ":" not in symbol:
            eea_xperp = await self.discover_eea_xperp_symbol(symbol)
            if eea_xperp:
                target_symbol = eea_xperp

        is_contract_symbol = (
            ":" in target_symbol or 
            "-" in target_symbol or 
            "XPERP" in target_symbol or 
            "SWAP" in target_symbol or 
            "PERP" in target_symbol or 
            "FUTURES" in target_symbol
        )

        final_amount = amount
        if is_contract_symbol and (amount < 1.0 or amount != math.floor(amount)):
            ref_price = price or 0.0
            if ref_price <= 0 and self.exchange is not None:
                try:
                    ticker = await self.exchange.fetch_ticker(target_symbol)
                    ref_price = float(ticker.get("last") or ticker.get("close") or 0.0)
                except Exception:
                    ref_price = 0.0

            if ref_price <= 0 or amount <= 0:
                raise ValueError(
                    f"Cannot size contract order for {target_symbol}: "
                    f"amount={amount}, reference price={ref_price}"
                )

            # The coin amount was computed for a spot market; its USD value is the true
            # target. Do NOT raise it to the exchange minimum here — that used to turn a
            # deliberately small SCOUT_MODE position ($0.70) into a $12.61 one.
            est_usd = amount * ref_price
            min_notional = self.min_notional_for(target_symbol, ref_price)
            if est_usd < min_notional:
                raise BelowMinimumNotional(
                    f"Order for {target_symbol} skipped: target ${est_usd:,.2f} is below the "
                    f"exchange minimum ${min_notional:,.2f}. Reduce risk by not trading, "
                    f"never by trading larger than intended."
                )

            final_amount = self.calculate_order_amount(target_symbol, est_usd, ref_price)
            if final_amount <= 0:
                raise ValueError(
                    f"Order for {target_symbol} skipped: no whole contract size fits the "
                    f"${est_usd:,.2f} allocation."
                )
            logger.info(
                f"Recalculated contract amount for {target_symbol}: "
                f"original_amount={amount} -> contract_amount={final_amount} (${est_usd:.2f} USD)"
            )

        if is_maintenance_locked():
            logger.warning(
                f"⛔ MAINTENANCE MODE: Blocked real order on OKX for {target_symbol} ({side_lower} amount={final_amount})"
            )
            return {
                "id": f"maintenance_blocked_{int(time.time()*1000)}",
                "symbol": target_symbol,
                "side": side_lower,
                "amount": final_amount,
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct,
                "status": "blocked_by_maintenance_mode",
                "mock": True
            }

        if not self.has_credentials or self.exchange is None:
            logger.info(f"Mock Order Execution: symbol={target_symbol}, side={side}, amount={final_amount}, leverage={leverage}x, SL={stop_loss_pct*100}%")
            return {
                "id": f"mock_order_{int(time.time()*1000)}",
                "symbol": target_symbol,
                "side": side_lower,
                "amount": final_amount,
                "leverage": leverage,
                "stop_loss_pct": stop_loss_pct,
                "status": "executed",
                "mock": True
            }

        try:
            is_spot = False
            if self.exchange.markets and target_symbol in self.exchange.markets:
                is_spot = self.exchange.markets[target_symbol].get("spot", False)
            elif ":" not in target_symbol and "XPERP" not in target_symbol:
                is_spot = True

            if not is_spot:
                try:
                    await self.set_leverage(leverage, target_symbol)
                except Exception as lev_err:
                    logger.warning(f"Skipping set_leverage for {target_symbol}: {lev_err}")

            params = {}
            if not is_spot:
                params["tdMode"] = "cross"
                pos_side = "short" if side_lower in ["sell", "short"] else "long"
                params["posSide"] = pos_side

            order = await self.exchange.create_order(
                symbol=target_symbol,
                type="market",
                side=side_lower,
                amount=final_amount,
                params=params
            )
            logger.info(f"Successfully executed OKX market order {order.get('id')} for {target_symbol}")

            # Attach conditional Stop Loss algo order if requested
            if not is_spot and stop_loss_pct > 0:
                current_price = price
                if not current_price or current_price <= 0:
                    current_price = float(order.get("average") or order.get("price") or 0.0)
                if not current_price or current_price <= 0:
                    try:
                        ticker = await self.exchange.fetch_ticker(target_symbol)
                        current_price = float(ticker.get("last") or ticker.get("close") or 0.0)
                    except Exception as tick_err:
                        logger.warning(f"Could not fetch ticker price for SL calculation on {target_symbol}: {tick_err}")

                if current_price and current_price > 0:
                    pos_side = "short" if side_lower in ["sell", "short"] else "long"
                    sl_attach_result = await self.attach_stop_loss(
                        symbol=target_symbol,
                        side=side_lower,
                        entry_price=current_price,
                        stop_loss_pct=stop_loss_pct,
                        pos_side=pos_side
                    )
                    # attach_stop_loss swallows its own exceptions and returns
                    # {"error": ...} instead of raising — surface that here rather
                    # than silently reporting the order as fully executed.
                    if isinstance(sl_attach_result, dict) and sl_attach_result.get("error"):
                        logger.warning(
                            f"SL attach failed for {target_symbol}: {sl_attach_result.get('error')}"
                        )

            return order
        except Exception as e:
            logger.error(f"Error executing order on OKX for {target_symbol}: {type(e).__name__}: {str(e)}")
            raise

    @staticmethod
    def _compute_sl_trigger_price(pos_side: str, ref_price: float, stop_loss_pct: float) -> float:
        """
        The single formula for where a conditional SL algo's trigger sits, relative to
        `ref_price` (whatever price the caller anchors it to — the addon fill price for
        a High Risk Add, avgPx for a base SL re-attach). Shared by `attach_stop_loss`
        (which actually places the order) and `execute_order` (which needs to report
        the same price back to callers without duplicating the direction logic).
        """
        if pos_side == "short":
            return ref_price * (1.0 + stop_loss_pct)
        return ref_price * (1.0 - stop_loss_pct)

    async def attach_stop_loss(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss_pct: float = 0.02,
        pos_side: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Attach a conditional Stop Loss algo order on OKX for a position.
        Uses private_post_trade_order_algo with closeFraction='1' to protect the entire position.
        """
        if not self.has_credentials or self.exchange is None:
            return {"status": "mock_sl_attached"}

        target_symbol = symbol
        if "XPERP" not in symbol and ":" not in symbol:
            eea_xperp = await self.discover_eea_xperp_symbol(symbol)
            if eea_xperp:
                target_symbol = eea_xperp

        pos_s = pos_side or ("short" if side.lower() in ["sell", "short"] else "long")
        order_s = "buy" if pos_s == "short" else "sell"

        sl_price = self._compute_sl_trigger_price(pos_s, entry_price, stop_loss_pct)

        try:
            sl_px_str = self.exchange.price_to_precision(target_symbol, sl_price)
        except Exception:
            sl_px_str = f"{sl_price:.2f}"

        raw_inst_id = self.to_native_inst_id(target_symbol)

        # Cancel any previous pending conditional SL orders for this instId so the new SL applies cleanly
        try:
            pending = await self.exchange.private_get_trade_orders_algo_pending({
                "instId": raw_inst_id,
                "ordType": "conditional"
            })
            if isinstance(pending, dict) and "data" in pending:
                algo_ids_to_cancel = [
                    {"algoId": item["algoId"], "instId": raw_inst_id}
                    for item in pending["data"]
                    if item.get("posSide") == pos_s or not item.get("posSide")
                ]
                if algo_ids_to_cancel:
                    await self.exchange.private_post_trade_cancel_algos(algo_ids_to_cancel)
                    logger.info(f"Cancelled {len(algo_ids_to_cancel)} previous SL algo orders for {raw_inst_id}")
        except Exception as cancel_err:
            logger.debug(f"Pre-SL cancellation check for {raw_inst_id}: {cancel_err}")

        try:
            res = await self.exchange.private_post_trade_order_algo({
                "instId": raw_inst_id,
                "tdMode": "cross",
                "side": order_s,
                "posSide": pos_s,
                "ordType": "conditional",
                "slTriggerPx": sl_px_str,
                "slOrdPx": "-1",
                "closeFraction": "1",
            })
            logger.info(f"Attached OKX SL algo order for {raw_inst_id} ({pos_s}) at ${sl_px_str}: {res.get('data')}")
            return res
        except Exception as e:
            logger.error(f"Failed to attach SL algo order on OKX for {raw_inst_id}: {e}")
            return {"error": str(e)}

    async def set_hedge_mode(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify/Set Hedge mode (long/short dual position mode) on OKX.
        OKX API endpoint: set_position_mode (posMode='long_short_mode').
        """
        target_symbol = symbol or os.getenv("MMS4_SYMBOL_FUTURES", os.getenv("MMS4_SYMBOL", "BTC/USDC"))
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
                "free": {"USDC": 0.0},
                "total": {"USDC": 0.0},
                "portfolio_usd": {
                    "total_usd": 0.0,
                    "funding_usd": 0.0,
                    "trading_usd": 0.0,
                    "earn_usd": 0.0,
                    "available_usd": 0.0
                },
                "mock": True
            }
        try:
            bal = await self.exchange.fetch_balance()
            portfolio_usd = {
                "total_usd": 0.0,
                "funding_usd": 0.0,
                "trading_usd": 0.0,
                "earn_usd": 0.0,
                "available_usd": 0.0
            }
            funding_assets = {}

            # 1. Query OKX V5 Account Balance directly for exact top-level availEq (free available margin for trading)
            try:
                raw_acc = await self.exchange.private_get_account_balance()
                if raw_acc and raw_acc.get("data"):
                    data0 = raw_acc["data"][0]
                    top_avail_eq = float(data0.get("availEq") or 0.0)
                    top_total_eq = float(data0.get("totalEq") or 0.0)
                    
                    if not isinstance(bal.get("free"), dict):
                        bal["free"] = {}
                    if not isinstance(bal.get("total"), dict):
                        bal["total"] = {}

                    # Directly assign real OKX available equity to USDC free balance
                    if top_avail_eq > 0:
                        bal["free"]["USDC"] = top_avail_eq
                        portfolio_usd["available_usd"] = top_avail_eq
                    if top_total_eq > 0:
                        bal["total"]["USDC"] = top_total_eq
                        portfolio_usd["total_usd"] = top_total_eq

                    details = data0.get("details", [])
                    for item in details:
                        ccy = item.get("ccy")
                        if ccy and ccy != "USDC":
                            avail_b = float(item.get("availEq") or item.get("availBal") or 0.0)
                            tot_eq = float(item.get("eq") or item.get("cashBal") or 0.0)
                            bal["free"][ccy] = avail_b
                            bal["total"][ccy] = tot_eq
            except Exception as ae:
                logger.warning(f"Failed to fetch raw OKX v5 account balance: {ae}")

            # 2. Attempt OKX V5 Asset Valuation call for total USD breakdown
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

            # 3. Attempt OKX V5 Funding asset breakdown
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
        """
        Set leverage for a specific market symbol.

        OKX only accepts integers >= 1, so sub-1x leverage cannot be expressed on the
        exchange. SCOUT_MODE's 0.1x therefore has to be realized through position SIZE,
        not through the leverage field — this is logged explicitly rather than silently
        rounded, because a caller assuming 0.1x reached the exchange would be wrong.
        """
        if not self.has_credentials or self.exchange is None:
            return {"symbol": symbol, "leverage": leverage, "mock": True}
        try:
            lev_int = max(1, int(leverage))
            if leverage < 1.0:
                logger.info(
                    f"Exchange leverage for {symbol} set to {lev_int}x (requested {leverage}x is "
                    f"below the OKX minimum). Risk reduction is applied via position size."
                )
            else:
                logger.info(f"Setting leverage {lev_int}x for symbol {symbol}")
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

    @staticmethod
    def reduction_amount(contracts: float, reduction_pct: float) -> Optional[float]:
        """
        Whole contracts to close for a `reduction_pct` reduction of `contracts`.

        Returns None when a partial reduction cannot be expressed without closing the
        entire position — the caller must then hold. The old rule,
        `max(1, floor(contracts * pct))`, silently turned a 1-contract SPLIT_50 into a
        100% liquidation; dropping the `max(1, …)` is what fixes that.

        Rounding stays on `floor`, deliberately. Contract counts are integers, so a
        partial is only accurate to +/- one contract, and the error must fall on the side
        of closing LESS: this rule exists to trim risk while keeping the position alive.
        Rounding to nearest would close 2 of 3 contracts (67%) for a "50%" split, cutting
        more than intended and irreversibly.
        """
        if contracts <= 0:
            return None
        if reduction_pct >= 1.0:
            return float(contracts)
        amount = float(math.floor(contracts * reduction_pct))
        if amount < 1.0 or amount >= contracts:
            return None
        return amount

    async def reduce_position(
        self,
        symbol: str,
        side: str,
        reduction_pct: float = 0.50,
        reason: str = "STALE_SPLIT"
    ) -> Dict[str, Any]:
        """
        Reduce an open position by a percentage via counter-direction market order.

        In OKX hedge mode (long_short_mode), closing a LONG means selling
        on posSide='long', and closing a SHORT means buying on posSide='short'.

        Args:
            symbol: Trading pair (e.g. 'BTC/USDC')
            side: Position side to reduce ('long' or 'short')
            reduction_pct: Fraction to close (0.50 = 50%, 1.0 = 100%)
            reason: Audit label for logging

        Returns:
            Order result dict or mock result.
        """
        side_lower = side.lower()
        close_side = "sell" if side_lower == "long" else "buy"

        # Resolve EEA X-Perp instrument
        target_symbol = symbol
        if "XPERP" not in symbol and ":" not in symbol:
            eea_xperp = await self.discover_eea_xperp_symbol(symbol)
            if eea_xperp:
                target_symbol = eea_xperp

        # Fetch current position contracts
        positions = await self.fetch_positions()
        clean_target = normalize_symbol(target_symbol) or normalize_symbol(symbol)

        matching_contracts = 0.0
        for p in positions:
            p_sym = p.get("symbol", "")
            p_clean = normalize_symbol(p_sym)
            p_side = str(
                p.get("side") or p.get("info", {}).get("posSide") or ""
            ).lower()
            contracts = float(
                p.get("contracts", 0) or p.get("info", {}).get("pos", 0) or 0
            )
            if p_clean == clean_target and p_side == side_lower and contracts > 0:
                matching_contracts = contracts
                break

        if matching_contracts <= 0:
            logger.warning(
                f"reduce_position: No matching {side_lower} position for {symbol}"
            )
            return {"status": "no_position", "symbol": symbol, "side": side_lower}

        reduce_amount = self.reduction_amount(matching_contracts, reduction_pct)
        if reduce_amount is None:
            logger.info(
                f"reduce_position: {symbol} {side_lower} has {matching_contracts} contract(s); "
                f"a {reduction_pct*100:.0f}% partial reduction is not representable "
                f"without closing the whole position. Holding instead."
            )
            return {
                "status": "not_divisible",
                "symbol": symbol,
                "side": side_lower,
                "contracts": matching_contracts,
                "reason": reason,
            }

        logger.info(
            f"▼ reduce_position: {symbol} {side_lower} "
            f"{reduce_amount}/{matching_contracts} contracts "
            f"({reduction_pct*100:.0f}%) reason={reason}"
        )

        if not self.has_credentials or self.exchange is None:
            return {
                "id": f"mock_reduce_{int(time.time()*1000)}",
                "symbol": target_symbol,
                "side": close_side,
                "amount": reduce_amount,
                "reason": reason,
                "status": "executed",
                "mock": True,
            }

        try:
            params = {
                "tdMode": "cross",
                "posSide": side_lower,  # close on the same posSide in hedge mode
                # Hard guarantee that this order can only shrink the position — if the
                # size drifts between the fetch and the fill, it will not open a new one.
                "reduceOnly": True,
            }
            order = await self.exchange.create_order(
                symbol=target_symbol,
                type="market",
                side=close_side,
                amount=reduce_amount,
                params=params,
            )
            logger.info(
                f"✓ reduce_position executed: {order.get('id')} "
                f"closed {reduce_amount} contracts of {target_symbol} ({reason})"
            )
            return order
        except Exception as e:
            logger.error(
                f"Error in reduce_position for {target_symbol}: "
                f"{type(e).__name__}: {str(e)}"
            )
            raise

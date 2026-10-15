import os
import time
import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from mms4tk.state_manager import (
    MMS4StateManager, MMS4State, BASE_ALLOCATION, ADDON_ALLOCATION, SCOUT_COUNTING_REASONS,
)
from mms4tk.okx_executor import OKXExecutor, normalize_symbol, BelowMinimumNotional, is_maintenance_locked, set_maintenance_lock
from mms4tk.analytics import MMS4SignalEngine, SignalResult, Candle, TMAIndicator, IndicatorParams, HigherTimeframeAnalytics
from mms4tk.logger_db import LoggerDB
from mms4tk.inactivity_manager import MMS4InactivityManager, StaleAction

logger = logging.getLogger("mms4tk.daemon")


def _parse_correlated_pairs(raw: str) -> List[frozenset]:
    """Parse `MMS4_CORRELATED_PAIRS` ("BTC/USDC:ETH/USDC,XAU/USDC:XAG/USDC") into a list of
    2-symbol frozensets. Malformed entries are skipped with a warning rather than crashing the
    daemon at import time."""
    pairs: List[frozenset] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":") if p.strip()]
        if len(parts) != 2:
            logger.warning(f"Ignoring malformed MMS4_CORRELATED_PAIRS entry: {chunk!r}")
            continue
        pairs.append(frozenset(normalize_symbol(p) for p in parts))
    return pairs


# ── Mutual Exclusion Guard for correlated symbols (Issue #130) ──────────────
#
# Production evidence (.ai/analysis/2026-10-14-sl-loss-attribution.md §6): of the 30 production
# STOP-LOSS closes, three strictly-simultaneous (<=10 minutes apart, same side) BTC+ETH pairs
# account for 23% of all realized SL loss (-17.49 USD brutto / -18.37 USD netto of -75.62/-79.83
# total) -- a single market impulse repeatedly stopped out both symbols independently. Default
# pair is BTC/USDC+ETH/USDC only; XAU/USDC and XAG/USDC showed no comparable correlated-SL
# pattern in the analysis and are deliberately NOT included without evidence.
CORRELATED_PAIRS: List[frozenset] = _parse_correlated_pairs(
    os.getenv("MMS4_CORRELATED_PAIRS", "BTC/USDC:ETH/USDC")
)


def _correlated_symbol(target_symbol: str) -> Optional[str]:
    """The other symbol in `target_symbol`'s configured correlated pair, if any."""
    norm_target = normalize_symbol(target_symbol)
    for pair in CORRELATED_PAIRS:
        if norm_target in pair:
            other = next((s for s in pair if s != norm_target), None)
            if other:
                return other
    return None


def check_correlation_guard(
    live_positions: list, target_symbol: str, target_pos_side: str
) -> Optional[Dict[str, Any]]:
    """Pure check (no I/O, no locking needed): the correlated symbol's open same-direction
    position, if any, that should block a fresh `target_pos_side` base entry on `target_symbol`.
    Returns None when the entry is not blocked (no correlated pair configured, correlated symbol
    flat, or correlated symbol holds the OPPOSITE side)."""
    correlated_symbol = _correlated_symbol(target_symbol)
    if not correlated_symbol or not target_pos_side:
        return None
    for p in live_positions:
        if normalize_symbol(p.get("symbol", "")) != correlated_symbol:
            continue
        if _pos_contracts(p) <= 0:
            continue
        if _pos_side(p) == target_pos_side:
            return p
    return None


def _pos_contracts(p: Dict[str, Any]) -> float:
    """Contract count of a CCXT/OKX position dict, 0.0 when absent."""
    try:
        return float(p.get("contracts", 0.0) or p.get("info", {}).get("pos", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pos_side(p: Dict[str, Any]) -> str:
    """'long' / 'short' for a position dict, '' when undeterminable."""
    return str(p.get("side") or p.get("info", {}).get("posSide") or "").lower()


def _pos_entry_price(p: Dict[str, Any]) -> float:
    """Average entry price of a position dict, 0.0 when absent."""
    try:
        return float(
            p.get("entryPrice")
            or p.get("info", {}).get("avgPx")
            or p.get("info", {}).get("openAvgPx")
            or 0.0
        )
    except (TypeError, ValueError):
        return 0.0


def _pos_notional(p: Dict[str, Any], price: float, symbol: str) -> float:
    """Notional USD of a position, falling back to contracts x ctVal x price."""
    try:
        raw = float(
            p.get("notional")
            or p.get("notional_usd")
            or p.get("info", {}).get("notionalUsd")
            or 0.0
        )
    except (TypeError, ValueError):
        raw = 0.0
    if raw > 0 or price <= 0:
        return raw
    try:
        ct_size = float(p.get("contractSize", 0.0) or p.get("info", {}).get("ctVal", 0.0) or 0.0)
    except (TypeError, ValueError):
        ct_size = 0.0
    if ct_size <= 0:
        sym_u = symbol.upper()
        if "BTC" in sym_u:
            ct_size = 0.0001
        elif "ETH" in sym_u or "XAU" in sym_u:
            ct_size = 0.001
        elif "XAG" in sym_u:
            ct_size = 0.01
        else:
            ct_size = 1.0
    return _pos_contracts(p) * ct_size * price


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
        
        # Allocation split is relational, so every figure below scales with the cap —
        # $10 of forward-testing and $1000 of live capital run identical arithmetic.
        self.base_allocation = BASE_ALLOCATION
        self.addon_allocation = ADDON_ALLOCATION
        self.base_trade_usd = round(self.max_margin_usd * self.base_allocation, 2)
        self.addon_trade_usd = round(self.max_margin_usd * self.addon_allocation, 2)
        self.trade_amount_usd = self.max_margin_usd

        self._is_running: bool = False
        self.processed_cycles: int = 0
        self.last_cycle_timestamp: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self.last_signal: Optional[SignalResult] = None
        self.inactivity_manager: MMS4InactivityManager = MMS4InactivityManager(logger_db=self.logger_db)
        self._last_candle_ts: Dict[str, int] = {}  # per-symbol: timestamp of last known closed candle
        self._last_signal_candle_ts: Dict[str, int] = {}  # PLAN-008 Ph2: per-symbol, last closed-candle ts an entry signal was dispatched on
        self._sl_cooldown_until: Dict[str, int] = {}  # PLAN-008 Ph2: per-symbol, candle ts (ms) until which new entries are blocked after a reconciled SL

        # Stop-loss percentages used when opening; reconciliation compares the exit price
        # against these to decide whether a vanished position was a stop-out.
        self.base_sl_pct: float = float(os.getenv("MMS4_BASE_SL_PCT", "0.02"))
        # Issue #95: daily USD drawdown circuit breaker. Default 0 = disabled — this
        # never changes behavior unless explicitly configured. Positive value = the
        # most negative today's realized net PnL (across all symbols) is allowed to
        # go before new entries are blocked for the rest of the UTC day via the
        # existing maintenance-lock gate (same one emergency_killall uses).
        self.daily_drawdown_limit_usd: float = float(os.getenv("MMS4_DAILY_DRAWDOWN_LIMIT_USD", "0"))
        self._daily_drawdown_halted_on: Optional[str] = None  # UTC date string, so the halt logs once per day
        # (symbol, side) pairs whose addon ledger is already known empty — avoids a no-op
        # DELETE on every cycle for the side that has no position.
        self._addons_known_clear: set = set()
        self.addon_sl_pct: float = float(os.getenv("MMS4_ADDON_SL_PCT", "0.0125"))

        # ── Portfolio Margin Guard (Circuit Breaker) ────────────────────
        # Global cap on total committed margin across ALL symbols.
        # When > 0, the daemon blocks any new entry whose addition would push
        # the aggregate portfolio notional above this limit. Set to 0 to disable.
        #
        # Audit P9.2 (2026-09-08): factory default raised 0 -> 300 USD. At current
        # ~$100 equity / up-to-$300 notional (2x($105+$45)) this cap is inert today,
        # but it becomes a real brake once the account scales — a guard that blocks
        # nothing by default is not a guard. Hybrid resolution, hardcoded default
        # overridable by persisted config: MMS4_TOTAL_MAX_PORTFOLIO_MARGIN_USD (if
        # set) replaces the 300 factory default, and a system_settings row (if an
        # operator has ever explicitly set one via POST /api/v1/settings/update)
        # wins over both — same precedence every other runtime-tunable parameter in
        # this daemon already follows (see _get_indicator_params below), so an
        # operator's own choice always survives a redeploy while a fresh install
        # still starts with the guard on.
        _env_margin_cap = os.getenv("MMS4_TOTAL_MAX_PORTFOLIO_MARGIN_USD")
        self.total_max_portfolio_margin_usd: float = (
            float(_env_margin_cap) if _env_margin_cap is not None else 300.0
        )
        # get_setting()'s documented contract is Optional[str] — checking isinstance
        # rather than just "is not None" matters here: a bare unittest.mock.MagicMock()
        # passed as logger_db in tests (several construct MMS4Daemon() this way) auto-
        # fabricates a non-None return for any attribute access, and float() coerces a
        # MagicMock without raising (it defines __float__), so a naive "not None" check
        # would silently adopt a bogus cap from an unconfigured mock.
        _db_margin_cap = self.logger_db.get_setting("total_max_portfolio_margin_usd")
        if isinstance(_db_margin_cap, str):
            try:
                self.total_max_portfolio_margin_usd = float(_db_margin_cap)
            except (TypeError, ValueError):
                logger.warning(
                    f"Ignoring invalid persisted total_max_portfolio_margin_usd={_db_margin_cap!r}"
                )

        # Serializes check-then-commit across concurrently running symbol cycles (see
        # reserve_portfolio_margin below) and protects _reserved_notional_usd.
        self._portfolio_margin_lock: asyncio.Lock = asyncio.Lock()
        # Notional already approved by the guard this cycle-batch but not yet visible in
        # live_positions (the order hasn't landed on the exchange, or the next
        # fetch_positions() hasn't run yet). Included in the guard's total so a sibling
        # symbol racing the same cap sees it as committed.
        self._reserved_notional_usd: float = 0.0

        # Issue #130: `execute_symbol_cycle` fetches `live_positions` independently per symbol
        # (default `live_positions=None` -> its own `fetch_positions()` call), and
        # `execute_multi_symbol_cycle` runs every symbol concurrently via `asyncio.gather` --
        # production evidence shows BTC's and ETH's entries landing ~0.1s apart (same
        # cycle-batch, .ai/analysis/2026-10-14-sl-loss-attribution.md §4's 18.09 entry
        # timestamps). A plain live_positions read-check never sees the sibling's
        # about-to-be-placed order, exactly the case this guard exists for. Serializes
        # check-then-commit the same way `_portfolio_margin_lock` does for the margin cap.
        self._correlation_guard_lock: asyncio.Lock = asyncio.Lock()
        self._pending_base_entries: Dict[str, str] = {}  # normalized symbol -> side ("long"/"short")

    # ── Portfolio Margin Guard ───────────────────────────────────────────

    def check_portfolio_margin_guard(
        self,
        live_positions: list,
        new_notional_usd: float,
        target_symbol: str,
        extra_reserved_usd: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Verify that opening a new position of `new_notional_usd` would not push
        the total portfolio notional above `total_max_portfolio_margin_usd`.

        `extra_reserved_usd` folds in notional reserved by other in-flight cycles (see
        reserve_portfolio_margin) that isn't in `live_positions` yet — a pure snapshot
        check here would let concurrent symbol cycles each pass against the same stale
        total and jointly blow through the cap.

        Returns a dict:
            allowed: bool — True if the order may proceed
            current_total_usd: float — aggregate notional of all open positions
            reserved_usd: float — notional reserved by other in-flight cycles this batch
            headroom_usd: float — remaining capacity before cap
            cap_usd: float — the configured cap (0 = disabled)
        """
        cap = self.total_max_portfolio_margin_usd
        if cap <= 0:
            return {
                "allowed": True,
                "current_total_usd": 0.0,
                "reserved_usd": 0.0,
                "headroom_usd": float("inf"),
                "cap_usd": 0.0,
            }

        total_notional = 0.0
        for p in live_positions:
            contracts = _pos_contracts(p)
            if contracts <= 0:
                continue
            sym = p.get("symbol", "")
            price = 0.0
            try:
                price = float(
                    p.get("markPrice")
                    or p.get("info", {}).get("markPx")
                    or p.get("entryPrice")
                    or p.get("info", {}).get("avgPx")
                    or 0.0
                )
            except (TypeError, ValueError):
                pass
            total_notional += _pos_notional(p, price, sym)

        total_notional += max(0.0, extra_reserved_usd)

        headroom = cap - total_notional
        allowed = (total_notional + new_notional_usd) <= cap * 1.05  # 5% tolerance

        if not allowed:
            logger.warning(
                f"🛡️ PORTFOLIO MARGIN GUARD: Blocking {target_symbol} order "
                f"(+${new_notional_usd:.2f}). Portfolio notional: ${total_notional:.2f} "
                f"(incl. ${extra_reserved_usd:.2f} reserved) / ${cap:.2f} cap. "
                f"Headroom: ${headroom:.2f}"
            )

        return {
            "allowed": allowed,
            "current_total_usd": round(total_notional, 2),
            "reserved_usd": round(max(0.0, extra_reserved_usd), 2),
            "headroom_usd": round(headroom, 2),
            "cap_usd": cap,
        }

    async def reserve_portfolio_margin(
        self, live_positions: list, new_notional_usd: float, target_symbol: str
    ) -> Dict[str, Any]:
        """
        Atomic check-and-commit wrapper around check_portfolio_margin_guard.

        execute_multi_symbol_cycle runs every symbol's cycle concurrently via
        asyncio.gather. Without this lock, two symbols can each fetch the same
        pre-order positions snapshot, both pass the cap check against it, and jointly
        exceed the cap once both orders land — the guard's own purpose defeated by the
        concurrency it runs under. Holding a global lock across the check makes the
        decisions serial; the caller MUST pair a True result with a matching
        release_portfolio_reservation(new_notional_usd) once the order attempt (success
        or failure) is finished, in a finally block.
        """
        async with self._portfolio_margin_lock:
            pmg = self.check_portfolio_margin_guard(
                live_positions,
                new_notional_usd,
                target_symbol,
                extra_reserved_usd=self._reserved_notional_usd,
            )
            if pmg["allowed"]:
                self._reserved_notional_usd += new_notional_usd
            return pmg

    async def release_portfolio_reservation(self, amount: float) -> None:
        """
        Release a reservation taken by reserve_portfolio_margin.

        Called once the order attempt this cycle is done (executed, failed, or
        skipped) — a filled order becomes real notional via the next fetch_positions(),
        so the reservation must not linger past this cycle or it double-counts forever.
        """
        if amount <= 0:
            return
        async with self._portfolio_margin_lock:
            self._reserved_notional_usd = max(0.0, self._reserved_notional_usd - amount)

    # ── Mutual Exclusion Guard (Issue #130) ──────────────────────────────

    async def reserve_correlation_guard(
        self, live_positions: list, target_symbol: str, target_pos_side: str
    ) -> Optional[Dict[str, Any]]:
        """
        Atomic check-and-commit wrapper around check_correlation_guard, mirroring
        reserve_portfolio_margin's race-closing pattern. Returns the blocking position dict
        (real or a pending-reservation placeholder) when blocked, None when the entry may
        proceed; a None result reserves `target_symbol`'s side until
        release_correlation_reservation() is called.
        """
        async with self._correlation_guard_lock:
            blocked = check_correlation_guard(live_positions, target_symbol, target_pos_side)
            if blocked is None:
                correlated_symbol = _correlated_symbol(target_symbol)
                if correlated_symbol and self._pending_base_entries.get(correlated_symbol) == target_pos_side:
                    blocked = {"symbol": correlated_symbol, "side": target_pos_side, "contracts": 0.0, "pending": True}
            if blocked is None:
                self._pending_base_entries[normalize_symbol(target_symbol)] = target_pos_side
            return blocked

    async def release_correlation_reservation(self, target_symbol: str) -> None:
        """
        Release a reservation taken by reserve_correlation_guard.

        Called once the order attempt this cycle is done (executed, failed, or skipped) — a
        filled order becomes real, visible in the next fetch_positions(), so the reservation
        must not linger past this cycle.
        """
        async with self._correlation_guard_lock:
            self._pending_base_entries.pop(normalize_symbol(target_symbol), None)

    # ── Position sizing ─────────────────────────────────────────────────

    def plan_allocation(self, symbol: str) -> Dict[str, Any]:
        """
        Resolve how much capital this symbol may commit right now.

        Everything is expressed as a relation to `max_margin_usd`, so the same code paths
        serve a $10 forward test and a $1000 live allocation:

            margin_base  = max_margin_usd * BASE_ALLOCATION  * size_multiplier
            margin_addon = max_margin_usd * ADDON_ALLOCATION * size_multiplier
            notional     = margin * leverage

        `size_multiplier` is 1.0 in BASE_MODE and SCOUT_SIZE_MULT in SCOUT_MODE, which is
        what actually reduces defensive exposure — leverage stays at an exchange-legal
        value (>= 1x) because OKX rejects anything smaller.
        """
        snap = self.state_manager.get_risk_profile_snapshot(symbol)
        leverage = float(snap["leverage"])
        size_mult = float(snap["size_multiplier"])

        # Resolve symbol-specific budget/margin cap from dynamic configuration
        sym_margin_usd = self.max_margin_usd
        base_ratio = self.base_allocation
        addon_ratio = self.addon_allocation

        try:
            from mms4tk.api import configured_symbols_store
            sym_key = symbol.upper()
            if "/" not in sym_key:
                sym_key = f"{sym_key}/USDC"
            sym_cfg = configured_symbols_store.get(sym_key) or configured_symbols_store.get(symbol) or {}
            if isinstance(sym_cfg, dict):
                if sym_cfg.get("allocation_usd") is not None and float(sym_cfg["allocation_usd"]) > 0:
                    sym_margin_usd = float(sym_cfg["allocation_usd"])
                elif sym_cfg.get("budget_usd") is not None and float(sym_cfg["budget_usd"]) > 0:
                    sym_margin_usd = float(sym_cfg["budget_usd"])
                
                if sym_cfg.get("base_ratio") is not None:
                    base_ratio = float(sym_cfg["base_ratio"]) / 100.0
                    addon_ratio = float(sym_cfg.get("add_ratio", 100 - sym_cfg["base_ratio"])) / 100.0
        except Exception:
            pass

        from mms4tk.state_manager import SCOUT_EXIT_K
        scout_since = self.state_manager.get_scout_since(symbol)
        scout_age_hours = (
            round((datetime.now(timezone.utc) - scout_since).total_seconds() / 3600.0, 2)
            if scout_since else None
        )

        margin_base = sym_margin_usd * base_ratio * size_mult
        margin_addon = sym_margin_usd * addon_ratio * size_mult

        return {
            "state": snap["state"],
            "leverage": leverage,
            "size_multiplier": size_mult,
            "margin_base_usd": round(margin_base, 8),
            "margin_addon_usd": round(margin_addon, 8),
            "notional_base_usd": round(margin_base * leverage, 8),
            "notional_addon_usd": round(margin_addon * leverage, 8),
            "margin_cap_usd": round(sym_margin_usd * size_mult, 8),
            "notional_cap_usd": round(sym_margin_usd * size_mult * leverage, 8),
            "notional_reduction_factor": snap["notional_reduction_factor"],
            "scout_since": scout_since.isoformat() if scout_since else None,
            "scout_age_hours": scout_age_hours,
            "scout_ok": self.state_manager.get_scout_ok(symbol),
            "scout_exit_k": SCOUT_EXIT_K,
        }

    # ── Addon ledger ────────────────────────────────────────────────────
    # Persisted in SQLite so a daemon restart cannot forget that an addon was already
    # filled and book a second one on the same position.

    @staticmethod
    def _position_key(entry_price: float) -> str:
        """Identity of a position instance. A new entry price means a new position."""
        return f"{float(entry_price):.8f}"

    def _addon_count(self, symbol: str, side: str, entry_price: float) -> int:
        try:
            # int() also guards against a logger_db double returning a non-numeric value,
            # which would otherwise blow up the comparison at the call site.
            self._addons_known_clear.discard((symbol, side.lower()))
            return int(self.logger_db.get_addon_count(symbol, side, self._position_key(entry_price)))
        except Exception as e:
            logger.warning(f"Addon ledger read failed for {symbol}/{side}: {e}")
            return 0

    def _record_addon(self, symbol: str, side: str, entry_price: float) -> int:
        try:
            self._addons_known_clear.discard((symbol, side.lower()))
            return self.logger_db.record_addon(symbol, side, self._position_key(entry_price))
        except Exception as e:
            logger.warning(f"Addon ledger write failed for {symbol}/{side}: {e}")
            return 1

    def _base_entry_price(self, symbol: str, side: str, fallback: float) -> float:
        """
        The position's pinned original base entry price (P3 V2 SSOT for SL), or
        `fallback` (today's avgPx) if no snapshot row exists yet for this cycle.

        reconcile_positions refreshes this snapshot for every still-open position
        before the Self-Healing SL Guard runs each cycle, so the normal case always
        finds a real value here; the fallback only covers a position appearing for
        the first time this exact cycle.
        """
        try:
            for snap in self.logger_db.get_position_snapshots(symbol):
                if str(snap.get("side", "")).lower() == side.lower():
                    value = float(snap.get("base_entry_price") or 0.0)
                    return value if value > 0 else fallback
        except Exception as e:
            logger.warning(f"base_entry_price lookup failed for {symbol}/{side}: {e}")
        return fallback

    @staticmethod
    def _stale_reduced_contracts(reduce_result: Any, active_pos: Any, fraction: float) -> float:
        """Contracts a stale reduction closed: the order's own `amount` when reported,
        otherwise the requested fraction of the position that was open before it."""
        try:
            amount = float((reduce_result or {}).get("amount") or 0.0)
        except (TypeError, ValueError, AttributeError):
            amount = 0.0
        if amount > 0:
            return amount
        return _pos_contracts(active_pos) * fraction if active_pos is not None else 0.0

    async def _fetch_close_fill(
        self, symbol: str, side: str, requested_at_ms: int, attempts: int = 2, delay_s: float = 1.0,
        timeout_s: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        """The real fill of the close just placed (OKX order history), or None.

        `requested_at_ms` (taken before the order was sent) keeps an older, unrelated close of
        the same symbol/side from being mistaken for this one. OKX history can lag the order by
        a moment, hence one short retry. Each attempt is bounded by `timeout_s` (the executor can
        issue several REST calls and swallows their errors), so a slow or unreachable OKX cannot
        stall the trading cycle. Any failure or timeout returns None: the caller then books an
        ESTIMATE, which the SCOUT clean-close rule treats as neutral. (A local clock running more
        than the 5 s margin ahead of OKX would also reject our own fill - same safe outcome.)
        """
        fetch = getattr(self.executor, "fetch_last_closing_order", None)
        if fetch is None:
            return None
        for attempt in range(attempts):
            try:
                res = fetch(symbol, side, min_fill_time_ms=requested_at_ms)
                if inspect.isawaitable(res) or asyncio.iscoroutine(res):
                    res = await asyncio.wait_for(res, timeout=timeout_s)
                if isinstance(res, dict) and float(res.get("exit_price") or 0.0) > 0:
                    return res
            except Exception as e:
                logger.warning(f"Could not fetch the close fill for {symbol}/{side}: {e}")
                return None
            if attempt + 1 < attempts:
                await asyncio.sleep(delay_s)
        return None

    @staticmethod
    def _stale_entry_price(active_entry_price: float, active_side: Any, t_side: str, t_entry: float) -> float:
        """Entry price for a stale-close PnL: the live position's avgPx, but only when that
        position is the SAME leg the inactivity tracker is closing (`active_pos` can be the
        opposite leg in hedge mode); otherwise the tracker's own entry."""
        if active_entry_price > 0 and str(active_side or "").lower() == str(t_side or "").lower():
            return active_entry_price
        return t_entry

    def _book_stale_close_if_executed(
        self, reduce_result: Any, symbol: str, side: str, entry_price: float,
        exit_price: float, contracts_closed: float, reason: str,
        fill: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """`_book_stale_close`, but only when `reduce_position` really placed a closing
        order. A `no_position` result (the position had already gone, e.g. a stop filled
        between the position fetch and the reduce) must not leave a fictitious
        FORCE_CLOSE in the ledger; the real stop-out is booked by reconciliation."""
        if not self._close_succeeded(reduce_result):
            logger.info(f"Stale {reason} for {symbol} {side.upper()} not booked: no closing order was placed.")
            return False
        fill_price = float((fill or {}).get("exit_price") or 0.0)
        self._book_stale_close(
            symbol, side, entry_price, fill_price if fill_price > 0 else exit_price,
            contracts_closed, reason, pnl_is_estimate=fill_price <= 0,
        )
        return True

    def _book_stale_close(
        self, symbol: str, side: str, entry_price: float, exit_price: float,
        contracts_closed: float, reason: str, pnl_is_estimate: bool = True,
    ) -> None:
        """Book an Inactivity-Manager close (SPLIT_50 / FORCE_CLOSE / FORCE_CLOSE_BE /
        BE_ESCAPE) through `finalize_close`, so these paths are no longer invisible to the
        state manager's ledger. The PnL is an ESTIMATE from the current price (like the
        midline path), not the fill; bookkeeping failures are logged and never block the
        trading cycle. State impact is unchanged: SPLIT_50 is a partial TP, the full
        closes are neither SL nor TP (see `CLOSE_REASON_FLAGS`)."""
        try:
            if entry_price > 0 and exit_price > 0:
                pnl_pct = (
                    (exit_price - entry_price) / entry_price if side == "long"
                    else (entry_price - exit_price) / entry_price
                )
            else:
                pnl_pct = 0.0
            ct_size = self._get_contract_size(symbol)
            self.state_manager.finalize_close(
                symbol=symbol, side=side, reason=reason,
                entry_price=entry_price, exit_price=exit_price, amount=contracts_closed,
                pnl=pnl_pct * entry_price * contracts_closed * ct_size, pnl_percent=pnl_pct,
                pnl_is_estimate=pnl_is_estimate,
            )
        except Exception as book_err:
            logger.error(f"Failed to book stale close ({reason}) for {symbol}: {book_err}")

    def _clear_addons(self, symbol: str, side: Optional[str] = None) -> None:
        """
        Drop the addon ledger for a symbol/side.

        Called every cycle for whichever side has no open position, so an in-memory guard
        keeps it from re-issuing the same no-op DELETE 5 760 times a day per side.
        """
        key = (symbol, (side or "").lower())
        if key in self._addons_known_clear:
            return
        try:
            self.logger_db.clear_addon_state(symbol, side)
            self._addons_known_clear.add(key)
        except Exception as e:
            logger.warning(f"Addon ledger clear failed for {symbol}: {e}")

    @property
    def _addon_executed_symbols(self) -> set:
        """
        Backwards-compatible view of the persisted addon ledger.

        Kept so existing callers/tests that inspect this attribute keep working; the
        authoritative store is the position_addons table.
        """
        try:
            return {row["symbol"] for row in self.logger_db.get_all_addon_state()
                    if int(row.get("addon_count", 0)) > 0}
        except Exception:
            return set()

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

    # ── Stop-loss reconciliation ────────────────────────────────────────

    def _get_contract_size(self, symbol: str) -> float:
        """Return contract size for symbol from executor markets, falling back to 0.0001 for BTC and 1.0 for others."""
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

    async def _async_reconcile_positions(
        self,
        target_symbol: Optional[str] = None,
        live_positions: Optional[List[Dict[str, Any]]] = None,
        current_price: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Compare the exchange's open positions against the snapshot from the previous cycle.

        A position present last cycle but absent now was closed by the exchange — almost
        always an exchange-side stop-loss trigger, since every intentional close the daemon
        performs (take-profit flip, stale evacuation) clears its own snapshot first. When
        the move against the entry reached the SL distance, the state machine is notified
        immediately instead of waiting for the 15-minute trade_sync worker.

        Snapshots live in SQLite, so this also works across a daemon restart.

        Returns a list of reconciliation events (empty when nothing changed).
        """
        if target_symbol is None:
            target_symbol = self.symbol
        if live_positions is None:
            try:
                live_positions = await self.executor.fetch_positions()
            except Exception as pe:
                logger.warning(f"Failed to fetch live positions for reconciliation: {pe}")
                live_positions = []
        if current_price is None:
            current_price = 0.0

        events: List[Dict[str, Any]] = []

        live_by_side: Dict[str, Dict[str, Any]] = {}
        for p in live_positions:
            if normalize_symbol(p.get("symbol", "")) != normalize_symbol(target_symbol):
                continue
            contracts = _pos_contracts(p)
            if contracts <= 0:
                continue
            side = _pos_side(p)
            if side:
                live_by_side[side] = p

        try:
            snapshots = self.logger_db.get_position_snapshots(target_symbol)
        except Exception as e:
            logger.warning(f"Could not read position snapshots for {target_symbol}: {e}")
            return events

        # 1. Positions that disappeared since the previous cycle
        for snap in snapshots:
            side = str(snap["side"]).lower()
            if side in live_by_side:
                continue

            entry_price = float(snap["entry_price"] or 0.0)

            # Lower bound for the closing-order search: the last time this snapshot was
            # confirmed still open (refreshed every cycle while alive — see
            # LoggerDB.save_position_snapshot). Anything closing THIS position must have
            # a fillTime at or after that moment; without this bound, a stale unrelated
            # historical close for the same symbol/side sitting in the last 10 order-
            # history rows could be mistaken for the fill that just happened.
            min_fill_time_ms: Optional[int] = None
            try:
                updated_at = snap.get("updated_at") if hasattr(snap, "get") else None
                if updated_at:
                    min_fill_time_ms = int(datetime.fromisoformat(str(updated_at)).timestamp() * 1000)
            except (ValueError, TypeError) as pe:
                logger.debug(f"Could not parse snapshot updated_at for {target_symbol}/{side}: {pe}")

            # Fetch exact closing order from OKX order history
            closing_order = None
            if hasattr(self.executor, "fetch_last_closing_order"):
                try:
                    res = self.executor.fetch_last_closing_order(
                        target_symbol, side, min_fill_time_ms=min_fill_time_ms
                    )
                    if inspect.isawaitable(res) or asyncio.iscoroutine(res):
                        closing_order = await res
                    elif isinstance(res, dict):
                        closing_order = res
                except Exception as fe:
                    logger.warning(f"Failed to fetch last closing order for {target_symbol}/{side}: {fe}")

            if not isinstance(closing_order, dict):
                closing_order = None

            is_real_fill = False
            real_pnl_usd = None
            real_fee_usd = None

            if closing_order:
                try:
                    ep = float(closing_order.get("exit_price") or 0.0)
                    if ep > 0:
                        exit_price = ep
                        is_real_fill = True
                        if closing_order.get("pnl_usd") is not None:
                            real_pnl_usd = float(closing_order["pnl_usd"])
                        if closing_order.get("fee_usd") is not None:
                            real_fee_usd = float(closing_order["fee_usd"])
                    else:
                        exit_price = float(current_price or snap.get("last_price") or 0.0)
                except (ValueError, TypeError):
                    exit_price = float(current_price or snap.get("last_price") or 0.0)
            else:
                exit_price = float(current_price or snap.get("last_price") or 0.0)

            if entry_price <= 0 or exit_price <= 0:
                self.logger_db.delete_position_snapshot(target_symbol, side)
                continue

            if side == "long":
                pnl_pct = (exit_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - exit_price) / entry_price

            loss_pct = -pnl_pct  # positive when the position lost money (vs avgPx — accounting only)

            # P3 V2 (2026-09-10, supersedes the addon-composite-trigger mechanism
            # below in git history): the SL is anchored to base_entry_price — pinned
            # for the life of the position at its first snapshot, never moved by
            # addon averaging — not to avgPx. This is a determinism fix, not an
            # addon-aware heuristic: previously the SAME position could get two
            # different SL prices depending on which code path attached last
            # (execute_order's addon attach vs the Self-Healing Guard, different
            # anchors). V2 removes the addon leg's own SL entirely (see the
            # HIGH_RISK_ADD branch and the Self-Healing Guard below/elsewhere in
            # this file) — there is only ever ONE trigger, always base_sl_pct away
            # from base_entry_price, regardless of addons. This also structurally
            # eliminates the round-2-review "inverted classification on degenerate
            # geometry" bug the previous mechanism had — the trigger is by
            # construction always on the adverse side of its own anchor — and
            # widens the tolerance band back to base_sl_pct's (2%, not addon_sl_pct's
            # ~1.25%), matching the pre-P3 heuristic's tolerance to candle-close
            # staleness.
            _raw_base_entry_price = float(snap.get("base_entry_price") or 0.0)
            base_entry_price = _raw_base_entry_price if _raw_base_entry_price > 0 else entry_price
            sl_distance = base_entry_price * self.base_sl_pct
            if side == "long":
                adverse_move = base_entry_price - exit_price
            else:
                adverse_move = exit_price - base_entry_price
            # Tolerance: the stop fills at market, so allow a little slippage either way.
            is_stop_out = adverse_move >= sl_distance * 0.75
            # Reported separately from pnl_pct/loss_pct (which stay avgPx-based, for
            # correct P&L accounting): this is what state_manager.on_stop_loss_hit's
            # >=2% SCOUT_MODE/Hermes-H4 gate sees, and it is now always ~base_sl_pct
            # whenever a genuine stop fires — addon or not — so that gate no longer
            # silently never fires for addon-carrying positions (state_manager.py:344).
            sl_percent = max(adverse_move / base_entry_price, 0.0) if base_entry_price > 0 else 0.0

            contracts = float(snap["contracts"] or 0.0)
            ct_size = self._get_contract_size(target_symbol)
            calc_pnl_usd = pnl_pct * entry_price * contracts * ct_size
            pnl_usd = real_pnl_usd if (is_real_fill and real_pnl_usd is not None) else calc_pnl_usd

            fill_tag = "real_fill" if is_real_fill else "est_fill"
            logger.warning(
                f"⟲ Reconciliation: {target_symbol} {side.upper()} disappeared from OKX "
                f"(entry=${entry_price:,.4f} exit=${exit_price:,.4f} [{fill_tag}] PnL={pnl_pct*100:+.2f}%) "
                f"→ {'STOP-LOSS' if is_stop_out else 'closed without SL trigger'}"
            )

            try:
                trade_id_str = f"recon_{target_symbol}_{side}_{int(time.time()*1000)}"
                # Issue #99: this used to be hardcoded False, so a position that
                # disappeared from OKX without hitting the stop (e.g. a ratcheted
                # exchange SL triggering in profit, #92) never restored SCOUT_MODE
                # to BASE_MODE even when genuinely profitable — the only state-manager
                # signal that does so (`on_take_profit_hit`) was simply never reached.
                # A reconciled close is a take-profit, for state-machine purposes,
                # whenever it wasn't a stop-out and it actually made money.
                if is_stop_out:
                    close_reason = "STOP_LOSS"
                elif pnl_usd > 0:
                    close_reason = "CLOSED_EXTERNAL_TP"
                else:
                    close_reason = "CLOSED_EXTERNAL"
                self.state_manager.finalize_close(
                    trade_id=trade_id_str,
                    symbol=target_symbol,
                    side=side,
                    reason=close_reason,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=contracts,
                    pnl=pnl_usd,
                    pnl_percent=pnl_pct,
                    sl_percent=sl_percent,
                )
                self.logger_db.upsert_closed_trade(
                    trade_id=trade_id_str,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    symbol=target_symbol,
                    side=side.upper(),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=contracts,
                    pnl_usd=pnl_usd,
                    pnl_pct=pnl_pct,
                    is_win=pnl_usd > 0,
                    leverage=float(snap.get("leverage", 1.0)) if snap else 1.0,
                    reason="STOP-LOSS" if is_stop_out else "CLOSED_EXTERNAL",
                )
            except Exception as e:
                logger.error(f"Failed to record reconciled trade for {target_symbol}: {e}")

            self.logger_db.delete_position_snapshot(target_symbol, side)
            self._clear_addons(target_symbol, side)
            try:
                self.logger_db.clear_midline_tp(target_symbol, side)
            except Exception:
                pass
            if self.inactivity_manager.is_tracking(target_symbol):
                self.inactivity_manager.stop_tracking(target_symbol)

            events.append({
                "symbol": target_symbol,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_usd": pnl_usd,
                "pnl_pct": pnl_pct,
                "is_stop_loss": is_stop_out,
                "is_real_fill": is_real_fill,
                "order_id": closing_order.get("order_id") if closing_order else None,
                "new_state": self.state_manager.get_symbol_state(target_symbol).value,
            })

            est_fee = entry_price * contracts * ct_size * 0.001
            fee_usd = real_fee_usd if (is_real_fill and real_fee_usd is not None) else est_fee
            pnl_net = pnl_usd - fee_usd
            pnl_icon = "🟢" if pnl_net >= 0 else "🔴"

            if is_real_fill:
                net_label = f"- Wynik Netto: {pnl_icon} ${pnl_net:+.2f} USD ({pnl_pct*100:+.2f}%)\n"
                fee_label = f"- Prowizje OKX: ${fee_usd:.2f} USD\n"
            else:
                net_label = f"- Wynik Netto (szac.): {pnl_icon} ${pnl_net:+.2f} USD ({pnl_pct*100:+.2f}%)\n"
                fee_label = f"- Prowizje OKX (szac.): ~${fee_usd:.2f} USD\n"

            if is_stop_out:
                try:
                    from mms4tk.hermes_webhook import HermesWebhookClient
                    HermesWebhookClient.send_telegram(
                        title=f"🛑 STOP-LOSS: {target_symbol} {side.upper()}",
                        body=(
                            f"Pozycja zamknięta przez Stop-Loss na giełdzie.\n"
                            f"- Wejście: `${entry_price:,.4f}`\n"
                            f"- Wyjście: `${exit_price:,.4f}`\n"
                            f"{net_label}"
                            f"{fee_label}"
                            f"- Wynik Brutto: ${pnl_usd:+.2f} USD\n"
                            f"- Stan: `{self.state_manager.get_symbol_state(target_symbol).value}`"
                        ),
                        priority=2,
                        dedupe_key=f"SL_{target_symbol}_{side}_{int(entry_price)}",
                    )
                except Exception as e:
                    logger.warning(f"Failed to send stop-loss notification: {e}", exc_info=True)
            else:
                try:
                    from mms4tk.hermes_webhook import HermesWebhookClient
                    HermesWebhookClient.send_telegram(
                        title=f"🔔 ZAMKNIĘCIE POZYCJI: {target_symbol} {side.upper()}",
                        body=(
                            f"Pozycja zamknięta na OKX (Manual/TP).\n"
                            f"- Wejście: `${entry_price:,.4f}`\n"
                            f"- Wyjście: `${exit_price:,.4f}`\n"
                            f"{net_label}"
                            f"{fee_label}"
                            f"- Wynik Brutto: ${pnl_usd:+.2f} USD\n"
                            f"- Stan: `{self.state_manager.get_symbol_state(target_symbol).value}`"
                        ),
                        priority=2,
                        dedupe_key=f"CLOSE_{target_symbol}_{side}_{int(entry_price)}",
                    )
                except Exception as e:
                    logger.warning(f"Failed to send close notification: {e}", exc_info=True)

        # 2. Refresh snapshots for positions that are still open
        for side, p in live_by_side.items():
            entry_px = _pos_entry_price(p)
            try:
                self.logger_db.save_position_snapshot(
                    symbol=target_symbol,
                    side=side,
                    contracts=_pos_contracts(p),
                    entry_price=entry_px,
                    last_price=float(current_price or 0.0),
                )
            except Exception as e:
                logger.warning(f"Could not persist position snapshot for {target_symbol}/{side}: {e}")

        return events

    def reconcile_positions(
        self,
        target_symbol: Optional[str] = None,
        live_positions: Optional[List[Dict[str, Any]]] = None,
        current_price: Optional[float] = None
    ):
        """
        Dual sync/async entry point for position reconciliation.
        If called inside an active asyncio event loop, returns a coroutine to be awaited.
        If called synchronously without an active event loop (e.g. in tests),
        runs the reconciliation via asyncio.run() and returns the event list directly.
        """
        coro = self._async_reconcile_positions(target_symbol, live_positions, current_price)
        try:
            asyncio.get_running_loop()
            return coro
        except RuntimeError:
            return asyncio.run(coro)

    _reconcile_positions = reconcile_positions

    def _get_indicator_params(self, symbol: Optional[str] = None) -> IndicatorParams:
        """Build IndicatorParams from live SQLite system_settings, falling back to
        env vars (and IndicatorParams' own hardcoded defaults) for any missing key.

        PLAN-008 Phase 1: previously evaluate_signals() was always called without
        `params`, so it built a fresh env-only IndicatorParams() internally — any
        engine_mode/adaptive_vr_*/time_lock_* mutation made via the UI (persisted to
        SQLite system_settings) never reached the signal engine at all.

        PLAN-008 Phase 3: the TMA Slope Trend Filter is PER SYMBOL — 90-day calibration
        found the same threshold helps BTC/USDC broadly but hurts ETH/USDC in most
        configurations, so a single global on/off switch would be wrong. Read from
        `system_settings` under per-symbol-qualified keys (e.g.
        "tma_trend_filter_enabled:BTC/USDC"): the `symbol_configurations` SQLite table
        has a fixed schema with no column for these, so storing them only in
        `configured_symbols_store`/symbols.json would silently reset to defaults on the
        next `load_symbols_config()` call (it prefers SQLite whenever any row exists).
        Falls back to `configured_symbols_store` (same-process-lifetime convenience) and
        then to disabled-by-default.

        `high_vol_base_filter_enabled` follows the exact same per-symbol-qualified,
        default-OFF pattern for the same reason: a backtest audit (2026-09-08) found
        it makes results worse in most tested half-windows, so it must stay opt-in
        pending real validation, not silently on for every ADAPTIVE-mode symbol.
        """
        db_settings: Dict[str, str] = {}
        try:
            if self.logger_db:
                db_settings = self.logger_db.get_all_settings()
        except Exception as e:
            logger.warning(f"_get_indicator_params: failed to read system_settings, using env defaults: {e}")

        defaults = IndicatorParams()

        sym_cfg: Dict[str, Any] = {}
        if symbol:
            try:
                from mms4tk.api import configured_symbols_store
                raw_cfg = configured_symbols_store.get(symbol, {})
                if isinstance(raw_cfg, dict):
                    sym_cfg = raw_cfg
            except Exception as e:
                logger.warning(f"_get_indicator_params: failed to read per-symbol config for {symbol}: {e}")

        def _sym_bool(key: str, fallback: bool) -> bool:
            raw = db_settings.get(f"{key}:{symbol}") if symbol else None
            if raw is None:
                raw = db_settings.get(key)
            if raw is not None:
                return str(raw).strip().lower() in ("true", "1", "yes", "on")
            return bool(sym_cfg.get(key, fallback))

        def _sym_int(key: str, fallback: int) -> int:
            raw = db_settings.get(f"{key}:{symbol}") if symbol else None
            if raw is None:
                raw = db_settings.get(key)
            if raw is not None:
                return int(float(raw))
            return int(sym_cfg.get(key, fallback))

        def _sym_float(key: str, fallback: float) -> float:
            raw = db_settings.get(f"{key}:{symbol}") if symbol else None
            if raw is None:
                raw = db_settings.get(key)
            if raw is not None:
                return float(raw)
            return float(sym_cfg.get(key, fallback))

        return IndicatorParams(
            engine_mode=str(db_settings.get("engine_mode", defaults.engine_mode)),
            adaptive_vr_high=float(db_settings.get("adaptive_vr_high", defaults.adaptive_vr_high)),
            adaptive_vr_low=float(db_settings.get("adaptive_vr_low", defaults.adaptive_vr_low)),
            time_lock_start=str(db_settings.get("time_lock_start", defaults.time_lock_start)),
            time_lock_end=str(db_settings.get("time_lock_end", defaults.time_lock_end)),
            tma_trend_filter_enabled=_sym_bool("tma_trend_filter_enabled", defaults.tma_trend_filter_enabled),
            tma_slope_lookback=_sym_int("tma_slope_lookback", defaults.tma_slope_lookback),
            tma_slope_threshold=_sym_float("tma_slope_threshold", defaults.tma_slope_threshold),
            high_vol_base_filter_enabled=_sym_bool("high_vol_base_filter_enabled", defaults.high_vol_base_filter_enabled),
            htf_directional_gate_enabled=_sym_bool("htf_directional_gate_enabled", defaults.htf_directional_gate_enabled),
            midline_partial_tp_enabled=_sym_bool("midline_partial_tp_enabled", defaults.midline_partial_tp_enabled),
            midline_tp_fraction=_sym_float("midline_tp_fraction", defaults.midline_tp_fraction),
        )

    async def _refresh_htf_directional_gate(self, symbol: str, params: IndicatorParams) -> None:
        """Faza 3.2 (task.md): when `htf_directional_gate_enabled` is on for
        `symbol`, fetch real H4 candles and update this symbol's automatic HTF
        bias reading (`state_manager.set_symbol_htf_bias`) -- "ABOVE" (H4 close
        beyond its own upper TMA band) -> LONG_ONLY, "BELOW" -> SHORT_ONLY,
        "INSIDE" -> BOTH. Mirrors exactly the causal H4-bucket bias
        `backtest_engine._htf_directional_bias_series` was measured with (same
        `HigherTimeframeAnalytics.calculate_higher_tf`, same `params`), except this
        reads real exchange candles instead of resampling H1 ones.

        Writes ONLY the per-symbol `_symbol_htf_bias` entry -- never touches the
        shared global `strategy_profile.directional_bias` (see
        `MMS4StateManager.get_effective_directional_bias`), so concurrent
        multi-symbol cycles (`execute_multi_symbol_cycle`'s `asyncio.gather`)
        cannot race on this. A manual Hermes override (anything but the neutral
        "BOTH" default) still always wins over this automatic reading -- enforced
        in `get_effective_directional_bias`, not here.

        Disabled (the default) is a no-op: does not fetch, does not touch the
        per-symbol reading (stays at its "BOTH" default). A fetch failure logs and
        leaves the previous reading in place rather than resetting it -- a
        transient API error should not suddenly unblock a gate that was actively
        blocking counter-trend entries.

        Audit Phase 3.2-followup (2026-09-11, real bug found and fixed):
        `fetch_candles` itself NEVER raises by default -- on a real OKX outage it
        silently returns deterministic (and mildly upward-drifting) mock candles
        instead, which used to make the `except Exception` below unreachable for
        the one scenario it exists to guard: a live gate would compute
        `h4_tma_position` from fabricated data and could flip a symbol's real
        `directional_bias` on fake market structure instead of freezing the last
        known-good reading. `allow_mock_fallback=False` makes a real failure
        actually raise here, so this handler's fail-safe is no longer dead code.
        """
        if not params.htf_directional_gate_enabled:
            return
        try:
            candles_h4 = await self.executor.fetch_candles(
                symbol=symbol, timeframe="4h", limit=50, allow_mock_fallback=False
            )
        except Exception as e:
            logger.warning(f"HTF directional gate: failed to fetch H4 candles for {symbol}: {e}")
            return
        try:
            position = HigherTimeframeAnalytics.calculate_higher_tf(candles_h4, [], params)["h4_tma_position"]
        except Exception as e:
            logger.warning(f"HTF directional gate: failed to evaluate H4 TMA position for {symbol}: {e}")
            return
        bias = {"ABOVE": "LONG_ONLY", "BELOW": "SHORT_ONLY"}.get(position, "BOTH")
        self.state_manager.set_symbol_htf_bias(symbol, bias)

    def _get_inactivity_overrides(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Build the per-symbol Inactivity Manager config override dict from live
        SQLite system_settings, falling back to `configured_symbols_store`, then to
        `MMS4InactivityManager`'s own hardcoded defaults.

        Audit P6.1 (2026-09-08): previously this read ONLY
        `configured_symbols_store`, which silently loses these 3 keys on every
        restart — `load_symbols_config()` prefers the SQLite
        `symbol_configurations` table whenever it has any rows (it always does once
        a symbol exists), and that table's fixed schema has no columns for these
        fields. So the UI's own setting reset to hardcoded defaults (12 / 0.5 /
        True) behind the operator's back after any redeploy or crash-restart.
        Mirrors `_get_indicator_params`'s exact fallback chain (system_settings
        first, per-symbol-qualified, then `configured_symbols_store`).

        Returns only the keys actually overridden somewhere — an empty dict means
        "use `MMS4InactivityManager`'s own defaults", matching the previous
        behavior of not calling `set_symbol_config()` at all when nothing was
        configured.
        """
        sym_cfg: Dict[str, Any] = {}
        if symbol:
            try:
                from mms4tk.api import configured_symbols_store
                raw_cfg = configured_symbols_store.get(symbol, {})
                if isinstance(raw_cfg, dict):
                    sym_cfg = raw_cfg
            except Exception as e:
                logger.warning(f"_get_inactivity_overrides: failed to read per-symbol config for {symbol}: {e}")

        db_settings: Dict[str, str] = {}
        try:
            if self.logger_db:
                db_settings = self.logger_db.get_all_settings()
        except Exception as e:
            logger.warning(f"_get_inactivity_overrides: failed to read system_settings, using config-store defaults: {e}")

        def _raw(key: str):
            raw = db_settings.get(f"{key}:{symbol}") if symbol else None
            if raw is None:
                raw = sym_cfg.get(key)
            return raw

        overrides: Dict[str, Any] = {}
        raw = _raw("stale_candles_threshold")
        if raw is not None:
            overrides["stale_candles_threshold"] = int(float(raw))
        raw = _raw("stale_atr_threshold")
        if raw is not None:
            overrides["stale_atr_threshold"] = float(raw)
        raw = _raw("impulse_negation_enabled")
        if raw is not None:
            overrides["impulse_negation_enabled"] = (
                str(raw).strip().lower() in ("true", "1", "yes", "on") if isinstance(raw, str) else bool(raw)
            )
        return overrides

    def _check_daily_drawdown(self, target_symbol: str) -> None:
        """
        Issue #95: account-wide daily USD drawdown circuit breaker. Disabled by
        default (`daily_drawdown_limit_usd <= 0`) — zero behavior change unless
        explicitly configured via MMS4_DAILY_DRAWDOWN_LIMIT_USD.

        Checked once per symbol's cycle (cheap single-row SQL aggregate), but the
        breach itself is account-wide, not per-symbol — the existing maintenance
        lock (`is_maintenance_locked()`, already gating every symbol's entries at
        `execute_symbol_cycle`'s signal-dispatch branch) is what actually stops new
        entries once engaged. This does not close open positions and does not
        auto-clear at UTC midnight — same manual-reset expectation as
        `emergency_killall`'s existing maintenance-lock behavior.
        """
        if self.daily_drawdown_limit_usd <= 0:
            return
        if is_maintenance_locked():
            return  # already halted (by this, emergency_killall, or an exchange-outage path)

        today = datetime.now(timezone.utc).date().isoformat()
        if self._daily_drawdown_halted_on == today:
            return  # already logged + locked today, avoid re-logging every cycle

        try:
            todays_pnl = self.logger_db.get_todays_realized_pnl_usd()
        except Exception as e:
            logger.warning(f"Daily drawdown check failed to read today's PnL: {e}")
            return

        if todays_pnl <= -abs(self.daily_drawdown_limit_usd):
            set_maintenance_lock(True, reason=f"DAILY_DRAWDOWN_BREACH: ${todays_pnl:.2f}")
            self._daily_drawdown_halted_on = today
            logger.error(
                f"🛑 Daily drawdown limit breached: realized PnL today ${todays_pnl:.2f} "
                f"<= -${self.daily_drawdown_limit_usd:.2f} — maintenance mode engaged, "
                f"new entries blocked for the rest of the day."
            )
            try:
                self.logger_db.log_cycle(
                    symbol=target_symbol,
                    signal_type="NONE",
                    state=self.state_manager.get_symbol_state(target_symbol).value,
                    leverage=self.state_manager.get_symbol_leverage(target_symbol),
                    price=0.0,
                    outcome="DAILY_DRAWDOWN_HALT",
                    details=(
                        f"Daily drawdown limit breached: realized PnL today ${todays_pnl:.2f} "
                        f"<= -${self.daily_drawdown_limit_usd:.2f}. Maintenance mode engaged."
                    ),
                )
            except Exception as log_err:
                logger.warning(f"Failed to log DAILY_DRAWDOWN_HALT cycle: {log_err}")

    async def execute_symbol_cycle(self, target_symbol: str) -> Dict[str, Any]:
        """
        Executes a single market evaluation cycle for a specific trading symbol.
        Isolates state, leverage, and logging per symbol.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        self._check_daily_drawdown(target_symbol)

        # 0. Hard Guard: Check if target_symbol is active
        try:
            from mms4tk.api import configured_symbols_store
            sym_cfg = configured_symbols_store.get(target_symbol, {})
            if isinstance(sym_cfg, dict):
                if not sym_cfg.get("is_active", True):
                    logger.info(f"⏭️ Skipping cycle for paused/inactive symbol: {target_symbol}")
                    return {
                        "symbol": target_symbol,
                        "status": "SKIPPED_INACTIVE",
                        "reason": f"Symbol {target_symbol} is set to inactive (is_active=False)",
                        "timestamp": now_iso
                    }
            elif sym_cfg is False:
                logger.info(f"⏭️ Skipping cycle for paused/inactive symbol: {target_symbol}")
                return {
                    "symbol": target_symbol,
                    "status": "SKIPPED_INACTIVE",
                    "reason": f"Symbol {target_symbol} is set to inactive",
                    "timestamp": now_iso
                }
        except Exception as e:
            logger.warning(f"Error checking active status for {target_symbol}: {e}")

        # 1. Fetch candles for target symbol using symbol's configured timeframe
        sym_tf = "15m"
        try:
            from mms4tk.api import configured_symbols_store
            sym_cfg = configured_symbols_store.get(target_symbol, {})
            if isinstance(sym_cfg, dict) and sym_cfg.get("timeframe"):
                sym_tf = str(sym_cfg.get("timeframe", "15m"))
        except Exception:
            pass

        # Audit P9.5 (2026-09-08): 100 -> 150. The VR denominator is a 100-period
        # rolling ATR mean (analytics.py rolling(window=100)); fetching only exactly
        # 100 candles left just ~86 usable after indicator warmup, shifting the
        # VR_HIGH/VR_LOW (1.6/0.8) crossover point away from where it was calibrated.
        candles: List[Candle] = await self.executor.fetch_candles(target_symbol, timeframe=sym_tf, limit=150)

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

        # Pass the symbol so gating uses THIS symbol's state, not the primary symbol's,
        # and pass live SQLite-backed params so UI-configured engine_mode/adaptive_vr_*
        # actually reach signal evaluation (PLAN-008 Phase 1).
        ind_params = self._get_indicator_params(target_symbol)

        # Faza 3.2: refresh this symbol's automatic H4 directional-bias reading
        # before evaluating signals -- no-op when the gate is disabled for this
        # symbol (the default).
        await self._refresh_htf_directional_gate(target_symbol, ind_params)

        signal: SignalResult = self.signal_engine.evaluate_signals(
            candles, self.state_manager, params=ind_params, symbol=target_symbol,
            base_sl_pct=self.base_sl_pct
        )
        if target_symbol == self.symbol:
            self.last_signal = signal

        # 3. Process signal and execute trades if triggered
        outcome = "NO_ACTION"
        details = signal.reason or ""

        # Issue #80: pure instrumentation, zero behavior change, no epoch bump.
        # Measures whether the signal candle's volume relative to its own recent
        # history differs for executed vs. blocked BASE entries -- per symbol,
        # since volume means something different on each market (see the XAG
        # weekend-candle incident and the external "EMA Alerts" volume-ratio
        # filter this issue is probing). The signal candle is candles[-2] (the
        # last CLOSED candle -- analytics.evaluate_signals's own `curr_c`);
        # candles[-1] is still open. `_sig_vol_suffix` is appended verbatim to
        # `details` below, never read back or acted on.
        _sig_vol_suffix = ""
        _is_directional_bias_block = (
            signal.signal_type == "NONE" and "directional_bias" in (signal.reason or "")
        )
        if (signal.signal_type in ("BASE_SHORT", "BASE_LONG") or _is_directional_bias_block) and len(candles) >= 22:
            try:
                _sig_vol = float(candles[-2].volume)
                _avg20 = sum(float(c.volume) for c in candles[-22:-2]) / 20.0
                _vol_ratio = (_sig_vol / _avg20) if _avg20 > 0 else float("nan")
                _sig_vol_suffix = f" vol={_sig_vol:.4f} vol_ratio={_vol_ratio:.4f}"
            except Exception:
                _sig_vol_suffix = ""
        order_info: Optional[Dict[str, Any]] = None
        # Set by whichever execution branch actually places an order; read by the
        # notification block below. Previously the flip branch named it `new_side`, so the
        # notification raised NameError and every TP&FLIP alert was silently swallowed.
        order_side: str = ""

        # Resolve the allocation for this symbol's CURRENT risk state. In SCOUT_MODE the
        # size multiplier shrinks both legs while leverage stays exchange-legal.
        alloc = self.plan_allocation(target_symbol)
        notional_base_usd = alloc["notional_base_usd"]
        notional_addon_usd = alloc["notional_addon_usd"]
        if alloc["size_multiplier"] != 1.0:
            logger.info(
                f"⚠ {target_symbol} in {alloc['state']}: sizing at "
                f"{alloc['size_multiplier']*100:.0f}% of allocation, leverage x{alloc['leverage']} "
                f"→ base notional ${notional_base_usd:,.2f} "
                f"({alloc['notional_reduction_factor']:.1f}x smaller than BASE_MODE)"
            )

        base_order_amount = self.executor.calculate_order_amount(target_symbol, notional_base_usd, signal.price)
        if not isinstance(base_order_amount, (int, float)):
            base_order_amount = round(notional_base_usd / signal.price, 6) if signal.price > 0 else 0.0

        addon_order_amount = self.executor.calculate_order_amount(target_symbol, notional_addon_usd, signal.price)
        if not isinstance(addon_order_amount, (int, float)):
            addon_order_amount = round(notional_addon_usd / signal.price, 6) if signal.price > 0 else 0.0

        # Fetch live open positions from OKX to enforce Single Position & Direction Lock Guard
        live_positions = []
        try:
            live_positions = await self.executor.fetch_positions()
        except Exception as pe:
            logger.warning(f"Failed to fetch live positions for position guard check: {pe}")

        # ── Stop-loss reconciliation, before any trading decision ──
        # Detects exchange-side closes (SL fills) and transitions the state machine now,
        # so this cycle already trades under the corrected risk state.
        reconciliation_events: List[Dict[str, Any]] = []
        try:
            reconciliation_events = await self.reconcile_positions(target_symbol, live_positions, signal.price)
        except Exception as rec_err:
            logger.error(f"Reconciliation failed for {target_symbol}: {rec_err}", exc_info=True)

        # PLAN-008 Phase 2: engage a post-SL cooldown so a fresh reconciled stop-loss
        # doesn't get immediately re-entered on the very next signal.
        for _rc_evt in reconciliation_events:
            if _rc_evt.get("is_stop_loss") and len(candles) >= 2:
                cooldown_n = int(os.getenv("MMS4_SL_COOLDOWN_CANDLES", "3"))
                if cooldown_n > 0:
                    c_interval = abs(int(candles[-1].timestamp) - int(candles[-2].timestamp))
                    if c_interval > 0:
                        self._sl_cooldown_until[target_symbol] = int(candles[-1].timestamp) + (cooldown_n * c_interval)
                        logger.info(
                            f"🧊 Post-SL cooldown engaged for {target_symbol}: {cooldown_n} candles "
                            f"(until ts={self._sl_cooldown_until[target_symbol]})"
                        )

        if reconciliation_events:
            # Risk state may have just changed (BASE -> SCOUT); re-read it.
            sym_state = self.state_manager.get_symbol_state(target_symbol)
            sym_leverage = self.state_manager.get_symbol_leverage(target_symbol)
            alloc = self.plan_allocation(target_symbol)
            notional_base_usd = alloc["notional_base_usd"]
            notional_addon_usd = alloc["notional_addon_usd"]
            base_order_amount = self.executor.calculate_order_amount(
                target_symbol, notional_base_usd, signal.price
            )
            addon_order_amount = self.executor.calculate_order_amount(
                target_symbol, notional_addon_usd, signal.price
            )

        # Classify open positions for THIS symbol by their real side. The intended side is
        # only known for directional signals — deriving it from signal_type alone made a
        # NONE cycle look like a "long" intent, so an open short counted as no match and
        # the addon guard was cleared on nearly every cycle.
        own_positions: Dict[str, Dict[str, Any]] = {}
        for p in live_positions:
            if normalize_symbol(p.get("symbol", "")) != normalize_symbol(target_symbol):
                continue
            if _pos_contracts(p) <= 0:
                continue
            own_positions[p.get("side", "")] = p

        # ── Self-Healing Stop Loss Guard ──
        # Every cycle, ensure every open position for this symbol has an active conditional SL order on OKX.
        if getattr(self.executor, "has_credentials", False) and getattr(self.executor, "exchange", None) is not None and not is_maintenance_locked():
            for p_side, p in own_positions.items():
                entry_px = _pos_entry_price(p)
                if entry_px > 0 and p_side in ["long", "short"]:
                    try:
                        target_inst = p.get("info", {}).get("instId") or target_symbol
                        # P3 V2: always anchor to the pinned base_entry_price with
                        # base_sl_pct, regardless of whether this position has an
                        # addon — the addon leg never gets its own SL (see the
                        # HIGH_RISK_ADD branch), so re-attaching here must reproduce
                        # the SAME price every time, not a different one depending on
                        # which code path last computed it (that mismatch was the
                        # original determinism bug V2 fixes).
                        base_entry_px = self._base_entry_price(target_symbol, p_side, fallback=entry_px)
                        sl_pct = self.base_sl_pct
                        raw_inst_id = self.executor.to_native_inst_id(target_inst) if hasattr(self.executor, "to_native_inst_id") else target_inst
                        pending_algos = await self.executor.exchange.private_get_trade_orders_algo_pending({
                            "instId": raw_inst_id,
                            "ordType": "conditional"
                        })
                        has_active_sl = any(
                            a.get("posSide") == p_side or not a.get("posSide")
                            for a in (pending_algos.get("data", []) if isinstance(pending_algos, dict) else [])
                        )
                        if not has_active_sl:
                            logger.warning(f"🛡️ Self-Healing SL Guard: Open position {target_symbol} ({p_side}) missing SL algo. Attaching now...")
                            await self.executor.attach_stop_loss(
                                symbol=target_inst,
                                side=p_side,
                                entry_price=base_entry_px,
                                stop_loss_pct=sl_pct,
                                pos_side=p_side
                            )
                    except Exception as sl_heal_err:
                        logger.debug(f"SL self-healing check for {target_symbol}/{p_side}: {sl_heal_err}")

        # Refresh own_positions with the logic needed for signal matching
        own_positions = {}
        for p in live_positions:
            if normalize_symbol(p.get("symbol", "")) != normalize_symbol(target_symbol):
                continue
            if _pos_contracts(p) <= 0:
                continue
            side = _pos_side(p)
            if side:
                own_positions[side] = p

        # Issue #76: filter_bias (analytics.py) collapses a directionally-vetoed
        # signal to signal_type="NONE" before this function ever sees which
        # entry/exit intent it had, so cycle_logs couldn't tell a blocked fresh
        # entry apart from a blocked opposite-band take-profit on an
        # already-open position (issue #70's exact open question). Pure
        # instrumentation — appended to `details`, never acted on; the signal
        # is suppressed exactly as before.
        if signal.signal_type == "NONE" and "directional_bias" in (signal.reason or ""):
            details = f"{details} (position_open={str(bool(own_positions)).lower()})"

        if "SHORT" in signal.signal_type:
            target_pos_side = "short"
        elif "LONG" in signal.signal_type:
            target_pos_side = "long"
        else:
            target_pos_side = ""  # no directional intent this cycle

        matching_pos = own_positions.get(target_pos_side) if target_pos_side else None
        opposite_pos = None
        if target_pos_side:
            opposite_pos = own_positions.get("short" if target_pos_side == "long" else "long")

        existing_contracts = 0.0
        existing_notional = 0.0
        existing_side = ""

        ref_pos = matching_pos or opposite_pos
        if ref_pos is not None:
            existing_contracts = _pos_contracts(ref_pos)
            existing_side = _pos_side(ref_pos)
            existing_notional = _pos_notional(ref_pos, signal.price, target_symbol)

        # Addon ledger housekeeping: only drop the ledger when the symbol genuinely has no
        # open position on that side. Reconciliation already clears it on a stop-out.
        for ledger_side in ("long", "short"):
            if ledger_side not in own_positions:
                self._clear_addons(target_symbol, ledger_side)

        # ────────────────────────────────────────────────────────────────
        # STALE POSITION MANAGEMENT (MMS4 Inactivity Manager) & MIDLINE TP
        # Runs every cycle: tracks active positions, detects stagnation,
        # executes Midline partial TP (T1), and executes protective actions.
        # ────────────────────────────────────────────────────────────────
        try:
            # Compute current ATR and TMA midline
            current_atr = 0.0
            tma_mid = 0.0
            ind_params = self._get_indicator_params(target_symbol)
            closed_candles = candles[:-1] if len(candles) > 1 else candles
            if len(closed_candles) >= 20:
                tma_df_closed = TMAIndicator.calculate(closed_candles, ind_params)
                if not tma_df_closed.empty and "atr" in tma_df_closed.columns:
                    last_valid_atr = tma_df_closed["atr"].dropna()
                    if len(last_valid_atr) > 0:
                        current_atr = float(last_valid_atr.iloc[-1])
            # Audit Phase 3.1 (2026-09-11): this used to run on the raw `candles` list,
            # whose last element is the still-forming candle (every OKX/ccxt
            # fetch_ohlcv call returns it that way — the same reason `closed_candles`
            # exists two lines above for the ATR calc, and the same discipline
            # `MMS4SignalEngine.evaluate_signals`/`HigherTimeframeAnalytics.calculate_higher_tf`
            # enforce). A live-candle TMA repaints intra-candle, so the Midline TP
            # threshold could shift (and misfire against `candle_high`/`candle_low`
            # below, previously also read off the unclosed candle) before the bar
            # that produced it had even closed. Reusing `closed_candles` here matches
            # the ATR calc immediately above and the backtest engine's own causal,
            # closed-candle-only precompute of `full_df["tma"]`.
            if len(closed_candles) >= 20:
                tma_df_all = TMAIndicator.calculate(closed_candles, ind_params)
                if not tma_df_all.empty and "tma" in tma_df_all.columns:
                    last_valid_tma = tma_df_all["tma"].dropna()
                    if len(last_valid_tma) > 0:
                        tma_mid = float(last_valid_tma.iloc[-1])

            # Load per-symbol inactivity config — see _get_inactivity_overrides
            # (audit P6.1, 2026-09-08) for why this must read system_settings, not
            # just configured_symbols_store.
            try:
                inact_overrides = self._get_inactivity_overrides(target_symbol)
                if inact_overrides:
                    self.inactivity_manager.set_symbol_config(target_symbol, inact_overrides)
            except Exception:
                pass

            # Determine which position (if any) is active for this symbol.
            # Independent of the signal direction: a position must be supervised even on
            # cycles where the engine produced no signal at all.
            active_pos = matching_pos or opposite_pos or next(iter(own_positions.values()), None)
            active_side = ""
            active_entry_price = 0.0
            if active_pos is not None:
                if _pos_contracts(active_pos) > 0:
                    active_side = _pos_side(active_pos)
                    active_entry_price = _pos_entry_price(active_pos)

                    # Start tracking if not yet tracked
                    if not self.inactivity_manager.is_tracking(target_symbol) and active_entry_price > 0:
                        self.inactivity_manager.track_position(
                            inst_id=target_symbol,
                            entry_price=active_entry_price,
                            entry_time=time.time(),
                            side=active_side,
                            atr=current_atr,
                        )
            else:
                # No position open — stop tracking if was tracked
                if self.inactivity_manager.is_tracking(target_symbol):
                    self.inactivity_manager.stop_tracking(target_symbol)
                if self.logger_db:
                    try:
                        self.logger_db.clear_midline_tp(target_symbol)
                    except Exception:
                        pass

            # ────────────────────────────────────────────────────────────────
            # MIDLINE PARTIAL TAKE PROFIT (PLAN-009 T1 Netting)
            # Evaluated when an active position is open before inactivity checks.
            # ────────────────────────────────────────────────────────────────
            if active_pos is not None and _pos_contracts(active_pos) > 0 and tma_mid > 0:
                side_to_close = active_side.lower()
                existing_contracts = _pos_contracts(active_pos)
                # Same fix as tma_mid above: read the touch check off the last CLOSED
                # candle, not the still-forming one `candles[-1]` would be.
                candle_high = float(getattr(closed_candles[-1], "high", signal.price)) if closed_candles else signal.price
                candle_low = float(getattr(closed_candles[-1], "low", signal.price)) if closed_candles else signal.price

                if side_to_close == "long":
                    midline_hit = (candle_high >= tma_mid or signal.price >= tma_mid) and (active_entry_price <= tma_mid or signal.price > active_entry_price)
                else:
                    midline_hit = (candle_low <= tma_mid or signal.price <= tma_mid) and (active_entry_price >= tma_mid or signal.price < active_entry_price)

                is_tp_active = self.logger_db.is_midline_tp_active(target_symbol, side_to_close) if self.logger_db else False

                if midline_hit and not is_tp_active and ind_params.midline_partial_tp_enabled:
                    reduce_amt = self.executor.reduction_amount(existing_contracts, ind_params.midline_tp_fraction)
                    if reduce_amt is None or existing_contracts < 2:
                        logger.info(f"Micro-position {existing_contracts} contracts not divisible → T1_SKIPPED_NOT_DIVISIBLE")
                    else:
                        contracts_to_reduce = reduce_amt
                        remaining_contracts = existing_contracts - contracts_to_reduce

                        logger.info(
                            f"🎯 Midline Take-Profit Triggered for {target_symbol} ({side_to_close.upper()}): "
                            f"Reducing {contracts_to_reduce}/{existing_contracts} contracts at ${tma_mid:,.2f}"
                        )

                        # 1. Partial close on OKX
                        reduce_result = await self.executor.reduce_position(
                            symbol=target_symbol,
                            side=side_to_close,
                            reduction_pct=ind_params.midline_tp_fraction,
                            reason="MIDLINE_PARTIAL_TP"
                        )

                        # 2. Re-attach the 2.0% hard Stop-Loss anchored to base_entry_price
                        # (P3 V2 SSOT) — NOT active_entry_price/avgPx. Using avgPx here
                        # would silently reproduce the exact determinism bug V2 fixes: on
                        # an addon-carrying position this re-attach would cancel the
                        # original base-anchored algo and replace it with one at a
                        # DIFFERENT price, and reconcile_positions/the Self-Healing Guard
                        # would keep comparing against base_entry_price while OKX actually
                        # holds a different trigger (independent review, round 3).
                        try:
                            reattach_anchor = self._base_entry_price(
                                target_symbol, side_to_close, fallback=active_entry_price
                            )
                            await self.executor.attach_stop_loss(
                                symbol=target_symbol,
                                side="sell" if side_to_close == "long" else "buy",
                                entry_price=reattach_anchor,
                                stop_loss_pct=self.base_sl_pct,
                                pos_side=side_to_close,
                            )
                        except Exception as sl_err:
                            logger.error(f"Failed to re-attach SL algo after midline TP: {sl_err}")

                        # 3. Calculate realized PnL
                        pnl_pct = (tma_mid - active_entry_price) / active_entry_price if side_to_close == "long" else (active_entry_price - tma_mid) / active_entry_price
                        ct_size = self._get_contract_size(target_symbol)
                        partial_pnl = pnl_pct * active_entry_price * contracts_to_reduce * ct_size

                        # 4. Record in persistent ledger (fail-closed guard)
                        if self.logger_db:
                            self.logger_db.record_midline_tp(
                                symbol=target_symbol,
                                side=side_to_close,
                                entry_price=active_entry_price,
                                midline_price=tma_mid,
                                reduced_contracts=contracts_to_reduce,
                                remaining_contracts=remaining_contracts,
                                pnl_usd=partial_pnl,
                            )

                        # 5. Record trade in state manager with is_partial=True
                        trade_id = f"midline_tp_{target_symbol}_{side_to_close}_{int(time.time()*1000)}"
                        self.state_manager.finalize_close(
                            trade_id=trade_id,
                            symbol=target_symbol,
                            side=side_to_close,
                            reason="MIDLINE_PARTIAL_TP",
                            entry_price=active_entry_price,
                            exit_price=tma_mid,
                            amount=contracts_to_reduce,
                            pnl=partial_pnl,
                            pnl_percent=pnl_pct,
                        )

                        # 6. Emit Hermes webhook & Telegram notification
                        try:
                            from mms4tk.hermes_webhook import HermesWebhookClient
                            wh_client = HermesWebhookClient()
                            wh_client.emit_midline_tp_event(
                                symbol=target_symbol,
                                side=side_to_close,
                                entry_price=active_entry_price,
                                exit_price=tma_mid,
                                contracts_closed=contracts_to_reduce,
                                remaining_contracts=remaining_contracts,
                                pnl_usd=partial_pnl,
                                fee_usd=0.0,
                            )
                        except Exception as whe:
                            logger.warning(f"Failed to emit midline TP webhook: {whe}")

                        # 7. Update tracker state in inactivity manager
                        if self.inactivity_manager.is_tracking(target_symbol):
                            tr = self.inactivity_manager.get_tracker(target_symbol)
                            if tr:
                                tr.is_split = True
                                tr.split_at_candle = tr.candle_count

                        outcome = "MIDLINE_PARTIAL_TP"
                        details = (
                            f"Midline Take Profit (T1) executed for {target_symbol} {side_to_close.upper()}: "
                            f"closed {contracts_to_reduce} contracts at ${tma_mid:,.2f} "
                            f"(Entry=${active_entry_price:,.2f}, PnL=+${partial_pnl:.2f} USD). "
                            f"Remaining {remaining_contracts} contracts protected with 2.0% SL."
                        )
                        self.logger_db.log_cycle(
                            symbol=target_symbol,
                            signal_type="MIDLINE_PARTIAL_TP",
                            state=sym_state.value,
                            leverage=sym_leverage,
                            price=signal.price,
                            outcome=outcome,
                            details=details,
                        )
                        return {
                            "symbol": target_symbol,
                            "timestamp": now_iso,
                            "signal_type": "MIDLINE_PARTIAL_TP",
                            "outcome": outcome,
                            "state": sym_state.value,
                            "leverage": sym_leverage,
                            "order": reduce_result,
                            "reconciliation": reconciliation_events,
                        }

            # Evaluate inactivity rules (only if tracking)
            if self.inactivity_manager.is_tracking(target_symbol):
                # Detect actual new candle close via timestamp comparison
                new_candle = False
                if len(candles) >= 2:
                    # candles[-1] is the active (unclosed) candle, candles[-2] is last closed
                    last_closed_ts = int(candles[-2].timestamp) if hasattr(candles[-2].timestamp, '__int__') else int(candles[-2].timestamp)
                    prev_ts = self._last_candle_ts.get(target_symbol, 0)
                    if last_closed_ts > prev_ts:
                        new_candle = True
                        self._last_candle_ts[target_symbol] = last_closed_ts
                stale_action = self.inactivity_manager.update_and_check(
                    inst_id=target_symbol,
                    current_price=candles[-1].close if candles else signal.price,
                    current_atr=current_atr,
                    new_candle_closed=new_candle,
                )

                if stale_action != StaleAction.HOLD:
                    tracker = self.inactivity_manager.get_tracker(target_symbol)
                    t_side = tracker.side if tracker else active_side
                    t_entry = tracker.entry_price if tracker else active_entry_price
                    t_candles = tracker.candle_count if tracker else 0
                    t_is_split = tracker.is_split if tracker else False

                    # Execute protective action
                    if stale_action == StaleAction.SPLIT_50:
                        reduce_result = await self.executor.reduce_position(
                            symbol=target_symbol, side=t_side,
                            reduction_pct=0.50, reason="STALE_SPLIT_50"
                        )
                        status = (reduce_result or {}).get("status", "")
                        if status == "no_position":
                            # Nothing left on the exchange — stop supervising instead of
                            # re-issuing the same reduction every cycle.
                            self.inactivity_manager.stop_tracking(target_symbol)
                            outcome = "STALE_SPLIT_50_NOOP"
                            details = (
                                f"Inactivity Manager: no {t_side.upper()} position left on OKX for "
                                f"{target_symbol}; tracking stopped."
                            )
                        elif status == "not_divisible":
                            outcome = "STALE_SPLIT_50_HELD"
                            details = (
                                f"Inactivity Manager: {target_symbol} {t_side.upper()} cannot be halved "
                                f"({reduce_result.get('contracts')} contract(s)) — position held intact."
                            )
                        else:
                            self._book_stale_close(
                                target_symbol, t_side, t_entry, signal.price,
                                self._stale_reduced_contracts(reduce_result, active_pos, 0.50),
                                "STALE_SPLIT_50",
                            )
                            outcome = "STALE_SPLIT_50"
                            details = (
                                f"Inactivity Manager: Split 50% of {t_side.upper()} position for {target_symbol} "
                                f"after {t_candles} candles stagnation. ATR=${current_atr:,.2f}"
                            )
                            # Issue #69: ratchet the exchange-side hard SL toward the
                            # Impulse Negation level whenever it's more protective than
                            # the original base_sl_pct anchor — SPLIT_50 previously left
                            # the SL at -2% from base_entry_price regardless of how much
                            # of the position's gain a protective split (e.g. Profit
                            # Giveback Protection) was reacting to. Only ever moves the
                            # SL toward profit, never away from it (mathematically the
                            # negation level always sits between entry and the
                            # observed peak/trough, so it is always at least as
                            # protective as the fixed 2% anchor — this guard is a
                            # defensive check, not load-bearing for that property).
                            try:
                                new_level = self.inactivity_manager.compute_negation_level(
                                    target_symbol, current_atr
                                )
                                base_entry_px = self._base_entry_price(target_symbol, t_side, fallback=t_entry)
                                # Loss side is BELOW entry for a LONG, ABOVE entry for a SHORT.
                                old_sl_level = (
                                    base_entry_px * (1.0 - self.base_sl_pct) if t_side == "long"
                                    else base_entry_px * (1.0 + self.base_sl_pct)
                                )
                                more_protective = (
                                    new_level is not None and (
                                        (t_side == "long" and new_level > old_sl_level) or
                                        (t_side == "short" and new_level < old_sl_level)
                                    )
                                )
                                if more_protective:
                                    await self.executor.attach_stop_loss(
                                        symbol=target_symbol,
                                        side="sell" if t_side == "long" else "buy",
                                        entry_price=base_entry_px,
                                        stop_loss_pct=self.base_sl_pct,
                                        pos_side=t_side,
                                        trigger_price=new_level,
                                    )
                                    details += f" SL ratcheted to ${new_level:,.4f}."
                                    logger.warning(
                                        f"🔒 Ratcheted exchange SL for {target_symbol} {t_side.upper()} "
                                        f"to ${new_level:,.4f} after protective split (was ~${old_sl_level:,.4f})."
                                    )
                            except Exception as ratchet_err:
                                logger.error(f"Failed to ratchet SL after SPLIT_50 for {target_symbol}: {ratchet_err}")
                    elif stale_action in (StaleAction.FORCE_CLOSE_BE, StaleAction.BE_ESCAPE, StaleAction.FORCE_CLOSE):
                        close_requested_ms = int(time.time() * 1000) - 5000  # small clock-skew margin
                        reduce_result = await self.executor.reduce_position(
                            symbol=target_symbol, side=t_side,
                            reduction_pct=1.0, reason=stale_action.value
                        )
                        self.inactivity_manager.stop_tracking(target_symbol)
                        # The position is intentionally gone: drop its snapshot so the next
                        # cycle's reconciliation does not mistake this for a stop-out.
                        try:
                            self.logger_db.delete_position_snapshot(target_symbol, t_side)
                            self.logger_db.clear_midline_tp(target_symbol, t_side)
                        except Exception:
                            pass
                        self._clear_addons(target_symbol, t_side)
                        # Only a REAL fill can move the SCOUT clean-close counter (see
                        # SCOUT_COUNTING_REASONS); without one the close is booked as an estimate.
                        stale_fill = None
                        if stale_action.value in SCOUT_COUNTING_REASONS and self._close_succeeded(reduce_result):
                            stale_fill = await self._fetch_close_fill(target_symbol, t_side, close_requested_ms)
                        self._book_stale_close_if_executed(
                            reduce_result, target_symbol, t_side,
                            self._stale_entry_price(active_entry_price, active_side, t_side, t_entry), signal.price,
                            _pos_contracts(active_pos) if active_pos is not None else 0.0,
                            stale_action.value, fill=stale_fill,
                        )
                        outcome = stale_action.value
                        details = (
                            f"Inactivity Manager: {stale_action.value} for {target_symbol} {t_side.upper()} "
                            f"after {t_candles} candles. Entry=${t_entry:,.2f} Current=${signal.price:,.2f}"
                        )

                    # Emit Hermes webhook + Telegram
                    try:
                        from mms4tk.hermes_webhook import HermesWebhookClient
                        wh_client = HermesWebhookClient()
                        wh_client.emit_stale_position_event(
                            symbol=target_symbol,
                            action=stale_action.value,
                            candle_count=t_candles,
                            entry_price=t_entry,
                            current_price=signal.price,
                            atr=current_atr,
                            side=t_side,
                            is_split=t_is_split,
                        )
                    except Exception as whe:
                        logger.warning(f"Failed to emit stale position webhook: {whe}")

                    # Log cycle and return early — stale action takes priority
                    self.logger_db.log_cycle(
                        symbol=target_symbol,
                        signal_type=f"STALE_{stale_action.value}",
                        state=sym_state.value,
                        leverage=sym_leverage,
                        price=signal.price,
                        outcome=outcome,
                        details=details,
                    )
                    return {
                        "symbol": target_symbol,
                        "timestamp": now_iso,
                        "signal_type": f"STALE_{stale_action.value}",
                        "outcome": outcome,
                        "state": sym_state.value,
                        "leverage": sym_leverage,
                        "order": None,
                        "reconciliation": reconciliation_events,
                    }
        except Exception as inact_err:
            logger.error(f"Inactivity Manager error for {target_symbol}: {inact_err}")


        # ────────────────────────────────────────────────────────────────
        # HARD SIGNAL GATE
        # analytics.py may return a fully-formed signal_type while marking it
        # is_allowed=False (directional_bias, risk_profile, SCOUT_MODE, ambiguous bar).
        # Dispatching on signal_type alone ignored that veto entirely.
        # ────────────────────────────────────────────────────────────────
        if signal.signal_type != "NONE" and not signal.is_allowed:
            outcome = f"BLOCKED_{signal.signal_type}"
            details = f"Signal vetoed by analytics (is_allowed=False): {signal.reason}{_sig_vol_suffix}"
            logger.info(f"🚫 {target_symbol}: {details}")
            self.logger_db.log_cycle(
                symbol=target_symbol,
                signal_type=signal.signal_type,
                state=sym_state.value,
                leverage=sym_leverage,
                price=signal.price,
                outcome=outcome,
                details=details,
            )
            return {
                "symbol": target_symbol,
                "timestamp": now_iso,
                "signal_type": signal.signal_type,
                "outcome": outcome,
                "state": sym_state.value,
                "leverage": sym_leverage,
                "order": None,
                "reconciliation": reconciliation_events,
            }

        # PLAN-008 Phase 2: Candle-Close Gate + Post-SL Cooldown — computed once, applied
        # at the top of both entry-signal branches below. Reconciliation, self-healing SL
        # and the Inactivity Manager have already run for this cycle (above); this section
        # only gates NEW entries, never position supervision.
        last_closed_ts = int(candles[-2].timestamp) if len(candles) >= 2 else 0
        sl_cooldown_until = self._sl_cooldown_until.get(target_symbol, 0)
        cooldown_active = False
        if sl_cooldown_until > 0 and len(candles) >= 1:
            if int(candles[-1].timestamp) < sl_cooldown_until:
                cooldown_active = True
            else:
                del self._sl_cooldown_until[target_symbol]

        if is_maintenance_locked():
            outcome = f"SKIPPED_MAINTENANCE_MODE"
            details = f"⛔ Trading blocked: Maintenance Mode is ACTIVE. Signal {signal.signal_type} for {target_symbol} skipped."
            logger.warning(details)
        elif signal.signal_type in ["BASE_SHORT", "BASE_LONG"]:
            if cooldown_active:
                outcome = "COOLDOWN_ACTIVE"
                details = (
                    f"Post-SL cooldown active for {target_symbol} until ts={sl_cooldown_until} "
                    f"— {signal.signal_type} skipped."
                )
            elif last_closed_ts > 0 and last_closed_ts == self._last_signal_candle_ts.get(target_symbol, 0):
                outcome = f"CANDLE_GATE_SKIP_{signal.signal_type}"
                details = (
                    f"Signal {signal.signal_type} for {target_symbol} already evaluated for closed "
                    f"candle {last_closed_ts}. Skipping repeat execution this candle."
                )
            else:
                self._last_signal_candle_ts[target_symbol] = last_closed_ts
                if matching_pos is not None and existing_contracts > 0:
                    outcome = f"SKIPPED_{signal.signal_type}"
                    details = f"Base position ({target_pos_side.upper()}) already open for {target_symbol} ({existing_contracts} contracts, notional=${existing_notional:.2f} USD). Single Position Guard active."
                elif opposite_pos is not None and existing_contracts > 0:
                    # MMS4 Rule 1.4 & 8.4: Opposite TMA band hit is the natural Take Profit for the existing position.
                    # Step 1: Close opposite position completely (Take Profit / Mean-Reversion Exit)
                    side_to_close = existing_side.lower()
                    pnl_pct = 0.0
                    if active_entry_price > 0:
                        if side_to_close == "short":
                            pnl_pct = (active_entry_price - signal.price) / active_entry_price
                        else:
                            pnl_pct = (signal.price - active_entry_price) / active_entry_price

                    pnl_str = f"+{pnl_pct*100:.2f}%" if pnl_pct >= 0 else f"{pnl_pct*100:.2f}%"
                    logger.info(
                        f"🎯 MMS4 Take Profit on opposite TMA band for {target_symbol}: "
                        f"Closing {side_to_close.upper()} (Entry=${active_entry_price:,.4f}, Exit=${signal.price:,.4f}, PnL={pnl_str})"
                    )

                    try:
                        # 1. Close existing opposite position and VERIFY it actually closed.
                        close_result = await self.executor.reduce_position(
                            symbol=target_symbol,
                            side=side_to_close,
                            reduction_pct=1.0,
                            reason=f"TAKE_PROFIT_{signal.signal_type}"
                        )
                        if not self._close_succeeded(close_result):
                            close_status = (close_result or {}).get("status", "unknown")
                            # The close is not confirmed. Re-query the exchange before deciding —
                            # opening the opposite leg on top of a live position would leave two
                            # hedged positions instead of a flip.
                            if await self._position_still_open(target_symbol, side_to_close):
                                raise RuntimeError(
                                    f"Take-Profit close reported '{close_status}' and {target_symbol} "
                                    f"{side_to_close.upper()} is still open on OKX — flip aborted to "
                                    f"avoid stacking an opposing leg."
                                )
                            logger.info(
                                f"Take-Profit close for {target_symbol} returned '{close_status}' but the "
                                f"position is gone on OKX; proceeding with the flip."
                            )

                        self.inactivity_manager.stop_tracking(target_symbol)
                        self._clear_addons(target_symbol, side_to_close)
                        try:
                            self.logger_db.delete_position_snapshot(target_symbol, side_to_close)
                            self.logger_db.clear_midline_tp(target_symbol, side_to_close)
                        except Exception:
                            pass

                        # 2. Book the realized take-profit and transition state (SCOUT -> BASE)
                        try:
                            try:
                                ct_size = self._get_contract_size(target_symbol)
                                pnl_usd = pnl_pct * active_entry_price * existing_contracts * ct_size
                            except Exception as pnl_err:
                                # The PnL figure is bookkeeping only; it must never keep the
                                # take-profit from reaching finalize_close (which restores
                                # SCOUT_MODE -> BASE_MODE, the #99 invariant).
                                logger.warning(f"Could not estimate take-profit PnL for {target_symbol}: {pnl_err}")
                                pnl_usd = 0.0
                            self.state_manager.finalize_close(
                                trade_id=f"tp_{target_symbol}_{side_to_close}_{int(time.time()*1000)}",
                                symbol=target_symbol,
                                side=side_to_close,
                                reason="TP_AND_FLIP",
                                entry_price=active_entry_price,
                                exit_price=signal.price,
                                amount=existing_contracts,
                                pnl=pnl_usd,
                                pnl_percent=pnl_pct,
                            )
                        except Exception as rec_err:
                            logger.error(f"Failed to record take-profit trade for {target_symbol}: {rec_err}")
                        # Issue #99: the exchange-side close already happened (step 1 above)
                        # regardless of whether the local DB bookkeeping just above succeeded,
                        # so the SCOUT_MODE->BASE_MODE restore must fire unconditionally.
                        # `finalize_close` guarantees it for a full take-profit even when its
                        # own bookkeeping raises, so no separate hook call is needed here.

                        # 3. Emit Take Profit webhook event & Telegram
                        try:
                            from mms4tk.hermes_webhook import HermesWebhookClient
                            wh_client = HermesWebhookClient()
                            wh_client.emit_take_profit_event(
                                symbol=target_symbol,
                                closed_side=side_to_close,
                                entry_price=active_entry_price,
                                exit_price=signal.price,
                                pnl_pct=pnl_pct,
                                new_side=target_pos_side,
                                reason=f"Opposite TMA Band reached ({signal.signal_type})"
                            )
                        except Exception as whe:
                            logger.warning(f"Failed to emit take profit event for {target_symbol}: {whe}")

                        # 4. Open new position in the flipped direction
                        if base_order_amount <= 0:
                            outcome = f"TAKE_PROFIT_NO_FLIP_{signal.signal_type}"
                            details = (
                                f"Take Profit on opposite TMA Band for {target_symbol}: Closed "
                                f"{side_to_close.upper()} (PnL: {pnl_str}). New {signal.signal_type} entry "
                                f"skipped — ${notional_base_usd:.2f} notional does not fit a whole contract "
                                f"within budget."
                            )
                        else:
                            order_side = "SELL" if signal.signal_type == "BASE_SHORT" else "BUY"
                            order_info = await self.executor.execute_order(
                                symbol=target_symbol,
                                side=order_side,
                                amount=base_order_amount,
                                stop_loss_pct=self.base_sl_pct,
                                leverage=sym_leverage,
                                price=signal.price
                            )
                            outcome = f"EXECUTED_TP_AND_FLIP_{signal.signal_type}"
                            details = (
                                f"Take Profit on opposite TMA Band for {target_symbol}: Closed {side_to_close.upper()} "
                                f"({existing_contracts} contracts, Entry=${active_entry_price:,.4f}, Exit=${signal.price:,.4f}, PnL: {pnl_str}). "
                                f"Flipped to new {signal.signal_type} base position "
                                f"(${self.base_trade_usd:.2f} USD margin, SL={self.base_sl_pct*100:.2f}%)."
                            )
                    except Exception as e:
                        outcome = f"ERROR_{signal.signal_type}"
                        details = f"Failed to execute TP & Flip for {target_symbol}: {str(e)}"
                        logger.error(details, exc_info=True)
                elif base_order_amount <= 0:
                    outcome = f"SKIPPED_{signal.signal_type}"
                    details = (
                        f"{signal.signal_type} for {target_symbol} skipped: ${notional_base_usd:.2f} notional "
                        f"(margin ${self.base_trade_usd:.2f} x{sym_leverage}) does not fit a whole contract "
                        f"within the allocation budget. Risk is reduced by NOT trading, never by oversizing."
                    )
                    logger.info(f"⊘ {details}")
                else:
                    # ── Mutual Exclusion Guard (Issue #130): block a fresh same-direction base
                    # entry on a symbol whose correlated partner (e.g. ETH for BTC) already holds
                    # (or is itself mid-cycle reserving) one. Production evidence shows BTC's and
                    # ETH's entries landing ~0.1s apart under asyncio.gather -- `live_positions`
                    # alone can't see a sibling cycle's about-to-be-placed order, so this reserves
                    # the side the same way the Portfolio Margin Guard reserves notional below.
                    # Checked (and released) first so a blocked entry never consumes margin
                    # headroom. ──
                    correlated_block = await self.reserve_correlation_guard(
                        live_positions, target_symbol, target_pos_side
                    )

                    if correlated_block is not None:
                        correlated_symbol = _correlated_symbol(target_symbol)
                        outcome = f"CORRELATION_GUARD_BLOCKED_{signal.signal_type}"
                        details = (
                            f"🔗 Correlation Guard blocked {signal.signal_type} for {target_symbol}: "
                            f"{correlated_symbol} already holds (or just reserved) a "
                            f"{target_pos_side.upper()} base position "
                            f"({_pos_contracts(correlated_block)} contracts). A single market move has "
                            f"repeatedly stopped out both symbols together — see "
                            f".ai/analysis/2026-10-14-sl-loss-attribution.md §6."
                        )
                        logger.warning(details)
                    else:
                        try:
                            # ── Portfolio Margin Guard: reserve before checking, hold across the
                            # await to execute_order() so a sibling symbol cycle racing the same
                            # cap (they all run concurrently under asyncio.gather) sees this
                            # notional as already committed instead of checking against a stale
                            # snapshot. ──
                            pmg = await self.reserve_portfolio_margin(
                                live_positions, notional_base_usd, target_symbol
                            )
                            if not pmg["allowed"]:
                                outcome = f"PORTFOLIO_GUARD_BLOCKED_{signal.signal_type}"
                                details = (
                                    f"🛡️ Portfolio Margin Guard blocked {signal.signal_type} for {target_symbol}: "
                                    f"adding ${notional_base_usd:.2f} would push total from "
                                    f"${pmg['current_total_usd']:.2f} over ${pmg['cap_usd']:.2f} cap. "
                                    f"Headroom: ${pmg['headroom_usd']:.2f}"
                                )
                                logger.warning(details)
                            else:
                                order_side = "SELL" if signal.signal_type == "BASE_SHORT" else "BUY"
                                try:
                                    order_info = await self.executor.execute_order(
                                        symbol=target_symbol,
                                        side=order_side,
                                        amount=base_order_amount,
                                        stop_loss_pct=self.base_sl_pct,
                                        leverage=sym_leverage,
                                        price=signal.price
                                    )
                                    outcome = f"EXECUTED_{signal.signal_type}"
                                    details = (
                                        f"Executed {signal.signal_type} base order for {target_symbol} "
                                        f"(${self.base_trade_usd:.2f} USD margin, ${notional_base_usd:.2f} USD notional) "
                                        f"with SL={self.base_sl_pct*100:.2f}% (leverage x{sym_leverage})"
                                    )
                                except BelowMinimumNotional as e:
                                    # Not a failure: the intended size is under the venue's minimum, so
                                    # the correct action is to stand down rather than trade bigger.
                                    outcome = f"SKIPPED_{signal.signal_type}"
                                    details = f"{signal.signal_type} for {target_symbol} skipped — {e}"
                                    logger.info(f"⊘ {details}")
                                except Exception as e:
                                    outcome = f"ERROR_{signal.signal_type}"
                                    details = f"Failed to execute order for {target_symbol}: {str(e)}"
                                    logger.error(details, exc_info=True)
                                finally:
                                    # Whichever branch ran, this cycle is done deciding on this
                                    # notional. A filled order becomes real notional via next cycle's
                                    # fetch_positions(); the reservation must not outlive this cycle.
                                    await self.release_portfolio_reservation(notional_base_usd)
                        finally:
                            # Mirrors the portfolio-margin reservation's lifetime: this cycle is
                            # done deciding on this symbol's side, win or lose. A filled order
                            # becomes visible in live_positions via the next fetch_positions().
                            await self.release_correlation_reservation(target_symbol)

        elif "HIGH_RISK_ADD" in signal.signal_type:
            if cooldown_active:
                outcome = "COOLDOWN_ACTIVE"
                details = (
                    f"Post-SL cooldown active for {target_symbol} until ts={sl_cooldown_until} "
                    f"— {signal.signal_type} skipped."
                )
            elif last_closed_ts > 0 and last_closed_ts == self._last_signal_candle_ts.get(target_symbol, 0):
                outcome = f"CANDLE_GATE_SKIP_{signal.signal_type}"
                details = (
                    f"Signal {signal.signal_type} for {target_symbol} already evaluated for closed "
                    f"candle {last_closed_ts}. Skipping repeat execution this candle."
                )
            else:
                self._last_signal_candle_ts[target_symbol] = last_closed_ts
                # Single Addon Guard: an explicit, persisted per-position counter.
                # The previous heuristic ("contracts exceed base by >= 1") classified any
                # position slightly larger than one base unit as already-added and blocked
                # legitimate first addons, while the in-memory lock was lost on restart.
                match_entry = _pos_entry_price(matching_pos) if matching_pos is not None else 0.0
                addons_done = (
                    self._addon_count(target_symbol, target_pos_side, match_entry)
                    if matching_pos is not None else 0
                )
                # Hard cap on total committed margin, independent of the counter.
                margin_cap_usd = alloc["notional_cap_usd"]
                would_exceed_cap = (existing_notional + notional_addon_usd) > margin_cap_usd * (1.0 + 0.10)

                if sym_state != MMS4State.BASE_MODE:
                    outcome = f"BLOCKED_{signal.signal_type}"
                    details = f"High Risk Add blocked for {target_symbol} in state {sym_state.value}"
                elif matching_pos is None or existing_contracts <= 0:
                    outcome = f"BLOCKED_{signal.signal_type}"
                    details = (
                        f"High Risk Add {signal.signal_type} blocked for {target_symbol} because no matching "
                        f"{target_pos_side.upper()} base position exists "
                        f"(Active: {existing_side.upper() if existing_side else 'NONE'})."
                    )
                elif addons_done >= 1:
                    outcome = f"SKIPPED_{signal.signal_type}"
                    details = (
                        f"High Risk Add already executed {addons_done}x on this {target_pos_side.upper()} "
                        f"position for {target_symbol} (entry=${match_entry:,.4f}). Single Addon Guard active."
                    )
                elif would_exceed_cap:
                    outcome = f"SKIPPED_{signal.signal_type}"
                    details = (
                        f"High Risk Add for {target_symbol} skipped: existing ${existing_notional:.2f} + "
                        f"addon ${notional_addon_usd:.2f} would exceed the ${margin_cap_usd:.2f} notional cap."
                    )
                elif addon_order_amount <= 0:
                    outcome = f"SKIPPED_{signal.signal_type}"
                    details = (
                        f"High Risk Add for {target_symbol} skipped: ${notional_addon_usd:.2f} notional does "
                        f"not fit a whole contract within budget."
                    )
                else:
                    # ── Portfolio Margin Guard: reserve + block addon if global cap would
                    # be exceeded (see reserve_portfolio_margin for the race it closes). ──
                    pmg = await self.reserve_portfolio_margin(
                        live_positions, notional_addon_usd, target_symbol
                    )
                    if not pmg["allowed"]:
                        outcome = f"PORTFOLIO_GUARD_BLOCKED_{signal.signal_type}"
                        details = (
                            f"🛡️ Portfolio Margin Guard blocked High Risk Add for {target_symbol}: "
                            f"adding ${notional_addon_usd:.2f} would push total from "
                            f"${pmg['current_total_usd']:.2f} over ${pmg['cap_usd']:.2f} cap."
                        )
                        logger.warning(details)
                    else:
                        order_side = "SELL" if "SHORT" in signal.signal_type else "BUY"
                        try:
                            # P3 V2: the addon leg gets NO independent SL. OKX's
                            # existing algo order for this position uses
                            # closeFraction="1" (the whole position, whatever its
                            # current size), so leaving the original base-anchored SL
                            # untouched already protects the addon's contracts too —
                            # attaching a second one here would just cancel and
                            # replace it with a DIFFERENT anchor (the addon's own fill
                            # price), which is exactly the non-determinism V2 removes.
                            order_info = await self.executor.execute_order(
                                symbol=target_symbol,
                                side=order_side,
                                amount=addon_order_amount,
                                stop_loss_pct=0.0,
                                leverage=sym_leverage,
                                price=signal.price
                            )
                            self._record_addon(target_symbol, target_pos_side, match_entry)
                            outcome = f"EXECUTED_{signal.signal_type}"
                            details = (
                                f"Executed High Risk Add order for {target_symbol} "
                                f"(${self.addon_trade_usd:.2f} USD margin, ${notional_addon_usd:.2f} USD notional) "
                                f"— protected by the existing base SL (base_sl_pct={self.base_sl_pct*100:.2f}% "
                                f"from base_entry_price, unchanged, leverage x{sym_leverage})"
                            )
                        except BelowMinimumNotional as e:
                            outcome = f"SKIPPED_{signal.signal_type}"
                            details = f"High Risk Add for {target_symbol} skipped — {e}"
                            logger.info(f"⊘ {details}")
                        except Exception as e:
                            outcome = f"ERROR_{signal.signal_type}"
                            details = f"Failed to execute High Risk Add for {target_symbol}: {str(e)}"
                            logger.error(details, exc_info=True)
                        finally:
                            await self.release_portfolio_reservation(notional_addon_usd)

        # 4. Log cycle outcome for specific symbol
        # Issue #80: append the signal-candle volume instrumentation computed above
        # to whichever `details` string this cycle's branch produced (EXECUTED_BASE_*,
        # SKIPPED_*, COOLDOWN_ACTIVE, CANDLE_GATE_SKIP, EXECUTED_TP_AND_FLIP_*,
        # PORTFOLIO_GUARD_BLOCKED_*, ERROR_*) -- every one of them re-assigns `details`
        # from scratch rather than appending, so this has to happen here, once, right
        # before the row is written. Empty string (non-BASE signal, or too few candles)
        # is a no-op suffix. Never changes `outcome` or any trading decision.
        if _sig_vol_suffix and _sig_vol_suffix not in details:
            details = f"{details}{_sig_vol_suffix}"
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
                    f"- Strona: `{order_side}`\n"
                    f"- Cena wejścia: `${signal.price:,.2f}`\n"
                    f"- Dźwignia: `{sym_leverage}x`\n"
                    # P3 V2: the addon leg never gets its own SL — the position stays
                    # protected by the base SL (base_entry_price, unchanged) either way.
                    f"- Stop-Loss: `{self.base_sl_pct*100:.2f}%`"
                    f"{' (od ceny bazy, bez zmian)' if 'BASE' not in signal.signal_type else ''}\n"
                    f"- Stan strategii: `{sym_state.value}`"
                )
                # Faza 3.2 gate visibility (audit fix 2026-09-13): note in the
                # notification itself when this entry was taken while the H4
                # directional gate or a manual Hermes override was actively
                # restricting direction. "BOTH" (the normal, unrestricted case)
                # adds nothing here — zero extra noise on the common path.
                effective_bias = self.state_manager.get_effective_directional_bias(target_symbol)
                if effective_bias != "BOTH":
                    body += f"\n🔒 Otwarto pod aktywną bramką kierunkową: {effective_bias}"
                wh_client.emit_event(HermesWebhookEvent(
                    event_type="POSITION_OPENED",
                    title=title,
                    body=body,
                    initial_status="in_progress",
                    priority=4
                ))
                HermesWebhookClient.send_telegram(
                    title=title,
                    body=body,
                    priority=3,
                    dedupe_key=f"OPEN_{clean_sym}_{signal.signal_type}"
                )
                logger.info(f"Emitted Telegram webhook notification for position opening: {target_symbol}")
            except Exception as whe:
                logger.warning(
                    f"Failed to emit position opening webhook notification: {whe}", exc_info=True
                )

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
            "order": order_info,
            "reconciliation": reconciliation_events,
        }

    @staticmethod
    def _close_succeeded(result: Optional[Dict[str, Any]]) -> bool:
        """
        Did reduce_position actually place a closing order?

        An accepted market order carries an exchange id (or a terminal status). The
        sentinel statuses mean nothing was sent — 'no_position' in particular used to be
        ignored, letting the flip open an opposing leg while the original stayed open.
        """
        if not result:
            return False
        status = str(result.get("status", "")).lower()
        if status in ("no_position", "not_divisible", "error", "rejected"):
            return False
        return bool(result.get("id")) or status in ("executed", "closed", "filled", "live")

    async def _position_still_open(self, symbol: str, side: str) -> bool:
        """Re-query OKX to confirm a position really closed before acting on that assumption."""
        try:
            positions = await self.executor.fetch_positions()
        except Exception as e:
            # Unknown state — treat as "still open" so the caller aborts rather than
            # stacking an opposing position on top of one that may still exist.
            logger.warning(f"Could not verify position closure for {symbol}/{side}: {e}")
            return True
        for p in positions:
            if normalize_symbol(p.get("symbol", "")) != normalize_symbol(symbol):
                continue
            if _pos_side(p) == side.lower() and _pos_contracts(p) > 0:
                return True
        return False

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

    def _check_stale_directional_bias_alert(self) -> None:
        """Faza 6 C.3.3: warn via Telegram when the GLOBAL manual
        `directional_bias` override (`strategy_profile.directional_bias`) has
        been stuck at something other than "BOTH" for longer than a
        configurable threshold, without any new `strategy_profile` write —
        the audit finding this covers: three real production gaps in the H4
        cron (up to 16h) left a non-"BOTH" bias silently frozen the whole
        time, with zero alerting.

        Threshold is read from `system_settings` key
        `stale_directional_bias_alert_hours` (same get_setting/float-parse
        pattern as `total_max_portfolio_margin_usd` in __init__ above),
        default 12.0h. Deliberately synchronous and exception-swallowing at
        the call site (see run_loop) — this is pure observability, must never
        interrupt or slow down the trading cycle it runs alongside.
        """
        bias = self.state_manager.strategy_profile.directional_bias
        if bias == "BOTH":
            return

        threshold_hours = 12.0
        raw_threshold = self.logger_db.get_setting("stale_directional_bias_alert_hours")
        if isinstance(raw_threshold, str):
            try:
                threshold_hours = float(raw_threshold)
            except (TypeError, ValueError):
                logger.warning(f"Ignoring invalid persisted stale_directional_bias_alert_hours={raw_threshold!r}")

        last_profile = self.logger_db.get_latest_strategy_profile()
        if not last_profile or not last_profile.get("timestamp"):
            return

        try:
            ts_raw = str(last_profile["timestamp"]).replace("Z", "+00:00")
            ts = datetime.fromisoformat(ts_raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception as e:
            logger.warning(f"stale_directional_bias check: failed to parse strategy_profile timestamp: {e}")
            return

        age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
        if age_hours < threshold_hours:
            return

        from mms4tk.hermes_webhook import HermesWebhookClient
        HermesWebhookClient.send_telegram(
            title=f"⚠️ directional_bias={bias} zamrożony od {age_hours:.1f}h",
            body=(
                f"Globalny manualny `directional_bias` = `{bias}` (tłumi wewnętrzną "
                f"bramkę H4) bez nowego zapisu `strategy_profile` od {age_hours:.1f}h "
                f"(próg: {threshold_hours:.1f}h).\n\n"
                f"Ostatni powód: {last_profile.get('reason', 'N/A')}\n"
                f"Ostatni zapis: {last_profile['timestamp']}\n\n"
                f"Ustaw \"BOTH\" przez POST /api/v1/strategy/profile, żeby przywrócić automatykę."
            ),
            priority=6,
            # Faza 6 C.3.3: dedupe key includes the frozen write's own timestamp, so a
            # NEW strategy_profile write (which changes this timestamp) always produces
            # a fresh key -- no manual reset needed. ttl_override keeps this from firing
            # every ~15s cycle while still stale, without suppressing it forever.
            dedupe_key=f"STALE_DIRECTIONAL_BIAS_{bias}_{last_profile['timestamp']}",
            ttl_override=3600.0,
        )

    def _check_scout_stale_alert(self) -> None:
        """Telegram alert for every symbol that has been in SCOUT_MODE longer than
        SCOUT_STALE_ALERT_HOURS, repeated each SCOUT_STALE_ALERT_HOURS while it stays there.

        The time of the last alert is persisted (`scout_stale_alerted:{symbol}`, cleared
        when the symbol leaves SCOUT) so a restart neither re-fires nor loses the cadence.
        Pure observability: a failure for one symbol is logged and never blocks the others
        or the trading loop.
        """
        now = datetime.now(timezone.utc)
        for symbol in self.state_manager.scout_symbols():
            try:
                self._scout_stale_alert_for(symbol, now)
            except Exception as e:
                logger.error(f"SCOUT_STALE check failed for {symbol}: {type(e).__name__}: {e}")

    def _scout_stale_alert_for(self, symbol: str, now: datetime) -> None:
        from mms4tk.state_manager import (
            SCOUT_EXIT_K, SCOUT_OVERRIDE_MIN_HOURS, SCOUT_STALE_ALERT_HOURS, _SCOUT_STALE_KEY_PREFIX,
        )
        since = self.state_manager.get_scout_since(symbol)
        if since is None:
            return
        age_hours = (now - since).total_seconds() / 3600.0
        if age_hours < SCOUT_STALE_ALERT_HOURS:
            return
        key = f"{_SCOUT_STALE_KEY_PREFIX}{symbol}"
        raw_last = self.logger_db.get_setting(key)
        if isinstance(raw_last, str):
            try:
                last = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() / 3600.0 < SCOUT_STALE_ALERT_HOURS:
                    return
            except ValueError:
                logger.warning(f"Ignoring invalid persisted {key}={raw_last!r}")

        if age_hours < SCOUT_OVERRIDE_MIN_HOURS:
            # Issue #129 follow-up: scout_override is still on cooldown at this point if
            # SCOUT_STALE_ALERT_HOURS (default 24h) fires before SCOUT_OVERRIDE_MIN_HOURS
            # (default 48h) -- calling it now would silently no-op (response echoes
            # scout_override=False). Say so explicitly instead of pointing at a call that fails.
            remaining = SCOUT_OVERRIDE_MIN_HOURS - age_hours
            override_line = (
                f"⛔ Ręczne zwolnienie (scout_override) jeszcze ZABLOKOWANE przez cooldown "
                f"({remaining:.1f}h do odblokowania, próg {SCOUT_OVERRIDE_MIN_HOURS:.0f}h) — "
                f"wywołanie teraz po cichu nic nie zrobi (odpowiedź: scout_override=false)."
            )
        else:
            override_line = (
                f"Zwolnij ręcznie: POST /api/v1/strategy/profile "
                f"{{\"scout_override\": true, \"symbol\": \"{symbol}\"}}"
            )

        from mms4tk.hermes_webhook import HermesWebhookClient
        sent = HermesWebhookClient.send_telegram(
            title=f"⚠️ SCOUT_STALE {symbol}: {age_hours:.0f}h w SCOUT_MODE",
            body=(
                f"`{symbol}` jest w SCOUT_MODE od {since.isoformat()} ({age_hours:.1f}h, "
                f"próg: {SCOUT_STALE_ALERT_HOURS:.0f}h).\n"
                f"Czyste zamknięcia: {self.state_manager.get_scout_ok(symbol)}/{SCOUT_EXIT_K}.\n\n"
                f"{override_line} albo poczekaj na "
                f"{SCOUT_EXIT_K} czyste zamknięcia. Alert powtórzy się za "
                f"{SCOUT_STALE_ALERT_HOURS:.0f}h, jeśli SCOUT trwa."
            ),
            priority=6,
            dedupe_key=f"SCOUT_STALE_{symbol}",
            ttl_override=3600.0,
        )
        if sent:
            self.logger_db.set_setting(key, now.isoformat(), updated_by="daemon")

    async def run_loop(self) -> None:
        """Background loop executing multi-symbol cycles at configured intervals."""
        while self._is_running:
            try:
                from mms4tk.api import configured_symbols_store
                symbols = [s for s, cfg in configured_symbols_store.items() if (isinstance(cfg, dict) and cfg.get("is_active")) or cfg is True]
            except Exception:
                symbols = [self.symbol]
            try:
                await self.execute_multi_symbol_cycle(symbols)
            except Exception as e:
                logger.error(f"Error during multi-symbol daemon loop: {type(e).__name__}: {str(e)}")

            try:
                self._check_stale_directional_bias_alert()
            except Exception as e:
                logger.error(f"Error during stale directional_bias check: {type(e).__name__}: {str(e)}")

            try:
                self._check_scout_stale_alert()
            except Exception as e:
                logger.error(f"Error during SCOUT_STALE check: {type(e).__name__}: {str(e)}")

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def get_status(self) -> Dict[str, Any]:
        """Return current status dictionary of the daemon."""
        try:
            from mms4tk.api import configured_symbols_store
            active_syms = [s for s, cfg in configured_symbols_store.items() if (isinstance(cfg, dict) and cfg.get("is_active")) or cfg is True]
        except Exception:
            active_syms = [self.symbol]
        db_settings = {}
        try:
            if hasattr(self, "logger_db") and self.logger_db:
                db_settings = self.logger_db.get_all_settings()
        except Exception:
            db_settings = {}

        return {
            "is_running": self._is_running,
            "maintenance_mode": is_maintenance_locked(),
            "interval_seconds": self.interval_seconds,
            "symbol": self.symbol,
            "active_symbols": active_syms,
            "max_margin_usd": self.max_margin_usd,
            "base_trade_usd": self.base_trade_usd,
            "addon_trade_usd": self.addon_trade_usd,
            "trade_amount_usd": self.trade_amount_usd,
            "last_cycle_timestamp": self.last_cycle_timestamp,
            "processed_cycles": self.processed_cycles,
            "inactivity_manager": self.inactivity_manager.get_status(),
            "addon_ledger": self._safe_addon_ledger(),
            "base_allocation": self.base_allocation,
            "addon_allocation": self.addon_allocation,
            "allocation_plan": {sym: self.plan_allocation(sym) for sym in active_syms},
            "base_sl_pct": self.base_sl_pct,
            "addon_sl_pct": self.addon_sl_pct,
            "total_max_portfolio_margin_usd": self.total_max_portfolio_margin_usd,
            "portfolio_margin_reserved_usd": round(self._reserved_notional_usd, 2),
            "engine_mode": db_settings.get("engine_mode", os.getenv("MMS4_ENGINE_MODE", "CLASSIC")),
            "adaptive_vr_high": float(db_settings.get("adaptive_vr_high", os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6"))),
            "adaptive_vr_low": float(db_settings.get("adaptive_vr_low", os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8"))),
            "time_lock_start": db_settings.get("time_lock_start", os.getenv("MMS4_TIME_LOCK_START", "13:30")),
            "time_lock_end": db_settings.get("time_lock_end", os.getenv("MMS4_TIME_LOCK_END", "14:30")),
        }

    def _safe_addon_ledger(self) -> List[Dict[str, Any]]:
        """Persisted addon counters, for dashboard introspection."""
        try:
            return self.logger_db.get_all_addon_state()
        except Exception:
            return []


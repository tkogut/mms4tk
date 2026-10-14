"""Issue #130: a Mutual Exclusion Guard blocking a fresh same-direction base entry on a symbol
whose correlated partner (BTC/ETH by default) already holds one.

Production evidence (.ai/analysis/2026-10-14-sl-loss-attribution.md §6): three strictly-
simultaneous (<=10 minutes apart, same side) BTC+ETH stop-loss pairs account for 23% of all
realized SL loss from the 30 production STOP-LOSS closes — a single market impulse repeatedly
stopped out both symbols independently, with entry timestamps on 2026-09-18 ~0.1s apart (the
same asyncio.gather cycle-batch), which is exactly what reserve_correlation_guard's lock exists
to catch: a plain live_positions read-check never sees a sibling cycle's about-to-be-placed order.
"""
import asyncio
import importlib

import pytest

import mms4tk.daemon as daemon_mod
from mms4tk.daemon import MMS4Daemon, check_correlation_guard


def _pos(symbol: str, side: str, contracts: float = 1.0) -> dict:
    return {"symbol": symbol, "side": side, "contracts": contracts}


def test_defaults_to_btc_eth_pair():
    assert daemon_mod._correlated_symbol("BTC/USDC") == "ETH"
    assert daemon_mod._correlated_symbol("ETH/USDC") == "BTC"


def test_uninvolved_symbol_has_no_correlated_partner():
    assert daemon_mod._correlated_symbol("XAU/USDC") is None
    assert daemon_mod._correlated_symbol("SOL/USDC") is None


def test_blocks_same_side_correlated_entry():
    live_positions = [_pos("BTC-USD_UM_XPERP-310404", "long")]
    blocked = check_correlation_guard(live_positions, "ETH/USDC", "long")
    assert blocked is not None
    assert blocked["symbol"] == "BTC-USD_UM_XPERP-310404"


def test_allows_opposite_side_correlated_position():
    live_positions = [_pos("BTC-USD_UM_XPERP-310404", "short")]
    assert check_correlation_guard(live_positions, "ETH/USDC", "long") is None


def test_allows_when_correlated_symbol_is_flat():
    live_positions = [_pos("BTC-USD_UM_XPERP-310404", "long", contracts=0.0)]
    assert check_correlation_guard(live_positions, "ETH/USDC", "long") is None
    assert check_correlation_guard([], "ETH/USDC", "long") is None


def test_unaffected_pairs_never_blocked():
    """XAU/XAG showed no comparable correlated-SL pattern in the analysis — not in
    CORRELATED_PAIRS by default, so a shared-direction position never blocks them."""
    live_positions = [_pos("XAU-USD_UM_XPERP-310502", "long")]
    assert check_correlation_guard(live_positions, "XAG/USDC", "long") is None


def test_no_directional_intent_is_never_blocked():
    live_positions = [_pos("BTC-USD_UM_XPERP-310404", "long")]
    assert check_correlation_guard(live_positions, "ETH/USDC", "") is None


def test_env_can_extend_pairs(monkeypatch):
    monkeypatch.setenv("MMS4_CORRELATED_PAIRS", "BTC/USDC:ETH/USDC,XAU/USDC:XAG/USDC")
    importlib.reload(daemon_mod)
    try:
        live_positions = [{"symbol": "XAU-USD_UM_XPERP-310502", "side": "short", "contracts": 1.0}]
        blocked = daemon_mod.check_correlation_guard(live_positions, "XAG/USDC", "short")
        assert blocked is not None
    finally:
        monkeypatch.delenv("MMS4_CORRELATED_PAIRS", raising=False)
        importlib.reload(daemon_mod)


def test_malformed_env_pair_is_skipped_not_fatal(monkeypatch, caplog):
    monkeypatch.setenv("MMS4_CORRELATED_PAIRS", "BTC/USDC-ETH/USDC")  # missing ':' separator
    importlib.reload(daemon_mod)
    try:
        assert daemon_mod._correlated_symbol("BTC/USDC") is None
    finally:
        monkeypatch.delenv("MMS4_CORRELATED_PAIRS", raising=False)
        importlib.reload(daemon_mod)


def test_reserve_correlation_guard_closes_the_same_cycle_race():
    """Documents the fix: two concurrent same-side entries on a correlated pair, racing the
    SAME live_positions snapshot (neither symbol has landed an order yet) -- only one may
    reserve. This is exactly the 2026-09-18 production scenario (ETH and BTC entries ~0.1s
    apart, same asyncio.gather batch), which check_correlation_guard() ALONE cannot catch."""
    async def _async_test():
        daemon = MMS4Daemon()
        live_positions = []  # neither symbol has an order on the exchange yet

        async def attempt(symbol):
            await asyncio.sleep(0)  # force real interleaving under asyncio.gather
            return await daemon.reserve_correlation_guard(live_positions, symbol, "short")

        btc_result, eth_result = await asyncio.gather(attempt("BTC/USDC"), attempt("ETH/USDC"))
        allowed = [r for r in (btc_result, eth_result) if r is None]
        assert len(allowed) == 1, "a plain read-check would have allowed both"

        # Releasing the reservation frees it for the next cycle.
        await daemon.release_correlation_reservation("BTC/USDC")
        await daemon.release_correlation_reservation("ETH/USDC")
        assert daemon._pending_base_entries == {}
        post_release = await daemon.reserve_correlation_guard(live_positions, "ETH/USDC", "short")
        assert post_release is None

    asyncio.run(_async_test())


def test_reserve_correlation_guard_allows_opposite_sides_concurrently():
    async def _async_test():
        daemon = MMS4Daemon()
        live_positions = []
        btc_result, eth_result = await asyncio.gather(
            daemon.reserve_correlation_guard(live_positions, "BTC/USDC", "long"),
            daemon.reserve_correlation_guard(live_positions, "ETH/USDC", "short"),
        )
        assert btc_result is None and eth_result is None

    asyncio.run(_async_test())

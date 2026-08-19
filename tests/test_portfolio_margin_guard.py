import asyncio

from mms4tk.daemon import MMS4Daemon

def test_portfolio_margin_guard_disabled_by_default():
    daemon = MMS4Daemon()
    daemon.total_max_portfolio_margin_usd = 0.0  # disabled
    
    live_positions = [
        {"symbol": "BTC/USD", "contracts": 1.0, "notional": 100.0}
    ]
    res = daemon.check_portfolio_margin_guard(live_positions, new_notional_usd=50.0, target_symbol="ETH/USDC")
    assert res["allowed"] is True
    assert res["cap_usd"] == 0.0

def test_portfolio_margin_guard_allows_under_cap():
    daemon = MMS4Daemon()
    daemon.total_max_portfolio_margin_usd = 100.0  # cap $100
    
    live_positions = [
        {"symbol": "BTC/USD", "contracts": 1.0, "notional": 40.0},
        {"symbol": "SOL/USD", "contracts": 1.0, "notional": 30.0},
    ]
    # Current notional = $70. New notional = $20. Total = $90 <= $100 -> ALLOWED
    res = daemon.check_portfolio_margin_guard(live_positions, new_notional_usd=20.0, target_symbol="ETH/USDC")
    assert res["allowed"] is True
    assert res["current_total_usd"] == 70.0
    assert res["headroom_usd"] == 30.0

def test_portfolio_margin_guard_blocks_over_cap():
    daemon = MMS4Daemon()
    daemon.total_max_portfolio_margin_usd = 100.0  # cap $100
    
    live_positions = [
        {"symbol": "BTC/USD", "contracts": 1.0, "notional": 50.0},
        {"symbol": "SOL/USD", "contracts": 1.0, "notional": 40.0},
    ]
    # Current notional = $90. New notional = $30. Total = $120 > $105 (with 5% tol) -> BLOCKED
    res = daemon.check_portfolio_margin_guard(live_positions, new_notional_usd=30.0, target_symbol="ETH/USDC")
    assert res["allowed"] is False
    assert res["current_total_usd"] == 90.0
    assert res["headroom_usd"] == 10.0

def test_raw_check_is_racy_against_concurrent_callers():
    """
    Documents the bug: check_portfolio_margin_guard alone has no memory of other
    in-flight callers, so N concurrent symbol cycles each checking against the same
    live_positions snapshot can all pass even though only one should fit under the cap.
    """
    daemon = MMS4Daemon()
    daemon.total_max_portfolio_margin_usd = 100.0  # cap $100, tolerance -> $105

    live_positions = [{"symbol": "BTC/USD", "contracts": 1.0, "notional": 70.0}]

    # Five sibling symbols each independently ask "can I add $30?" against the SAME
    # stale snapshot (current=$70). 70+30=100 <= 105, so every single one says yes.
    results = [
        daemon.check_portfolio_margin_guard(live_positions, new_notional_usd=30.0, target_symbol=f"SYM{i}")
        for i in range(5)
    ]
    assert all(r["allowed"] for r in results), "raw check is expected to be fooled by a stale snapshot"
    # Had all five actually been allowed to open, the real portfolio would sit at
    # 70 + 5*30 = 220 — more than double the $105 tolerant cap.


def test_reserve_portfolio_margin_serializes_concurrent_cycles():
    """
    reserve_portfolio_margin must close the race: of N concurrent reservation attempts
    against the same cap, only as many may succeed as actually fit — regardless of
    execution order under asyncio.gather.
    """
    async def _async_test():
        daemon = MMS4Daemon()
        daemon.total_max_portfolio_margin_usd = 100.0  # cap $100, tolerance -> $105

        live_positions = [{"symbol": "BTC/USD", "contracts": 1.0, "notional": 70.0}]

        async def attempt(i):
            # Force a real yield so asyncio actually interleaves these coroutines
            # instead of running one to completion before starting the next.
            await asyncio.sleep(0)
            return await daemon.reserve_portfolio_margin(
                live_positions, new_notional_usd=30.0, target_symbol=f"SYM{i}"
            )

        results = await asyncio.gather(*[attempt(i) for i in range(5)])
        allowed = [r for r in results if r["allowed"]]

        # current $70 + n*$30 <= $105 (5% tolerance) is only satisfiable for n == 1.
        assert len(allowed) == 1
        assert daemon._reserved_notional_usd == 30.0

        # Releasing the reservation (as execute_symbol_cycle does once the order
        # attempt finishes) frees capacity for the next cycle.
        await daemon.release_portfolio_reservation(30.0)
        assert daemon._reserved_notional_usd == 0.0

        post_release = await daemon.reserve_portfolio_margin(
            live_positions, new_notional_usd=30.0, target_symbol="SYM_NEXT_CYCLE"
        )
        assert post_release["allowed"] is True

    asyncio.run(_async_test())


if __name__ == "__main__":
    test_portfolio_margin_guard_disabled_by_default()
    test_portfolio_margin_guard_allows_under_cap()
    test_portfolio_margin_guard_blocks_over_cap()
    test_raw_check_is_racy_against_concurrent_callers()
    test_reserve_portfolio_margin_serializes_concurrent_cycles()
    print("All Portfolio Margin Guard tests passed successfully!")

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

if __name__ == "__main__":
    test_portfolio_margin_guard_disabled_by_default()
    test_portfolio_margin_guard_allows_under_cap()
    test_portfolio_margin_guard_blocks_over_cap()
    print("All Portfolio Margin Guard tests passed successfully!")

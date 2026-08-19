import pytest
import asyncio
from mms4tk.api import (
    app,
    get_health,
    get_state,
    get_pnl,
    calculate_analytics,
    emergency_killall,
    emergency_reverse,
    high_risk_add,
    CalculateRequest,
    HighRiskAddRequest,
    state_manager
)
from mms4tk.analytics import Candle, IndicatorParams
from mms4tk.hermes_tools import HermesToolGenerator, get_hermes_tools


def test_hermes_tool_schemas_validity():
    """Verify Hermes function tool schemas are generated properly."""
    tools = get_hermes_tools()
    assert len(tools) == 12

    names = {tool["function"]["name"] for tool in tools}
    expected_names = {
        "get_symbols_status",
        "get_state",
        "get_balance",
        "get_pnl",
        "calculate_analytics",
        "emergency_killall",
        "emergency_reverse",
        "high_risk_add",
        "mms4_get_higher_tf",
        "mms4_set_strategy_profile",
        "mms4_get_pending_events",
        "mms4_hitl_resolve",
    }
    assert names == expected_names


    # Check individual tool schema getter
    state_tool = HermesToolGenerator.get_tool_by_name("get_state")
    assert state_tool["type"] == "function"
    assert state_tool["function"]["name"] == "get_state"


def test_hermes_endpoints_integration():
    """Verify endpoint handlers return compliant responses for Hermes agent tools."""

    async def _run_integration():
        # 1. Health check
        health_res = await get_health()
        assert health_res["status"] == "ok"
        assert "version" in health_res

        # 2. State tool
        state_res = await get_state()
        assert "state" in state_res
        assert "leverage" in state_res
        assert "current_equity" in state_res
        assert "realized_pnl" in state_res
        assert "total_trades" in state_res

        # 3. PnL tool
        pnl_res = await get_pnl()
        assert "daily_roe" in pnl_res
        assert "realized_pnl" in pnl_res
        assert "unrealized_pnl" in pnl_res
        assert "total_trades" in pnl_res
        assert "win_rate" in pnl_res

        # 4. Analytics calculation tool
        candles = [
            Candle(timestamp=i * 60, open=100.0, high=102.0, low=98.0, close=100.0, volume=100.0)
            for i in range(15)
        ]
        calc_req = CalculateRequest(candles=candles, params=IndicatorParams(tma_length=10, atr_period=5, atr_mult=2.0))
        calc_res = await calculate_analytics(calc_req)
        assert "tma" in calc_res
        assert "atr" in calc_res
        assert "upper_band" in calc_res
        assert "lower_band" in calc_res
        assert "signal" in calc_res
        assert calc_res["signal"].signal_type == "NONE"

        # 5. Emergency killall tool
        kill_res = await emergency_killall()
        assert kill_res["status"] == "success"
        assert "message" in kill_res
        assert "action_taken" in kill_res

        # 6. Emergency reverse tool
        rev_res = await emergency_reverse()
        assert rev_res["status"] == "success"
        assert "message" in rev_res
        assert "action_taken" in rev_res

        # 7. High risk add tool
        hra_req = HighRiskAddRequest(side="LONG", size=1.5, stop_loss_pct=0.01)
        hra_res = await high_risk_add(hra_req)
        assert hra_res["status"] in ("success", "rejected")
        assert "executed" in hra_res
        assert "reason" in hra_res
        assert "details" in hra_res

    asyncio.run(_run_integration())

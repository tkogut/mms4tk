"""
Hermes Tools Module (mms4tk.hermes_tools)

Provides JSON schemas (OpenAPI/OpenAI function format) for Hermes agent (Nous Research)
function calling interface to interact with mms4tk FastAPI endpoints.

Faza 6 C.2 (2026-09-13): `emergency_reverse` was removed from the schemas this
module returns to the LLM. The underlying `POST /api/v1/emergency/reverse`
endpoint is untouched (still returns HTTP 501, fail-closed) — this change
only stops Hermes from being offered a tool that can never actually act, so
it isn't tempted to try it during a real incident. Use `emergency_killall` to
flatten exposure instead.
"""

from typing import Dict, Any, List


class HermesToolGenerator:
    """Helper class to build JSON schemas for Hermes function tools."""

    @staticmethod
    def get_tool_schemas() -> List[Dict[str, Any]]:
        """
        Returns the list of function tools exposed to Hermes agent.
        Includes tools for: get_state, get_pnl, calculate_analytics,
        emergency_killall, high_risk_add.

        Faza 6 C.2: `emergency_reverse` deliberately NOT included — the
        endpoint always returns HTTP 501 (fail-closed, unimplemented), so it
        is not offered to the LLM as a usable option. See module docstring.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_symbols_status",
                    "description": "Fetch active multi-symbol trading list, per-symbol operating states (BASE_MODE/SCOUT_MODE), and active leverage. Each symbol also reports 3 directional-bias layers: 'directional_bias' (raw manual/Hermes-set override, 'BOTH' unless explicitly changed), 'htf_bias' (the automatic Faza 3.2 H4 directional gate's own reading, 'BOTH' when the gate is disabled or has not fired), and 'effective_directional_bias' (what evaluate_signals actually enforces right now, combining the two). Check 'effective_directional_bias' per symbol to know when the gate is actively restricting LONG or SHORT entries.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_state",
                    "description": "Fetch current MMS4 strategy state, active leverage, equity, and trade counters.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_balance",
                    "description": "Fetch unified OKX account balances (free available and total funds per currency) via mms4tk.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_pnl",
                    "description": "Fetch daily ROE %, realized PnL, unrealized PnL, and trade performance statistics.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "calculate_analytics",
                    "description": "Calculate TMA, ATR, envelope bands, and evaluate trading signals from OHLCV candles.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "candles": {
                                "type": "array",
                                "description": "List of OHLCV candle objects",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "timestamp": {"type": "number"},
                                        "open": {"type": "number"},
                                        "high": {"type": "number"},
                                        "low": {"type": "number"},
                                        "close": {"type": "number"},
                                        "volume": {"type": "number"}
                                    },
                                    "required": ["timestamp", "open", "high", "low", "close", "volume"]
                                }
                            },
                            "params": {
                                "type": "object",
                                "description": "Optional indicator parameters",
                                "properties": {
                                    "tma_length": {"type": "integer", "default": 20},
                                    "atr_period": {"type": "integer", "default": 14},
                                    "atr_mult": {"type": "number", "default": 2.0}
                                }
                            }
                        },
                        "required": ["candles"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "emergency_killall",
                    "description": "Emergency stop signal: cancels all open orders and closes active positions immediately. Requires X-MMS4-API-KEY header.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x_mms4_api_key": {
                                "type": "string",
                                "description": "Authentication key for X-MMS4-API-KEY header."
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "high_risk_add",
                    "description": "Force high-risk position addition (30% cap, composite SL=1.25%). Blocked in SCOUT_MODE. Requires X-MMS4-API-KEY header.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "side": {
                                "type": "string",
                                "enum": ["LONG", "SHORT"],
                                "default": "LONG",
                                "description": "Direction of position add"
                            },
                            "size": {
                                "type": "number",
                                "default": 1.0,
                                "description": "Size or contracts for addition"
                            },
                            "stop_loss_pct": {
                                "type": "number",
                                "default": 0.01,
                                "description": "Tight stop loss percentage (e.g. 0.01)"
                            },
                            "x_mms4_api_key": {
                                "type": "string",
                                "description": "Authentication key for X-MMS4-API-KEY header."
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "mms4_get_higher_tf",
                    "description": "Fetch higher timeframe (H4/D1) analytics including D1 ATR expansion ratio, H4 TMA position, and candle formation.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "mms4_set_strategy_profile",
                    "description": "Update active strategy profile (directional_bias, risk_profile, scout_override, reason) for adaptive autotrading. Requires X-MMS4-API-KEY header.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "directional_bias": {
                                "type": "string",
                                "enum": ["BOTH", "LONG_ONLY", "SHORT_ONLY", "FLAT"],
                                "default": "BOTH",
                                "description": "Directional bias filtering"
                            },
                            "risk_profile": {
                                "type": "string",
                                "enum": ["CONSERVATIVE", "MODERATE", "AGGRESSIVE"],
                                "default": "MODERATE",
                                "description": "Risk profile modulating leverage"
                            },
                            "scout_override": {
                                "type": "boolean",
                                "default": False,
                                "description": "Set True to relax SCOUT_MODE early. REFUSED if the symbol entered SCOUT_MODE less than MMS4_SCOUT_OVERRIDE_MIN_HOURS ago (default 48h) -- when refused, the response's scout_override field comes back False even though you sent True, and SCOUT_MODE is unchanged. Check GET /api/v1/daemon/status's allocation_plan.scout_age_hours for the symbol before calling this, or expect a silent no-op within the first 48h."
                            },
                            "reason": {
                                "type": "string",
                                "default": "",
                                "description": "Reason for profile update"
                            },
                            "symbol": {
                                "type": "string",
                                "description": "Symbol whose SCOUT_MODE scout_override relaxes (e.g. XAG/USDC). Omit for the primary trading symbol."
                            },
                            "x_mms4_api_key": {
                                "type": "string",
                                "description": "Authentication key for X-MMS4-API-KEY header."
                            }
                        },
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "mms4_get_pending_events",
                    "description": "Fetch pending webhook events from mms4tk that need to be dispatched to Hermes Kanban. Returns a list of undispatched SCOUT_MODE events.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "mms4_hitl_resolve",
                    "description": "Human-in-the-Loop resolution for uncertain H4 candle classification. Decision: SWEEP_WICK (attempts to relax SCOUT_MODE -- REFUSED if the symbol entered SCOUT_MODE less than MMS4_SCOUT_OVERRIDE_MIN_HOURS ago, default 48h; check the response's scout_mode_relaxed field, not just scout_override, to know if it actually happened) or MARUBOZU_BREAKOUT (maintain SCOUT_MODE). Requires X-MMS4-API-KEY header.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["SWEEP_WICK", "MARUBOZU_BREAKOUT"],
                                "description": "H4 candle classification decision"
                            },
                            "directional_bias": {
                                "type": "string",
                                "enum": ["BOTH", "LONG_ONLY", "SHORT_ONLY", "FLAT"],
                                "default": "BOTH",
                                "description": "Directional bias to apply"
                            },
                            "risk_profile": {
                                "type": "string",
                                "enum": ["CONSERVATIVE", "MODERATE", "AGGRESSIVE"],
                                "default": "MODERATE",
                                "description": "Risk profile to apply"
                            },
                            "reason": {
                                "type": "string",
                                "default": "Human-in-the-Loop decision",
                                "description": "Reason for the decision"
                            },
                            "symbol": {
                                "type": "string",
                                "description": "Symbol the decision applies to (e.g. XAG/USDC). Omit for the primary trading symbol."
                            },
                            "x_mms4_api_key": {
                                "type": "string",
                                "description": "Authentication key for X-MMS4-API-KEY header."
                            }
                        },
                        "required": ["decision"]
                    }
                }
            }
        ]


    @classmethod
    def get_tool_by_name(cls, name: str) -> Dict[str, Any]:
        """Fetch tool schema by function name."""
        for tool in cls.get_tool_schemas():
            if tool["function"]["name"] == name:
                return tool
        raise KeyError(f"Tool '{name}' not found in Hermes tool schemas.")


def get_hermes_tools() -> List[Dict[str, Any]]:
    """Convenience function returning Hermes JSON schemas."""
    return HermesToolGenerator.get_tool_schemas()

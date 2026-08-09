"""
Hermes Tools Module (mms4tk.hermes_tools)

Provides JSON schemas (OpenAPI/OpenAI function format) for Hermes agent (Nous Research)
function calling interface to interact with mms4tk FastAPI endpoints.
"""

from typing import Dict, Any, List


class HermesToolGenerator:
    """Helper class to build JSON schemas for Hermes function tools."""

    @staticmethod
    def get_tool_schemas() -> List[Dict[str, Any]]:
        """
        Returns the list of function tools exposed to Hermes agent.
        Includes tools for: get_state, get_pnl, calculate_analytics,
        emergency_killall, emergency_reverse, high_risk_add.
        """
        return [
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
                    "name": "emergency_reverse",
                    "description": "Emergency position reversal: closes current position and opens opposite position with SL=2%. Requires X-MMS4-API-KEY header.",
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
                    "description": "Force high-risk position addition (leverage x1, tight SL=1%). Blocked in SCOUT_MODE. Requires X-MMS4-API-KEY header.",
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

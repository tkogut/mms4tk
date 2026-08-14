import math
from datetime import datetime
from typing import List, Optional, Union, Dict, Any
import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from mms4tk.state_manager import MMS4StateManager, MMS4State


class Candle(BaseModel):
    """
    OHLCV candle representation.
    """
    timestamp: Union[int, float, str, datetime]
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class IndicatorParams(BaseModel):
    """
    Parameters for TMA and ATR calculation.
    """
    tma_length: int = 20
    atr_period: int = 14
    atr_mult: float = 2.0


class SignalResult(BaseModel):
    """
    Result of MMS4 signal evaluation.
    signal_type: "NONE" | "BASE_LONG" | "BASE_SHORT" | "HIGH_RISK_ADD_LONG" | "HIGH_RISK_ADD_SHORT"
    """
    signal_type: str
    price: float
    stop_loss: float
    leverage: float
    is_allowed: bool = True
    reason: Optional[str] = None


class TMAIndicator:
    """
    Calculates Triangular Moving Average (TMA) and ATR Bands.
    """

    @staticmethod
    def calculate(candles: List[Candle], params: IndicatorParams) -> pd.DataFrame:
        """
        Calculates TMA, ATR, Upper Band, and Lower Band for a list of candles.
        
        TMA formula: SMA(SMA(close, ceil(tma_length/2)), ceil(tma_length/2))
        ATR formula: rolling mean of True Range = max(high-low, abs(high-close_prev), abs(low-close_prev))
        Upper Band: TMA + atr_mult * ATR
        Lower Band: TMA - atr_mult * ATR
        """
        if not candles:
            return pd.DataFrame(columns=["tma", "atr", "upper_band", "lower_band"])

        # Convert candles list to DataFrame
        data = []
        for c in candles:
            if isinstance(c, Candle):
                data.append(c.model_dump())
            elif isinstance(c, dict):
                data.append(c)
            else:
                data.append({
                    "timestamp": getattr(c, "timestamp", 0),
                    "open": float(getattr(c, "open", 0)),
                    "high": float(getattr(c, "high", 0)),
                    "low": float(getattr(c, "low", 0)),
                    "close": float(getattr(c, "close", 0)),
                    "volume": float(getattr(c, "volume", 0))
                })

        df = pd.DataFrame(data)

        half_length = math.ceil(params.tma_length / 2)

        # 1. Double SMA to compute Triangular Moving Average (TMA)
        sma1 = df["close"].rolling(window=half_length, min_periods=half_length).mean()
        tma = sma1.rolling(window=half_length, min_periods=half_length).mean()

        # 2. True Range calculation
        close_prev = df["close"].shift(1)
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - close_prev).abs()
        tr3 = (df["low"] - close_prev).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # 3. ATR calculation
        atr = tr.rolling(window=params.atr_period, min_periods=params.atr_period).mean()

        # 4. Upper and Lower Bands
        upper_band = tma + (params.atr_mult * atr)
        lower_band = tma - (params.atr_mult * atr)

        return pd.DataFrame({
            "tma": tma,
            "atr": atr,
            "upper_band": upper_band,
            "lower_band": lower_band
        })


class MMS4SignalEngine:
    """
    Evaluates MMS v4 strategy trading signals (Base Entries and High Risk Adds).
    """

    def evaluate_signals(
        self,
        candles: List[Candle],
        state_manager: MMS4StateManager,
        params: Optional[IndicatorParams] = None
    ) -> SignalResult:
        """
        Evaluates candles and current strategy state to trigger trading signals.
        """
        if params is None:
            params = IndicatorParams()

        if len(candles) < 2:
            return SignalResult(
                signal_type="NONE",
                price=0.0,
                stop_loss=0.0,
                leverage=state_manager.leverage,
                is_allowed=True,
                reason="Insufficient candles for closed candle analysis"
            )

        closed_candles = candles[:-1]
        if len(closed_candles) < 2:
            return SignalResult(
                signal_type="NONE",
                price=0.0,
                stop_loss=0.0,
                leverage=state_manager.leverage,
                is_allowed=True,
                reason="Insufficient candles for closed candle analysis"
            )

        df = TMAIndicator.calculate(closed_candles, params)

        # Check if latest indicator values are valid
        latest_tma = df["tma"].iloc[-1]
        latest_upper = df["upper_band"].iloc[-1]
        latest_lower = df["lower_band"].iloc[-1]

        curr_c = closed_candles[-1]
        prev_c = closed_candles[-2]

        if pd.isna(latest_upper) or pd.isna(latest_lower) or pd.isna(latest_tma):
            return SignalResult(
                signal_type="NONE",
                price=curr_c.close,
                stop_loss=0.0,
                leverage=state_manager.leverage,
                is_allowed=True,
                reason="Indicators resulting in NaN (need more candle history)"
            )

        prev_upper = df["upper_band"].iloc[-2]
        prev_lower = df["lower_band"].iloc[-2]

        # Retrieve active strategy profile from state_manager
        profile = state_manager.strategy_profile
        bias = profile.directional_bias
        risk_prof = profile.risk_profile

        # Helper to apply directional_bias filtering
        def filter_bias(sig_type: str) -> Optional[SignalResult]:
            if bias == "FLAT":
                return SignalResult(
                    signal_type="NONE",
                    price=curr_c.close,
                    stop_loss=0.0,
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="Signal blocked by directional_bias=FLAT"
                )
            if bias == "LONG_ONLY" and sig_type in ("BASE_SHORT", "HIGH_RISK_ADD_SHORT"):
                return SignalResult(
                    signal_type="NONE",
                    price=curr_c.close,
                    stop_loss=0.0,
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="Signal blocked by directional_bias=LONG_ONLY"
                )
            if bias == "SHORT_ONLY" and sig_type in ("BASE_LONG", "HIGH_RISK_ADD_LONG"):
                return SignalResult(
                    signal_type="NONE",
                    price=curr_c.close,
                    stop_loss=0.0,
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="Signal blocked by directional_bias=SHORT_ONLY"
                )
            return None

        # 1. Check if previous candle was a Base Entry
        prev_was_base_short = (prev_c.high >= prev_upper or prev_c.close >= prev_upper) and (prev_c.close < prev_c.open)
        prev_was_base_long = (prev_c.low <= prev_lower or prev_c.close <= prev_lower) and (prev_c.close > prev_c.open)

        # 2. Evaluate High Risk Add (follows previous base entry, current candle closes in trend direction)
        if prev_was_base_short and (curr_c.close < curr_c.open):
            # High Risk Add SHORT candidate
            blocked = filter_bias("HIGH_RISK_ADD_SHORT")
            if blocked:
                return blocked

            if risk_prof == "CONSERVATIVE":
                return SignalResult(
                    signal_type="NONE",
                    price=curr_c.close,
                    stop_loss=0.0,
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="HIGH_RISK_ADD blocked by risk_profile=CONSERVATIVE"
                )

            if state_manager.current_state == MMS4State.SCOUT_MODE or not state_manager.is_high_risk_add_allowed():
                return SignalResult(
                    signal_type="HIGH_RISK_ADD_SHORT",
                    price=curr_c.close,
                    stop_loss=round(curr_c.close * 1.01, 6),
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="HIGH_RISK_ADD blocked in SCOUT_MODE (leverage x0.1)"
                )
            else:
                return SignalResult(
                    signal_type="HIGH_RISK_ADD_SHORT",
                    price=curr_c.close,
                    stop_loss=round(curr_c.close * 1.01, 6),
                    leverage=1.0,
                    is_allowed=True,
                    reason="HIGH_RISK_ADD SHORT signal: follow-up candle confirmed down trend"
                )

        if prev_was_base_long and (curr_c.close > curr_c.open):
            # High Risk Add LONG candidate
            blocked = filter_bias("HIGH_RISK_ADD_LONG")
            if blocked:
                return blocked

            if risk_prof == "CONSERVATIVE":
                return SignalResult(
                    signal_type="NONE",
                    price=curr_c.close,
                    stop_loss=0.0,
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="HIGH_RISK_ADD blocked by risk_profile=CONSERVATIVE"
                )

            if state_manager.current_state == MMS4State.SCOUT_MODE or not state_manager.is_high_risk_add_allowed():
                return SignalResult(
                    signal_type="HIGH_RISK_ADD_LONG",
                    price=curr_c.close,
                    stop_loss=round(curr_c.close * 0.99, 6),
                    leverage=state_manager.leverage,
                    is_allowed=False,
                    reason="HIGH_RISK_ADD blocked in SCOUT_MODE (leverage x0.1)"
                )
            else:
                return SignalResult(
                    signal_type="HIGH_RISK_ADD_LONG",
                    price=curr_c.close,
                    stop_loss=round(curr_c.close * 0.99, 6),
                    leverage=1.0,
                    is_allowed=True,
                    reason="HIGH_RISK_ADD LONG signal: follow-up candle confirmed up trend"
                )

        # 3. Evaluate Base Entries on current candle (Outer TMA Band penetration)
        curr_is_base_short = (curr_c.high >= latest_upper or curr_c.close >= latest_upper)
        curr_is_base_long = (curr_c.low <= latest_lower or curr_c.close <= latest_lower)

        if curr_is_base_short:
            blocked = filter_bias("BASE_SHORT")
            if blocked:
                return blocked

            if risk_prof == "CONSERVATIVE":
                lev = min(state_manager.leverage, 0.5)
            elif risk_prof == "AGGRESSIVE":
                lev = 2.0 if state_manager.current_state == MMS4State.BASE_MODE else state_manager.leverage
            else:
                lev = state_manager.leverage

            return SignalResult(
                signal_type="BASE_SHORT",
                price=curr_c.close,
                stop_loss=round(curr_c.close * 1.02, 6),
                leverage=lev,
                is_allowed=True,
                reason="Base SHORT signal: candle hit upper band and closed down"
            )

        if curr_is_base_long:
            blocked = filter_bias("BASE_LONG")
            if blocked:
                return blocked

            if risk_prof == "CONSERVATIVE":
                lev = min(state_manager.leverage, 0.5)
            elif risk_prof == "AGGRESSIVE":
                lev = 2.0 if state_manager.current_state == MMS4State.BASE_MODE else state_manager.leverage
            else:
                lev = state_manager.leverage

            return SignalResult(
                signal_type="BASE_LONG",
                price=curr_c.close,
                stop_loss=round(curr_c.close * 0.98, 6),
                leverage=lev,
                is_allowed=True,
                reason="Base LONG signal: candle hit lower band and closed up"
            )

        return SignalResult(
            signal_type="NONE",
            price=curr_c.close,
            stop_loss=0.0,
            leverage=state_manager.leverage,
            is_allowed=True,
            reason="No signal condition met"
        )


class HigherTimeframeAnalytics:
    """
    Analytics module for Higher Timeframe (H4 & D1) market state evaluation.
    Provides Hermes agent with volatility expansion & structural context.
    """

    @staticmethod
    def calculate_higher_tf(
        candles_h4: List[Candle],
        candles_d1: List[Candle],
        params: Optional[IndicatorParams] = None
    ) -> Dict[str, Any]:
        if params is None:
            params = IndicatorParams()

        # D1 ATR & Expansion Ratio
        d1_atr = 0.0
        atr_d1_expansion_ratio = 0.0
        if candles_d1:
            d1_df = TMAIndicator.calculate(candles_d1, params)
            if not d1_df.empty and not pd.isna(d1_df["atr"].iloc[-1]):
                d1_atr = float(d1_df["atr"].iloc[-1])

            latest_d1 = candles_d1[-1]
            d1_range = latest_d1.high - latest_d1.low
            if d1_atr > 0:
                atr_d1_expansion_ratio = round(d1_range / d1_atr, 4)

        # H4 TMA & Bands
        h4_tma_position = "INSIDE"
        h4_tma_val = 0.0
        h4_upper_val = 0.0
        h4_lower_val = 0.0
        h4_candle_formation = "NEUTRAL"

        if candles_h4:
            h4_df = TMAIndicator.calculate(candles_h4, params)
            if not h4_df.empty:
                latest_tma = h4_df["tma"].iloc[-1]
                latest_upper = h4_df["upper_band"].iloc[-1]
                latest_lower = h4_df["lower_band"].iloc[-1]

                if not (pd.isna(latest_tma) or pd.isna(latest_upper) or pd.isna(latest_lower)):
                    h4_tma_val = float(latest_tma)
                    h4_upper_val = float(latest_upper)
                    h4_lower_val = float(latest_lower)

                    curr_h4 = candles_h4[-1]
                    if curr_h4.close > h4_upper_val:
                        h4_tma_position = "ABOVE"
                    elif curr_h4.close < h4_lower_val:
                        h4_tma_position = "BELOW"
                    else:
                        h4_tma_position = "INSIDE"

            # Evaluate H4 candle formation
            curr_h4 = candles_h4[-1]
            total_range = curr_h4.high - curr_h4.low
            if total_range > 0:
                body = abs(curr_h4.close - curr_h4.open)
                upper_wick = curr_h4.high - max(curr_h4.open, curr_h4.close)
                lower_wick = min(curr_h4.open, curr_h4.close) - curr_h4.low
                max_wick = max(upper_wick, lower_wick)

                if body / total_range >= 0.65:
                    h4_candle_formation = "MARUBOZU_BREAKOUT"
                elif max_wick / total_range >= 0.35:
                    h4_candle_formation = "SWEEP_WICK"
                else:
                    h4_candle_formation = "NEUTRAL"

        return {
            "atr_d1": d1_atr,
            "atr_d1_expansion_ratio": atr_d1_expansion_ratio,
            "h4_tma_position": h4_tma_position,
            "h4_candle_formation": h4_candle_formation,
            "h4_tma": h4_tma_val,
            "h4_upper_band": h4_upper_val,
            "h4_lower_band": h4_lower_val
        }


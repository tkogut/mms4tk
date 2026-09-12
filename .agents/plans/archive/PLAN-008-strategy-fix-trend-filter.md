# 🔧 PLAN-008: MMS v4 Strategy Fix — Trend Filter + Candle Gate + Cooldown

**Status:** APPROVED / READY FOR EXECUTION  
**Version Phasing:**
- **Phase 1**: `0.33.7` → `0.34.1` (PATCH — Fix live parameter synchronization `_get_indicator_params()`)
- **Phase 2**: `0.34.1` → `0.35.0` (MINOR — Candle-Close Gate + Post-SL Cooldown)
- **Phase 3**: `0.35.0` → `0.36.0` (MINOR — TMA Slope Trend Filter post 90-day calibration)
**Impact:** CRITICAL — resolves live execution spam bug, eliminates cascade stop-outs, adds trend regime filtering  
**Principle:** 100% compliant with MMS v4 mathematical foundations (mean-reversion stays intact; filtering only blocks counter-trend entries in strong directional moves).

---

## 1. Problem Statement & Confirmed Root Causes

### 1.1 Confirmed Root Cause: 24.08 Signal Spam Cluster (10-15x `EXECUTED_BASE_SHORT`)
Auditing `daemon.py` revealed that while Telegram notifications use a message deduplication key (`dedupe_key=f"CLOSE_{target_symbol}_{side}_{int(entry_price)}"`), **there is ZERO deduplication or candle lock for `execute_order()`**.

When `fetch_positions()` experiences transient latency, rate-limiting, or empty responses from OKX:
- `own_positions` is empty (`existing_contracts == 0`, `matching_pos is None`).
- `Single Position Guard` (`if matching_pos is not None and existing_contracts > 0:`) evaluates to `False`.
- The daemon assumes no position exists and executes a new market order via `execute_order()` every 15 seconds.
- On a 1h candle, `candles[:-1]` produces the exact same signal 240 times/hour, causing repeated entries on transient exchange disconnects.

**Conclusion:** The **Candle-Close Gate** is a critical bugfix for a live, active defect, not a speculative optimization.

### 1.2 Inactivity Manager Asymmetry & Payoff Distortion
Historical database analysis reveals a structural payoff issue:
- **104 `STALE_SPLIT_50` + 19 `FORCE_CLOSE`** vs **only 16 `TP_AND_FLIP`**.
- Realized Risk/Reward is **0.89**, requiring a breakeven win rate of **52.8%**.
- Current `stale_candles_threshold=12` (12 hours on 1h timeframe) frequently halves or closes winning mean-reversion swings before price reaches the opposite TMA band.
- Trend filtering will reduce bad counter-trend entries, but tuning `stale_candles_threshold` is equally necessary to allow winners to achieve full target.

### 1.3 Decoupled Indicator Parameters
`daemon.py` invoked `signal_engine.evaluate_signals()` with default `IndicatorParams()`, which only read from `os.getenv()`. SQLite `system_settings` mutations from the UI were ignored by the signal engine, meaning `ADAPTIVE` mode was never truly active on production.

---

## 2. Phased Rollout Sequence

```
┌────────────────────────────────────────────────────────────────────────┐
│                        3-STAGE CONTROLLED ROLLOUT                      │
│                                                                        │
│ ── PHASE 1 (v0.34.1) ───────────────────────────────────────────────── │
│    • Implement _get_indicator_params() in daemon.py                    │
│    • Wire SQLite system_settings into evaluate_signals()               │
│    • Activates ADAPTIVE mode on production for the first time          │
│    • Observation period: monitor VR filter and time-lock in isolation │
│                                                                        │
│ ── PHASE 2 (v0.35.0) ───────────────────────────────────────────────── │
│    • Implement Candle-Close Gate (_last_signal_candle_ts)              │
│    • Implement Post-SL Cooldown (_sl_cooldown_until)                   │
│    • Fully eliminates 15s execution redundancy & cascade stop-outs     │
│                                                                        │
│ ── PHASE 3 (v0.36.0) ───────────────────────────────────────────────── │
│    • Calibrate TMA Slope lookback & threshold on >= 90 days data       │
│    • Implement TMA Slope Trend Filter in analytics.py                  │
│    • Add UI controls in SettingsTab.jsx & SQLite SSOT                  │
│    • Inactivity Manager threshold investigation                        │
└────────────────────────────────────────────────────────────────────────┘
```

> [!WARNING]
> **Triple-Filter Interaction Warning**:
> Fixing `_get_indicator_params()` activates `ADAPTIVE` mode on production for the first time. Deploying VR filter, US Open time-lock, and trend filter simultaneously in a single release creates unmanageable interaction risk. Phasing the rollout isolates each behavioral change.

---

## 3. Phase 1: Live Parameter Synchronization (`v0.34.1`)

### 3.1 Changes in `src/mms4tk/daemon.py`
Add `_get_indicator_params()` helper and pass dynamic params to `evaluate_signals()`:
```python
def _get_indicator_params(self) -> IndicatorParams:
    """Build IndicatorParams from live SQLite system_settings with env fallbacks."""
    db_settings = {}
    try:
        if self.logger_db:
            db_settings = self.logger_db.get_all_settings()
    except Exception:
        pass
    return IndicatorParams(
        engine_mode=str(db_settings.get("engine_mode", os.getenv("MMS4_ENGINE_MODE", "CLASSIC"))),
        adaptive_vr_high=float(db_settings.get("adaptive_vr_high", os.getenv("MMS4_ADAPTIVE_VR_HIGH", "1.6"))),
        adaptive_vr_low=float(db_settings.get("adaptive_vr_low", os.getenv("MMS4_ADAPTIVE_VR_LOW", "0.8"))),
        time_lock_start=str(db_settings.get("time_lock_start", os.getenv("MMS4_TIME_LOCK_START", "13:30"))),
        time_lock_end=str(db_settings.get("time_lock_end", os.getenv("MMS4_TIME_LOCK_END", "14:30"))),
    )
```

In `execute_symbol_cycle()` (around line 654):
```python
params = self._get_indicator_params()
signal: SignalResult = self.signal_engine.evaluate_signals(
    candles, self.state_manager, params=params, symbol=target_symbol
)
```

### 3.2 Verification for Phase 1
- `tests/test_params_sync.py`: Test that mutating SQLite `system_settings` changes `evaluate_signals()` behavior dynamically without daemon restart.
- Version bump to `0.34.1` across `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `CHANGELOG.md`.

---

## 4. Phase 2: Candle-Close Gate + Post-SL Cooldown (`v0.35.0`)

### 4.1 Strict Placement of Candle-Close Gate in `daemon.py`
> [!IMPORTANT]
> **Safety Invariant**: Position Reconciliation (`reconcile_positions`), Self-Healing Stop Loss (`attach_stop_loss`), and Stale Position Management (`InactivityManager.update_and_check`) MUST execute every 15 seconds uninterrupted.
> The Candle-Close Gate is strictly placed **inside the order execution dispatch block** (lines ~1007+), preventing repeat order submissions while allowing continuous 15s monitoring.

```python
        # ────────────────────────────────────────────────────────────────
        # POST-SL COOLDOWN CHECK (Lines ~1003+)
        # ────────────────────────────────────────────────────────────────
        cooldown_until = self._sl_cooldown_until.get(target_symbol, 0)
        if cooldown_until > 0 and len(candles) >= 2:
            cur_ts = int(candles[-1].timestamp) if hasattr(candles[-1].timestamp, '__int__') else int(candles[-1].timestamp)
            if cur_ts < cooldown_until:
                outcome = "COOLDOWN_ACTIVE"
                details = f"Post-SL cooldown active for {target_symbol} until {datetime.fromtimestamp(cooldown_until/1000.0, tz=timezone.utc).isoformat()}"
                self.logger_db.log_cycle(
                    symbol=target_symbol, signal_type="NONE", state=sym_state.value,
                    leverage=sym_leverage, price=float(candles[-1].close), outcome=outcome, details=details
                )
                return {
                    "symbol": target_symbol, "timestamp": now_iso, "signal_type": "NONE",
                    "outcome": outcome, "state": sym_state.value, "leverage": sym_leverage,
                    "order": None, "reconciliation": reconciliation_events
                }
            else:
                del self._sl_cooldown_until[target_symbol]

        # ────────────────────────────────────────────────────────────────
        # CANDLE-CLOSE GATE FOR SIGNAL DISPATCH (Lines ~1007+)
        # ────────────────────────────────────────────────────────────────
        last_closed_ts = int(candles[-2].timestamp) if len(candles) >= 2 else 0
        prev_signal_ts = self._last_signal_candle_ts.get(target_symbol, 0)

        if is_maintenance_locked():
            outcome = f"SKIPPED_MAINTENANCE_MODE"
            details = f"⛔ Trading blocked: Maintenance Mode is ACTIVE. Signal {signal.signal_type} for {target_symbol} skipped."
            logger.warning(details)
        elif signal.signal_type in ["BASE_SHORT", "BASE_LONG", "HIGH_RISK_ADD_SHORT", "HIGH_RISK_ADD_LONG"]:
            # Gate check: has this closed candle already triggered an evaluation/order this candle period?
            if last_closed_ts > 0 and last_closed_ts == prev_signal_ts:
                outcome = f"CANDLE_GATE_SKIP_{signal.signal_type}"
                details = f"Signal {signal.signal_type} for {target_symbol} already evaluated for closed candle {last_closed_ts}. Skipping repeat execution."
                self.logger_db.log_cycle(
                    symbol=target_symbol, signal_type=signal.signal_type, state=sym_state.value,
                    leverage=sym_leverage, price=signal.price, outcome=outcome, details=details
                )
                return {
                    "symbol": target_symbol, "timestamp": now_iso, "signal_type": signal.signal_type,
                    "outcome": outcome, "state": sym_state.value, "leverage": sym_leverage,
                    "order": None, "reconciliation": reconciliation_events
                }

            # Proceed with normal order execution...
            # Upon execution / handling, record candle timestamp:
            self._last_signal_candle_ts[target_symbol] = last_closed_ts
```

> [!NOTE]
> **Implementation detail — two separate `elif` blocks, not one merged condition.**
> In current `daemon.py` this is TWO distinct blocks: `elif signal.signal_type in ["BASE_SHORT", "BASE_LONG"]:` (line ~1007) and `elif "HIGH_RISK_ADD" in signal.signal_type:` (line ~1177). The snippet above illustrates the shared gate logic — do **not** literally merge both signal types into one `elif` list. The gate check (and the `_last_signal_candle_ts[target_symbol]` read/write) must be added at the top of **both** existing blocks independently; each block must keep dispatching to its own sizing/SL logic (`base_order_amount`/`base_sl_pct` vs `addon_order_amount`/`addon_sl_pct`). Merging the conditions would route `HIGH_RISK_ADD_*` signals through the base-entry branch with the wrong order size and stop-loss.
>
> Also note: `_last_signal_candle_ts` is keyed per-symbol, not per-(symbol, signal_type) — a `BASE_SHORT` gate-write can suppress a genuine `HIGH_RISK_ADD_SHORT` evaluated later in the same closed-candle window. This is an accepted trade-off (one signal-driven action per symbol per closed candle) and is additionally covered by the existing persisted Single Addon Guard (`_addon_count`), so no separate fix is required — documented here so the behavior isn't mistaken for a bug during QA.

### 4.2 Post-SL Cooldown Activation (Verified `is_stop_loss` Schema)
In `execute_symbol_cycle()` right after `reconciliation_events`:
```python
        # Engage Cooldown on Stop-Loss event from reconciliation:
        for evt in reconciliation_events:
            # Verified exact key in daemon.py line 541: "is_stop_loss": is_stop_out
            if evt.get("is_stop_loss"):
                params = self._get_indicator_params()
                cooldown_n = int(getattr(params, "sl_cooldown_candles", os.getenv("MMS4_SL_COOLDOWN_CANDLES", "3")))
                if cooldown_n > 0 and len(candles) >= 2:
                    c_interval = abs(int(candles[-1].timestamp) - int(candles[-2].timestamp))
                    if c_interval > 0:
                        self._sl_cooldown_until[target_symbol] = int(candles[-1].timestamp) + (cooldown_n * c_interval)
                        logger.info(f"🧊 Post-SL cooldown engaged for {target_symbol}: {cooldown_n} candles")
```

### 4.3 Verification for Phase 2
- `tests/test_candle_gate.py`:
  - Verifies repeat cycles with same candle timestamp return `CANDLE_GATE_SKIP`.
  - Verifies reconciliation, SL healing, and InactivityManager execute on every cycle (15s).
- `tests/test_sl_cooldown.py`:
  - Verifies reconciliation event with `is_stop_loss: True` engages cooldown and blocks entries.
- Version bump to `0.35.0`.

---

## 5. Phase 3: TMA Slope Trend Filter + Inactivity Audit (`v0.36.0`)

### 5.1 Requirement: 90-Day Backtest & Threshold Calibration
TMA is double-smoothed (`SMA(SMA(close, 10), 10)`), introducing inherent lag. A static `pct_change(5)` with threshold `0.002` (0.2%) on 1h BTC may capture noise or lag behind price turns.

**Prerequisite for Phase 3:**
1. Fetch 90 days of 1h OHLCV for BTC/USDC and ETH/USDC.
2. Run backtest matrix across:
   - `tma_slope_lookback`: [3, 5, 8, 12]
   - `tma_slope_threshold`: [0.001, 0.002, 0.003, 0.005]
3. Evaluate Win Rate, Profit Factor, and False Positive rejection rate before hardcoding defaults.

### 5.2 Implementation Details (Post-Calibration)
- Add `tma_slope` column to `TMAIndicator.calculate()`.
- Add `tma_slope_threshold` to `IndicatorParams`, `SettingsUpdateRequest`, `SettingsTab.jsx`, and SQLite.
- Gating in `evaluate_signals()`:
  - `BASE_SHORT` blocked when `slope > threshold` (Uptrend).
  - `BASE_LONG` blocked when `slope < -threshold` (Downtrend).
- Inactivity Manager audit: evaluate impact of increasing `stale_candles_threshold` from 12 to 18-24 to reduce premature position cuts.

---

## 6. Swarm Triad Governance & Safe Deployment

1. **Worktree Isolation**: All changes executed in `tmp/worktrees/fix-plan-008-...`
2. **Zero-Loss Maintenance Mode**: Lock engaged via `scripts/maintenance.py --enable` prior to any code edit or deployment.
3. **Builder & Auditor Handshake**: Both JSON handshakes verified before production merge.
4. **SemVer SSOT Discipline**: Versions strictly bumped across `VERSION`, `src/mms4tk/__init__.py`, `ui_dashboard/app.py`, `CHANGELOG.md`.

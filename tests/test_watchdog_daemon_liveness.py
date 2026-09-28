"""
Tests for scripts/hermes/mms4tk_daemon_liveness_watchdog.py — the external
watchdog (issue #68) that detects a stalled/dead antigravity_core daemon and
alerts via Telegram with a computed suggested manual SL price.
"""
import importlib.util
import os
import time

import pytest

_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "hermes", "mms4tk_daemon_liveness_watchdog.py"
)
_spec = importlib.util.spec_from_file_location("mms4tk_daemon_liveness_watchdog", _SCRIPT_PATH)
watchdog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(watchdog)


# ---------------------------------------------------------------------------
# compute_negation_level — must match InactivityManager._check_impulse_negation
# ---------------------------------------------------------------------------

def test_negation_level_matches_real_production_short_example():
    """Real production tracker state (XAG/USDC short, 2026-09-28 incident):
    entry=64.2498, min_observed=60.83, small ATR -> negation level ~62.14."""
    level = watchdog.compute_negation_level(
        side="short", entry_price=64.2498, min_observed_price=60.83,
        max_observed_price=0.0, atr=0.058,
    )
    assert level == pytest.approx(62.136, abs=0.01)


def test_negation_level_long_side_uses_fib_50_for_small_impulse():
    """When the impulse hasn't cleared 0.8*ATR, fib_ratio is 0.5, not 0.382."""
    # impulse = 101 - 100 = 1.0, 0.8*atr = 0.8*2.0 = 1.6 -> impulse < 0.8*atr -> fib=0.5
    level = watchdog.compute_negation_level(
        side="long", entry_price=100.0, min_observed_price=0.0,
        max_observed_price=101.0, atr=2.0,
    )
    assert level == pytest.approx(101.0 - (1.0 * 0.5))


def test_negation_level_raises_when_extreme_never_observed():
    with pytest.raises(ValueError):
        watchdog.compute_negation_level(
            side="short", entry_price=100.0, min_observed_price=0.0,
            max_observed_price=0.0, atr=1.0,
        )


def test_negation_level_raises_on_nonpositive_atr():
    with pytest.raises(ValueError):
        watchdog.compute_negation_level(
            side="long", entry_price=100.0, min_observed_price=0.0,
            max_observed_price=105.0, atr=0.0,
        )


# ---------------------------------------------------------------------------
# is_fresh
# ---------------------------------------------------------------------------

def test_is_fresh_within_threshold():
    now = time.time()
    from datetime import datetime, timezone
    recent = datetime.fromtimestamp(now - 30, tz=timezone.utc).isoformat()
    assert watchdog.is_fresh(recent, now, threshold=180) is True


def test_is_fresh_beyond_threshold():
    now = time.time()
    from datetime import datetime, timezone
    old = datetime.fromtimestamp(now - 300, tz=timezone.utc).isoformat()
    assert watchdog.is_fresh(old, now, threshold=180) is False


def test_is_fresh_handles_missing_or_malformed_timestamp():
    now = time.time()
    assert watchdog.is_fresh(None, now) is False
    assert watchdog.is_fresh("not-a-timestamp", now) is False


# ---------------------------------------------------------------------------
# main() — end-to-end with fetch_json/send_telegram monkeypatched
# ---------------------------------------------------------------------------

def _status_payload(last_cycle_iso, entry=64.2498, min_obs=60.83, atr=0.058):
    return {
        "last_cycle_timestamp": last_cycle_iso,
        "inactivity_manager": {
            "positions": {
                "XAG/USDC": {
                    "side": "short",
                    "entry_price": entry,
                    "min_observed_price": min_obs,
                    "max_observed_price": 0.0,
                    "atr_at_entry": atr,
                }
            }
        },
    }


def _positions_payload(unrealized_pnl=9.29):
    return {
        "positions": [
            {"symbol": "XAG/USDC", "side": "short", "unrealized_pnl": unrealized_pnl}
        ]
    }


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    state_path = str(tmp_path / "watchdog_state.json")
    monkeypatch.setattr(watchdog, "STATE_FILE", state_path)
    return state_path


def test_main_does_not_alert_when_daemon_is_fresh(isolated_state, monkeypatch):
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    calls = {"fetch": [], "telegram": []}

    def fake_fetch_json(path):
        calls["fetch"].append(path)
        if path == "/api/v1/daemon/status":
            return _status_payload(now_iso)
        return _positions_payload()

    monkeypatch.setattr(watchdog, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(watchdog, "send_telegram", lambda text: calls["telegram"].append(text))

    watchdog.main()

    assert calls["telegram"] == []


def test_main_alerts_with_suggested_price_when_stale_and_profitable(isolated_state, monkeypatch):
    from datetime import datetime, timezone, timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    def fake_fetch_json(path):
        if path == "/api/v1/daemon/status":
            return _status_payload(stale_iso)
        return _positions_payload(unrealized_pnl=9.29)

    calls = {"telegram": []}
    monkeypatch.setattr(watchdog, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(watchdog, "send_telegram", lambda text: calls["telegram"].append(text))

    watchdog.main()

    assert len(calls["telegram"]) == 1
    msg = calls["telegram"][0]
    assert "XAG/USDC" in msg
    assert "62.1" in msg  # the computed suggested SL level
    assert "9.29" in msg


def test_main_does_not_resend_alert_for_same_stall_episode(isolated_state, monkeypatch):
    from datetime import datetime, timezone, timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    def fake_fetch_json(path):
        if path == "/api/v1/daemon/status":
            return _status_payload(stale_iso)
        return _positions_payload(unrealized_pnl=9.29)

    calls = {"telegram": []}
    monkeypatch.setattr(watchdog, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(watchdog, "send_telegram", lambda text: calls["telegram"].append(text))

    watchdog.main()
    watchdog.main()  # simulate the next cron tick, still stale

    assert len(calls["telegram"]) == 1


def test_main_resets_alert_flag_once_daemon_recovers(isolated_state, monkeypatch):
    from datetime import datetime, timezone, timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    fresh_iso_holder = {"ts": stale_iso}

    def fake_fetch_json(path):
        if path == "/api/v1/daemon/status":
            return _status_payload(fresh_iso_holder["ts"])
        return _positions_payload(unrealized_pnl=9.29)

    calls = {"telegram": []}
    monkeypatch.setattr(watchdog, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(watchdog, "send_telegram", lambda text: calls["telegram"].append(text))

    watchdog.main()
    assert len(calls["telegram"]) == 1

    # Daemon recovers.
    fresh_iso_holder["ts"] = datetime.now(timezone.utc).isoformat()
    watchdog.main()
    assert len(calls["telegram"]) == 1  # no new alert while fresh

    # Stalls again — this must be treated as a NEW episode and re-alert.
    fresh_iso_holder["ts"] = stale_iso
    watchdog.main()
    assert len(calls["telegram"]) == 2


def test_main_does_not_alert_when_stale_but_no_significant_profit(isolated_state, monkeypatch):
    from datetime import datetime, timezone, timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    def fake_fetch_json(path):
        if path == "/api/v1/daemon/status":
            return _status_payload(stale_iso)
        return _positions_payload(unrealized_pnl=0.05)  # below MIN_PROFIT_USD_TO_ALERT

    calls = {"telegram": []}
    monkeypatch.setattr(watchdog, "fetch_json", fake_fetch_json)
    monkeypatch.setattr(watchdog, "send_telegram", lambda text: calls["telegram"].append(text))

    watchdog.main()

    assert calls["telegram"] == []


def test_main_uses_cached_state_when_api_totally_unreachable(isolated_state, monkeypatch):
    """The exact scenario this watchdog exists for: the container has died, so
    BOTH endpoints are unreachable. It must fall back to the last persisted
    suggestion/position snapshot instead of going silent."""
    from datetime import datetime, timezone, timedelta
    stale_iso = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    # First run: daemon still reachable, but already stale-timestamped, with a
    # profitable position — this seeds the persisted state.
    def fake_fetch_json_seed(path):
        if path == "/api/v1/daemon/status":
            return _status_payload(stale_iso)
        return _positions_payload(unrealized_pnl=9.29)

    calls = {"telegram": []}
    monkeypatch.setattr(watchdog, "fetch_json", fake_fetch_json_seed)
    monkeypatch.setattr(watchdog, "send_telegram", lambda text: calls["telegram"].append(text))
    watchdog.main()
    assert len(calls["telegram"]) == 1

    # Force a new stall episode next tick, but this time the API is fully down.
    state = watchdog.load_state()
    state["alert_sent"] = False
    watchdog.save_state(state)

    monkeypatch.setattr(watchdog, "fetch_json", lambda path: None)
    watchdog.main()

    assert len(calls["telegram"]) == 2
    assert "62.1" in calls["telegram"][1]
    assert "Nie udało się połączyć" in calls["telegram"][1]

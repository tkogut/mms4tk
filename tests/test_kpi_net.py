"""#122: the dashboard showed gross as "Netto" and derived win rate / profit factor /
max drawdown / R:R from gross PnL. `get_kpi_summary` now also reports those measures net
of fees (additive `*_net` keys); the existing gross keys keep their meaning."""
import os

import pytest
from fastapi.testclient import TestClient

import mms4tk.api as api_module
from mms4tk.logger_db import LoggerDB

# (pnl_usd, fee_usd) in chronological order. Gross: 4 wins, 1 loss; net: 2 wins, 3 losses.
TRADES = [
    (0.30, 0.10),   # net +0.20
    (0.10, 0.15),   # net -0.05  (gross win, net loss)
    (0.05, 0.10),   # net -0.05  (gross win, net loss)
    (-0.40, 0.10),  # net -0.50
    (0.20, 0.05),   # net +0.15
]


@pytest.fixture()
def db(tmp_path) -> LoggerDB:
    d = LoggerDB(os.path.join(tmp_path, "kpi_net.db"))
    for i, (pnl, fee) in enumerate(TRADES):
        d.upsert_closed_trade(
            trade_id=f"t{i}", timestamp=f"2026-10-0{i + 1}T10:00:00+00:00", symbol="XAG/USDC",
            side="LONG", entry_price=60.0, exit_price=61.0, amount=1.0,
            pnl_usd=pnl, pnl_pct=0.001, is_win=(pnl - fee) > 0, fee_usd=fee, pnl_net_usd=pnl - fee,
        )
    return d


def test_net_ratios_are_computed_from_net_pnl(db):
    kpi = db.get_kpi_summary()
    # net: profit 0.35, loss 0.60 -> PF 0.58; avg win 0.175 / avg loss 0.20 -> R:R 0.88
    assert kpi["profit_factor_net"] == pytest.approx(0.58, abs=0.011)
    assert kpi["average_rr_net"] == pytest.approx(0.88, abs=0.011)
    # net equity: +.20 +.15(peak .20) .10 -.40 -.25 -> peak-to-trough 0.60
    assert kpi["max_drawdown_net_usd"] == pytest.approx(0.60)
    assert kpi["net_win_rate_pct"] == pytest.approx(40.0)


def test_gross_keys_keep_their_meaning(db):
    kpi = db.get_kpi_summary()
    assert kpi["win_rate"] == pytest.approx(80.0)                 # 4 of 5 gross wins
    assert kpi["profit_factor"] == pytest.approx(1.62, abs=0.011)  # 0.65 / 0.40
    assert kpi["max_drawdown_usd"] == pytest.approx(0.40)
    assert kpi["realized_gross_pnl_usd"] == pytest.approx(0.25)
    assert kpi["realized_net_pnl_usd"] == pytest.approx(-0.25)


def test_net_drawdown_pct_uses_initial_equity(db):
    kpi = db.get_kpi_summary(initial_equity=10.0)
    # equity 10 -> 10.20 peak; trough 9.60 -> 0.60 / 10.20
    assert kpi["max_drawdown_net_pct"] == pytest.approx(0.60 / 10.20 * 100, abs=0.01)


def test_empty_db_reports_zero_net_ratios(tmp_path):
    kpi = LoggerDB(os.path.join(tmp_path, "empty.db")).get_kpi_summary()
    for key in ("profit_factor_net", "average_rr_net", "max_drawdown_net_usd", "max_drawdown_net_pct"):
        assert kpi[key] == 0.0


def test_stats_endpoint_exposes_net_keys(db, monkeypatch, empty_symbols_store):
    monkeypatch.setattr(api_module, "logger_db", db)
    with TestClient(api_module.app) as client:
        data = client.get("/api/v1/stats/pnl").json()
    for block in (data, data["since_epoch"]):
        assert block["profit_factor_net"] == pytest.approx(0.58, abs=0.011)
        assert block["max_drawdown_net_usd"] == pytest.approx(0.60)
        assert "average_rr_net" in block and "max_drawdown_net_pct" in block
    # gross keys unchanged in the same payload
    assert data["profit_factor"] == pytest.approx(1.62, abs=0.011)
    assert data["realized_net_pnl_usd"] == pytest.approx(-0.25)


def test_net_falls_back_to_gross_minus_fee_when_pnl_net_is_missing(tmp_path):
    d = LoggerDB(os.path.join(tmp_path, "fallback.db"))
    d.upsert_closed_trade(
        trade_id="f1", timestamp="2026-10-01T10:00:00+00:00", symbol="XAG/USDC", side="LONG",
        entry_price=60.0, exit_price=61.0, amount=1.0, pnl_usd=0.30, pnl_pct=0.001,
        is_win=True, fee_usd=0.10, pnl_net_usd=None,
    )
    d.upsert_closed_trade(
        trade_id="f2", timestamp="2026-10-02T10:00:00+00:00", symbol="XAG/USDC", side="LONG",
        entry_price=60.0, exit_price=59.0, amount=1.0, pnl_usd=-0.10, pnl_pct=-0.001,
        is_win=False, fee_usd=0.10, pnl_net_usd=None,
    )
    kpi = d.get_kpi_summary()
    # the DB layer defaults a missing net to gross - fee, so net = +0.20 and -0.20
    assert kpi["profit_factor_net"] == pytest.approx(1.0)
    assert kpi["max_drawdown_net_usd"] == pytest.approx(0.20)


def test_all_net_wins_hit_the_profit_factor_cap(tmp_path):
    d = LoggerDB(os.path.join(tmp_path, "cap.db"))
    for i in range(2):
        d.upsert_closed_trade(
            trade_id=f"w{i}", timestamp=f"2026-10-0{i + 1}T10:00:00+00:00", symbol="XAG/USDC", side="LONG",
            entry_price=60.0, exit_price=61.0, amount=1.0, pnl_usd=0.50, pnl_pct=0.01,
            is_win=True, fee_usd=0.10, pnl_net_usd=0.40,
        )
    kpi = d.get_kpi_summary()
    assert kpi["profit_factor_net"] == 99.0
    assert kpi["average_rr_net"] == 99.0
    assert kpi["max_drawdown_net_usd"] == 0.0

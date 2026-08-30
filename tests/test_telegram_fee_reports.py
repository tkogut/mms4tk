import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add project root and scripts/hermes to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "hermes"))

from mms4tk_position_close_monitor import format_close_notification
from mms4tk_multi_symbol_digest import build_digest_content
from mms4tk.daemon import MMS4Daemon
from mms4tk.state_manager import MMS4StateManager, MMS4State
from mms4tk.okx_executor import OKXExecutor
from mms4tk.logger_db import LoggerDB


def test_position_close_monitor_formatting_win():
    """Test format_close_notification with profitable trade."""
    trade = {
        "symbol": "BTC/USDC",
        "side": "long",
        "amount": 2.0,
        "leverage": 3.0,
        "entry_price": 50000.0,
        "exit_price": 51000.0,
        "pnl_usd": 20.0,
        "fee_usd": 1.5,
        "pnl_net_usd": 18.5,
        "is_win": True,
    }
    msg = format_close_notification(trade)
    assert "🔔 ZAMKNIĘCIE POZYCJI OKX: BTC/USDC" in msg
    assert "- Strona: `LONG`" in msg
    assert "- Ilość: `2.00`" in msg
    assert "- Dźwignia: `3x`" in msg
    assert "- Wejście: `$50,000.0000`" in msg
    assert "- Wyjście: `$51,000.0000`" in msg
    assert "- 💸 Wynik Netto: 🟢 **`+$18.50`** (+2.00%)" in msg
    assert "- 🏷️ Prowizje OKX: `-$1.5000 USD`" in msg
    assert "- 📈 Wynik Brutto: `+$20.00`" in msg
    assert "- Status: `ZYSK` (WIN 🟢)" in msg


def test_position_close_monitor_gross_win_net_loss():
    """Test format_close_notification where gross PnL is positive but Net PnL is negative due to fees."""
    trade = {
        "symbol": "ETH/USDC",
        "side": "short",
        "amount": 10.0,
        "leverage": 2.0,
        "entry_price": 2000.0,
        "exit_price": 1999.0,
        "pnl_usd": 0.10,
        "fee_usd": 0.50,
        "pnl_net_usd": -0.40,
        "is_win": False,
    }
    msg = format_close_notification(trade)
    assert "ETH/USDC" in msg
    assert "- Strona: `SHORT`" in msg
    assert "- 💸 Wynik Netto: 🔴 **`-$0.40`** (+0.05%)" in msg
    assert "- 🏷️ Prowizje OKX: `-$0.5000 USD`" in msg
    assert "- 📈 Wynik Brutto: `+$0.10`" in msg
    assert "- Status: `STRATA` (LOSS 🔴)" in msg


def test_position_close_monitor_fallback_net_pnl():
    """Test fallback calculation when pnl_net_usd or is_win is omitted."""
    trade = {
        "symbol": "SOL/USDC",
        "side": "LONG",
        "amount": 5.0,
        "leverage": 3.0,
        "entry_price": 100.0,
        "exit_price": 95.0,
        "pnl_usd": -5.0,
        "fee_usd": 0.30,
    }
    msg = format_close_notification(trade)
    assert "- 💸 Wynik Netto: 🔴 **`-$5.30`** (-5.00%)" in msg
    assert "- 🏷️ Prowizje OKX: `-$0.3000 USD`" in msg
    assert "- 📈 Wynik Brutto: `-$5.00`" in msg
    assert "- Status: `STRATA` (LOSS 🔴)" in msg


def test_multi_symbol_digest_portfolio_summary():
    """Test multi_symbol_digest includes net pnl, fees, gross pnl and net win rate."""
    symbols = [
        {"symbol": "BTC/USDC", "is_active": True, "state": "BASE_MODE", "allocation_usd": 50.0, "okx_price": 60000.0},
        {"symbol": "ETH/USDC", "is_active": True, "state": "BASE_MODE", "allocation_usd": 50.0, "okx_price": 3000.0},
    ]
    positions = []
    balance_data = {
        "free": {"USDC": 150.0},
        "total": {"USDC": 200.0},
    }
    stats_data = {
        "realized_gross_pnl_usd": 45.50,
        "total_fees_usd": 5.25,
        "realized_net_pnl_usd": 40.25,
        "net_win_rate_pct": 65.5,
    }

    digest = build_digest_content(
        ver="0.41.1",
        symbols=symbols,
        positions=positions,
        balance_data=balance_data,
        stats_data=stats_data,
    )

    assert "📊 **HERMES MULTI-SYMBOL SWARM DIGEST** (Wersja v0.41.1)" in digest
    assert "💰 **PODSUMOWANIE PORTFELA OKX (API)**:" in digest
    assert "- 💸 Zrealizowany PnL Netto: +$40.25 USD" in digest
    assert "- 🏷️ Łączne Prowizje OKX: -$5.25 USD" in digest
    assert "- 📈 Zrealizowany PnL Brutto: +$45.50 USD" in digest
    assert "- 🎯 Skuteczność Netto (Win Rate): 65.5%" in digest


def test_multi_symbol_digest_negative_net_pnl():
    """Test multi_symbol_digest with negative realized net PnL."""
    symbols = [{"symbol": "BTC/USDC", "is_active": True, "allocation_usd": 50.0}]
    positions = []
    balance_data = {"free": {"USDC": 80.0}, "total": {"USDC": 100.0}}
    stats_data = {
        "realized_gross_pnl_usd": -10.0,
        "total_fees_usd": 2.50,
        "realized_net_pnl_usd": -12.50,
        "net_win_rate_pct": 33.3,
    }

    digest = build_digest_content(
        ver="0.41.1",
        symbols=symbols,
        positions=positions,
        balance_data=balance_data,
        stats_data=stats_data,
    )

    assert "- 💸 Zrealizowany PnL Netto: -$12.50 USD" in digest
    assert "- 🏷️ Łączne Prowizje OKX: -$2.50 USD" in digest
    assert "- 📈 Zrealizowany PnL Brutto: -$10.00 USD" in digest
    assert "- 🎯 Skuteczność Netto (Win Rate): 33.3%" in digest


def test_daemon_reconciliation_notifications(tmp_path: Path):
    """Test that daemon reconciliation close notifications include estimated fee and net PnL."""
    db_file = tmp_path / "test_daemon_fee_notify.db"
    logger_db = LoggerDB(db_path=str(db_file))

    executor = OKXExecutor()
    executor.exchange = MagicMock()
    executor.exchange.markets = {"BTC/USDC": {"contractSize": 0.0001}}
    executor.exchange.market = MagicMock(return_value={"contractSize": 0.0001})

    state_mgr = MMS4StateManager(initial_state=MMS4State.BASE_MODE)

    daemon = MMS4Daemon(
        executor=executor,
        state_manager=state_mgr,
        logger_db=logger_db,
        symbol="BTC/USDC",
    )

    # 1. Normal close notification
    logger_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=100.0,
        entry_price=50000.0,
        last_price=50000.0,
    )

    with patch("mms4tk.hermes_webhook.HermesWebhookClient.send_telegram") as mock_send:
        # Exit at 52000 (gain)
        daemon.reconcile_positions(
            target_symbol="BTC/USDC",
            live_positions=[],
            current_price=52000.0,
        )

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert "🔔 ZAMKNIĘCIE POZYCJI: BTC/USDC LONG" in call_kwargs["title"]
        body = call_kwargs["body"]
        assert "- Wejście: `$50,000.0000`" in body
        assert "- Wyjście: `$52,000.0000`" in body
        assert "- Wynik Netto (szac.): 🟢" in body
        assert "- Prowizje OKX (szac.): ~$" in body
        assert "- Wynik Brutto: $" in body

    # 2. Stop-loss close notification
    logger_db.save_position_snapshot(
        symbol="BTC/USDC",
        side="long",
        contracts=100.0,
        entry_price=50000.0,
        last_price=50000.0,
    )

    with patch("mms4tk.hermes_webhook.HermesWebhookClient.send_telegram") as mock_send_sl:
        # Exit at 45000 (10% drop, triggers stop-loss notification)
        daemon.reconcile_positions(
            target_symbol="BTC/USDC",
            live_positions=[],
            current_price=45000.0,
        )

        mock_send_sl.assert_called_once()
        call_kwargs = mock_send_sl.call_args[1]
        assert "🛑 STOP-LOSS: BTC/USDC LONG" in call_kwargs["title"]
        body = call_kwargs["body"]
        assert "- Wejście: `$50,000.0000`" in body
        assert "- Wyjście: `$45,000.0000`" in body
        assert "- Wynik Netto (szac.): 🔴" in body
        assert "- Prowizje OKX (szac.): ~$" in body
        assert "- Wynik Brutto: $" in body

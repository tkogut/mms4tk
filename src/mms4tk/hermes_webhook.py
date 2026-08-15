"""
Hermes Webhook Client (mms4tk.hermes_webhook)

Sends event-driven notifications to Hermes Agent's Kanban board
when critical state transitions occur in mms4tk (e.g., SCOUT_MODE activation).

Uses the `hermes kanban create` CLI interface via HTTP or direct SQLite insertion
as the Hermes Kanban API is CLI-based, not REST-based.

For production: mms4tk POSTs to its own endpoint which shells out to
`docker exec hermes-agent hermes kanban create ...` on the VPS host,
OR directly inserts into kanban.db via the shared Docker network.

Implementation: We use HTTP POST to a lightweight relay endpoint
that mms4tk exposes, which the VPS host's cron/systemd can pick up.
Alternatively, we write directly to the kanban.db SQLite file
mounted at /docker/hermes-agent/data/kanban.db from the host.

For container-to-container: mms4tk writes a webhook event to its own
SQLite (logs.db) in a `webhook_events` table, and a Hermes cron job
picks up pending events and creates Kanban cards.
"""

import os
import time
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger("mms4tk.hermes_webhook")


class HermesWebhookEvent:
    """Represents a webhook event to be dispatched to Hermes Kanban."""

    def __init__(
        self,
        event_type: str,
        title: str,
        body: str,
        metadata: Optional[Dict[str, Any]] = None,
        initial_status: str = "todo",
        priority: int = 5,
    ):
        self.event_type = event_type
        self.title = title
        self.body = body
        self.metadata = metadata or {}
        self.initial_status = initial_status
        self.priority = priority
        self.timestamp = datetime.now(timezone.utc).isoformat()


class HermesWebhookClient:
    """
    Client for dispatching webhook events to Hermes Agent.

    Strategy: Write events to a `webhook_events` table in mms4tk's logs.db.
    A Hermes cron job (mms4tk_event_dispatcher.py) polls this table
    and creates Kanban cards via `hermes kanban create`.

    This decoupled approach:
    - Survives Hermes container restarts (events persist in mms4tk's DB)
    - Doesn't require direct network access to Hermes internal APIs
    - Uses the established container-to-container HTTP channel
    """

    def __init__(self, db_path: str = ".agents/swarm/logs.db"):
        self.db_path = db_path
        self._init_webhook_table()

    def _init_webhook_table(self) -> None:
        """Create webhook_events table if it doesn't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS webhook_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        title TEXT NOT NULL,
                        body TEXT NOT NULL,
                        metadata TEXT,
                        initial_status TEXT NOT NULL DEFAULT 'todo',
                        priority INTEGER NOT NULL DEFAULT 5,
                        dispatched INTEGER NOT NULL DEFAULT 0,
                        dispatched_at TEXT,
                        kanban_task_id TEXT,
                        error TEXT
                    )
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to init webhook_events table: {e}")

    def emit_event(self, event: HermesWebhookEvent) -> int:
        """
        Write a webhook event to the database for async dispatch.
        Returns the event ID.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO webhook_events
                        (timestamp, event_type, title, body, metadata, initial_status, priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.timestamp,
                        event.event_type,
                        event.title,
                        event.body,
                        json.dumps(event.metadata),
                        event.initial_status,
                        event.priority,
                    )
                )
                conn.commit()
                event_id = cursor.lastrowid or 0
                logger.info(f"Webhook event #{event_id} queued: [{event.event_type}] {event.title}")
                return event_id
        except Exception as e:
            logger.error(f"Failed to emit webhook event: {e}")
            return 0

    def emit_scout_mode_event(
        self,
        symbol: str,
        sl_price: float,
        sl_percent: float,
        reason: str = "",
    ) -> int:
        """
        Emit a SCOUT_MODE activation event for Hermes H4 analysis.
        Creates a Kanban card requesting H4 Liquidity Sweep vs Breakout assessment.
        """
        event = HermesWebhookEvent(
            event_type="SCOUT_MODE_ACTIVATED",
            title=f"H4 Liquidity Sweep Analysis ({symbol} SL {sl_percent*100:.1f}%)",
            body=(
                f"## SCOUT_MODE Activated\n\n"
                f"**Symbol:** {symbol}\n"
                f"**SL Price:** {sl_price}\n"
                f"**SL Percent:** {sl_percent*100:.1f}%\n"
                f"**Reason:** {reason}\n"
                f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n\n"
                f"### Required Analysis\n"
                f"1. Fetch H4 candle data via `mms4_get_higher_tf`\n"
                f"2. Classify: **SWEEP_WICK** (fakeout) or **MARUBOZU_BREAKOUT** (real breakout)\n"
                f"3. If SWEEP_WICK → set `scout_override=True` (relax to BASE_MODE)\n"
                f"4. If MARUBOZU_BREAKOUT → maintain SCOUT_MODE, set `directional_bias` accordingly\n"
                f"5. If uncertain → block this card for Human-in-the-Loop review\n"
            ),
            metadata={
                "symbol": symbol,
                "sl_price": sl_price,
                "sl_percent": sl_percent,
                "trigger": "on_stop_loss_hit",
            },
            initial_status="todo",
            priority=9,
        )
        return self.emit_event(event)

    def emit_stale_position_event(
        self,
        symbol: str,
        action: str,
        candle_count: int,
        entry_price: float,
        current_price: float,
        atr: float,
        side: str,
        is_split: bool = False,
        extra_details: str = "",
    ) -> int:
        """
        Emit a stale position management event for Hermes Kanban + Telegram.

        Actions: SPLIT_50, FORCE_CLOSE_BE, BE_ESCAPE, FORCE_CLOSE
        """
        action_emoji = {
            "SPLIT_50": "✂️",
            "FORCE_CLOSE_BE": "🚨",
            "BE_ESCAPE": "🏃",
            "FORCE_CLOSE": "⚡",
        }
        action_label = {
            "SPLIT_50": "Redukcja 50% (Split)",
            "FORCE_CLOSE_BE": "Zamknięcie po stagnacji (BE/Market)",
            "BE_ESCAPE": "Ucieczka na Break-Even",
            "FORCE_CLOSE": "Zamknięcie (Negacja Impulsu)",
        }
        emoji = action_emoji.get(action, "⚠️")
        label = action_label.get(action, action)

        pnl_pct = 0.0
        if entry_price > 0:
            if side.lower() == "long":
                pnl_pct = ((current_price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - current_price) / entry_price) * 100

        title = f"{emoji} STALE POSITION: {symbol} — {label}"
        body = (
            f"## Zarządzanie Pozycją Stagnacyjną\n\n"
            f"**Symbol:** {symbol}\n"
            f"**Strona:** `{side.upper()}`\n"
            f"**Akcja:** `{action}`\n"
            f"**Świece stagnacji:** `{candle_count}`\n"
            f"**Cena wejścia:** `${entry_price:,.2f}`\n"
            f"**Cena bieżąca:** `${current_price:,.2f}`\n"
            f"**PnL:** `{pnl_pct:+.2f}%`\n"
            f"**ATR:** `${atr:,.2f}`\n"
            f"**Po splicie:** `{'Tak' if is_split else 'Nie'}`\n"
            f"**Timestamp:** {datetime.now(timezone.utc).isoformat()}\n"
        )
        if extra_details:
            body += f"\n### Szczegóły\n{extra_details}\n"

        event = HermesWebhookEvent(
            event_type=f"STALE_POSITION_{action}",
            title=title,
            body=body,
            metadata={
                "symbol": symbol,
                "action": action,
                "side": side,
                "candle_count": candle_count,
                "entry_price": entry_price,
                "current_price": current_price,
                "atr": atr,
                "pnl_pct": round(pnl_pct, 4),
                "is_split": is_split,
                "trigger": "inactivity_manager",
            },
            initial_status="in_progress",
            priority=8,
        )
        event_id = self.emit_event(event)

        # Also dispatch Telegram alert
        HermesWebhookClient.send_telegram(
            title=title,
            body=body,
            priority=4,
            dedupe_key=f"STALE_{symbol}_{action}",
        )

        return event_id

    def emit_take_profit_event(
        self,
        symbol: str,
        closed_side: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        new_side: str = "",
        reason: str = "Opposite TMA Band reached"
    ) -> int:
        """
        Emit a Take Profit event when a position is closed with profit on opposite TMA band.
        Creates a Kanban card and dispatches a Telegram alert.
        """
        pnl_sign = "+" if pnl_pct >= 0 else ""
        title = f"💰 TAKE PROFIT: {symbol} ({closed_side.upper()}) | {pnl_sign}{pnl_pct*100:.2f}%"
        flip_msg = f" ➔ Otwarto pozycję odwróconą {new_side.upper()}." if new_side else ""
        body = (
            f"🎯 Realizacja zysku na przeciwległej wstędze TMA dla {symbol}.\n\n"
            f"• Pozycja zamknięta: {closed_side.upper()}\n"
            f"• Cena wejścia: ${entry_price:,.4f}\n"
            f"• Cena wyjścia (TP): ${exit_price:,.4f}\n"
            f"• Wynik: {pnl_sign}{pnl_pct*100:.2f}%\n"
            f"• Powód: {reason}{flip_msg}\n"
            f"• Czas: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )

        event = HermesWebhookEvent(
            event_type="TAKE_PROFIT_OPPOSITE_BAND",
            title=title,
            body=body,
            metadata={
                "symbol": symbol,
                "closed_side": closed_side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 4),
                "new_side": new_side,
                "reason": reason,
                "trigger": "opposite_band_tp",
            },
            initial_status="done",
            priority=9,
        )
        event_id = self.emit_event(event)

        # Dispatch Telegram alert
        HermesWebhookClient.send_telegram(
            title=title,
            body=body,
            priority=5,
            dedupe_key=f"TP_{symbol}_{int(time.time()/60)}",
        )

        return event_id

    def get_pending_events(self, limit: int = 10) -> list:
        """Retrieve undispatched webhook events."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(
                    "SELECT * FROM webhook_events WHERE dispatched = 0 ORDER BY id ASC LIMIT ?",
                    (limit,)
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get pending events: {e}")
            return []

    def mark_dispatched(self, event_id: int, kanban_task_id: str = "") -> None:
        """Mark an event as dispatched after Kanban card creation."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE webhook_events
                    SET dispatched = 1,
                        dispatched_at = ?,
                        kanban_task_id = ?
                    WHERE id = ?
                    """,
                    (datetime.now(timezone.utc).isoformat(), kanban_task_id, event_id)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark event #{event_id} dispatched: {e}")

    def mark_error(self, event_id: int, error: str) -> None:
        """Record dispatch error for an event."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE webhook_events SET error = ? WHERE id = ?",
                    (error, event_id)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to mark error on event #{event_id}: {e}")

    @staticmethod
    def send_telegram(title: str, body: str, priority: int = 5, dedupe_key: Optional[str] = None) -> bool:
        """Enqueue Telegram alert via TelegramQueueManager with rate-limiting and deduplication."""
        try:
            from mms4tk.telegram_queue import get_telegram_queue
            tq = get_telegram_queue()
            return tq.enqueue(title=title, body=body, priority=priority, dedupe_key=dedupe_key)
        except Exception as e:
            logger.warning(f"Failed to enqueue Telegram alert, falling back to direct send: {e}")
            import subprocess
            notify_msg = f"{title}\n\n{body}"
            hermes_cli = os.getenv("HERMES_CLI_PATH", "/docker/hermes-agent/bin/hermes")
            cmd_candidates = []
            if os.path.exists(hermes_cli):
                cmd_candidates.append([hermes_cli, "send", "--to", "telegram", notify_msg])
            cmd_candidates.append(["docker", "exec", "hermes-agent", "hermes", "send", "--to", "telegram", notify_msg])
            cmd_candidates.append(["hermes", "send", "--to", "telegram", notify_msg])

            for cmd in cmd_candidates:
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    if res.returncode == 0:
                        logger.info("Successfully dispatched Telegram alert via fallback Hermes CLI")
                        return True
                except Exception:
                    continue
            return False

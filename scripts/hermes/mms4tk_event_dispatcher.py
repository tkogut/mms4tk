#!/usr/bin/env python3
"""
mms4tk Event Dispatcher for Hermes Kanban (Cron Script)

Runs as a Hermes cron job (every 1 minute).
Polls mms4tk's /api/v1/hermes/events for pending webhook events
and creates Kanban cards via `hermes kanban create`.

Install via:
    hermes cron create "*/1 * * * *" \
        --name "mms4tk Event Dispatcher" \
        --script mms4tk_event_dispatcher.py \
        --no-agent
"""

import json
import os
import subprocess
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError

def get_api_base_url() -> str:
    """Probe candidate base URLs to reach mms4tk API anywhere."""
    env_url = os.environ.get("MMS4TK_BASE_URL")
    if env_url:
        return env_url

    candidates = [
        "http://127.0.0.1:8501",
        "http://antigravity_core:8000",
        "http://localhost:8501",
    ]
    for url in candidates:
        try:
            req = Request(f"{url}/api/v1/health")
            with urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return url
        except Exception:
            continue
    return "http://127.0.0.1:8501"


MMS4TK_BASE = get_api_base_url()
MMS4TK_API_KEY = os.environ.get("MMS4_API_KEY", "")


def fetch_pending_events():
    """Fetch pending webhook events from mms4tk."""
    try:
        headers = {}
        if MMS4TK_API_KEY:
            headers["X-MMS4-API-KEY"] = MMS4TK_API_KEY
        url = f"{MMS4TK_BASE}/api/v1/hermes/events"
        req = Request(url, headers=headers)
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"ERROR: Failed to fetch events: {e}", file=sys.stderr)
        return []


def create_kanban_card(event):
    """Create a Kanban card via hermes CLI."""
    title = event["title"]
    body = event.get("body", "")
    priority = event.get("priority", 5)
    initial_status = event.get("initial_status", "todo")

    cmd = [
        "hermes", "kanban", "create",
        title,
        "--body", body,
        "--priority", str(priority),
        "--created-by", "mms4tk-webhook",
        "--max-retries", "2",
        "--goal",
        "--json",
    ]

    # If initial_status is 'blocked', create with --initial-status blocked for HITL
    if initial_status == "blocked":
        cmd.extend(["--initial-status", "blocked"])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            try:
                card_data = json.loads(result.stdout)
                task_id = card_data.get("id", "")
                return True, task_id
            except json.JSONDecodeError:
                # Extract task ID from text output
                return True, result.stdout.strip()[:36]
        else:
            print(f"ERROR: hermes kanban create failed: {result.stderr}", file=sys.stderr)
            return False, result.stderr
    except Exception as e:
        print(f"ERROR: Failed to run hermes kanban create: {e}", file=sys.stderr)
        return False, str(e)


def mark_dispatched(event_id, kanban_task_id=""):
    """Mark event as dispatched in mms4tk."""
    try:
        payload = json.dumps({
            "event_id": event_id,
            "kanban_task_id": kanban_task_id
        }).encode()
        req = Request(
            f"{MMS4TK_BASE}/api/v1/hermes/events/dispatched",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-MMS4-API-KEY": MMS4TK_API_KEY,
            },
            method="POST",
        )
        urlopen(req, timeout=10)
    except Exception as e:
        print(f"ERROR: Failed to mark event #{event_id} dispatched: {e}", file=sys.stderr)


def main():
    events = fetch_pending_events()
    if not events:
        return  # Silent when no events (--no-agent cron pattern)

    dispatched_count = 0
    for event in events:
        event_id = event["id"]
        event_type = event.get("event_type", "")
        event_title = event.get("title", "MMS4TK Webhook Event")
        event_body = event.get("body", "")
        print(f"Processing event #{event_id}: {event_title}")

        # Actionable events require Kanban cards for Hermes AI analysis or HITL review
        is_actionable = (
            event_type in ["SCOUT_MODE_ACTIVATED", "HITL_REVIEW", "RISK_ALERT"] or
            event.get("initial_status") in ["blocked", "todo"]
        ) and event_type not in ["POSITION_OPENED", "MULTI_SYMBOL_DIGEST"]

        task_id = "telegram_only"
        if is_actionable:
            success, task_id_or_error = create_kanban_card(event)
            if success:
                task_id = str(task_id_or_error)
                print(f"  📋 Kanban card created (task_id={task_id})")
            else:
                print(f"  ❌ Kanban card creation failed: {task_id_or_error}")

        mark_dispatched(event_id, task_id)
        dispatched_count += 1

        # Send Telegram alert via hermes send --to telegram for actionable/alert events
        if event_type in ["SCOUT_MODE_ACTIVATED", "HITL_REVIEW", "RISK_ALERT"] or is_actionable:
            try:
                alert_msg = f"{event_title}\n\n{event_body}"
                hermes_cli = os.getenv("HERMES_CLI_PATH", "hermes")
                cmd = [hermes_cli, "send", "--to", "telegram", alert_msg]
                subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                print("  💬 Telegram push notification sent")
                import time
                time.sleep(3.0)  # Pacing to respect Telegram rate limit
            except Exception as ne:
                print(f"  ⚠️ Telegram notification skipped: {ne}")

    if dispatched_count > 0:
        print(f"\n📋 Processed {dispatched_count}/{len(events)} webhook events")


if __name__ == "__main__":
    main()

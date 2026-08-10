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

MMS4TK_BASE = os.environ.get("MMS4TK_BASE_URL", "http://antigravity_core:8000")
MMS4TK_API_KEY = os.environ.get("MMS4_API_KEY", "mms4tk-secret-key-2026")


def fetch_pending_events():
    """Fetch pending webhook events from mms4tk."""
    try:
        url = f"{MMS4TK_BASE}/api/v1/hermes/events"
        resp = urlopen(url, timeout=10)
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
        print(f"Dispatching event #{event_id}: {event['title']}")

        success, task_id_or_error = create_kanban_card(event)
        if success:
            mark_dispatched(event_id, str(task_id_or_error))
            dispatched_count += 1
            print(f"  ✅ Card created (task_id={task_id_or_error})")
        else:
            print(f"  ❌ Failed: {task_id_or_error}")

    if dispatched_count > 0:
        print(f"\n📋 Dispatched {dispatched_count}/{len(events)} events to Hermes Kanban")


if __name__ == "__main__":
    main()

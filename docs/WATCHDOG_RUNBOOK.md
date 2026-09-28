# Daemon Liveness Watchdog — Operator Runbook

Covers `scripts/hermes/mms4tk_daemon_liveness_watchdog.py` (issue #68, part of
the 2026-09-28 XAG Incident Postmortem protection-gaps audit).

## What it does

Runs every minute, outside `antigravity_core`, as a Hermes cron job. It checks
`/api/v1/daemon/status` for `last_cycle_timestamp` and, if the daemon's own
trading loop looks stalled or the container is unreachable, sends a Telegram
alert — but only when at least one open position has significant floating
profit (default floor: $1, `MMS4TK_WATCHDOG_MIN_PROFIT_USD`). It computes and
includes a suggested manual Stop-Loss price using the same 38.2%/50%
Fibonacci "Impulse Negation" formula the bot's own in-memory trailing stop
uses (`inactivity_manager.py`), so a human can act with a real number instead
of guessing.

It persists its last known state to a file on the host
(`MMS4TK_WATCHDOG_STATE`, default `/opt/data/state/mms4tk_watchdog_state.json`)
on every run — this is deliberate: the suggested price must still be
available at the exact moment the API stops answering, which is precisely
when the alert fires.

## What it does NOT do

- It does not touch the exchange. It never places, moves, or cancels any
  order — it only computes a number and sends a message.
- It does not survive a total VPS outage (it runs on the same VPS as the bot).
  The only protection that survives that is a real, resting order on OKX —
  see issue #69 (ratchet the exchange-side SL after a protective split).
- It does not replace `/api/v1/emergency/killall` (issue #67) — it tells a
  human there's a problem and gives them a number; it does not stop trading
  by itself.

## When you get the alert

1. Read the suggested SL price and the symbol/side in the message.
2. Open OKX → the position in question → set (or move) the Stop-Loss
   conditional order to the suggested price. If OKX already shows a SL closer
   to the current price than the suggestion, leave it — never move a SL
   away from profit based on this alert.
3. Check whether `antigravity_core` is actually down:
   ```bash
   docker compose -f /docker/mms4tk/docker-compose.yml ps
   docker logs antigravity_core --since 10m
   ```
4. If the container is dead, restart it (`docker compose up -d --build` or
   `docker restart antigravity_core`). Restarting resets the bot's own
   in-memory trailing-stop tracker (see issue #71) — the manual SL you just
   set on the exchange is what carries you through that reset.
5. If the container is alive but the loop looks hung, capture logs before
   restarting — a hung-but-alive process is a different failure mode than a
   crash and worth its own investigation.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `MMS4TK_BASE_URL` | `http://antigravity_core:8000` | Where to reach the mms4tk API from the watchdog's own container/host. |
| `MMS4_API_KEY` | (from `.env`) | Same key the rest of the Hermes cron scripts use. |
| `MMS4TK_WATCHDOG_STATE` | `/opt/data/state/mms4tk_watchdog_state.json` | Host-persisted state file — must be on a path that survives `antigravity_core` restarting/dying. |
| `MMS4TK_WATCHDOG_STALE_SECONDS` | `180` | How old `last_cycle_timestamp` must be before the daemon is considered stalled. |
| `MMS4TK_WATCHDOG_MIN_PROFIT_USD` | `1.0` | Floating-profit floor below which a stalled daemon isn't worth alerting on. |

## Install

```bash
hermes cron create "* * * * *" \
    --name "mms4tk Daemon Liveness Watchdog" \
    --script mms4tk_daemon_liveness_watchdog.py \
    --no-agent
```

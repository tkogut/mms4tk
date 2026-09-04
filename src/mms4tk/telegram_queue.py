"""
Telegram Queue & Rate Limiter Module (telegram_queue.py)

Manages outbound Telegram message queuing, rate-limiting (throttling),
and deduplication to prevent flood and duplicate alerts.

Design: Uses a thread-safe stdlib queue (not asyncio.PriorityQueue) so it
can be safely enqueued from both sync and async contexts. A background
thread drains the queue and dispatches directly via the Telegram Bot API.

2026-09-04 fix: dispatch previously ONLY shelled out to an external "hermes" CLI
(local binary path, `docker exec hermes-agent`, or PATH lookup) — all three are
structurally unreachable from inside this container (no `docker` binary, no
docker socket mounted, no such binary present), so every alert had been silently
failing since deployment (confirmed: both an entry and a stop-loss notification
failed the same day). Dispatch now calls `api.telegram.org` directly first, using
`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (already present as env vars) — the
old CLI candidates are kept as a fallback only, never as the primary path.
"""

import os
import time
import hashlib
import logging
import subprocess
import threading
import queue as stdlib_queue
from typing import Optional, Dict
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("mms4tk.telegram_queue")


@dataclass(order=True)
class QueuedMessage:
    priority: int  # Lower number = higher priority (e.g. 1=CRITICAL, 5=NORMAL, 10=DIGEST)
    timestamp: float = field(compare=False)
    title: str = field(compare=False)
    body: str = field(compare=False)
    dedupe_key: Optional[str] = field(default=None, compare=False)


class TelegramQueueManager:
    """
    Thread-safe Telegram message queue with rate limiting and deduplication.
    Uses stdlib PriorityQueue (not asyncio) so it works from any call context.
    A background daemon thread drains the queue and dispatches via Hermes CLI.
    """

    def __init__(self, min_interval_seconds: float = 3.0, dedupe_ttl_seconds: float = 60.0) -> None:
        self.min_interval: float = min_interval_seconds
        self.dedupe_ttl: float = dedupe_ttl_seconds
        self._queue: stdlib_queue.PriorityQueue = stdlib_queue.PriorityQueue()
        self._recent_hashes: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_send_time: float = 0.0
        self._worker_thread: Optional[threading.Thread] = None
        self._is_running: bool = False
        self._start_worker()

    def _start_worker(self) -> None:
        """Start background drain thread (daemon=True so it exits with the process)."""
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._is_running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="TelegramQueueWorker",
            daemon=True
        )
        self._worker_thread.start()
        logger.info("TelegramQueueManager worker thread started.")

    async def ensure_started(self) -> None:
        """Async no-op compatibility shim for api.py lifespan. Worker starts in __init__."""
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._start_worker()
        logger.info("TelegramQueueManager ensure_started() called — worker thread active.")

    def _clean_expired_hashes(self) -> None:
        """Remove dedupe hashes older than TTL."""
        now = time.time()
        with self._lock:
            expired = [k for k, ts in self._recent_hashes.items() if (now - ts) > self.dedupe_ttl]
            for k in expired:
                del self._recent_hashes[k]

    def _generate_hash(self, title: str, body: str, dedupe_key: Optional[str] = None) -> str:
        """Create a fingerprint for deduplication."""
        if dedupe_key:
            return f"key:{dedupe_key}"
        raw = f"{title.strip()}:{body.strip()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def enqueue(
        self,
        title: str,
        body: str,
        priority: int = 5,
        dedupe_key: Optional[str] = None,
        ttl_override: Optional[float] = None
    ) -> bool:
        """
        Enqueue a message for rate-limited dispatch.
        Returns True if enqueued, False if dropped by deduplication.
        Thread-safe — can be called from sync or async contexts.
        """
        self._clean_expired_hashes()
        h = self._generate_hash(title, body, dedupe_key)
        now = time.time()

        with self._lock:
            # Check deduplication
            if h in self._recent_hashes:
                elapsed = now - self._recent_hashes[h]
                ttl = ttl_override if ttl_override is not None else self.dedupe_ttl
                if elapsed < ttl:
                    logger.info(f"Telegram alert suppressed by deduplication filter (key: {dedupe_key or title[:25]})")
                    return False

            self._recent_hashes[h] = now

        msg = QueuedMessage(
            priority=priority,
            timestamp=now,
            title=title,
            body=body,
            dedupe_key=dedupe_key
        )
        self._queue.put(msg)
        logger.debug(f"Enqueued Telegram message: {title[:40]}... (Queue depth: ~{self._queue.qsize()})")

        # Ensure worker is alive (self-healing)
        if self._worker_thread is None or not self._worker_thread.is_alive():
            logger.warning("TelegramQueueManager worker was dead — restarting.")
            self._start_worker()

        return True

    def _send_via_bot_api(self, notify_msg: str) -> bool:
        """Dispatch directly via `api.telegram.org` — no subprocess, no dependency
        on another container's CLI/binary being reachable from this one. Requires
        `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (both plain env vars, R-SEC-01:
        read via os.getenv only, never logged — even a failure response body is
        truncated before logging so a token accidentally echoed by Telegram's own
        error message can't leak into the logs)."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("chat_id")
        if not token or not chat_id:
            logger.debug("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not configured — skipping direct Bot API dispatch.")
            return False
        try:
            resp = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": notify_msg[:4096]},  # Telegram's own message length cap
                timeout=10.0,
            )
            if resp.status_code == 200 and resp.json().get("ok"):
                return True
            logger.debug(f"Telegram Bot API dispatch failed (status={resp.status_code}): {resp.text[:120]}")
            return False
        except Exception as e:
            logger.debug(f"Telegram Bot API dispatch raised: {type(e).__name__}: {e}")
            return False

    def _send_now(self, title: str, body: str) -> bool:
        """Synchronously dispatch delivery — direct Telegram Bot API first, then the
        legacy Hermes CLI candidates as a fallback (kept in case a future
        environment makes them reachable again; they are NOT expected to work in
        the current container setup — see module docstring)."""
        notify_msg = f"{title}\n\n{body}".strip()

        if self._send_via_bot_api(notify_msg):
            logger.info(f"Telegram alert dispatched via Bot API: {title[:40]}")
            self._last_send_time = time.time()
            return True

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
                    logger.info(f"Telegram alert dispatched: {title[:40]}")
                    self._last_send_time = time.time()
                    return True
                else:
                    logger.debug(f"Hermes CLI cmd {cmd[0]} failed (rc={res.returncode}): {res.stderr.strip()[:80]}")
            except FileNotFoundError:
                logger.debug(f"Hermes CLI not found at {cmd[0]}, trying next candidate.")
            except Exception as e:
                logger.debug(f"Telegram dispatch attempt failed: {e}")
                continue

        logger.warning(f"All Telegram dispatch candidates failed for: {title[:40]}")
        return False

    def _worker_loop(self) -> None:
        """Background thread: drain queue with rate limiting."""
        logger.info("Telegram queue worker loop active.")
        while self._is_running:
            try:
                msg: QueuedMessage = self._queue.get(timeout=5.0)
                now = time.time()
                time_since_last = now - self._last_send_time

                # Enforce pacing interval
                if time_since_last < self.min_interval:
                    time.sleep(self.min_interval - time_since_last)

                self._send_now(msg.title, msg.body)
                self._queue.task_done()

            except stdlib_queue.Empty:
                # No messages — keep looping
                continue
            except Exception as e:
                logger.error(f"Error in Telegram worker loop: {e}")
                time.sleep(1.0)

    def stop(self) -> None:
        """Signal worker to stop (non-blocking)."""
        self._is_running = False


# Global Singleton Instance
_global_telegram_queue: Optional[TelegramQueueManager] = None
_singleton_lock = threading.Lock()


def get_telegram_queue() -> TelegramQueueManager:
    """Return singleton TelegramQueueManager instance."""
    global _global_telegram_queue
    if _global_telegram_queue is None:
        with _singleton_lock:
            if _global_telegram_queue is None:
                _global_telegram_queue = TelegramQueueManager()
    return _global_telegram_queue

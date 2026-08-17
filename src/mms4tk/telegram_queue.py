"""
Telegram Queue & Rate Limiter Module (telegram_queue.py)

Manages outbound Telegram message queuing, rate-limiting (throttling),
and deduplication to prevent flood and duplicate alerts.

Design: Uses a thread-safe stdlib queue (not asyncio.PriorityQueue) so it
can be safely enqueued from both sync and async contexts. A background
thread drains the queue and dispatches via Hermes CLI.
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

    def _send_now(self, title: str, body: str) -> bool:
        """Synchronously dispatch delivery via Hermes CLI."""
        notify_msg = f"{title}\n\n{body}".strip()
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

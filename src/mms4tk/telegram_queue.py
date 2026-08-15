"""
Telegram Queue & Rate Limiter Module (telegram_queue.py)

Manages outbound Telegram message queuing, rate-limiting (throttling),
and deduplication to prevent flood and duplicate alerts.
"""

import os
import time
import asyncio
import hashlib
import logging
import subprocess
from typing import Optional, Dict, Any
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
    Asynchronous Telegram message queue with rate limiting and deduplication.
    Ensures safe pacing (default min 3.0s between sends) and deduplication.
    """

    def __init__(self, min_interval_seconds: float = 3.0, dedupe_ttl_seconds: float = 60.0) -> None:
        self.min_interval: float = min_interval_seconds
        self.dedupe_ttl: float = dedupe_ttl_seconds
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._recent_hashes: Dict[str, float] = {}
        self._worker_task: Optional[asyncio.Task] = None
        self._is_running: bool = False
        self._last_send_time: float = 0.0

    def start(self) -> None:
        """Start the background worker if in an active event loop."""
        if self._is_running:
            return
        try:
            loop = asyncio.get_running_loop()
            self._is_running = True
            self._worker_task = loop.create_task(self._worker_loop())
            logger.info("TelegramQueueManager worker started successfully.")
        except RuntimeError:
            logger.debug("No active event loop found; worker will start upon first async call.")

    async def ensure_started(self) -> None:
        """Ensure background worker loop is running."""
        if not self._is_running or self._worker_task is None or self._worker_task.done():
            self._is_running = True
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("TelegramQueueManager worker initialized.")

    def _clean_expired_hashes(self) -> None:
        """Remove dedupe hashes older than TTL."""
        now = time.time()
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
        """
        self._clean_expired_hashes()
        h = self._generate_hash(title, body, dedupe_key)
        now = time.time()

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

        try:
            self._queue.put_nowait(msg)
            logger.debug(f"Enqueued Telegram message: {title[:30]}... (Queue depth: {self._queue.qsize()})")
        except Exception as e:
            logger.warning(f"Failed to enqueue Telegram message: {e}")
            # Fallback to direct synchronous delivery if queue unavailable
            self._send_now(title, body)
            return True

        # If worker is not running, ensure it starts
        try:
            loop = asyncio.get_running_loop()
            if not self._is_running or self._worker_task is None or self._worker_task.done():
                self._is_running = True
                self._worker_task = loop.create_task(self._worker_loop())
        except RuntimeError:
            pass

        return True

    def _send_now(self, title: str, body: str) -> bool:
        """Synchronously execute delivery via Hermes CLI."""
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
                    logger.info("Successfully dispatched Telegram alert via Hermes CLI")
                    self._last_send_time = time.time()
                    return True
            except Exception:
                continue

        logger.warning("All candidate commands to send Telegram alert failed.")
        return False

    async def _worker_loop(self) -> None:
        """Continuously process queued messages with rate limiting."""
        logger.info("Telegram queue worker loop active.")
        while self._is_running:
            try:
                msg: QueuedMessage = await self._queue.get()
                now = time.time()
                time_since_last = now - self._last_send_time

                # Enforce pacing interval
                if time_since_last < self.min_interval:
                    wait_sec = self.min_interval - time_since_last
                    await asyncio.sleep(wait_sec)

                # Send message in threadpool to avoid blocking async loop
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._send_now, msg.title, msg.body)
                self._last_send_time = time.time()
                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Telegram worker loop: {e}")
                await asyncio.sleep(1.0)

    async def stop(self) -> None:
        """Gracefully stop queue worker."""
        self._is_running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass


# Global Singleton Instance
_global_telegram_queue: Optional[TelegramQueueManager] = None


def get_telegram_queue() -> TelegramQueueManager:
    """Return singleton TelegramQueueManager instance."""
    global _global_telegram_queue
    if _global_telegram_queue is None:
        _global_telegram_queue = TelegramQueueManager()
    return _global_telegram_queue

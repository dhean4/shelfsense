"""A small job queue on Redis Streams with consumer groups.

Why not a library: the two async candidates pin ``redis<6`` or bring their own
serialisation and scheduler. We need enqueue, at-least-once delivery, retries and a
dead-letter stream; Streams give that natively (``XADD`` / ``XREADGROUP`` / ``XACK`` /
``XAUTOCLAIM``). Handlers are plain async functions keyed by job name.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]

STREAM = "shelfsense:jobs"
DEAD_LETTER = "shelfsense:jobs:dead"
GROUP = "workers"
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Job:
    """One dequeued message."""

    message_id: str
    name: str
    payload: dict[str, Any]
    attempts: int


class JobQueue:
    """Producer and consumer over one stream."""

    def __init__(self, redis_url: str, *, stream: str = STREAM, group: str = GROUP) -> None:
        """Connect lazily to ``redis_url``."""
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._stream = stream
        self._group = group
        self._dead = f"{stream}:dead"
        self.handlers: dict[str, Handler] = {}

    async def close(self) -> None:
        """Release the connection pool."""
        await self._redis.aclose()

    # --- producer -----------------------------------------------------------------------

    async def enqueue(self, name: str, payload: dict[str, Any]) -> str:
        """Append a job; returns the stream message id."""
        message_id = await self._redis.xadd(
            self._stream, {"name": name, "payload": json.dumps(payload), "attempts": "0"}
        )
        return str(message_id)

    @staticmethod
    def _job(message_id: Any, fields: Any) -> Job:
        """Build a :class:`Job` from a raw stream entry (redis-py types these loosely)."""
        return Job(
            str(message_id),
            str(fields["name"]),
            json.loads(fields["payload"]),
            int(fields.get("attempts", "0")),
        )

    # --- consumer -----------------------------------------------------------------------

    async def ensure_group(self) -> None:
        """Create the consumer group once; tolerate it already existing."""
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def run(self, consumer: str, *, stop: asyncio.Event, block_ms: int = 2000) -> None:
        """Consume until ``stop`` is set. Each job is handled, acked, or dead-lettered."""
        await self.ensure_group()
        while not stop.is_set():
            await self._reclaim_stale(consumer)
            entries: Any = await self._redis.xreadgroup(
                self._group, consumer, {self._stream: ">"}, count=1, block=block_ms
            )
            for _stream, messages in entries or []:
                for message_id, fields in messages:
                    await self._handle(self._job(message_id, fields))

    async def process_one(self, consumer: str = "inline") -> bool:
        """Handle at most one pending job now (tests and CLI). Returns whether one ran."""
        await self.ensure_group()
        entries: Any = await self._redis.xreadgroup(
            self._group, consumer, {self._stream: ">"}, count=1, block=None
        )
        for _stream, messages in entries or []:
            for message_id, fields in messages:
                await self._handle(self._job(message_id, fields))
                return True
        return False

    async def _handle(self, job: Job) -> None:
        handler = self.handlers.get(job.name)
        if handler is None:
            log.error("no handler for job %s; dead-lettering", job.name)
            await self._dead_letter(job, "no handler")
            return
        try:
            await handler(job.payload)
        except Exception as exc:
            log.exception("job %s failed (attempt %d)", job.name, job.attempts + 1)
            await self._retry_or_dead_letter(job, repr(exc))
            return
        await self._redis.xack(self._stream, self._group, job.message_id)

    async def _retry_or_dead_letter(self, job: Job, error: str) -> None:
        attempts = job.attempts + 1
        await self._redis.xack(self._stream, self._group, job.message_id)
        if attempts >= MAX_ATTEMPTS:
            await self._dead_letter(job, error, attempts)
            return
        await self._redis.xadd(
            self._stream,
            {"name": job.name, "payload": json.dumps(job.payload), "attempts": str(attempts)},
        )

    async def _dead_letter(self, job: Job, error: str, attempts: int | None = None) -> None:
        await self._redis.xack(self._stream, self._group, job.message_id)
        await self._redis.xadd(
            self._dead,
            {
                "name": job.name,
                "payload": json.dumps(job.payload),
                "attempts": str(attempts if attempts is not None else job.attempts),
                "error": error,
            },
        )

    async def _reclaim_stale(self, consumer: str, idle_ms: int = 60_000) -> None:
        """Take over messages a crashed worker left pending for over ``idle_ms``."""
        try:
            result: Any = await self._redis.xautoclaim(
                self._stream, self._group, consumer, min_idle_time=idle_ms, count=10
            )
        except ResponseError:
            return
        _next, claimed, _deleted = result
        for message_id, fields in claimed:
            await self._handle(self._job(message_id, fields))

    # --- introspection ------------------------------------------------------------------

    async def dead_letters(self, count: int = 100) -> list[dict[str, Any]]:
        """Most recent dead-lettered jobs."""
        rows: Any = await self._redis.xrevrange(self._dead, count=count)
        return [{"id": str(message_id), **dict(fields)} for message_id, fields in rows]

    async def pending_count(self) -> int:
        """Messages delivered but not yet acked."""
        try:
            info = await self._redis.xpending(self._stream, self._group)
        except ResponseError:
            return 0
        pending: int = info["pending"]
        return pending

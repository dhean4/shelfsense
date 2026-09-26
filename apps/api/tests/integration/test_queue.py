"""The Redis Streams job queue: delivery, retries, dead letters."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from shelfsense_api.queue import MAX_ATTEMPTS, JobQueue

pytestmark = pytest.mark.integration


@pytest.fixture
async def queue(redis_url: str) -> AsyncIterator[JobQueue]:
    q = JobQueue(redis_url, stream=f"test:jobs:{uuid4().hex}", group="g")
    yield q
    await q.close()


async def test_enqueue_then_process_one_runs_the_handler(queue: JobQueue) -> None:
    seen: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        seen.append(payload)

    queue.handlers["echo"] = handler
    await queue.enqueue("echo", {"n": 1})
    assert await queue.process_one() is True
    assert await queue.process_one() is False
    assert seen == [{"n": 1}]
    assert await queue.pending_count() == 0


async def test_failing_handler_is_retried_then_dead_lettered(queue: JobQueue) -> None:
    calls = 0

    async def handler(payload: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    queue.handlers["flaky"] = handler
    await queue.enqueue("flaky", {"k": "v"})
    for _ in range(MAX_ATTEMPTS):
        assert await queue.process_one() is True
    assert await queue.process_one() is False
    assert calls == MAX_ATTEMPTS
    dead = await queue.dead_letters()
    assert len(dead) == 1
    assert dead[0]["name"] == "flaky"
    assert dead[0]["attempts"] == str(MAX_ATTEMPTS)
    assert "boom" in dead[0]["error"]


async def test_unknown_job_name_is_dead_lettered(queue: JobQueue) -> None:
    await queue.enqueue("nobody-handles-this", {})
    assert await queue.process_one() is True
    dead = await queue.dead_letters()
    assert dead[0]["error"] == "no handler"

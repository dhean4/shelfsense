"""Record real responses to JSON fixtures, and replay them offline.

Fixtures are keyed by :meth:`LLMRequest.fingerprint`, so a prompt or schema change
produces a miss, which is the signal to re-record. Recording needs an API key and money;
replaying needs neither, which is what keeps the unit suite and CI honest and free.
"""

import json
from pathlib import Path
from typing import Any

from shelfsense_api.llm.provider import LLMError, LLMProvider
from shelfsense_api.llm.types import LLMRequest, LLMResponse


def _fixture_path(directory: Path, request: LLMRequest) -> Path:
    return directory / f"{request.fingerprint()}.json"


def _describe(request: LLMRequest) -> dict[str, Any]:
    """Human-readable context stored next to the response so fixtures are reviewable."""
    return {
        "model": request.model,
        "effort": request.effort,
        "metadata": request.metadata,
        "system_head": request.system[:200],
        "last_user_text": next(
            (
                p.text
                for m in reversed(request.messages)
                if m.role == "user"
                for p in m.parts
                if p.type == "text"
            ),
            "",
        )[:500],
    }


class ReplayProvider:
    """Serves recorded responses; a miss is an error, never a silent fake."""

    name = "replay"

    def __init__(self, directory: Path) -> None:
        """Read fixtures from ``directory``."""
        self._dir = directory

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the recorded response for this exact request."""
        path = _fixture_path(self._dir, request)
        if not path.exists():
            raise LLMError(
                f"no recorded fixture for request {request.fingerprint()[:12]} "
                f"({request.metadata}); run `make record-fixtures` with an API key",
                retryable=False,
            )
        payload = json.loads(path.read_text())
        return LLMResponse.model_validate(payload["response"])


class RecordingProvider:
    """Wraps a live provider and writes every response to a fixture file."""

    name = "recording"

    def __init__(self, inner: LLMProvider, directory: Path) -> None:
        """Delegate to ``inner`` and write into ``directory``."""
        self._inner = inner
        self._dir = directory
        self.name = inner.name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Call through, then persist the response with a description of the request."""
        response = await self._inner.complete(request)
        self._dir.mkdir(parents=True, exist_ok=True)
        path = _fixture_path(self._dir, request)
        path.write_text(
            json.dumps(
                {"request": _describe(request), "response": response.model_dump()},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        return response

"""A scripted provider for integration tests: deterministic, offline, inspectable."""

from collections.abc import Callable
from typing import Any

from shelfsense_api.llm.provider import LLMError
from shelfsense_api.llm.types import LLMRequest, LLMResponse, ToolCall, Usage

Script = Callable[[LLMRequest], LLMResponse]


class FakeProvider:
    """Answers from a queue of canned responses or a scripting function."""

    name = "fake"

    def __init__(self) -> None:
        """Start with nothing scripted; tests push responses or set ``script``."""
        self.requests: list[LLMRequest] = []
        self.queue: list[LLMResponse | Exception] = []
        self.script: Script | None = None

    def push_text(self, text: str, *, model: str = "fake-model") -> None:
        """Queue a plain text (or JSON text) reply."""
        self.queue.append(
            LLMResponse(
                model=model,
                text=text,
                stop_reason="end_turn",
                usage=Usage(input_tokens=100, output_tokens=50),
                latency_ms=1,
                provider=self.name,
            )
        )

    def push_tool_calls(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        text: str = "",
        model: str = "fake-model",
        usage: Usage | None = None,
    ) -> None:
        """Queue a turn in which the model calls ``calls`` (name, input) in parallel."""
        n = sum(len(r.tool_calls) for r in self.queue if isinstance(r, LLMResponse))
        self.queue.append(
            LLMResponse(
                model=model,
                text=text,
                tool_calls=[
                    ToolCall(id=f"call_{n + i}", name=name, input=args)
                    for i, (name, args) in enumerate(calls)
                ],
                stop_reason="tool_use",
                usage=usage or Usage(input_tokens=200, output_tokens=80),
                latency_ms=1,
                provider=self.name,
            )
        )

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Record the request and serve the next scripted answer."""
        self.requests.append(request)
        if self.script is not None:
            return self.script(request)
        if not self.queue:
            raise LLMError("fake provider has no scripted response left", retryable=False)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

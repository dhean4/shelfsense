"""A provider wrapper that emits a Langfuse generation and Prometheus metrics per call.

Provider-neutral: the same wrapper traces the Anthropic client, replayed fixtures and the
fake, so a trace looks the same in every environment. Images are never sent to the trace;
text is scrubbed and truncated.
"""

import time
from typing import Any

from shelfsense_api.llm.pricing import Price, cost_usd
from shelfsense_api.llm.provider import LLMError, LLMProvider
from shelfsense_api.llm.types import LLMRequest, LLMResponse, TextPart, ToolResultPart, ToolUsePart
from shelfsense_api.observability import LLM_CALLS, LLM_COST, LLM_LATENCY, LLM_TOKENS, observation
from shelfsense_api.pii import scrub

MAX_TEXT = 4_000


def _trace_input(request: LLMRequest) -> dict[str, Any]:
    """What we record as the generation's input: text only, scrubbed, bounded."""
    turns: list[dict[str, Any]] = []
    for message in request.messages:
        parts: list[Any] = []
        for part in message.parts:
            if isinstance(part, TextPart):
                parts.append(scrub(part.text)[:MAX_TEXT])
            elif isinstance(part, ToolUsePart):
                parts.append({"tool_use": part.name, "input": part.input})
            elif isinstance(part, ToolResultPart):
                parts.append({"tool_result": part.tool_use_id, "content": part.content[:MAX_TEXT]})
            else:
                parts.append({"image": part.media_type, "bytes": len(part.data)})
        turns.append({"role": message.role, "content": parts})
    return {
        "system": request.system[:MAX_TEXT],
        "messages": turns,
        "tools": [t.name for t in request.tools],
    }


class TracedProvider:
    """Wraps any :class:`LLMProvider`."""

    def __init__(self, inner: LLMProvider, prices: dict[str, Price]) -> None:
        """Delegate to ``inner`` and price with ``prices``."""
        self.inner = inner
        self._prices = prices
        self.name = inner.name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Call through, recording a generation observation and metrics either way."""
        agent = request.metadata.get("agent", "unknown")
        started = time.perf_counter()
        with observation(
            f"{agent}.generate",
            as_type="generation",
            input=_trace_input(request),
            metadata={k: v for k, v in request.metadata.items() if k != "agent"},
            model=request.model,
            model_parameters={"effort": request.effort, "max_tokens": request.max_tokens},
        ) as span:
            try:
                response = await self.inner.complete(request)
            except LLMError as exc:
                LLM_CALLS.labels(agent, request.model, "error").inc()
                LLM_LATENCY.labels(agent).observe(time.perf_counter() - started)
                span.update(level="ERROR", status_message=str(exc)[:500])
                raise
            cost = cost_usd(response.model, response.usage, self._prices)
            usage = response.usage
            LLM_CALLS.labels(agent, response.model, "ok").inc()
            LLM_TOKENS.labels(agent, response.model, "input").inc(usage.input_tokens)
            LLM_TOKENS.labels(agent, response.model, "output").inc(usage.output_tokens)
            LLM_TOKENS.labels(agent, response.model, "cache_read").inc(usage.cache_read_tokens)
            LLM_LATENCY.labels(agent).observe(response.latency_ms / 1000)
            if cost is not None:
                LLM_COST.labels(agent, response.model).inc(cost)
            span.update(
                output={
                    "text": response.text[:MAX_TEXT],
                    "tool_calls": [{"name": c.name, "input": c.input} for c in response.tool_calls],
                    "stop_reason": response.stop_reason,
                },
                model=response.model,
                usage_details={
                    "input": usage.input_tokens,
                    "output": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_tokens,
                    "cache_creation_input_tokens": usage.cache_write_tokens,
                },
                cost_details={"total": cost} if cost is not None else None,
                metadata={"latency_ms": response.latency_ms, "provider": response.provider},
            )
            return response

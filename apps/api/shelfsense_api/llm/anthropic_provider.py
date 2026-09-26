"""Anthropic implementation of :class:`LLMProvider` on the official async SDK.

Structured output uses ``output_config.format`` (json_schema), so the reply text is
guaranteed to parse against the request's schema. Thinking is left adaptive (the model's
default) and depth is controlled by ``effort``. Refusal fallbacks are on so a
safety-classifier decline is retried server-side on an alternate model.
"""

import base64
import time
from typing import Any

import anthropic

from shelfsense_api.llm.provider import LLMError
from shelfsense_api.llm.types import (
    ImagePart,
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolResultPart,
    ToolUsePart,
    Usage,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _content(message: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, ImagePart):
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": part.media_type,
                        "data": base64.standard_b64encode(part.data).decode("ascii"),
                    },
                }
            )
        elif isinstance(part, ToolUsePart):
            blocks.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.input}
            )
        elif isinstance(part, ToolResultPart):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": part.tool_use_id,
                    "content": part.content,
                    "is_error": part.is_error,
                }
            )
        else:
            blocks.append({"type": "text", "text": part.text})
    return blocks


class AnthropicProvider:
    """Calls ``POST /v1/messages`` through :class:`anthropic.AsyncAnthropic`."""

    name = "anthropic"

    def __init__(self, client: anthropic.AsyncAnthropic | None = None) -> None:
        """Use ``client`` or build one from ``ANTHROPIC_API_KEY``."""
        self._client = client or anthropic.AsyncAnthropic()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """One non-streaming call. Structured output when ``output_schema`` is set."""
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [
                {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": m.role, "content": _content(m)} for m in request.messages],
            "output_config": {"effort": request.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if request.output_schema is not None:
            kwargs["output_config"]["format"] = {
                "type": "json_schema",
                "schema": request.output_schema,
            }
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "strict": True,
                }
                for t in request.tools
            ]

        started = time.perf_counter()
        try:
            message = await self._client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise LLMError(f"rate limited: {exc.message}", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"api error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"connection error: {exc}", retryable=True) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMError(f"model refused (category={category})", retryable=False)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input or {}))
                )

        usage = message.usage
        return LLMResponse(
            model=message.model,
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=message.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
            latency_ms=latency_ms,
            provider=self.name,
            request_id=message._request_id,
        )

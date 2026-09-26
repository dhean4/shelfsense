"""Request and response shapes shared by every provider. Plain Pydantic, no SDK types."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class TextPart(BaseModel):
    """A text segment of a message."""

    type: Literal["text"] = "text"
    text: str


class ImagePart(BaseModel):
    """An inline image. ``data`` is raw bytes; providers base64-encode as needed."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: Literal["image"] = "image"
    media_type: Literal["image/jpeg", "image/png", "image/webp"]
    data: bytes = Field(repr=False)


Part = TextPart | ImagePart


class Message(BaseModel):
    """One conversation turn."""

    role: Literal["user", "assistant"]
    parts: list[Part]

    @classmethod
    def user(cls, *parts: Part | str) -> "Message":
        """Build a user turn from parts or plain strings."""
        return cls(
            role="user", parts=[TextPart(text=p) if isinstance(p, str) else p for p in parts]
        )

    @classmethod
    def assistant(cls, text: str) -> "Message":
        """Build an assistant turn holding text."""
        return cls(role="assistant", parts=[TextPart(text=text)])


class ToolSpec(BaseModel):
    """A tool the model may call. ``input_schema`` is JSON Schema."""

    name: str
    description: str
    input_schema: dict[str, Any]


class LLMRequest(BaseModel):
    """Everything a provider needs to produce one completion."""

    model: str
    system: str
    messages: list[Message]
    max_tokens: int = 8_000
    effort: Effort = "medium"
    output_schema: dict[str, Any] | None = Field(
        default=None, description="When set, the reply is JSON conforming to this schema."
    )
    tools: list[ToolSpec] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict, description="Not sent; for traces.")

    def fingerprint(self) -> str:
        """Stable hash of the request, with image bytes replaced by their own hash.

        Used to key recorded fixtures, so it must not include anything volatile.
        """
        canonical = {
            "model": self.model,
            "system": self.system,
            "max_tokens": self.max_tokens,
            "effort": self.effort,
            "output_schema": self.output_schema,
            "tools": [t.model_dump() for t in self.tools],
            "messages": [
                {
                    "role": m.role,
                    "parts": [
                        {"type": "text", "text": p.text}
                        if isinstance(p, TextPart)
                        else {
                            "type": "image",
                            "media_type": p.media_type,
                            "sha256": hashlib.sha256(p.data).hexdigest(),
                        }
                        for p in m.parts
                    ],
                }
                for m in self.messages
            ],
        }
        blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()


class Usage(BaseModel):
    """Token accounting for one call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class ToolCall(BaseModel):
    """A tool invocation the model asked for."""

    id: str
    name: str
    input: dict[str, Any]


class LLMResponse(BaseModel):
    """What came back, normalised across providers."""

    model: str
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str
    usage: Usage
    latency_ms: int
    provider: str
    request_id: str | None = None

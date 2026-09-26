"""Provider-neutral LLM layer.

Agents build an :class:`LLMRequest` and receive an :class:`LLMResponse`; they never import
a vendor SDK. :func:`get_provider` picks the implementation from settings: the Anthropic
SDK, a replay of recorded fixtures (tests), or scripted fakes (integration tests).
"""

from shelfsense_api.llm.provider import LLMProvider, get_provider
from shelfsense_api.llm.types import (
    ImagePart,
    LLMRequest,
    LLMResponse,
    Message,
    TextPart,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "ImagePart",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "TextPart",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "get_provider",
]

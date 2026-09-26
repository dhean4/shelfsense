"""The provider protocol and the settings-driven factory."""

from pathlib import Path
from typing import Protocol

from shelfsense_api.config import Settings
from shelfsense_api.llm.types import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    """Anything that can answer an :class:`LLMRequest`."""

    name: str

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Produce one completion. Raises :class:`LLMError` on failure."""
        ...


class LLMError(RuntimeError):
    """A provider could not produce a response. ``retryable`` guides the caller."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        """Record the message and whether a retry could help."""
        super().__init__(message)
        self.retryable = retryable


_providers: dict[str, LLMProvider] = {}


def get_provider(settings: Settings) -> LLMProvider:
    """One provider instance per configuration for the life of the process."""
    key = f"{settings.llm_provider}:{settings.llm_record}:{settings.llm_fixtures_dir}"
    provider = _providers.get(key)
    if provider is not None:
        return provider

    fixtures = Path(settings.llm_fixtures_dir)
    if settings.llm_provider == "replay":
        from shelfsense_api.llm.replay import ReplayProvider

        fallback: LLMProvider | None = None
        if settings.llm_record:
            # Replay what exists, call the API only for misses and record them: the cheap
            # way to fill in fixtures after adding cases or after a partial run.
            from shelfsense_api.llm.anthropic_provider import AnthropicProvider
            from shelfsense_api.llm.replay import RecordingProvider

            fallback = RecordingProvider(AnthropicProvider(), fixtures)
        provider = ReplayProvider(fixtures, fallback=fallback)
    elif settings.llm_provider == "fake":
        from shelfsense_api.llm.fake import FakeProvider

        provider = FakeProvider()
    else:
        from shelfsense_api.llm.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider()
        if settings.llm_record:
            from shelfsense_api.llm.replay import RecordingProvider

            provider = RecordingProvider(provider, fixtures)
    _providers[key] = provider
    return provider


def reset_providers() -> None:
    """Drop cached providers (tests)."""
    _providers.clear()

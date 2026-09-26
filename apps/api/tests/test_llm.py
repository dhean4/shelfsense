"""Offline tests for the provider-neutral LLM layer."""

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from shelfsense_api.llm import ImagePart, LLMRequest, Message, Usage
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.llm.pricing import DEFAULT_PRICES, cost_usd, load_prices, price_for
from shelfsense_api.llm.provider import LLMError
from shelfsense_api.llm.replay import RecordingProvider, ReplayProvider
from shelfsense_api.llm.schema import api_schema


def _request(text: str = "hi", image: bytes = b"\x89PNG") -> LLMRequest:
    return LLMRequest(
        model="claude-opus-5",
        system="sys",
        messages=[Message.user(ImagePart(media_type="image/png", data=image), text)],
        output_schema={"type": "object"},
    )


# --- fingerprint ------------------------------------------------------------------------


def test_fingerprint_is_stable_and_ignores_metadata() -> None:
    a = _request()
    b = _request()
    b.metadata["tag"] = "anything"
    assert a.fingerprint() == b.fingerprint()


def test_fingerprint_changes_with_text_or_image() -> None:
    base = _request().fingerprint()
    assert _request(text="other").fingerprint() != base
    assert _request(image=b"\x89PNG2").fingerprint() != base


# --- schema -----------------------------------------------------------------------------


class _Inner(BaseModel):
    score: float = Field(ge=0, le=1)


class _Outer(BaseModel):
    name: str = Field(min_length=1, max_length=10, description="keep me")
    items: list[_Inner] = Field(min_length=1)
    maybe: int | None = None


def test_api_schema_strips_unsupported_keywords_and_closes_objects() -> None:
    schema = api_schema(_Outer)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items", "maybe", "name"]
    assert "minLength" not in schema["properties"]["name"]
    assert schema["properties"]["name"]["description"] == "keep me"
    assert "minItems" not in schema["properties"]["items"]
    assert "default" not in schema["properties"]["maybe"]
    inner = schema["$defs"]["_Inner"]
    assert inner["additionalProperties"] is False
    assert "minimum" not in inner["properties"]["score"]
    assert json.dumps(schema)  # serialisable


# --- pricing ----------------------------------------------------------------------------


def test_cost_uses_all_four_rates() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=500_000,
        cache_write_tokens=0,
    )
    assert cost_usd("claude-opus-5", usage, DEFAULT_PRICES) == pytest.approx(5 + 2.5 + 0.25)


def test_price_prefix_match_and_override() -> None:
    assert price_for("claude-opus-5-20991231", DEFAULT_PRICES) is DEFAULT_PRICES["claude-opus-5"]
    assert price_for("gpt-9", DEFAULT_PRICES) is None
    prices = load_prices('{"custom-model": [1, 2, 0.1, 0.2]}')
    assert cost_usd("custom-model", Usage(input_tokens=1_000_000), prices) == 1.0
    assert cost_usd("unknown", Usage(input_tokens=5), prices) is None


# --- record / replay --------------------------------------------------------------------


async def test_record_then_replay_roundtrip(tmp_path: Path) -> None:
    inner = FakeProvider()
    inner.push_text('{"ok": true}')
    recorder = RecordingProvider(inner, tmp_path)
    request = _request()
    live = await recorder.complete(request)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["request"]["model"] == "claude-opus-5"
    assert saved["request"]["last_user_text"] == "hi"

    replayed = await ReplayProvider(tmp_path).complete(request)
    assert replayed == live
    assert replayed.text == '{"ok": true}'


async def test_replay_miss_is_loud(tmp_path: Path) -> None:
    with pytest.raises(LLMError, match="no recorded fixture"):
        await ReplayProvider(tmp_path).complete(_request())


async def test_fake_provider_records_requests_and_raises_when_empty() -> None:
    fake = FakeProvider()
    fake.push_text("a")
    first = await fake.complete(_request())
    assert first.text == "a"
    assert fake.requests[0].system == "sys"
    with pytest.raises(LLMError):
        await fake.complete(_request())

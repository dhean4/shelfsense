"""Tracing degrades to no-ops without Langfuse; metrics always count."""

from httpx import AsyncClient
from prometheus_client import REGISTRY

from shelfsense_api.llm import LLMRequest, Message
from shelfsense_api.llm.fake import FakeProvider
from shelfsense_api.llm.pricing import DEFAULT_PRICES
from shelfsense_api.llm.provider import LLMError
from shelfsense_api.llm.traced import TracedProvider, _trace_input
from shelfsense_api.llm.types import ImagePart
from shelfsense_api.observability import current_trace_id, observation, tracing_client


def _sample(metric: str, **labels: str) -> float:
    value = REGISTRY.get_sample_value(metric, labels)
    return float(value or 0.0)


async def test_traced_provider_counts_tokens_cost_and_calls() -> None:
    fake = FakeProvider()
    fake.push_text("hello", model="claude-opus-5")
    traced = TracedProvider(fake, DEFAULT_PRICES)
    request = LLMRequest(
        model="claude-opus-5", system="s", messages=[Message.user("hi")], metadata={"agent": "t"}
    )
    before_in = _sample(
        "shelfsense_llm_tokens_total", agent="t", model="claude-opus-5", direction="input"
    )
    before_cost = _sample("shelfsense_llm_cost_usd_total", agent="t", model="claude-opus-5")
    response = await traced.complete(request)
    assert response.text == "hello"
    assert (
        _sample("shelfsense_llm_tokens_total", agent="t", model="claude-opus-5", direction="input")
        == before_in + 100
    )
    assert _sample("shelfsense_llm_cost_usd_total", agent="t", model="claude-opus-5") > before_cost
    assert _sample("shelfsense_llm_calls_total", agent="t", model="claude-opus-5", status="ok") >= 1


async def test_traced_provider_counts_errors_and_reraises() -> None:
    fake = FakeProvider()
    traced = TracedProvider(fake, DEFAULT_PRICES)
    request = LLMRequest(
        model="m", system="s", messages=[Message.user("hi")], metadata={"agent": "e"}
    )
    before = _sample("shelfsense_llm_calls_total", agent="e", model="m", status="error")
    try:
        await traced.complete(request)
    except LLMError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected LLMError")
    assert _sample("shelfsense_llm_calls_total", agent="e", model="m", status="error") == before + 1


def test_trace_input_omits_image_bytes_and_scrubs_pii() -> None:
    request = LLMRequest(
        model="m",
        system="sys",
        messages=[
            Message.user(
                ImagePart(media_type="image/png", data=b"\x89PNG" * 100), "call 0803 123 4567"
            )
        ],
    )
    recorded = _trace_input(request)
    content = recorded["messages"][0]["content"]
    assert content[0] == {"image": "image/png", "bytes": 400}
    assert content[1] == "call [phone]"


def test_observation_is_a_noop_without_langfuse() -> None:
    assert tracing_client() is None
    with observation("x", as_type="agent", input={"a": 1}) as span:
        span.update(output={"b": 2})
        assert current_trace_id() is None


async def test_metrics_endpoint_exposes_shelfsense_series(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    assert "shelfsense_llm_tokens_total" in body
    assert "shelfsense_http_request_seconds" in body

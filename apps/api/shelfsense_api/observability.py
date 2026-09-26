"""Tracing (OpenTelemetry via the Langfuse SDK) and Prometheus metrics.

Everything here degrades to no-ops when Langfuse is not configured, so the API, worker
and ingester run identically with or without an observability stack. Metrics are always
on: they are cheap and a scrape target costs nothing until something scrapes it.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langfuse import Langfuse
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from shelfsense_api.config import Settings

log = logging.getLogger(__name__)

# --- metrics ------------------------------------------------------------------------------

LLM_TOKENS = Counter(
    "shelfsense_llm_tokens_total",
    "Tokens exchanged with the model, by agent, model and direction.",
    ["agent", "model", "direction"],
)
LLM_COST = Counter(
    "shelfsense_llm_cost_usd_total", "Estimated USD spent on model calls.", ["agent", "model"]
)
LLM_CALLS = Counter(
    "shelfsense_llm_calls_total", "Model calls by outcome.", ["agent", "model", "status"]
)
LLM_LATENCY = Histogram(
    "shelfsense_llm_call_seconds",
    "Wall time of one model call.",
    ["agent"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)
AGENT_RUNS = Counter(
    "shelfsense_agent_runs_total", "Agent runs by kind and status.", ["kind", "status"]
)
JOBS = Counter("shelfsense_jobs_total", "Background jobs by name and outcome.", ["name", "status"])
TOOL_CALLS = Counter(
    "shelfsense_tool_calls_total", "Tool invocations by tool and outcome.", ["tool", "status"]
)
ANOMALIES = Counter(
    "shelfsense_anomalies_total", "Cold-chain anomalies opened or resolved.", ["event"]
)
ACTIONS = Counter(
    "shelfsense_actions_total", "Planner actions by kind and initial status.", ["kind", "status"]
)
HTTP_REQUESTS = Histogram(
    "shelfsense_http_request_seconds",
    "HTTP request latency by route and status.",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
QUEUE_PENDING = Gauge("shelfsense_jobs_pending", "Jobs delivered but not yet acked.")


def start_metrics_server(port: int | None) -> None:
    """Expose ``/metrics`` from a non-HTTP process (worker, ingester)."""
    if port:
        start_http_server(port)
        log.info("prometheus metrics on :%d/metrics", port)


# --- tracing --------------------------------------------------------------------------------

_client: Langfuse | None = None
_configured = False


def configure_tracing(settings: Settings) -> Langfuse | None:
    """Create the Langfuse client (which installs the OTel tracer provider) once per process."""
    global _client, _configured
    if _configured:
        return _client
    _configured = True
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        log.info("langfuse not configured; tracing disabled")
        return None
    _client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.env,
        release=settings.release,
        flush_interval=2.0,
    )
    log.info("langfuse tracing enabled (%s)", settings.langfuse_host)
    return _client


def tracing_client() -> Langfuse | None:
    """The configured client, or ``None``."""
    return _client


def reset_tracing() -> None:
    """Forget the client (tests)."""
    global _client, _configured
    if _client is not None:
        _client.shutdown()
    _client = None
    _configured = False


class _NoopSpan:
    """Stands in for a Langfuse observation when tracing is off."""

    trace_id: str | None = None

    def update(self, **_: Any) -> "_NoopSpan":
        return self


@contextmanager
def observation(
    name: str,
    *,
    as_type: str = "span",
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """A Langfuse observation of the given type, or a no-op when tracing is off.

    The yielded object always supports ``.update(**fields)``; a real span also exposes
    ``.trace_id``.
    """
    client = _client
    if client is None:
        yield _NoopSpan()
        return
    kwargs: dict[str, Any] = {"name": name, "as_type": as_type}
    if input is not None:
        kwargs["input"] = input
    if metadata:
        kwargs["metadata"] = metadata
    if model is not None:
        kwargs["model"] = model
    if model_parameters:
        kwargs["model_parameters"] = model_parameters
    with client.start_as_current_observation(**kwargs) as span:
        yield span


def current_trace_id() -> str | None:
    """Trace id of the active observation, if any."""
    if _client is None:
        return None
    return _client.get_current_trace_id()


def trace_url(settings: Settings, trace_id: str | None) -> str | None:
    """Deep link into the Langfuse UI for a stored trace id."""
    if trace_id is None or _client is None:
        return None
    try:
        return _client.get_trace_url(trace_id=trace_id)
    except Exception:  # noqa: BLE001 — a bad link must never break a read endpoint
        return f"{settings.langfuse_host.rstrip('/')}/trace/{trace_id}"


def flush() -> None:
    """Push buffered spans (call before a short-lived process exits)."""
    if _client is not None:
        _client.flush()

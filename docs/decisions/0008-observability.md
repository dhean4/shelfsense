# ADR-0008: Observability: Langfuse traces, Prometheus metrics, cost per run

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P7

## Context

An agentic system that spends money and pages people needs three views: what one run did
(trace), what the system is doing now (metrics), and what it cost (per run, over time).
The brief fixes OpenTelemetry + Langfuse for spans and Prometheus for metrics.

## Decisions

### 1. Langfuse's SDK is the OpenTelemetry provider

The Langfuse v4 Python SDK is built on OpenTelemetry: constructing the client installs a
tracer provider that exports to Langfuse. FastAPI's OTel instrumentation therefore lands
in the same traces as the agent, generation and tool observations, with no second
exporter. Without Langfuse keys, `observation()` yields a no-op and the app runs
unchanged: tests, CI and a laptop without `make dev-full` never see a tracing dependency.

### 2. Trace shape: job → agent → generation/tool

Each background job is an `agent` observation carrying its payload; every model call is a
`generation` with model, parameters, scrubbed text input (images are recorded as a byte
count, never sent), output, token usage and cost; every tool call is a `tool` observation
with its arguments and result. HTTP requests that enqueue work are ordinary spans. The
trace id is stored on `agent_runs.trace_id`, and the runs API returns a deep link.

### 3. Tracing and metrics live in the provider wrapper, not the vendor client

`llm/traced.py` wraps whichever provider is configured (Anthropic, replay, fake). A replayed
eval run produces the same span shape and metrics as a live one, which is how the eval
pages and the cost page stay comparable, and how a provider swap changes nothing here.

### 4. Metrics are business-shaped

Beyond HTTP latency, the counters are the things an operator asks about: tokens and USD by
agent and model, model calls by outcome, agent runs by kind and status, jobs by outcome,
tool calls by tool, actions by kind and initial status, anomalies opened and resolved.
The worker and ingester serve `/metrics` on their own port when `SHELFSENSE_METRICS_PORT`
is set; the API mounts it at `/metrics`. No exemplars, no push gateway.

### 5. Cost is computed at the run and reported from the database

Cost is priced from token usage with the table in `llm/pricing.py` (overridable by env)
at the moment the call returns, and stored on the run. `GET /v1/usage` aggregates runs by
day, by model and lists the costliest, with p50/p95 latency, under the caller's RLS. The
cost page reads that; it does not scrape Prometheus.

### 6. The cost chart follows the data-viz method

Two categorical series in fixed slot order (blue, orange), validated with the palette
script in both light and dark modes; thin columns with a rounded data end; a 2px surface
gap between stacked segments; hairline gridlines; a legend plus hover tooltip; a table
view. No chart library.

## Consequences

- Langfuse keys go in `.env` (the Compose bootstrap creates them); the API, worker and
  ingester each need them to contribute to the same traces.
- Sampling is 100 %. At real volume set `sample_rate` on the client and keep metrics as
  the always-on signal.
- `trace_id` is only set for runs made while tracing was on; older runs show no link.

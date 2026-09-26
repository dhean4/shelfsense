# ADR-0003: LLM provider layer, structured extraction, fixtures and the job queue

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P2

## Context

The vision agent is the first model call in the system. Decisions made here (how agents
talk to a model, how their output is trusted, how tests avoid the network, how work is
queued) are inherited by the planner (P3) and the evals (P6).

## Decisions

### 1. A provider-neutral request/response layer, not the SDK types

Agents build `LLMRequest` (system, messages with text and image parts, optional
`output_schema`, tools, effort) and get back `LLMResponse` (text, tool calls, usage,
latency). `llm/anthropic_provider.py` is the only file that imports the Anthropic SDK.
Swapping providers is one module and one settings value; nothing in `agents/` changes.

### 2. Structured outputs, then our own validation, then a repair turn

The Anthropic call uses `output_config.format` with a JSON Schema derived from the
Pydantic model, so the reply always parses. The grammar supports a subset of JSON Schema,
so `llm/schema.py` strips numeric bounds, lengths and patterns before sending and the full
Pydantic model validates client-side. Rules the schema cannot say at all (SKU ids must be
in the planogram, a stock-out cannot have facings, shares sum to 100 or less) live in
`validate_extraction`. A failure is sent back to the model as a repair turn with the exact
problem, at most `vision_max_repairs` times, then the run fails loudly.

_Rejected_: forced tool use (`tool_choice`) to get JSON, since the newest model tier
rejects it; free-text JSON with regex extraction (unreliable, unvalidated).

### 3. Model and effort are settings

`SHELFSENSE_VISION_MODEL` (default `claude-opus-5`) and `SHELFSENSE_VISION_EFFORT`
(default `medium`) are configuration. Thinking stays adaptive (the model's default) and
`fallbacks: "default"` is on so a safety-classifier refusal is retried server-side on
another model rather than failing the photo.

### 4. Recorded fixtures, keyed by request fingerprint

Unit tests never hit the network. `RecordingProvider` writes each real response to
`tests/fixtures/llm/<fingerprint>.json`; `ReplayProvider` serves it and **fails on a miss**
rather than inventing an answer. The fingerprint hashes the prompt, schema, model, effort
and the image bytes, so any change to the prompt or the schema invalidates the fixtures
and `make record-fixtures` must be re-run with a key. Small images are sent as-is so the
same PNG produces the same fingerprint on every machine; only large uploads are
re-encoded.

### 5. Synthetic shelves with ground truth

`synthetic.py` renders shelves from the seed's planograms with chosen facings, stock-outs
and an intruder product, and writes the truth next to each PNG. They are not photographs
and the model finds them easy; their value is a controlled truth for the repair loop, the
summary maths and the fixtures. P6 adds real photos and explains how to label them.

### 6. A small Redis Streams queue instead of a library

`arq` pins `redis<6`; alternatives bring their own serialisation and schedulers. Streams
with a consumer group give enqueue, at-least-once delivery, `XAUTOCLAIM` recovery of jobs
a dead worker left pending, bounded retries and a dead-letter stream in about a hundred
lines (`queue.py`). Handlers are async functions keyed by job name; the same worker will
run the planner in P3.

### 7. The worker is the `system` role

Background work runs under the tenant's RLS context with `app.role = 'system'`. Tables the
worker writes (`photos`, `agent_runs`, `extractions`) list `system` in their write
policy; field agents can write `photos` because uploading is their job. User-facing
tables from P1 keep owner/manager as the only writers.

### 8. Every model call is an `agent_runs` row

Tokens, cache hits, cost (from a price table in `llm/pricing.py`, overridable by env),
latency, attempts and the outcome are stored per run. P7 exports the same rows as
traces and metrics; the cost page is a query over this table.

## Consequences

- Changing the vision prompt is a two-step change: edit, then `make record-fixtures`.
  The unit suite fails until the fixtures match, which is the point.
- Photos cannot be processed for a shelf without a planogram; the run fails with a clear
  error rather than guessing what should be there.
- Handlers must be idempotent: a job may be delivered twice. `process_photo` returns early
  when the photo is already `done`.

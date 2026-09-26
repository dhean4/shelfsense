# ADR-0006: Telemetry ingest, the excursion rule, and the live stream

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P5

## Context

Fridges fail silently. The brief asks for MQTT telemetry, a time series in plain Postgres
(ADR-0001), a rule (above 8°C for 15 minutes) that wakes the planner, and a live stream to
the dashboard.

## Decisions

### 1. Device time, not server time

Every reading carries the device's `recorded_at`; the rule, the anomaly's start and end,
and the ordering all use it. Ingest time is stored separately. This makes replayed
backfills and the accelerated simulator behave exactly like live data, and it is what a
real fleet with flaky connectivity needs anyway.

### 2. One rule, one function

`telemetry.excursion_start` is a pure function over the device's recent samples: the
trailing run of readings above the limit qualifies when it spans at least the configured
duration. It is unit-tested on boundaries (exactly 15 minutes counts; a cool reading resets
the run). The database part only fetches recent samples and manages the anomaly row: open
once per excursion, update the peak, resolve on the first reading back under the limit.

### 3. Anomalies wake the planner as the system role

An opened anomaly queues `plan_anomaly`. The planner gets a telemetry-only context (no
shelf audit) and the `system` role's tools; a dispatch it proposes is held for review by
the cost guardrail (a call-out is priced above the review limit) and by the role guardrail.
The run id is stored on the anomaly, which also makes the job idempotent.

### 4. Two ingest paths, one function

MQTT (`shelfsense-api ingest`, topic `shelfsense/<tenant slug>/telemetry/<device>`) and
HTTP (`POST /v1/telemetry`) both call `ingest_readings`. The MQTT process resolves the
tenant slug through a `SECURITY DEFINER` lookup (the same pattern as ADR-0002) and runs as
the system role; unknown devices and bad payloads are logged and dropped, never
auto-registered, because a topic name is not proof of ownership.

### 5. Live stream over Redis pub/sub and SSE

Ingest publishes each event to a per-tenant Redis channel; `GET /v1/telemetry/stream`
subscribes and emits server-sent events with heartbeats. The web app consumes it with
`fetch` and a stream reader rather than `EventSource`, because `EventSource` cannot carry
auth headers. Any number of API replicas can serve the stream since the fan-out is in Redis.

### 6. The simulator is a separate package with a decoupled clock

`apps/simulator` publishes deterministic readings (seeded RNG, mean-reverting fridge
temperature, scripted excursions, vans on a route between stores) and advances simulated
time by a fixed interval per step, at a configurable multiple of wall time. Device ids
mirror `seed.device_external_ids`, so the seeded database accepts them without setup.

## Consequences

- `telemetry` is append-only with a BRIN index; a retention job (P9 or later) should
  drop old partitions or rows.
- The rule needs a fridge's recent samples on every reading (up to 500 rows). For a real
  fleet this becomes a small per-device state row; the function's contract would not change.
- Vehicles carry no temperature and never open anomalies; GPS feeds the map (P8).

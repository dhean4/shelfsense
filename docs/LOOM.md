# Loom walkthrough script (≈ 6 minutes)

Record at 1440p, browser at 125 % zoom, terminal font 16 px. Have `make dev-full`, `make api`,
`make worker`, `make ingest`, `make web` running, and the simulator ready to start.

## 0:00 — The problem (30 s)

"Small FMCG distributors in Lagos audit shelves with WhatsApp photos and Excel, and their
chill-chain fridges fail silently. ShelfSense turns a shelf photo and fridge telemetry into
decisions, with a human in the loop, evals in CI and every model call traced and priced."

Show the architecture diagram in `docs/ARCHITECTURE.md` for five seconds.

## 0:30 — Field agent uploads (60 s)

Phone-sized window at `/upload`. Pick _Ikeja Depot Shop · Dairy Chiller_, choose
`dairy_two_stockouts.png` (or a real photo), Upload.

"That's the whole field-agent job. The API stored the photo in object storage, wrote a row under
row-level security, and queued a job."

## 1:30 — The vision agent, live (60 s)

Switch to **Agent runs**, open the newest run while it is running. Point at _Live progress_:

"Structured output from Claude, validated against the planogram. If the model contradicts
itself, a stock-out with facings, it gets the error back and repairs. Here it took one
attempt: two stock-outs of six, confidence 0.82. Every call is a Langfuse generation with
tokens and cost."

Click **Open trace in Langfuse**. Three seconds on the trace tree.

## 2:30 — The planner and the guardrails (75 s)

Back on the run list, open the planner run.

"The planner is our own loop: it read inventory through a typed tool, decided Peak Powdered
Milk is a back-room refill not a supply problem, reordered the one SKU below its reorder
point, and notified a manager. Tools are logged call by call."

Open **Review queue**: "The guardrails held the expensive order: ₦10,800 is above the
₦5,000 auto-approve limit. Approve it with an edited quantity; that decision is stamped."

## 3:45 — Cold chain (60 s)

Terminal: `make simulate`. Switch to **Cold chain**: readings stream in over MQTT → Redis →
SSE. After ~90 s the Ikeja fridge crosses 8 °C for 15 simulated minutes:

"Rule fires, anomaly opens, the planner is triggered as the system role. It proposes a
technician dispatch, which cost gating sends to review, and escalates."

Dashboard map: the store turns red.

## 4:45 — Evals and cost (60 s)

**Evals** page: "100 synthetic cases, real recorded model responses, replayed in CI on every
PR with a score-delta comment and a 2 % regression gate. Reviewer corrections promote into
this golden set." Show `evals/README.md` for how the 30 real photos come in.

**Costs** page: "Spend and p95 latency per day and per model, from the stored runs."

## 5:45 — Wrap (15 s)

"Multi-tenant Postgres with RLS, an MCP server for the same tools, OpenAPI-first with
generated types, ADRs for every decision. Repo link in the description."

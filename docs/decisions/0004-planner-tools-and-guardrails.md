# ADR-0004: Planner loop, typed tools, MCP bridge and guardrails

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P3

## Context

The planner turns an audit into actions. It is the part of the system that can spend
money and page people, so what it may do has to be bounded by code, not by prompt.

## Decisions

### 1. Our own loop, ~150 lines, no framework

`agents/planner.py` is a plan → act → observe loop over the provider-neutral LLM layer:
send context and tool specs, execute the tool calls the model makes (in the order given,
all results returned in one user turn), repeat until the model calls
`submit_decisions`. The loop owns termination: a step cap, a cost cap computed from
token usage after every call, and a single nudge when the model stops without submitting.

_Rejected_: the SDK tool runner (ties the loop to one vendor and hides the per-step cost
check we need) and LangChain (excluded by the brief).

### 2. Tools are typed contracts, executed in one place

`agents/tools.py` holds every tool as name + description + Pydantic input model + async
handler taking a `ToolContext` (tenant session, role, run id). `execute()` validates,
runs, times and logs each call to `tool_calls` whether it came from the planner, the
HTTP endpoint or MCP, and never raises for tool-level problems: the error text is what
the model sees. The JSON Schemas the model receives are generated from the same models
(`llm/schema.py`), so there is one definition of each tool.

`submit_decisions` is a tool in the spec but not in the handler table: it is the loop's
termination signal, and its validated input becomes the run summary.

### 3. The MCP server is a bridge, not a second implementation

`packages/mcp-tools` lists `GET /v1/tools` as MCP tools and forwards `CallTool` to
`POST /v1/tools/{name}` with the caller's credentials (Clerk JWT, or dev headers). Role
filtering, tenant isolation and logging therefore happen once, in the API. The bridge
is tested against a fake API with the MCP SDK's in-memory transport.

### 4. Guardrails are deterministic and tested without a model

`guardrails.py`:

- **Tool visibility by role**: the role that triggered a run (uploader of the photo, or
  `system` for telemetry) determines the tools the model is offered. A field agent's run
  cannot dispatch a technician because the tool is not in its list, not because the
  prompt says so.
- **Review gating**: after `submit_decisions`, every proposed action is judged in order:
  confidence below `review_confidence_threshold`, estimated cost above
  `review_cost_limit_kobo`, escalations, and kinds the trigger role may not auto-approve.
  Anything caught goes to `pending_review` with the reason recorded; the rest is
  `approved`. A run that fails mid-way parks its proposals for review rather than
  approving or discarding them.
- **Spend caps**: `planner_max_cost_usd` and `planner_max_steps`; both fail the run
  loudly.
- **PII scrub**: emails, phone numbers and long digit runs are replaced before free text
  (auditor notes, telemetry) enters a prompt. Store names and SKU names are business
  data, not personal data, and are kept.

### 5. Cost estimates are the server's, not the model's

`create_reorder` computes `quantity × unit_price_kobo` from the catalogue;
`dispatch_technician` uses a configured call-out cost. The model proposes; the server
prices; the guardrail compares.

### 6. Notifications and geocoding are honest stubs

`notify` records a `notifications` row with status `stubbed` for every user in the target
role and returns only the count: no external sends until P9, and no recipient addresses
back to the model. `geocode` answers from the tenant's own store records and refuses
anything else, stating that no external geocoder is configured.

## Consequences

- Every model call and tool call of a run is queryable: `GET /v1/runs/{id}` is the
  timeline P8 renders and P7 exports as traces.
- Adding a tool means one entry in `REGISTRY` plus a role entry in `ROLE_TOOLS`; the
  HTTP endpoint, MCP server and planner pick it up unchanged.
- The planner's real-model behaviour is exercised manually (`shelfsense-api` CLI against
  the Compose stack) and by the evals in P6; the automated suites use the fake provider,
  as the brief requires.

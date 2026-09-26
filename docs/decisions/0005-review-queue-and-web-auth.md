# ADR-0005: Review queue, labelled examples, golden set, and web identity

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P4

## Context

Guardrails (ADR-0004) hold back risky actions and the vision agent reports its own
confidence. A human has to close the loop, and every human judgement is worth keeping:
it is exactly the data an eval set is made of.

## Decisions

### 1. Two kinds of review items, one queue

`GET /v1/review/queue` returns actions in `pending_review` and extractions whose
confidence is below `review_confidence_threshold` that nobody has judged yet. Actions are
decided with `approve` (optionally editing a reorder's quantity, which re-prices it from the
catalogue) or `reject`, both stamped with who and when. Deciding an already-decided action
is a 409, never a silent overwrite.

### 2. A verdict is a labelled example

`POST /v1/extractions/{id}/review` stores a verdict: `correct`, `corrected` (with the
reviewer's extraction) or `unusable`. A correction passes the same `validate_extraction`
rules as model output, against the shelf's current planogram, so a label can never
reference a SKU the shelf does not hold or contradict itself. Corrections carry the
derived summary too, so evals compare like with like.

### 3. Promotion snapshots everything the eval needs

`POST /v1/labels/{id}/promote` creates a `golden_cases` row with the photo's object key,
a **snapshot** of the planogram (ids, names, expected facings) and the expected extraction
(the correction, or the model's own output for `correct`). Planograms change; a golden case
must not. `unusable` verdicts cannot be promoted, and a review promotes at most once.
P6 exports these rows to the JSONL dataset the eval runner consumes, next to the
synthetic cases.

### 4. The web app mirrors the API's two auth modes

`NEXT_PUBLIC_AUTH_MODE=clerk` (paired with the API's `jwks`) wraps the app in
`ClerkProvider`, runs `clerkMiddleware`, and sends the session token as a bearer header
from both server components and the browser. `NEXT_PUBLIC_AUTH_MODE=dev` (the default,
paired with the API's `dev`) takes identity from three cookies (tenant, role, user) with
env defaults, editable from a control in the header. The mode is explicit rather than
inferred from the presence of a Clerk key: during P4 a key sitting in `.env` silently
flipped the web app into Clerk mode while the API was still in dev mode. The API's dev
mode is refused in production, so the cookie path cannot be deployed by accident.

_Why cookies rather than a login form_: the dev identity is a testing convenience, not an
account. Switching role in one click is what makes the review flow demonstrable: upload as
a field agent, hold as a reviewer, approve as a manager.

### 5. Server components read, client components write

Pages fetch with `apiGet` on the server (no CORS, no loading flicker) and hand the data
to client components as `initialData` for TanStack Query, which then owns refetching after
mutations. The browser calls the API directly, so the API allows the web origin in CORS.

## Consequences

- A reviewer's corrections in the UI edit only facings per planogram slot; regions are
  copied from the model when it saw the SKU and default to the whole frame otherwise.
  Bounding-box correction is a P8 candidate, not a P4 requirement.
- Golden cases reference the photo by object key; deleting photos must consider them.
- The `photos` page is a desktop uploader for the demo; the mobile PWA arrives in P8.

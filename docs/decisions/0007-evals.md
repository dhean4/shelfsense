# ADR-0007: Evals: dataset, scoring, replay in CI, and the regression gate

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P6

## Context

Prompts, schemas and guardrails will keep changing. Without a fixed dataset and a
repeatable scorer, "the agent got better" is an anecdote. The brief asks for a golden set,
scoring functions, a CLI and a CI job that comments score deltas on PRs.

## Decisions

### 1. Two case kinds, one JSONL file, validated on load

`vision` cases pair a photo with its planogram and truth; `planner` cases pair an audit
summary, inventory and telemetry with the decisions the documented policy expects.
`shelfsense_evals.dataset` validates every line (a vision truth must cover exactly the
planogram's SKUs; ids must be unique) so a malformed case fails loudly before any model
call.

### 2. Metrics are pure functions, and "failed" scores zero

`scoring.py` takes predictions and truth and returns numbers. A run that raises (repair
budget exhausted, provider error) scores zero on quality metrics and still carries its cost
and latency, so a regression that makes the agent crash shows up as a drop, not as a
missing row. Hallucination rate counts planogram SKUs reported present when absent plus
unknown products the truth does not contain; unneeded-action rate counts reorders and
dispatches the policy did not call for.

### 3. The expected planner decisions are the policy, written as code

`generate._expected_decisions` encodes the same rules the planner prompt states (reorder
when out/low and at or below the reorder point; notify on two or more stock-outs, low
confidence or a fault; dispatch only on a fault; escalate below 0.5 confidence). The
planner is therefore scored against an explicit policy, and changing the policy means
changing both the prompt and this function, in one PR, with the delta visible.

### 4. Planner cases run without a database

`fake_tools.CaseToolExecutor` serves the same tool contracts from the case's inventory and
records what the model decided. `run_planner` takes an `executor` and a `gate` flag so the
production loop and the eval loop are the same code with different tool backends. Ids the
executor mints are UUIDv5 of the case and call sequence, so recorded conversations replay
byte-for-byte.

### 5. CI replays; humans record

CI runs the dataset with the `replay` provider against committed fixtures (deterministic,
free, no key), compares against `evals/baseline.json`, posts a sticky PR comment with the
delta table, and fails when a gated metric (SKU recall, stock-out F1, hallucination rate,
decision accuracy, unneeded-action rate) drops more than 2 % relative. A prompt or schema
change invalidates the fixtures, so the PR that changes a prompt also has to carry
re-recorded fixtures and, if the author accepts the new scores, a promoted baseline. That
is the review conversation the eval exists to force.

### 6. Synthetic first, real photos through the product

The 100 committed cases are synthetic and easy. `evals/README.md` gives the steps for
the 30 real photos: upload through the app, correct in the review queue, promote to the
golden set, `export-golden` to a second dataset file. Real cases will score lower; their
baseline is set separately and gated the same way.

## Consequences

- `evals/fixtures/llm` holds one JSON per model call (~160 files); `evals/data/photos`
  holds 60 PNGs (~1.2 MB). Both are committed by design.
- A full live run costs roughly $7 and ten minutes at concurrency 4; `--limit` and `--tag`
  exist for cheaper iterations.
- The scoreboard page reads `apps/web/src/data/eval-latest.json`, which `make eval`
  refreshes. It is a static snapshot, not a database; P7's cost page reads live runs.

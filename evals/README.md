# ShelfSense evals

What the agents are measured against, how, and how to add real photos.

```sh
make eval             # replay recorded model responses over the committed dataset (free, offline)
make eval-live        # call the real model (needs ANTHROPIC_API_KEY; ~$7 for the full set)
make eval-record      # replay what exists, call the API only for missing fixtures, save them
make eval-rerecord    # re-record everything live (after a prompt or schema change)
uv run shelfsense-evals compare      # delta vs evals/baseline.json; exits 1 on a gated regression
uv run shelfsense-evals generate     # regenerate the synthetic seed (same seed => same bytes)
```

## Dataset

`data/golden.jsonl`, one case per line (schema in `shelfsense_evals/dataset.py`):

- **vision** cases: a photo path, the planogram it was audited against, and the truth
  (facings per SKU, unknown products). Scored on SKU precision/recall, stock-out F1,
  facings MAE, hallucination rate, exact match, cost and latency.
- **planner** cases: an audit summary, inventory rows, optional telemetry, and the decisions
  the documented policy expects (reorder SKUs, notify, dispatch, escalate). Scored on
  decision accuracy, reorder F1, per-decision correctness, unneeded-action rate, cost, latency.

The 100 committed cases (60 vision, 40 planner) are **synthetic**: rendered shelves and
generated audits over the seeded planograms. They are deliberately easy for a vision model;
their job is to catch regressions in the prompt, the schema, the repair loop and the policy,
not to estimate real-world accuracy.

## Adding your 30 real photos

Real photos are what make the numbers mean something. The flow keeps labelling inside the
product so every label is validated against the planogram:

1. Run the stack (`make dev`, `make api`, `make worker`, `make web`).
2. On the **Photos** page pick the shelf the photo shows and upload it. Take photos of one
   planogram shelf at a time, straight on, in the shop's own light: that is what the model
   will see in production. Ten to fifteen per shelf across a few visits gives a useful
   spread of stock-outs.
3. Wait for the extraction, then open the **Review queue**. If the photo is not there (the
   model was confident), open it from the runs page and review it anyway: type the real
   facings per slot and submit the correction, or mark the model right.
4. Promote the verdict to the golden set with tags (`real`, the shop, `evening`, …).
5. Export: `uv run shelfsense-evals export-golden --tenant-id <id>` writes
   `data/review/golden.jsonl` and downloads the photos next to it. Commit both.
6. Run `uv run shelfsense-evals run --dataset evals/data/review/golden.jsonl --provider anthropic --record`
   once to record fixtures, then `compare` and `promote-baseline` when happy.

Photos of real shelves are personal to the shop: keep faces out of frame, and do not
commit anything the shop owner has not agreed to publish.

## CI

`.github/workflows/ci.yml` has an `evals` job that replays the fixtures on every PR,
compares against `baseline.json`, posts (and updates) a sticky comment with the delta table,
and fails when a gated metric drops more than 2 % relative. Changing a prompt or a schema
invalidates the fixtures: re-record locally with a key, commit the new fixtures and, if the
scores are acceptable, the new baseline.

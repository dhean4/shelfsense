# shelfsense-api

FastAPI service. See the [root README](../../README.md) for the quickstart and
[ADR-0001](../../docs/decisions/0001-stack-choices.md) for why things are the way they are.

```
make api                      # uvicorn with reload on :8000
curl localhost:8000/healthz   # liveness: process is up
curl localhost:8000/readyz    # readiness: postgres + redis reachable
```

Configuration is read from `SHELFSENSE_*` environment variables (see `.env.example`).

.DEFAULT_GOAL := help
SHELL := /bin/bash

UV := uv run
COMPOSE := docker compose
PY_MEMBERS := apps/api apps/simulator evals

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Environment -------------------------------------------------------------------

.env:
	cp .env.example .env
	@echo "created .env from .env.example"

.PHONY: install
install: ## Install Python (uv) and Node (pnpm) dependencies
	uv sync
	pnpm install

.PHONY: dev
dev: .env ## Start core infra: postgres, redis, s3 (RustFS), mosquitto
	$(COMPOSE) up -d --wait
	@echo "postgres :$${POSTGRES_PORT:-5433}  redis :$${REDIS_PORT:-6379}  s3 :$${S3_PORT:-9000}  mqtt :$${MQTT_PORT:-1883}"

.PHONY: dev-full
dev-full: .env ## Core infra plus Langfuse v3 + ClickHouse (observability profile)
	$(COMPOSE) --profile observability up -d --wait
	@echo "langfuse http://localhost:$${LANGFUSE_PORT:-3001}  (dev@shelfsense.local / shelfsense-dev-password)"

.PHONY: down
down: ## Stop every service, keep volumes
	$(COMPOSE) --profile observability down

.PHONY: nuke
nuke: ## Stop every service and delete volumes (destroys local data)
	$(COMPOSE) --profile observability down -v

.PHONY: logs
logs: ## Tail service logs
	$(COMPOSE) --profile observability logs -f --tail=100

.PHONY: api
api: ## Run the API with reload on :8000
	$(UV) shelfsense-api serve

.PHONY: migrate
migrate: ## Apply database migrations (uses SHELFSENSE_MIGRATION_DATABASE_URL)
	$(UV) shelfsense-api migrate

.PHONY: seed
seed: ## Load the deterministic demo dataset (idempotent)
	$(UV) shelfsense-api seed

.PHONY: worker
worker: ## Run the background job worker (vision extraction, later the planner)
	$(UV) shelfsense-api worker

.PHONY: ingest
ingest: ## Subscribe to MQTT telemetry and ingest it (anomalies trigger the planner)
	$(UV) shelfsense-api ingest

.PHONY: simulate
simulate: ## Publish simulated fridge/van telemetry at 10x speed (one fridge overheats after 2 min)
	$(UV) shelfsense-simulator run

.PHONY: process-jobs
process-jobs: ## Handle queued jobs inline once, without a long-running worker
	$(UV) shelfsense-api process-jobs

.PHONY: synth-photos
synth-photos: ## Render the synthetic shelf photos + ground truth into apps/api/tests/fixtures/photos
	$(UV) shelfsense-api synth-photos

.PHONY: record-fixtures
record-fixtures: ## Call the real model on the synthetic photos and save responses (needs ANTHROPIC_API_KEY, costs money)
	$(UV) shelfsense-api record-fixtures

.PHONY: demo
demo: ## Queue the synthetic photos and an hour of telemetry for the seeded tenants (then run the worker)
	$(UV) shelfsense-api demo

.PHONY: docker-build
docker-build: ## Build the API/worker/ingester image locally
	docker build -f apps/api/Dockerfile -t shelfsense-api:local .

.PHONY: openapi
openapi: ## Export the OpenAPI document and regenerate the TS types from it
	$(UV) shelfsense-api export-openapi
	pnpm --filter @shelfsense/shared generate

.PHONY: web
web: .env ## Run the Next.js dev server on :3000 (reads NEXT_PUBLIC_* from .env)
	pnpm --filter @shelfsense/shared build
	set -a; . ./.env; set +a; pnpm --filter @shelfsense/web dev

# --- Verification ------------------------------------------------------------------

.PHONY: test
test: ## Unit tests: Python (offline) and TS
	$(UV) pytest -m "not integration and not network"
	pnpm test

.PHONY: test-integration
test-integration: ## Python tests that need `make dev` running
	$(UV) pytest -m integration

.PHONY: lint
lint: ## Lint and check formatting (ruff, eslint, prettier)
	$(UV) ruff check .
	$(UV) ruff format --check .
	pnpm lint
	pnpm format:check

.PHONY: format
format: ## Auto-fix lint and formatting
	$(UV) ruff check --fix .
	$(UV) ruff format .
	pnpm format

.PHONY: typecheck
typecheck: typecheck-py ## mypy --strict and tsc across the workspace
	pnpm typecheck

.PHONY: typecheck-py
typecheck-py: ## mypy --strict, one run per Python workspace member
	@for m in $(PY_MEMBERS); do \
		echo "mypy $$m"; \
		(cd $$m && $(UV) mypy --config-file $(CURDIR)/pyproject.toml .) || exit 1; \
	done

.PHONY: build
build: ## Build every TS package and the web app
	pnpm build

.PHONY: verify
verify: lint typecheck test build ## Everything CI runs, locally
	@echo "all checks passed"

.PHONY: eval
eval: ## Replay recorded model responses over the golden set and print the scores (offline)
	$(UV) shelfsense-evals run --provider replay
	$(UV) shelfsense-evals compare --no-fail-on-regression
	cp evals/results/latest.json apps/web/src/data/eval-latest.json

.PHONY: eval-live
eval-live: ## Run the golden set against the real model (needs ANTHROPIC_API_KEY, ~$$7)
	$(UV) shelfsense-evals run --provider anthropic

.PHONY: eval-record
eval-record: ## Replay what is recorded, call the API only for missing fixtures and save them
	$(UV) shelfsense-evals run --provider replay --record
	cp evals/results/latest.json apps/web/src/data/eval-latest.json

.PHONY: eval-rerecord
eval-rerecord: ## Re-record every fixture live (after a prompt/schema change; ~$$7)
	$(UV) shelfsense-evals run --provider anthropic --record
	cp evals/results/latest.json apps/web/src/data/eval-latest.json

.PHONY: eval-generate
eval-generate: ## Regenerate the synthetic seed dataset (photos + JSONL)
	$(UV) shelfsense-evals generate

.PHONY: clean
clean: ## Remove build artefacts and caches (not Docker volumes)
	rm -rf .turbo .pytest_cache .mypy_cache .ruff_cache
	find . -name node_modules -type d -prune -exec rm -rf {} +
	find . -name .next -type d -prune -exec rm -rf {} +
	find . -name dist -type d -prune -not -path "*/node_modules/*" -exec rm -rf {} +

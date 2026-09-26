# Deployment runbook

Target: **Fly.io** for the API, worker and MQTT ingester (one image, three process groups),
**Vercel** for the web app. Managed services: Fly Postgres (pgvector), Upstash Redis on
Fly, Tigris (S3-compatible) on Fly, a Mosquitto Fly app for MQTT, Clerk for identity,
Langfuse Cloud (or your own) for traces.

Everything below is scripted but **not yet executed** for this repository: the deploy
tooling for Fly (`flyctl`) was not installed on the build machine. The Vercel project is
created by the CLI; the first production deploy needs the API URL and Clerk keys set as
environment variables. Treat this file as the checklist for the first deploy.

## 0. Prerequisites

```sh
brew install flyctl && fly auth login
npm i -g vercel && vercel login          # or `pnpm dlx vercel`
```

Clerk: enable **Organizations** on the instance (Configure → Organizations), create the
organisation roles `org:manager`, `org:field_agent`, `org:reviewer` (the default
`org:admin` maps to owner), and note the frontend domain from the publishable key.

## 1. Fly resources

```sh
fly apps create shelfsense-api
fly postgres create --name shelfsense-db --region jnb --vm-size shared-cpu-1x --volume-size 3
fly postgres attach shelfsense-db --app shelfsense-api          # sets DATABASE_URL
fly redis create --name shelfsense-redis --region jnb           # Upstash; note the URL
fly storage create --name shelfsense-photos --app shelfsense-api # Tigris; sets AWS_* secrets
```

The API reads its own variable names, so map the attached ones:

```sh
fly secrets set --app shelfsense-api \
  SHELFSENSE_MIGRATION_DATABASE_URL="<DATABASE_URL from attach>" \
  SHELFSENSE_DATABASE_URL="<same host/db, user shelfsense_app>" \
  SHELFSENSE_REDIS_URL="<redis url>" \
  SHELFSENSE_S3_ENDPOINT_URL="https://fly.storage.tigris.dev" \
  SHELFSENSE_S3_ACCESS_KEY="$AWS_ACCESS_KEY_ID" SHELFSENSE_S3_SECRET_KEY="$AWS_SECRET_ACCESS_KEY" \
  SHELFSENSE_JWKS_URL="https://<clerk-frontend-domain>/.well-known/jwks.json" \
  SHELFSENSE_JWT_ISSUER="https://<clerk-frontend-domain>" \
  ANTHROPIC_API_KEY="sk-ant-..." \
  SHELFSENSE_LANGFUSE_PUBLIC_KEY="pk-lf-..." SHELFSENSE_LANGFUSE_SECRET_KEY="sk-lf-..."
```

Create the non-superuser role once (the API refuses to run as a superuser, ADR-0002):

```sh
fly postgres connect -a shelfsense-db
  CREATE ROLE shelfsense_app LOGIN PASSWORD '<strong password>';
  CREATE EXTENSION IF NOT EXISTS vector;
```

## 2. MQTT broker

```sh
fly launch --name shelfsense-mqtt --image eclipse-mosquitto:2 --no-deploy --region jnb
# mount infra/mosquitto/mosquitto.conf via a volume or bake an image; add a password file
# and TLS before exposing 8883 publicly. Then:
fly secrets set --app shelfsense-api SHELFSENSE_MQTT_URL="mqtt://shelfsense-mqtt.internal:1883"
```

The ingester talks to the broker over Fly's private network, so the broker need not be public.

## 3. Deploy the API

```sh
fly deploy                                    # runs `shelfsense-api migrate` as the release command
fly scale count api=1 worker=1 ingest=1
fly ssh console -C "shelfsense-api seed"      # demo tenant (idempotent)
fly ssh console -C "shelfsense-api demo"      # demo photos + telemetry (idempotent)
curl https://shelfsense-api.fly.dev/readyz
```

Link the Clerk organisation to the seeded tenant:

```sql
UPDATE tenants SET external_org_id = 'org_...' WHERE slug = 'lagos-fresh';
```

## 4. Deploy the web app

```sh
cd apps/web
vercel link                                   # creates the project; root directory = apps/web
vercel env add NEXT_PUBLIC_API_URL production # https://shelfsense-api.fly.dev
vercel env add NEXT_PUBLIC_AUTH_MODE production # clerk
vercel env add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY production
vercel env add CLERK_SECRET_KEY production
vercel --prod
```

Set `SHELFSENSE_CORS_ORIGINS` on Fly to the Vercel URL if it differs from the default in
`fly.toml`.

## 5. Smoke test

1. Sign in on the Vercel URL, pick the organisation, open **Upload**, take a photo.
2. Watch the run page stream turns; check the Langfuse trace link.
3. `fly logs -a shelfsense-api` shows the worker and ingester; `/metrics` on the API answers.

## Costs at idle

One shared-cpu machine per process group with auto-stop on the API, the smallest Fly
Postgres, Upstash's free tier and Tigris' free tier come to a few dollars a month. Model
spend dominates: roughly $0.04 per photo and $0.05–0.12 per planner run at the defaults.

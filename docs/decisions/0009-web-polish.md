# ADR-0009: Live run timeline, fleet map, mobile upload PWA, loading and accessibility

- **Status**: accepted
- **Date**: 2026-09-26
- **Phase**: P8

## Context

The API and worker are complete; the web app existed as a functional review console. P8
makes it the product a field agent, a manager and an engineer would each open.

## Decisions

### 1. Run progress is streamed per turn, not per token

The worker publishes run events (`run_started`, `model_turn` with the model's text and the
tools it called, `tool_result`, `vision_attempt`, `run_finished`) to a per-tenant Redis
channel through the same hook the evals use; `GET /v1/runs/stream` serves them as SSE, and
the run page appends them live then refreshes when the run finishes. Model calls are not
token-streamed: the loop needs the complete response to validate structured output and to
decide on tool calls, and per-turn granularity is what a reviewer reads anyway. Text in
events is PII-scrubbed and bounded, like traces.

### 2. Leaflet with circle markers over OpenStreetMap

The dashboard map shows each store's fridge state (ok, excursion open, no telemetry) and
the last van positions. Circle markers avoid Leaflet's image icons, which break under
bundlers; the map is loaded client-side only because Leaflet reads `window` at import.
Status colours are the reserved status palette and always pair with an icon and label.

### 3. A hand-written service worker, not a PWA plugin

The Next.js PWA plugins either lag Next releases or do not support Turbopack builds. A
40-line `public/sw.js` (network-first navigations with cached fallback, cache-first static
assets, never caching API calls) plus a Next `manifest.ts` and generated icons make the app
installable with nothing to keep in step. An offline upload queue is a non-goal: a photo
taken offline is kept by the phone's camera roll and uploaded when the agent has signal.

### 4. The mobile page is a separate route, not a responsive variant

`/upload` is the whole field-agent job: store, shelf (both remembered), camera capture,
confirm, upload, result. Large targets, no navigation chrome in the way, `aria-live` on the
status. The desktop Photos page stays for managers.

### 5. Accessibility as defaults, not a checklist pass

Skip link, landmark `main` with focus target, `aria-current` on navigation, labelled form
controls, `role="status"` skeletons, `aria-live` regions for streamed content, table view
for every chart, status meaning never carried by colour alone. The Next ESLint preset
already runs `jsx-a11y` rules on every build.

## Consequences

- The web app now has its own unit tests (Vitest, node environment) for pure helpers; page
  rendering is verified by the production build plus fetches in CI-adjacent smoke scripts,
  not by a browser harness. Adding Playwright is the next step if the UI keeps growing.
- Map tiles come from the public OpenStreetMap servers; a deployment with real traffic
  needs a tile provider with a key or a self-hosted tile cache.

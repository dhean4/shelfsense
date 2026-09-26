/*
 * Capture the README / article screenshots from a running local stack.
 *
 * Needs `make api`, `make web` (dev auth mode) and, for a live cold-chain page, `make ingest`
 * + `make simulate`. Playwright is deliberately not a repo dependency: install it once in a
 * scratch folder and point NODE_PATH at it.
 *
 *   cd /tmp/pw && pnpm init && pnpm add playwright-core@1.63.0   # pairs with the cached Chromium
 *   NODE_PATH=/tmp/pw/node_modules node scripts/capture-screenshots.cjs
 *
 * Env: WEB_URL (default http://localhost:3000), API_URL (http://localhost:8000),
 * TENANT (seeded Lagos Fresh tenant id), CHROMIUM_PATH (override the browser binary).
 */
const path = require("node:path");
const fs = require("node:fs");
const { chromium } = require("playwright-core");

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const API = process.env.API_URL ?? "http://localhost:8000";
const OUT = path.resolve(__dirname, "../docs/images");
const TENANT = process.env.TENANT ?? "ef23fb7b-a8db-5875-a68a-feddef32c864";
const IDENTITY = { tenant: TENANT, role: "manager", user: "demo-manager" };
const HEADERS = {
  "X-Dev-Tenant": IDENTITY.tenant,
  "X-Dev-Role": IDENTITY.role,
  "X-Dev-User": IDENTITY.user,
};

async function apiJson(route) {
  const response = await fetch(`${API}${route}`, { headers: HEADERS });
  if (!response.ok) throw new Error(`${route} -> ${response.status}`);
  return response.json();
}

/** Screenshot one page once every selector in `ready` is visible. */
async function shot(context, { url, file, ready, size, fullPage = false, settleMs = 600 }) {
  const page = await context.newPage();
  if (size) await page.setViewportSize(size);
  // Never "networkidle": the cold-chain page and unfinished runs keep an SSE fetch open.
  await page.goto(`${WEB}${url}`, { waitUntil: "domcontentloaded" });
  for (const selector of ready) await page.waitForSelector(selector, { timeout: 20_000 });
  if (url === "/") {
    // Leaflet is client-only and its OSM tiles arrive over the network; give them a moment.
    await page
      .waitForFunction(
        () => {
          const tiles = document.querySelectorAll(".leaflet-tile");
          const loaded = document.querySelectorAll(".leaflet-tile-loaded");
          return tiles.length >= 6 && loaded.length === tiles.length;
        },
        null,
        { timeout: 30_000 },
      )
      .catch(() => console.warn("map tiles did not finish loading; capturing anyway"));
  }
  // The dev-only identity control and the Next.js dev badge have no place in product shots.
  await page.addStyleTag({
    content:
      'button[title^="Dev auth mode"] { visibility: hidden; } nextjs-portal { display: none; }',
  });
  await page.waitForTimeout(settleMs);
  await page.screenshot({ path: path.join(OUT, file), fullPage });
  await page.close();
  console.log(`captured ${file}`);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const runs = await apiJson("/v1/runs?limit=100");
  const planner = runs.find(
    (r) => r.kind === "planner" && r.status === "succeeded" && r.actions.length > 0,
  );
  const vision = runs.find((r) => r.kind === "vision" && r.status === "succeeded");
  if (!planner || !vision)
    throw new Error("need one succeeded planner and vision run; run `make demo`");

  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH });
  const newContext = (mobile) =>
    browser.newContext({
      viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 },
      deviceScaleFactor: mobile ? 3 : 2,
      isMobile: mobile,
      hasTouch: mobile,
      locale: "en-NG",
      timezoneId: "Africa/Lagos",
      colorScheme: "light",
    });
  const cookies = Object.entries(IDENTITY).map(([key, value]) => ({
    name: `ss-dev-${key}`,
    value,
    url: WEB,
  }));
  const desktop = await newContext(false);
  await desktop.addCookies(cookies);
  const phone = await newContext(true);
  await phone.addCookies(cookies);

  const shots = [
    [desktop, { url: "/", file: "dashboard.png", ready: ["h1", ".leaflet-container"] }],
    [desktop, { url: "/review", file: "review-queue.png", ready: ["text=Review queue"] }],
    [desktop, { url: "/runs", file: "runs.png", ready: ["text=Agent runs", "table tbody tr"] }],
    [
      desktop,
      {
        url: `/runs/${planner.id}`,
        file: "run-planner.png",
        ready: ["text=Tool calls (", "text=Actions ("],
        fullPage: true,
      },
    ],
    [
      desktop,
      { url: `/runs/${vision.id}`, file: "run-vision.png", ready: ["text=Tokens in / out"] },
    ],
    [desktop, { url: "/photos", file: "photos.png", ready: ["text=Shelf photos"], fullPage: true }],
    [
      desktop,
      {
        url: "/telemetry",
        file: "cold-chain.png",
        ready: ["text=Cold chain", "text=excursion since"],
        settleMs: 8_000,
      },
    ],
    [desktop, { url: "/evals", file: "evals.png", ready: ["text=Eval scoreboard"] }],
    [
      desktop,
      { url: "/costs?days=30", file: "costs.png", ready: ["text=Daily spend by agent", "svg"] },
    ],
    [phone, { url: "/upload", file: "upload-mobile.png", ready: ["select"] }],
  ];
  // `node capture-screenshots.cjs dashboard cold-chain` re-captures just those files.
  const only = new Set(process.argv.slice(2).map((name) => name.replace(/\.png$/, "")));
  let failures = 0;
  for (const [context, spec] of shots) {
    if (only.size > 0 && !only.has(spec.file.replace(/\.png$/, ""))) continue;
    try {
      await shot(context, spec);
    } catch (error) {
      failures += 1;
      console.error(`FAILED ${spec.file}: ${error.message.split("\n")[0]}`);
    }
  }

  await browser.close();
  if (failures > 0) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

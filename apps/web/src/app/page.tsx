import Link from "next/link";

import { FleetMapLoader } from "@/components/map/fleet-map-loader";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGetOrNull } from "@/lib/api.server";
import { clerkEnabled } from "@/lib/auth";
import type { ActionOut, DeviceOut, MeOut, ReviewQueueOut, RunOut, StoreOut } from "@/lib/types";

export const dynamic = "force-dynamic";

async function SignIn() {
  const { SignInButton, SignUpButton } = await import("@clerk/nextjs");
  return (
    <div className="flex items-center justify-center gap-3">
      <SignInButton mode="modal">
        <button type="button" className="rounded-md bg-primary px-4 py-2 text-primary-foreground">
          Sign in
        </button>
      </SignInButton>
      <SignUpButton mode="modal">
        <button type="button" className="rounded-md border px-4 py-2 hover:bg-accent">
          Sign up
        </button>
      </SignUpButton>
    </div>
  );
}

export default async function Home() {
  const me = await apiGetOrNull<MeOut>("/v1/me");
  if (!me) {
    return (
      <div className="mx-auto max-w-lg space-y-3 py-12 text-center">
        <h1 className="text-2xl font-semibold">Not signed in</h1>
        {clerkEnabled ? (
          <>
            <p className="text-muted-foreground">
              Sign in with your organisation account. Your organisation must be registered as a
              ShelfSense tenant.
            </p>
            <SignIn />
          </>
        ) : (
          <p className="text-muted-foreground">
            In dev mode, set a tenant id and role with the identity control at the top right. Tenant
            ids are printed by <code className="font-mono">make seed</code>.
          </p>
        )}
      </div>
    );
  }
  const [stores, queue, runs, approved, devices] = await Promise.all([
    apiGetOrNull<StoreOut[]>("/v1/stores"),
    apiGetOrNull<ReviewQueueOut>("/v1/review/queue"),
    apiGetOrNull<RunOut[]>("/v1/runs?limit=10"),
    apiGetOrNull<ActionOut[]>("/v1/actions?status=approved&limit=50"),
    apiGetOrNull<DeviceOut[]>("/v1/devices"),
  ]);
  const openExcursions = (devices ?? []).filter((d) => d.open_anomaly).length;
  const pending = queue ? queue.actions.length + queue.extractions.length : null;
  const spend = (runs ?? []).reduce((total, run) => total + (run.cost_usd ?? 0), 0);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{me.tenant.name}</h1>
        <p className="text-sm text-muted-foreground">
          Signed in as {me.user_id} · {me.role.replace("_", " ")}
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Stores" value={stores ? String(stores.length) : "—"} href="/photos" />
        <Stat
          label="Awaiting review"
          value={pending == null ? "—" : String(pending)}
          href="/review"
          hint={
            queue
              ? `${String(queue.actions.length)} actions · ${String(queue.extractions.length)} photos`
              : "reviewers only"
          }
        />
        <Stat
          label="Approved actions"
          value={approved ? String(approved.length) : "—"}
          href="/runs"
        />
        <Stat
          label="Spend, last 10 runs"
          value={runs ? `$${spend.toFixed(3)}` : "—"}
          href="/runs"
          hint={runs ? `${String(runs.length)} runs` : undefined}
        />
      </div>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center gap-2">
          <CardTitle>Fridge health</CardTitle>
          <span className="text-sm text-muted-foreground">
            {openExcursions === 0
              ? "no open excursions"
              : `${String(openExcursions)} open excursion${openExcursions === 1 ? "" : "s"}`}
            {" · "}
            <Link href="/telemetry" className="underline">
              cold chain
            </Link>
          </span>
        </CardHeader>
        <CardContent>
          {stores && stores.length > 0 ? (
            <FleetMapLoader stores={stores} devices={devices ?? []} />
          ) : (
            <p className="text-sm text-muted-foreground">
              No stores yet. Run <code className="font-mono">make seed</code> or create one via the
              API.
            </p>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Where things stand</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            Field agents upload shelf photos; the vision agent audits them against the planogram;
            the planner reads inventory and proposes actions; guardrails send anything risky to the
            <Link href="/review" className="text-foreground underline">
              {" "}
              review queue
            </Link>
            . Every model and tool call is on the{" "}
            <Link href="/runs" className="text-foreground underline">
              runs
            </Link>{" "}
            page.
          </p>
          <p>
            Fridge and van readings stream on the{" "}
            <Link href="/telemetry" className="text-foreground underline">
              cold chain
            </Link>{" "}
            page; quality and spend are on{" "}
            <Link href="/evals" className="text-foreground underline">
              evals
            </Link>{" "}
            and{" "}
            <Link href="/costs" className="text-foreground underline">
              costs
            </Link>
            .
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  href,
  hint,
}: {
  label: string;
  value: string;
  href: string;
  hint?: string;
}) {
  return (
    <Link href={href}>
      <Card className="h-full transition-colors hover:bg-accent/40">
        <CardHeader className="pb-1">
          <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold">{value}</div>
          {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
        </CardContent>
      </Card>
    </Link>
  );
}

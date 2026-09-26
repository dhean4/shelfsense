import { CostChart } from "@/components/costs/cost-chart";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiGetOrNull } from "@/lib/api.server";
import { usd, type UsageOut } from "@/lib/types";

export const dynamic = "force-dynamic";

function compact(n: number): string {
  return n >= 1_000_000
    ? `${(n / 1_000_000).toFixed(1)}M`
    : n >= 1_000
      ? `${(n / 1_000).toFixed(1)}K`
      : String(n);
}

export default async function CostsPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string }>;
}) {
  const { days: rawDays } = await searchParams;
  const days = [7, 30, 90].includes(Number(rawDays)) ? Number(rawDays) : 7;
  const usage = await apiGetOrNull<UsageOut>(`/v1/usage?days=${String(days)}`);
  if (!usage) {
    return <p className="text-sm text-muted-foreground">Sign in to see costs.</p>;
  }
  const t = usage.totals;
  const perRun = t.runs ? t.cost_usd / t.runs : 0;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cost &amp; latency</h1>
          <p className="text-sm text-muted-foreground">
            Every model call is priced from token usage and stored on its run. Prometheus metrics
            are at <code className="font-mono">/metrics</code>; traces open in Langfuse from a run.
          </p>
        </div>
        <nav className="ml-auto flex gap-1 text-sm" aria-label="Time range">
          {[7, 30, 90].map((d) => (
            <a
              key={d}
              href={`/costs?days=${String(d)}`}
              className={`rounded-md border px-2 py-1 ${d === days ? "bg-accent font-medium" : "text-muted-foreground"}`}
              aria-current={d === days ? "page" : undefined}
            >
              {d}d
            </a>
          ))}
        </nav>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label={`Spend, last ${String(days)} days`}
          value={usd(t.cost_usd)}
          hint={`${String(t.runs)} runs · ${usd(perRun)} per run`}
        />
        <Tile
          label="Runs succeeded"
          value={`${String(t.succeeded)} / ${String(t.runs)}`}
          hint={t.failed ? `${String(t.failed)} failed` : "no failures"}
        />
        <Tile
          label="Tokens in / out"
          value={`${compact(t.input_tokens)} / ${compact(t.output_tokens)}`}
          hint={`${compact(t.cache_read_tokens)} read from cache`}
        />
        <Tile
          label="Latency p50 / p95"
          value={`${(t.p50_latency_ms / 1000).toFixed(1)}s / ${(t.p95_latency_ms / 1000).toFixed(1)}s`}
          hint="per run, all model calls"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Daily spend by agent</CardTitle>
        </CardHeader>
        <CardContent>
          <CostChart buckets={usage.by_day} days={days} />
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">By model</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead>Agent</TableHead>
                  <TableHead className="text-right">Runs</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                  <TableHead className="text-right">p95</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.by_model.map((b) => (
                  <TableRow key={`${b.key}-${b.kind}`}>
                    <TableCell className="font-mono text-xs">{b.key}</TableCell>
                    <TableCell>{b.kind}</TableCell>
                    <TableCell className="text-right tabular-nums">{b.runs}</TableCell>
                    <TableCell className="text-right tabular-nums">{usd(b.cost_usd)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {(b.p95_latency_ms / 1000).toFixed(1)}s
                    </TableCell>
                  </TableRow>
                ))}
                {usage.by_model.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground">
                      No runs in this window.
                    </TableCell>
                  </TableRow>
                ) : null}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Costliest runs</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.top_runs.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <a href={`/runs/${r.id}`} className="underline-offset-2 hover:underline">
                        {r.kind} · {new Date(r.started_at).toLocaleDateString()}
                      </a>
                    </TableCell>
                    <TableCell>
                      <Badge variant={r.status === "failed" ? "destructive" : "secondary"}>
                        {r.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {compact(r.input_tokens + r.output_tokens)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{usd(r.cost_usd)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold">{value}</div>
        {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
      </CardContent>
    </Card>
  );
}

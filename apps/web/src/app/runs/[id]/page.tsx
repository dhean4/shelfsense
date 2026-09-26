import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiGet } from "@/lib/api.server";
import { naira, usd, type RunOut } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  let run: RunOut;
  try {
    run = await apiGet<RunOut>(`/v1/runs/${id}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
  const summary = run.summary as {
    summary?: string;
    escalate?: boolean;
    escalation_reason?: string | null;
  } | null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Link href="/runs" className="text-sm text-muted-foreground hover:text-foreground">
          ← runs
        </Link>
        <h1 className="text-2xl font-semibold tracking-tight">{run.kind} run</h1>
        <Badge variant={run.status === "failed" ? "destructive" : "default"}>{run.status}</Badge>
      </div>
      <div className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-5">
        <Fact label="Model" value={run.model} mono />
        <Fact
          label="Tokens in / out"
          value={`${run.input_tokens.toLocaleString()} / ${run.output_tokens.toLocaleString()}`}
        />
        <Fact
          label="Cache read / write"
          value={`${run.cache_read_tokens.toLocaleString()} / ${run.cache_write_tokens.toLocaleString()}`}
        />
        <Fact label="Cost" value={usd(run.cost_usd)} />
        <Fact
          label="Latency"
          value={`${(run.latency_ms / 1000).toFixed(1)}s over ${String(run.attempts)} call(s)`}
        />
      </div>
      {run.trace_url ? (
        <p className="text-sm">
          <a
            href={run.trace_url}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            Open trace in Langfuse
          </a>
          <span className="ml-2 font-mono text-xs text-muted-foreground">{run.trace_id}</span>
        </p>
      ) : null}
      {run.error ? (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
          {run.error}
        </p>
      ) : null}
      {summary?.summary ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Planner summary{summary.escalate ? " · escalated" : ""}
            </CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-wrap text-sm">
            {summary.summary}
            {summary.escalation_reason ? `\n\nEscalation: ${summary.escalation_reason}` : ""}
          </CardContent>
        </Card>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Tool calls ({run.tool_calls.length})</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {run.tool_calls.length === 0 ? (
            <p className="text-sm text-muted-foreground">None.</p>
          ) : null}
          {run.tool_calls.map((call) => (
            <div key={call.id} className="rounded-md border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono">
                  {call.seq}. {call.tool_name}
                </span>
                <Badge variant={call.error ? "destructive" : "secondary"}>
                  {call.error ? "error" : "ok"}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {call.duration_ms} ms · {call.caller_role}
                </span>
              </div>
              <pre className="mt-2 overflow-x-auto rounded bg-muted p-2 text-xs">
                {JSON.stringify(call.arguments, null, 2)}
              </pre>
              <pre className="mt-1 overflow-x-auto rounded bg-muted p-2 text-xs">
                {call.error ?? JSON.stringify(call.result, null, 2)}
              </pre>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Actions ({run.actions.length})</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {run.actions.length === 0 ? <p className="text-sm text-muted-foreground">None.</p> : null}
          {run.actions.map((action) => (
            <div
              key={action.id}
              className="flex flex-wrap items-center gap-2 rounded-md border p-3 text-sm"
            >
              <Badge variant="outline">{action.kind}</Badge>
              <Badge
                variant={
                  action.status === "rejected"
                    ? "destructive"
                    : action.status === "approved"
                      ? "default"
                      : "secondary"
                }
              >
                {action.status}
              </Badge>
              <span>{action.rationale}</span>
              <span className="ml-auto text-muted-foreground">
                {naira(action.estimated_cost_kobo)}
              </span>
              {action.review_reason ? (
                <span className="w-full text-xs text-muted-foreground">{action.review_reason}</span>
              ) : null}
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function Fact({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={mono ? "font-mono text-xs" : ""}>{value}</div>
    </div>
  );
}
